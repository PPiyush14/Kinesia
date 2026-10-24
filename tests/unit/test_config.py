"""Unit tests for configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.config import AppConfig, ChannelConfig
from backend.config import load_config


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


def speech_config(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.project_root", lambda: tmp_path)
    for name in list(os.environ):
        if name.startswith(("SONIOX_", "CARTESIA_", "GEMMA_", "TTS_", "TRANSLATION_", "CHANNEL_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("SONIOX_API_KEY", "mock-soniox")
    monkeypatch.setenv("CARTESIA_API_KEY", "mock-cartesia")


def test_web_configuration_defaults_to_two_provider_mode(monkeypatch, tmp_path):
    speech_config(monkeypatch, tmp_path)
    config = load_config()
    assert config.translation_provider == "soniox"
    assert config.tts_provider == "cartesia"
    assert config.cartesia_emotions == []
    assert config.cartesia_speed == 1.1
    assert config.cartesia_request_timeout_seconds == 15
    assert config.channels["1"].cartesia_voice_id
    assert config.channels["1"].cartesia_model_id == "sonic-3.6-2026-08-27"
    assert config.missing_runtime_values() == []
    monkeypatch.setenv("CARTESIA_SPEED", "1.05")
    assert load_config().cartesia_speed == 1.05


def test_soniox_mode_cannot_silently_select_another_tts_provider(monkeypatch, tmp_path):
    speech_config(monkeypatch, tmp_path)
    monkeypatch.setenv("TTS_PROVIDER", "google")
    with pytest.raises(ValueError, match="requires TTS_PROVIDER=cartesia"):
        load_config()


def test_language_voice_map_is_validated(monkeypatch, tmp_path):
    speech_config(monkeypatch, tmp_path)
    monkeypatch.setenv("CARTESIA_VOICE_MAP", '{"hi":"test-voice"}')
    assert load_config().cartesia_voice_map == {"hi": "test-voice"}
    monkeypatch.setenv("CARTESIA_VOICE_MAP", '["not-a-mapping"]')
    with pytest.raises(ValueError, match="map language"):
        load_config()
