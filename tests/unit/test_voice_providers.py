"""Translation output and Edge TTS lifecycle regressions."""
from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from backend.core.exceptions import TranslationError
from backend.edge_tts_client import EdgeTtsClient
from backend.gemma_client import GemmaClient
from backend.tts_client import TtsClient


def response(client, content, finish="stop"):
    reply = MagicMock(ok=True)
    reply.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    client.session.post = MagicMock(return_value=reply)


def test_null_translation_is_empty_instead_of_spoken_none(sample_config):
    client = GemmaClient(sample_config)
    response(client, None)
    channel = next(iter(sample_config.channels.values()))
    with pytest.raises(TranslationError, match="empty"):
        client.translate("Hello", channel, channel, "en", "hi")


def test_reasoning_is_hidden_for_groq_qwen_and_never_returned_as_speech(sample_config):
    config = replace(sample_config, gemma_response_base_url="https://api.groq.com/openai/v1", gemma_model="qwen/qwen3.8-27b")
    client = GemmaClient(config)
    response(client, "<think>Internal model deliberation</think>नमस्ते")
    assert client.chat_completion([]) == "नमस्ते"
    assert client.session.post.call_args.kwargs["json"]["reasoning_format"] == "hidden"
    response(client, "<think>Unfinished internal deliberation")
    assert client.chat_completion([]) == ""


def test_non_groq_provider_keeps_compatible_payload(sample_config):
    client = GemmaClient(sample_config)
    response(client, [{"type": "text", "text": "नमस्ते"}])
    assert client.chat_completion([]) == "नमस्ते"
    assert "reasoning_format" not in client.session.post.call_args.kwargs["json"]


def test_truncated_translation_is_not_sent_to_tts(sample_config):
    client = GemmaClient(sample_config)
    response(client, "incomplete sentence", finish="length")
    with pytest.raises(TranslationError, match="truncated"):
        client.chat_completion([])


def test_translation_timeout_is_reported(sample_config):
    client = GemmaClient(sample_config)
    client.session.post = MagicMock(side_effect=requests.Timeout("timed out"))
    with pytest.raises(TranslationError, match="request failed"):
        client.chat_completion([])


def edge_provider(chunks=None, error=None):
    communicate = MagicMock()
    async def stream():
        if error:
            raise error
        for chunk in chunks or []:
            yield chunk
    communicate.return_value.stream = stream
    return SimpleNamespace(Communicate=communicate)


def test_edge_audio_chunks_are_concatenated_once_in_order(sample_config):
    provider = edge_provider([
        {"type": "audio", "data": b"first"},
        {"type": "WordBoundary", "data": b"ignored"},
        {"type": "audio", "data": b"second"},
    ])
    channel = next(iter(sample_config.channels.values()))
    with patch.dict(sys.modules, {"edge_tts": provider}):
        result = EdgeTtsClient(sample_config).synthesize_for_language("नमस्ते", channel, "hi", gender="male")
    assert result == b"firstsecond"
    provider.Communicate.assert_called_once_with("नमस्ते", "hi-IN-MadhurNeural")


def test_edge_runtime_error_does_not_repeat_provider_request(sample_config):
    provider = edge_provider(error=RuntimeError("provider failed"))
    channel = next(iter(sample_config.channels.values()))
    with patch.dict(sys.modules, {"edge_tts": provider}):
        assert EdgeTtsClient(sample_config).synthesize("Hello", channel) == b""
    assert provider.Communicate.call_count == 1


def test_edge_from_running_event_loop_uses_and_closes_an_independent_loop(sample_config):
    provider = edge_provider([{"type": "audio", "data": b"speech"}])
    channel = next(iter(sample_config.channels.values()))
    async def synthesize():
        return EdgeTtsClient(sample_config).synthesize("Hello", channel)
    with patch.dict(sys.modules, {"edge_tts": provider}):
        assert asyncio.run(synthesize()) == b"speech"
    assert provider.Communicate.call_count == 1


def test_edge_timeout_is_bounded_and_not_retried(sample_config):
    provider = edge_provider()
    channel = next(iter(sample_config.channels.values()))
    async def timeout(coroutine, *, timeout):
        assert timeout == 15
        coroutine.close()
        raise asyncio.TimeoutError()
    with patch.dict(sys.modules, {"edge_tts": provider}), patch("backend.edge_tts_client.asyncio.wait_for", side_effect=timeout):
        assert EdgeTtsClient(sample_config).synthesize("Hello", channel) == b""


def test_edge_router_does_not_retry_the_same_provider_on_empty_audio(sample_config):
    client = TtsClient(replace(sample_config, tts_provider="edge"))
    client._edge.synthesize = MagicMock(return_value=b"")
    assert client.synthesize("Hello", next(iter(sample_config.channels.values()))) == b""
    client._edge.synthesize.assert_called_once()
