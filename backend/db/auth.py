"""Authentication and user management for Kinesia platform.

Implements PBKDF2-HMAC salted password hashing and cryptographically signed
session tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from typing import Any

from backend.db.connection import get_db

logger = logging.getLogger(__name__)

_DEFAULT_SECRET = "kinesia_jwt_secret_dev_key_32_chars_long_12345678"
_TOKEN_TTL_SECONDS = 7 * 86400  # 7 days


def _get_signing_key() -> bytes:
    """Return secret signing key from environment or default."""
    secret = os.getenv("JWT_SECRET", _DEFAULT_SECRET).strip()
    return secret.encode("utf-8")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations.

    Returns:
        tuple[password_hash, salt]
    """
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return hashed, salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verify password against stored PBKDF2 hash using constant-time comparison."""
    computed_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(computed_hash, stored_hash)


def create_token(user_id: str, username: str, email: str) -> str:
    """Create a signed, base64url-encoded bearer session token."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "username": username,
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + _TOKEN_TTL_SECONDS,
    }

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

    h_bytes = b64url(json.dumps(header).encode("utf-8"))
    p_bytes = b64url(json.dumps(payload).encode("utf-8"))
    signing_input = f"{h_bytes}.{p_bytes}".encode("utf-8")
    sig = b64url(hmac.new(_get_signing_key(), signing_input, hashlib.sha256).digest())
    return f"{h_bytes}.{p_bytes}.{sig}"


def verify_token(token: str) -> dict[str, Any] | None:
    """Verify a signed bearer session token and return payload if valid."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None

    h_str, p_str, sig_str = parts

    # Re-compute signature
    signing_input = f"{h_str}.{p_str}".encode("utf-8")
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(_get_signing_key(), signing_input, hashlib.sha256).digest()
    ).decode("utf-8").rstrip("=")

    if not hmac.compare_digest(sig_str, expected_sig):
        return None

    def pad_b64(s: str) -> str:
        return s + "=" * (-len(s) % 4)

    try:
        payload_json = base64.urlsafe_b64decode(pad_b64(p_str)).decode("utf-8")
        payload = json.loads(payload_json)
        if time.time() > payload.get("exp", 0):
            return None
        return payload
    except Exception:
        return None


def register_user(
    username: str,
    email: str,
    password: str,
    display_name: str | None = None,
    default_input_lang: str = "en",
    default_output_lang: str = "hi",
) -> dict[str, Any]:
    """Register a new user account.

    Raises:
        ValueError: If username or email is invalid or already registered.
    """
    clean_username = username.strip().lower()
    clean_email = email.strip().lower()
    clean_display = (display_name or username).strip()

    if not clean_username or len(clean_username) < 3:
        raise ValueError("Username must be at least 3 characters long.")
    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", clean_email):
        raise ValueError("Invalid email format.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters long.")

    pwd_hash, salt = hash_password(password)
    user_id = str(uuid.uuid4())
    now = time.time()

    avatar_colors = ["#10B981", "#06B6D4", "#6366F1", "#8B5CF6", "#EC4899", "#F59E0B"]
    chosen_color = avatar_colors[int(hashlib.md5(clean_username.encode()).hexdigest(), 16) % len(avatar_colors)]

    with get_db() as db:
        # Check existing username or email
        existing = db.fetchone(
            "SELECT id FROM users WHERE username = ? OR email = ?",
            (clean_username, clean_email),
        )
        if existing:
            raise ValueError("Username or email already in use.")

        db.execute(
            """
            INSERT INTO users (
                id, username, email, password_hash, salt, display_name,
                avatar_color, default_input_lang, default_output_lang, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                clean_username,
                clean_email,
                pwd_hash,
                salt,
                clean_display,
                chosen_color,
                default_input_lang,
                default_output_lang,
                now,
            ),
        )

    token = create_token(user_id, clean_username, clean_email)
    user_dict = {
        "id": user_id,
        "username": clean_username,
        "email": clean_email,
        "display_name": clean_display,
        "avatar_color": chosen_color,
        "default_input_lang": default_input_lang,
        "default_output_lang": default_output_lang,
        "created_at": now,
    }
    result = dict(user_dict)
    result["token"] = token
    result["user"] = user_dict
    return result


def authenticate_user(login_id: str, password: str) -> dict[str, Any]:
    """Authenticate user with username/email and password.

    Returns:
        dict with 'token' and 'user' fields.

    Raises:
        ValueError: On invalid credentials.
    """
    clean_login = login_id.strip().lower()
    with get_db() as db:
        user = db.fetchone(
            """
            SELECT id, username, email, password_hash, salt, display_name,
                   avatar_color, default_input_lang, default_output_lang, created_at
            FROM users
            WHERE username = ? OR email = ?
            """,
            (clean_login, clean_login),
        )

    if not user:
        raise ValueError("Invalid email/username or password.")

    if not verify_password(password, user["password_hash"], user["salt"]):
        raise ValueError("Invalid email/username or password.")

    token = create_token(user["id"], user["username"], user["email"])
    user_profile = {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "display_name": user["display_name"],
        "avatar_color": user["avatar_color"],
        "default_input_lang": user["default_input_lang"],
        "default_output_lang": user["default_output_lang"],
        "created_at": user["created_at"],
    }

    return {"token": token, "user": user_profile}


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    """Retrieve user public profile by ID."""
    with get_db() as db:
        user = db.fetchone(
            """
            SELECT id, username, email, display_name, avatar_color,
                   default_input_lang, default_output_lang, created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        )
    return user


def update_user_profile(
    user_id: str,
    *,
    display_name: str | None = None,
    avatar_color: str | None = None,
    default_input_lang: str | None = None,
    default_output_lang: str | None = None,
) -> dict[str, Any]:
    """Update user profile fields and return updated profile."""
    current = get_user_profile(user_id)
    if not current:
        raise ValueError("User not found.")

    new_display = display_name.strip() if display_name and display_name.strip() else current["display_name"]
    new_avatar = avatar_color.strip() if avatar_color and avatar_color.strip() else current.get("avatar_color", "#10B981")
    new_input = default_input_lang.strip() if default_input_lang and default_input_lang.strip() else current.get("default_input_lang", "en")
    new_output = default_output_lang.strip() if default_output_lang and default_output_lang.strip() else current.get("default_output_lang", "hi")

    sql = "UPDATE users SET display_name = ?, avatar_color = ?, default_input_lang = ?, default_output_lang = ? WHERE id = ?"
    with get_db() as db:
        db.execute(sql, (new_display, new_avatar, new_input, new_output, user_id))

    return get_user_profile(user_id) or {}
