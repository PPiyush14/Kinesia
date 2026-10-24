"""Room, participant, transcript, and dialogue intelligence persistence for Kinesia.

Handles multi-party session states, room codes, transcripts, and post-session MoM.
"""

from __future__ import annotations

import json
import logging
import secrets
import string
import time
import uuid
from typing import Any

from backend.db.auth import hash_password, verify_password
from backend.db.connection import get_db

logger = logging.getLogger(__name__)


def generate_room_code() -> str:
    """Generate a clean Google Meet style slug: kin-123-abc."""
    digits = "".join(secrets.choice(string.digits) for _ in range(3))
    chars = "".join(secrets.choice(string.ascii_lowercase) for _ in range(3))
    return f"kin-{digits}-{chars}"


def create_room(
    title: str,
    host_id: str,
    passcode: str | None = None,
    record_session: bool = True,
) -> dict[str, Any]:
    """Create a new Kinesia dialogue room."""
    clean_title = (title or "General Session").strip()
    room_id = str(uuid.uuid4())
    room_code = generate_room_code()
    now = time.time()

    passcode_hash = None
    salt = None
    if passcode and passcode.strip():
        passcode_hash, salt = hash_password(passcode.strip())
        # Store salt with hash separated by '$'
        stored_pass = f"{salt}${passcode_hash}"
    else:
        stored_pass = None

    with get_db() as db:
        db.execute(
            """
            INSERT INTO rooms (
                id, room_code, title, passcode_hash, host_id,
                record_session, is_active, created_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, TRUE, ?, NULL)
            """,
            (room_id, room_code, clean_title, stored_pass, host_id, bool(record_session), now),
        )

    return {
        "id": room_id,
        "room_code": room_code,
        "title": clean_title,
        "has_passcode": stored_pass is not None,
        "passcode_required": stored_pass is not None,
        "host_id": host_id,
        "record_session": record_session,
        "is_active": True,
        "status": "live",
        "created_at": now,
    }


def get_room_by_code(room_code: str) -> dict[str, Any] | None:
    """Retrieve room information by its slug code."""
    clean_code = room_code.strip().lower()
    with get_db() as db:
        room = db.fetchone(
            """
            SELECT id, room_code, title, passcode_hash, host_id,
                   record_session, is_active, created_at, ended_at
            FROM rooms
            WHERE room_code = ?
            """,
            (clean_code,),
        )
    if not room:
        return None

    return {
        "id": room["id"],
        "room_code": room["room_code"],
        "title": room["title"],
        "has_passcode": bool(room.get("passcode_hash")),
        "passcode_required": bool(room.get("passcode_hash")),
        "host_id": room["host_id"],
        "record_session": bool(room.get("record_session")),
        "is_active": bool(room.get("is_active")),
        "status": "live" if bool(room.get("is_active")) else "ended",
        "created_at": room["created_at"],
        "ended_at": room.get("ended_at"),
    }


def verify_room_access(room_code: str, passcode: str | None = None) -> bool:
    """Verify if participant passcode matches room passcode."""
    clean_code = room_code.strip().lower()
    with get_db() as db:
        room = db.fetchone("SELECT passcode_hash FROM rooms WHERE room_code = ?", (clean_code,))
    if not room:
        return False

    stored_pass = room.get("passcode_hash")
    if not stored_pass:
        return True  # Room is public / no passcode required

    if not passcode:
        return False

    parts = stored_pass.split("$")
    if len(parts) != 2:
        return False
    salt, expected_hash = parts
    return verify_password(passcode.strip(), expected_hash, salt)


def end_room(room_code: str) -> dict[str, Any]:
    """Mark a room session as concluded."""
    clean_code = room_code.strip().lower()
    now = time.time()
    with get_db() as db:
        db.execute(
            "UPDATE rooms SET is_active = FALSE, ended_at = ? WHERE room_code = ?",
            (now, clean_code),
        )
        room = db.fetchone("SELECT id, room_code, title, created_at, ended_at FROM rooms WHERE room_code = ?", (clean_code,))
    return room or {}


def list_user_rooms(user_id: str) -> list[dict[str, Any]]:
    """List recent rooms created by or joined by the user."""
    with get_db() as db:
        rows = db.fetchall(
            """
            SELECT DISTINCT r.id, r.room_code, r.title, r.record_session, r.is_active, r.created_at, r.ended_at
            FROM rooms r
            LEFT JOIN room_participants p ON r.id = p.room_id
            WHERE r.host_id = ? OR p.user_id = ?
            ORDER BY r.created_at DESC
            LIMIT 50
            """,
            (user_id, user_id),
        )
    return [
        {
            "id": r["id"],
            "room_code": r["room_code"],
            "title": r["title"],
            "record_session": bool(r.get("record_session")),
            "is_active": bool(r.get("is_active")),
            "status": "live" if bool(r.get("is_active")) else "ended",
            "created_at": r.get("created_at"),
            "ended_at": r.get("ended_at"),
        }
        for r in rows
    ]


