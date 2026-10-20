"""Integration tests for Kinesia Auth, Room Management, and Fan-Out REST endpoints."""

from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
import threading
from urllib import request
from urllib.error import HTTPError
from unittest.mock import patch, MagicMock

import pytest

from backend.config import AppConfig
from backend.dashboard_server import DashboardRequestHandler, DashboardState
from backend.db.connection import init_db


@pytest.fixture
def kinesia_server(sample_config: AppConfig, tmp_path, monkeypatch):
    """Run isolated dashboard server with clean SQLite test database."""
    test_db = tmp_path / "test_kinesia_api.db"
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("backend.db.connection._get_sqlite_path", lambda: test_db)
    init_db()

    state = DashboardState(sample_config)

    class Handler(DashboardRequestHandler):
        pass

    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}", state

    state.stop_all_channels()
    server.shutdown()
    server.server_close()


def api_post(url: str, data: dict, token: str | None = None) -> dict:
    body = json.dumps(data).encode("utf-8")
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, data=body, headers=headers)
    with request.urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_get(url: str, token: str | None = None) -> dict:
    headers = {"Connection": "close"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers)
    with request.urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_kinesia_auth_and_room_flow(kinesia_server) -> None:
    base_url, state = kinesia_server

    # 1. Register User
    reg_payload = {
        "username": "piyush",
        "email": "piyush@kinesia.ai",
        "password": "Password123!",
        "display_name": "Piyush K",
        "default_input_lang": "en",
        "default_output_lang": "mr",
    }
    reg_resp = api_post(f"{base_url}/api/v1/auth/register", reg_payload)
    assert reg_resp["ok"] is True
    assert reg_resp["data"]["username"] == "piyush"
    assert reg_resp["data"]["default_output_lang"] == "mr"

    # 2. Login User
    login_resp = api_post(f"{base_url}/api/v1/auth/login", {"login": "piyush", "password": "Password123!"})
    assert login_resp["ok"] is True
    token = login_resp["data"]["token"]
    assert token is not None

    # 3. Get Auth Profile (GET /api/v1/auth/me)
    me_resp = api_get(f"{base_url}/api/v1/auth/me", token=token)
    assert me_resp["ok"] is True
    assert me_resp["data"]["email"] == "piyush@kinesia.ai"

    # 4. Create Room (POST /api/v1/rooms)
    room_resp = api_post(
        f"{base_url}/api/v1/rooms",
        {"title": "Global Product Sync", "passcode": "secret123", "record_session": True},
        token=token,
    )
    assert room_resp["ok"] is True
    room_code = room_resp["data"]["room_code"]
    assert room_code.startswith("kin-")

    # 5. Verify Room Passcode (POST /api/v1/rooms/{code}/verify)
    verify_resp = api_post(f"{base_url}/api/v1/rooms/{room_code}/verify", {"passcode": "secret123"})
    assert verify_resp["ok"] is True
    assert verify_resp["data"]["valid"] is True

    verify_bad = api_post(f"{base_url}/api/v1/rooms/{room_code}/verify", {"passcode": "wrongPasscode"})
    assert verify_bad["data"]["valid"] is False

    # 6. Speak in Room (POST /api/v1/rooms/{code}/speak)
    with patch.object(state.coordinator.gemma, "translate", return_value="नमस्ते, हम बात कर रहे हैं।"), \
         patch.object(state.coordinator.tts, "synthesize_for_language", return_value=b"RIFFdummywav"):

        speak_resp = api_post(
            f"{base_url}/api/v1/rooms/{room_code}/speak",
            {"text": "Hello, we are talking.", "source_lang": "en", "confidence": 0.95},
            token=token,
        )
        assert speak_resp["ok"] is True
        assert speak_resp["data"]["original_text"] == "Hello, we are talking."

    # 7. End Room Session (POST /api/v1/rooms/{code}/end)
    with patch.object(state.coordinator.summarizer, "summarize") as mock_summarize:
        mock_summary = MagicMock()
        mock_summary.executive_summary = "Discussion on global scaling."
        mock_summary.action_items = ["Deploy frontend to Vercel"]
        mock_summary.key_points = ["Multi-party architecture validated"]
        mock_summarize.return_value = mock_summary

        end_resp = api_post(f"{base_url}/api/v1/rooms/{room_code}/end", {}, token=token)
        assert end_resp["ok"] is True
        assert end_resp["data"]["executive_summary"] == "Discussion on global scaling."
        assert "Deploy frontend to Vercel" in end_resp["data"]["action_items"]

    # 8. Query Saved Insights (GET /api/v1/rooms/{code}/insights)
    insights_resp = api_get(f"{base_url}/api/v1/rooms/{room_code}/insights")
    assert insights_resp["ok"] is True
    assert insights_resp["data"]["executive_summary"] == "Discussion on global scaling."
