"""Opt-in synthetic Groq recap check. Never reads conversations or connects to the DB."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import load_config
from backend.conversation_recap import GroqRecapGenerator, RecapError

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the configured Groq API for a small synthetic request.")
    parser.add_argument("--language", default="hi")
    parser.add_argument("--source-language", choices=("en", "hi", "mixed"), default="en")
    parser.add_argument("--repeat", type=int, choices=(1, 4, 12), default=1, help="Repeat synthetic input to exercise multi-batch quota pacing.")
    parser.add_argument("--suite", action="store_true", help="Also test acknowledgements, fragmented speech and multiple topics.")
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
    cases = [("corrections", turns)]
    if arguments.suite:
        examples = {
            "acknowledgements": ["Hmm.", "हाँ, ठीक है।", "Okay."],
            "fragmented": ["पाँच बजे...", "नहीं, मेरा मतलब छह बजे था।", "Hmm, yes.",
                           "Tickets? I cannot book them yet.", "मैं चाय लाऊँगा, कॉफी नहीं।"],
            "multiple_topics": ["I liked the film yesterday.", "The train leaves at six, not five.",
                                "मुझे बारिश पसंद है।", "I will bring tea tomorrow.",
                                "The book is blue, not green.", "I do not know who won the game.",
                                "अगले हफ्ते शायद यात्रा करूँ, अभी पक्का नहीं है।", "Do we need to buy tickets?"],
        }
        cases.extend((name, [{"id": f"synthetic-{name}-{index}", "speaker_name": "Alex" if index % 2 == 0 else "Sam",
                              "original_text": text} for index, text in enumerate(texts)]) for name, texts in examples.items())
    calls = []
    def observe_response(response):
        if response.status_code < 400:
            return
        response.read()
        try:
            error = response.json().get("error", {})
            code = error.get("code")
            failed = error.get("failed_generation")
            shape = {}
            if isinstance(failed, str):
                try:
                    value = json.loads(failed)
                    for key in ("summary", "highlights", "follow_ups", "open_questions"):
                        points = [value[key]] if key == "summary" else value[key]
                        shape[key] = [{"text_length": len(point.get("text", "")),
                                       "reference_count": len(point.get("source_refs", []))} for point in points]
                except (ValueError, KeyError, TypeError, AttributeError):
                    shape = {"parseable": False, "characters": len(failed)}
        except (ValueError, AttributeError):
            code = None
            shape = {}
        print(json.dumps({"provider_status": response.status_code, "provider_code": code
                          if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) <= 64 else None,
                          "failed_output_shape": shape}), flush=True)
    def client_factory(**kwargs):
        return httpx.Client(event_hooks={"request": [lambda _request: calls.append(1)], "response": [observe_response]}, **kwargs)
    generator = GroqRecapGenerator(config, client_factory=client_factory)
    generator.on_progress = lambda retry_at, message: print(json.dumps({
        "phase": "waiting" if retry_at else "generating", "retry_at": retry_at,
        "message": message}), flush=True)
    failures = 0
    for name, case_turns in cases:
        started, before = time.monotonic(), len(calls)
        try:
            result = generator.generate(case_turns, arguments.language)
            print(json.dumps({"ok": True, "case": name, "language": arguments.language, "model": config.groq_recap_model,
                              "synthetic_turns": len(case_turns), "provider_requests": len(calls) - before,
                              "duration_ms": round((time.monotonic() - started) * 1000),
                              "source_quotes_validated": True, "synthetic_recap": result}, ensure_ascii=True), flush=True)
        except Exception as exc:  # noqa: BLE001 - Report sanitized probe status without credentials.
            failures += 1
            print(json.dumps({"ok": False, "case": name, "error_type": type(exc).__name__,
                              "error": str(exc) if isinstance(exc, RecapError) else "Provider check failed.",
                              "retry_at": getattr(exc, "retry_at", None),
                              "hint": "Check the separate Groq key, model and free-tier quota."}), flush=True)
    raise SystemExit(bool(failures))
