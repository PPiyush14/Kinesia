from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.realtime_providers import SpeechSegment
from backend.realtime_server import create_app


class FakeSoniox:
    def __init__(self, config, source, target, offset, caption, phrase, final, error):
        self.source, self.target, self.offset = source, target, offset
        self.caption, self.phrase, self.final, self.error = caption, phrase, final, error
        self.closed = False
        self.once = False
    def start(self): pass
    def feed(self, data):
        if self.once: return
        self.once = True
        translated = "नमस्ते" if self.target == "hi" else "Hello"
        self.caption({"turn_id": "partial", "original_text": "Hello", "localized_text": translated, "source_lang": "en"})
        self.phrase(translated, "utterance")
        asyncio.create_task(self.final(SpeechSegment("provider-turn", "Hello", translated, "en", self.target, self.offset, self.offset + 500, 0.95)))
    async def flush(self): pass
    async def close(self): self.closed = True


class FakeCartesia:
    def __init__(self, config): pass
    async def synthesize(self, text, language, context_id, chunk, **kwargs):
        await chunk(b"\0\0" * 100, 0, 24000)
    async def finish_context(self, *args): pass
    async def cancel_context(self, *args): pass
    async def close(self): pass


@pytest.fixture
def realtime_client(sample_config):
    config = replace(sample_config, translation_provider="soniox", tts_provider="cartesia", groq_api_key="test-recap-key")
    with patch("backend.rooms.coordinator.EntityExtractor", return_value=None), \
         patch("backend.dashboard_server.EntityExtractor", return_value=None), \
         patch("backend.realtime_rooms.SonioxStream", FakeSoniox), \
         patch("backend.realtime_rooms.CartesiaStream", FakeCartesia), \
         TestClient(create_app(config)) as client:
        yield client


def user(client, name):
    assert client.post("/api/v1/auth/register", json={"username": name, "email": name + "@example.com", "password": "pass123"}).status_code == 200
    data = client.post("/api/v1/auth/login", json={"login": name, "password": "pass123"}).json()["data"]
    return data["token"], {"Authorization": "Bearer " + data["token"]}


def receive_event(socket, name, limit=40):
    for _ in range(limit):
        event = socket.receive()
        if event.get("type") == "websocket.close":
            raise AssertionError(f"Socket closed before {name}: {event.get('code')}")
        if event.get("text"):
            parsed = json.loads(event["text"])
            if parsed.get("type") == name:
                return parsed.get("data")
    raise AssertionError(f"No {name} event")


def authenticate(socket, token, listening="en"):
    socket.send_json({"type": "authenticate", "token": token, "speaking_lang": "en", "listening_lang": listening})
    return receive_event(socket, "connected")


def test_health_and_rest_contracts_survive_asgi_adapter(realtime_client):
    client = realtime_client
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["data"]["transport"] == "websocket"
    assert "mock_soniox_key" not in health.text
    assert client.get("/api/v1/settings").status_code == 404
    _, headers = user(client, "health_user")
    assert client.get("/api/v1/auth/me", headers=headers).json()["data"]["username"] == "health_user"


def test_websocket_requires_authenticated_first_message(realtime_client):
    with realtime_client.websocket_connect("/ws/rooms/example") as socket:
        socket.send_json({"type": "authenticate", "token": "not-valid"})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4401


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/v1/auth/login", "[]"),
    ("POST", "/api/v1/auth/register", '{"username": ["wrong"], "email":"a@b.co", "password":"pass123"}'),
    ("PATCH", "/api/v1/auth/profile", "{broken"),
    ("PATCH", "/api/v1/auth/profile", "[]"),
    ("PATCH", "/api/v1/auth/profile", '{"display_name": 123}'),
    ("POST", "/api/v1/rooms", '{"title":"Private", "record_session":"false"}'),
])
def test_invalid_request_bodies_return_client_errors_and_leave_the_service_healthy(realtime_client, method, path, body):
    _, headers = user(realtime_client, "invalid_request_user")
    response = realtime_client.request(method, path, content=body, headers=dict(headers, **{"Content-Type": "application/json"}))
    assert response.status_code == 400
    assert realtime_client.get("/api/v1/health").status_code == 200


