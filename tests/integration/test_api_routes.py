"""Integration tests for the dashboard server REST API routes."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib import request

import pytest

from backend.config import AppConfig
from backend.dashboard_server import DashboardRequestHandler, DashboardState


@pytest.fixture
def test_server(sample_config: AppConfig):
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


def http_get(url: str) -> dict:
    req = request.Request(url, headers={"Connection": "close"})
    with request.urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, data: dict) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Connection": "close"},
    )
    with request.urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_patch(url: str, data: dict) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Connection": "close"},
        method="PATCH",
    )
    with request.urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_api_v1_bootstrap(test_server) -> None:
    base_url, _ = test_server
    resp = http_get(f"{base_url}/api/v1/bootstrap")
    assert resp["ok"] is True
    assert "devices" in resp["data"]
    assert "languages" in resp["data"]
    assert "glossary" in resp["data"]


def test_api_v1_state(test_server) -> None:
    base_url, _ = test_server
    resp = http_get(f"{base_url}/api/v1/state")
    assert resp["ok"] is True
    assert "channels" in resp["data"]
    assert "session" in resp["data"]


def test_api_v1_sessions_list(test_server) -> None:
    base_url, _ = test_server
    resp = http_get(f"{base_url}/api/v1/sessions")
    assert resp["ok"] is True
    assert isinstance(resp["data"], list)


def test_api_v1_glossary_crud(test_server) -> None:
    base_url, _ = test_server
    # Initial load
    resp = http_get(f"{base_url}/api/v1/glossary")
    assert resp["ok"] is True

    # Save new entries
    payload = [
        {"term": "TechCorp", "preserve": True},
        {"term": "AlphaProject", "preserve": True},
    ]
    post_resp = http_post(f"{base_url}/api/v1/glossary", payload)
    assert post_resp["ok"] is True
    assert len(post_resp["data"]) == 2
    assert post_resp["data"][0]["term"] == "TechCorp"


def test_api_v1_patch_turn(test_server, sample_turns) -> None:
    base_url, state = test_server
    session_id = state.session_store.read_payload().get("session_id")
    state.conversation_store.append_turn(session_id, sample_turns[0])

    patch_url = f"{base_url}/api/v1/sessions/{session_id}/turns/0"
    patch_resp = http_patch(patch_url, {"detected_source_language": "fr", "retranslate": False})

    assert patch_resp["ok"] is True
    assert patch_resp["data"]["detected_source_language"] == "fr"
