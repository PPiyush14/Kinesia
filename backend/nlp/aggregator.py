"""Business Intelligence (BI) and analytics aggregator.

Processes real conversation turns and summaries to produce chart-ready data:
- Entity frequencies and timelines
- Language distributions
- Latency statistics (Gemma, TTS, playback, total)
- STT confidence distributions

Zero synthetic data policy: if no data exists, methods return honest empty structures.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


class BIAggregator:
    """Aggregates conversation metrics and NLP extractions for visual analytics."""

    @staticmethod
    def entity_frequency(turns: list[dict[str, Any]]) -> dict[str, int]:
        """Calculate frequency of each detected entity across all turns."""
        counter: Counter[str] = Counter()
        for turn in turns:
            entities = turn.get("entities")
            if not isinstance(entities, dict):
                continue
            for items in entities.values():
                if isinstance(items, list):
                    for item in items:
                        cleaned = str(item).strip()
                        if cleaned:
                            counter[cleaned] += 1
        return dict(counter.most_common(50))

    @staticmethod
    def entity_by_type(turns: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Group unique entities by their NER type (PERSON, ORG, LOC, DATE, etc.)."""
        type_map: dict[str, set[str]] = {}
        for turn in turns:
            entities = turn.get("entities")
            if not isinstance(entities, dict):
                continue
            for label, items in entities.items():
                if isinstance(items, list):
                    type_map.setdefault(label, set())
                    for item in items:
                        cleaned = str(item).strip()
                        if cleaned:
                            type_map[label].add(cleaned)
        return {label: sorted(items) for label, items in sorted(type_map.items())}

    @staticmethod
    def entity_timeline(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return chronological timeline of entity occurrences per turn."""
        timeline: list[dict[str, Any]] = []
        for idx, turn in enumerate(turns):
            entities = turn.get("entities")
            if not isinstance(entities, dict) or not entities:
                continue
            flat_entities: list[dict[str, str]] = []
            for label, items in entities.items():
                if isinstance(items, list):
                    for item in items:
                        flat_entities.append({"type": label, "name": str(item)})
            if flat_entities:
                timeline.append({
                    "turn_index": turn.get("turn_index", idx),
                    "captured_at": turn.get("captured_at"),
                    "speaker": turn.get("speaker_channel_name") or turn.get("speaker_channel_key"),
                    "entities": flat_entities,
                })
        return timeline

    @staticmethod
    def language_distribution(turns: list[dict[str, Any]]) -> dict[str, int]:
        """Count turns by detected source language."""
        counts: Counter[str] = Counter()
        for turn in turns:
            lang = turn.get("detected_source_language") or turn.get("speaker_session_language")
            if lang:
                counts[str(lang).lower()] += 1
        return dict(counts.most_common())

    @staticmethod
    def latency_stats(turns: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute real latency averages and breakdown across turns."""
        gemma_latencies: list[float] = []
        tts_latencies: list[float] = []
        playback_latencies: list[float] = []
        total_latencies: list[float] = []

        for turn in turns:
            lat = turn.get("latency_ms")
            if isinstance(lat, dict):
                g = lat.get("gemma")
                t = lat.get("tts")
                p = lat.get("playback")
                tot = lat.get("total")
                if isinstance(g, (int, float)):
                    gemma_latencies.append(float(g))
                if isinstance(t, (int, float)):
                    tts_latencies.append(float(t))
                if isinstance(p, (int, float)):
                    playback_latencies.append(float(p))
                if isinstance(tot, (int, float)):
                    total_latencies.append(float(tot))

        n = len(total_latencies)
        if n == 0:
            return {
                "turns_count": 0,
                "avg_gemma_ms": 0.0,
                "avg_tts_ms": 0.0,
                "avg_playback_ms": 0.0,
                "avg_total_ms": 0.0,
                "min_total_ms": 0.0,
                "max_total_ms": 0.0,
                "history": [],
            }

        history = [
            {
                "turn_index": turn.get("turn_index", i),
                "total_ms": round(float(turn.get("latency_ms", {}).get("total", 0)), 1),
                "gemma_ms": round(float(turn.get("latency_ms", {}).get("gemma", 0)), 1),
                "tts_ms": round(float(turn.get("latency_ms", {}).get("tts", 0)), 1),
            }
            for i, turn in enumerate(turns)
            if isinstance(turn.get("latency_ms"), dict)
        ]

        return {
            "turns_count": n,
            "avg_gemma_ms": round(sum(gemma_latencies) / len(gemma_latencies), 1) if gemma_latencies else 0.0,
            "avg_tts_ms": round(sum(tts_latencies) / len(tts_latencies), 1) if tts_latencies else 0.0,
            "avg_playback_ms": round(sum(playback_latencies) / len(playback_latencies), 1) if playback_latencies else 0.0,
            "avg_total_ms": round(sum(total_latencies) / n, 1),
            "min_total_ms": round(min(total_latencies), 1),
            "max_total_ms": round(max(total_latencies), 1),
            "history": history,
        }

    @staticmethod
    def confidence_stats(turns: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate distribution of STT confidence scores across turns."""
        scores: list[float] = []
        for turn in turns:
            conf = turn.get("stt_confidence")
            if isinstance(conf, (int, float)):
                scores.append(float(conf))

        if not scores:
            return {
                "avg_confidence": 1.0,
                "low_confidence_count": 0,
                "high_confidence_count": 0,
                "scores": [],
            }

        low_count = sum(1 for s in scores if s < 0.70)
        high_count = len(scores) - low_count

        return {
            "avg_confidence": round(sum(scores) / len(scores), 3),
            "low_confidence_count": low_count,
            "high_confidence_count": high_count,
            "scores": [round(s, 2) for s in scores],
        }

    def build_session_insights(
        self,
        session_id: str,
        turns: list[dict[str, Any]],
        summary_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the complete analytical payload for a session."""
        return {
            "session_id": session_id,
            "turn_count": len(turns),
            "entity_frequency": self.entity_frequency(turns),
            "entity_by_type": self.entity_by_type(turns),
            "entity_timeline": self.entity_timeline(turns),
            "language_distribution": self.language_distribution(turns),
            "latency": self.latency_stats(turns),
            "confidence": self.confidence_stats(turns),
            "summary": summary_data,
        }