def test_passcode_verification_does_not_show_a_disconnected_guest_as_present(realtime_client):
    client = realtime_client
    _, headers = user(client, "presence_host")
    _, guest = user(client, "presence_guest")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Private", "passcode": "pass123"}).json()["data"]["room_code"]
    assert client.post(f"/api/v1/rooms/{code}/verify", headers=guest, json={"passcode": "pass123"}).json()["data"]["valid"]
    assert client.get(f"/api/v1/rooms/{code}/participants", headers=headers).json()["data"] == []


def test_room_payload_validation_rejects_bad_fields_before_provider_work(realtime_client):
    client = realtime_client
    _, headers = user(client, "room_fields")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Input validation", "passcode": "private-code"}).json()["data"]["room_code"]
    for endpoint, body in (
        ("verify", {"passcode": {"wrong": True}}),
        ("speak", {"text": [], "source_lang": "en"}),
        ("speak", {"text": "hello", "source_lang": "en", "confidence": []}),
        ("speak", {"text": "hello", "source_lang": "unsupported"}),
        ("languages", {"speaking_lang": []}),
        ("languages", {"is_muted": "false"}),
    ):
        response = client.post(f"/api/v1/rooms/{code}/{endpoint}", headers=headers, json=body)
        assert response.status_code == 400, (endpoint, response.status_code)


def test_private_room_cannot_be_joined_without_passcode_grant(realtime_client):
    client = realtime_client
    _, headers = user(client, "private_host")
    guest, guest_headers = user(client, "private_guest")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Private", "passcode": "private-code"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        socket.send_json({"type": "authenticate", "token": guest})
        assert receive_event(socket, "pipeline_error")["provider"] == "room"
    assert client.post(f"/api/v1/rooms/{code}/verify", headers=guest_headers, json={"passcode": "private-code"}).json()["data"]["valid"]
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        assert authenticate(socket, guest)["provider"] == "soniox"


def test_audio_streams_to_peer_once_and_finalized_translations_merge_in_db(realtime_client):
    client = realtime_client
    host, headers = user(client, "audio_host")
    guest, _ = user(client, "audio_guest")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Audio room"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as speaker:
        authenticate(speaker, host)
        with client.websocket_connect(f"/ws/rooms/{code}") as listener:
            authenticate(listener, guest, "hi")
            speaker.send_bytes(b"\0\0" * 1600)
            started = receive_event(listener, "audio_start")
            assert started["language"] == "hi" and started["sample_rate"] == 24000
            for _ in range(30):
                packet = listener.receive()
                if packet.get("bytes"):
                    assert packet["bytes"][:4] == b"KNS1"
                    assert packet["bytes"][24:] == b"\0\0" * 100
                    break
            else: pytest.fail("No PCM audio delivered")
            receive_event(listener, "audio_end")
            finalized = receive_event(listener, "turn")
            assert finalized["localized_text"] == "नमस्ते"
            history = client.get(f"/api/v1/rooms/{code}/transcripts", headers=headers).json()["data"]
            assert len(history) == 1
            assert history[0]["translations"]["hi"] == "नमस्ते"
            ended = client.post(f"/api/v1/rooms/{code}/end", json={}, headers=headers)
            assert ended.status_code == 200 and ended.json()["data"]["summary_method"] == "extractive"
            assert receive_event(listener, "session_concluded")["summary_method"] == "extractive"


def test_odd_sized_microphone_frame_is_rejected(realtime_client):
    client = realtime_client
    token, headers = user(client, "bad_audio")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Room"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        authenticate(socket, token)
        socket.send_bytes(b"odd")
        assert "PCM16" in receive_event(socket, "pipeline_error")["message"]


def test_microphone_mute_does_not_reset_incoming_audio(realtime_client):
    client = realtime_client
    token, headers = user(client, "mute_preserves_listening")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Keep listening"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        authenticate(socket, token)
        socket.send_json({"type": "configure", "is_muted": True})
        socket.send_json({"type": "ping", "data": "configured"})
        messages = []
        while True:
            message = socket.receive_json()
            messages.append(message["type"])
            if message["type"] == "pong":
                break
        assert "audio_reset" not in messages


