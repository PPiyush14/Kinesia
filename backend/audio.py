from __future__ import annotations

import io
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
try:
    import sounddevice as sd
except ImportError:
    sd = None


@dataclass(frozen=True)
class CapturedAudio:
    raw_pcm_s16le: bytes
    sample_rate: int
    channels: int
    duration_seconds: float


class MicrophoneRecorder:
    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        block_ms: int,
        voice_threshold: float,
        silence_seconds: float,
        max_record_seconds: float,
        wait_for_speech_seconds: float,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_ms = block_ms
        self.voice_threshold = voice_threshold
        self.silence_seconds = silence_seconds
        self.max_record_seconds = max_record_seconds
        self.wait_for_speech_seconds = wait_for_speech_seconds
        self.block_size = max(1, int((self.sample_rate * self.block_ms) / 1000))

    def record_phrase(self, *, input_device: int | str | None = None) -> CapturedAudio:
        return self.stream_phrase(input_device=input_device)

    def stream_phrase(
        self,
        *,
        input_device: int | str | None = None,
        sample_rate_override: int | None = None,
        on_chunk: Callable[[bytes], None] | None = None,
        on_speech_start: Callable[[], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> CapturedAudio:
        pre_roll = deque(maxlen=max(1, int(0.4 / (self.block_ms / 1000.0))))
        recorded_chunks: list[bytes] = []
        status_messages: list[str] = []
        resolved_input_device = self.resolve_device(input_device, kind="input")
        input_sample_rate = self.resolve_input_sample_rate(
            resolved_input_device,
            channels=self.channels,
            preferred_sample_rate=sample_rate_override or self.sample_rate,
        )
        block_size = max(1, int((input_sample_rate * self.block_ms) / 1000))
        state = {
            "speech_started": False,
            "speech_started_at": None,
            "last_voice_at": None,
        }

        def callback(indata: bytes, frames: int, time_info, status) -> None:
            if status:
                status_messages.append(str(status))
            if stop_event is not None and stop_event.is_set():
                return

            chunk = bytes(indata)
            pre_roll.append(chunk)
            energy = self._chunk_energy(chunk)
            now = time.monotonic()

            if not state["speech_started"]:
                if energy >= self.voice_threshold:
                    state["speech_started"] = True
                    state["speech_started_at"] = now
                    state["last_voice_at"] = now
                    if on_speech_start is not None:
                        on_speech_start()
                    recorded_chunks.extend(pre_roll)
                    if on_chunk is not None:
                        for buffered_chunk in pre_roll:
                            on_chunk(buffered_chunk)
                    pre_roll.clear()
                return

            recorded_chunks.append(chunk)
            if on_chunk is not None:
                on_chunk(chunk)
            if energy >= self.voice_threshold:
                state["last_voice_at"] = now

        started_waiting_at = time.monotonic()
        with sd.RawInputStream(
            samplerate=input_sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=block_size,
            callback=callback,
            device=resolved_input_device,
        ):
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                now = time.monotonic()
                if state["speech_started"]:
                    speech_started_at = state["speech_started_at"] or now
                    last_voice_at = state["last_voice_at"] or now
                    if now - speech_started_at >= self.max_record_seconds:
                        break
                    if now - last_voice_at >= self.silence_seconds:
                        break
                elif (
                    self.wait_for_speech_seconds > 0
                    and now - started_waiting_at >= self.wait_for_speech_seconds
                ):
                    break
                time.sleep(0.05)

        if status_messages:
            raise RuntimeError(f"Microphone stream reported an error: {'; '.join(status_messages)}")

        audio_bytes = b"".join(recorded_chunks)
        duration = len(audio_bytes) / float(input_sample_rate * self.channels * 2)
        return CapturedAudio(
            raw_pcm_s16le=audio_bytes,
            sample_rate=input_sample_rate,
            channels=self.channels,
            duration_seconds=duration,
        )

    def stream_continuous(
        self,
        *,
        input_device: int | str | None = None,
        sample_rate_override: int | None = None,
        on_chunk: Callable[[bytes], None] | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_speech_end: Callable[[], None] | None = None,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
    ) -> None:
        pre_roll = deque(maxlen=max(1, int(0.4 / (self.block_ms / 1000.0))))
        status_messages: list[str] = []
        resolved_input_device = self.resolve_device(input_device, kind="input")
        input_sample_rate = self.resolve_input_sample_rate(
            resolved_input_device,
            channels=self.channels,
            preferred_sample_rate=sample_rate_override or self.sample_rate,
        )
        block_size = max(1, int((input_sample_rate * self.block_ms) / 1000))
        state = {
            "speech_started": False,
            "speech_started_at": None,
            "last_voice_at": None,
        }

        def finish_speech() -> None:
            if not state["speech_started"]:
                pre_roll.clear()
                return
            state["speech_started"] = False
            state["speech_started_at"] = None
            state["last_voice_at"] = None
            pre_roll.clear()
            if on_speech_end is not None:
                on_speech_end()

        def callback(indata: bytes, frames: int, time_info, status) -> None:
            if status:
                status_messages.append(str(status))
            if stop_event is not None and stop_event.is_set():
                return
            if pause_event is not None and pause_event.is_set():
                finish_speech()
                return

            chunk = bytes(indata)
            energy = self._chunk_energy(chunk)
            now = time.monotonic()

            if not state["speech_started"]:
                pre_roll.append(chunk)
                if energy < self.voice_threshold:
                    return
                state["speech_started"] = True
                state["speech_started_at"] = now
                state["last_voice_at"] = now
                if on_speech_start is not None:
                    on_speech_start()
                if on_chunk is not None:
                    for buffered_chunk in pre_roll:
                        on_chunk(buffered_chunk)
                pre_roll.clear()
                return

            if on_chunk is not None:
                on_chunk(chunk)
            if energy >= self.voice_threshold:
                state["last_voice_at"] = now

            speech_started_at = state["speech_started_at"] or now
            last_voice_at = state["last_voice_at"] or now
            if now - speech_started_at >= self.max_record_seconds:
                finish_speech()
            elif now - last_voice_at >= self.silence_seconds:
                finish_speech()

        with sd.RawInputStream(
            samplerate=input_sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=block_size,
            callback=callback,
            device=resolved_input_device,
        ):
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                time.sleep(0.05)

        if state["speech_started"]:
            finish_speech()

        if status_messages:
            raise RuntimeError(f"Microphone stream reported an error: {'; '.join(status_messages)}")

    @staticmethod
    def play_wav_bytes(
        wav_bytes: bytes,
        *,
        output_device: int | str | None = None,
    ) -> None:
        resolved_output_device = MicrophoneRecorder.resolve_device(
            output_device,
            kind="output",
        )
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())

        dtype = {
            1: np.int8,
            2: np.int16,
            4: np.int32,
        }.get(sample_width)
        if dtype is None:
            raise RuntimeError(f"Unsupported WAV sample width: {sample_width}")

        audio = np.frombuffer(frames, dtype=dtype)
        if channels > 1:
            audio = audio.reshape(-1, channels)

        try:
            sd.play(
                audio,
                samplerate=sample_rate,
                device=resolved_output_device,
                blocking=True,
            )
            return
        except sd.PortAudioError as exc:
            if "Invalid sample rate" not in str(exc):
                raise RuntimeError(
                    f"Failed to play audio on output device {output_device!r}: {exc}"
                ) from exc

        fallback_sample_rate = MicrophoneRecorder._find_fallback_output_sample_rate(
            resolved_output_device,
            channels=channels,
            dtype_name=np.dtype(dtype).name,
            original_sample_rate=sample_rate,
        )
        if fallback_sample_rate is None:
            raise RuntimeError(
                "The output device rejected the synthesized audio sample rate "
                f"({sample_rate} Hz), and no supported fallback sample rate was found."
            )

        resampled_audio = MicrophoneRecorder._resample_audio(
            audio,
            from_sample_rate=sample_rate,
            to_sample_rate=fallback_sample_rate,
        )
        try:
            sd.play(
                resampled_audio,
                samplerate=fallback_sample_rate,
                device=resolved_output_device,
                blocking=True,
            )
        except sd.PortAudioError as exc:
            raise RuntimeError(
                "Failed to play audio after retrying with a fallback sample rate "
                f"({fallback_sample_rate} Hz) on output device {output_device!r}: {exc}"
            ) from exc

    @staticmethod
    def wav_bytes_from_pcm_s16le(
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
        return buffer.getvalue()

    @staticmethod
    def list_audio_devices() -> list[dict]:
        devices = sd.query_devices()
        return [dict(device) for device in devices]

    @staticmethod
    def get_device_info(
        device: int | str | None,
        *,
        kind: str | None = None,
    ) -> dict:
        try:
            resolved_device = MicrophoneRecorder.resolve_device(device, kind=kind)
            if kind is None:
                info = sd.query_devices(resolved_device)
            else:
                info = sd.query_devices(resolved_device, kind=kind)
        except Exception as exc:
            label = "default device" if device is None else repr(device)
            kind_label = kind or "audio"
            raise RuntimeError(
                f"Unable to query the {kind_label} device {label}: {exc}"
            ) from exc
        return dict(info)

    @staticmethod
    def resolve_input_sample_rate(
        input_device: int | str | None,
        *,
        channels: int,
        preferred_sample_rate: int,
    ) -> int:
        candidate_rates: list[int] = [int(preferred_sample_rate)]
        try:
            info = MicrophoneRecorder.get_device_info(input_device, kind="input")
        except RuntimeError:
            info = {}

        default_rate = info.get("default_samplerate")
        if default_rate:
            rounded_default = int(round(float(default_rate)))
            if rounded_default not in candidate_rates:
                candidate_rates.append(rounded_default)

        for rate in (48000, 44100, 32000, 24000, 22050, 16000, 8000):
            if rate not in candidate_rates:
                candidate_rates.append(rate)

        last_error: Exception | None = None
        for candidate_rate in candidate_rates:
            try:
                sd.check_input_settings(
                    device=input_device,
                    channels=channels,
                    samplerate=candidate_rate,
                    dtype="int16",
                )
            except Exception as exc:
                last_error = exc
                continue
            return candidate_rate

        details = f": {last_error}" if last_error is not None else "."
        raise RuntimeError(
            "No supported microphone sample rate was found for the selected input device"
            f"{details}"
        )

    @staticmethod
    def resolve_device(
        device: int | str | None,
        *,
        kind: str | None = None,
    ) -> int | str | None:
        if device is None or isinstance(device, int):
            return device

        selector = str(device).strip()
        if not selector:
            return None
        if selector.isdigit():
            return int(selector)

        try:
            if kind is None:
                sd.query_devices(selector)
            else:
                sd.query_devices(selector, kind=kind)
            return selector
        except Exception:
            pass

        devices = MicrophoneRecorder.list_audio_devices()
        normalized_selector = MicrophoneRecorder._normalize_device_selector(selector)
        exact_matches: list[tuple[int, str]] = []
        partial_matches: list[tuple[int, str]] = []

        for index, info in enumerate(devices):
            if kind == "input" and int(info.get("max_input_channels") or 0) < 1:
                continue
            if kind == "output" and int(info.get("max_output_channels") or 0) < 1:
                continue

            name = str(info.get("name") or "").strip()
            if not name:
                continue

            normalized_name = MicrophoneRecorder._normalize_device_selector(name)
            if normalized_name == normalized_selector:
                exact_matches.append((index, name))
                continue
            if normalized_selector in normalized_name:
                partial_matches.append((index, name))

        matches = exact_matches or partial_matches
        if len(matches) == 1:
            return matches[0][0]
        if len(matches) > 1:
            candidates = ", ".join(
                f"[{index}] {name}"
                for index, name in matches[:8]
            )
            raise RuntimeError(
                f"Device selector {selector!r} matched multiple {kind or 'audio'} devices: "
                f"{candidates}. Use a more specific name or the exact index from --list-devices."
            )

        raise RuntimeError(
            f"Device selector {selector!r} did not match any {kind or 'audio'} device. "
            "Use --list-devices and set the exact index or a unique name fragment."
        )

    @staticmethod
    def _find_fallback_output_sample_rate(
        output_device: int | str | None,
        *,
        channels: int,
        dtype_name: str,
        original_sample_rate: int,
    ) -> int | None:
        candidate_rates: list[int] = []
        try:
            info = MicrophoneRecorder.get_device_info(output_device, kind="output")
        except RuntimeError:
            info = {}

        default_rate = info.get("default_samplerate")
        if default_rate:
            candidate_rates.append(int(round(float(default_rate))))

        for rate in (48000, 44100, 32000, 24000, 22050, 16000):
            if rate not in candidate_rates:
                candidate_rates.append(rate)

        for candidate_rate in candidate_rates:
            if candidate_rate == original_sample_rate:
                continue
            try:
                sd.check_output_settings(
                    device=output_device,
                    channels=channels,
                    samplerate=candidate_rate,
                    dtype=dtype_name,
                )
            except Exception:
                continue
            return candidate_rate
        return None

    @staticmethod
    def _resample_audio(
        audio: np.ndarray,
        *,
        from_sample_rate: int,
        to_sample_rate: int,
    ) -> np.ndarray:
        if from_sample_rate == to_sample_rate or audio.size == 0:
            return audio

        original_was_1d = audio.ndim == 1
        audio_2d = audio[:, np.newaxis] if original_was_1d else audio

        source_length = audio_2d.shape[0]
        target_length = max(
            1,
            int(round(source_length * (to_sample_rate / float(from_sample_rate)))),
        )
        source_positions = np.arange(source_length, dtype=np.float32)
        target_positions = np.linspace(
            0,
            max(source_length - 1, 0),
            num=target_length,
            dtype=np.float32,
        )

        resampled = np.empty((target_length, audio_2d.shape[1]), dtype=np.float32)
        for channel_index in range(audio_2d.shape[1]):
            resampled[:, channel_index] = np.interp(
                target_positions,
                source_positions,
                audio_2d[:, channel_index].astype(np.float32),
            )

        if np.issubdtype(audio.dtype, np.integer):
            dtype_info = np.iinfo(audio.dtype)
            resampled = np.clip(
                np.rint(resampled),
                dtype_info.min,
                dtype_info.max,
            ).astype(audio.dtype)
        else:
            resampled = resampled.astype(audio.dtype)

        if original_was_1d:
            return resampled[:, 0]
        return resampled

    @staticmethod
    def _chunk_energy(chunk: bytes) -> float:
        if not chunk:
            return 0.0
        samples = np.frombuffer(chunk, dtype=np.int16)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

    @staticmethod
    def _normalize_device_selector(value: str) -> str:
        return " ".join(value.strip().lower().split())
