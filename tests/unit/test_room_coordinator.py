"""Unit tests for the RoomCoordinator and multi-party fan-out translation."""

from __future__ import annotations

from pathlib import Path
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from backend.config import AppConfig
from backend.db.auth import register_user
from backend.db.connection import init_db
from backend.db.rooms import create_room
from backend.rooms.coordinator import RoomCoordinator
from backend.db.rooms import get_room_transcripts, list_active_participants
from backend.soniox_client import SonioxTranscriptionResult
from backend.realtime_providers import SpeechSegment
from backend.dashboard_server import DashboardRequestHandler
from backend.db.auth import create_token
from types import SimpleNamespace


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


@pytest.fixture
def room_pipeline(sample_config):
    with patch("backend.rooms.coordinator.EntityExtractor", return_value=None):
        coordinator = RoomCoordinator(sample_config)
    a = register_user("speaker", "speaker@example.com", "pass123")
    b = register_user("listener", "listener@example.com", "pass123")
    record = create_room("Regression room", a["id"])
    room, qa = coordinator.join_room(record["room_code"], a, "en", "en")
    _, qb = coordinator.join_room(record["room_code"], b, "hi", "hi")
    coordinator.gemma.translate = MagicMock(return_value="नमस्ते")
    coordinator.tts.synthesize_for_language = MagicMock(return_value=b"RIFFaudio")
    return coordinator, room, a, b, qa, qb


def turns(events):
    result = []
    while True:
        try:
            event = events.get_nowait()
            if event["type"] == "turn":
                result.append(event["data"])
        except queue.Empty:
            return result


def test_repeated_words_prefixes_and_consecutive_utterances_are_preserved(room_pipeline):
    coordinator, room, a, _, _, qb = room_pipeline
    texts = ["very very good", "I had had lunch", "book", "booking", "नहीं नहीं", "Hello", "Hello"]
    for text in texts:
        result = coordinator.process_utterance(room.room_code, a["id"], "Speaker", text, "en")
        assert result["original_text"] == text
    assert [turn["original_text"] for turn in turns(qb)] == texts


def test_duplicate_audio_request_only_transcribes_and_synthesizes_once(room_pipeline):
    coordinator, room, a, _, _, qb = room_pipeline
    coordinator.soniox.transcribe_pcm16 = MagicMock(return_value=SonioxTranscriptionResult("Hello", "en", 0.9))
    with patch.object(coordinator, "_extract_pcm16le", return_value=(b"pcm", 16000, 1)):
        def submit():
            return coordinator.process_audio_utterance(room.room_code, a["id"], "Speaker", b"wav", "en", utterance_id="same-request")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0]["id"] == results[1]["id"]
    assert coordinator.soniox.transcribe_pcm16.call_count == 1
    assert coordinator.tts.synthesize_for_language.call_count == 1
    assert len(turns(qb)) == 1
    assert len(get_room_transcripts(room.room_id)) == 1


def test_unrecorded_turn_ids_are_unique_even_in_same_millisecond(room_pipeline):
    coordinator, room, a, _, _, qb = room_pipeline
    room.record_session = False
    with patch("backend.rooms.coordinator.time.time", return_value=123.0):
        for _ in range(2):
            coordinator.process_utterance(room.room_code, a["id"], "Speaker", "Hello", "en")
    events = turns(qb)
    assert len(events) == 2
    assert events[0]["turn_id"] != events[1]["turn_id"]
    assert get_room_transcripts(room.room_id) == []


def test_old_sse_cleanup_cannot_remove_new_subscription(room_pipeline):
    coordinator, room, a, _, qa, qb = room_pipeline
    coordinator.update_participant_status(room.room_code, a["id"], speaking_lang="mr", listening_lang="te", is_muted=True)
    _, replacement = coordinator.join_room(room.room_code, a, "en", "hi")
    coordinator.leave_room(room.room_code, a["id"], event_queue=qa)
    participant = room.participants[a["id"]]
    assert participant.event_queue is replacement
    assert participant.speaking_lang == "mr"
    assert participant.listening_lang == "te"
    assert participant.is_muted is True
    assert len(list_active_participants(room.room_id)) == 2
    coordinator.leave_room(room.room_code, a["id"], event_queue=replacement)
    assert a["id"] not in room.participants
    assert len(list_active_participants(room.room_id)) == 1


def test_fanout_runs_distinct_languages_concurrently_and_skips_speaker_tts(room_pipeline):
    coordinator, room, a, _, _, qb = room_pipeline
    c = register_user("listener3", "third@example.com", "pass123")
    _, qc = coordinator.join_room(room.room_code, c, "mr", "mr")
    barrier = threading.Barrier(2)
    def translate(*args):
        barrier.wait(timeout=3)  # Fails deterministically if languages are serialized.
        return "translated-" + args[4]
    coordinator.gemma.translate.side_effect = translate
    coordinator.process_utterance(room.room_code, a["id"], "Speaker", "Hello", "en")
    assert turns(qb)[0]["localized_text"] == "translated-hi"
    assert turns(qc)[0]["localized_text"] == "translated-mr"
    assert {call.args[2] for call in coordinator.tts.synthesize_for_language.call_args_list} == {"hi", "mr"}


def test_room_turn_order_is_stable_and_other_rooms_are_independent(room_pipeline):
    coordinator, room, a, _, _, qb = room_pipeline
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    def translate(text, *args):
        if text == "first":
            entered.set()
            assert release.wait(3)
        return text
    coordinator.gemma.translate.side_effect = translate
    other_record = create_room("Independent room", a["id"])
    other, _ = coordinator.join_room(other_record["room_code"], a, "en", "en")
    def submit(text):
        if text == "second":
            second_started.set()
        return coordinator.process_utterance(room.room_code, a["id"], "Speaker", text, "en")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, "first")
        assert entered.wait(3)
        second = pool.submit(submit, "second")
        assert second_started.wait(3)
        coordinator.process_utterance(other.room_code, a["id"], "Speaker", "independent", "en")
        release.set()
        first.result(3); second.result(3)
    assert [turn["original_text"] for turn in turns(qb)] == ["first", "second"]