def test_mute_flushes_pending_words_to_transcript_and_peer_audio(realtime_client):
    import threading

    flushed, spoken = threading.Event(), threading.Event()
    class PendingSoniox(FakeSoniox):
        def feed(self, data): self.once = True
        async def flush(self):
            if not self.once or self.closed:
                return
            self.phrase("The final words.", "pending-turn")
            self.on_boundary("pending-turn")
            await self.final(SpeechSegment("pending-turn", "The final words.", "The final words.",
                                           "en", self.target, self.offset, self.offset + 500, 0.95))
            self.closed = True
            flushed.set()
    class ObservedCartesia(FakeCartesia):
        async def synthesize(self, *args, **kwargs):
            await super().synthesize(*args, **kwargs)
            spoken.set()
    client = realtime_client
    host, headers = user(client, "pending_words_host")
    guest, _ = user(client, "pending_words_guest")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Pending words"}).json()["data"]["room_code"]
    with patch("backend.realtime_rooms.SonioxStream", PendingSoniox), patch("backend.realtime_rooms.CartesiaStream", ObservedCartesia), client.websocket_connect(f"/ws/rooms/{code}") as speaker, client.websocket_connect(f"/ws/rooms/{code}") as listener:
        authenticate(speaker, host)
        authenticate(listener, guest, "en")
        speaker.send_bytes(b"\0\0" * 1600)
        speaker.send_json({"type": "configure", "is_muted": True})
        assert flushed.wait(2), "Mute discarded the unfinalized STT stream"
        assert spoken.wait(2), "Mute discarded the final translated speech"
        history = client.get(f"/api/v1/rooms/{code}/transcripts", headers=headers).json()["data"]
        assert history[-1]["original_text"] == "The final words."


def test_new_subscription_survives_old_socket_cleanup(realtime_client):
    client = realtime_client
    token, headers = user(client, "reconnecting")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Reconnect"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as old:
        authenticate(old, token)
        with client.websocket_connect(f"/ws/rooms/{code}") as current:
            authenticate(current, token, "hi")
            participants = client.get(f"/api/v1/rooms/{code}/participants", headers=headers).json()["data"]
            assert len(participants) == 1
            assert participants[0]["listening_lang"] == "hi"
            current.send_json({"type": "ping", "data": "still-here"})
            assert receive_event(current, "pong") == "still-here"


def test_simultaneous_joins_cannot_exceed_the_room_participant_limit(realtime_client):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack

    client = realtime_client
    accounts = [user(client, f"capacity_{index}") for index in range(9)]
    code = client.post("/api/v1/rooms", headers=accounts[0][1], json={"title": "Capacity"}).json()["data"]["room_code"]
    coordinator = client.app.state.dashboard.coordinator
    original_join = coordinator.join_room
    barrier, finished, release = threading.Barrier(2), threading.Event(), threading.Event()
    outcomes, guard = [], threading.Lock()
    def delayed_join(room_code, profile, *args):
        if profile["username"] in {"capacity_7", "capacity_8"}:
            try:
                barrier.wait(0.3)
            except threading.BrokenBarrierError:
                pass
        return original_join(room_code, profile, *args)
    def join(token):
        with client.websocket_connect(f"/ws/rooms/{code}") as socket:
            socket.send_json({"type": "authenticate", "token": token, "speaking_lang": "en", "listening_lang": "hi"})
            while True:
                event = socket.receive_json()
                if event["type"] in {"connected", "pipeline_error"}:
                    with guard:
                        outcomes.append(event["type"])
                        if len(outcomes) == 2:
                            finished.set()
                    break
            release.wait(5)
    with ExitStack() as stack, ThreadPoolExecutor(max_workers=2) as pool:
        for token, _ in accounts[:7]:
            authenticate(stack.enter_context(client.websocket_connect(f"/ws/rooms/{code}")), token)
        with patch.object(coordinator, "join_room", side_effect=delayed_join):
            futures = [pool.submit(join, token) for token, _ in accounts[7:]]
            try:
                assert finished.wait(3)
                assert outcomes.count("connected") == 1
                assert outcomes.count("pipeline_error") == 1
            finally:
                release.set()
                for future in futures:
                    future.result(3)


def test_websocket_control_array_is_a_client_error_not_a_reconnect_loop(realtime_client):
    client = realtime_client
    token, headers = user(client, "invalid_control")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Control validation"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        authenticate(socket, token)
        socket.send_json([])
        assert "JSON object" in receive_event(socket, "pipeline_error")["message"]


