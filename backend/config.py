from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from backend.languages import default_indian_language_hints

PLACEHOLDER_PREFIXES = (
    "api-key",
    "api_key",
    "replace-with-",
    "replace_with_",
    "your-",
    "your_",
    "<",
)

DEFAULT_GEMMA_RESPONSE_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_CARTESIA_VOICE_ID = "95d51f79-c397-46f9-b49a-23763d3eaa2d"


@dataclass(frozen=True)
class ChannelConfig:
    key: str
    name: str
    source_language: str
    language_mode: str
    input_sample_rate: int | None = None
    soniox_language_hints: list[str] = field(default_factory=list)
    cartesia_voice_id: str = ""
    cartesia_model_id: str = "sonic-3.5"
    cartesia_sample_rate: int = 24000
    google_tts_language_code: str = ""
    google_tts_voice_name: str = ""
    google_tts_voice_gender: str = "NEUTRAL"
    google_tts_sample_rate: int = 24000
    translation_hint: str = ""
    input_device: int | str | None = None
    output_device: int | str | None = None


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    session_state_path: Path
    session_join_window_seconds: float
    log_level: str
    mic_sample_rate: int
    mic_channels: int
    mic_block_ms: int
    voice_activity_threshold: float
    voice_silence_seconds: float
    voice_max_record_seconds: float
    wait_for_speech_seconds: float
    soniox_api_key: str
    soniox_model: str
    soniox_websocket_url: str
    soniox_max_endpoint_delay_ms: int
    soniox_keepalive_seconds: float
    soniox_session_retry_attempts: int
    soniox_session_retry_delay_seconds: float
    soniox_session_max_retry_delay_seconds: float
    stt_stream_chunk_ms: int
    gemma_response_base_url: str
    gemma_response_api_key: str
    gemma_model: str
    gemma_temperature: float
    gemma_max_tokens: int
    gemma_request_timeout_seconds: float
    tts_provider: str
    google_tts_credentials_path: Path | None
    google_tts_speaking_rate: float
    google_tts_pitch: float
    google_tts_request_timeout_seconds: float
    cartesia_api_key: str
    cartesia_api_url: str
    cartesia_websocket_url: str
    cartesia_version: str
    cartesia_request_timeout_seconds: float
    cartesia_speed: float
    cartesia_context_prefix: str
    cartesia_emotions: list[str]
    channels: dict[str, ChannelConfig]
    groq_whisper_model: str = "whisper-large-v3-turbo"
    save_audio: bool = False
    webhook_url: str | None = None
    webhook_secret: str | None = None
    translation_provider: str = "llm"  # Compatibility for existing programmatic clients.
    cartesia_voice_map: dict[str, str] = field(default_factory=dict)
    allowed_origins: list[str] = field(default_factory=lambda: ["*"])
    realtime_max_languages: int = 4

    def get_channel(self, channel_key: str) -> ChannelConfig:
        try:
            return self.channels[channel_key]
        except KeyError as exc:
            raise KeyError(f"Unknown channel '{channel_key}'. Expected one of: {', '.join(self.channels)}") from exc

    def get_peer_channel(self, channel_key: str) -> ChannelConfig:
        if len(self.channels) != 2:
            raise RuntimeError("Peer channel resolution currently expects exactly 2 channels.")
        for key, channel in self.channels.items():
            if key != channel_key:
                return channel
        raise KeyError(f"Unable to find peer channel for '{channel_key}'.")

    def missing_runtime_values(self, channel_key: str | None = None) -> list[str]:
        missing: list[str] = []

        if _is_missing(self.soniox_api_key):
            missing.append("SONIOX_API_KEY")
        if self.translation_provider != "soniox" and _is_missing(self.gemma_model):
            missing.append("GEMMA_MODEL")
        if self.tts_provider == "google":
            if self.google_tts_credentials_path is None:
                missing.append("GOOGLE_TTS_CREDENTIALS_PATH")
            elif not self.google_tts_credentials_path.exists():
                missing.append("GOOGLE_TTS_CREDENTIALS_PATH")
        elif self.tts_provider == "cartesia":
            if _is_missing(self.cartesia_api_key):
                missing.append("CARTESIA_API_KEY")

        if channel_key is None:
            channels = list(self.channels.values())
        else:
            speaker_channel = self.get_channel(channel_key)
            listener_channel = self.get_peer_channel(channel_key)
            channels = [speaker_channel, listener_channel]

        for channel in channels:
            key = channel.key
            if _is_missing(channel.source_language) or channel.source_language.lower() == "auto":
                missing.append(f"CHANNEL_{key}_SOURCE_LANGUAGE")
        if channel_key is None:
            voice_channels = channels
        else:
            voice_channels = [self.get_peer_channel(channel_key)]
        if self.tts_provider == "cartesia":
            for channel in voice_channels:
                if _is_missing(channel.cartesia_voice_id):
                    missing.append(f"CHANNEL_{channel.key}_CARTESIA_VOICE_ID")

        return missing


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config() -> AppConfig:
    root = project_root()
    load_dotenv(root / ".env", override=False)
    tts_provider = _normalize_tts_provider(_env("TTS_PROVIDER", "cartesia"))
    translation_provider = _env("TRANSLATION_PROVIDER", "soniox").lower()
    if translation_provider not in {"soniox", "llm"}:
        raise ValueError("TRANSLATION_PROVIDER must be soniox or llm.")
    if translation_provider == "soniox" and tts_provider != "cartesia":
        raise ValueError("Soniox meeting mode requires TTS_PROVIDER=cartesia.")
    try:
        voice_map = json.loads(_env("CARTESIA_VOICE_MAP", "{}"))
    except ValueError as exc:
        raise ValueError("CARTESIA_VOICE_MAP must be a JSON language-to-voice mapping.") from exc
    if not isinstance(voice_map, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in voice_map.items()):
        raise ValueError("CARTESIA_VOICE_MAP must map language codes to non-empty voice IDs.")
    google_tts_credentials_path = _resolve_optional_path(
        _first_non_empty_env(
            "GOOGLE_TTS_CREDENTIALS_PATH",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ),
        root,
    )

    channels = {
        "1": _load_channel("1"),
        "2": _load_channel("2"),
    }

    return AppConfig(
        project_root=root,
        session_state_path=_resolve_path(
            _env("SESSION_STATE_FILE", ".session_languages.json"),
            root,
        ),
        session_join_window_seconds=_env_float("SESSION_JOIN_WINDOW_SECONDS", 300.0),
        log_level=_env("APP_LOG_LEVEL", "INFO"),
        mic_sample_rate=_env_int("MIC_SAMPLE_RATE", 16000),
        mic_channels=_env_int("MIC_CHANNELS", 1),
        mic_block_ms=_env_int("MIC_BLOCK_MS", 100),
        voice_activity_threshold=_env_float("VOICE_ACTIVITY_THRESHOLD", 550.0),
        voice_silence_seconds=_env_float("VOICE_SILENCE_SECONDS", 1.0),
        voice_max_record_seconds=_env_float("VOICE_MAX_RECORD_SECONDS", 12.0),
        wait_for_speech_seconds=_env_float("WAIT_FOR_SPEECH_SECONDS", 0.0),
        soniox_api_key=_env("SONIOX_API_KEY", ""),
        soniox_model=_env("SONIOX_MODEL", "stt-rt-v5"),
        soniox_websocket_url=_env(
            "SONIOX_WEBSOCKET_URL",
            "wss://stt-rt.soniox.com/transcribe-websocket",
        ),
        soniox_max_endpoint_delay_ms=_env_int("SONIOX_MAX_ENDPOINT_DELAY_MS", 1000),
        soniox_keepalive_seconds=_env_float("SONIOX_KEEPALIVE_SECONDS", 8.0),
        soniox_session_retry_attempts=_env_int("SONIOX_SESSION_RETRY_ATTEMPTS", 1),
        soniox_session_retry_delay_seconds=_env_float(
            "SONIOX_SESSION_RETRY_DELAY_SECONDS",
            1.0,
        ),
        soniox_session_max_retry_delay_seconds=_env_float(
            "SONIOX_SESSION_MAX_RETRY_DELAY_SECONDS",
            8.0,
        ),
        stt_stream_chunk_ms=_env_int("STT_STREAM_CHUNK_MS", 100),
        gemma_response_base_url=_env(
            "GEMMA_RESPONSE_BASE_URL",
            DEFAULT_GEMMA_RESPONSE_BASE_URL,
        ).rstrip("/"),
        gemma_response_api_key=_env("GEMMA_RESPONSE_API_KEY", ""),
        gemma_model=_env("GEMMA_MODEL", "qwen/qwen3.8-27b"),
        gemma_temperature=_env_float("GEMMA_TEMPERATURE", 0.0),
        gemma_max_tokens=_env_int("GEMMA_MAX_TOKENS", 128),
        gemma_request_timeout_seconds=_env_float(
            "GEMMA_REQUEST_TIMEOUT_SECONDS",
            45.0,
        ),
        tts_provider=tts_provider,
        google_tts_credentials_path=google_tts_credentials_path,
        google_tts_speaking_rate=_env_float("GOOGLE_TTS_SPEAKING_RATE", 1.0),
        google_tts_pitch=_env_float("GOOGLE_TTS_PITCH", 0.0),
        google_tts_request_timeout_seconds=_env_float(
            "GOOGLE_TTS_REQUEST_TIMEOUT_SECONDS",
            20.0,
        ),
        cartesia_api_key=_env("CARTESIA_API_KEY", ""),
        cartesia_api_url=_env("CARTESIA_API_URL", "https://api.cartesia.ai").rstrip("/"),
        cartesia_websocket_url=_env(
            "CARTESIA_WEBSOCKET_URL",
            "wss://api.cartesia.ai/tts/websocket",
        ).rstrip("/"),
        cartesia_version=_env("CARTESIA_VERSION", "2026-08-14"),
        cartesia_request_timeout_seconds=_env_float(
            "CARTESIA_REQUEST_TIMEOUT_SECONDS",
            15.0,
        ),
        cartesia_speed=_env_float("CARTESIA_SPEED", 1.0),
        cartesia_context_prefix=_env("CARTESIA_CONTEXT_PREFIX", "kinesia"),
        cartesia_emotions=_split_csv(
            _env("CARTESIA_EMOTIONS", "")
        ),
        save_audio=_env("SAVE_AUDIO", "false").lower() in {"1", "true", "yes"},
        webhook_url=_env("WEBHOOK_URL", "") or None,
        webhook_secret=_env("WEBHOOK_SECRET", "") or None,
        channels=channels,
        groq_whisper_model=_env("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"),
        translation_provider=translation_provider,
        cartesia_voice_map=voice_map,
        allowed_origins=_split_csv(_env("CORS_ORIGINS", "*")),
        realtime_max_languages=max(1, min(8, _env_int("REALTIME_MAX_LANGUAGES", 4))),
    )


