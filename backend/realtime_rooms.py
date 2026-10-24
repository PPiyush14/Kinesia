"""Async audio routing around the existing room membership/persistence APIs."""
from __future__ import annotations

import asyncio
import contextlib
import queue
import struct
import time
import uuid
from dataclasses import replace

from backend.conversation_floor import SpeechActivity
from backend.realtime_providers import CartesiaStream, SonioxStream, SpeechCancelled


class RealtimeSession:
    def __init__(self, runtime, room, profile, events, websocket):
        self.runtime, self.room, self.profile = runtime, room, profile
        self.user_id, self.events, self.websocket = profile["id"], events, websocket
        self.outgoing = asyncio.Queue(maxsize=128)
        self.lanes = {}
        self.primary = None
        self.audio_ms = room.speaker_audio_positions.get(profile["id"], 0.0)
        self.capture_epoch = str(uuid.uuid4())
        self.closed = False
        self.ending = False
        self.overloaded = False
        self.last_speaking_event = 0.0
        self.activity = SpeechActivity()
        self.audio_cutoffs = {}

    @property
    def participant(self):
        participant = self.room.participants.get(self.user_id)
        return participant if participant and participant.event_queue is self.events else None

    def send(self, message):
        if self.closed:
            return
        try:
            self.outgoing.put_nowait(message)
        except asyncio.QueueFull:
            self.overloaded = True

    def error(self, message, provider="soniox"):
        self.send({"type": "pipeline_error", "data": {"provider": provider, "message": message}})

    def caption(self, target, data):
        if target == self.primary:
            self.runtime.recognized(self, data)
        data = dict(data, speaker_id=self.user_id, speaker_name=self.profile.get("display_name") or self.profile["username"])
        for listener in self.runtime.listeners(self.room.room_code, target):
            listener.send({"type": "caption", "data": data})
        if target == self.primary and self not in self.runtime.listeners(self.room.room_code, target):
            self.send({"type": "caption", "data": dict(data, localized_text=data["original_text"])})

    async def final(self, segment):
        self.room.speaker_audio_positions[self.user_id] = max(
            self.room.speaker_audio_positions.get(self.user_id, 0), segment.end_ms, self.audio_ms)
        await asyncio.to_thread(
            self.runtime.coordinator.record_stream_segment, self.room.room_code, self.user_id,
            self.profile.get("display_name") or self.profile["username"], replace(segment, audio_epoch=self.capture_epoch),
        )

    async def update_routes(self):
        participant = self.participant
        if not participant or participant.is_muted or self.ending:
            desired = set()
        else:
            desired = {session.participant.listening_lang for session in self.runtime.sessions.values()
                       if session.room is self.room and session.participant and session.user_id != self.user_id}
            desired = desired or {participant.speaking_lang}
            # Retain the primary STT route while speaking so audio timestamps and
            # source captions do not reset when a listener joins/leaves.
            if self.primary:
                desired.add(self.primary)
            self.primary = self.primary if self.primary in desired else min(desired)
        for language in list(self.lanes):
            if language not in desired or self.lanes[language].source != (participant.speaking_lang if participant else ""):
                await self.lanes.pop(language).close()
        for language in sorted(desired):
            if language not in self.lanes:
                lane = TranslationLane(self, participant.speaking_lang, language)
                self.lanes[language] = lane
                lane.start()

    async def bridge_events(self):
        while not self.closed:
            if self.participant is None:
                self.send({"_close": 4009})
                await asyncio.Future()
            try:
                event = await asyncio.to_thread(self.events.get, True, 0.25)
            except queue.Empty:
                continue
            if event.get("type") in {"session_concluded", "session_ended"}:
                while not self.outgoing.empty():
                    self.outgoing.get_nowait()
                self.send({"type": "audio_reset"})
                self.send(event)
                await asyncio.Future()
            self.send(event)

    async def writer(self):
        while not self.closed:
            if self.overloaded:
                await self.websocket.close(code=1013, reason="Audio connection is too slow; reconnect.")
                return
            try:
                message = await asyncio.wait_for(self.outgoing.get(), 1)
            except asyncio.TimeoutError:
                continue
            if isinstance(message, bytes):
                await asyncio.wait_for(self.websocket.send_bytes(message), 5)
            elif message.get("_close"):
                await self.websocket.close(code=message["_close"])
                return
            else:
                await asyncio.wait_for(self.websocket.send_json(message), 5)
                if message.get("type") in {"session_concluded", "session_ended"}:
                    await self.websocket.close(code=1000)
                    return

    async def receive_audio(self, data):
        participant = self.participant
        if not participant or participant.is_muted or self.ending:
            return
        if not data or len(data) % 2 or len(data) > 16384:
            raise ValueError("Expected small 16-kHz mono PCM16 audio frames.")
        self.audio_ms += len(data) / 32
        self.room.speaker_audio_positions[self.user_id] = self.audio_ms
        for lane in self.lanes.values():
            lane.soniox.feed(data)

    async def close(self):
        self.closed = True
        await asyncio.gather(*(lane.close() for lane in self.lanes.values()), return_exceptions=True)
        self.lanes.clear()


