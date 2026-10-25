"""Opt-in synthetic Groq recap check. Never reads conversations or connects to the DB."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import load_config
from backend.conversation_recap import GroqRecapGenerator, RecapError

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the configured Groq API for a small synthetic request.")
    parser.add_argument("--language", default="hi")
    parser.add_argument("--source-language", choices=("en", "hi", "mixed"), default="en")
    parser.add_argument("--repeat", type=int, choices=(1, 4, 12), default=1, help="Repeat synthetic input to exercise multi-batch quota pacing.")
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
    if arguments.source_language == "hi":
        turns[0]["original_text"] = "हाँ ठीक है। कल शाम पाँच बजे मिलते हैं। नहीं, मेरा मतलब छह बजे था, पाँच नहीं।"
        turns[1]["original_text"] = "हम्म हाँ छह बजे ठीक है। मैं चाय लेकर आऊँगा लेकिन कॉफी नहीं लाऊँगा। तुम नीली किताब लाना मत भूलना।"
    elif arguments.source_language == "mixed":
        turns[0]["original_text"] = "Mujhe lagta hai kal six baje milna chahiye, five nahi. Budget fifteen hundred hai, fifty thousand nahi."
        turns[1]["original_text"] = "हाँ, छह बजे। I will bring the blue notebook, but I cannot book the tickets yet."
    turns = [{**turn, "id": f"{turn['id']}-{index}"} for index in range(arguments.repeat) for turn in turns]
    started = time.monotonic()
    try:
        generator = GroqRecapGenerator(config)
        generator.on_progress = lambda retry_at, message: print(json.dumps({
            "phase": "waiting" if retry_at else "generating", "retry_at": retry_at,
            "message": message}), flush=True)
        result = generator.generate(turns, arguments.language)
        print(json.dumps({"ok": True, "language": arguments.language, "model": config.groq_recap_model,
                          "source_language": arguments.source_language,
                          "synthetic_turns": len(turns),
                          "duration_ms": round((time.monotonic() - started) * 1000),
                          "source_quotes_validated": True, "synthetic_recap": result}, ensure_ascii=True))
    except Exception as exc:  # noqa: BLE001 - Report sanitized probe status without credentials.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "error": str(exc) if isinstance(exc, RecapError) else "Provider check failed.",
                          "retry_at": getattr(exc, "retry_at", None),
                          "hint": "Check the separate Groq key, model and free-tier quota."}))
        raise SystemExit(1)
