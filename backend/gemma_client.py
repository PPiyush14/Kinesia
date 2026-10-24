from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from backend.config import AppConfig, ChannelConfig
from backend.core.exceptions import TranslationError
from backend.languages import language_label

logger = logging.getLogger(__name__)


class GemmaClient:
    def __init__(self, config: AppConfig) -> None:
        self.base_url = config.gemma_response_base_url.rstrip("/")
        self.api_key = config.gemma_response_api_key.strip()
        self.model = config.gemma_model
        self.temperature = config.gemma_temperature
        self.max_tokens = config.gemma_max_tokens
        self.timeout = config.gemma_request_timeout_seconds
        self.session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=2,
            pool_maxsize=4,
            max_retries=0,
            pool_block=False,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.headers = self._headers()

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Execute a general chat completion call against the Gemma endpoint."""
        payload = {
            "model": self.model,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "messages": messages,
        }
        if urlparse(self.base_url).hostname == "api.groq.com" and self.model.lower().startswith("qwen/qwen3"):
            # Groq's raw reasoning format may put <think> text in content.
            payload["reasoning_format"] = "hidden"

        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self.headers,
                timeout=(1.5, self.timeout),
            )
        except requests.RequestException as exc:
            raise TranslationError(f"Gemma request failed: {exc}") from exc

        if not response.ok:
            raise TranslationError(
                f"Gemma request failed with {response.status_code}: {response.text.strip()}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise TranslationError(f"Gemma returned invalid JSON: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise TranslationError("Gemma response did not contain any choices.")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if choices[0].get("finish_reason") == "length":
            raise TranslationError("Gemma response was truncated by the token limit.")
        text = self._content_to_text(content)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # An unfinished reasoning block has no safe user-visible answer.
        text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
        return text.strip()

    def translate(
        self,
        text: str,
        speaker_channel: ChannelConfig,
        listener_channel: ChannelConfig,
        speaker_session_language: str,
        listener_session_language: str,
        detected_source_language: str | None = None,
        glossary_hint: str | None = None,
    ) -> str:
        if not text.strip():
            return ""

        max_tokens = self._estimate_max_tokens(text)
        logger.debug(
            "Gemma request target=%s detected=%s max_tokens=%d chars=%d",
            listener_session_language,
            detected_source_language,
            max_tokens,
            len(text.strip()),
        )

        messages = [
            {
                "role": "system",
                "content": self._system_prompt(
                    speaker_channel,
                    listener_channel,
                    speaker_session_language,
                    listener_session_language,
                    detected_source_language,
                    glossary_hint=glossary_hint,
                ),
            },
            {
                "role": "user",
                "content": self._user_prompt(text, listener_session_language),
            },
        ]

        translated = self.chat_completion(
            messages,
            temperature=self.temperature,
            max_tokens=max_tokens,
        )
        if not translated:
            raise TranslationError("Gemma returned an empty translation.")
        return translated

    @staticmethod
    def _system_prompt(
        speaker_channel: ChannelConfig,
        listener_channel: ChannelConfig,
        speaker_session_language: str,
        listener_session_language: str,
        detected_source_language: str | None,
        glossary_hint: str | None = None,
    ) -> str:
        target_language = language_label(listener_session_language)
        source_hint = language_label(
            detected_source_language or speaker_session_language
        )
        prompt = (
            "You are a professional real-time conversational translation engine. "
            f"Your job is to translate the user transcript strictly and accurately into {target_language}. "
            f"Source-language context: {source_hint}. "
            "STRICT MANDATORY RULES:\n"
            f"1. OUTPUT TARGET LANGUAGE: The entire output MUST be in {target_language}. "
            f"If target is Hindi, output exclusively in fluent, natural Devanagari Hindi. "
            "Even if the speaker spoke English, code-switched, or used Hinglish words, you MUST translate the whole sentence into the target language. "
            "NEVER echo back untranslated English words when target is Hindi or another Indian language.\n"
            "2. CONVERSATIONAL ACCURACY: Maintain informal tone, humor, sentence pacing, and natural flow.\n"
            "3. CLEAN FORMAT: Return ONLY the direct translation. Do not add quotes, markdown, explanations, speaker tags, or notes.\n"
            "4. GRAMMAR & NATURAL DIALOGUE: Preserve the speaker's meaning, including intentional repetition, emphasis, and grammatical repeated words. Do not remove words merely because they repeat."
        )
        if listener_channel.translation_hint.strip():
            prompt += f" Additional instruction: {listener_channel.translation_hint.strip()}"
        if glossary_hint and glossary_hint.strip():
            prompt += f" Glossary instruction: {glossary_hint.strip()}"
        return prompt

    @staticmethod
    def _user_prompt(text: str, listener_session_language: str) -> str:
        target_language = language_label(listener_session_language)
        return (
            f"Target language: {target_language}\n"
            "<transcript>\n"
            f"{text.strip()}\n"
            "</transcript>"
        )

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "keep-alive",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _estimate_max_tokens(self, text: str) -> int:
        words = max(1, len(text.strip().split()))
        estimated = (words * 4) + 16
        return max(48, min(self.max_tokens, estimated))
