"""Protocol regressions for finite Soniox audio clips (no external services)."""
from __future__ import annotations

import json
import threading
from unittest.mock import patch
from unittest.mock import MagicMock

import pytest
from websockets import ConnectionClosedOK

from backend.soniox_client import SonioxClient


class ClipSocket:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []
        self.ended = threading.Event()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, message):
        self.sent.append(message)
        if message == "":
            self.ended.set()

    def recv(self, timeout=None):
        assert timeout is not None and timeout > 0
        assert self.ended.wait(2), "Sender never ended the audio stream"
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response)

    def close(self):
        self.closed = True


def token(text, final=True, language="hi"):
    return {"text": text, "is_final": final, "language": language, "confidence": 0.9}


def test_clip_waits_for_finished_and_preserves_all_final_segments(sample_config):
    socket = ClipSocket([
        {"tokens": [token("wrong hypothesis", False)]},
        {"tokens": [token("नमस्ते"), token("<end>"), token(" दुनिया")]},
        {"tokens": [token("<fin>"), token("!"), token("stale guess", False)]},
        {"tokens": [], "finished": True},
    ])
    with patch("backend.soniox_client.connect", return_value=socket):
        result = SonioxClient(sample_config).transcribe_pcm16(b"\0\0" * 5000, session_language_hint="hi")
    assert result.transcript == "नमस्ते दुनिया!"
    assert result.detected_language == "hi"
    assert socket.closed
    assert socket.sent[-1] == ""  # Text, not an empty binary frame or finalize.
    config = json.loads(socket.sent[0])
    assert config["enable_endpoint_detection"] is False
    assert config["language_hints"] == ["hi"]
    assert b"".join(message for message in socket.sent if isinstance(message, bytes)) == b"\0\0" * 5000


def test_clip_does_not_promote_partial_tokens_on_finish(sample_config):
    socket = ClipSocket([{"tokens": [token("unconfirmed", False)], "finished": True}])
    with patch("backend.soniox_client.connect", return_value=socket):
        assert SonioxClient(sample_config)._transcribe_pcm16_once(b"\0\0").transcript == ""


def test_clip_rejects_early_close_instead_of_publishing_truncated_text(sample_config):
    socket = ClipSocket([{"tokens": [token("incomplete")]}, ConnectionClosedOK(None, None)])
    with patch("backend.soniox_client.connect", return_value=socket):
        with pytest.raises(RuntimeError, match="before confirming"):
            SonioxClient(sample_config)._transcribe_pcm16_once(b"\0\0")
    assert socket.closed


def test_clip_timeout_closes_socket(sample_config):
    socket = ClipSocket([])
    with patch("backend.soniox_client.connect", return_value=socket), \
         patch("backend.soniox_client.time.monotonic", side_effect=[0, 31]):
        with pytest.raises(TimeoutError, match="in time"):
            SonioxClient(sample_config)._transcribe_pcm16_once(b"\0\0")
    assert socket.closed


def test_provider_error_closes_socket(sample_config):
    socket = ClipSocket([{"error_code": 401, "error_message": "Unauthorized"}])
    with patch("backend.soniox_client.connect", return_value=socket):
        with pytest.raises(RuntimeError, match="Soniox error 401"):
            SonioxClient(sample_config)._transcribe_pcm16_once(b"\0\0")
    assert socket.closed


def test_live_parser_consumes_tokens_after_an_endpoint_in_same_response(sample_config):
    client = SonioxClient(sample_config)
    final = []
    result, interim = client._parse_tokens([token("hello"), token("<end>"), token(" world")], final)
    assert result == "hello world"
    assert interim == "hello world"
    assert client._render_tokens(final) == "hello world"


def test_live_parser_does_not_finalize_a_partial_endpoint(sample_config):
    client = SonioxClient(sample_config)
    final = []
    result, interim = client._parse_tokens([token("guess", False), token("<end>", False)], final)
    assert result is None
    assert final == []


def test_empty_finalization_releases_live_utterance_boundary(sample_config):
    client = SonioxClient(sample_config)
    result, _ = client._parse_tokens([token("<fin>")], [])
    assert result == ""  # None would leave utterance_pause_event set forever.


def test_live_stop_watcher_exits_when_connection_finishes(sample_config):
    client = SonioxClient(sample_config)
    recorder = MagicMock(sample_rate=16000, channels=1)
    recorder.resolve_input_sample_rate.return_value = 16000
    recorder.stream_continuous.side_effect = lambda **kwargs: kwargs["stop_event"].wait(2)
    socket = MagicMock()
    socket.__enter__.return_value = socket
    socket.recv.return_value = json.dumps({"finished": True})
    threads = []
    thread_type = threading.Thread
    def thread(*args, **kwargs):
        instance = thread_type(*args, **kwargs)
        threads.append(instance)
        return instance
    stop = threading.Event()
    try:
        with patch("backend.soniox_client.connect", return_value=socket), \
             patch("backend.soniox_client.threading.Thread", side_effect=thread):
            assert list(client._stream_microphone_session_once(
                recorder, next(iter(sample_config.channels.values())), stop_event=stop
            )) == []
        assert len(threads) == 3
        assert all(not instance.is_alive() for instance in threads)
    finally:
        stop.set()


def test_native_translation_tokens_never_pollute_original_transcript(sample_config):
    original = token("Hello everyone.", language="en")
    translation = {**token("सभी को नमस्कार।"), "translation_status": "translation"}
    partial = {**token("guess", False), "translation_status": "translation"}
    socket = ClipSocket([{"tokens": [original, translation, partial]}, {"finished": True}])
    with patch("backend.soniox_client.connect", return_value=socket):
        result = SonioxClient(sample_config).transcribe_pcm16(b"\0\0" * 1000, target_language="hi")
    assert result.transcript == "Hello everyone."
    assert result.translation == "सभी को नमस्कार।"
    assert json.loads(socket.sent[0])["translation"] == {"type": "one_way", "target_language": "hi"}
