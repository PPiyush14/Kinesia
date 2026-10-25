"""Unit tests for GlossaryStore and term matching."""

from __future__ import annotations

from backend.storage.glossary import GlossaryEntry, GlossaryStore


def test_glossary_save_and_load(glossary_store: GlossaryStore) -> None:
    entries = [
        GlossaryEntry(term="TechCorp", preserve=True),
        GlossaryEntry(term="Project Alpha", preserve=True),
        GlossaryEntry(term="Dr. Sharma", target="डॉ. शर्मा", preserve=False),
    ]
    glossary_store.save_entries(entries)

    loaded = glossary_store.load_entries()
    assert len(loaded) == 3
    assert loaded[0].term == "TechCorp"
    assert loaded[0].preserve is True
    assert loaded[2].target == "डॉ. शर्मा"


def test_glossary_term_matching(glossary_store: GlossaryStore) -> None:
    entries = [
        GlossaryEntry(term="TechCorp", preserve=True),
        GlossaryEntry(term="Acme Inc", preserve=True),
    ]
    glossary_store.save_entries(entries)

    sentence = "We met with TechCorp representatives today."
    matched = glossary_store.find_matched_entries(sentence)

    assert len(matched) == 1
    assert matched[0].term == "TechCorp"

    hint = glossary_store.build_prompt_hint(matched)
    assert "Preserve these terms exactly as-is: TechCorp." in hint
