from __future__ import annotations

import logging
from typing import Any

from backend.audio import MicrophoneRecorder
from backend.config import AppConfig, ChannelConfig
from backend.languages import (
    default_google_tts_voice_name,
    normalize_google_tts_language,
)

logger = logging.getLogger(__name__)


class GoogleTtsClient:
    def __init__(self, config: AppConfig) -> None:
        texttospeech, service_account = _load_google_tts_sdk()
        self._texttospeech = texttospeech
        self.credentials_path = config.google_tts_credentials_path
        self.speaking_rate = config.google_tts_speaking_rate
        self.pitch = config.google_tts_pitch
        self.timeout = config.google_tts_request_timeout_seconds

        if self.credentials_path is None:
            self.client = texttospeech.TextToSpeechClient()
            return

        credentials = service_account.Credentials.from_service_account_file(
            str(self.credentials_path)
        )
        self.client = texttospeech.TextToSpeechClient(credentials=credentials)

    def synthesize(self, text: str, channel: ChannelConfig) -> bytes:
        return self.synthesize_for_language(text, channel, channel.source_language)

    def synthesize_for_language(
        self,
        text: str,
        channel: ChannelConfig,
        language: str,
    ) -> bytes:
        if not text.strip():
            raise RuntimeError("Google TTS received an empty text input.")

        resolved_language = (
            normalize_google_tts_language(language)
            or normalize_google_tts_language(channel.google_tts_language_code)
            or normalize_google_tts_language(channel.source_language)
            or "hi-IN"
        )
        preferred_voice_name = (
            channel.google_tts_voice_name.strip()
            or default_google_tts_voice_name(resolved_language)
        )
        logger.debug(
            "Google TTS selected language=%s voice=%s",
            resolved_language,
            preferred_voice_name,
        )

        try:
            return self._synthesize_once(
                text=text,
                channel=channel,
                language_code=resolved_language,
                voice_name=preferred_voice_name,
            )
        except Exception as exc:
            if preferred_voice_name:
                logger.warning(
                    "Google TTS voice '%s' failed for %s; retrying with automatic voice selection: %s",
                    preferred_voice_name,
                    resolved_language,
                    exc,
                )
                return self._synthesize_once(
                    text=text,
                    channel=channel,
                    language_code=resolved_language,
                    voice_name="",
                )
            raise

    def _synthesize_once(
        self,
        *,
        text: str,
        channel: ChannelConfig,
        language_code: str,
        voice_name: str,
    ) -> bytes:
        sdk = self._texttospeech
        voice_kwargs: dict[str, Any] = {
            "language_code": language_code,
            "ssml_gender": _resolve_ssml_gender(
                sdk,
                channel.google_tts_voice_gender,
            ),
        }
        if voice_name:
            voice_kwargs["name"] = voice_name

        request = sdk.SynthesizeSpeechRequest(
            input=sdk.SynthesisInput(text=text.strip()),
            voice=sdk.VoiceSelectionParams(**voice_kwargs),
            audio_config=sdk.AudioConfig(
                audio_encoding=sdk.AudioEncoding.LINEAR16,
                speaking_rate=self.speaking_rate,
                pitch=self.pitch,
                sample_rate_hertz=channel.google_tts_sample_rate,
            ),
        )
        response = self.client.synthesize_speech(
            request=request,
            timeout=self.timeout,
        )
        audio_content = bytes(response.audio_content)
        if not audio_content:
            raise RuntimeError("Google TTS returned no audio content.")
        if audio_content.startswith(b"RIFF"):
            return audio_content
        return MicrophoneRecorder.wav_bytes_from_pcm_s16le(
            audio_content,
            sample_rate=channel.google_tts_sample_rate,
            channels=1,
        )


def _load_google_tts_sdk():
    try:
        from google.cloud import texttospeech
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "Google TTS support requires google-cloud-texttospeech. "
            "Install the updated requirements first."
        ) from exc
    return texttospeech, service_account


def _resolve_ssml_gender(texttospeech, value: str):
    gender = (value or "").strip().upper()
    if gender == "MALE":
        return texttospeech.SsmlVoiceGender.MALE
    if gender == "FEMALE":
        return texttospeech.SsmlVoiceGender.FEMALE
    return texttospeech.SsmlVoiceGender.NEUTRAL
