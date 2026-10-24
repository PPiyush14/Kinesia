"""Conservative interruption evidence. A voice detector alone never takes the floor."""
from __future__ import annotations

import re


class SpeechActivity:
    def __init__(self):
        self.active = False
        self.started = 0.0
        self.audio_start_ms = 0.0
        self.last_text = ""
        self.turn_id = None
        self.baseline = ""
        self.taken = False
        self.ended = 0.0

    def voice(self, active, audio_ms, now):
        if active and not self.active:
            self.started, self.audio_start_ms = now, audio_ms
            self.baseline, self.taken = self.last_text, False
        if not active and self.active:
            self.ended = now
        self.active = active

    def observe(self, caption, audio_ms, now):
        text = str(caption.get("original_text") or "")
        turn_id = caption.get("turn_id")
        if turn_id != self.turn_id:
            self.turn_id, self.baseline = turn_id, ""
        self.last_text = text
        current = text.removeprefix(self.baseline)
        # Short acknowledgements are still translated/spoken; they just do not
        # delete another person's audio. No word (including 'hmm') is blacklisted.
        substantial = len(re.sub(r"[\W_]+", "", current)) >= 10 or len(current.split()) >= 3
        end = caption.get("audio_end_ms")
        fresh = isinstance(end, (int, float)) and end >= self.audio_start_ms - 100 and audio_ms - end <= 1500
        return bool(self.active and now - self.started >= 0.55 and substantial and fresh)
