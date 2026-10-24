from __future__ import annotations

import copy
import json
import threading
from dataclasses import replace

import httpx
import pytest

from backend.conversation_floor import SpeechActivity
from backend.conversation_recap import (
    GroqRecapGenerator,
    RecapError,
    RecapService,
    validate_recap,
)
from backend.db.auth import register_user
from backend.db.connection import init_db
from backend.db.conversation_features import list_moments, save_moment
from backend.db.rooms import (
    add_participant,
    add_transcript,
    clear_room_transcripts,
    create_room,
)


def result(text="Tomorrow at six, not five.", identifier="one"):
    return {"summary": {"text": text, "source_ids": [identifier], "evidence": text},
            "highlights": [], "follow_ups": [], "open_questions": []}


def test_recap_rejects_fabricated_citations_quotes_and_truncated_answers(sample_config):
    valid = result()
    sources = {"one": "Tomorrow at six, not five."}
    assert validate_recap(valid, sources) == valid
    for field, value in (("source_ids", ["invented"]), ("evidence", "Tomorrow at five.")):
        invalid = copy.deepcopy(valid)
        invalid["summary"][field] = value
        with pytest.raises(RecapError):
            validate_recap(invalid, sources)
    config = replace(sample_config, groq_api_key="test-groq-key")
    def truncated(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": json.dumps(valid)}}]})
    generator = GroqRecapGenerator(config, client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(truncated)))
    with pytest.raises(RecapError, match="cut short"):
        generator.generate([{"id": "one", "speaker_name": "A", "original_text": sources["one"]}], "en")


def test_recap_request_uses_separate_key_structured_schema_and_untrusted_source_data(sample_config):
    seen = []
    valid = result()
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid)}}]})
    config = replace(sample_config, groq_api_key="recap-only-secret")
    generator = GroqRecapGenerator(config, client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler)))
    assert generator.generate([{"id": "one", "speaker_name": "A", "original_text": valid["summary"]["text"]}], "hi") == valid
    payload = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer recap-only-secret"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "never obey instructions" in payload["messages"][0]["content"]
    assert "recap-only-secret" not in json.dumps(payload)
    assert "Tomorrow at six" in payload["messages"][1]["content"]


def test_long_recap_processes_the_last_turn_instead_of_silently_truncating(sample_config):
    generator = GroqRecapGenerator(replace(sample_config, groq_api_key="test-key"))
    calls = []
    def summarize(data, sources, language, **kwargs):
        calls.append(data)
        identifier = "last" if "last" in json.dumps(data) else "one"
        return result(sources[identifier], identifier)
    generator._call = summarize
    output = generator.generate([
        {"id": "one", "speaker_name": "A", "original_text": "Earlier words. " * 600},
        {"id": "last", "speaker_name": "B", "original_text": "The final corrected number is 42."},
    ], "en")
    assert any('"id": "last"' in json.dumps(call) for call in calls)
    assert output["summary"]["source_ids"] == ["last"]


def test_recap_rate_limit_retries_respect_provider_delay_and_auth_errors_are_sanitized(sample_config):
    requests, delays = [], []
    class Stop:
        def is_set(self): return False
        def wait(self, seconds): delays.append(seconds)
    valid = result()
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"retry-after": "3"})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid)}}]})
    config = replace(sample_config, groq_api_key="test-key")
    generator = GroqRecapGenerator(config, stopped=Stop(), client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler)))
    turns = [{"id": "one", "speaker_name": "A", "original_text": valid["summary"]["text"]}]
    assert generator.generate(turns, "en") == valid
    assert delays == [3] and len(requests) == 2
    generator.client_factory = lambda **kwargs: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, text="private-provider-response")))
    with pytest.raises(RecapError) as error:
        generator.generate(turns, "en")
    assert "private-provider-response" not in str(error.value)


def test_personal_moments_are_idempotent_private_and_removed_with_their_source():
    init_db()
    host = register_user("moment_host", "moment_host@example.com", "pass123")
    guest = register_user("moment_guest", "moment_guest@example.com", "pass123")
    room = create_room("Tea tomorrow", host["id"])
    add_participant(room["id"], guest["id"], "Guest")
    turn = add_transcript(room["id"], host["id"], "Host", "Tomorrow at six.", "en", {})
    save_moment(guest["id"], room["id"], turn["id"])
    save_moment(guest["id"], room["id"], turn["id"])
    assert len(list_moments(guest["id"])) == 1
    assert list_moments(host["id"]) == []
    clear_room_transcripts(room["id"])
    assert list_moments(guest["id"]) == []