class TranslationLane:
    def __init__(self, session, source, target):
        self.session, self.source, self.target = session, source, target
        self.queue = asyncio.Queue(maxsize=16)
        self.cartesia = CartesiaStream(session.runtime.config)
        self.soniox = SonioxStream(session.runtime.config, source, target, session.audio_ms,
                                   lambda caption: session.caption(target, caption), self.phrase,
                                   session.final, session.error)
        self.task = None
        self.soniox.on_boundary = self.boundary
        self.active_context = None
        self.active_turn = None

    def start(self):
        self.soniox.start()
        self.task = asyncio.create_task(self.speak())

    def phrase(self, text, utterance_id):
        listeners = [listener for listener in self.session.runtime.listeners(self.session.room.room_code, self.target)
                     if listener.user_id != self.session.user_id]
        if not listeners:
            return
        try:
            self.queue.put_nowait((text, utterance_id, time.monotonic()))
        except asyncio.QueueFull:
            self.session.error("Translated speech is falling behind. Shorter turns will help; captions remain available.", "playback")

    def boundary(self, utterance_id):
        try:
            self.queue.put_nowait((None, utterance_id, time.monotonic()))
        except asyncio.QueueFull:
            self.session.error("Translated audio is overloaded. Reconnect to catch up; the transcript remains available.", "playback")

    async def speak(self):
        while True:
            text, utterance_id, queued_at = await self.queue.get()
            if text is None:
                if self.active_context and self.active_turn == utterance_id:
                    try:
                        await self.cartesia.finish_context(self.active_context, self.target)
                    except Exception:  # noqa: BLE001 - the next phrase can establish a fresh provider connection.
                        await self.cartesia.close()
                    self.active_context = self.active_turn = None
                continue
            if time.monotonic() - queued_at > 8:
                self.session.error("Old queued speech was skipped to stay live. Read the transcript for the full conversation.", "playback")
                continue
            if self.active_turn != utterance_id:
                if self.active_context:
                    await self.cartesia.cancel_context(self.active_context)
                self.active_context, self.active_turn = str(uuid.uuid4()), utterance_id
            provider_context = self.active_context
            context = str(uuid.uuid4())  # A transport stream per flushed clause.
            started = set()
            started_at = time.monotonic()
            def listeners(queued_at=queued_at):
                return [listener for listener in self.session.runtime.listeners(self.session.room.room_code, self.target)
                        if listener.user_id != self.session.user_id
                        and queued_at > listener.audio_cutoffs.get(self.session.user_id, 0)]
            async def chunk(audio, sequence, sample_rate, context=context, utterance_id=utterance_id,
                            started_at=started_at, queued_at=queued_at, started=started, listeners=listeners):
                current = listeners()
                if not current:
                    raise SpeechCancelled()
                for listener in current:
                    if listener not in started:
                        listener.send({"type": "audio_start", "data": {
                            "stream_id": context, "utterance_id": utterance_id, "speaker_id": self.session.user_id,
                            "sample_rate": sample_rate, "language": self.target,
                            "sequence_start": sequence,
                            "queue_wait_ms": round((started_at - queued_at) * 1000),
                            "voice_start_ms": round((time.monotonic() - started_at) * 1000),
                        }})
                        started.add(listener)
                packet = b"KNS1" + uuid.UUID(context).bytes + struct.pack(">I", sequence) + audio
                for listener in listeners():
                    listener.send(packet)
            try:
                if listeners():
                    await self.cartesia.synthesize(text, self.target, provider_context, chunk, continuation=True)
            except SpeechCancelled:
                self.active_context = self.active_turn = None
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - provider failures must preserve caption delivery.
                for listener in listeners():
                    listener.error("Cartesia speech is unavailable. Captions are still live; check the API key, voice and credits.", "cartesia")
            finally:
                for listener in started:
                    listener.send({"type": "audio_end", "data": {"stream_id": context}})

    async def close(self):
        await self.soniox.close()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.cartesia.close()


