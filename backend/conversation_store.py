from __future__ import annotations

import json
import logging
import threading
import time
import wave
from pathlib import Path
from typing import Any

from backend.core.exceptions import StorageError

logger = logging.getLogger(__name__)


class SessionConversationStore:
    """Thread-safe persistent storage for translation session conversations.

    Manages conversation session JSON files under ``session_conversations/``,
    supporting turn appending, turn updating (e.g. language corrections),
    session summaries, audio artifact storage, and cross-session search.
    """

    def __init__(self, project_root: Path) -> None:
        self.root = project_root / "session_conversations"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._summary_cache: dict[str, dict[str, Any]] = {}

    def session_path(self, session_id: str) -> Path:
        """Returns the file path for a session's conversation JSON."""
        safe_session_id = session_id.strip()
        return self.root / f"{safe_session_id}.json"

    def summary_path(self, session_id: str) -> Path:
        """Returns the file path for a session's NLP summary JSON."""
        safe_session_id = session_id.strip()
        return self.root / f"{safe_session_id}_summary.json"

    def audio_dir(self, session_id: str) -> Path:
        """Returns the directory path for a session's recorded turn audio WAVs."""
        safe_session_id = session_id.strip()
        path = self.root / f"{safe_session_id}_audio"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_session(self, session_id: str, started_at: float) -> Path:
        """Ensure the session JSON file exists, creating it if needed."""
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
            try:
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            except OSError as exc:
                raise StorageError(f"Failed to initialize session file {path}: {exc}") from exc
            self._summary_cache[session_id] = self._summary_from_payload(payload)
        return path

    def append_turn(self, session_id: str, turn: dict[str, Any]) -> dict[str, Any]:
        """Append a completed conversation turn to the session JSON."""
        with self._lock:
            payload = self._read_unlocked(session_id)
            turns = payload.setdefault("turns", [])
            if not isinstance(turns, list):
                turns = []
                payload["turns"] = turns

            # Assign sequential turn_index if not already present
            if "turn_index" not in turn:
                turn["turn_index"] = len(turns)

            turns.append(turn)
            payload["updated_at"] = time.time()
            self._write_unlocked(session_id, payload)
            self._summary_cache[session_id] = self._summary_from_payload(payload)
            return payload

    def update_turn(
        self,
        session_id: str,
        turn_index: int,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Update fields of an existing turn by index (e.g., language correction).

        Args:
            session_id: Target session ID.
            turn_index: 0-based index of the turn to update.
            updates: Dictionary of key-value pairs to merge into the turn.

        Returns:
            The updated turn dictionary.

        Raises:
            StorageError: If the turn index is out of bounds or session missing.
        """
        with self._lock:
            payload = self._read_unlocked(session_id)
            turns = payload.get("turns")
            if not isinstance(turns, list) or turn_index < 0 or turn_index >= len(turns):
                raise StorageError(
                    f"Turn index {turn_index} out of bounds for session '{session_id}' "
                    f"(total turns: {len(turns) if isinstance(turns, list) else 0})"
                )

            target_turn = turns[turn_index]
            target_turn.update(updates)
            target_turn["updated_at"] = time.time()

            payload["updated_at"] = time.time()
            self._write_unlocked(session_id, payload)
            self._summary_cache[session_id] = self._summary_from_payload(payload)
            return target_turn

    def save_summary(self, session_id: str, summary_data: dict[str, Any]) -> Path:
        """Write NLP session summary to ``<session_id>_summary.json``."""
        path = self.summary_path(session_id)
        with self._lock:
            try:
                path.write_text(
                    json.dumps(summary_data, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            except OSError as exc:
                raise StorageError(f"Failed to save summary to {path}: {exc}") from exc
        return path

    def read_summary_file(self, session_id: str) -> dict[str, Any] | None:
        """Read NLP summary from ``<session_id>_summary.json`` if it exists."""
        path = self.summary_path(session_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to read summary file %s: %s", path, exc)
                return None

    def save_turn_audio(
        self,
        session_id: str,
        turn_index: int,
        channel_key: str,
        raw_pcm_s16le: bytes,
        sample_rate: int = 16000,
    ) -> str:
        """Save raw PCM audio bytes to a WAV file in the session's audio directory.

        Returns:
            The filename of the saved WAV file (relative to audio directory).
        """
        filename = f"turn_{turn_index:03d}_{channel_key}.wav"
        filepath = self.audio_dir(session_id) / filename
        try:
            with wave.open(str(filepath), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(raw_pcm_s16le)
        except OSError as exc:
            raise StorageError(f"Failed to write audio file {filepath}: {exc}") from exc
        return filename

    def mark_session_ended(
        self,
        session_id: str,
        *,
        ended_at: float | None = None,
    ) -> dict[str, Any]:
        """Mark the session as completed with a timestamp."""
        with self._lock:
            payload = self._read_unlocked(session_id)
            payload["ended_at"] = ended_at or time.time()
            payload["updated_at"] = payload["ended_at"]
            self._write_unlocked(session_id, payload)
            self._summary_cache[session_id] = self._summary_from_payload(payload)
            return payload

    def read_session(self, session_id: str) -> dict[str, Any]:
        """Read the full session dictionary including all turns."""
        with self._lock:
            return self._read_unlocked(session_id)

    def read_summary(self, session_id: str) -> dict[str, Any]:
        """Read lightweight metadata for the session."""
        with self._lock:
            cached = self._summary_cache.get(session_id)
            if cached is not None:
                return dict(cached)
            payload = self._read_unlocked(session_id)
            summary = self._summary_from_payload(payload)
            self._summary_cache[session_id] = summary
            return dict(summary)

    def list_sessions(self) -> list[dict[str, Any]]:
        """List metadata for all saved sessions on disk, sorted latest first."""
        sessions: list[dict[str, Any]] = []
        with self._lock:
            for file_path in self.root.glob("*.json"):
                if file_path.name.endswith("_summary.json"):
                    continue
                session_id = file_path.stem
                try:
                    meta = self.read_summary(session_id)
                    sessions.append(meta)
                except Exception as exc:
                    logger.debug("Failed to read session %s: %s", session_id, exc)
        sessions.sort(key=lambda s: s.get("started_at") or 0.0, reverse=True)
        return sessions

    def search_turns(
        self,
        query: str,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search across all saved session turns for text matches.

        Args:
            query: Case-insensitive search string.
            language: Optional language code filter.

        Returns:
            List of matching turns augmented with ``session_id``.
        """
        query_norm = query.strip().lower()
        if not query_norm:
            return []

        lang_norm = language.strip().lower() if language else None
        matches: list[dict[str, Any]] = []

        with self._lock:
            for file_path in self.root.glob("*.json"):
                if file_path.name.endswith("_summary.json"):
                    continue
                session_id = file_path.stem
                payload = self._read_unlocked(session_id)
                turns = payload.get("turns", [])
                if not isinstance(turns, list):
                    continue

                for turn in turns:
                    if not isinstance(turn, dict):
                        continue

                    # Language filter if requested
                    if lang_norm:
                        src_lang = str(turn.get("detected_source_language") or "").lower()
                        tgt_lang = str(turn.get("listener_language") or "").lower()
                        if lang_norm not in (src_lang, tgt_lang):
                            continue

                    transcript = str(turn.get("transcript") or "").lower()
                    translation = str(turn.get("translation") or "").lower()

                    if query_norm in transcript or query_norm in translation:
                        match_item = dict(turn)
                        match_item["session_id"] = session_id
                        matches.append(match_item)

        matches.sort(key=lambda t: t.get("captured_at") or 0.0, reverse=True)
        return matches

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
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            raise StorageError(f"Failed to write session file {path}: {exc}") from exc

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
