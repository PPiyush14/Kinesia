from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from backend.nlp.extractive_summary import ExtractiveSummarizer
from backend.realtime_providers import CartesiaStream, TranscriptBuffer


def token(text, *, final=True, translation=False, language="en", start=0, end=500):
    return {"text": text, "is_final": final, "translation_status": "translation" if translation else "original",
            "language": language, "start_ms": start, "end_ms": end, "confidence": 0.9}


def test_partial_tokens_only_update_captions():
    buffer = TranscriptBuffer("en", "hi")
    caption, _ = buffer.feed([token("Hello", final=False), token("नमस्ते", final=False, translation=True)])
    assert caption["original_text"] == "Hello"
    assert caption["localized_text"] == "नमस्ते"
    assert buffer.phrase(final=True) == ""
    assert buffer.finish().original == ""


def test_only_new_finalized_translation_is_spoken_once():
    buffer = TranscriptBuffer("en", "hi")
    buffer.feed([token("Hello everyone. "), token("सभी को नमस्कार। ", translation=True, language="hi")])
    assert buffer.phrase() == "सभी को नमस्कार।"
    assert buffer.phrase() == ""
    buffer.feed([token("We meet tomorrow."), token("हम कल मिलेंगे।", translation=True, language="hi")])
    assert buffer.phrase(final=True) == "हम कल मिलेंगे।"
    final = buffer.finish()
    assert final.original == "Hello everyone. We meet tomorrow."
    assert final.translated == "सभी को नमस्कार। हम कल मिलेंगे।"
    assert buffer.phrase(final=True) == ""


def test_repeated_sentences_are_distinct_stream_turns():
    buffer = TranscriptBuffer("en", "en")
    results = []
    for _ in range(2):
        buffer.feed([token("very very good")])
        assert buffer.phrase(final=True) == "very very good"
        results.append(buffer.finish())
    assert results[0].turn_id != results[1].turn_id


def test_code_switching_does_not_obey_language_hint_as_a_translation_bypass():
    buffer = TranscriptBuffer("en", "en")
    buffer.feed([token("नमस्ते", language="hi"), token("Hello", translation=True)])
    assert buffer.phrase(final=True) == "Hello"
    buffer = TranscriptBuffer("en", "hi")
    buffer.feed([token("नमस्ते", language="hi")])
    assert buffer.phrase(final=True) == "नमस्ते"


def test_source_audio_offsets_preserve_language_alignment():
    buffer = TranscriptBuffer("en", "hi", offset_ms=1200)
    buffer.feed([token("Hello", start=100, end=800)])
    result = buffer.finish()
    assert (result.start_ms, result.end_ms) == (1300, 2000)


class Socket:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []
        self.closed = False
    async def send(self, value): self.sent.append(json.loads(value))
    async def recv(self): return json.dumps(next(self.responses))
    async def close(self): self.closed = True


def test_cartesia_streams_before_done_and_reuses_its_socket(sample_config):
    async def run():
        socket = Socket([
            {"type": "chunk", "data": base64.b64encode(b"\1\0\2\0").decode()}, {"type": "done", "done": True},
            {"type": "chunk", "data": base64.b64encode(b"\3\0").decode()}, {"type": "done", "done": True},
        ])
        connections, chunks = [], []
        async def connect(*args, **kwargs): connections.append(kwargs); return socket
        async def chunk(data, sequence, rate): chunks.append((data, sequence))
        client = CartesiaStream(replace(sample_config, cartesia_voice_map={"hi": "test-voice", "en": "test-voice"}))
        with patch("backend.realtime_providers.connect", side_effect=connect):
            await client.synthesize("one", "hi", "first", chunk)
            await client.synthesize("two", "hi", "second", chunk)
        assert len(connections) == 1
        assert chunks == [(b"\1\0\2\0", 0), (b"\3\0", 0)]
        assert socket.sent[0]["context_id"] != socket.sent[1]["context_id"]
        assert "X-API-Key" in connections[0]["additional_headers"]
        await client.close()
        assert socket.closed
    asyncio.run(run())


def test_cartesia_error_is_not_retried_or_replaced_by_another_provider(sample_config):
    async def run():
        socket = Socket([{"type": "error", "status_code": 400, "message": "invalid voice"}])
        client = CartesiaStream(replace(sample_config, cartesia_voice_map={"hi": "test-voice", "en": "test-voice"}))
        async def connect(*args, **kwargs): return socket
        async def chunk(*args): pytest.fail("failed synthesis produced audio")
        with patch("backend.realtime_providers.connect", side_effect=connect) as connection:
            with pytest.raises(RuntimeError): await client.synthesize("hello", "en", "context", chunk)
            assert connection.call_count == 1
        assert socket.closed
    asyncio.run(run())


def test_extractive_notes_preserve_quotes_and_do_not_invent_tasks():
    summary = ExtractiveSummarizer().summarize("session", [
        {"transcript": "Hello everyone.", "speaker_name": "A", "source_lang": "en"},
        {"transcript": "The revenue was 250 rupees, not 500.", "speaker_name": "B", "source_lang": "en"},
    ])
    assert summary.action_items == []
    assert all(point in {"Hello everyone.", "The revenue was 250 rupees, not 500."} for point in summary.key_points)
    result = ExtractiveSummarizer().summarize("session", [{"transcript": "I will send the report on Friday."}])
    assert result.action_items == ["I will send the report on Friday."]
    negated = ExtractiveSummarizer().summarize("session", [{"transcript": "We will not send the report. No action items today."}])
    assert negated.action_items == []
