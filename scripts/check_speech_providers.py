"""Opt-in paid-provider smoke test with synthetic speech; never touches the DB."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import _is_missing, load_config
from backend.realtime_providers import CartesiaStream, SonioxStream


async def check(target, streaming=False):
    config = load_config()
    if _is_missing(config.soniox_api_key) or _is_missing(config.cartesia_api_key):
        print(json.dumps({"ok": False, "error": "Configure the two backend API keys first."}))
        return 1
    cartesia, pcm, captions, segments, errors = CartesiaStream(config), [], [], [], []
    started = time.monotonic()
    worker = None
    stream = None
    spoken, timings, translated_audio = [], [], []
    async def collect(data, sequence, rate):
        pcm.append(data)
    try:
        sentence = "Hmm, yes. Let us meet tomorrow at nine. Sorry, I mean six, not nine. I will bring tea, but not coffee." if streaming else "Hello. Our meeting starts tomorrow at nine."
        await cartesia.synthesize(sentence, "en", "kinesia-check-" + str(time.time_ns()), collect)
        print(json.dumps({"provider": "cartesia", "ok": True, "audio_bytes": sum(map(len, pcm)),
                          "duration_ms": round((time.monotonic() - started) * 1000)}), flush=True)
        # Convert the generated PCM to the input stream's 16kHz format.
        import numpy as np
        source = np.frombuffer(b"".join(pcm), dtype="<i2")
        rate = next(iter(config.channels.values())).cartesia_sample_rate
        positions = np.arange(0, len(source), rate / 16000)
        audio = np.interp(positions, np.arange(len(source)), source).astype("<i2").tobytes()
        audio += b"\0\0" * 16000
        async def final(segment):
            segments.append(segment)
        phrases = asyncio.Queue()
        def phrase(text, utterance_id):
            spoken.append(text)
            phrases.put_nowait((text, utterance_id, time.monotonic()))
        async def speak():
            active = None
            while True:
                text, utterance_id, queued_at = await phrases.get()
                if utterance_id is None:
                    return
                if text is None:
                    if active == utterance_id:
                        await cartesia.finish_context(active, target)
                        active = None
                    continue
                active = utterance_id
                first = True
                async def chunk(data, sequence, sample_rate, queued_at=queued_at):
                    nonlocal first
                    if first:
                        timings.append(round((time.monotonic() - queued_at) * 1000))
                        first = False
                    translated_audio.append(data)
                await cartesia.synthesize(text, target, utterance_id, chunk, continuation=True)
        stream = SonioxStream(config, "en", target, 0, captions.append, phrase if streaming else lambda *args: None, final, errors.append)
        if streaming:
            stream.on_boundary = lambda identifier: phrases.put_nowait((None, identifier, time.monotonic()))
            worker = asyncio.create_task(speak())
        stream.start()
        for offset in range(0, len(audio), 3200):
            stream.feed(audio[offset:offset + 3200])
            await asyncio.sleep(0.1)
        await stream.flush()
        await stream.close()
        print(json.dumps({"provider": "soniox", "ok": bool(segments) and not errors,
                          "caption_updates": len(captions), "final_segments": len(segments),
                          "translated_segments": sum(bool(segment.translated) for segment in segments),
                          "errors": errors}, ensure_ascii=False), flush=True)
        if errors or not segments or not any(segment.translated for segment in segments):
            return 1
        # These are synthetic test sentences, not private meeting transcripts.
        print(json.dumps({"synthetic_original": " ".join(segment.original for segment in segments),
                          "synthetic_translation": " ".join(segment.translated for segment in segments)}, ensure_ascii=True), flush=True)
        async def translated_chunk(data, sequence, rate):
            translated_audio.append(data)
        if streaming:
            phrases.put_nowait((None, None, time.monotonic()))
            await asyncio.wait_for(worker, 40)
            complete = "".join("".join(segment.translated.split()) for segment in segments)
            emitted = "".join("".join(text.split()) for text in spoken)
            print(json.dumps({"streaming": True, "stable_phrases": len(spoken),
                              "stable_phrase_to_first_pcm_ms": timings,
                              "spoken_text_matches_final_translation": complete == emitted,
                              "note": "Provider pipeline timings; excludes browser playback and Render transport."}), flush=True)
            if complete != emitted:
                return 1
        else:
            await cartesia.synthesize(" ".join(segment.translated for segment in segments), target,
                                      "kinesia-translated-check-" + str(time.time_ns()), translated_chunk)
        print(json.dumps({"provider": "cartesia", "language": target, "ok": True,
                          "audio_bytes": sum(map(len, translated_audio))}), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - report sanitized status only.
        # Do not print provider response bodies, URLs or credential-bearing headers.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "hint": "Check provider keys, credits, model and voice availability."}), flush=True)
        return 1
    finally:
        if worker and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        if stream:
            await stream.close()
        await cartesia.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use a few seconds of provider credits.")
    parser.add_argument("--language", default="hi")
    parser.add_argument("--streaming", action="store_true", help="Exercise stable clauses, continuations, self-corrections and an intentional hmm.")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("Pass --live to authorize the synthetic provider calls.")
    raise SystemExit(asyncio.run(check(arguments.language, arguments.streaming)))
