from __future__ import annotations

import logging
from backend.cartesia_client import CartesiaClient
from backend.config import AppConfig, ChannelConfig
from backend.edge_tts_client import EdgeTtsClient
from backend.google_tts_client import GoogleTtsClient

logger = logging.getLogger(__name__)


class TtsClient:
    def __init__(self, config: AppConfig) -> None:
        self.allow_edge_fallback = config.translation_provider != "soniox"
        self.provider_name = config.tts_provider.lower().strip()
        self._edge = EdgeTtsClient(config)

        if self.provider_name == "google":
            self._client = GoogleTtsClient(config)
        elif self.provider_name == "cartesia":
            self._client = CartesiaClient(config)
        else:
            self._client = self._edge

    def synthesize(self, text: str, channel: ChannelConfig) -> bytes:
        if self._client is self._edge:
            return self._edge.synthesize(text, channel)
        try:
            audio = self._client.synthesize(text, channel)
            if audio:
                return audio
        except Exception as exc:
            if not self.allow_edge_fallback:
                raise
            logger.debug("Primary TTS failed, falling back to free Edge-TTS: %s", exc)
        if not self.allow_edge_fallback:
            return b""
        return self._edge.synthesize(text, channel)

    def synthesize_for_language(
        self,
        text: str,
        channel: ChannelConfig,
        language: str,
        gender: str = "female",
    ) -> bytes:
        if self.provider_name == "edge":
            return self._edge.synthesize_for_language(text, channel, language, gender=gender)
        try:
            audio = self._client.synthesize_for_language(text, channel, language)
            if audio:
                return audio
        except Exception as exc:
            if not self.allow_edge_fallback:
                raise
            logger.debug("Primary TTS failed for %s, falling back to free Edge-TTS: %s", language, exc)
        if not self.allow_edge_fallback:
            return b""
        return self._edge.synthesize_for_language(text, channel, language, gender=gender)
