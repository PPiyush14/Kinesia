"""Bounded, cancellable provider streams. Keys never leave the backend."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from dataclasses import dataclass

from websockets.asyncio.client import connect
from websockets.protocol import State

from backend.config import AppConfig

CONTROL_TOKENS = {"<end>", "<fin>"}


class SpeechCancelled(Exception):
    """No listener needs this obsolete speech phrase any longer."""


@dataclass
class SpeechSegment:
    turn_id: str
    original: str
    translated: str
    source_lang: str
    target_lang: str
    start_ms: float
    end_ms: float
    confidence: float
    audio_epoch: str = ""


class TranscriptBuffer:
    """Final tokens append once; provisional hypotheses only affect captions."""
    def __init__(self, source_lang: str, target_lang: str, offset_ms=0.0):
        self.source_lang, self.target_lang, self.offset_ms = source_lang, target_lang, offset_ms
        self.reset()

    def reset(self):
        self.turn_id = str(uuid.uuid4())
        self.original = ""
        self.translated = ""
        self.spoken = 0
        self.start_ms = None
        self.end_ms = 0.0
        self.confidences = []
        self.detected = self.source_lang

    def feed(self, tokens):
        partial_original, partial_translation = [], []
        partial_end = self.end_ms
        ended = False
        for token in tokens:
            text = str(token.get("text") or "")
            if text in CONTROL_TOKENS:
                ended = ended or bool(token.get("is_final"))
                continue
            translated = token.get("translation_status") == "translation"
            if not token.get("is_final"):
                (partial_translation if translated else partial_original).append(text)
                if not translated:
                    partial_end = max(partial_end, float(token.get("end_ms") or 0) + self.offset_ms)
                continue
            if translated:
                self.translated += text
            else:
                self.original += text
                if self.start_ms is None:
                    self.start_ms = float(token.get("start_ms") or 0) + self.offset_ms
                self.end_ms = float(token.get("end_ms") or self.end_ms - self.offset_ms) + self.offset_ms
                self.detected = token.get("language") or self.detected
                if isinstance(token.get("confidence"), (float, int)):
                    self.confidences.append(float(token["confidence"]))
        local = self.text()
        partial = partial_translation if self.translated or partial_translation or self.detected != self.target_lang else partial_original
        return {
            "turn_id": self.turn_id, "original_text": self.original + "".join(partial_original),
            "localized_text": local + "".join(partial), "listening_lang": self.target_lang,
            "source_lang": self.detected,
            "audio_end_ms": max(self.end_ms, partial_end),
        }, ended

    def text(self):
        # A speaking-language hint must not prevent code-switched speech from
        # being translated, even when the selected languages happen to match.
        return self.translated or (self.original if self.detected == self.target_lang else "")

    def phrase(self, *, final=False, allow_fragment=False):
        text = self.text()
        pending = text[self.spoken:]
        if not pending.strip():
            return ""
        # Never send replaceable partial tokens to TTS. Prefer complete clauses;
        # limit long stable passages without cutting a word in half.
        # A finalized sentence needs no trailing space before synthesis. A short
        # greeting must not block the following complete sentence, either.
        end = next((boundary.end() for boundary in re.finditer(r"[.!?।](?:\s+|$)", pending)
                    if len(pending[:boundary.end()].strip()) >= 12), 0)
        if not end and len(pending) >= 180:
            end = pending.rfind(" ", 0, 180)
        if not end and allow_fragment and len(pending.strip()) >= 32:
            # Stable translated words only. Prefer a clause boundary, otherwise
            # release a bounded word-safe prefix instead of waiting indefinitely.
            clauses = list(re.finditer(r"[,;:—](?:\s+|$)", pending[:120]))
            end = clauses[-1].end() if clauses and clauses[-1].end() >= 24 else pending.rfind(" ", 0, 120)
            if end < 24:
                end = 0
        if final:
            end = len(pending)
        if not end:
            return ""
        self.spoken += end
        return pending[:end].strip()

    def finish(self):
        segment = SpeechSegment(
            self.turn_id, self.original.strip(),
            self.text().strip(),
            self.detected, self.target_lang, self.start_ms or self.offset_ms,
            self.end_ms, sum(self.confidences) / len(self.confidences) if self.confidences else 1.0,
        )
        self.reset()
        return segment


class CartesiaStream:
    def __init__(self, config: AppConfig):
        self.config = config
        self.socket = None

    async def close(self):
        socket, self.socket = self.socket, None
        if socket is not None:
            await socket.close()

    async def synthesize(self, text, language, context_id, on_chunk, *, continuation=False, allow_empty=False):
        """Reuse the connection; a fresh context owns each complete stable clause."""
        channel = next(iter(self.config.channels.values()))
        voice = self.config.cartesia_voice_map.get(language, channel.cartesia_voice_id)
        if not voice:
            raise ValueError("Cartesia voice ID is not configured.")
        if self.socket is not None and getattr(self.socket, "state", State.OPEN) != State.OPEN:
            await self.close()
        if self.socket is None:
            self.socket = await connect(
                self.config.cartesia_websocket_url,
                additional_headers={"X-API-Key": self.config.cartesia_api_key, "Cartesia-Version": self.config.cartesia_version},
                open_timeout=8, close_timeout=2, max_size=2 * 1024 * 1024,
            )
        payload = {
            "model_id": channel.cartesia_model_id, "transcript": text,
            "voice": {"mode": "id", "id": voice}, "language": language,
            "context_id": context_id, "continue": continuation,
            "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": channel.cartesia_sample_rate},
            "generation_config": {"speed": self.config.cartesia_speed, "volume": 1.0},
        }
        started, sequence = time.monotonic(), 0
        try:
            await self.socket.send(json.dumps(payload))
            if continuation:
                # Explicit flush releases this stable clause while preserving
                # prosodic context for the next clause in the same utterance.
                await self.socket.send(json.dumps(dict(payload, transcript="", flush=True)))
            while True:
                remaining = self.config.cartesia_request_timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Cartesia synthesis deadline exceeded.")
                event = json.loads(await asyncio.wait_for(self.socket.recv(), remaining))
                if event.get("context_id") not in {None, context_id}:
                    continue
                if event.get("type") == "error" or int(event.get("status_code") or 200) >= 400:
                    raise RuntimeError("Cartesia rejected the speech request.")
                if event.get("type") == "chunk" and event.get("data"):
                    audio = base64.b64decode(event["data"], validate=True)
                    if len(audio) % 2:
                        raise ValueError("Cartesia returned an incomplete PCM sample.")
                    await on_chunk(audio, sequence, channel.cartesia_sample_rate)
                    sequence += 1
                if continuation and event.get("type") == "flush_done":
                    if not sequence and not allow_empty:
                        raise RuntimeError("Cartesia returned no speech audio.")
                    return
                if event.get("done") or event.get("type") in {"done", "completed"}:
                    if not sequence and not allow_empty:
                        raise RuntimeError("Cartesia returned no speech audio.")
                    return
        except SpeechCancelled:
            await self.cancel_context(context_id)
            raise
        except BaseException:
            await self.close()
            raise

    async def cancel_context(self, context_id):
        if self.socket is not None:
            try:
                await self.socket.send(json.dumps({"context_id": context_id, "cancel": True}))
            except Exception:  # noqa: BLE001 - Report sanitized provider failure and always clean up.
                await self.close()

    async def finish_context(self, context_id, language):
        if self.socket is not None:
            async def ignore_tail(*_):
                pass
            await self.synthesize("", language, context_id, ignore_tail, allow_empty=True)


class SonioxStream:
    """One persistent Soniox connection for one speaker/target-language route."""
    def __init__(self, config, source_lang, target_lang, offset_ms, on_caption, on_phrase, on_final, on_error):
        self.config, self.source_lang, self.target_lang = config, source_lang, target_lang
        self.buffer = TranscriptBuffer(source_lang, target_lang, offset_ms)
        self.on_caption, self.on_phrase, self.on_final, self.on_error = on_caption, on_phrase, on_final, on_error
        self.audio = asyncio.Queue(maxsize=50)
        self.queued_audio_bytes = 0  # Two seconds of PCM, independent of frame size.
        self.socket = None
        self.task = None
        self.closed = False
        self.on_boundary = lambda _turn_id: None

    def start(self):
        self.task = asyncio.create_task(self.run())

    def feed(self, audio):
        if self.closed:
            return
        try:
            if self.queued_audio_bytes + len(audio) > 64000:
                raise asyncio.QueueFull
            self.audio.put_nowait(audio)
            self.queued_audio_bytes += len(audio)
        except asyncio.QueueFull:
            # Replaying stale microphone audio later is worse than reconnecting.
            self.on_error("Soniox audio upload fell behind. Reconnect to resume live speech.")
            self.closed = True
            if self.task:
                self.task.cancel()

    async def flush(self):
        if not self.closed and self.task and not self.task.done():
            try:
                self.audio.put_nowait(None)
                await asyncio.wait_for(asyncio.shield(self.task), timeout=3)
            except (asyncio.TimeoutError, asyncio.QueueFull):
                await self.close()

    async def close(self):
        self.closed = True
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _finish(self):
        phrase = self.buffer.phrase(final=True)
        if phrase:
            self.on_phrase(phrase, self.buffer.turn_id)
        self.on_boundary(self.buffer.turn_id)
        segment = self.buffer.finish()
        if segment.original:
            await self.on_final(segment)

    async def run(self):
        sender = None
        finish_due = None
        phrase_due = None
        try:
            async with connect(self.config.soniox_websocket_url, open_timeout=8, close_timeout=2, max_size=2 * 1024 * 1024) as socket:
                self.socket = socket
                config = {
                    "api_key": self.config.soniox_api_key, "model": self.config.soniox_model,
                    "audio_format": "pcm_s16le", "sample_rate": 16000, "num_channels": 1,
                    "enable_language_identification": True, "enable_endpoint_detection": True,
                    "max_endpoint_delay_ms": max(500, min(3000, self.config.soniox_max_endpoint_delay_ms)),
                    "language_hints": [self.source_lang],
                }
                config["translation"] = {"type": "one_way", "target_language": self.target_lang}
                await socket.send(json.dumps(config))

                async def send_audio():
                    while True:
                        try:
                            chunk = await asyncio.wait_for(self.audio.get(), timeout=4)
                        except asyncio.TimeoutError:
                            await socket.send(json.dumps({"type": "keepalive"}))
                            continue
                        if chunk is None:
                            await socket.send("")
                            return
                        self.queued_audio_bytes -= len(chunk)
                        await socket.send(chunk)
                sender = asyncio.create_task(send_audio())
                while not self.closed:
                    if sender.done() and sender.exception():
                        raise sender.exception()
                    # Caption packets may arrive continuously. Enforce the
                    # clause deadline even when recv() never times out.
                    if phrase_due is not None and time.monotonic() >= phrase_due:
                        phrase = self.buffer.phrase(allow_fragment=True)
                        if phrase:
                            self.on_phrase(phrase, self.buffer.turn_id)
                        phrase_due = None
                    deadlines = [due for due in (finish_due, phrase_due) if due is not None]
                    timeout = max(0.01, min(deadlines) - time.monotonic()) if deadlines else 1.0
                    try:
                        response = json.loads(await asyncio.wait_for(socket.recv(), timeout=timeout))
                    except asyncio.TimeoutError:
                        if finish_due and time.monotonic() >= finish_due:
                            await self._finish()
                            finish_due = None
                            phrase_due = None
                        continue
                    if response.get("error_code") is not None or response.get("error_type"):
                        raise RuntimeError("Soniox rejected the stream. Check credentials, model and concurrency quota.")
                    caption, ended = self.buffer.feed(response.get("tokens") or [])
                    if caption["original_text"] or caption["localized_text"]:
                        self.on_caption(caption)
                    phrase = self.buffer.phrase()
                    if phrase:
                        self.on_phrase(phrase, self.buffer.turn_id)
                        phrase_due = None
                    if phrase_due is None and len(self.buffer.text()[self.buffer.spoken:].strip()) >= 32:
                        phrase_due = time.monotonic() + self.config.speech_clause_wait_ms / 1000
                    if ended or finish_due:
                        # Translation can trail its source endpoint in the next packet.
                        finish_due = time.monotonic() + 0.18
                    if response.get("finished"):
                        await self._finish()
                        return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - Report sanitized provider failure and always clean up.
            self.on_error("Soniox stream unavailable. Check the API key, credits and active-stream limit, then reconnect.")
        finally:
            self.closed = True
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
