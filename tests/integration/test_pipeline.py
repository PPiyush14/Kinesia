"""Integration tests for the translator pipeline."""

from __future__ import annotations

from unittest.mock import patch

from backend.audio import CapturedAudio
from backend.config import AppConfig
from backend.pipeline import TranslationRunResult, TranslatorPipeline
from backend.soniox_client import SonioxLiveTranscriptionResult


def test_pipeline_turn_execution(sample_config: AppConfig) -> None:
    pipeline = TranslatorPipeline(sample_config)

    # Mock STT output
    mock_audio = CapturedAudio(
        raw_pcm_s16le=b"\x00\x00" * 8000,
        sample_rate=16000,
        channels=1,
        duration_seconds=0.5,
    )
    live_result = SonioxLiveTranscriptionResult(
        captured_audio=mock_audio,
        transcript="Hello, we are testing the speech translator.",
        detected_language="en",
        confidence=0.95,
    )

    # Mock Gemma translation and TTS synthesis
    with patch.object(pipeline.gemma, "translate", return_value="नमस्ते, हम भाषण अनुवादक का परीक्षण कर रहे हैं।"), \
         patch.object(pipeline.tts, "synthesize_for_language", return_value=b"RIFFdummywav"), \
         patch.object(pipeline.recorder, "play_wav_bytes", return_value=None):

        result = pipeline._translate_live_transcription("ch1", live_result)

        assert isinstance(result, TranslationRunResult)
        assert result.speaker_channel_key == "ch1"
        assert result.listener_channel_key == "ch2"
        assert result.detected_source_language == "en"
        assert result.stt_confidence == 0.95
        assert result.transcript == "Hello, we are testing the speech translator."
        assert result.translation == "नमस्ते, हम भाषण अनुवादक का परीक्षण कर रहे हैं।"
        assert isinstance(result.entities, dict)
