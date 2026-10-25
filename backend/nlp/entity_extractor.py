"""Named entity recognition for multilingual conversation transcripts.

Uses spaCy's multilingual NER model (``xx_ent_wiki_sm``) to extract entities
from transcript and translation text after each conversation turn.

The model is loaded once at class instantiation and reused across all turns
to avoid repeated disk I/O and model initialisation overhead.
"""

from __future__ import annotations

import logging
from typing import Final

from backend.core.exceptions import EntityExtractionError

logger = logging.getLogger(__name__)

# Entity types we surface to the user and store in the turn JSON.
# We deliberately omit low-signal types (MISC, NORP, ORDINAL, etc.) to keep
# the output focused and meaningful.
SURFACED_ENTITY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "PER",      # Person names  (xx model uses PER not PERSON)
        "PERSON",   # Person names  (en model)
        "ORG",      # Organisations / companies
        "GPE",      # Geopolitical entities (countries, cities, states)
        "LOC",      # Non-GPE locations (rivers, mountains)
        "DATE",     # Absolute or relative dates ("Friday", "2024")
        "TIME",     # Times of day
        "MONEY",    # Monetary values with currency
        "PRODUCT",  # Products, objects, services
        "EVENT",    # Named events
    }
)

# Canonical output keys — normalise PER → PERSON so callers see one schema.
_TYPE_ALIASES: Final[dict[str, str]] = {"PER": "PERSON"}


class EntityExtractor:
    """Extracts named entities from multilingual text using spaCy.

    The underlying model (``xx_ent_wiki_sm``) supports ~100 languages and is
    suitable for the Indian-language + English code-switched text this system
    handles. For purely English text the model still performs well; for
    high-accuracy English-only use, swap ``en_core_web_sm`` instead.

    Example::

        extractor = EntityExtractor()
        result = extractor.extract("Let's meet at TechCorp on Friday at 3 PM.")
        # {"PERSON": [], "ORG": ["TechCorp"], "DATE": ["Friday"], "TIME": ["3 PM"], ...}
    """

    _MODEL_NAME: Final[str] = "xx_ent_wiki_sm"

    def __init__(self) -> None:
        self._nlp = self._load_model()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, text: str) -> dict[str, list[str]]:
        """Extract named entities from *text*.

        Runs spaCy NER on the provided text and returns a dict mapping each
        surfaced entity type to a de-duplicated, ordered list of entity strings.
        If the model produces no entities the dict is empty (not None).

        Args:
            text: Raw transcript or translation text to analyse. May be
                multilingual or code-switched.

        Returns:
            Mapping of entity type label to list of unique entity strings.
            Only types in ``SURFACED_ENTITY_TYPES`` are included.
            Example::

                {
                    "PERSON": ["Rahul"],
                    "ORG": ["Tata Motors"],
                    "DATE": ["Monday"],
                    "MONEY": ["₹50 lakh"],
                }

        Raises:
            EntityExtractionError: If the spaCy pipeline raises an unexpected
                error during processing.
        """
        text = text.strip()
        if not text:
            return {}

        try:
            doc = self._nlp(text)
        except Exception as exc:
            raise EntityExtractionError(
                f"spaCy NER pipeline failed: {exc}"
            ) from exc

        result: dict[str, list[str]] = {}
        seen: dict[str, set[str]] = {}

        for ent in doc.ents:
            raw_label = ent.label_
            if raw_label not in SURFACED_ENTITY_TYPES:
                continue
            label = _TYPE_ALIASES.get(raw_label, raw_label)
            entity_text = ent.text.strip()
            if not entity_text:
                continue
            if label not in seen:
                seen[label] = set()
                result[label] = []
            if entity_text not in seen[label]:
                seen[label].add(entity_text)
                result[label].append(entity_text)

        logger.debug(
            "entity_extraction_complete",
            extra={"input_chars": len(text), "entity_count": sum(len(v) for v in result.values())},
        )
        return result

    def extract_combined(
        self,
        transcript: str,
        translation: str,
    ) -> dict[str, list[str]]:
        """Extract entities from both transcript and translation, merged.

        Running NER on both the source transcript and the translation gives
        better coverage: entities that the model misses in one language may
        be recognised in the other.

        Args:
            transcript: Source-language transcript text.
            translation: Translated text.

        Returns:
            Merged entity dict with duplicates removed across both runs.

        Raises:
            EntityExtractionError: If either NER run fails.
        """
        from_transcript = self.extract(transcript)
        from_translation = self.extract(translation)

        merged: dict[str, list[str]] = {}
        for src in (from_transcript, from_translation):
            for label, entities in src.items():
                if label not in merged:
                    merged[label] = []
                existing = set(merged[label])
                for entity in entities:
                    if entity not in existing:
                        merged[label].append(entity)
                        existing.add(entity)
        return merged

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _load_model(cls):
        """Load the spaCy model, raising a clear error if not installed."""
        try:
            import spacy
            return spacy.load(cls._MODEL_NAME)
        except OSError as exc:
            raise EntityExtractionError(
                f"spaCy model '{cls._MODEL_NAME}' is not installed. "
                f"Run: python -m spacy download {cls._MODEL_NAME}"
            ) from exc
        except ImportError as exc:
            raise EntityExtractionError(
                "spaCy is not installed. Run: pip install spacy"
            ) from exc
