from __future__ import annotations

import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from websockets import ConnectionClosed, ConnectionClosedOK
from websockets.sync.client import connect

from backend.audio import CapturedAudio, MicrophoneRecorder
from backend.config import AppConfig, ChannelConfig


@dataclass(frozen=True)
class SonioxTranscriptionResult:
    transcript: str
    detected_language: str | None
    confidence: float = 1.0


@dataclass(frozen=True)
class SonioxLiveTranscriptionResult:
    captured_audio: CapturedAudio
    transcript: str
    detected_language: str | None
    confidence: float = 1.0


class _StreamingUtteranceAudioBuffer:
    def __init__(self, *, sample_rate: int, channels: int) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.current_chunks: list[bytes] = []
        self.lock = threading.Lock()

    def reset(self) -> None:
        with self.lock:
            self.current_chunks.clear()

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return

        with self.lock:
            self.current_chunks.append(chunk)

    def finalize(self) -> CapturedAudio:
        with self.lock:
            audio_bytes = b"".join(self.current_chunks)
            self.current_chunks.clear()

        duration = len(audio_bytes) / float(self.sample_rate * self.channels * 2)
        return CapturedAudio(
            raw_pcm_s16le=audio_bytes,
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_seconds=duration,
        )

    def has_audio(self) -> bool:
        with self.lock:
            return bool(self.current_chunks)


