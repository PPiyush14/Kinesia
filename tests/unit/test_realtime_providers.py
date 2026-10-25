from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.nlp.extractive_summary import ExtractiveSummarizer
from backend.realtime_providers import CartesiaStream, SonioxStream, TranscriptBuffer


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


def test_final_sentence_can_start_speech_without_a_trailing_space_or_endpoint():
    buffer = TranscriptBuffer("en", "en")
    buffer.feed([token("Hello everyone.")])
    assert buffer.phrase() == "Hello everyone."
    assert buffer.phrase() == ""
    buffer.feed([token(" We meet tomorrow.")])
    assert buffer.phrase() == "We meet tomorrow."
    assert buffer.phrase(final=True) == ""


def test_short_greeting_does_not_block_the_next_stable_sentence():
    buffer = TranscriptBuffer("en", "en")
    buffer.feed([token("Hi. Welcome to the conversation.")])
    assert buffer.phrase() == "Hi. Welcome to the conversation."


def test_stable_clause_release_never_speaks_partials_or_loses_negation_and_hmm():
    buffer = TranscriptBuffer("en", "en")
    buffer.feed([token("This is only an uncertain partial hypothesis", final=False)])
    assert buffer.phrase(allow_fragment=True) == ""
    original = "Hmm, I do not want to lose these words while speaking a longer sentence without any punctuation"
    buffer.feed([token(original)])
    first = buffer.phrase(allow_fragment=True)
    second = buffer.phrase(final=True)
    assert " ".join((first + " " + second).split()) == original
    assert first.startswith("Hmm") and "not" in first
    assert buffer.phrase(final=True) == ""
    short = TranscriptBuffer("en", "en")
    short.feed([token("Hmm.")])
    assert short.phrase(final=True) == "Hmm."


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


@pytest.mark.parametrize("frame_ms", [40, 100])
def test_soniox_upload_budget_preserves_two_seconds_independent_of_frame_size(sample_config, frame_ms):
    errors = []
    stream = SonioxStream(sample_config, "en", "hi", 0, lambda _: None, lambda *_: None, None, errors.append)
    for _ in range(2000 // frame_ms):
        stream.feed(b"\0" * (32 * frame_ms))
    assert not errors and not stream.closed
    assert stream.queued_audio_bytes == 64000
    stream.feed(b"\0" * (32 * frame_ms))
    assert len(errors) == 1 and stream.closed


@pytest.mark.parametrize("packet_interval", [0.01, 0.05])
def test_stable_clause_deadline_is_not_starved_by_continuous_caption_packets(sample_config, packet_interval):
    async def run():
        clock = [0.0]
        text = "Tomorrow I will bring tea but definitely not coffee"
        responses = [{"tokens": [token(text), token(text, translation=True)]}]
        responses += [{"tokens": [token(" another provisional word", final=False)]}] * round(1 / packet_interval)
        responses += [{"finished": True}]
        class CaptionSocket(Socket):
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            async def recv(self):
                clock[0] += packet_interval
                return await super().recv()
        socket = CaptionSocket(responses)
        phrases, finals, errors = [], [], []
        async def final(segment): finals.append(segment)
        def phrase(value, _identifier): phrases.append((clock[0], value))
        config = replace(sample_config, speech_clause_wait_ms=350)
        stream = SonioxStream(config, "en", "hi", 0, lambda _: None, phrase, final, errors.append)
        with patch("backend.realtime_providers.connect", return_value=socket), \
             patch("backend.realtime_providers.time", SimpleNamespace(monotonic=lambda: clock[0])):
            await stream.run()
        assert not errors
        assert phrases[0][0] <= packet_interval + 0.35 + packet_interval + 0.001
        assert " ".join(value for _, value in phrases) == text
        assert len(finals) == 1 and finals[0].translated == text
    asyncio.run(run())


def test_cartesia_continuations_flush_clauses_without_closing_voice_context(sample_config):
    async def run():
        socket = Socket([
            {"type": "chunk", "data": base64.b64encode(b"\1\0").decode()}, {"type": "flush_done"},
            {"type": "chunk", "data": base64.b64encode(b"\2\0").decode()}, {"type": "flush_done"},
            {"type": "done", "done": True},
        ])
        client = CartesiaStream(replace(sample_config, cartesia_voice_map={"en": "test-voice"}))
        chunks = []
        async def connect(*args, **kwargs): return socket
        async def chunk(data, sequence, rate): chunks.append((data, sequence))
        with patch("backend.realtime_providers.connect", side_effect=connect):
            await client.synthesize("Hmm, yes.", "en", "same-context", chunk, continuation=True)
            await client.synthesize("Let us continue.", "en", "same-context", chunk, continuation=True)
            await client.finish_context("same-context", "en")
        assert chunks == [(b"\1\0", 0), (b"\2\0", 0)]
        assert len({message["context_id"] for message in socket.sent}) == 1
        assert socket.sent[1]["flush"] is True and socket.sent[1]["transcript"] == ""
        assert socket.sent[-1]["continue"] is False
        await client.close()
    asyncio.run(run())


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
