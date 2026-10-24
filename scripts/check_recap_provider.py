"""Opt-in synthetic Groq recap check. Never reads conversations or connects to the DB."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import load_config
from backend.conversation_recap import GroqRecapGenerator

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the configured Groq API for a small synthetic request.")
    parser.add_argument("--language", default="hi")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("Pass --live to authorize the synthetic provider request.")
    config = load_config()
    turns = [
        {"id": "synthetic-one", "speaker_name": "Alex",
         "original_text": "Let us meet tomorrow at five. Sorry, I mean six in the evening, not five."},
        {"id": "synthetic-two", "speaker_name": "Sam",
         "original_text": "Hmm, yes, six works. I will bring tea. I will not bring coffee."},
    ]
    started = time.monotonic()
    try:
        result = GroqRecapGenerator(config).generate(turns, arguments.language)
        print(json.dumps({"ok": True, "language": arguments.language, "model": config.groq_recap_model,
                          "duration_ms": round((time.monotonic() - started) * 1000),
                          "source_quotes_validated": True, "synthetic_recap": result}, ensure_ascii=True))
    except Exception as exc:  # noqa: BLE001 - only sanitized errors, never request headers or provider bodies.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "hint": "Check the separate Groq key, model and free-tier quota."}))
        raise SystemExit(1)
