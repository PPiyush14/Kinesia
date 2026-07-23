from __future__ import annotations

import json
import logging
import mimetypes
import queue
import threading
import time
from collections import deque
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pi_translator.audio import MicrophoneRecorder
from pi_translator.config import AppConfig
from pi_translator.conversation_store import SessionConversationStore
from pi_translator.languages import LANGUAGE_DISPLAY_NAMES, normalize_language_code
from pi_translator.pipeline import TranslationRunResult, TranslatorPipeline
from pi_translator.session_state import SessionInfo, SessionLanguageStore

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
        else:
            self._log_system("End session requested, but no active session was present.")
        return self.snapshot()

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

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/bootstrap":
            self._send_json(self.state.bootstrap())
            return
        if path == "/api/events":
            self._serve_event_stream()
            return
        if path == "/api/state":
            self._send_json(self.state.snapshot(include_conversation=False))
            return
        if path == "/api/session/conversation":
            self._send_json(self.state.conversation_snapshot())
            return
        if path == "/":
            self._serve_static("index.html")
            return
        if path in {"/app.js", "/styles.css"}:
            self._serve_static(path.lstrip("/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_json_body()
        try:
            if path == "/api/settings":
                response = self.state.update_settings(payload)
            elif path == "/api/session/new":
                response = self.state.create_new_session()
            elif path == "/api/session/end":
                response = self.state.end_session()
            elif path == "/api/channels/start-all":
                response = self.state.start_all_channels()
            elif path == "/api/channels/stop-all":
                response = self.state.stop_all_channels()
            elif path == "/api/devices/refresh":
                self.state.refresh_devices()
                response = self.state.bootstrap()
            elif path.startswith("/api/channels/") and path.endswith("/start"):
                channel_key = path.split("/")[3]
                response = self.state.start_channel(channel_key)
            elif path.startswith("/api/channels/") and path.endswith("/stop"):
                channel_key = path.split("/")[3]
                response = self.state.stop_channel(channel_key)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:  # pragma: no cover - defensive server guard
            logger.exception("dashboard_request_failed path=%s", path)
            self._send_json(
                {"error": f"Server error: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(response)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.debug("dashboard_http " + format, *args)

    def _read_json_body(self) -> dict[str, Any]:
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
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object.")
        return parsed

    def _serve_static(self, filename: str) -> None:
        path = _STATIC_DIR / filename
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content = path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
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
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


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