def add_participant(
    room_id: str,
    user_id: str,
    user_name: str,
    speaking_lang: str = "en",
    listening_lang: str = "hi",
) -> dict[str, Any]:
    """Register participant entering a room, closing any dangling previous sessions."""
    participant_id = str(uuid.uuid4())
    now = time.time()
    with get_db() as db:
        # Close any lingering prior open entry for this user in this room
        db.execute(
            "UPDATE room_participants SET left_at = ? WHERE room_id = ? AND user_id = ? AND left_at IS NULL",
            (now, room_id, user_id),
        )
        db.execute(
            """
            INSERT INTO room_participants (
                id, room_id, user_id, user_name, joined_at, left_at,
                speaking_lang, listening_lang, is_mic_muted
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, FALSE)
            """,
            (participant_id, room_id, user_id, user_name, now, speaking_lang, listening_lang),
        )
    return {
        "id": participant_id,
        "room_id": room_id,
        "user_id": user_id,
        "user_name": user_name,
        "joined_at": now,
        "speaking_lang": speaking_lang,
        "listening_lang": listening_lang,
    }


def remove_participant(room_id: str, user_id: str) -> None:
    """Mark participant as having left the room."""
    now = time.time()
    with get_db() as db:
        db.execute(
            "UPDATE room_participants SET left_at = ? WHERE room_id = ? AND user_id = ? AND left_at IS NULL",
            (now, room_id, user_id),
        )


def update_participant_languages(
    room_id: str,
    user_id: str,
    speaking_lang: str | None = None,
    listening_lang: str | None = None,
) -> None:
    """Update participant dialect settings in active room session."""
    updates = []
    params = []
    if speaking_lang:
        updates.append("speaking_lang = ?")
        params.append(speaking_lang)
    if listening_lang:
        updates.append("listening_lang = ?")
        params.append(listening_lang)
    if not updates:
        return
    params.extend([room_id, user_id])
    with get_db() as db:
        db.execute(
            f"UPDATE room_participants SET {', '.join(updates)} WHERE room_id = ? AND user_id = ? AND left_at IS NULL",
            tuple(params),
        )


def list_active_participants(room_id: str) -> list[dict[str, Any]]:
    """Return all active participants currently in a room (deduplicated by user)."""
    with get_db() as db:
        rows = db.fetchall(
            """
            SELECT p.id, p.user_id, p.user_name, p.joined_at, p.speaking_lang,
                   p.listening_lang, p.is_mic_muted, u.avatar_color
            FROM room_participants p
            LEFT JOIN users u ON p.user_id = u.id
            WHERE p.room_id = ? AND p.left_at IS NULL
            ORDER BY p.joined_at ASC
            """,
            (room_id,),
        )
    seen = set()
    deduped = []
    for r in rows:
        uid = str(r["user_id"])
        if uid not in seen:
            seen.add(uid)
            deduped.append(r)
    return deduped


