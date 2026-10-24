from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Any

from backend.config import AppConfig, ChannelConfig

logger = logging.getLogger(__name__)

# Microsoft Azure Indian & Global Neural Voices (100% Free, Zero Key, Studio Quality)
INDIAN_VOICES_FEMALE: dict[str, str] = {
    "hi": "hi-IN-SwaraNeural",           # Hindi (Female, Warm & Natural)
    "en": "en-IN-NeerjaExpressiveNeural", # Indian English (Female, Expressive)
    "mr": "mr-IN-AarohiNeural",          # Marathi (Female)
    "bn": "bn-IN-TanishaaNeural",        # Bengali (Female)
    "ta": "ta-IN-PallaviNeural",         # Tamil (Female)
    "te": "te-IN-ShrutiNeural",          # Telugu (Female)
    "gu": "gu-IN-DhwaniNeural",          # Gujarati (Female)
    "kn": "kn-IN-SapnaNeural",           # Kannada (Female)
    "ml": "ml-IN-SobhanaNeural",         # Malayalam (Female)
    "pa": "pa-IN-GurpreetNeural",        # Punjabi (Female)
    "ur": "ur-IN-GulNeural",             # Urdu (Female)
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

INDIAN_VOICES_MALE: dict[str, str] = {
    "hi": "hi-IN-MadhurNeural",          # Hindi (Male, Deep & Clear)
    "en": "en-IN-PrabhatNeural",         # Indian English (Male)
    "mr": "mr-IN-ManoharNeural",         # Marathi (Male)
    "bn": "bn-IN-BashkarNeural",         # Bengali (Male)
    "ta": "ta-IN-ValluvarNeural",        # Tamil (Male)
    "te": "te-IN-MohanNeural",           # Telugu (Male)
    "gu": "gu-IN-NiranjanNeural",        # Gujarati (Male)
    "kn": "kn-IN-GaganNeural",           # Kannada (Male)
    "ml": "ml-IN-MidhunNeural",          # Malayalam (Male)
    "pa": "pa-IN-GurpreetNeural",        # Punjabi
    "ur": "ur-IN-SalmanNeural",          # Urdu (Male)
    "es": "es-ES-AlvaroNeural",
    "fr": "fr-FR-HenriNeural",
    "de": "de-DE-ConradNeural",
    "it": "it-IT-DiegoNeural",
    "pt": "pt-BR-AntonioNeural",
    "ru": "ru-RU-DmitryNeural",
    "ja": "ja-JP-KeitaNeural",
    "ko": "ko-KR-InJoonNeural",
    "zh": "zh-CN-YunjianNeural",
    "ar": "ar-SA-HamedNeural",
}


class EdgeTtsClient:
    """100% Free, Unlimited Neural Text-to-Speech using Microsoft Edge Neural Voices.
    Supports authentic Indian Hindi, Indian English, Marathi, Bengali, Tamil, Telugu, etc.
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
        gender: str | None = None,
    ) -> bytes:
        clean = text.strip()
        if not clean:
            return b""

        # Use passed gender directly, with env fallback
        effective_gender = (gender or os.getenv("TTS_VOICE_GENDER", "female")).lower().strip()
        voice_map = INDIAN_VOICES_MALE if effective_gender == "male" else INDIAN_VOICES_FEMALE

        # If a specific custom voice is explicitly requested in env or channel
        custom_voice = os.getenv("DEFAULT_TTS_VOICE", "").strip()
        lang_code = language.lower().split("-")[0].strip()

        voice = custom_voice or voice_map.get(lang_code, "en-IN-NeerjaExpressiveNeural")

        try:
            import edge_tts

            async def _synthesize() -> bytes:
                communicate = edge_tts.Communicate(clean, voice)
                buf = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
                return buf.getvalue()

            async def _bounded_synthesize() -> bytes:
                return await asyncio.wait_for(_synthesize(), timeout=15)

            # Only catch the absence of an event loop. A provider RuntimeError
            # must not silently trigger a second synthesis request.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_bounded_synthesize())
            else:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(lambda: asyncio.run(_bounded_synthesize())).result(timeout=18)

        except Exception as exc:
            logger.warning("Edge-TTS synthesis error for lang %s: %s", language, exc)
            return b""
