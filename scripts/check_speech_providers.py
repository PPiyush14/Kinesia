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


async def check(target):
    config = load_config()
    if _is_missing(config.soniox_api_key) or _is_missing(config.cartesia_api_key):
        print(json.dumps({"ok": False, "error": "Configure the two backend API keys first."}))
        return 1
    cartesia, pcm, captions, segments, errors = CartesiaStream(config), [], [], [], []
    started = time.monotonic()
    async def collect(data, sequence, rate):
        pcm.append(data)
    try:
        await cartesia.synthesize("Hello. Our meeting starts tomorrow at nine.", "en", "kinesia-check-" + str(time.time_ns()), collect)
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
        stream = SonioxStream(config, "en", target, 0, captions.append, lambda *args: None, final, errors.append)
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
        translated_audio = []
        async def translated_chunk(data, sequence, rate):
            translated_audio.append(data)
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
        await cartesia.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use a few seconds of provider credits.")
    parser.add_argument("--language", default="hi")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("Pass --live to authorize the synthetic provider calls.")
    raise SystemExit(asyncio.run(check(arguments.language)))
