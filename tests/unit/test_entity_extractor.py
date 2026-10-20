"""Unit tests for EntityExtractor with multilingual NER."""

from __future__ import annotations

import pytest

from backend.nlp.entity_extractor import EntityExtractor


def test_entity_extractor_instantiation() -> None:
    extractor = EntityExtractor()
    assert extractor is not None


def test_entity_extractor_empty_string() -> None:
    extractor = EntityExtractor()
    assert extractor.extract("") == {}
    assert extractor.extract("   ") == {}


def test_entity_extractor_english_entities() -> None:
    extractor = EntityExtractor()
    text = "Rahul visited TechCorp in Mumbai on Friday."
    result = extractor.extract(text)

    # Should detect at least one of the entities with the multilingual model
    all_entities = [item for sublist in result.values() for item in sublist]
    assert len(all_entities) > 0


def test_entity_extractor_combined() -> None:
    extractor = EntityExtractor()
    transcript = "Let's meet at Microsoft tomorrow."
    translation = "आइए कल माइक्रोसॉफ्ट में मिलें।"
    merged = extractor.extract_combined(transcript, translation)

    assert isinstance(merged, dict)
    all_entities = [item for sublist in merged.values() for item in sublist]
    assert len(all_entities) > 0
