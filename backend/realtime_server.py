"""Public ASGI entry point; preserves REST/SSE behind a loopback-only adapter."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from http.server import ThreadingHTTPServer

import anyio
import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from backend.config import AppConfig, _is_missing, load_config
from backend.dashboard_server import DashboardRequestHandler, DashboardState
from backend.db.auth import get_user_profile, verify_token
from backend.db.connection import is_postgres
from backend.db.rooms import get_room_by_code
from backend.languages import LANGUAGE_DISPLAY_NAMES, normalize_language_code
from backend.realtime_rooms import RealtimeRooms

logger = logging.getLogger(__name__)


def auth_user(token):
    payload = verify_token(token) if isinstance(token, str) else None
    return get_user_profile(payload["sub"]) if payload and payload.get("sub") else None


def language(value):
    code = normalize_language_code(value)
    if not code or code not in LANGUAGE_DISPLAY_NAMES:
        raise ValueError("Select a supported speaking/listening language.")
    return code


def create_app(config: AppConfig | None = None, *, state_factory=DashboardState):
    config = config or load_config()

    @asynccontextmanager
    async def lifespan(app):
        state = await asyncio.to_thread(state_factory, config)
        app.state.dashboard = state
        app.state.rooms = RealtimeRooms(state.coordinator, config)
        class LegacyHandler(DashboardRequestHandler):
            pass
        LegacyHandler.state = state
        server = ThreadingHTTPServer(("127.0.0.1", 0), LegacyHandler)
        server.daemon_threads = True
        server.block_on_close = False
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="kinesia-rest")
        thread.start()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False,
                                     timeout=httpx.Timeout(60, connect=5, read=None),
                                     limits=httpx.Limits(max_connections=64, max_keepalive_connections=16)) as client:
            app.state.rest = client
            try:
                yield
            finally:
                await app.state.rooms.close()
                await asyncio.to_thread(server.shutdown)
                server.server_close()
                await asyncio.to_thread(state.stop_all_channels)
                thread.join(timeout=2)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=config.allowed_origins or ["*"],
                       allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Accept"])

    @app.get("/api/v1/health")
    @app.get("/api/health")
    async def health():
        return {"ok": True, "data": {"status": "healthy", "transport": "websocket",
                "translation_provider": "soniox", "tts_provider": "cartesia",
                "database": "postgresql" if is_postgres() else "sqlite",
                "speech_configured": not _is_missing(config.soniox_api_key) and not _is_missing(config.cartesia_api_key)
                                     and any(channel.cartesia_voice_id for channel in config.channels.values())}, "error": None}

    @app.websocket("/ws/rooms/{room_code}")
    async def room_socket(websocket: WebSocket, room_code: str):
        origin = websocket.headers.get("origin")
        if origin and "*" not in config.allowed_origins and origin not in config.allowed_origins:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        session, tasks, subscription = None, [], None
        try:
            hello = await asyncio.wait_for(websocket.receive_json(), 10)
            if not isinstance(hello, dict) or hello.get("type") != "authenticate":
                await websocket.close(code=4401, reason="Sign in to join this room.")
                return
            user = await asyncio.to_thread(auth_user, hello.get("token", ""))
            if not user:
                await websocket.close(code=4401, reason="Session expired. Sign in again.")
                return
            speaking, listening = language(hello.get("speaking_lang", "en")), language(hello.get("listening_lang", "hi"))
            runtime, coordinator = app.state.rooms, app.state.dashboard.coordinator
            code = room_code.strip().lower()
            # Admission limits protect a small Render instance and provider quotas.
            peers = [value for (room, _), value in runtime.sessions.items() if room == code]
            if len(peers) >= 8 and all(peer.user_id != user["id"] for peer in peers):
                raise ValueError("This room currently supports up to eight participants.")
            languages = {peer.participant.listening_lang for peer in peers if peer.participant} | {listening}
            if len(languages) > config.realtime_max_languages:
                raise ValueError(f"This room supports {config.realtime_max_languages} listening languages at once.")
            room, events = await asyncio.to_thread(coordinator.join_room, code, user, speaking, listening)
            subscription = (code, user["id"], events)
            await asyncio.to_thread(coordinator.update_participant_status, code, user["id"], speaking, listening, bool(hello.get("is_muted", False)))
            session = await runtime.attach(room, user, events, websocket)
            session.send({"type": "connected", "data": {"room_code": code, "provider": "soniox", "tts_provider": "cartesia"}})
            if _is_missing(config.soniox_api_key) or _is_missing(config.cartesia_api_key):
                session.error("Configure SONIOX_API_KEY and CARTESIA_API_KEY on the Render backend.")

            async def read():
                window_start, byte_count, controls = time.monotonic(), 0, 0
                while True:
                    message = await websocket.receive()
                    now = time.monotonic()
                    if now - window_start >= 1:
                        window_start, byte_count, controls = now, 0, 0
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        now = time.monotonic()
                        if now - window_start >= 1:
                            window_start, byte_count, controls = now, 0, 0
                        byte_count += len(message["bytes"])
                        if byte_count > 80000:
                            raise ValueError("Audio is arriving faster than real time.")
                        await session.receive_audio(message["bytes"])
                    elif message.get("text"):
                        if len(message["text"]) > 4096:
                            raise ValueError("Control message is too large.")
                        event = json.loads(message["text"])
                        if event.get("type") == "configure":
                            controls += 1
                            if controls > 12:
                                raise ValueError("Too many configuration updates; reconnect.")
                            source = language(event.get("speaking_lang", session.participant.speaking_lang))
                            target = language(event.get("listening_lang", session.participant.listening_lang))
                            active_languages = {peer.participant.listening_lang for (room, _), peer in runtime.sessions.items()
                                                if room == code and peer.participant and peer.user_id != user["id"]} | {target}
                            if len(active_languages) > config.realtime_max_languages:
                                session.error("The room's listening-language limit was reached.", "room")
                                continue
                            await asyncio.to_thread(coordinator.update_participant_status, code, user["id"], source, target, event.get("is_muted"))
                            session.send({"type": "audio_reset"})
                            await runtime.refresh(code)
                        elif event.get("type") == "speaking" and time.monotonic() - session.last_speaking_event > 0.15:
                            session.last_speaking_event = time.monotonic()
                            coordinator.broadcast_event(code, "speaking", {"speaker_id": user["id"], "speaker_name": user.get("display_name"), "is_speaking": bool(event.get("active"))})
                        elif event.get("type") == "ping":
                            session.send({"type": "pong", "data": event.get("data")})
            tasks = [asyncio.create_task(read()), asyncio.create_task(session.writer()), asyncio.create_task(session.bridge_events())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exception = task.exception()
                if exception:
                    raise exception
        except (WebSocketDisconnect, asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except ValueError as exc:
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "pipeline_error", "data": {"provider": "room", "message": str(exc)}})
                await websocket.close(code=4400, reason="Room configuration failed.")
        except Exception as exc:  # noqa: BLE001 - public boundary must not leak provider credentials.
            logger.warning("Realtime connection stopped (%s)", type(exc).__name__)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="Connection interrupted; please reconnect.")
        finally:
            # ASGI servers/test clients may cancel the handler during disconnect.
            # Finish ownership cleanup even inside a cancelled AnyIO scope.
            with anyio.CancelScope(shield=True):
                if session:
                    session.closed = True
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if session:
                    await app.state.rooms.detach(session)
                elif subscription:
                    await asyncio.to_thread(app.state.dashboard.coordinator.leave_room, *subscription)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE", "HEAD"])
    async def rest_proxy(path: str, request: Request):
        # Desktop microphone/settings routes were never designed for public users.
        if path.startswith("api/") and not (path.startswith(("api/v1/auth/", "api/v1/rooms/")) or path == "api/v1/rooms"):
            return JSONResponse({"ok": False, "error": "Route not available in meeting mode.", "data": None}, status_code=404)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 8 * 1024 * 1024:
                return JSONResponse({"error": "Request exceeds the audio upload limit."}, status_code=413)
        if request.method == "POST" and path.startswith("api/v1/rooms/") and path.endswith("/end"):
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            user = await asyncio.to_thread(auth_user, token)
            code = path.split("/")[3]
            record = await asyncio.to_thread(get_room_by_code, code)
            if user and record and str(record["host_id"]) == str(user["id"]):
                await app.state.rooms.flush_room(code)
        headers = {key: value for key, value in request.headers.items() if key.lower() not in {"host", "connection", "transfer-encoding", "content-length"}}
        url = "/" + path + ("?" + request.url.query if request.url.query else "")
        upstream = app.state.rest.build_request(request.method, url, content=bytes(content), headers=headers)
        try:
            response = await app.state.rest.send(upstream, stream=True)
        except httpx.HTTPError:
            return JSONResponse({"ok": False, "error": "Backend temporarily unavailable.", "data": None}, status_code=503)
        if response.status_code >= 500:
            await response.aclose()
            return JSONResponse({"ok": False, "error": "The request could not be completed. Check the backend logs and provider configuration.", "data": None}, status_code=500)
        async def body():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
        response_headers = {key: value for key, value in response.headers.items()
                            if key.lower() not in {"connection", "transfer-encoding", "server", "date", "access-control-allow-origin"}}
        return StreamingResponse(body(), status_code=response.status_code, headers=response_headers)

    return app


def run_realtime_server(config=None, *, host=None, port=None):
    import uvicorn
    config = config or load_config()
    if os.getenv("RENDER", "").lower() == "true":
        if not is_postgres():
            raise RuntimeError("Set DATABASE_URL to your Render PostgreSQL internal URL on the backend service.")
        if len(os.getenv("JWT_SECRET", "").strip()) < 32:
            raise RuntimeError("Set a persistent random JWT_SECRET of at least 32 characters on the Render backend.")
    uvicorn.run(create_app(config), host=host or os.getenv("HOST", "0.0.0.0"), port=port or int(os.getenv("PORT", "8765")),
                access_log=False, ws_max_size=65536, ws_max_queue=16, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    run_realtime_server()
