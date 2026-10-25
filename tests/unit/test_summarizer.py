"""Unit tests for ConversationSummarizer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from backend.nlp.summarizer import ConversationSummarizer, SummaryResult


def test_summarizer_empty_turns() -> None:
    mock_gemma = MagicMock()
    summarizer = ConversationSummarizer(mock_gemma)

    res = summarizer.summarize("test_empty", [])
    assert isinstance(res, SummaryResult)
    assert res.turn_count == 0
    assert res.summary == "No conversation turns were recorded in this session."
    assert res.key_points == []
    assert res.action_items == []
    assert not mock_gemma.chat_completion.called


def test_summarizer_with_turns(sample_turns: list[dict]) -> None:
    mock_gemma = MagicMock()
    mock_gemma.chat_completion.return_value = json.dumps({
        "summary": "The teams discussed scheduling a project review with TechCorp.",
        "key_points": ["Meeting scheduled for Friday", "Rahul attending from Mumbai"],
        "action_items": ["Confirm calendar invite"],
    })

    summarizer = ConversationSummarizer(mock_gemma)
    result = summarizer.summarize("test_session_1", sample_turns)

    assert result.session_id == "test_session_1"
    assert result.turn_count == 2
    assert "TechCorp" in result.summary
    assert len(result.key_points) == 2
    assert result.action_items == ["Confirm calendar invite"]

    # Verify entity aggregation from sample_turns
    assert "TechCorp" in result.entity_table.get("ORG", [])
    assert "मुंबई" in result.entity_table.get("LOC", [])


def test_summarizer_parses_markdown_code_fences() -> None:
    raw_llm_output = """```json
{
  "summary": "Review completed successfully.",
  "key_points": ["Point A"],
  "action_items": []
}
```"""
    parsed = ConversationSummarizer._parse_json_response(raw_llm_output)
    assert parsed["summary"] == "Review completed successfully."
    assert parsed["key_points"] == ["Point A"]
    assert parsed["action_items"] == []