class SonioxClient:
    def __init__(self, config: AppConfig) -> None:
        self.api_key = config.soniox_api_key
        self.model = config.soniox_model
        self.websocket_url = config.soniox_websocket_url
        self.sample_rate = config.mic_sample_rate
        self.channels = config.mic_channels
        self.max_endpoint_delay_ms = config.soniox_max_endpoint_delay_ms
        self.keepalive_seconds = config.soniox_keepalive_seconds
        self.session_retry_attempts = max(0, config.soniox_session_retry_attempts)
        self.session_retry_delay_seconds = max(0.1, config.soniox_session_retry_delay_seconds)
        self.session_max_retry_delay_seconds = max(
            self.session_retry_delay_seconds,
            config.soniox_session_max_retry_delay_seconds,
        )
        self.chunk_ms = config.stt_stream_chunk_ms

    def transcribe_live_microphone(
        self,
        recorder: MicrophoneRecorder,
        channel: ChannelConfig,
        *,
        input_device: int | str | None = None,
        input_sample_rate_override: int | None = None,
        session_language_hint: str | None = None,
        on_interim: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_speech_end: Callable[[], None] | None = None,
    ) -> SonioxLiveTranscriptionResult | None:
        live_stream = self.stream_microphone_session(
            recorder,
            channel,
            input_device=input_device,
            input_sample_rate_override=input_sample_rate_override,
            session_language_hint=session_language_hint,
            on_interim=on_interim,
            stop_event=stop_event,
            on_speech_start=on_speech_start,
            on_speech_end=on_speech_end,
        )
        try:
            return next(live_stream)
        except StopIteration as exc:
            if stop_event is not None and stop_event.is_set():
                return None
            raise RuntimeError("Soniox live stream ended before a final transcript was received.") from exc
        finally:
            live_stream.close()

    def stream_microphone_session(
        self,
        recorder: MicrophoneRecorder,
        channel: ChannelConfig,
        *,
        input_device: int | str | None = None,
        input_sample_rate_override: int | None = None,
        session_language_hint: str | None = None,
        on_interim: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_speech_end: Callable[[], None] | None = None,
    ) -> Iterator[SonioxLiveTranscriptionResult]:
        attempt = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                if on_interim is not None:
                    on_interim("")
                return
            try:
                yield from self._stream_microphone_session_once(
                    recorder,
                    channel,
                    input_device=input_device,
                    input_sample_rate_override=input_sample_rate_override,
                    session_language_hint=session_language_hint,
                    on_interim=on_interim,
                    stop_event=stop_event,
                    on_speech_start=on_speech_start,
                    on_speech_end=on_speech_end,
                )
                return
            except GeneratorExit:
                raise
            except BaseException as exc:
                attempt += 1
                if stop_event is not None and stop_event.is_set():
                    if on_interim is not None:
                        on_interim("")
                    return
                if not self._should_retry_session_error(exc, attempt):
                    raise
                if on_interim is not None:
                    on_interim("")
                time.sleep(self._retry_delay_for_attempt(attempt))

    def _stream_microphone_session_once(
        self,
        recorder: MicrophoneRecorder,
        channel: ChannelConfig,
        *,
        input_device: int | str | None = None,
        input_sample_rate_override: int | None = None,
        session_language_hint: str | None = None,
        on_interim: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_speech_end: Callable[[], None] | None = None,
    ) -> Iterator[SonioxLiveTranscriptionResult]:
        if stop_event is not None and stop_event.is_set():
            return

        resolved_input_device = recorder.resolve_device(input_device, kind="input")
        input_sample_rate = recorder.resolve_input_sample_rate(
            resolved_input_device,
            channels=recorder.channels,
            preferred_sample_rate=input_sample_rate_override or recorder.sample_rate,
        )
        request_config = self._build_request_config(
            channel,
            session_language_hint,
            sample_rate=input_sample_rate,
            channels=recorder.channels,
        )
        final_tokens: list[dict] = []
        connection_stop_event = threading.Event()
        capture_stop_event = threading.Event()
        utterance_pause_event = threading.Event()
        send_lock = threading.Lock()
        capture_error: list[BaseException] = []
        receiver_error: list[Exception] = []
        last_interim_text = ""
        utterance_audio = _StreamingUtteranceAudioBuffer(
            sample_rate=input_sample_rate,
            channels=recorder.channels,
        )

        with connect(self.websocket_url) as websocket:
            websocket.send(json.dumps(request_config))

            def send_chunk(chunk: bytes) -> None:
                if capture_stop_event.is_set():
                    return
                utterance_audio.append(chunk)
                try:
                    with send_lock:
                        websocket.send(chunk)
                except Exception:
                    if not capture_stop_event.is_set():
                        raise

            def start_utterance() -> None:
                utterance_audio.reset()
                if on_speech_start is not None:
                    on_speech_start()

            def finish_utterance() -> None:
                if capture_stop_event.is_set():
                    return
                utterance_pause_event.set()
                if on_speech_end is not None:
                    on_speech_end()
                try:
                    with send_lock:
                        websocket.send(json.dumps({"type": "finalize", "trailing_silence_ms": 300}))
                except Exception:
                    if not capture_stop_event.is_set():
                        raise

            def capture_audio() -> None:
                try:
                    recorder.stream_continuous(
                        input_device=resolved_input_device,
                        sample_rate_override=input_sample_rate,
                        on_chunk=send_chunk,
                        on_speech_start=start_utterance,
                        on_speech_end=finish_utterance,
                        stop_event=capture_stop_event,
                        pause_event=utterance_pause_event,
                    )
                except BaseException as exc:  # pragma: no cover - thread handoff
                    if not capture_stop_event.is_set():
                        capture_error.append(exc)
                finally:
                    try:
                        if utterance_audio.has_audio():
                            with send_lock:
                                websocket.send(json.dumps({"type": "finalize", "trailing_silence_ms": 300}))
                    except Exception:
                        pass

            def watch_for_stop_request() -> None:
                if stop_event is None:
                    return
                stop_event.wait()
                if not stop_event.is_set():
                    return
                connection_stop_event.set()
                capture_stop_event.set()
                utterance_pause_event.set()
                try:
                    websocket.close()
                except Exception:
                    pass

            capture_thread = threading.Thread(target=capture_audio, daemon=True)
            keepalive_thread = threading.Thread(
                target=self._send_keepalives,
                args=(websocket, connection_stop_event, send_lock),
                daemon=True,
            )
            stop_thread = (
                threading.Thread(target=watch_for_stop_request, daemon=True)
                if stop_event is not None
                else None
            )
            capture_thread.start()
            keepalive_thread.start()
            if stop_thread is not None:
                stop_thread.start()

            try:
                while True:
                    message = websocket.recv()
                    response = json.loads(message)
                    if response.get("error_code") is not None:
                        raise RuntimeError(
                            f"Soniox error {response.get('error_code')}: {response.get('error_message')}"
                        )

                    tokens = (
                        response.get("tokens")
                        or (response.get("result") or {}).get("tokens")
                        or (response.get("data") or {}).get("tokens")
                        or []
                    )

                    maybe_final, interim_text = self._parse_tokens(tokens, final_tokens)
                    if on_interim is not None and interim_text != last_interim_text:
                        on_interim(interim_text)
                        last_interim_text = interim_text

                    if maybe_final is not None:
                        utterance_tokens = list(final_tokens)
                        captured_audio = utterance_audio.finalize()
                        final_tokens.clear()
                        last_interim_text = ""
                        if on_interim is not None:
                            on_interim("")
                        if maybe_final.strip():
                            detected_lang, confidence = self._detect_language_and_confidence(utterance_tokens)
                            result = SonioxLiveTranscriptionResult(
                                captured_audio=captured_audio,
                                transcript=maybe_final,
                                detected_language=detected_lang,
                                confidence=confidence,
                            )
                            yield result
                        utterance_pause_event.clear()
                        continue

                    legacy_text = self._extract_legacy_text(response)
                    if legacy_text is not None:
                        utterance_tokens = list(final_tokens)
                        captured_audio = utterance_audio.finalize()
                        final_tokens.clear()
                        last_interim_text = ""
                        if on_interim is not None:
                            on_interim("")
                        detected_lang, confidence = self._detect_language_and_confidence(utterance_tokens)
                        result = SonioxLiveTranscriptionResult(
                            captured_audio=captured_audio,
                            transcript=legacy_text,
                            detected_language=detected_lang,
                            confidence=confidence,
                        )
                        yield result
                        utterance_pause_event.clear()
                        continue

                    if response.get("finished"):
                        break
            except ConnectionClosedOK:
                pass
            except ConnectionClosed:
                if stop_event is None or not stop_event.is_set():
                    raise
            except GeneratorExit:
                raise
            except Exception as exc:
                receiver_error.append(exc)
            finally:
                connection_stop_event.set()
                capture_stop_event.set()
                utterance_pause_event.set()
                capture_thread.join()
                keepalive_thread.join()

        if stop_event is not None and stop_event.is_set():
            if on_interim is not None:
                on_interim("")
            return
        if capture_error:
            raise RuntimeError(f"Failed to capture streaming microphone audio: {capture_error[0]}")
        if receiver_error:
            raise RuntimeError(f"Streaming Soniox session failed: {receiver_error[0]}")

    def transcribe_pcm16(
        self,
        audio_bytes: bytes,
        channel: ChannelConfig | None = None,
        session_language_hint: str | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
    ) -> SonioxTranscriptionResult:
        attempt = 0
        while True:
            try:
                return self._transcribe_pcm16_once(
                    audio_bytes,
                    channel,
                    session_language_hint=session_language_hint,
                    sample_rate=sample_rate,
                    channels=channels,
                )
            except BaseException as exc:
                attempt += 1
                if not self._should_retry_session_error(exc, attempt):
                    raise
                time.sleep(self._retry_delay_for_attempt(attempt))

    def _transcribe_pcm16_once(
        self,
        audio_bytes: bytes,
        channel: ChannelConfig | None = None,
        session_language_hint: str | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
    ) -> SonioxTranscriptionResult:
        if not audio_bytes:
            return SonioxTranscriptionResult(transcript="", detected_language=None)

        active_sample_rate = sample_rate or self.sample_rate
        active_channels = channels or self.channels
        request_config = self._build_request_config(
            channel,
            session_language_hint,
            sample_rate=active_sample_rate,
            channels=active_channels,
        )

        final_tokens: list[dict] = []
        sender_error: list[BaseException] = []
        stop_event = threading.Event()
        send_lock = threading.Lock()

        with connect(self.websocket_url) as websocket:
            websocket.send(json.dumps(request_config))
            sender = threading.Thread(
                target=self._stream_audio,
                args=(
                    audio_bytes,
                    websocket,
                    sender_error,
                    send_lock,
                    active_sample_rate,
                    active_channels,
                ),
                kwargs={"realtime_pacing": False},
                daemon=True,
            )
            keepalive = threading.Thread(
                target=self._send_keepalives,
                args=(websocket, stop_event, send_lock),
                daemon=True,
            )
            sender.start()
            keepalive.start()

            try:
                while True:
                    message = websocket.recv()
                    response = json.loads(message)
                    if response.get("error_code") is not None:
                        raise RuntimeError(
                            f"Soniox error {response.get('error_code')}: {response.get('error_message')}"
                        )

                    tokens = (
                        response.get("tokens")
                        or (response.get("result") or {}).get("tokens")
                        or (response.get("data") or {}).get("tokens")
                        or []
                    )

                    maybe_transcript, _ = self._parse_tokens(tokens, final_tokens)
                    if maybe_transcript is not None:
                        detected_language, confidence = self._detect_language_and_confidence(final_tokens)
                        return SonioxTranscriptionResult(
                            transcript=maybe_transcript,
                            detected_language=detected_language,
                            confidence=confidence,
                        )

                    # Prompt return on stream completion tokens
                    if any(t.get("text") in {"<end>", "<fin>"} for t in tokens):
                        transcript = self._render_tokens(final_tokens)
                        detected_language, confidence = self._detect_language_and_confidence(final_tokens)
                        return SonioxTranscriptionResult(
                            transcript=transcript,
                            detected_language=detected_language,
                            confidence=confidence,
                        )

                    legacy_text = self._extract_legacy_text(response)
                    if legacy_text is not None:
                        detected_language, confidence = self._detect_language_and_confidence(final_tokens)
                        return SonioxTranscriptionResult(
                            transcript=legacy_text,
                            detected_language=detected_language,
                            confidence=confidence,
                        )

                    if response.get("finished"):
                        break
            except ConnectionClosedOK:
                pass
            finally:
                stop_event.set()

            sender.join()
            keepalive.join()

        if sender_error:
            raise RuntimeError(f"Failed to stream audio to Soniox: {sender_error[0]}")

        transcript = self._render_tokens(final_tokens)
        detected_language, confidence = self._detect_language_and_confidence(final_tokens)
        return SonioxTranscriptionResult(
            transcript=transcript,
            detected_language=detected_language,
            confidence=confidence,
        )

    def _build_request_config(
        self,
        channel: ChannelConfig | None = None,
        session_language_hint: str | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
    ) -> dict[str, object]:
        request_config: dict[str, object] = {
            "api_key": self.api_key,
            "model": self.model,
            "audio_format": "pcm_s16le",
            "sample_rate": sample_rate or self.sample_rate,
            "num_channels": channels or self.channels,
            "enable_endpoint_detection": True,
            "enable_language_identification": True,
            "max_endpoint_delay_ms": self.max_endpoint_delay_ms,
        }
        if session_language_hint:
            request_config["language_hints"] = [session_language_hint]
        elif channel and channel.soniox_language_hints:
            request_config["language_hints"] = channel.soniox_language_hints
        return request_config

    def _stream_audio(
        self,
        audio_bytes: bytes,
        websocket,
        sender_error: list[BaseException],
        send_lock: threading.Lock,
        sample_rate: int,
        channels: int,
        *,
        realtime_pacing: bool = False,
    ) -> None:
        chunk_size = max(1, int(sample_rate * channels * 2 * (self.chunk_ms / 1000.0)))
        delay_seconds = chunk_size / float(sample_rate * channels * 2) if realtime_pacing else 0.0
        try:
            for index in range(0, len(audio_bytes), chunk_size):
                with send_lock:
                    websocket.send(audio_bytes[index : index + chunk_size])
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
            with send_lock:
                websocket.send(json.dumps({"type": "finalize", "trailing_silence_ms": 300}))
        except BaseException as exc:  # pragma: no cover - defensive thread capture
            sender_error.append(exc)

    def _send_keepalives(
        self,
        websocket,
        stop_event: threading.Event,
        send_lock: threading.Lock,
    ) -> None:
        while not stop_event.wait(self.keepalive_seconds):
            try:
                with send_lock:
                    websocket.send(json.dumps({"type": "keepalive"}))
            except Exception:
                return

    def _should_retry_session_error(
        self,
        error: BaseException,
        attempt: int,
    ) -> bool:
        if attempt > self.session_retry_attempts:
            return False
        if isinstance(error, KeyboardInterrupt):
            return False

        message = str(error).lower()
        non_retryable_markers = (
            "unauthorized",
            "forbidden",
            "invalid api key",
            "unknown model",
            "unsupported",
            "bad request",
        )
        if any(marker in message for marker in non_retryable_markers):
            return False
        return True

    def _retry_delay_for_attempt(self, attempt: int) -> float:
        delay = self.session_retry_delay_seconds * (2 ** max(0, attempt - 1))
        return min(delay, self.session_max_retry_delay_seconds)

    @staticmethod
    def _render_tokens(tokens: list[dict]) -> str:
        parts: list[str] = []
        for token in tokens:
            text = str(token.get("text") or "")
            if text and text not in {"<end>", "<fin>"}:
                parts.append(text)
        return "".join(parts).strip()

    def _parse_tokens(
        self,
        tokens: list[dict],
        final_tokens: list[dict],
    ) -> tuple[str | None, str]:
        non_final_tokens: list[dict] = []
        for token in tokens:
            token_text = str(token.get("text") or "")
            if not token.get("is_final"):
                if token_text:
                    non_final_tokens.append(token)
                continue
            if token_text in {"<end>", "<fin>"}:
                transcript = self._render_tokens(final_tokens)
                if transcript:
                    return transcript, transcript
                final_tokens.clear()
                continue
            if token_text:
                final_tokens.append(token)
        interim_text = self._render_tokens(final_tokens + non_final_tokens)
        return None, interim_text

    def _extract_legacy_text(self, response: dict) -> str | None:
        text = str(
            response.get("text")
            or (response.get("result") or {}).get("text")
            or ""
        ).strip()
        if text and bool(response.get("is_final")):
            return text
        return None

    @staticmethod
    def _detect_language_and_confidence(tokens: list[dict]) -> tuple[str | None, float]:
        """Detect dominant language and calculate STT confidence from token weights and scores.

        Returns:
            Tuple of (detected_language_code, confidence_score) where confidence is between
            0.0 and 1.0.
        """
        if not tokens:
            return None, 1.0

        weighted_languages: Counter[str] = Counter()
        explicit_confidences: list[float] = []

        for token in tokens:
            language = token.get("language")
            text = (token.get("text") or "").strip()
            score = token.get("confidence")
            if score is not None:
                try:
                    explicit_confidences.append(float(score))
                except (ValueError, TypeError):
                    pass

            if not language or not text:
                continue
            weight = sum(1 for char in text if char.isalnum())
            if weight <= 0:
                weight = len(text)
            weighted_languages[language] += max(weight, 1)

        if not weighted_languages:
            avg_conf = sum(explicit_confidences) / len(explicit_confidences) if explicit_confidences else 1.0
            return None, round(avg_conf, 3)

        most_common = weighted_languages.most_common(1)[0]
        detected_lang = most_common[0]
        winning_weight = most_common[1]
        total_weight = sum(weighted_languages.values())

        # Language consistency confidence: proportion of token weights matching dominant language
        lang_consistency = winning_weight / total_weight if total_weight > 0 else 1.0

        if explicit_confidences:
            avg_token_conf = sum(explicit_confidences) / len(explicit_confidences)
            confidence = (avg_token_conf * 0.7) + (lang_consistency * 0.3)
        else:
            confidence = lang_consistency

        return detected_lang, round(min(max(confidence, 0.0), 1.0), 3)

    @classmethod
    def _detect_language(cls, tokens: list[dict]) -> str | None:
        return cls._detect_language_and_confidence(tokens)[0]
