"""Unit tests for the RoomCoordinator and multi-party fan-out translation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.config import AppConfig
from backend.db.auth import register_user
from backend.db.connection import init_db
from backend.db.rooms import create_room
from backend.rooms.coordinator import RoomCoordinator


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point database to isolated test SQLite path."""
    test_db_path = tmp_path / "test_kinesia.db"
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("backend.db.connection._get_sqlite_path", lambda: test_db_path)
    init_db()


def test_room_coordinator_fanout_translation(sample_config: AppConfig) -> None:
    coordinator = RoomCoordinator(sample_config)

    # Register 2 users
    user_a = register_user("piyush", "p@example.com", "pass123", display_name="Piyush", default_input_lang="en", default_output_lang="mr")
    user_b = register_user("tanvi", "t@example.com", "pass123", display_name="Tanvi", default_input_lang="mr", default_output_lang="en")

    # Create Room
    room_record = create_room("Kinesia Launch Dialogue", user_a["id"], passcode=None)
    room_code = room_record["room_code"]

    # Both join
    room_sess, queue_a = coordinator.join_room(room_code, user_a, speaking_lang="en", listening_lang="en")
    _, queue_b = coordinator.join_room(room_code, user_b, speaking_lang="mr", listening_lang="mr")

    assert len(room_sess.participants) == 2

    # User A speaks in English. User B listens in Marathi.
    # Mock Gemma translation to Marathi and TTS synthesis
    with patch.object(coordinator.gemma, "translate", return_value="नमस्कार, आपण प्रणालीची चाचणी करत आहोत."), \
         patch.object(coordinator.tts, "synthesize_for_language", return_value=b"RIFFdummywav"):

        turn = coordinator.process_utterance(
            room_code=room_code,
            speaker_id=user_a["id"],
            speaker_name="Piyush",
            text="Hello, we are testing the system.",
            source_lang="en",
            confidence=0.96,
        )

        assert turn["original_text"] == "Hello, we are testing the system."
        assert "mr" in turn["translations"]
        assert turn["translations"]["mr"] == "नमस्कार, आपण प्रणालीची चाचणी करत आहोत."

        # Verify User B received localized Marathi translation in their queue
        # First event in queue is participant_joined (for user_b), second is speaking, third is turn
        events = []
        while not queue_b.empty():
            events.append(queue_b.get_nowait())

        turn_events = [e for e in events if e["type"] == "turn"]
        assert len(turn_events) == 1
        b_turn_data = turn_events[0]["data"]
        assert b_turn_data["localized_text"] == "नमस्कार, आपण प्रणालीची चाचणी करत आहोत."
        assert b_turn_data["audio_data_url"] is not None
        assert b_turn_data["audio_data_url"].startswith("data:audio/wav;base64,")

    # Conclude session
    with patch.object(coordinator.summarizer, "summarize") as mock_summarize:
        mock_summary = MagicMock()
        mock_summary.executive_summary = "Discussion about platform readiness."
        mock_summary.action_items = ["Deploy to Vercel"]
        mock_summary.key_points = ["Marathi support functional"]
        mock_summarize.return_value = mock_summary

        insights = coordinator.conclude_session(room_code)
        assert insights["executive_summary"] == "Discussion about platform readiness."
        assert "Deploy to Vercel" in insights["action_items"]