def test_end_session_waits_for_current_turn_and_rejects_new_work(room_pipeline):
    coordinator, room, a, _, _, qb = room_pipeline
    entered, release = threading.Event(), threading.Event()
    def translate(*args):
        entered.set()
        assert release.wait(3)
        return "नमस्ते"
    coordinator.gemma.translate.side_effect = translate
    coordinator.summarizer.summarize = MagicMock(return_value=SimpleNamespace(
        executive_summary="One turn", action_items=[], key_points=[]
    ))
    with ThreadPoolExecutor(max_workers=2) as pool:
        utterance = pool.submit(coordinator.process_utterance, room.room_code, a["id"], "Speaker", "Hello", "en")
        assert entered.wait(3)
        # Observe the exact admission boundary, without timing-dependent sleeps.
        with coordinator._lock:
            ending = pool.submit(coordinator.conclude_session, room.room_code)
        for _ in range(1000):
            if room.ending:
                break
            threading.Event().wait(0.001)
        assert room.ending
        with pytest.raises(ValueError, match="ended"):
            coordinator.process_utterance(room.room_code, a["id"], "Speaker", "late", "en")
        release.set()
        utterance.result(3)
        ending.result(3)
    assert room.room_code not in coordinator.active_rooms
    assert len(get_room_transcripts(room.room_id)) == 1
    assert len(coordinator.summarizer.summarize.call_args.args[1]) == 1
    assert qb.get_nowait()["type"] == "session_concluded"


def test_sse_initial_write_failure_cleans_up_exact_subscription(room_pipeline):
    coordinator, room, a, _, _, _ = room_pipeline
    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    handler.state = SimpleNamespace(coordinator=coordinator)
    handler._stream_room_events = MagicMock(side_effect=BrokenPipeError)
    handler.send_error = MagicMock()
    token = create_token(a["id"], a["username"], a["email"])
    handler._serve_room_events(room.room_code, token, "en", "en")
    assert a["id"] not in room.participants
    assert handler.close_connection is True


def test_sse_logs_redact_query_bearer_token(caplog):
    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    with caplog.at_level("DEBUG", logger="backend.dashboard_server"):
        handler.log_message('"%s" %s', "GET /events/rooms/example?token=private-token&speaking_lang=en HTTP/1.1", 200)
    assert "private-token" not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_session_end_displaces_a_full_audio_queue(room_pipeline):
    coordinator, room, _, _, _, qb = room_pipeline
    while not qb.empty():
        qb.get_nowait()
    for _ in range(qb.maxsize):
        qb.put_nowait({"type": "turn", "data": {"audio_data_url": "stale"}})
    room.record_session = False
    coordinator.conclude_session(room.room_code)
    assert qb.get_nowait()["type"] == "session_concluded"
    assert qb.get_nowait()["type"] == "session_ended"
    assert qb.empty()


def test_http_error_logging_preserves_numeric_format_arguments(caplog):
    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    with caplog.at_level("DEBUG", logger="backend.dashboard_server"):
        handler.log_message("code %d, message %s", 400, "Bad Request")
    assert "code 400, message Bad Request" in caplog.text


def test_failed_translation_never_speaks_source_text_in_target_voice(room_pipeline):
    coordinator, room, a, _, _, qb = room_pipeline
    coordinator.gemma.translate.side_effect = RuntimeError("provider unavailable")
    coordinator.process_utterance(room.room_code, a["id"], "Speaker", "Hello", "en")
    event = turns(qb)[0]
    assert event["original_text"] == "Hello"
    assert event["localized_text"] == ""
    assert event["audio_data_url"] is None
    coordinator.tts.synthesize_for_language.assert_not_called()


def test_undecodable_container_is_never_mislabeled_as_raw_pcm():
    with patch.dict("sys.modules", {"pydub": None}):
        with pytest.raises(ValueError, match="Unable to decode"):
            RoomCoordinator._extract_pcm16le(b"not-a-wav", "audio/wav")
    with pytest.raises(ValueError, match="complete samples"):
        RoomCoordinator._extract_pcm16le(b"odd", "audio/pcm")


def test_parallel_stream_languages_merge_by_time_without_removing_repeated_speech(room_pipeline):
    coordinator, room, a, _, _, qb = room_pipeline
    coordinator.record_stream_segment(room.room_code, a["id"], "Speaker", SpeechSegment(
        "provider-hi", "very very good", "बहुत अच्छा", "en", "hi", 100, 1100, 0.9))
    coordinator.record_stream_segment(room.room_code, a["id"], "Speaker", SpeechSegment(
        "provider-mr", "very very good", "खूप छान", "en", "mr", 150, 1150, 0.9))
    coordinator.record_stream_segment(room.room_code, a["id"], "Speaker", SpeechSegment(
        "provider-repeat", "very very good", "बहुत अच्छा", "en", "hi", 1800, 2800, 0.9))
    history = get_room_transcripts(room.room_id)
    assert len(history) == 2
    assert {turn["original_text"] for turn in history} == {"very very good"}
    merged = next(turn for turn in history if "mr" in turn["translations"])
    assert merged["translations"]["hi"] == "बहुत अच्छा"
    assert merged["translations"]["mr"] == "खूप छान"
    events = turns(qb)
    assert [event["revision"] for event in events] == [1, 2, 1]
    assert "mr" not in events[0]["translations"]  # Earlier events retain their own snapshot.
