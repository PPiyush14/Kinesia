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
import math
import os
import re
import secrets
import time
import uuid
from typing import Any

from backend.db.connection import get_db
from backend.languages import LANGUAGE_DISPLAY_NAMES, normalize_language_code

logger = logging.getLogger(__name__)

# Local development may run without configuration, but must never use a public
# signing key. Set JWT_SECRET for tokens that survive process restarts.
_DEFAULT_SECRET = secrets.token_urlsafe(48)
_TOKEN_TTL_SECONDS = 7 * 86400  # 7 days


def _get_signing_key() -> bytes:
    """Return secret signing key from environment or default."""
    secret = os.getenv("JWT_SECRET", "").strip() or _DEFAULT_SECRET
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
    signing_input = f"{h_bytes}.{p_bytes}".encode()
    sig = b64url(hmac.new(_get_signing_key(), signing_input, hashlib.sha256).digest())
    return f"{h_bytes}.{p_bytes}.{sig}"


def verify_token(token: str) -> dict[str, Any] | None:
    """Verify a signed bearer session token and return payload if valid."""
    if not isinstance(token, str) or len(token) > 8192:
        return None
    token = token.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
        return None
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None

    h_str, p_str, sig_str = parts

    # Re-compute signature
    signing_input = f"{h_str}.{p_str}".encode()
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(_get_signing_key(), signing_input, hashlib.sha256).digest()
    ).decode("utf-8").rstrip("=")

    if not hmac.compare_digest(sig_str, expected_sig):
        return None

    def pad_b64(s: str) -> str:
        return s + "=" * (-len(s) % 4)

    try:
        header = json.loads(base64.urlsafe_b64decode(pad_b64(h_str)))
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            return None
        payload_json = base64.urlsafe_b64decode(pad_b64(p_str)).decode("utf-8")
        payload = json.loads(payload_json)
        if not isinstance(payload, dict) or not isinstance(payload.get("sub"), str) or not payload["sub"]:
            return None
        expiry = payload.get("exp")
        if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry) or time.time() >= expiry):
            return None
        return payload
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def _validate_text(value, label, maximum, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label} must be text of at most {maximum} characters.")


def _profile_language(value):
    _validate_text(value, "Language", 10)
    code = normalize_language_code(value)
    if code not in LANGUAGE_DISPLAY_NAMES:
        raise ValueError("Select a supported language.")
    return code


def register_user(
    username: str,
    email: str,
    password: str,
    display_name: str | None = None,
    default_input_lang: str = "en",
    default_output_lang: str = "hi",
    gender: str = "female",
) -> dict[str, Any]:
    """Register a new user account.

    Raises:
        ValueError: If username or email is invalid or already registered.
    """
    for value, label, maximum, optional in (
        (username, "Username", 50, False), (email, "Email", 100, False),
        (password, "Password", 1024, False), (display_name, "Display name", 100, True),
        (gender, "Gender", 10, True),
    ):
        _validate_text(value, label, maximum, optional=optional)
    default_input_lang = _profile_language(default_input_lang)
    default_output_lang = _profile_language(default_output_lang)
    clean_username = username.strip().lower()
    clean_email = email.strip().lower()
    clean_display = (display_name or username).strip()
    clean_gender = (gender or "female").strip().lower()
    if clean_gender not in {"male", "female"}:
        clean_gender = "female"

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
                avatar_color, default_input_lang, default_output_lang, gender, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                clean_gender,
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
        "gender": clean_gender,
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
    _validate_text(login_id, "Login", 100)
    _validate_text(password, "Password", 1024)
    clean_login = login_id.strip().lower()
    with get_db() as db:
        user = db.fetchone(
            """
            SELECT id, username, email, password_hash, salt, display_name,
                   avatar_color, default_input_lang, default_output_lang, gender, created_at
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
        "gender": user.get("gender") or "female",
        "created_at": user["created_at"],
    }

    return {"token": token, "user": user_profile}


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    """Retrieve user public profile by ID."""
    with get_db() as db:
        user = db.fetchone(
            """
            SELECT id, username, email, display_name, avatar_color,
                   default_input_lang, default_output_lang, gender, created_at
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
    gender: str | None = None,
) -> dict[str, Any]:
    """Update user profile fields and return updated profile."""
    current = get_user_profile(user_id)
    if not current:
        raise ValueError("User not found.")

    for value, label, maximum in ((display_name, "Display name", 100),
                                  (avatar_color, "Avatar color", 20), (gender, "Gender", 10)):
        _validate_text(value, label, maximum, optional=True)
    if default_input_lang is not None:
        default_input_lang = _profile_language(default_input_lang)
    if default_output_lang is not None:
        default_output_lang = _profile_language(default_output_lang)

    new_display = display_name.strip() if display_name and display_name.strip() else current["display_name"]
    new_avatar = avatar_color.strip() if avatar_color and avatar_color.strip() else current.get("avatar_color", "#10B981")
    new_input = default_input_lang.strip() if default_input_lang and default_input_lang.strip() else current.get("default_input_lang", "en")
    new_output = default_output_lang.strip() if default_output_lang and default_output_lang.strip() else current.get("default_output_lang", "hi")
    new_gender = (gender or current.get("gender") or "female").strip().lower()
    if new_gender not in {"male", "female"}:
        new_gender = current.get("gender") or "female"

    sql = "UPDATE users SET display_name = ?, avatar_color = ?, default_input_lang = ?, default_output_lang = ?, gender = ? WHERE id = ?"
    with get_db() as db:
        db.execute(sql, (new_display, new_avatar, new_input, new_output, new_gender, user_id))

    return get_user_profile(user_id) or {}


def delete_user_account(user_id: str) -> bool:
    """Permanently delete a user account and associated room data (Full CRUD delete)."""
    with get_db() as db:
        # Delete room participants entries
        db.execute("DELETE FROM room_participants WHERE user_id = ?", (user_id,))
        # Find all rooms hosted by this user and clean them up
        hosted_rooms = db.fetchall("SELECT id FROM rooms WHERE host_id = ?", (user_id,))
        for r in hosted_rooms:
            r_id = r["id"]
            db.execute("DELETE FROM session_insights WHERE room_id = ?", (r_id,))
            db.execute("DELETE FROM transcripts WHERE room_id = ?", (r_id,))
            db.execute("DELETE FROM room_participants WHERE room_id = ?", (r_id,))
            db.execute("DELETE FROM rooms WHERE id = ?", (r_id,))
        # Finally delete user from users table
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True
