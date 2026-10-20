from __future__ import annotations

import uuid

from backend.audio import MicrophoneRecorder
from backend.cartesia_ws_helper import CartesiaSyncWS
from backend.config import AppConfig, ChannelConfig
from backend.languages import normalize_cartesia_language


class CartesiaClient:
    def __init__(self, config: AppConfig) -> None:
        self.api_key = config.cartesia_api_key
        self.api_url = config.cartesia_api_url.rstrip("/")
        self.websocket_url = config.cartesia_websocket_url.rstrip("/")
        self.version = config.cartesia_version
        self.timeout = config.cartesia_request_timeout_seconds
        self.speed = config.cartesia_speed
        self.context_prefix = config.cartesia_context_prefix
        self.emotions = config.cartesia_emotions

    def synthesize(self, text: str, channel: ChannelConfig) -> bytes:
        return self.synthesize_for_language(text, channel, channel.source_language)

    def synthesize_for_language(
        self,
        text: str,
        channel: ChannelConfig,
        language: str,
    ) -> bytes:
        if not text.strip():
            raise RuntimeError("Cartesia received an empty text input.")
        resolved_language = (
            normalize_cartesia_language(language)
            or normalize_cartesia_language(channel.source_language)
            or "hi"
        )

        voice_payload: dict[str, object] = {
            "mode": "id",
            "id": channel.cartesia_voice_id,
        }
        if self.emotions:
            voice_payload["__experimental_controls"] = {
                "emotion": self.emotions,
            }

        payload = {
            "model_id": channel.cartesia_model_id,
            "transcript": text.strip(),
            "voice": voice_payload,
            "language": resolved_language,
            "context_id": f"{self.context_prefix}-{uuid.uuid4()}",
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": channel.cartesia_sample_rate,
            },
            "generation_config": {
                "speed": self.speed,
                "volume": 1.0,
            },
            "continue": False,
        }

        cartesia = CartesiaSyncWS(
            url=self.websocket_url,
            api_key=self.api_key,
            version=self.version,
            timeout_seconds=self.timeout,
        )
        websocket, ok = cartesia.send_tts(payload)
        if not ok or websocket is None:
            raise RuntimeError("Cartesia websocket connection failed before audio streaming started.")

        pcm_chunks: list[bytes] = []
        try:
            for chunk in cartesia.receive_chunks(websocket):
                if chunk:
                    pcm_chunks.append(chunk)
        finally:
            cartesia.close()

        pcm_audio = b"".join(pcm_chunks)
        if not pcm_audio:
            raise RuntimeError("Cartesia websocket returned no audio chunks.")

        return MicrophoneRecorder.wav_bytes_from_pcm_s16le(
            pcm_audio,
            sample_rate=channel.cartesia_sample_rate,
            channels=1,
        )
