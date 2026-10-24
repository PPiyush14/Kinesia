"""Grounded meeting notes without a third-party LLM or invented assignments."""
from __future__ import annotations

import re
from collections import Counter

from backend.nlp.summarizer import SummaryResult


class ExtractiveSummarizer:
    def summarize(self, session_id, turns):
        texts = [str(turn.get("transcript") or turn.get("original_text") or "").strip() for turn in turns]
        texts = list(dict.fromkeys(text for text in texts if text))
        words = Counter(word.lower() for text in texts for word in re.findall(r"[^\W\d_]{4,}", text))
        ranked = sorted(enumerate(texts), key=lambda item: sum(words[word.lower()] for word in re.findall(r"[^\W\d_]{4,}", item[1])) / max(1, len(item[1].split())), reverse=True)
        selected = sorted(index for index, _ in ranked[:5])
        points = [texts[index] for index in selected]
        # Exact source quotes only; these are possible commitments, not inferred tasks.
        cues = re.compile(r"\b(?:i will|we will|i'll|we'll|please|follow up|deadline|action item|need to)\b|करूंगा|करूँगा|करेंगे|करूया|करूया|చేస్తాను|செய்வேன்", re.IGNORECASE)
        negated = re.compile(r"\b(?:no action items|no tasks|nothing to do|will not|won't|do not need|don't need)\b", re.IGNORECASE)
        actions = [text for text in texts if cues.search(text) and not negated.search(text)][:10]
        speakers = {turn.get("speaker_channel_name") or turn.get("speaker_name") for turn in turns}
        speakers.discard(None)
        languages = sorted({str(turn.get("detected_source_language") or turn.get("source_lang") or "") for turn in turns} - {""})
        summary = f"Recorded {len(turns)} conversation segments from {len(speakers)} participant(s). "
        summary += "These notes contain excerpts from the transcript; review possible commitments in context." if texts else "No speech was recorded."
        return SummaryResult(session_id, summary, points, actions, {}, len(turns), languages)
