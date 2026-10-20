from __future__ import annotations

import json
import logging
import mimetypes
import os
import queue
import threading
import time
from collections import deque
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from backend.api.webhook import WebhookDispatcher
from backend.audio import MicrophoneRecorder
from backend.config import AppConfig
from backend.conversation_store import SessionConversationStore
from backend.gemma_client import GemmaClient
from backend.languages import LANGUAGE_DISPLAY_NAMES, normalize_language_code
from backend.nlp.aggregator import BIAggregator
from backend.nlp.entity_extractor import EntityExtractor
from backend.nlp.summarizer import ConversationSummarizer
from backend.pipeline import TranslationRunResult, TranslatorPipeline
from backend.session_state import SessionInfo, SessionLanguageStore
from backend.storage.glossary import GlossaryStore
from backend.db.auth import authenticate_user, get_user_profile, register_user, verify_token, update_user_profile
from backend.db.connection import init_db
from backend.db.rooms import (
    create_room,
    get_room_by_code,
    clear_room_transcripts,
    delete_room,
    clear_all_rooms,
    is_room_member,
    get_room_transcripts,
    get_session_insights,
    list_active_participants,
    list_user_rooms,
    verify_room_access,
)
from backend.rooms.coordinator import RoomCoordinator

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "web"
_MAX_SYSTEM_LOGS_FOR_UI = 40
_MAX_CHANNEL_LOGS_FOR_UI = 30
_EVENT_QUEUE_MAXSIZE = 200
_UNSET = object()


