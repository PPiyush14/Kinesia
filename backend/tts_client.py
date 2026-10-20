from __future__ import annotations

from backend.cartesia_client import CartesiaClient
from backend.config import AppConfig, ChannelConfig
from backend.google_tts_client import GoogleTtsClient


class TtsClient:
    def __init__(self, config: AppConfig) -> None:
        self.provider_name = config.tts_provider
        if config.tts_provider == "google":
            self._client = GoogleTtsClient(config)
        else:
            self._client = CartesiaClient(config)

    def synthesize(self, text: str, channel: ChannelConfig) -> bytes:
        return self._client.synthesize(text, channel)

    def synthesize_for_language(
        self,
        text: str,
        channel: ChannelConfig,
        language: str,
    ) -> bytes:
        return self._client.synthesize_for_language(text, channel, language)
