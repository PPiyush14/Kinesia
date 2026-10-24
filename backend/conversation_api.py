"""Authenticated recap and personal bookmark routes for browser conversations."""
from __future__ import annotations

import asyncio
import json

from fastapi import HTTPException, Request

from backend.db.conversation_features import list_moments, remove_moment, save_moment
from backend.db.rooms import get_room_by_code, is_room_member


def register_conversation_routes(app, auth_user, language):
    async def member(request, room_code=None):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Sign in to continue.")
        user = await asyncio.to_thread(auth_user, authorization[7:])
        if not user:
            raise HTTPException(401, "Your session expired. Sign in again.")
        if room_code is None:
            return user, None
        room = await asyncio.to_thread(get_room_by_code, room_code)
        if not room:
            raise HTTPException(404, "Conversation not found.")
        if not await asyncio.to_thread(is_room_member, room["id"], user["id"]):
            raise HTTPException(403, "Only conversation participants can access this content.")
        return user, room

    async def body(request):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 4096:
                raise HTTPException(413, "Request is too large.")
        try:
            value = json.loads(content or b"{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "Expected a JSON object.") from exc
        if not isinstance(value, dict):
            raise HTTPException(400, "Expected a JSON object.")
        return value

    def envelope(value):
        return {"ok": True, "data": value, "error": None}

    @app.get("/api/v1/moments")
    async def personal_moments(request: Request):
        user, _ = await member(request)
        return envelope(await asyncio.to_thread(list_moments, user["id"]))

    @app.get("/api/v1/rooms/{room_code}/moments")
    async def room_moments(room_code: str, request: Request):
        user, room = await member(request, room_code)
        return envelope(await asyncio.to_thread(list_moments, user["id"], room["id"]))

    @app.post("/api/v1/rooms/{room_code}/moments")
    async def add_moment(room_code: str, request: Request):
        user, room = await member(request, room_code)
        if not room["record_session"]:
            raise HTTPException(409, "Saved Moments require a saved conversation transcript.")
        data = await body(request)
        turn_id = data.get("turn_id")
        if not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 128:
            raise HTTPException(400, "Choose a saved transcript turn.")
        try:
            await asyncio.to_thread(save_moment, user["id"], room["id"], turn_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return envelope({"saved": True, "turn_id": turn_id})

    @app.delete("/api/v1/rooms/{room_code}/moments/{turn_id}")
    async def delete_moment(room_code: str, turn_id: str, request: Request):
        user, room = await member(request, room_code)
        await asyncio.to_thread(remove_moment, user["id"], room["id"], turn_id)
        return envelope({"saved": False, "turn_id": turn_id})

    @app.get("/api/v1/rooms/{room_code}/recap")
    async def recap_status(room_code: str, request: Request, output_language: str = "en"):
        _, room = await member(request, room_code)
        try:
            target = language(output_language)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        value = await asyncio.to_thread(app.state.recaps.status, room["id"], target)
        value.update(available=room["record_session"] and not room["is_active"],
                     recording_enabled=room["record_session"], conversation_active=room["is_active"])
        return envelope(value)

    @app.post("/api/v1/rooms/{room_code}/recap")
    async def generate_recap(room_code: str, request: Request):
        _, room = await member(request, room_code)
        if room["is_active"]:
            raise HTTPException(409, "Recaps are available after the conversation ends.")
        if not room["record_session"]:
            raise HTTPException(409, "This conversation did not save a transcript.")
        data = await body(request)
        try:
            target = language(data.get("language", "en"))
            value = await asyncio.to_thread(app.state.recaps.start, room["id"], target)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return envelope(value)
