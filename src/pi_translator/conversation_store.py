from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class SessionConversationStore:
    def __init__(self, project_root: Path) -> None:
        self.root = project_root / "session_conversations"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._summary_cache: dict[str, dict[str, Any]] = {}

    def session_path(self, session_id: str) -> Path:
        safe_session_id = session_id.strip()
        return self.root / f"{safe_session_id}.json"

    def ensure_session(self, session_id: str, started_at: float) -> Path:
        path = self.session_path(session_id)
        with self._lock:
            if path.exists():
                self._summary_cache.setdefault(
                    session_id,
                    {
                        "session_id": session_id,
                        "started_at": started_at,
                        "ended_at": None,
                        "updated_at": started_at,
                        "turn_count": 0,
                    },
                )
                return path
            payload = {
                "session_id": session_id,
                "started_at": started_at,
                "ended_at": None,
                "updated_at": started_at,
                "turns": [],
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            self._summary_cache[session_id] = self._summary_from_payload(payload)
        return path

    def append_turn(self, session_id: str, turn: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = self._read_unlocked(session_id)
            turns = payload.setdefault("turns", [])
            if not isinstance(turns, list):
                turns = []
                payload["turns"] = turns
            turns.append(turn)
            payload["updated_at"] = time.time()
            self._write_unlocked(session_id, payload)
            self._summary_cache[session_id] = self._summary_from_payload(payload)
            return payload

    def mark_session_ended(
        self,
        session_id: str,
        *,
        ended_at: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._read_unlocked(session_id)
            payload["ended_at"] = ended_at or time.time()
            payload["updated_at"] = payload["ended_at"]
            self._write_unlocked(session_id, payload)
            self._summary_cache[session_id] = self._summary_from_payload(payload)
            return payload

    def read_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._read_unlocked(session_id)

    def read_summary(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            cached = self._summary_cache.get(session_id)
            if cached is not None:
                return dict(cached)
            payload = self._read_unlocked(session_id)
            summary = self._summary_from_payload(payload)
            self._summary_cache[session_id] = summary
            return dict(summary)

    def _read_unlocked(self, session_id: str) -> dict[str, Any]:
        path = self.session_path(session_id)
        if not path.exists():
            return {
                "session_id": session_id,
                "started_at": None,
                "ended_at": None,
                "updated_at": None,
                "turns": [],
            }

        try:
            raw_text = path.read_text(encoding="utf-8").strip()
        except OSError:
            raw_text = ""
        if not raw_text:
            return {
                "session_id": session_id,
                "started_at": None,
                "ended_at": None,
                "updated_at": None,
                "turns": [],
            }

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return {
                "session_id": session_id,
                "started_at": None,
                "ended_at": None,
                "updated_at": None,
                "turns": [],
            }
        if not isinstance(payload, dict):
            return {
                "session_id": session_id,
                "started_at": None,
                "ended_at": None,
                "updated_at": None,
                "turns": [],
            }
        payload.setdefault("session_id", session_id)
        payload.setdefault("turns", [])
        return payload

    def _write_unlocked(self, session_id: str, payload: dict[str, Any]) -> None:
        path = self.session_path(session_id)
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _summary_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        turns = payload.get("turns")
        turn_count = len(turns) if isinstance(turns, list) else 0
        return {
            "session_id": payload.get("session_id"),
            "started_at": payload.get("started_at"),
            "ended_at": payload.get("ended_at"),
            "updated_at": payload.get("updated_at"),
            "turn_count": turn_count,
        }
