"""Personal bookmarks and persisted recap jobs; shared transcript remains canonical."""
from __future__ import annotations

import json
import time

from backend.db.connection import get_db


def list_moments(user_id, room_id=None):
    clause = " AND t.room_id = ?" if room_id else ""
    with get_db() as db:
        rows = db.fetchall(
            """SELECT m.turn_id, m.created_at, t.room_id, t.speaker_name, t.source_lang,
                      t.original_text, t.translations_json, t.timestamp, r.room_code, r.title
               FROM saved_moments m JOIN transcripts t ON t.id = m.turn_id
               JOIN rooms r ON r.id = t.room_id
               WHERE m.user_id = ?""" + clause + " ORDER BY m.created_at DESC LIMIT 500",
            (user_id, room_id) if room_id else (user_id,),
        )
    for row in rows:
        try:
            translations = json.loads(row.pop("translations_json") or "{}")
        except (TypeError, ValueError):
            translations = {}
        row["translations"] = translations if isinstance(translations, dict) else {}
    return rows


def save_moment(user_id, room_id, turn_id):
    with get_db() as db:
        turn = db.fetchone("SELECT id FROM transcripts WHERE id = ? AND room_id = ?", (turn_id, room_id))
        if not turn:
            raise ValueError("This transcript turn is no longer available.")
        db.execute(
            """INSERT INTO saved_moments (user_id, turn_id, created_at) VALUES (?, ?, ?)
               ON CONFLICT (user_id, turn_id) DO NOTHING""", (user_id, turn_id, time.time()),
        )


def remove_moment(user_id, room_id, turn_id):
    with get_db() as db:
        db.execute(
            """DELETE FROM saved_moments WHERE user_id = ? AND turn_id = ?
               AND EXISTS (SELECT 1 FROM transcripts t WHERE t.id = saved_moments.turn_id AND t.room_id = ?)""",
            (user_id, turn_id, room_id),
        )


def read_recap(room_id, language):
    with get_db() as db:
        row = db.fetchone("SELECT * FROM conversation_recaps WHERE room_id = ? AND language = ?", (room_id, language))
    if row and row.get("result_json"):
        row["result"] = json.loads(row.pop("result_json"))
    elif row:
        row.pop("result_json", None)
    return row


def queue_recap(room_id, language, job_id, source_hash):
    with get_db() as db:
        db.execute(
            """INSERT INTO conversation_recaps
               (room_id, language, job_id, source_hash, status, result_json, error, updated_at)
               VALUES (?, ?, ?, ?, 'queued', NULL, NULL, ?)
               ON CONFLICT (room_id, language) DO UPDATE SET job_id=excluded.job_id,
                 source_hash=excluded.source_hash, status='queued', result_json=NULL, error=NULL,
                 retry_at=NULL, updated_at=excluded.updated_at""", (room_id, language, job_id, source_hash, time.time()),
        )


def finish_recap(job_id, status, result=None, error=None, *, retry_at=None):
    with get_db() as db:
        db.execute(
            """UPDATE conversation_recaps SET status = ?, result_json = ?, error = ?, updated_at = ?, retry_at = ?
               WHERE job_id = ?""",
            (status, json.dumps(result, ensure_ascii=False) if result else None, error, time.time(), retry_at, job_id),
        )


def recover_recaps():
    with get_db() as db:
        db.execute(
            """UPDATE conversation_recaps SET status='failed',
               error='The server restarted. You can retry this recap.', updated_at=?
               WHERE status IN ('queued', 'generating')""", (time.time(),),
        )
