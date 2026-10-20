"""Unit tests for SessionConversationStore."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.conversation_store import SessionConversationStore
from backend.core.exceptions import StorageError


def test_ensure_and_read_session(conversation_store: SessionConversationStore) -> None:
    session_id = "test_session_123"
    path = conversation_store.ensure_session(session_id, started_at=1700000000.0)
    assert path.exists()

    data = conversation_store.read_session(session_id)
    assert data["session_id"] == session_id
    assert data["started_at"] == 1700000000.0
    assert data["turns"] == []


def test_append_and_update_turn(
    conversation_store: SessionConversationStore,
    sample_turns: list[dict[str, Any]],
) -> None:
    session_id = "test_session_turns"
    conversation_store.ensure_session(session_id, started_at=1700000000.0)

    # Append turn
    conversation_store.append_turn(session_id, sample_turns[0])
    summary = conversation_store.read_summary(session_id)
    assert summary["turn_count"] == 1

    # Update turn (Language override test)
    updated = conversation_store.update_turn(
        session_id,
        0,
        {"detected_source_language": "fr", "translation": "Bonjour le monde"},
    )
    assert updated["detected_source_language"] == "fr"
    assert updated["translation"] == "Bonjour le monde"

    # Verify persistence
    fresh = conversation_store.read_session(session_id)
    assert fresh["turns"][0]["detected_source_language"] == "fr"


def test_update_turn_out_of_bounds(conversation_store: SessionConversationStore) -> None:
    session_id = "test_empty_session"
    conversation_store.ensure_session(session_id, started_at=1700000000.0)

    with pytest.raises(StorageError):
        conversation_store.update_turn(session_id, 999, {"detected_source_language": "hi"})


def test_search_turns(
    conversation_store: SessionConversationStore,
    sample_turns: list[dict[str, Any]],
) -> None:
    session_id = "session_search_test"
    conversation_store.ensure_session(session_id, started_at=1700000000.0)
    conversation_store.append_turn(session_id, sample_turns[0])
    conversation_store.append_turn(session_id, sample_turns[1])

    # Search for term in English transcript
    matches = conversation_store.search_turns("TechCorp")
    assert len(matches) == 1
    assert matches[0]["session_id"] == session_id

    # Search for term in Hindi transcript
    matches_hi = conversation_store.search_turns("मुंबई")
    assert len(matches_hi) == 1

    # Case-insensitive search
    matches_case = conversation_store.search_turns("techcorp")
    assert len(matches_case) == 1

    # Non-existent query
    empty = conversation_store.search_turns("non_existent_keyword_xyz")
    assert len(empty) == 0


def test_save_and_read_summary_file(conversation_store: SessionConversationStore) -> None:
    session_id = "session_summary_test"
    summary_data = {
        "session_id": session_id,
        "summary": "This was a productive meeting.",
        "key_points": ["Point 1", "Point 2"],
        "action_items": ["Action 1"],
    }
    path = conversation_store.save_summary(session_id, summary_data)
    assert path.exists()

    loaded = conversation_store.read_summary_file(session_id)
    assert loaded is not None
    assert loaded["summary"] == "This was a productive meeting."
    assert loaded["key_points"] == ["Point 1", "Point 2"]


def test_save_turn_audio(conversation_store: SessionConversationStore) -> None:
    session_id = "session_audio_test"
    dummy_pcm = b"\x00\x00" * 16000  # 1 second of 16kHz silence
    filename = conversation_store.save_turn_audio(
        session_id=session_id,
        turn_index=0,
        channel_key="ch1",
        raw_pcm_s16le=dummy_pcm,
        sample_rate=16000,
    )
    assert filename.startswith("turn_000_ch1.wav")
    wav_path = conversation_store.audio_dir(session_id) / filename
    assert wav_path.exists()
    assert wav_path.stat().st_size > 32000  # PCM bytes + WAV header
