"""Unit tests for BIAggregator analytics calculations."""

from __future__ import annotations

from typing import Any

from backend.nlp.aggregator import BIAggregator


def test_entity_frequency(sample_turns: list[dict[str, Any]]) -> None:
    aggregator = BIAggregator()
    freq = aggregator.entity_frequency(sample_turns)

    assert freq.get("TechCorp") == 1
    assert freq.get("Friday") == 1
    assert freq.get("मुंबई") == 1


def test_language_distribution(sample_turns: list[dict[str, Any]]) -> None:
    aggregator = BIAggregator()
    distribution = aggregator.language_distribution(sample_turns)

    assert distribution.get("en") == 1
    assert distribution.get("hi") == 1


def test_latency_statistics(sample_turns: list[dict[str, Any]]) -> None:
    aggregator = BIAggregator()
    stats = aggregator.latency_stats(sample_turns)

    assert stats["turns_count"] == 2
    assert stats["avg_gemma_ms"] == 785.0  # (820 + 750) / 2
    assert stats["avg_total_ms"] == 1305.0  # (1370 + 1240) / 2
    assert stats["min_total_ms"] == 1240.0
    assert stats["max_total_ms"] == 1370.0


def test_confidence_statistics(sample_turns: list[dict[str, Any]]) -> None:
    aggregator = BIAggregator()
    conf = aggregator.confidence_stats(sample_turns)

    assert conf["low_confidence_count"] == 0
    assert conf["high_confidence_count"] == 2
    assert conf["avg_confidence"] == 0.91  # (0.94 + 0.88) / 2 = 0.91


def test_build_session_insights(sample_turns: list[dict[str, Any]]) -> None:
    aggregator = BIAggregator()
    insights = aggregator.build_session_insights(
        "sess_01",
        sample_turns,
        summary_data={"summary": "Executive briefing"},
    )
    assert insights["session_id"] == "sess_01"
    assert insights["turn_count"] == 2
    assert insights["summary"]["summary"] == "Executive briefing"
    assert "entity_frequency" in insights
    assert "latency" in insights
