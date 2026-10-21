from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from backend.config import AppConfig, ChannelConfig

logger = logging.getLogger(__name__)

DEFAULT_EDGE_VOICES: dict[str, str] = {
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
    "mr": "mr-IN-AarohiNeural",
    "bn": "bn-IN-TanishaaNeural",
    "ta": "ta-IN-PallaviNeural",
    "te": "te-IN-ShrutiNeural",
    "gu": "gu-IN-DhwaniNeural",
    "kn": "kn-IN-SapnaNeural",
    "ml": "ml-IN-SobhanaNeural",
    "pa": "pa-IN-GurpreetNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ar": "ar-SA-ZariyahNeural",
}


class EdgeTtsClient:
    """100% Free, Unlimited Neural Text-to-Speech using Microsoft Edge Neural Voices.
    Requires ZERO API keys, ZERO credit cards, and has NO credit limits.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def synthesize(self, text: str, channel: ChannelConfig) -> bytes:
        return self.synthesize_for_language(text, channel, channel.source_language)

    def synthesize_for_language(
        self,
        text: str,
        channel: ChannelConfig,
        language: str,
    ) -> bytes:
        clean = text.strip()
        if not clean:
            return b""

        lang_code = language.lower().split("-")[0].strip()
        voice = DEFAULT_EDGE_VOICES.get(lang_code, "en-IN-NeerjaNeural")

        try:
            import edge_tts

            async def _synthesize() -> bytes:
                communicate = edge_tts.Communicate(clean, voice)
                buf = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
                return buf.getvalue()

            # Run in a dedicated asyncio loop if already in an async event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        return pool.submit(lambda: asyncio.run(_synthesize())).result(timeout=15)
                else:
                    return loop.run_until_complete(_synthesize())
            except RuntimeError:
                return asyncio.run(_synthesize())

        except Exception as exc:
            logger.warning("Edge-TTS synthesis error for lang %s: %s", language, exc)
            return b""
