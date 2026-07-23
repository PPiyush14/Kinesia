from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from pi_translator.config import AppConfig

if os.name == "nt":  # pragma: no cover - platform-specific
    import msvcrt
else:  # pragma: no cover - platform-specific
    import fcntl


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    started_at: float
    is_new_session: bool


class SessionLanguageStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.path = config.session_state_path
        self.lock_path = Path(f"{self.path}.lock")

    def start_or_join_session(self) -> SessionInfo:
        with self._locked() as handle:
            payload = self._read_payload_from_handle(handle)
            now = time.time()
            if self._should_start_new_session(payload, now):
                payload = self._new_payload(now)
                is_new_session = True
            else:
                payload["last_activity_at"] = now
                is_new_session = False
            self._write_payload_to_handle(handle, payload)
            return self._payload_to_session_info(payload, is_new_session=is_new_session)

    def create_new_session(self) -> SessionInfo:
        with self._locked() as handle:
            now = time.time()
            payload = self._new_payload(now)
            self._write_payload_to_handle(handle, payload)
            return self._payload_to_session_info(payload, is_new_session=True)

    def end_current_session(self) -> SessionInfo | None:
        with self._locked() as handle:
            payload = self._read_payload_from_handle(handle)
            session_id = payload.get("session_id")
            started_at = payload.get("started_at")
            if not isinstance(session_id, str) or not session_id.strip():
                return None

            payload["ended_at"] = time.time()
            payload["last_activity_at"] = 0.0
            payload["languages"] = {}
            self._write_payload_to_handle(handle, payload)
            return SessionInfo(
                session_id=session_id,
                started_at=float(started_at or 0.0),
                is_new_session=False,
            )

    def read_payload(self) -> dict[str, Any]:
        with self._locked() as handle:
            return self._read_payload_from_handle(handle)

    def read_languages(self) -> dict[str, str]:
        return self._read_languages()

    def get_language(self, channel_key: str) -> str:
        channel = self.config.get_channel(channel_key)
        if channel.language_mode != "auto":
            return channel.source_language
        languages = self._read_languages()
        return languages.get(channel_key) or channel.source_language

    def has_learned_language(self, channel_key: str) -> bool:
        channel = self.config.get_channel(channel_key)
        if channel.language_mode != "auto":
            return False
        languages = self._read_languages()
        return bool(languages.get(channel_key))

    def update_language(self, channel_key: str, detected_language: str | None) -> bool:
        if not detected_language:
            return False

        with self._locked() as handle:
            payload = self._read_payload_from_handle(handle)
            languages = self._extract_languages(payload)
            if languages.get(channel_key) == detected_language:
                return False
            languages[channel_key] = detected_language
            payload["languages"] = languages
            payload["last_activity_at"] = time.time()
            self._write_payload_to_handle(handle, payload)
            return True

    def _read_languages(self) -> dict[str, str]:
        with self._locked() as handle:
            payload = self._read_payload_from_handle(handle)
            return self._extract_languages(payload)

    def _read_payload_from_handle(self, handle) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw_text = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return {}
        if not raw_text:
            return {}
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _extract_languages(self, payload: dict[str, Any]) -> dict[str, str]:
        maybe_languages = payload.get("languages")
        if isinstance(maybe_languages, dict):
            source = maybe_languages
        else:
            source = payload

        languages: dict[str, str] = {}
        for key in self.config.channels:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                languages[key] = value.strip()
        return languages

    def _should_start_new_session(self, payload: dict[str, Any], now: float) -> bool:
        if not payload:
            return True
        if payload.get("ended_at"):
            return True
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return True

        last_activity_at = payload.get("last_activity_at")
        started_at = payload.get("started_at")
        reference_time: float | None = None
        if isinstance(last_activity_at, (int, float)):
            reference_time = float(last_activity_at)
        elif isinstance(started_at, (int, float)):
            reference_time = float(started_at)
        else:
            try:
                reference_time = self.path.stat().st_mtime
            except OSError:
                reference_time = None

        if reference_time is None:
            return True
        return (now - reference_time) > self.config.session_join_window_seconds

    def _new_payload(self, now: float) -> dict[str, Any]:
        return {
            "session_id": str(uuid.uuid4()),
            "started_at": now,
            "last_activity_at": now,
            "ended_at": None,
            "languages": {},
        }

    def _payload_to_session_info(
        self,
        payload: dict[str, Any],
        *,
        is_new_session: bool,
    ) -> SessionInfo:
        return SessionInfo(
            session_id=str(payload["session_id"]),
            started_at=float(payload["started_at"]),
            is_new_session=is_new_session,
        )

    def _write_payload_to_handle(self, handle, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @contextmanager
    def _locked(self) -> Iterator[object]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as handle:
            self._acquire_lock(handle)
            try:
                yield handle
            finally:
                self._release_lock(handle)

    def _acquire_lock(self, handle) -> None:
        handle.seek(0)
        handle.write("0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":  # pragma: no cover - platform-specific
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:  # pragma: no cover - platform-specific
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _release_lock(self, handle) -> None:
        handle.seek(0)
        if os.name == "nt":  # pragma: no cover - platform-specific
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - platform-specific
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
