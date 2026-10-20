"""Unit tests for configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.config import AppConfig, ChannelConfig


def test_channel_resolution(sample_config: AppConfig) -> None:
    ch1 = sample_config.get_channel("ch1")
    assert ch1.name == "Channel A (English)"
    assert ch1.source_language == "en"

    peer = sample_config.get_peer_channel("ch1")
    assert peer.key == "ch2"

    with pytest.raises(KeyError):
        sample_config.get_channel("non_existent")


def test_missing_runtime_values_checks(sample_config: AppConfig) -> None:
    # Key is non-placeholder
    missing = sample_config.missing_runtime_values()
    assert "SONIOX_API_KEY" not in missing

    # Placeholder test
    dummy = AppConfig(
        project_root=sample_config.project_root,
        session_state_path=sample_config.session_state_path,
        session_join_window_seconds=300.0,
        log_level="INFO",
        mic_sample_rate=16000,
        mic_channels=1,
        mic_block_ms=20,
        voice_activity_threshold=0.015,
        voice_silence_seconds=0.7,
        voice_max_record_seconds=12.0,
        wait_for_speech_seconds=0.0,
        soniox_api_key="your_soniox_key_here",
        soniox_model="stt-rt-v5",
        soniox_websocket_url="wss://stt-rt.soniox.com",
        soniox_max_endpoint_delay_ms=1500,
        soniox_keepalive_seconds=8.0,
        soniox_session_retry_attempts=3,
        soniox_session_retry_delay_seconds=1.0,
        soniox_session_max_retry_delay_seconds=2.0,
        stt_stream_chunk_ms=100,
        gemma_response_base_url="https://api.gemma",
        gemma_response_api_key="",
        gemma_model="model",
        gemma_temperature=0.0,
        gemma_max_tokens=128,
        gemma_request_timeout_seconds=5.0,
        tts_provider="cartesia",
        google_tts_credentials_path=None,
        google_tts_speaking_rate=1.0,
        google_tts_pitch=0.0,
        google_tts_request_timeout_seconds=5.0,
        cartesia_api_key="",
        cartesia_api_url="https://api.cartesia.ai",
        cartesia_websocket_url="wss://api.cartesia.ai",
        cartesia_version="v1",
        cartesia_request_timeout_seconds=5.0,
        cartesia_speed=0.9,
        cartesia_context_prefix="prefix",
        cartesia_emotions=[],
        channels=sample_config.channels,
    )
    missing = dummy.missing_runtime_values()
    assert "SONIOX_API_KEY" in missing
    assert "CARTESIA_API_KEY" in missing
