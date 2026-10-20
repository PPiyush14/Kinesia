"""Session summarization and conversational intelligence extractor.

Uses the existing Gemma LLM client to generate:
1. Executive summary (2-4 sentences)
2. Key discussion points
3. Action items (only if task-oriented; strictly empty list otherwise — zero fabrication)
4. Aggregated entity table across all turns in the session
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.core.exceptions import SummarizationError
from backend.gemma_client import GemmaClient

logger = logging.getLogger(__name__)


@dataclass
class SummaryResult:
    """Structured session intelligence report."""

    session_id: str
    summary: str
    key_points: list[str]
    action_items: list[str]
    entity_table: dict[str, list[str]]
    turn_count: int
    languages_used: list[str]
    generated_at: float = field(default_factory=time.time)
    duration_seconds: float = 0.0

    @property
    def executive_summary(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["executive_summary"] = self.summary
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SummaryResult:
        return cls(
            session_id=str(data.get("session_id") or ""),
            summary=str(data.get("summary") or ""),
            key_points=list(data.get("key_points") or []),
            action_items=list(data.get("action_items") or []),
            entity_table=dict(data.get("entity_table") or {}),
            turn_count=int(data.get("turn_count") or 0),
            languages_used=list(data.get("languages_used") or []),
            generated_at=float(data.get("generated_at") or time.time()),
            duration_seconds=float(data.get("duration_seconds") or 0.0),
        )


class ConversationSummarizer:
    """Generates structured session intelligence using the Gemma LLM client."""

    def __init__(self, gemma_client: GemmaClient) -> None:
        self.gemma = gemma_client

    def summarize(self, session_id: str, turns: list[dict[str, Any]]) -> SummaryResult:
        """Generate an executive summary from conversation turns.

        Args:
            session_id: Session identifier.
            turns: List of turn dictionaries stored in the session JSON.

        Returns:
            Structured SummaryResult.

        Raises:
            SummarizationError: If LLM call or parsing fails unexpectedly.
        """
        if not turns:
            return SummaryResult(
                session_id=session_id,
                summary="No conversation turns were recorded in this session.",
                key_points=[],
                action_items=[],
                entity_table={},
                turn_count=0,
                languages_used=[],
                duration_seconds=0.0,
            )

        # Aggregate entity table across all turns
        entity_table: dict[str, list[str]] = {}
        languages_seen: set[str] = set()
        turn_times: list[float] = []

        formatted_dialogue: list[str] = []
        for idx, turn in enumerate(turns, start=1):
            speaker = turn.get("speaker_name") or turn.get("speaker_channel_name") or turn.get("speaker_channel_key") or "Speaker"
            src_lang = turn.get("source_lang") or turn.get("detected_source_language") or turn.get("speaker_session_language") or "?"
            tgt_lang = turn.get("listening_lang") or turn.get("listener_language") or "?"
            transcript = str(turn.get("transcript") or turn.get("raw_text") or turn.get("text") or "").strip()
            translation = str(turn.get("translation") or turn.get("localized_text") or "").strip()

            if src_lang != "?":
                languages_seen.add(src_lang)
            if tgt_lang != "?":
                languages_seen.add(tgt_lang)

            captured_at = turn.get("captured_at")
            if isinstance(captured_at, (int, float)):
                turn_times.append(float(captured_at))

            # Merge entities
            turn_entities = turn.get("entities")
            if isinstance(turn_entities, dict):
                for label, items in turn_entities.items():
                    if isinstance(items, list):
                        entity_table.setdefault(label, [])
                        for item in items:
                            if item not in entity_table[label]:
                                entity_table[label].append(item)

            if transcript:
                formatted_dialogue.append(
                    f"[Turn {idx}] {speaker} ({src_lang}): {transcript}\n"
                    f"       (Translation into {tgt_lang}): {translation}"
                )

        duration = 0.0
        if len(turn_times) >= 2:
            duration = max(0.0, round(max(turn_times) - min(turn_times), 1))

        if not formatted_dialogue:
            return SummaryResult(
                session_id=session_id,
                summary="Session contains turns with empty transcripts.",
                key_points=[],
                action_items=[],
                entity_table=entity_table,
                turn_count=len(turns),
                languages_used=sorted(languages_seen),
                duration_seconds=duration,
            )

        conversation_text = "\n\n".join(formatted_dialogue)
        prompt_system = (
            "You are an executive conversation intelligence assistant. "
            "Analyze the bilingual conversation transcript and return a structured summary. "
            "Respond strictly with valid JSON only, without markdown fences, conforming to this schema:\n"
            "{\n"
            '  "summary": "2-4 sentence executive overview of what was discussed and concluded",\n'
            '  "key_points": ["Key point 1", "Key point 2", ...],\n'
            '  "action_items": ["Action item 1", ...] (or empty list [] if none were mentioned)\n'
            "}\n"
            "CRITICAL: If no action items or tasks were discussed, 'action_items' MUST be an empty array []. "
            "Do NOT invent or fabricate facts, decisions, or action items."
        )

        messages = [
            {"role": "system", "content": prompt_system},
            {
                "role": "user",
                "content": f"Here is the conversation transcript:\n\n{conversation_text}",
            },
        ]

        try:
            raw_response = self.gemma.chat_completion(
                messages,
                temperature=0.2,
                max_tokens=768,
            )
        except Exception as exc:
            raise SummarizationError(f"Failed to query Gemma for session summary: {exc}") from exc

        parsed = self._parse_json_response(raw_response)

        return SummaryResult(
            session_id=session_id,
            summary=parsed.get("summary") or "Summary could not be generated.",
            key_points=parsed.get("key_points") or [],
            action_items=parsed.get("action_items") or [],
            entity_table=entity_table,
            turn_count=len(turns),
            languages_used=sorted(languages_seen),
            duration_seconds=duration,
        )

    @staticmethod
    def _parse_json_response(response_text: str) -> dict[str, Any]:
        """Safely parse JSON from LLM output, stripping markdown fences if present."""
        text = response_text.strip()
        # Remove markdown code fences if LLM wrapped output
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # Fallback regex extraction if full JSON parse fails
        summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', text)
        summary = summary_match.group(1) if summary_match else text[:300]

        return {
            "summary": summary,
            "key_points": [],
            "action_items": [],
        }