class DashboardState:
    def __init__(self, base_config: AppConfig) -> None:
        self.base_config = base_config
        self.session_store = SessionLanguageStore(base_config)
        self.conversation_store = SessionConversationStore(base_config.project_root)
        self._lock = threading.RLock()
        self._system_logs: deque[dict[str, Any]] = deque(maxlen=300)
        self._channel_logs: dict[str, deque[dict[str, Any]]] = {
            key: deque(maxlen=250)
            for key in base_config.channels
        }
        self._workers: dict[str, threading.Thread | None] = {
            key: None
            for key in base_config.channels
        }
        self._stop_events: dict[str, threading.Event | None] = {
            key: None
            for key in base_config.channels
        }
        self._last_results: dict[str, dict[str, Any] | None] = {
            key: None
            for key in base_config.channels
        }
        self._last_errors: dict[str, str | None] = {
            key: None
            for key in base_config.channels
        }
        self._live_channels: dict[str, dict[str, Any]] = {
            key: {
                "live_status": "stopped",
                "live_transcript": "",
                "live_updated_at": None,
            }
            for key in base_config.channels
        }
        self._event_subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._device_cache: dict[str, Any] = {
            "inputs": [],
            "outputs": [],
            "error": None,
            "refreshed_at": None,
        }
        self._channel_settings: dict[str, dict[str, Any]] = {
            key: {
                "input_device": channel.input_device,
                "output_device": channel.output_device,
                "language_mode": channel.language_mode,
                "language_code": normalize_language_code(channel.source_language) or "en",
            }
            for key, channel in base_config.channels.items()
        }

        self.glossary_store = GlossaryStore(base_config.project_root)
        self.gemma_client = GemmaClient(base_config)
        self.summarizer = ConversationSummarizer(self.gemma_client)
        self.aggregator = BIAggregator()
        self.webhook = WebhookDispatcher(base_config.webhook_url, base_config.webhook_secret)
        try:
            self.entity_extractor: EntityExtractor | None = EntityExtractor()
        except Exception:
            self.entity_extractor = None

        # Initialize Kinesia DB & Multi-Party Room Coordinator
        try:
            init_db()
        except Exception as exc:
            logger.warning("Database initialization notice: %s", exc)
        self.coordinator = RoomCoordinator(base_config)

        session_info = self.session_store.start_or_join_session()
        self.conversation_store.ensure_session(
            session_info.session_id,
            session_info.started_at,
        )
        self.refresh_devices()
        self._log_system(
            f"Dashboard ready for session {session_info.session_id}.",
            data={"session_id": session_info.session_id},
        )

    def refresh_devices(self) -> dict[str, Any]:
        try:
            devices = MicrophoneRecorder.list_audio_devices()
            inputs: list[dict[str, Any]] = []
            outputs: list[dict[str, Any]] = []
            for index, device in enumerate(devices):
                record = {
                    "value": index,
                    "label": self._device_label(index, device),
                    "name": device.get("name") or f"Device {index}",
                    "max_input_channels": int(device.get("max_input_channels") or 0),
                    "max_output_channels": int(device.get("max_output_channels") or 0),
                    "default_samplerate": device.get("default_samplerate"),
                }
                if record["max_input_channels"] > 0:
                    inputs.append(record)
                if record["max_output_channels"] > 0:
                    outputs.append(record)
            snapshot = {
                "inputs": inputs,
                "outputs": outputs,
                "error": None,
                "refreshed_at": time.time(),
            }
        except Exception as exc:
            snapshot = {
                "inputs": [],
                "outputs": [],
                "error": str(exc),
                "refreshed_at": time.time(),
            }
        with self._lock:
            self._device_cache = snapshot
        return snapshot

    def snapshot(
        self,
        *,
        include_conversation: bool = False,
        include_static: bool = False,
    ) -> dict[str, Any]:
        runtime_config = self._build_runtime_config()
        runtime_session_store = SessionLanguageStore(runtime_config)
        payload = self.session_store.read_payload()
        learned_languages = runtime_session_store.read_languages()
        session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
        conversation_summary = self.conversation_summary(session_id)
        conversation_turn_count = int(conversation_summary.get("turn_count") or 0)
        conversation_updated_at = conversation_summary.get("updated_at")
        conversation = self.conversation_snapshot(session_id) if include_conversation else None
        with self._lock:
            channels_snapshot = {
                key: {
                    "key": key,
                    "name": runtime_config.channels[key].name,
                    "running": self._is_running_unlocked(key),
                    "input_device": runtime_config.channels[key].input_device,
                    "output_device": runtime_config.channels[key].output_device,
                    "language_mode": runtime_config.channels[key].language_mode,
                    "language_code": runtime_config.channels[key].source_language,
                    "current_language": self._resolve_channel_language(
                        runtime_config,
                        key,
                        learned_languages,
                    ),
                    "has_learned_language": self._channel_has_learned_language(
                        runtime_config,
                        key,
                        learned_languages,
                    ),
                    "live_status": self._live_channels[key]["live_status"],
                    "live_transcript": self._live_channels[key]["live_transcript"],
                    "live_updated_at": self._live_channels[key]["live_updated_at"],
                    "last_result": self._last_results[key],
                    "last_error": self._last_errors[key],
                    "logs": self._tail_logs(self._channel_logs[key], _MAX_CHANNEL_LOGS_FOR_UI),
                }
                for key in runtime_config.channels
            }
            system_logs = self._tail_logs(self._system_logs, _MAX_SYSTEM_LOGS_FOR_UI)
            devices = dict(self._device_cache) if include_static else None
            languages = self._language_options() if include_static else None

        session_payload = {
            "id": session_id,
            "started_at": payload.get("started_at"),
            "last_activity_at": payload.get("last_activity_at"),
            "ended_at": payload.get("ended_at"),
            "is_active": bool(session_id) and not payload.get("ended_at"),
            "conversation_turn_count": conversation_turn_count,
            "conversation_updated_at": conversation_updated_at,
        }
        if include_conversation:
            session_payload["conversation"] = conversation

        return {
            "session": session_payload,
            "channels": channels_snapshot,
            "devices": devices,
            "languages": languages,
            "system_logs": system_logs,
            "server_time": time.time(),
        }

    def bootstrap(self) -> dict[str, Any]:
        with self._lock:
            devices = dict(self._device_cache)
        return {
            "devices": devices,
            "languages": self._language_options(),
            "glossary": [e.to_dict() for e in self.glossary_store.load_entries()],
            "save_audio_enabled": self.base_config.save_audio,
            "webhook_enabled": self.webhook.is_enabled,
            "server_time": time.time(),
        }

    def subscribe_events(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_EVENT_QUEUE_MAXSIZE)
        with self._lock:
            self._event_subscribers.add(subscriber)
        return subscriber

    def unsubscribe_events(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._event_subscribers.discard(subscriber)

    def conversation_snapshot(
        self,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = session_id
        if not resolved_session_id:
            payload = self.session_store.read_payload()
            maybe_session_id = payload.get("session_id")
            resolved_session_id = (
                maybe_session_id
                if isinstance(maybe_session_id, str) and maybe_session_id.strip()
                else None
            )
        if not resolved_session_id:
            return {
                "session_id": None,
                "started_at": None,
                "ended_at": None,
                "updated_at": None,
                "turns": [],
            }
        return self.conversation_store.read_session(resolved_session_id)

    def conversation_summary(
        self,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = session_id
        if not resolved_session_id:
            payload = self.session_store.read_payload()
            maybe_session_id = payload.get("session_id")
            resolved_session_id = (
                maybe_session_id
                if isinstance(maybe_session_id, str) and maybe_session_id.strip()
                else None
            )
        if not resolved_session_id:
            return {
                "session_id": None,
                "started_at": None,
                "ended_at": None,
                "updated_at": None,
                "turn_count": 0,
            }
        return self.conversation_store.read_summary(resolved_session_id)

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        channels_payload = payload.get("channels")
        if not isinstance(channels_payload, dict):
            raise ValueError("Expected a 'channels' object in the request body.")

        updated_settings: dict[str, dict[str, Any]] = {}
        for channel_key, raw_updates in channels_payload.items():
            if channel_key not in self._channel_settings:
                raise ValueError(f"Unknown channel '{channel_key}'.")
            if not isinstance(raw_updates, dict):
                raise ValueError(f"Expected an object for channel '{channel_key}'.")

            current = dict(self._channel_settings[channel_key])
            if "input_device" in raw_updates:
                current["input_device"] = self._parse_device_value(raw_updates.get("input_device"))
            if "output_device" in raw_updates:
                current["output_device"] = self._parse_device_value(raw_updates.get("output_device"))

            mode = str(raw_updates.get("language_mode", current["language_mode"])).strip().lower()
            if mode not in {"auto", "fixed"}:
                raise ValueError(
                    f"Unsupported language mode '{mode}' for channel '{channel_key}'."
                )
            current["language_mode"] = mode

            language_code = normalize_language_code(
                str(raw_updates.get("language_code", current["language_code"]))
            )
            if not language_code:
                raise ValueError(f"Channel '{channel_key}' needs a valid language code.")
            current["language_code"] = language_code

            self._validate_device(current["input_device"], kind="input")
            self._validate_device(current["output_device"], kind="output")
            updated_settings[channel_key] = current

        with self._lock:
            self._channel_settings.update(updated_settings)

        for channel_key, settings in updated_settings.items():
            self._log_channel(
                channel_key,
                "Runtime settings updated.",
                data=settings,
            )
        return self.snapshot()

    def start_channel(self, channel_key: str) -> dict[str, Any]:
        if channel_key not in self.base_config.channels:
            raise ValueError(f"Unknown channel '{channel_key}'.")

        self._ensure_active_session()
        with self._lock:
            if self._is_running_unlocked(channel_key):
                return self.snapshot()
            stop_event = threading.Event()
            worker = threading.Thread(
                target=self._run_channel_loop,
                args=(channel_key, stop_event),
                daemon=True,
                name=f"channel-{channel_key}-worker",
            )
            self._stop_events[channel_key] = stop_event
            self._workers[channel_key] = worker
            self._last_errors[channel_key] = None
            worker.start()

        self._set_channel_live_state(
            channel_key,
            status="listening",
            transcript="",
        )
        self._log_channel(channel_key, "Channel worker started.")
        return self.snapshot()

    def stop_channel(self, channel_key: str) -> dict[str, Any]:
        if channel_key not in self.base_config.channels:
            raise ValueError(f"Unknown channel '{channel_key}'.")
        worker, stop_event = self._get_worker(channel_key)
        if stop_event is not None:
            stop_event.set()
        if worker is not None:
            worker.join(timeout=3.0)
        with self._lock:
            if self._workers.get(channel_key) is worker:
                self._workers[channel_key] = None
                self._stop_events[channel_key] = None
        self._set_channel_live_state(
            channel_key,
            status="stopped",
            transcript="",
        )
        self._log_channel(channel_key, "Channel worker stopped.")
        return self.snapshot()

    def start_all_channels(self) -> dict[str, Any]:
        for channel_key in self.base_config.channels:
            self.start_channel(channel_key)
        return self.snapshot()

    def stop_all_channels(self) -> dict[str, Any]:
        for channel_key in self.base_config.channels:
            self.stop_channel(channel_key)
        return self.snapshot()

    def create_new_session(self) -> dict[str, Any]:
        self.stop_all_channels()
        session_info = self.session_store.create_new_session()
        self.conversation_store.ensure_session(
            session_info.session_id,
            session_info.started_at,
        )
        with self._lock:
            for channel_key in self.base_config.channels:
                self._last_results[channel_key] = None
                self._last_errors[channel_key] = None
                self._channel_logs[channel_key].clear()
        for channel_key in self.base_config.channels:
            self._set_channel_live_state(
                channel_key,
                status="stopped",
                transcript="",
            )
        self._log_system(
            f"Created new session {session_info.session_id}.",
            data={"session_id": session_info.session_id},
        )
        return self.snapshot()

    def end_session(self) -> dict[str, Any]:
        self.stop_all_channels()
        ended = self.session_store.end_current_session()
        if ended is not None:
            self.conversation_store.mark_session_ended(
                ended.session_id,
                ended_at=time.time(),
            )
            self._log_system(
                f"Ended session {ended.session_id}.",
                data={"session_id": ended.session_id},
            )
            try:
                turns = self.conversation_store.read_session(ended.session_id).get("turns", [])
                if turns:
                    summary_result = self.summarizer.summarize(ended.session_id, turns)
                    summary_payload = summary_result.to_dict()
                    self.conversation_store.save_summary(ended.session_id, summary_payload)
                    self.webhook.dispatch_session_summary_async(ended.session_id, summary_payload)
            except Exception as exc:
                logger.warning("Failed to auto-summarize or dispatch webhook on session end: %s", exc)
        else:
            self._log_system("End session requested, but no active session was present.")
        return self.snapshot()

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.conversation_store.list_sessions()

    def search_sessions(self, query: str, language: str | None = None) -> list[dict[str, Any]]:
        return self.conversation_store.search_turns(query, language=language)

    def get_session_insights(self, session_id: str | None = None) -> dict[str, Any]:
        resolved_id = session_id or self.session_store.read_payload().get("session_id")
        if not resolved_id:
            return {"error": "No active session"}
        session_data = self.conversation_store.read_session(resolved_id)
        turns = session_data.get("turns", [])
        summary = self.conversation_store.read_summary_file(resolved_id)
        return self.aggregator.build_session_insights(resolved_id, turns, summary)

    def generate_session_summary(self, session_id: str | None = None) -> dict[str, Any]:
        resolved_id = session_id or self.session_store.read_payload().get("session_id")
        if not resolved_id:
            raise ValueError("No active or specified session.")
        session_data = self.conversation_store.read_session(resolved_id)
        turns = session_data.get("turns", [])
        summary_result = self.summarizer.summarize(resolved_id, turns)
        summary_payload = summary_result.to_dict()
        self.conversation_store.save_summary(resolved_id, summary_payload)
        self._emit_event("refresh", {"reason": "summary-generated", "session_id": resolved_id})
        return summary_payload

    def get_session_summary(self, session_id: str | None = None) -> dict[str, Any] | None:
        resolved_id = session_id or self.session_store.read_payload().get("session_id")
        if not resolved_id:
            return None
        return self.conversation_store.read_summary_file(resolved_id)

    def patch_turn_language(
        self,
        session_id: str,
        turn_index: int,
        new_source_lang: str,
        *,
        retranslate: bool = True,
    ) -> dict[str, Any]:
        resolved_id = session_id or self.session_store.read_payload().get("session_id")
        session_data = self.conversation_store.read_session(resolved_id)
        turns = session_data.get("turns", [])
        if not isinstance(turns, list) or turn_index < 0 or turn_index >= len(turns):
            raise ValueError(f"Turn index {turn_index} invalid for session '{resolved_id}'")

        target_turn = turns[turn_index]
        updates: dict[str, Any] = {
            "detected_source_language": new_source_lang,
            "speaker_session_language": new_source_lang,
        }

        if retranslate and target_turn.get("transcript"):
            speaker_key = target_turn.get("speaker_channel_key") or "ch1"
            listener_key = target_turn.get("listener_channel_key") or "ch2"
            speaker_ch = self.base_config.get_channel(speaker_key)
            listener_ch = self.base_config.get_channel(listener_key)
            target_lang = target_turn.get("listener_language") or "en"

            glossary_hint = ""
            matched = self.glossary_store.find_matched_entries(target_turn["transcript"])
            glossary_hint = self.glossary_store.build_prompt_hint(matched)

            new_translation = self.gemma_client.translate(
                target_turn["transcript"],
                speaker_ch,
                listener_ch,
                new_source_lang,
                target_lang,
                new_source_lang,
                glossary_hint=glossary_hint,
            )
            updates["translation"] = new_translation

            if self.entity_extractor is not None:
                try:
                    updates["entities"] = self.entity_extractor.extract_combined(
                        target_turn["transcript"],
                        new_translation,
                    )
                except Exception as exc:
                    logger.warning("Entity extraction on retranslation failed: %s", exc)

        updated_turn = self.conversation_store.update_turn(resolved_id, turn_index, updates)
        self._emit_event("refresh", {"reason": "turn-corrected", "turn_index": turn_index})
        return updated_turn

    def get_glossary(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.glossary_store.load_entries()]

    def save_glossary(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.glossary_store.save_entries(entries)
        self._emit_event("refresh", {"reason": "glossary-updated"})
        return self.get_glossary()

    def get_turn_audio(self, session_id: str, filename: str) -> tuple[bytes, str]:
        safe_session = Path(session_id).name
        safe_filename = Path(filename).name
        filepath = self.conversation_store.audio_dir(safe_session) / safe_filename
        if not filepath.exists() or not filepath.is_file():
            raise FileNotFoundError(f"Audio file '{safe_filename}' not found for session '{safe_session}'")
        return filepath.read_bytes(), "audio/wav"

    def _run_channel_loop(
        self,
        channel_key: str,
        stop_event: threading.Event,
    ) -> None:
        try:
            while not stop_event.is_set():
                runtime_config = self._build_runtime_config()
                pipeline = TranslatorPipeline(runtime_config)
                session_info = pipeline.session_info
                self.conversation_store.ensure_session(
                    session_info.session_id,
                    session_info.started_at,
                )
                channel = runtime_config.get_channel(channel_key)
                self._log_channel(
                    channel_key,
                    "Listening for speech.",
                    data={
                        "session_id": session_info.session_id,
                        "input_device": channel.input_device,
                        "output_device": channel.output_device,
                        "language_mode": channel.language_mode,
                        "language_code": channel.source_language,
                    },
                )

                self._set_channel_live_state(
                    channel_key,
                    status="listening",
                    transcript="",
                )

                def handle_speech_start() -> None:
                    self._log_channel(channel_key, "Speech detected.")
                    self._set_channel_live_state(
                        channel_key,
                        status="speaking",
                        transcript="",
                    )

                def handle_speech_end() -> None:
                    self._set_channel_live_state(
                        channel_key,
                        status="processing",
                    )

                def handle_interim(text: str) -> None:
                    if stop_event.is_set():
                        return
                    interim = text.strip()
                    if interim:
                        self._set_channel_live_state(
                            channel_key,
                            status="speaking",
                            transcript=interim,
                        )
                        return
                    self._set_channel_live_state(
                        channel_key,
                        status="processing",
                    )

                result = pipeline.run_once_live(
                    channel_key,
                    stop_event=stop_event,
                    on_speech_start=handle_speech_start,
                    on_speech_end=handle_speech_end,
                    on_interim=handle_interim,
                )
                if stop_event.is_set():
                    break
                if result is None:
                    continue

                self._record_turn(session_info, result)
                self._set_channel_live_state(
                    channel_key,
                    status="listening",
                    transcript="",
                )
        except BaseException as exc:
            if not stop_event.is_set():
                with self._lock:
                    self._last_errors[channel_key] = str(exc)
                self._set_channel_live_state(
                    channel_key,
                    status="error",
                    transcript="",
                )
                self._log_channel(
                    channel_key,
                    f"Channel worker failed: {exc}",
                    level="error",
                )
                self._emit_event(
                    "refresh",
                    {
                        "channel_key": channel_key,
                        "reason": "channel-error",
                    },
                )
        finally:
            with self._lock:
                self._workers[channel_key] = None
                self._stop_events[channel_key] = None
            self._set_channel_live_state(
                channel_key,
                status="stopped",
                transcript="",
            )

    def _record_turn(
        self,
        session_info: SessionInfo,
        result: TranslationRunResult,
    ) -> None:
        audio_file = None
        if self.base_config.save_audio and getattr(result, "captured_audio", None) is not None:
            try:
                turn_count = self.conversation_summary(session_info.session_id).get("turn_count", 0)
                audio_file = self.conversation_store.save_turn_audio(
                    session_info.session_id,
                    turn_count,
                    result.speaker_channel_key,
                    result.captured_audio.raw_pcm_s16le,
                    sample_rate=result.captured_audio.sample_rate,
                )
            except Exception as exc:
                logger.warning("Failed to save turn audio: %s", exc)

        turn = {
            "captured_at": time.time(),
            "speaker_channel_key": result.speaker_channel_key,
            "speaker_channel_name": result.speaker_channel_name,
            "listener_channel_key": result.listener_channel_key,
            "listener_channel_name": result.listener_channel_name,
            "detected_source_language": result.detected_source_language,
            "speaker_session_language": result.speaker_session_language,
            "listener_language": result.listener_language,
            "transcript": result.transcript,
            "translation": result.translation,
            "stt_confidence": getattr(result, "stt_confidence", 1.0),
            "entities": getattr(result, "entities", {}),
            "audio_file": audio_file,
            "tts_provider": result.tts_provider,
            "latency_ms": {
                "gemma": round(result.gemma_latency_ms, 2),
                "tts": round(result.tts_latency_ms, 2),
                "playback": round(result.playback_latency_ms, 2),
                "total": round(result.turn_latency_ms, 2),
            },
            "speaker_language_updated_this_turn": result.speaker_language_updated_this_turn,
        }
        self.conversation_store.append_turn(session_info.session_id, turn)

        result_snapshot = self._serialize_result(result)
        with self._lock:
            self._last_results[result.speaker_channel_key] = result_snapshot
            self._last_errors[result.speaker_channel_key] = None

        self._log_channel(
            result.speaker_channel_key,
            "Turn completed.",
            data={
                "detected_source_language": result.detected_source_language,
                "speaker_session_language": result.speaker_session_language,
                "listener_language": result.listener_language,
                "gemma_latency_ms": round(result.gemma_latency_ms, 2),
                "tts_latency_ms": round(result.tts_latency_ms, 2),
                "playback_latency_ms": round(result.playback_latency_ms, 2),
                "turn_latency_ms": round(result.turn_latency_ms, 2),
                "stt_confidence": getattr(result, "stt_confidence", 1.0),
                "entities_count": sum(len(v) for v in getattr(result, "entities", {}).values()),
            },
        )
        self._emit_event(
            "refresh",
            {
                "channel_key": result.speaker_channel_key,
                "reason": "turn-completed",
            },
        )

    def _ensure_active_session(self) -> SessionInfo:
        payload = self.session_store.read_payload()
        if payload.get("ended_at"):
            session_info = self.session_store.create_new_session()
        else:
            session_info = self.session_store.start_or_join_session()
        self.conversation_store.ensure_session(
            session_info.session_id,
            session_info.started_at,
        )
        return session_info

    def _build_runtime_config(self) -> AppConfig:
        with self._lock:
            channels = {}
            for key, base_channel in self.base_config.channels.items():
                settings = self._channel_settings[key]
                channels[key] = replace(
                    base_channel,
                    source_language=settings["language_code"],
                    language_mode=settings["language_mode"],
                    input_device=settings["input_device"],
                    output_device=settings["output_device"],
                )
        return replace(self.base_config, channels=channels)

    def _get_worker(
        self,
        channel_key: str,
    ) -> tuple[threading.Thread | None, threading.Event | None]:
        with self._lock:
            return self._workers.get(channel_key), self._stop_events.get(channel_key)

    def _is_running_unlocked(self, channel_key: str) -> bool:
        worker = self._workers.get(channel_key)
        return bool(worker and worker.is_alive())

    def _serialize_result(self, result: TranslationRunResult) -> dict[str, Any]:
        return {
            "speaker_channel_key": result.speaker_channel_key,
            "speaker_channel_name": result.speaker_channel_name,
            "listener_channel_key": result.listener_channel_key,
            "listener_channel_name": result.listener_channel_name,
            "detected_source_language": result.detected_source_language,
            "speaker_session_language": result.speaker_session_language,
            "listener_language": result.listener_language,
            "transcript": result.transcript,
            "translation": result.translation,
            "tts_provider": result.tts_provider,
            "gemma_latency_ms": round(result.gemma_latency_ms, 2),
            "tts_latency_ms": round(result.tts_latency_ms, 2),
            "playback_latency_ms": round(result.playback_latency_ms, 2),
            "turn_latency_ms": round(result.turn_latency_ms, 2),
            "speaker_language_updated_this_turn": result.speaker_language_updated_this_turn,
            "stt_confidence": getattr(result, "stt_confidence", 1.0),
            "entities": getattr(result, "entities", {}),
        }

    def _log_system(
        self,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        entry = self._log_entry(
            message=message,
            channel_key="system",
            level=level,
            data=data,
        )
        with self._lock:
            self._system_logs.append(entry)

    def _log_channel(
        self,
        channel_key: str,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        entry = self._log_entry(
            message=message,
            channel_key=channel_key,
            level=level,
            data=data,
        )
        with self._lock:
            self._channel_logs[channel_key].append(entry)

    def _set_channel_live_state(
        self,
        channel_key: str,
        *,
        status: str | None = None,
        transcript: object = _UNSET,
    ) -> None:
        with self._lock:
            current = dict(self._live_channels[channel_key])
            next_status = status or str(current.get("live_status") or "stopped")
            next_transcript = (
                str(current.get("live_transcript") or "")
                if transcript is _UNSET
                else str(transcript or "")
            )
            if (
                current.get("live_status") == next_status
                and current.get("live_transcript") == next_transcript
            ):
                return
            next_state = {
                "live_status": next_status,
                "live_transcript": next_transcript,
                "live_updated_at": time.time(),
            }
            self._live_channels[channel_key] = next_state
        self._emit_event(
            "channel-live",
            {
                "channel_key": channel_key,
                **next_state,
            },
        )

    def _emit_event(
        self,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        event = {
            "type": event_type,
            "data": data,
        }
        with self._lock:
            subscribers = list(self._event_subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    continue

    def _log_entry(
        self,
        *,
        message: str,
        channel_key: str,
        level: str,
        data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        timestamp = time.time()
        return {
            "timestamp": timestamp,
            "time_label": time.strftime("%H:%M:%S", time.localtime(timestamp)),
            "channel_key": channel_key,
            "level": level,
            "message": message,
            "data": data or {},
        }

    def _language_options(self) -> list[dict[str, str]]:
        return [
            {"code": code, "label": f"{name} ({code})"}
            for code, name in sorted(
                LANGUAGE_DISPLAY_NAMES.items(),
                key=lambda item: item[1].lower(),
            )
        ]

    def _tail_logs(
        self,
        entries: deque[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        if len(entries) <= limit:
            return list(entries)
        return list(entries)[-limit:]

    def _resolve_channel_language(
        self,
        runtime_config: AppConfig,
        channel_key: str,
        learned_languages: dict[str, str],
    ) -> str:
        channel = runtime_config.get_channel(channel_key)
        if channel.language_mode != "auto":
            return channel.source_language
        return learned_languages.get(channel_key) or channel.source_language

    def _channel_has_learned_language(
        self,
        runtime_config: AppConfig,
        channel_key: str,
        learned_languages: dict[str, str],
    ) -> bool:
        channel = runtime_config.get_channel(channel_key)
        if channel.language_mode != "auto":
            return False
        return bool(learned_languages.get(channel_key))

    def _device_label(self, index: int, device: dict[str, Any]) -> str:
        return (
            f"[{index}] {device.get('name')} | "
            f"in={device.get('max_input_channels')} | "
            f"out={device.get('max_output_channels')}"
        )

    def _parse_device_value(self, value: Any) -> int | str | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        if text.lstrip("-").isdigit():
            return int(text)
        return text

    def _validate_device(
        self,
        value: int | str | None,
        *,
        kind: str,
    ) -> None:
        if value is None:
            return
        MicrophoneRecorder.get_device_info(value, kind=kind)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: DashboardState

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, Accept")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def _send_v1_json(self, data: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = {
            "ok": True,
            "data": data,
            "error": None,
        }
        self._send_json(payload, status=status)

    def _send_v1_error(self, message: str, *, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        payload = {
            "ok": False,
            "data": None,
            "error": message,
        }
        self._send_json(payload, status=status)

    def _serve_audio(self, session_id: str, filename: str) -> None:
        try:
            content, ctype = self.state.get_turn_audio(session_id, filename)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "Audio file not found")
        except Exception as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _get_auth_user(self) -> dict[str, Any] | None:
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:].strip()
        payload = verify_token(token)
        if not payload:
            return None
        return get_user_profile(payload["sub"])

    def _serve_room_events(self, room_code: str, token: str, speaking_lang: str, listening_lang: str) -> None:
        payload = verify_token(token)
        if not payload:
            self.send_error(HTTPStatus.UNAUTHORIZED, "Invalid or expired token")
            return
        user_profile = get_user_profile(payload["sub"])
        if not user_profile:
            self.send_error(HTTPStatus.UNAUTHORIZED, "User not found")
            return

        try:
            room, event_queue = self.state.coordinator.join_room(
                room_code,
                user_profile,
                speaking_lang=speaking_lang,
                listening_lang=listening_lang,
            )
        except Exception as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        init_event = {
            "type": "connected",
            "room_code": room_code,
            "title": room.title,
            "user_id": user_profile["id"],
            "participants": [
                {
                    "user_id": p.user_id,
                    "user_name": p.user_name,
                    "avatar_color": p.avatar_color,
                    "speaking_lang": p.speaking_lang,
                    "listening_lang": p.listening_lang,
                }
                for p in room.participants.values()
            ],
        }
        self._write_sse_event("connected", init_event)

        try:
            while True:
                try:
                    event = event_queue.get(timeout=15.0)
                    self._write_sse_event(event.get("type", "message"), event.get("data", {}))
                except queue.Empty:
                    self._write_sse_comment("keepalive")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.state.coordinator.leave_room(room_code, user_profile["id"])

    def do_GET(self) -> None:  # noqa: N802
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        # Health check endpoints
        if path in {"/api/health", "/api/v1/health"}:
            self._send_v1_json({"status": "healthy", "service": "kinesia", "time": time.time()})
            return

        # Static assets and SPA client-side routing
        if path in {"/", "/index.html"}:
            self._serve_static("index.html")
            return
        if path.startswith("/assets/"):
            self._serve_static(path.lstrip("/"))
            return
        if path in {"/app.js", "/styles.css", "/favicon.svg", "/favicon.ico", "/icons.svg"}:
            self._serve_static(path.lstrip("/"))
            return

        # Generic static file fallback if file exists in _STATIC_DIR
        clean_rel = path.lstrip("/")
        if clean_rel and not path.startswith("/api/") and not path.startswith("/events"):
            try:
                candidate = (_STATIC_DIR / clean_rel).resolve()
                candidate.relative_to(_STATIC_DIR)
                if candidate.is_file():
                    self._serve_static(clean_rel)
                    return
            except (ValueError, OSError):
                pass

        # SSE stream (both /api/events and /api/v1/events)
        if path in {"/api/events", "/api/v1/events"}:
            self._serve_event_stream()
            return

        # Audio file serving: /api/v1/audio/{session_id}/{filename}
        if path.startswith("/api/v1/audio/"):
            parts = path.split("/")
            if len(parts) >= 6:
                session_id = parts[4]
                filename = parts[5]
                self._serve_audio(session_id, filename)
                return

        # Versioned /api/v1/ endpoints
        if path == "/api/v1/bootstrap":
            self._send_v1_json(self.state.bootstrap())
            return
        if path == "/api/v1/state":
            self._send_v1_json(self.state.snapshot(include_conversation=False))
            return
        if path == "/api/v1/sessions":
            self._send_v1_json(self.state.list_sessions())
            return
        if path == "/api/v1/sessions/search":
            q = query_params.get("q", [""])[0]
            lang = query_params.get("lang", [None])[0]
            self._send_v1_json(self.state.search_sessions(q, language=lang))
            return
        if path.startswith("/api/v1/sessions/"):
            session_id = path.split("/")[4]
            self._send_v1_json(self.state.conversation_snapshot(session_id))
            return
        if path.startswith("/api/v1/insights/") and path.endswith("/summary"):
            session_id = path.split("/")[4]
            self._send_v1_json(self.state.get_session_summary(session_id))
            return
        if path.startswith("/api/v1/insights/"):
            session_id = path.split("/")[4]
            self._send_v1_json(self.state.get_session_insights(session_id))
            return
        if path == "/api/v1/glossary":
            self._send_v1_json(self.state.get_glossary())
            return

        # Kinesia Auth & Room Endpoints
        if path == "/api/v1/auth/me":
            user = self._get_auth_user()
            if not user:
                self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                return
            self._send_v1_json(user)
            return

        if path == "/api/v1/rooms":
            user = self._get_auth_user()
            if not user:
                self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                return
            self._send_v1_json(list_user_rooms(user["id"]))
            return

        if path.startswith("/events/rooms/"):
            room_code = path[len("/events/rooms/"):].split("/")[0]
            token = query_params.get("token", [""])[0]
            speaking_lang = query_params.get("speaking_lang", ["en"])[0]
            listening_lang = query_params.get("listening_lang", ["hi"])[0]
            self._serve_room_events(room_code, token, speaking_lang, listening_lang)
            return

        if path.startswith("/api/v1/rooms/"):
            subpath = path[len("/api/v1/rooms/"):]
            parts = subpath.split("/")
            room_code = parts[0]

            if len(parts) >= 2 and parts[1] == "events":
                token = query_params.get("token", [""])[0]
                speaking_lang = query_params.get("speaking_lang", ["en"])[0]
                listening_lang = query_params.get("listening_lang", ["hi"])[0]
                self._serve_room_events(room_code, token, speaking_lang, listening_lang)
                return

            if len(parts) >= 2 and parts[1] == "participants":
                room = get_room_by_code(room_code)
                if not room:
                    self._send_v1_error("Room not found", status=HTTPStatus.NOT_FOUND)
                    return
                self._send_v1_json(list_active_participants(room["id"]))
                return

            if len(parts) >= 2 and parts[1] == "transcripts":
                room = get_room_by_code(room_code)
                if not room:
                    self._send_v1_error("Room not found", status=HTTPStatus.NOT_FOUND)
                    return
                user = self._get_auth_user()
                if not user:
                    self._send_v1_error("Unauthorized: Sign in required", status=HTTPStatus.UNAUTHORIZED)
                    return
                if not is_room_member(room["id"], user["id"]):
                    self._send_v1_error("Forbidden: Only room participants can view logs", status=HTTPStatus.FORBIDDEN)
                    return
                self._send_v1_json(get_room_transcripts(room["id"]))
                return

            if len(parts) >= 2 and parts[1] == "insights":
                room = get_room_by_code(room_code)
                if not room:
                    self._send_v1_error("Room not found", status=HTTPStatus.NOT_FOUND)
                    return
                self._send_v1_json(get_session_insights(room["id"]) or {})
                return

            if len(parts) == 1:
                room = get_room_by_code(room_code)
                if not room:
                    self._send_v1_error("Room not found", status=HTTPStatus.NOT_FOUND)
                    return
                self._send_v1_json(room)
                return

        # Legacy endpoints
        if path == "/api/bootstrap":
            self._send_json(self.state.bootstrap())
            return
        if path == "/api/state":
            self._send_json(self.state.snapshot(include_conversation=False))
            return
        # SPA Fallback for client-side routing
        if not path.startswith("/api/") and not path.startswith("/events"):
            self._serve_static("index.html")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        is_v1 = path.startswith("/api/v1/")

        try:
            payload = self._read_json_body()
        except ValueError as exc:
            if is_v1:
                self._send_v1_error(str(exc), status=HTTPStatus.BAD_REQUEST)
            else:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        try:
            if path in {"/api/settings", "/api/v1/settings"}:
                response = self.state.update_settings(payload)
            elif path in {"/api/session/new", "/api/v1/session/new"}:
                response = self.state.create_new_session()
            elif path in {"/api/session/end", "/api/v1/session/end"}:
                response = self.state.end_session()
            elif path in {"/api/channels/start-all", "/api/v1/channels/start-all"}:
                response = self.state.start_all_channels()
            elif path in {"/api/channels/stop-all", "/api/v1/channels/stop-all"}:
                response = self.state.stop_all_channels()
            elif path in {"/api/devices/refresh", "/api/v1/devices/refresh"}:
                self.state.refresh_devices()
                response = self.state.bootstrap()
            elif path in {"/api/v1/glossary"}:
                entries = payload.get("glossary", payload) if isinstance(payload, dict) else payload
                response = self.state.save_glossary(entries if isinstance(entries, list) else [])
            elif path.startswith("/api/v1/insights/") and path.endswith("/summarize"):
                session_id = path.split("/")[4]
                response = self.state.generate_session_summary(session_id)
            elif (path.startswith("/api/channels/") or path.startswith("/api/v1/channels/")) and path.endswith("/start"):
                channel_key = path.split("/")[4 if is_v1 else 3]
                response = self.state.start_channel(channel_key)
            elif (path.startswith("/api/channels/") or path.startswith("/api/v1/channels/")) and path.endswith("/stop"):
                channel_key = path.split("/")[4 if is_v1 else 3]
                response = self.state.stop_channel(channel_key)
            # Kinesia Auth & Room Endpoints
            elif path == "/api/v1/auth/register":
                response = register_user(
                    username=payload.get("username", ""),
                    email=payload.get("email", ""),
                    password=payload.get("password", ""),
                    display_name=payload.get("display_name"),
                    default_input_lang=payload.get("default_input_lang", "en"),
                    default_output_lang=payload.get("default_output_lang", "hi"),
                )
            elif path == "/api/v1/auth/login":
                response = authenticate_user(
                    login_id=payload.get("login", "") or payload.get("username", "") or payload.get("email", ""),
                    password=payload.get("password", ""),
                )
            elif path == "/api/v1/rooms":
                user = self._get_auth_user()
                if not user:
                    self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                    return
                response = create_room(
                    title=payload.get("title", "Kinesia Dialogue"),
                    host_id=user["id"],
                    passcode=payload.get("passcode"),
                    record_session=payload.get("record_session", True),
                )
            elif path.startswith("/api/v1/rooms/") and path.endswith("/verify"):
                room_code = path.split("/")[4]
                response = {"valid": verify_room_access(room_code, payload.get("passcode"))}
            elif path.startswith("/api/v1/rooms/") and path.endswith("/speak"):
                user = self._get_auth_user()
                if not user:
                    self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                    return
                room_code = path.split("/")[4]
                response = self.state.coordinator.process_utterance(
                    room_code=room_code,
                    speaker_id=user["id"],
                    speaker_name=user.get("display_name") or user["username"],
                    text=payload.get("text", ""),
                    source_lang=payload.get("source_lang", "en"),
                    confidence=float(payload.get("confidence", 1.0)),
                )
            elif path.startswith("/api/v1/rooms/") and path.endswith("/end"):
                user = self._get_auth_user()
                if not user:
                    self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                    return
                room_code = path.split("/")[4]
                response = self.state.coordinator.conclude_session(room_code)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
        except ValueError as exc:
            if is_v1:
                self._send_v1_error(str(exc), status=HTTPStatus.BAD_REQUEST)
            else:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:  # pragma: no cover
            logger.exception("dashboard_request_failed path=%s", path)
            if is_v1:
                self._send_v1_error(f"Server error: {exc}", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self._send_json({"error": f"Server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if is_v1:
            self._send_v1_json(response)
        else:
            self._send_json(response)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        if path.startswith("/api/v1/rooms/"):
            tail = path[len("/api/v1/rooms/"):]
            parts = tail.split("/")
            room_code = parts[0]
            if len(parts) >= 2 and parts[1] == "transcripts":
                room = get_room_by_code(room_code)
                if not room:
                    self._send_v1_error("Room not found", status=HTTPStatus.NOT_FOUND)
                    return
                user = self._get_auth_user()
                if not user:
                    self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                    return
                if not is_room_member(room["id"], user["id"]):
                    self._send_v1_error("Forbidden: Only participants can clear room logs", status=HTTPStatus.FORBIDDEN)
                    return
                try:
                    self.state.coordinator.clear_transcripts(
                        room_code,
                        user["id"],
                        user.get("display_name") or user.get("username", ""),
                    )
                except Exception:
                    clear_room_transcripts(room["id"])
                self._send_v1_json({"ok": True, "message": "Transcripts cleared"})
                return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_json_body()

        if path == "/api/v1/auth/profile":
            user = self._get_auth_user()
            if not user:
                self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                return
            try:
                display_name = payload.get("display_name")
                avatar_color = payload.get("avatar_color")
                default_input_lang = payload.get("default_input_lang")
                default_output_lang = payload.get("default_output_lang")
                updated = update_user_profile(
                    user["id"],
                    display_name=display_name,
                    avatar_color=avatar_color,
                    default_input_lang=default_input_lang,
                    default_output_lang=default_output_lang,
                )
                self._send_v1_json(updated)
                return
            except Exception as exc:
                self._send_v1_error(f"Failed to update profile: {exc}", status=HTTPStatus.BAD_REQUEST)
                return

        # Route: PATCH /api/v1/sessions/{session_id}/turns/{turn_index}
        if path.startswith("/api/v1/sessions/") and "/turns/" in path:
            parts = path.split("/")
            try:
                session_id = parts[4]
                turn_index = int(parts[6])
                new_lang = str(payload.get("detected_source_language") or payload.get("language_code") or "").strip()
                if not new_lang:
                    self._send_v1_error("Field 'detected_source_language' or 'language_code' is required.")
                    return
                retranslate = bool(payload.get("retranslate", True))
                updated = self.state.patch_turn_language(
                    session_id,
                    turn_index,
                    new_lang,
                    retranslate=retranslate,
                )
                self._send_v1_json(updated)
                return
            except (IndexError, ValueError) as exc:
                self._send_v1_error(str(exc), status=HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:
                logger.exception("turn_patch_failed path=%s", path)
                self._send_v1_error(f"Failed to update turn: {exc}", status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_DELETE(self) -> None:  # noqa: N802
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        # DELETE /api/v1/rooms/clear-all or /api/v1/rooms
        if path in {"/api/v1/rooms", "/api/v1/rooms/clear-all"}:
            user = self._get_auth_user()
            if not user:
                self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                return
            count = clear_all_rooms(user["id"])
            self._send_v1_json({"ok": True, "message": f"Cleared {count} sessions"})
            return

        # DELETE /api/v1/rooms/{room_code}
        if path.startswith("/api/v1/rooms/"):
            user = self._get_auth_user()
            if not user:
                self._send_v1_error("Unauthorized", status=HTTPStatus.UNAUTHORIZED)
                return
            parts = path.split("/")
            if len(parts) >= 5:
                room_code = parts[4]
                # Also clean up from active memory if currently open
                try:
                    self.state.coordinator.active_rooms.pop(room_code.lower(), None)
                except Exception:
                    pass
                success = delete_room(room_code)
                if success:
                    self._send_v1_json({"ok": True, "message": f"Session {room_code} deleted"})
                else:
                    self._send_v1_error(f"Session {room_code} not found", status=HTTPStatus.NOT_FOUND)
                return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.debug("dashboard_http " + format, *args)

    def _read_json_body(self) -> Any:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc
        if not isinstance(parsed, (dict, list)):
            raise ValueError("Request body must be a JSON object or array.")
        return parsed

    def _serve_static(self, filename: str) -> None:
        path = (_STATIC_DIR / filename).resolve()
        try:
            path.relative_to(_STATIC_DIR)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content = path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(path))
        if str(path).endswith(".js"):
            content_type = "application/javascript; charset=utf-8"
        elif str(path).endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif str(path).endswith(".svg"):
            content_type = "image/svg+xml"

        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)

    def _serve_event_stream(self) -> None:
        subscriber = self.state.subscribe_events()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = False
            self._write_sse_comment("connected")
            while True:
                try:
                    event = subscriber.get(timeout=15.0)
                except queue.Empty:
                    self._write_sse_comment("keepalive")
                    continue
                self._write_sse_event(
                    str(event.get("type") or "message"),
                    event.get("data") or {},
                )
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            return
        finally:
            self.state.unsubscribe_events(subscriber)

    def _write_sse_comment(self, text: str) -> None:
        self.wfile.write(f": {text}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _write_sse_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=True)
        self.wfile.write(
            f"event: {event_type}\ndata: {body}\n\n".encode("utf-8")
        )
        self.wfile.flush()

    def _send_json(
        self,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


def run_dashboard_server(
    config: AppConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    state = DashboardState(config)

    class Handler(DashboardRequestHandler):
        pass

    Handler.state = state
    server, bound_port = _bind_dashboard_server(host, port, Handler)
    logger.info("dashboard_server_started url=http://%s:%s", host, bound_port)
    print(f"Dashboard running at http://{host}:{bound_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        state.stop_all_channels()
        server.server_close()


def _bind_dashboard_server(
    host: str,
    port: int,
    handler_type,
) -> tuple[ThreadingHTTPServer, int]:
    candidate_ports = [port]
    for fallback in (port + 1, 8787, 9000, 18080):
        if fallback > 0 and fallback not in candidate_ports:
            candidate_ports.append(fallback)

    last_error: OSError | None = None
    for candidate_port in candidate_ports:
        try:
            server = ThreadingHTTPServer((host, candidate_port), handler_type)
            if candidate_port != port:
                logger.warning(
                    "dashboard_port_fallback requested=%s bound=%s",
                    port,
                    candidate_port,
                )
            return server, candidate_port
        except OSError as exc:
            last_error = exc
            continue

    raise RuntimeError(
        f"Unable to bind the dashboard server on {host}. "
        f"Tried ports: {', '.join(str(item) for item in candidate_ports)}. "
        f"Last error: {last_error}"
    )


def main() -> None:
    """Entry point for executing the Kinesia dashboard server directly."""
    from backend.config import load_config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    run_dashboard_server(config, host=host, port=port)


if __name__ == "__main__":
    main()

