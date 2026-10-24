"""Shared fixtures and mocks for pytest test suite."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import pytest

from backend.config import AppConfig, ChannelConfig
from backend.conversation_store import SessionConversationStore
from backend.storage.glossary import GlossaryEntry, GlossaryStore


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Never let integration server startup touch the developer's database."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("backend.db.connection._get_sqlite_path", lambda: tmp_path / "isolated.db")


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provides an isolated temporary directory for test file I/O."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def sample_config(temp_dir: Path) -> AppConfig:
    """Provides a valid AppConfig configured for testing."""
    channels = {
        "ch1": ChannelConfig(
            key="ch1",
            name="Channel A (English)",
            source_language="en",
            language_mode="auto",
            input_device=None,
            output_device=None,
        ),
        "ch2": ChannelConfig(
            key="ch2",
            name="Channel B (Hindi)",
            source_language="hi",
            language_mode="auto",
            input_device=None,
            output_device=None,
        ),
    }
    return AppConfig(
        project_root=temp_dir,
        session_state_path=temp_dir / ".session_languages.json",
        session_join_window_seconds=300.0,
        log_level="INFO",
        mic_sample_rate=16000,
        mic_channels=1,
        mic_block_ms=20,
        voice_activity_threshold=0.015,
        voice_silence_seconds=0.7,
        voice_max_record_seconds=12.0,
        wait_for_speech_seconds=0.0,
        soniox_api_key="mock_soniox_key",
        soniox_model="stt-rt-v5",
        soniox_websocket_url="wss://mock.soniox.com",
        soniox_max_endpoint_delay_ms=1500,
        soniox_keepalive_seconds=8.0,
        soniox_session_retry_attempts=3,
        soniox_session_retry_delay_seconds=0.5,
        soniox_session_max_retry_delay_seconds=2.0,
        stt_stream_chunk_ms=100,
        gemma_response_base_url="https://mock.gemma.api",
        gemma_response_api_key="mock_gemma_key",
        gemma_model="gemma-2-9b-it",
        gemma_temperature=0.0,
        gemma_max_tokens=128,
        gemma_request_timeout_seconds=5.0,
        tts_provider="cartesia",
        google_tts_credentials_path=None,
        google_tts_speaking_rate=1.0,
        google_tts_pitch=0.0,
        google_tts_request_timeout_seconds=5.0,
        cartesia_api_key="mock_cartesia_key",
        cartesia_api_url="https://api.cartesia.ai",
        cartesia_websocket_url="wss://api.cartesia.ai",
        cartesia_version="2026-03-01",
        cartesia_request_timeout_seconds=5.0,
        cartesia_speed=0.9,
        cartesia_context_prefix="test",
        cartesia_emotions=[],
        channels=channels,
        save_audio=True,
        webhook_url=None,
        webhook_secret=None,
    )


@pytest.fixture
def conversation_store(temp_dir: Path) -> SessionConversationStore:
    """Provides a fresh SessionConversationStore writing into a temporary directory."""
    return SessionConversationStore(temp_dir)


@pytest.fixture
def glossary_store(temp_dir: Path) -> GlossaryStore:
    """Provides a fresh GlossaryStore."""
    return GlossaryStore(temp_dir)


@pytest.fixture
def sample_turns() -> list[dict]:
    """Returns sample conversation turns for unit and integration testing."""
    return [
        {
            "turn_index": 0,
            "captured_at": 1700000000.0,
            "speaker_channel_key": "ch1",
            "speaker_channel_name": "Channel A",
            "listener_channel_key": "ch2",
            "listener_channel_name": "Channel B",
            "detected_source_language": "en",
            "speaker_session_language": "en",
            "listener_language": "hi",
            "transcript": "Let's schedule the project review with TechCorp on Friday.",
            "translation": "आइए शुक्रवार को टेकॉर्प के साथ परियोजना समीक्षा निर्धारित करें।",
            "stt_confidence": 0.94,
            "entities": {
                "ORG": ["TechCorp"],
                "DATE": ["Friday"],
            },
            "tts_provider": "cartesia",
            "latency_ms": {
                "gemma": 820.0,
                "tts": 350.0,
                "playback": 200.0,
                "total": 1370.0,
            },
        },
        {
            "turn_index": 1,
            "captured_at": 1700000015.0,
            "speaker_channel_key": "ch2",
            "speaker_channel_name": "Channel B",
            "listener_channel_key": "ch1",
            "listener_channel_name": "Channel A",
            "detected_source_language": "hi",
            "speaker_session_language": "hi",
            "listener_language": "en",
            "transcript": "हाँ, राहुल भी मुंबई से बैठक में शामिल होंगे।",
            "translation": "Yes, Rahul will also join the meeting from Mumbai.",
            "stt_confidence": 0.88,
            "entities": {
                "PERSON": ["राहुल"],
                "LOC": ["मुंबई"],
            },
            "tts_provider": "cartesia",
            "latency_ms": {
                "gemma": 750.0,
                "tts": 310.0,
                "playback": 180.0,
                "total": 1240.0,
            },
        },
    ]
