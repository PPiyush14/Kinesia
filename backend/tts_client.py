from __future__ import annotations

import logging
from backend.cartesia_client import CartesiaClient
from backend.config import AppConfig, ChannelConfig
from backend.edge_tts_client import EdgeTtsClient
from backend.google_tts_client import GoogleTtsClient

logger = logging.getLogger(__name__)


class TtsClient:
    def __init__(self, config: AppConfig) -> None:
        self.provider_name = config.tts_provider.lower().strip()
        self._edge = EdgeTtsClient(config)

        if self.provider_name == "google":
            self._client = GoogleTtsClient(config)
        elif self.provider_name == "cartesia":
            self._client = CartesiaClient(config)
        else:
            self._client = self._edge

    def synthesize(self, text: str, channel: ChannelConfig) -> bytes:
        try:
            audio = self._client.synthesize(text, channel)
            if audio:
                return audio
        except Exception as exc:
            logger.debug("Primary TTS failed, falling back to free Edge-TTS: %s", exc)
        return self._edge.synthesize(text, channel)

    def synthesize_for_language(
        self,
        text: str,
        channel: ChannelConfig,
        language: str,
        gender: str = "female",
    ) -> bytes:
        try:
            audio = self._client.synthesize_for_language(text, channel, language)
            if audio:
                return audio
        except Exception as exc:
            logger.debug("Primary TTS failed for %s, falling back to free Edge-TTS: %s", language, exc)
        return self._edge.synthesize_for_language(text, channel, language, gender=gender)