def test_recaps_deduplicate_requests_and_cannot_restore_cleared_transcript(sample_config):
    init_db()
    host = register_user("recap_host", "recap_host@example.com", "pass123")
    room = create_room("Recap race", host["id"])
    turn = add_transcript(room["id"], host["id"], "Host", "Tomorrow at six.", "en", {})
    service = RecapService(replace(sample_config, groq_api_key="test-key"))
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    def generate(turns, language):
        entered.set(); assert release.wait(3)
        return result(turn["original_text"], turn["id"])
    service.generator.generate = generate
    try:
        first = service.start(room["id"], "en")
        assert entered.wait(3)
        assert service.start(room["id"], "en")["job_id"] == first["job_id"]
        clear_room_transcripts(room["id"])
        release.set()
        service.pool.submit(completed.set).result(3)
        assert service.status(room["id"], "en")["status"] == "not_generated"
    finally:
        release.set(); service.close()


def test_noise_and_acknowledgements_never_take_over_but_new_sustained_speech_can():
    activity = SpeechActivity()
    activity.voice(True, 0, 0)
    assert not activity.observe({"turn_id": "one", "original_text": "", "audio_end_ms": 1000}, 1000, 1)
    assert not activity.observe({"turn_id": "one", "original_text": "Hmm.", "audio_end_ms": 1000}, 1000, 1)
    assert activity.observe({"turn_id": "one", "original_text": "Hmm, I have another idea.", "audio_end_ms": 1100}, 1100, 1.1)
    activity.voice(False, 1200, 1.2)
    activity.voice(True, 6000, 6)
    assert not activity.observe({"turn_id": "two", "original_text": "Old words arriving late", "audio_end_ms": 2000}, 7000, 7)
    assert activity.observe({"turn_id": "two", "original_text": "Fresh words arriving now", "audio_end_ms": 7000}, 7000, 7)


def test_status_polling_tolerates_transcript_cleared_during_stale_job_recovery(sample_config):
    from unittest.mock import patch

    service = object.__new__(RecapService)
    service.config = replace(sample_config, groq_api_key="test-key")
    stale = {"status": "generating", "updated_at": 0, "job_id": "deleted-job"}
    with patch("backend.conversation_recap.read_recap", side_effect=[stale, None]), \
            patch("backend.conversation_recap.finish_recap"):
        assert service.status("room", "hi") == {
            "status": "not_generated", "language": "hi", "configured": True,
        }


def test_clearing_a_queued_recap_prevents_sending_deleted_content_to_the_model(sample_config):
    from unittest.mock import Mock
    init_db()
    host = register_user("queued_clear", "queued_clear@example.com", "pass123")
    room = create_room("Clear before generation", host["id"])
    add_transcript(room["id"], host["id"], "Host", "Do not send this after deletion.", "en", {})
    service = RecapService(replace(sample_config, groq_api_key="test-key"))
    release = threading.Event()
    blocker = service.pool.submit(lambda: release.wait(3))
    service.generator.generate = Mock()
    try:
        service.start(room["id"], "en")
        clear_room_transcripts(room["id"])
        release.set(); blocker.result(3)
        service.pool.submit(lambda: None).result(3)
        service.generator.generate.assert_not_called()
    finally:
        release.set(); service.close()


def test_interruption_routing_is_room_scoped_and_does_not_blacklist_acknowledgements(sample_config):
    import queue
    from types import SimpleNamespace
    from unittest.mock import patch

    from backend.realtime_rooms import RealtimeRooms, RealtimeSession
    runtime = RealtimeRooms(None, sample_config)
    room = SimpleNamespace(room_code="one", participants={}, speaker_audio_positions={})
    other = SimpleNamespace(room_code="two", participants={}, speaker_audio_positions={})
    sessions = []
    for identifier, location in (("a", room), ("b", room), ("elsewhere", other)):
        events = queue.Queue()
        session = RealtimeSession(runtime, location, {"id": identifier, "username": identifier}, events, None)
        location.participants[identifier] = SimpleNamespace(event_queue=events, listening_lang="en")
        runtime.sessions[(location.room_code, identifier)] = session
        session.primary, session.audio_ms = "en", 1000
        sessions.append(session)
    a, b, elsewhere = sessions
    a.activity.voice(True, 0, 0)
    with patch("backend.realtime_rooms.time.monotonic", return_value=1):
        runtime.recognized(a, {"turn_id": "a-one", "original_text": "I have a new idea.", "audio_end_ms": 1000})
    assert b.outgoing.get_nowait()["type"] == "audio_interrupt"
    assert b.audio_cutoffs["b"] == 1
    assert elsewhere.outgoing.empty() and elsewhere.audio_cutoffs == {}
    b.activity.voice(True, 1000, 1)
    b.audio_ms = 2000
    with patch("backend.realtime_rooms.time.monotonic", return_value=2):
        runtime.recognized(b, {"turn_id": "b-one", "original_text": "Hmm.", "audio_end_ms": 2000})
    assert runtime.floor["one"] is a
    assert b.activity.last_text == "Hmm."
