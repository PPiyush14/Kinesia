"""Unit tests for Kinesia database, authentication, and room management."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db.auth import (
    _get_signing_key,
    authenticate_user,
    create_token,
    register_user,
    verify_token,
)
from backend.db.connection import init_db
from backend.db.rooms import (
    add_participant,
    add_transcript,
    clear_room_transcripts,
    create_room,
    end_room,
    get_room_by_code,
    get_room_transcripts,
    get_session_insights,
    hide_all_room_history,
    hide_room_from_history,
    list_active_participants,
    list_user_rooms,
    save_session_insights,
    verify_room_access,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point database to isolated test SQLite path."""
    test_db_path = tmp_path / "test_kinesia.db"
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("backend.db.connection._get_sqlite_path", lambda: test_db_path)
    init_db()


def test_user_registration_and_authentication() -> None:
    # 1. Register valid user
    user = register_user(
        username="piyush",
        email="piyush@example.com",
        password="securePassword123",
        display_name="Piyush K",
        default_input_lang="en",
        default_output_lang="mr",
    )
    assert user["username"] == "piyush"
    assert user["email"] == "piyush@example.com"
    assert user["display_name"] == "Piyush K"
    assert user["default_output_lang"] == "mr"

    # 2. Reject duplicate email/username
    with pytest.raises(ValueError, match="already in use"):
        register_user(username="piyush", email="other@example.com", password="password")

    # 3. Authenticate with correct password
    auth_resp = authenticate_user("piyush@example.com", "securePassword123")
    assert "token" in auth_resp
    assert auth_resp["user"]["username"] == "piyush"

    # 4. Verify token
    token = auth_resp["token"]
    payload = verify_token(token)
    assert payload is not None
    assert payload["username"] == "piyush"

    # 5. Reject invalid password
    with pytest.raises(ValueError, match="Invalid email/username or password"):
        authenticate_user("piyush", "wrongPassword")


def test_missing_or_empty_jwt_secret_uses_random_process_key(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    key = _get_signing_key()
    assert len(key) >= 32
    token = create_token("user", "user", "user@example.com")
    assert verify_token(token)["sub"] == "user"
    monkeypatch.setenv("JWT_SECRET", "  ")
    assert _get_signing_key() == key
    monkeypatch.setenv("JWT_SECRET", "configured-test-signing-key-32-characters")
    assert _get_signing_key() != key
    assert verify_token(token) is None


@pytest.mark.parametrize("token", [None, b"bytes", "a.b.☃", "a.b." + "x" * 9000])
def test_malformed_bearer_tokens_are_rejected_without_raising(token):
    assert verify_token(token) is None


def test_room_lifecycle_and_access_control() -> None:
    host = register_user("host_user", "host@test.com", "pass123")
    guest = register_user("guest_user", "guest@test.com", "pass123")

    # 1. Create room with passcode
    room = create_room(
        title="Cross-Border Innovation Sync",
        host_id=host["id"],
        passcode="secretPass456",
        record_session=True,
    )
    assert room["has_passcode"] is True
    assert room["room_code"].startswith("kin-")

    code = room["room_code"]

    # 2. Check access with correct and wrong passcode
    assert verify_room_access(code, "secretPass456") is True
    assert verify_room_access(code, "wrongPass") is False
    assert verify_room_access(code, None) is False

    # 3. Add active participant
    part = add_participant(room["id"], guest["id"], "Guest User", speaking_lang="hi", listening_lang="en")
    assert part["user_name"] == "Guest User"

    active = list_active_participants(room["id"])
    assert len(active) == 1
    assert active[0]["user_id"] == guest["id"]

    # 4. Add transcripts
    t1 = add_transcript(
        room_id=room["id"],
        speaker_id=guest["id"],
        speaker_name="Guest User",
        original_text="नमस्ते, हम आज की योजना पर चर्चा कर रहे हैं।",
        source_lang="hi",
        translations={"en": "Hello, we are discussing today's plan.", "mr": "नमस्कार, आपण आजच्या योजनेवर चर्चा करत आहोत."},
        confidence=0.98,
    )
    assert t1["confidence"] == 0.98
    assert "mr" in t1["translations"]

    history = get_room_transcripts(room["id"])
    assert len(history) == 1
    assert history[0]["translations"]["en"] == "Hello, we are discussing today's plan."

    # 5. Save Session Insights (MoM)
    save_session_insights(
        room_id=room["id"],
        executive_summary="The team aligned on cross-lingual product roadmap.",
        action_items=["Piyush to finalize architecture by Friday"],
        key_points=["Marathi language support approved"],
        entities={"PERSON": ["Piyush"], "DATE": ["Friday"]},
    )

    insights = get_session_insights(room["id"])
    assert insights is not None
    assert len(insights["action_items"]) == 1
    assert "Piyush" in insights["entities"]["PERSON"]

    # 6. End room
    ended = end_room(code)
    assert ended["ended_at"] is not None


def test_history_metadata_keeps_creator_identity_after_leaving():
    host = register_user("owner", "owner@example.com", "pass123")
    create_room("Persistent host", host["id"], passcode="room-pass")
    entry = list_user_rooms(host["id"])[0]
    assert entry["host_id"] == host["id"]
    assert entry["is_host"] is True
    assert entry["passcode_required"] is True
    assert "passcode_hash" not in entry


def test_room_history_removal_is_private_idempotent_and_rejoin_restores_it():
    host = register_user("history_host", "history_host@example.com", "pass123")
    guest = register_user("history_guest", "history_guest@example.com", "pass123")
    room = create_room("Shared conversation", host["id"])
    add_participant(room["id"], guest["id"], "Guest")
    add_transcript(room["id"], host["id"], "Host", "Keep this transcript.", "en", {"hi": "translation"})
    hide_room_from_history(room["id"], guest["id"])
    hide_room_from_history(room["id"], guest["id"])
    assert list_user_rooms(guest["id"]) == []
    assert len(list_user_rooms(host["id"])) == 1
    assert len(get_room_transcripts(room["id"])) == 1
    assert get_room_by_code(room["room_code"])["is_active"]
    add_participant(room["id"], guest["id"], "Guest again")
    assert len(list_user_rooms(guest["id"])) == 1
    hide_all_room_history(host["id"])
    assert list_user_rooms(host["id"]) == []
    assert len(list_user_rooms(guest["id"])) == 1
    assert len(get_room_transcripts(room["id"])) == 1


def test_clear_history_hides_joined_rooms_beyond_the_fifty_entry_display_limit():
    host = register_user("many_host", "many_host@example.com", "pass123")
    guest = register_user("many_guest", "many_guest@example.com", "pass123")
    for index in range(52):
        room = create_room(f"Conversation {index}", host["id"])
        add_participant(room["id"], guest["id"], "Guest")
    assert len(list_user_rooms(guest["id"])) == 50
    hide_all_room_history(guest["id"])
    assert list_user_rooms(guest["id"]) == []
    assert len(list_user_rooms(host["id"])) == 50


def test_clearing_shared_transcript_also_invalidates_its_saved_notes():
    host = register_user("clear_notes", "clear_notes@example.com", "pass123")
    room = create_room("Clear shared text", host["id"])
    add_transcript(room["id"], host["id"], "Host", "A private sentence.", "en", {})
    save_session_insights(room["id"], "A private sentence.", [], [], {})
    clear_room_transcripts(room["id"])
    assert get_room_transcripts(room["id"]) == []
    assert get_session_insights(room["id"]) is None
    assert get_room_by_code(room["room_code"]) is not None