def test_speaking_stop_is_not_lost_when_it_follows_start_immediately(realtime_client):
    client = realtime_client
    token, headers = user(client, "short_speech_activity")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Short acknowledgement"}).json()["data"]["room_code"]
    coordinator = client.app.state.dashboard.coordinator
    with patch.object(coordinator, "broadcast_event", wraps=coordinator.broadcast_event) as broadcast, \
         client.websocket_connect(f"/ws/rooms/{code}") as socket:
        authenticate(socket, token)
        socket.send_json({"type": "speaking", "active": True})
        socket.send_json({"type": "speaking", "active": False})
        socket.send_json({"type": "ping", "data": "activity-complete"})
        while True:
            event = socket.receive_json()
            if event["type"] == "pong":
                break
        # Pong acknowledges control processing. The separate event bridge may
        # deliver broadcasts later, so assert production of both transitions.
        activity = [call.args[2]["is_speaking"] for call in broadcast.call_args_list if call.args[1] == "speaking"]
        assert activity == [True, False]


def test_history_removal_does_not_delete_shared_rooms_and_account_route_still_works(realtime_client):
    client = realtime_client
    _, headers = user(client, "delete_host")
    _, other_headers = user(client, "delete_guest")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Delete test"}).json()["data"]["room_code"]
    assert client.delete(f"/api/v1/rooms/{code}", headers=other_headers).status_code == 403
    assert client.get(f"/api/v1/rooms/{code}").status_code == 200
    assert client.delete(f"/api/v1/rooms/{code}/transcripts", headers=other_headers).status_code == 403
    assert client.delete(f"/api/v1/rooms/{code}/transcripts", headers=headers).status_code == 200
    assert client.get(f"/api/v1/rooms/{code}").status_code == 200
    assert client.delete(f"/api/v1/rooms/{code}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/rooms/{code}").status_code == 200
    assert client.get("/api/v1/rooms", headers=headers).json()["data"] == []
    assert client.delete("/api/v1/auth/profile", headers=other_headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=other_headers).status_code == 401


@pytest.mark.parametrize("record_session", [True, False])
def test_ending_an_already_ended_room_is_safe_to_retry(realtime_client, record_session):
    client = realtime_client
    _, headers = user(client, "retry_end")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "End twice", "record_session": record_session}).json()["data"]["room_code"]
    first = client.post(f"/api/v1/rooms/{code}/end", headers=headers, json={})
    second = client.post(f"/api/v1/rooms/{code}/end", headers=headers, json={})
    assert first.status_code == second.status_code == 200
    assert second.json()["data"]["executive_summary"] == first.json()["data"]["executive_summary"]


def test_account_deletion_revokes_live_guest_presence_in_other_peoples_rooms(realtime_client):
    client = realtime_client
    host, headers = user(client, "account_host")
    guest, guest_headers = user(client, "account_guest")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Guest account"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as speaker, client.websocket_connect(f"/ws/rooms/{code}") as listener:
        authenticate(speaker, host)
        authenticate(listener, guest)
        assert client.delete("/api/v1/auth/profile", headers=guest_headers).status_code == 200
        participants = client.get(f"/api/v1/rooms/{code}/participants", headers=headers).json()["data"]
        assert [entry["user_name"] for entry in participants] == ["account_host"]


def test_repeated_disconnect_releases_sessions_and_membership(realtime_client):
    client = realtime_client
    token, headers = user(client, "repeated_disconnect")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Disconnects"}).json()["data"]["room_code"]
    for _ in range(5):
        with client.websocket_connect(f"/ws/rooms/{code}") as socket:
            authenticate(socket, token)
            socket.send_json({"type": "ping", "data": "alive"})
            assert receive_event(socket, "pong") == "alive"
        # Synchronize on the ASGI event loop so ownership cleanup has completed.
        async def synchronize():
            for _ in range(100):
                if not client.app.state.rooms.sessions:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("Disconnected session leaked")
        client.portal.call(synchronize)
        assert client.get(f"/api/v1/rooms/{code}/participants", headers=headers).json()["data"] == []