def add_transcript(
    room_id: str,
    speaker_id: str,
    speaker_name: str,
    original_text: str,
    source_lang: str,
    translations: dict[str, str],
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Record a translated dialogue turn in the room's permanent history."""
    turn_id = str(uuid.uuid4())
    now = time.time()
    trans_json = json.dumps(translations, ensure_ascii=False)

    with get_db() as db:
        db.execute(
            """
            INSERT INTO transcripts (
                id, room_id, speaker_id, speaker_name, original_text,
                source_lang, confidence, translations_json, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (turn_id, room_id, speaker_id, speaker_name, original_text, source_lang, confidence, trans_json, now),
        )

    return {
        "id": turn_id,
        "room_id": room_id,
        "speaker_id": speaker_id,
        "speaker_name": speaker_name,
        "original_text": original_text,
        "source_lang": source_lang,
        "confidence": confidence,
        "translations": translations,
        "timestamp": now,
    }


def get_room_transcripts(room_id: str) -> list[dict[str, Any]]:
    """Retrieve full transcript history for a room."""
    with get_db() as db:
        rows = db.fetchall(
            """
            SELECT id, speaker_id, speaker_name, original_text,
                   source_lang, confidence, translations_json, timestamp
            FROM transcripts
            WHERE room_id = ?
            ORDER BY timestamp ASC
            """,
            (room_id,),
        )

    result = []
    for row in rows:
        try:
            translations = json.loads(row["translations_json"])
        except Exception:
            translations = {}
        result.append(
            {
                "id": row["id"],
                "speaker_id": row["speaker_id"],
                "speaker_name": row["speaker_name"],
                "original_text": row["original_text"],
                "source_lang": row["source_lang"],
                "confidence": row.get("confidence", 1.0),
                "translations": translations,
                "timestamp": row["timestamp"],
            }
        )
    return result


def save_stream_transcript(turn: dict[str, Any]) -> None:
    """Merge language results into one stable turn ID without schema changes."""
    with get_db() as db:
        db.execute(
            """INSERT INTO transcripts
               (id, room_id, speaker_id, speaker_name, original_text, source_lang,
                confidence, translations_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET
                 original_text=excluded.original_text, source_lang=excluded.source_lang,
                 confidence=excluded.confidence, translations_json=excluded.translations_json""",
            (turn["id"], turn["room_id"], turn["speaker_id"], turn["speaker_name"],
             turn["original_text"], turn["source_lang"], turn["confidence"],
             json.dumps(turn["translations"], ensure_ascii=False), turn["timestamp"]),
        )


def save_session_insights(
    room_id: str,
    executive_summary: str,
    action_items: list[str],
    key_points: list[str],
    entities: dict[str, list[str]],
) -> dict[str, Any]:
    """Save post-session AI intelligence & Minutes of Meeting."""
    now = time.time()
    clean_summary = str(executive_summary) if executive_summary is not None else ""
    ai_json = json.dumps(action_items, ensure_ascii=False)
    kp_json = json.dumps(key_points, ensure_ascii=False)
    ent_json = json.dumps(entities, ensure_ascii=False)

    with get_db() as db:
        if db.is_pg:
            db.execute(
                """
                INSERT INTO session_insights (
                    room_id, executive_summary, action_items_json,
                    key_points_json, entities_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (room_id) DO UPDATE SET
                    executive_summary = EXCLUDED.executive_summary,
                    action_items_json = EXCLUDED.action_items_json,
                    key_points_json = EXCLUDED.key_points_json,
                    entities_json = EXCLUDED.entities_json,
                    created_at = EXCLUDED.created_at
                """,
                (room_id, clean_summary, ai_json, kp_json, ent_json, now),
            )
        else:
            db.execute(
                """
                INSERT OR REPLACE INTO session_insights (
                    room_id, executive_summary, action_items_json,
                    key_points_json, entities_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (room_id, clean_summary, ai_json, kp_json, ent_json, now),
            )

    return {
        "room_id": room_id,
        "executive_summary": executive_summary,
        "action_items": action_items,
        "key_points": key_points,
        "entities": entities,
        "created_at": now,
    }


def get_session_insights(room_id: str) -> dict[str, Any] | None:
    """Retrieve post-session intelligence & Minutes of Meeting for a room."""
    with get_db() as db:
        row = db.fetchone(
            """
            SELECT room_id, executive_summary, action_items_json,
                   key_points_json, entities_json, created_at
            FROM session_insights
            WHERE room_id = ?
            """,
            (room_id,),
        )
    if not row:
        return None

    try:
        action_items = json.loads(row["action_items_json"])
    except Exception:
        action_items = []
    try:
        key_points = json.loads(row["key_points_json"])
    except Exception:
        key_points = []
    try:
        entities = json.loads(row["entities_json"])
    except Exception:
        entities = {}

    return {
        "room_id": row["room_id"],
        "executive_summary": row["executive_summary"],
        "action_items": action_items,
        "key_points": key_points,
        "entities": entities,
        "created_at": row["created_at"],
    }

def is_room_member(room_id: str, user_id: str) -> bool:
    """Check if user is the room creator or has joined as a participant."""
    with get_db() as db:
        room = db.fetchone("SELECT host_id FROM rooms WHERE id = ?", (room_id,))
        if room and str(room["host_id"]) == str(user_id):
            return True
        p = db.fetchone(
            "SELECT id FROM room_participants WHERE room_id = ? AND user_id = ?",
            (room_id, str(user_id)),
        )
        return p is not None


def clear_room_transcripts(room_id: str) -> None:
    """Delete all transcripts for a room."""
    with get_db() as db:
        db.execute("DELETE FROM transcripts WHERE room_id = ?", (room_id,))


def delete_room(room_code_or_id: str) -> bool:
    """Permanently delete a room, its participants, transcripts, and insights."""
    room = get_room_by_code(room_code_or_id)
    if room is None:
        with get_db() as db:
            room = db.fetchone("SELECT id FROM rooms WHERE id = ?", (room_code_or_id,))
    if not room:
        return False
    room_id = room["id"]
    with get_db() as db:
        db.execute("DELETE FROM room_participants WHERE room_id = ?", (room_id,))
        db.execute("DELETE FROM transcripts WHERE room_id = ?", (room_id,))
        db.execute("DELETE FROM session_insights WHERE room_id = ?", (room_id,))
        db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    return True


def clear_all_rooms(host_id: str | None = None) -> int:
    """Clear all past rooms or all rooms belonging to a host."""
    with get_db() as db:
        if host_id:
            cursor = db.execute("SELECT id FROM rooms WHERE host_id = ?", (str(host_id),))
        else:
            cursor = db.execute("SELECT id FROM rooms")
        rows = cursor.fetchall()
        for row in rows:
            rid = row["id"] if isinstance(row, dict) else row[0]
            db.execute("DELETE FROM room_participants WHERE room_id = ?", (rid,))
            db.execute("DELETE FROM transcripts WHERE room_id = ?", (rid,))
            db.execute("DELETE FROM session_insights WHERE room_id = ?", (rid,))
            db.execute("DELETE FROM rooms WHERE id = ?", (rid,))
        return len(rows)

