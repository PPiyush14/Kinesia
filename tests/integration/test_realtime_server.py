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
    async def synthesize(self, text, language, context_id, chunk):
        await chunk(b"\0\0" * 100, 0, 24000)
    async def close(self): pass


@pytest.fixture
def realtime_client(sample_config):
    config = replace(sample_config, translation_provider="soniox", tts_provider="cartesia")
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


def test_delete_routes_are_not_shadowed_and_only_host_can_remove_room(realtime_client):
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
    assert client.get(f"/api/v1/rooms/{code}").status_code == 404
    assert client.delete("/api/v1/auth/profile", headers=other_headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=other_headers).status_code == 401


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


def test_clearing_host_rooms_ends_active_streams_but_preserves_other_hosts(realtime_client):
    client = realtime_client
    token, headers = user(client, "clear_host")
    _, other_headers = user(client, "remaining_host")
    code = client.post("/api/v1/rooms", headers=headers, json={"title": "To remove"}).json()["data"]["room_code"]
    other_code = client.post("/api/v1/rooms", headers=other_headers, json={"title": "Keep"}).json()["data"]["room_code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        authenticate(socket, token)
        assert client.delete("/api/v1/rooms/clear-all", headers=headers).status_code == 200
        assert receive_event(socket, "session_ended")["summary_method"] == "extractive"
    assert client.get(f"/api/v1/rooms/{code}").status_code == 404
    assert client.get(f"/api/v1/rooms/{other_code}").status_code == 200


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
