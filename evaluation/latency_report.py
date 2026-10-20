"""Latency and performance telemetry report generator.

Reads genuine session conversation files from session_conversations/,
aggregates end-to-end turnaround latency and per-component breakdowns (Gemma, TTS, playback),
computes statistical percentiles (Mean, Median, P90, P95),
and generates publication-quality visualization charts using matplotlib.

Strict no-fake policy: if no recorded session turns exist, prints a clear diagnostic notice
and exits without producing synthetic or fabricated numbers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_ROOT / "session_conversations"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


class LatencyReporter:
    """Aggregates and visualizes latency telemetry from actual session logs."""

    def __init__(self, sessions_dir: Path = SESSIONS_DIR) -> None:
        self.sessions_dir = sessions_dir

    def collect_turn_latencies(self) -> list[dict[str, Any]]:
        """Collects latency records across all completed conversation sessions."""
        records: list[dict[str, Any]] = []

        if not self.sessions_dir.exists():
            return records

        for file_path in self.sessions_dir.glob("*.json"):
            if file_path.name.endswith("_summary.json"):
                continue

            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            session_id = data.get("session_id", file_path.stem)
            turns = data.get("turns", [])
            if not isinstance(turns, list):
                continue

            for idx, turn in enumerate(turns):
                latency = turn.get("latency_ms")
                if not isinstance(latency, dict):
                    continue

                total = latency.get("total")
                if total is None or not isinstance(total, (int, float)):
                    continue

                records.append({
                    "session_id": session_id,
                    "turn_index": turn.get("turn_index", idx),
                    "captured_at": turn.get("captured_at"),
                    "gemma_ms": float(latency.get("gemma", 0.0)),
                    "tts_ms": float(latency.get("tts", 0.0)),
                    "playback_ms": float(latency.get("playback", 0.0)),
                    "total_ms": float(total),
                    "transcript_len": len(str(turn.get("transcript") or "")),
                    "source_lang": turn.get("detected_source_language"),
                    "target_lang": turn.get("listener_language"),
                })

        return records

    def compute_summary_statistics(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculates mean, median, P90, P95, min, and max for all latency components."""
        if not records:
            return {}

        totals = np.array([r["total_ms"] for r in records])
        gemmas = np.array([r["gemma_ms"] for r in records])
        ttss = np.array([r["tts_ms"] for r in records])
        playbacks = np.array([r["playback_ms"] for r in records])

        return {
            "turns_analyzed": len(records),
            "total": {
                "mean_ms": round(float(np.mean(totals)), 1),
                "median_ms": round(float(np.median(totals)), 1),
                "p90_ms": round(float(np.percentile(totals, 90)), 1),
                "p95_ms": round(float(np.percentile(totals, 95)), 1),
                "min_ms": round(float(np.min(totals)), 1),
                "max_ms": round(float(np.max(totals)), 1),
            },
            "gemma_llm": {
                "mean_ms": round(float(np.mean(gemmas)), 1),
                "median_ms": round(float(np.median(gemmas)), 1),
                "p90_ms": round(float(np.percentile(gemmas, 90)), 1),
            },
            "tts_synthesis": {
                "mean_ms": round(float(np.mean(ttss)), 1),
                "median_ms": round(float(np.median(ttss)), 1),
                "p90_ms": round(float(np.percentile(ttss, 90)), 1),
            },
            "audio_playback": {
                "mean_ms": round(float(np.mean(playbacks)), 1),
                "median_ms": round(float(np.median(playbacks)), 1),
            },
        }

    def generate_charts(self, records: list[dict[str, Any]]) -> Path | None:
        """Renders 3-panel latency chart to evaluation/results/latency_telemetry.png."""
        if not records:
            return None

        import matplotlib.pyplot as plt

        totals = [r["total_ms"] for r in records]
        gemmas = [r["gemma_ms"] for r in records]
        ttss = [r["tts_ms"] for r in records]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
        fig.suptitle("Real-Time Speech Translation Pipeline — Latency Telemetry", fontsize=14, fontweight="bold")

        # Subplot 1: Stacked component comparison
        turns_idx = list(range(1, len(records) + 1))
        ax1.bar(turns_idx, gemmas, label="Gemma LLM Translation", color="#6366f1", alpha=0.85)
        ax1.bar(turns_idx, ttss, bottom=gemmas, label="TTS Synthesis", color="#10b981", alpha=0.85)
        ax1.set_xlabel("Conversation Turn Index", fontsize=10)
        ax1.set_ylabel("Latency (milliseconds)", fontsize=10)
        ax1.set_title("Component Latency per Turn", fontsize=11, fontweight="semibold")
        ax1.legend(loc="upper left")
        ax1.grid(True, linestyle="--", alpha=0.3)

        # Subplot 2: Total Latency CDF (Cumulative Distribution Function)
        sorted_totals = np.sort(totals)
        cdf = np.arange(1, len(sorted_totals) + 1) / len(sorted_totals)
        ax2.plot(sorted_totals, cdf * 100, color="#06b6d4", linewidth=2.5, label="Empirical CDF")
        ax2.axvline(np.median(totals), color="#f59e0b", linestyle="--", label=f"Median ({np.median(totals):.0f} ms)")
        ax2.axvline(np.percentile(totals, 90), color="#f43f5e", linestyle=":", label=f"P90 ({np.percentile(totals, 90):.0f} ms)")
        ax2.set_xlabel("Total Turnaround Time (ms)", fontsize=10)
        ax2.set_ylabel("Cumulative Percentage (%)", fontsize=10)
        ax2.set_title("Latency CDF & Percentiles", fontsize=11, fontweight="semibold")
        ax2.legend(loc="lower right")
        ax2.grid(True, linestyle="--", alpha=0.3)

        plt.tight_layout()
        chart_path = RESULTS_DIR / "latency_telemetry.png"
        fig.savefig(chart_path, dpi=200)
        plt.close(fig)

        logger.info("Latency visualization chart saved to %s", chart_path)
        return chart_path


def main() -> None:
    reporter = LatencyReporter()
    records = reporter.collect_turn_latencies()

    if not records:
        print("\n" + "=" * 60)
        print("NOTICE: No recorded conversation session turns found.")
        print(f"Session directory: {SESSIONS_DIR}")
        print("Please run a translation session first so telemetry can be collected.")
        print("=" * 60 + "\n")
        return

    stats = reporter.compute_summary_statistics(records)
    summary_path = RESULTS_DIR / "latency_summary.json"
    summary_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    reporter.generate_charts(records)

    print("\n" + "=" * 60)
    print(f"Total Turns Analyzed: {stats['turns_analyzed']}")
    print(f"Total Latency: Mean = {stats['total']['mean_ms']}ms | Median = {stats['total']['median_ms']}ms | P90 = {stats['total']['p90_ms']}ms")
    print(f"Gemma LLM:     Mean = {stats['gemma_llm']['mean_ms']}ms | P90 = {stats['gemma_llm']['p90_ms']}ms")
    print(f"TTS Synthesis: Mean = {stats['tts_synthesis']['mean_ms']}ms | P90 = {stats['tts_synthesis']['p90_ms']}ms")
    print(f"Report JSON:   {summary_path}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
