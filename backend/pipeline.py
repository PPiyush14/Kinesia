from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.audio import CapturedAudio, MicrophoneRecorder
from backend.config import AppConfig
from backend.gemma_client import GemmaClient
from backend.nlp.entity_extractor import EntityExtractor
from backend.session_state import SessionInfo, SessionLanguageStore
from backend.soniox_client import SonioxClient, SonioxLiveTranscriptionResult
from backend.storage.glossary import GlossaryStore
from backend.tts_client import TtsClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranslationRunResult:
    speaker_channel_key: str
    speaker_channel_name: str
    listener_channel_key: str
    listener_channel_name: str
    captured_audio: CapturedAudio
    detected_source_language: str | None
    speaker_session_language: str
    listener_language: str
    tts_provider: str
    transcript: str
    translation: str
    synthesized_audio: bytes
    gemma_latency_ms: float
    tts_latency_ms: float
    playback_latency_ms: float
    turn_latency_ms: float
    speaker_language_updated_this_turn: bool
    stt_confidence: float = 1.0
    entities: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectedTurn:
    speaker_channel_key: str
    captured_audio: CapturedAudio
    spoken_at: float


@dataclass(frozen=True)
class ListenerWarning:
    channel_key: str
    error: BaseException
    retry_delay_seconds: float
    consecutive_failures: int


@dataclass(frozen=True)
class TurnProcessingFailure:
    detected_turn: DetectedTurn
    error: BaseException


class TranslatorPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.session_state = SessionLanguageStore(config)
        self.session_info: SessionInfo = self.session_state.start_or_join_session()
        self.recorder = MicrophoneRecorder(
            sample_rate=config.mic_sample_rate,
            channels=config.mic_channels,
            block_ms=config.mic_block_ms,
            voice_threshold=config.voice_activity_threshold,
            silence_seconds=config.voice_silence_seconds,
            max_record_seconds=config.voice_max_record_seconds,
            wait_for_speech_seconds=config.wait_for_speech_seconds,
        )
        self.soniox = SonioxClient(config)
        self.gemma = GemmaClient(config)
        self.tts = TtsClient(config)
        self.glossary_store = GlossaryStore(config.project_root)
        try:
            self.entity_extractor: EntityExtractor | None = EntityExtractor()
        except Exception as exc:
            logger.warning("Could not initialize EntityExtractor: %s", exc)
            self.entity_extractor = None

    def run_once(self, channel_key: str) -> TranslationRunResult:
        return self.run_once_live(channel_key)

    def run_once_live(
        self,
        channel_key: str,
        *,
        on_interim: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_speech_end: Callable[[], None] | None = None,
    ) -> TranslationRunResult | None:
        speaker_channel = self.config.get_channel(channel_key)
        live_transcription = self.soniox.transcribe_live_microphone(
            self.recorder,
            speaker_channel,
            input_device=speaker_channel.input_device,
            input_sample_rate_override=speaker_channel.input_sample_rate,
            session_language_hint=None,
            on_interim=on_interim,
            stop_event=stop_event,
            on_speech_start=on_speech_start,
            on_speech_end=on_speech_end,
        )
        if live_transcription is None:
            return None
        if stop_event is not None and stop_event.is_set():
            return None
        return self._translate_live_transcription(channel_key, live_transcription)

    def run_once_phrase(
        self,
        channel_key: str,
        *,
        stop_event: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
    ) -> TranslationRunResult | None:
        speaker_channel = self.config.get_channel(channel_key)
        captured_audio = self.recorder.stream_phrase(
            input_device=speaker_channel.input_device,
            sample_rate_override=speaker_channel.input_sample_rate,
            on_speech_start=on_speech_start,
            stop_event=stop_event,
        )
        if stop_event is not None and stop_event.is_set():
            return None
        if not captured_audio.raw_pcm_s16le:
            return None

        transcription = self.soniox.transcribe_pcm16(
            captured_audio.raw_pcm_s16le,
            speaker_channel,
            session_language_hint=None,
            sample_rate=captured_audio.sample_rate,
            channels=captured_audio.channels,
        )
        if stop_event is not None and stop_event.is_set():
            return None

        live_transcription = SonioxLiveTranscriptionResult(
            captured_audio=captured_audio,
            transcript=transcription.transcript,
            detected_language=transcription.detected_language,
            confidence=transcription.confidence,
        )
        return self._translate_live_transcription(channel_key, live_transcription)

    def stream_channel(
        self,
        channel_key: str,
        *,
        on_interim: Callable[[str], None] | None = None,
        on_result: Callable[[TranslationRunResult], None] | None = None,
        stop_event: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_speech_end: Callable[[], None] | None = None,
    ) -> None:
        speaker_channel = self.config.get_channel(channel_key)
        live_stream = self.soniox.stream_microphone_session(
            self.recorder,
            speaker_channel,
            input_device=speaker_channel.input_device,
            input_sample_rate_override=speaker_channel.input_sample_rate,
            session_language_hint=None,
            on_interim=on_interim,
            stop_event=stop_event,
            on_speech_start=on_speech_start,
            on_speech_end=on_speech_end,
        )

        try:
            for live_transcription in live_stream:
                if stop_event is not None and stop_event.is_set():
                    break
                if not live_transcription.transcript.strip():
                    continue
                result = self._translate_live_transcription(channel_key, live_transcription)
                if on_result is not None:
                    on_result(result)
        finally:
            live_stream.close()

    def run_detected_turn(
        self,
        *,
        on_speaker_detected: Callable[[str], None] | None = None,
    ) -> TranslationRunResult:
        detected_turn = self.detect_next_speaker_turn(
            on_speaker_detected=on_speaker_detected,
        )
        speaker_channel = self.config.get_channel(detected_turn.speaker_channel_key)
        transcription = self.soniox.transcribe_pcm16(
            detected_turn.captured_audio.raw_pcm_s16le,
            speaker_channel,
            session_language_hint=None,
            sample_rate=detected_turn.captured_audio.sample_rate,
            channels=detected_turn.captured_audio.channels,
        )
        live_transcription = SonioxLiveTranscriptionResult(
            captured_audio=detected_turn.captured_audio,
            transcript=transcription.transcript,
            detected_language=transcription.detected_language,
            confidence=transcription.confidence,
        )
        return self._translate_live_transcription(
            detected_turn.speaker_channel_key,
            live_transcription,
        )

    def run_simultaneous_conversation(
        self,
        *,
        on_turn_captured: Callable[[DetectedTurn], None] | None = None,
        on_result: Callable[[TranslationRunResult], None] | None = None,
        on_listener_warning: Callable[[ListenerWarning], None] | None = None,
        on_turn_failure: Callable[[TurnProcessingFailure], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        active_stop_event = stop_event or threading.Event()
        turn_queue: queue.PriorityQueue[tuple[float, int, DetectedTurn]] = queue.PriorityQueue()
        warning_queue: queue.Queue[ListenerWarning] = queue.Queue()
        turn_counter = itertools.count()

        listener_threads = [
            threading.Thread(
                target=self._listen_for_channel_turns,
                args=(
                    channel_key,
                    turn_queue,
                    warning_queue,
                    active_stop_event,
                    turn_counter,
                    on_turn_captured,
                ),
                daemon=True,
            )
            for channel_key in self.config.channels
        ]
        for thread in listener_threads:
            thread.start()

        try:
            while not active_stop_event.is_set():
                try:
                    warning = warning_queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    if on_listener_warning is not None:
                        on_listener_warning(warning)

                try:
                    _, _, detected_turn = turn_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                try:
                    result = self._transcribe_and_translate_detected_turn(detected_turn)
                except BaseException as exc:
                    if on_turn_failure is not None:
                        on_turn_failure(
                            TurnProcessingFailure(
                                detected_turn=detected_turn,
                                error=exc,
                            )
                        )
                    continue
                if on_result is not None:
                    on_result(result)
        finally:
            active_stop_event.set()
            for thread in listener_threads:
                thread.join()

    def detect_next_speaker_turn(
        self,
        *,
        on_speaker_detected: Callable[[str], None] | None = None,
    ) -> DetectedTurn:
        stop_events = {
            key: threading.Event()
            for key in self.config.channels
        }
        winner_event = threading.Event()
        winner_lock = threading.Lock()
        winner_key: dict[str, str | None] = {"value": None}
        captured_results: dict[str, CapturedAudio] = {}
        errors: list[tuple[str, BaseException]] = []

        def mark_winner(channel_key: str) -> None:
            notify = False
            with winner_lock:
                if winner_key["value"] is not None:
                    return
                winner_key["value"] = channel_key
                notify = True
                for other_key, stop_event in stop_events.items():
                    if other_key != channel_key:
                        stop_event.set()
                winner_event.set()
            if notify and on_speaker_detected is not None:
                on_speaker_detected(channel_key)

        def capture_channel(channel_key: str) -> None:
            channel = self.config.get_channel(channel_key)
            try:
                captured_audio = self.recorder.stream_phrase(
                    input_device=channel.input_device,
                    sample_rate_override=channel.input_sample_rate,
                    on_speech_start=lambda: mark_winner(channel_key),
                    stop_event=stop_events[channel_key],
                )
                if captured_audio.raw_pcm_s16le and winner_key["value"] == channel_key:
                    captured_results[channel_key] = captured_audio
            except BaseException as exc:  # pragma: no cover - thread handoff
                errors.append((channel_key, exc))
                winner_event.set()
                for stop_event in stop_events.values():
                    stop_event.set()

        threads = [
            threading.Thread(
                target=capture_channel,
                args=(channel_key,),
                daemon=True,
            )
            for channel_key in self.config.channels
        ]
        for thread in threads:
            thread.start()

        winner_event.wait()
        for thread in threads:
            thread.join()

        if errors:
            channel_key, error = errors[0]
            raise RuntimeError(
                f"Failed while listening for speech on channel {channel_key}: {error}"
            )

        channel_key = winner_key["value"]
        if channel_key is None:
            raise RuntimeError("No active speaker channel was detected.")

        captured_audio = captured_results.get(channel_key)
        if captured_audio is None or not captured_audio.raw_pcm_s16le:
            raise RuntimeError(
                f"Detected speech on channel {channel_key}, but no audio was captured for that turn."
            )

        return DetectedTurn(
            speaker_channel_key=channel_key,
            captured_audio=captured_audio,
            spoken_at=time.monotonic(),
        )

    def _listen_for_channel_turns(
        self,
        channel_key: str,
        turn_queue: queue.PriorityQueue[tuple[float, int, DetectedTurn]],
        warning_queue: queue.Queue[ListenerWarning],
        stop_event: threading.Event,
        turn_counter,
        on_turn_captured: Callable[[DetectedTurn], None] | None,
    ) -> None:
        channel = self.config.get_channel(channel_key)
        consecutive_failures = 0
        while not stop_event.is_set():
            speech_started_at = {"value": None}

            def mark_speech_started() -> None:
                if speech_started_at["value"] is None:
                    speech_started_at["value"] = time.monotonic()

            try:
                captured_audio = self.recorder.stream_phrase(
                    input_device=channel.input_device,
                    sample_rate_override=channel.input_sample_rate,
                    on_speech_start=mark_speech_started,
                    stop_event=stop_event,
                )
            except BaseException as exc:  # pragma: no cover - thread handoff
                if not stop_event.is_set():
                    consecutive_failures += 1
                    retry_delay_seconds = min(5.0, float(consecutive_failures))
                    warning_queue.put(
                        ListenerWarning(
                            channel_key=channel_key,
                            error=exc,
                            retry_delay_seconds=retry_delay_seconds,
                            consecutive_failures=consecutive_failures,
                        )
                    )
                    stop_event.wait(retry_delay_seconds)
                continue

            if stop_event.is_set():
                return
            consecutive_failures = 0
            if not captured_audio.raw_pcm_s16le:
                continue

            detected_turn = DetectedTurn(
                speaker_channel_key=channel_key,
                captured_audio=captured_audio,
                spoken_at=speech_started_at["value"] or time.monotonic(),
            )
            turn_queue.put((detected_turn.spoken_at, next(turn_counter), detected_turn))
            if on_turn_captured is not None:
                on_turn_captured(detected_turn)

    def _transcribe_and_translate_detected_turn(
        self,
        detected_turn: DetectedTurn,
    ) -> TranslationRunResult:
        speaker_channel = self.config.get_channel(detected_turn.speaker_channel_key)
        transcription = self.soniox.transcribe_pcm16(
            detected_turn.captured_audio.raw_pcm_s16le,
            speaker_channel,
            session_language_hint=None,
            sample_rate=detected_turn.captured_audio.sample_rate,
            channels=detected_turn.captured_audio.channels,
        )
        live_transcription = SonioxLiveTranscriptionResult(
            captured_audio=detected_turn.captured_audio,
            transcript=transcription.transcript,
            detected_language=transcription.detected_language,
            confidence=transcription.confidence,
        )
        return self._translate_live_transcription(
            detected_turn.speaker_channel_key,
            live_transcription,
        )

    def get_session_language(self, channel_key: str) -> str:
        return self.session_state.get_language(channel_key)

    def has_learned_session_language(self, channel_key: str) -> bool:
        return self.session_state.has_learned_language(channel_key)

    def update_session_language(
        self,
        channel_key: str,
        detected_language: str | None,
    ) -> bool:
        speaker_channel = self.config.get_channel(channel_key)
        if speaker_channel.language_mode != "auto":
            return False
        return self.session_state.update_language(channel_key, detected_language)

    def _translate_live_transcription(
        self,
        channel_key: str,
        live_transcription: SonioxLiveTranscriptionResult,
    ) -> TranslationRunResult:
        turn_started_at = time.perf_counter()
        speaker_channel = self.config.get_channel(channel_key)
        listener_channel = self.config.get_peer_channel(channel_key)
        captured_audio = live_transcription.captured_audio
        if not live_transcription.transcript:
            raise RuntimeError("Soniox returned an empty transcript.")

        speaker_language_updated_this_turn = self.update_session_language(
            channel_key,
            live_transcription.detected_language,
        )
        speaker_session_language = self.get_session_language(channel_key)
        listener_session_language = self.get_session_language(listener_channel.key)

        gemma_started_at = time.perf_counter()
        glossary_hint = ""
        if self.glossary_store is not None:
            matched_terms = self.glossary_store.find_matched_entries(live_transcription.transcript)
            glossary_hint = self.glossary_store.build_prompt_hint(matched_terms)

        translation = self.gemma.translate(
            live_transcription.transcript,
            speaker_channel,
            listener_channel,
            speaker_session_language,
            listener_session_language,
            live_transcription.detected_language,
            glossary_hint=glossary_hint,
        )
        gemma_latency_ms = (time.perf_counter() - gemma_started_at) * 1000.0

        entities: dict[str, list[str]] = {}
        if self.entity_extractor is not None:
            try:
                entities = self.entity_extractor.extract_combined(
                    live_transcription.transcript,
                    translation,
                )
            except Exception as exc:
                logger.warning("Entity extraction failed: %s", exc)

        tts_started_at = time.perf_counter()
        spoken_audio = self.tts.synthesize_for_language(
            translation,
            listener_channel,
            listener_session_language,
        )
        tts_latency_ms = (time.perf_counter() - tts_started_at) * 1000.0

        playback_started_at = time.perf_counter()
        self.recorder.play_wav_bytes(
            spoken_audio,
            output_device=listener_channel.output_device,
        )
        playback_latency_ms = (time.perf_counter() - playback_started_at) * 1000.0
        turn_latency_ms = (time.perf_counter() - turn_started_at) * 1000.0

        logger.info(
            "turn_latency speaker=%s listener=%s tts_provider=%s gemma_ms=%.2f tts_ms=%.2f playback_ms=%.2f total_ms=%.2f transcript_chars=%d translation_chars=%d detected_source_language=%s target_language=%s entities=%d",
            speaker_channel.key,
            listener_channel.key,
            self.tts.provider_name,
            gemma_latency_ms,
            tts_latency_ms,
            playback_latency_ms,
            turn_latency_ms,
            len(live_transcription.transcript.strip()),
            len(translation.strip()),
            live_transcription.detected_language or "",
            listener_session_language,
            sum(len(v) for v in entities.values()),
        )

        return TranslationRunResult(
            speaker_channel_key=speaker_channel.key,
            speaker_channel_name=speaker_channel.name,
            listener_channel_key=listener_channel.key,
            listener_channel_name=listener_channel.name,
            captured_audio=captured_audio,
            detected_source_language=live_transcription.detected_language,
            speaker_session_language=speaker_session_language,
            listener_language=listener_session_language,
            tts_provider=self.tts.provider_name,
            transcript=live_transcription.transcript,
            translation=translation,
            synthesized_audio=spoken_audio,
            gemma_latency_ms=gemma_latency_ms,
            tts_latency_ms=tts_latency_ms,
            playback_latency_ms=playback_latency_ms,
            turn_latency_ms=turn_latency_ms,
            speaker_language_updated_this_turn=speaker_language_updated_this_turn,
            stt_confidence=live_transcription.confidence,
            entities=entities,
        )