def test_clearing_history_preserves_active_streams_and_other_users_history(realtime_client):
    client = realtime_client
    token, headers = user(client, "clear_host")
    _, other_headers = user(client, "remaining_host")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "To remove"}).json()["data"]["room_code"]
    other_code = client.post("/api/v1/rooms", headers=other_headers, json={"title": "Keep"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        authenticate(socket, token)
        assert client.delete("/api/v1/rooms/clear-all", headers=headers).status_code == 200
        socket.send_json({"type": "ping", "data": "still-live"})
        assert receive_event(socket, "pong") == "still-live"
        assert client.get("/api/v1/rooms", headers=headers).json()["data"] == []
    assert client.get(f"/api/v1/rooms/{code}").status_code == 200
    assert client.get(f"/api/v1/rooms/{other_code}").status_code == 200


def test_owner_rejoins_after_guest_and_can_still_end_the_room(realtime_client):
    client = realtime_client
    host, headers = user(client, "returning_owner")
    guest, guest_headers = user(client, "arrived_first")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Return to my room"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        authenticate(socket, host)
    with client.websocket_connect(f"/ws/rooms/{code}") as listener:
        authenticate(listener, guest)
        entry = client.get("/api/v1/rooms", headers=headers).json()["data"][0]
        assert entry["host_id"] == client.get("/api/v1/auth/me", headers=headers).json()["data"]["id"]
        assert entry["is_host"]
        assert client.post(f"/api/v1/rooms/{code}/end", headers=guest_headers, json={}).status_code == 403
        assert client.delete(f"/api/v1/rooms/{code}/transcripts", headers=guest_headers).status_code == 403
        assert client.delete(f"/api/v1/rooms/{code}", headers=guest_headers).status_code == 200
        assert client.get("/api/v1/rooms", headers=guest_headers).json()["data"] == []
        assert len(client.get("/api/v1/rooms", headers=headers).json()["data"]) == 1
        with client.websocket_connect(f"/ws/rooms/{code}") as returning:
            authenticate(returning, host)
            assert client.post(f"/api/v1/rooms/{code}/end", headers=headers, json={}).status_code == 200
            receive_event(listener, "session_concluded")


def test_failed_stream_setup_does_not_leave_a_ghost_participant(realtime_client):
    client = realtime_client
    token, headers = user(client, "setup_failure")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "Setup failure"}).json()["data"]["room_code"]
    runtime = client.app.state.rooms
    with patch.object(runtime, "refresh", AsyncMock(side_effect=[RuntimeError("setup failed"), None])), \
         client.websocket_connect(f"/ws/rooms/{code}") as socket:
        socket.send_json({"type": "authenticate", "token": token})
        assert socket.receive()["code"] == 1011
    assert runtime.sessions == {}
    assert client.get(f"/api/v1/rooms/{code}/participants", headers=headers).json()["data"] == []


def test_saved_moments_require_membership_and_are_private(realtime_client):
    from backend.db.rooms import add_transcript
    client = realtime_client
    _, headers = user(client, "bookmark_owner")
    guest, guest_headers = user(client, "bookmark_guest")
    room = client.post("/api/v1/rooms", headers=headers, json={"title": "Personal moments"}).json()["data"]
    code = room["room_code"]
    turn = add_transcript(room["id"], room["host_id"], "Host", "Remember tomorrow at six.", "en", {})
    path = f"/api/v1/rooms/{code}/moments"
    assert client.post(path, headers=guest_headers, json={"turn_id": turn["id"]}).status_code == 403
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        authenticate(socket, guest)
        assert client.post(path, headers=guest_headers, json={"turn_id": turn["id"]}).status_code == 200
        assert len(client.get(path, headers=guest_headers).json()["data"]) == 1
        assert client.get(path, headers=headers).json()["data"] == []
        assert client.post(path, headers=guest_headers, json={"turn_id": "not-in-this-room"}).status_code == 404
        assert client.get("/api/v1/moments").status_code == 401
        assert len(client.get("/api/v1/moments", headers=guest_headers).json()["data"]) == 1
        assert client.delete(path + "/" + turn["id"], headers=guest_headers).status_code == 200
        assert client.get(path, headers=guest_headers).json()["data"] == []


