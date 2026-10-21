from __future__ import annotations

import io
import logging
from typing import Any

import requests

from backend.config import AppConfig

logger = logging.getLogger(__name__)


class GroqWhisperClient:
    """Ultra-fast, multilingual Speech-to-Text powered by Groq Whisper LPU inference.
    Uses the exact same Groq API key as the translation engine.
    """

    def __init__(self, config: AppConfig) -> None:
        self.api_key = config.gemma_response_api_key.strip()
        self.base_url = config.gemma_response_base_url.rstrip("/")
        self.model = getattr(config, "groq_whisper_model", "whisper-large-v3-turbo")

    def transcribe(
        self,
        audio_bytes: bytes,
        language: str | None = None,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
    ) -> str:
        """Transcribe speech audio using Groq Whisper."""
        if not audio_bytes:
            return ""

        if not self.api_key:
            raise RuntimeError("Groq API key not configured for Groq Whisper STT.")

        url = f"{self.base_url}/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        data: dict[str, Any] = {
            "model": self.model,
            "response_format": "json",
            "temperature": "0.0",
        }
        if language:
            # Pass clean ISO 639-1 code (e.g. 'hi' or 'en')
            clean_lang = language.lower().split("-")[0].strip()
            data["language"] = clean_lang

        files = {
            "file": (filename, io.BytesIO(audio_bytes), mime_type),
        }

        try:
            resp = requests.post(url, headers=headers, data=data, files=files, timeout=15)
            if not resp.ok:
                logger.error("Groq Whisper error (%s): %s", resp.status_code, resp.text)
                return ""
            payload = resp.json()
            return str(payload.get("text", "")).strip()
        except Exception as exc:
            logger.error("Groq Whisper request failed: %s", exc)
            return ""