def _load_channel(channel_key: str) -> ChannelConfig:
    prefix = f"CHANNEL_{channel_key}_"
    source_language = _env(f"{prefix}SOURCE_LANGUAGE", "en" if channel_key == "1" else "hi")
    raw_hints = _env(f"{prefix}SONIOX_LANGUAGE_HINTS", "")
    soniox_language_hints = _split_csv(raw_hints) or default_indian_language_hints()
    return ChannelConfig(
        key=channel_key,
        name=_env(f"{prefix}NAME", f"Channel {channel_key}"),
        source_language=source_language,
        language_mode=_normalize_language_mode(
            _env(f"{prefix}LANGUAGE_MODE", "auto")
        ),
        input_sample_rate=_env_optional_int(f"{prefix}INPUT_SAMPLE_RATE"),
        soniox_language_hints=soniox_language_hints,
        cartesia_voice_id=_env(f"{prefix}CARTESIA_VOICE_ID", _env("CARTESIA_VOICE_ID", DEFAULT_CARTESIA_VOICE_ID)),
        cartesia_model_id=_env(f"{prefix}CARTESIA_MODEL_ID", _env("CARTESIA_MODEL_ID", "sonic-3.6-2026-08-27")),
        cartesia_sample_rate=_env_int(f"{prefix}CARTESIA_SAMPLE_RATE", _env_int("CARTESIA_SAMPLE_RATE", 24000)),
        google_tts_language_code=_env(f"{prefix}GOOGLE_TTS_LANGUAGE_CODE", ""),
        google_tts_voice_name=_env(f"{prefix}GOOGLE_TTS_VOICE_NAME", ""),
        google_tts_voice_gender=_env(
            f"{prefix}GOOGLE_TTS_VOICE_GENDER",
            "NEUTRAL",
        ),
        google_tts_sample_rate=_env_int(
            f"{prefix}GOOGLE_TTS_SAMPLE_RATE",
            24000,
        ),
        translation_hint=_env(
            f"{prefix}TRANSLATION_HINT",
            "Keep the translation natural and ready to be spoken aloud.",
        ),
        input_device=_env_device(f"{prefix}INPUT_DEVICE"),
        output_device=_env_device(f"{prefix}OUTPUT_DEVICE"),
    )


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.strip() else default


def _env_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if not value or not value.strip():
        return None
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value and value.strip() else default


def _first_non_empty_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _env_device(name: str) -> int | str | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    return value


def _resolve_path(value: str, root: Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _resolve_optional_path(value: str, root: Path) -> Path | None:
    if not value.strip():
        return None
    return _resolve_path(value, root)


def _normalize_tts_provider(value: str) -> str:
    provider = value.strip().lower()
    if provider in {"edge", "google", "cartesia"}:
        return provider
    return "edge"


def _normalize_language_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode in {"auto", "fixed"}:
        return mode
    return "auto"


def _is_missing(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    return normalized.startswith(PLACEHOLDER_PREFIXES)