def test_recap_is_explicit_post_call_and_sources_remain_access_controlled(realtime_client):
    from backend.db.rooms import add_transcript
    from tests.unit.test_conversation_features import result
    client = realtime_client
    _, headers = user(client, "ai_owner")
    _, outsider = user(client, "ai_outsider")
    room = client.post("/api/v1/rooms", headers=headers, json={"title": "Tea at six"}).json()["data"]
    code = room["room_code"]
    turn = add_transcript(room["id"], room["host_id"], "Host", "Tomorrow at six, not five.", "en", {})
    path = f"/api/v1/rooms/{code}/recap"
    assert client.get(path, headers=outsider).status_code == 403
    assert client.post(path, headers=headers, json={"language": "en"}).status_code == 409
    with patch.object(client.app.state.recaps.generator, "generate", return_value=result(turn["original_text"], turn["id"])) as model:
        assert client.post(f"/api/v1/rooms/{code}/end", headers=headers, json={}).status_code == 200
        model.assert_not_called()
        response = client.post(path, headers=headers, json={"language": "en"})
        assert response.status_code == 200
        client.app.state.recaps.pool.submit(lambda: None).result(3)
        ready = client.get(path, headers=headers).json()["data"]
        assert ready["status"] == "ready"
        assert ready["result"]["summary"]["source_ids"] == [turn["id"]]
        assert client.post(path, headers=headers, json={"language": "en"}).json()["data"]["job_id"] == ready["job_id"]
        assert model.call_count == 1
    assert client.delete(f"/api/v1/rooms/{code}/transcripts", headers=headers).status_code == 200
    assert client.get(path, headers=headers).json()["data"]["status"] == "not_generated"


def test_new_speech_after_reconnect_does_not_overwrite_previous_turn(realtime_client):
    client = realtime_client
    token, headers = user(client, "reconnect_transcript")
    room = client.post("/api/v1/rooms", headers=headers, json={"title": "Two separate hellos"}).json()["data"]
    code = room["room_code"]
    for _ in range(2):
        with client.websocket_connect(f"/ws/rooms/{code}") as socket:
            authenticate(socket, token)
            socket.send_bytes(b"\0\0" * 1600)
            receive_event(socket, "turn")
    history = client.get(f"/api/v1/rooms/{code}/transcripts", headers=headers).json()["data"]
    assert len(history) == 2 and history[0]["id"] != history[1]["id"]


def test_recap_resolves_model_references_to_saved_hindi_sources_and_caches_the_result(realtime_client):
    import httpx

    from backend.db.rooms import add_transcript
    from tests.unit.test_conversation_features import referenced_result

    client = realtime_client
    _, headers = user(client, "hindi_recap")
    room = client.post("/api/v1/rooms", headers=headers, json={"title": "A Hindi conversation"}).json()["data"]
    turn = add_transcript(room["id"], room["host_id"], "Host", "कल शाम छह बजे मिलेंगे, पाँच बजे नहीं।", "hi", {})
    requests = []
    def handler(request):
        requests.append(request)
        reply = referenced_result("They agreed to meet at six, not five.", ["wrong"] if len(requests) == 1 else ["p1"])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}]})
    client.app.state.recaps.generator.client_factory = lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler))
    code = room["room_code"]
    path = f"/api/v1/rooms/{code}/recap"
    assert client.post(f"/api/v1/rooms/{code}/end", headers=headers, json={}).status_code == 200
    assert client.post(path, headers=headers, json={"language": "en"}).status_code == 200
    client.app.state.recaps.pool.submit(lambda: None).result(3)
    ready = client.get(path, headers=headers).json()["data"]
    assert ready["status"] == "ready"
    assert ready["result"]["summary"] == {"text": "They agreed to meet at six, not five.",
                                          "source_ids": [turn["id"]], "evidence": turn["original_text"]}
    assert client.post(path, headers=headers, json={"language": "en"}).json()["data"]["job_id"] == ready["job_id"]
    assert len(requests) == 2


def test_saving_disabled_cannot_create_moments_or_send_transcript_for_ai_recap(realtime_client):
    client = realtime_client
    token, headers = user(client, "no_saved_text")
    room = client.post("/api/v1/rooms", headers=headers, json={"title": "Temporary conversation", "record_session": False}).json()["data"]
    code = room["room_code"]
    with patch.object(client.app.state.recaps.generator, "generate") as model:
        with client.websocket_connect(f"/ws/rooms/{code}") as socket:
            authenticate(socket, token)
            socket.send_bytes(b"\0\0" * 1600)
            turn = receive_event(socket, "turn")
            assert client.post(f"/api/v1/rooms/{code}/moments", headers=headers, json={"turn_id": turn["turn_id"]}).status_code == 409
            assert client.post(f"/api/v1/rooms/{code}/end", headers=headers, json={}).status_code == 200
        assert client.post(f"/api/v1/rooms/{code}/recap", headers=headers, json={"language": "en"}).status_code == 409
        assert client.get(f"/api/v1/rooms/{code}/transcripts", headers=headers).json()["data"] == []
        assert client.get(f"/api/v1/rooms/{code}/moments", headers=headers).json()["data"] == []
        model.assert_not_called()
