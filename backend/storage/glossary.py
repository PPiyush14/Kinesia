"""Custom translation glossary management.

Allows users to define terms that must either be preserved as-is
(e.g., proper nouns, brands, product names) or mapped to a specific target term.
Matched glossary terms are dynamically injected into the Gemma translation prompt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.core.exceptions import StorageError

logger = logging.getLogger(__name__)

DEFAULT_GLOSSARY_FILENAME = "glossary.json"


@dataclass
class GlossaryEntry:
    """A single glossary rule."""

    term: str
    target: str | None = None  # If specified, translate to this target; if None, preserve unchanged
    preserve: bool = True  # If True, must not be translated (kept as original)

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "target": self.target,
            "preserve": self.preserve,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlossaryEntry:
        return cls(
            term=str(data.get("term") or "").strip(),
            target=data.get("target") or None,
            preserve=bool(data.get("preserve", True)),
        )


class GlossaryStore:
    """Reads and writes glossary definitions from ``glossary.json``."""

    def __init__(self, project_root: Path, filename: str = DEFAULT_GLOSSARY_FILENAME) -> None:
        self.file_path = project_root / filename

    def load_entries(self) -> list[GlossaryEntry]:
        """Load all glossary entries from disk. Returns empty list if file missing."""
        if not self.file_path.exists():
            return []
        try:
            raw = self.file_path.read_text(encoding="utf-8").strip()
            if not raw:
                return []
            data = json.loads(raw)
            items = data.get("glossary", []) if isinstance(data, dict) else data
            if not isinstance(items, list):
                return []
            entries = []
            for item in items:
                if isinstance(item, dict) and item.get("term"):
                    entries.append(GlossaryEntry.from_dict(item))
            return entries
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load glossary from %s: %s", self.file_path, exc)
            return []

    def save_entries(self, entries: list[GlossaryEntry] | list[dict[str, Any]]) -> None:
        """Save glossary entries to disk."""
        serialized = []
        for e in entries:
            if isinstance(e, GlossaryEntry):
                serialized.append(e.to_dict())
            elif isinstance(e, dict) and e.get("term"):
                serialized.append(GlossaryEntry.from_dict(e).to_dict())

        payload = {"glossary": serialized}
        try:
            self.file_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            raise StorageError(f"Failed to save glossary to {self.file_path}: {exc}") from exc

    def find_matched_entries(self, text: str) -> list[GlossaryEntry]:
        """Find entries from the glossary that appear in the given text."""
        entries = self.load_entries()
        if not entries or not text:
            return []

        text_lower = text.lower()
        matched: list[GlossaryEntry] = []
        for entry in entries:
            if entry.term.lower() in text_lower:
                matched.append(entry)
        return matched

    @staticmethod
    def build_prompt_hint(matched_entries: list[GlossaryEntry]) -> str:
        """Format matched entries into instructions for the LLM system prompt."""
        if not matched_entries:
            return ""

        preserve_terms = [e.term for e in matched_entries if e.preserve or not e.target]
        mapping_terms = [
            f"'{e.term}' -> '{e.target}'"
            for e in matched_entries
            if e.target and not e.preserve
        ]

        hints: list[str] = []
        if preserve_terms:
            hints.append(f"Preserve these terms exactly as-is: {', '.join(preserve_terms)}.")
        if mapping_terms:
            hints.append(f"Use these exact translations: {', '.join(mapping_terms)}.")

        return " ".join(hints)