class RealtimeRooms:
    def __init__(self, coordinator, config):
        self.coordinator, self.config = coordinator, config
        self.sessions = {}
        self.lock = asyncio.Lock()
        self.floor = {}

    def voice(self, session, active):
        session.activity.voice(active, session.audio_ms, time.monotonic())

    def recognized(self, session, caption):
        now = time.monotonic()
        if not session.activity.observe(caption, session.audio_ms, now):
            return
        owner = self.floor.get(session.room.room_code)
        if owner is session:
            return
        released = owner is not None and (owner.closed or not owner.activity.active and now - owner.activity.ended >= 0.35)
        if session.activity.taken and not released:
            return
        session.activity.taken = True
        self.floor[session.room.room_code] = session
        peers = [peer for (code, _), peer in self.sessions.items() if code == session.room.room_code and not peer.closed]
        for listener in peers:
            for speaker in peers:
                if speaker is not session:
                    listener.audio_cutoffs[speaker.user_id] = now
            listener.send({"type": "audio_interrupt", "data": {"speaker_id": session.user_id}})

    def listeners(self, room_code, language):
        return [session for (code, _), session in self.sessions.items()
                if code == room_code and session.participant and session.participant.listening_lang == language and not session.closed]

    async def refresh(self, room_code):
        async with self.lock:
            await asyncio.gather(*(session.update_routes() for (code, _), session in self.sessions.items() if code == room_code))

    async def attach(self, room, profile, events, websocket):
        key = (room.room_code, profile["id"])
        previous = self.sessions.get(key)
        if previous:
            with contextlib.suppress(Exception):
                await previous.websocket.close(code=4009, reason="Joined from another tab.")
            await previous.close()
        session = RealtimeSession(self, room, profile, events, websocket)
        self.sessions[key] = session
        try:
            await self.refresh(room.room_code)
        except BaseException:
            await self.detach(session)
            raise
        return session

    async def detach(self, session):
        key = (session.room.room_code, session.user_id)
        if self.sessions.get(key) is session:
            del self.sessions[key]
            if self.floor.get(session.room.room_code) is session:
                self.floor.pop(session.room.room_code, None)
            await asyncio.to_thread(self.coordinator.leave_room, session.room.room_code, session.user_id, session.events)
        await session.close()
        await self.refresh(session.room.room_code)

    async def flush_room(self, room_code):
        sessions = [session for (code, _), session in self.sessions.items() if code == room_code]
        for session in sessions:
            session.ending = True
        await asyncio.gather(*(lane.soniox.flush() for session in sessions for lane in session.lanes.values()), return_exceptions=True)

    async def close(self):
        await asyncio.gather(*(session.close() for session in list(self.sessions.values())), return_exceptions=True)
        self.sessions.clear()
