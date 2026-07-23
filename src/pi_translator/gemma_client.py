from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from pi_translator.config import AppConfig, ChannelConfig
from pi_translator.languages import language_label

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

    def translate(
        self,
        text: str,
        speaker_channel: ChannelConfig,
        listener_channel: ChannelConfig,
        speaker_session_language: str,
        listener_session_language: str,
        detected_source_language: str | None = None,
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

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(
                        speaker_channel,
                        listener_channel,
                        speaker_session_language,
                        listener_session_language,
                        detected_source_language,
                    ),
                },
                {
                    "role": "user",
                    "content": self._user_prompt(text, listener_session_language),
                },
            ],
        }

        response = self.session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=self.headers,
            timeout=(3.05, self.timeout),
        )
        if not response.ok:
            raise RuntimeError(
                f"Gemma request failed with {response.status_code}: {response.text.strip()}"
            )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Gemma response did not contain any choices.")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        translated = self._content_to_text(content).strip()
        if not translated:
            raise RuntimeError("Gemma returned an empty translation.")
        return translated

    @staticmethod
    def _system_prompt(
        speaker_channel: ChannelConfig,
        listener_channel: ChannelConfig,
        speaker_session_language: str,
        listener_session_language: str,
        detected_source_language: str | None,
    ) -> str:
        target_language = language_label(listener_session_language)
        source_hint = language_label(
            detected_source_language or speaker_session_language
        )
        prompt = (
            "You are a real-time translation engine. "
            f"Translate the transcript into {target_language}. "
            f"Source-language hint: {source_hint}. "
            "Return only the translation. "
            "Preserve names, numbers, tone, slang, punctuation, uncertainty, and incomplete phrasing. "
            "Do not answer, explain, summarize, add details, add labels, or continue the conversation. "
            "Keep proper nouns, brands, numbers, and addresses unchanged when needed."
        )
        if listener_channel.translation_hint.strip():
            prompt += f" Additional instruction: {listener_channel.translation_hint.strip()}"
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
        return str(content)

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
