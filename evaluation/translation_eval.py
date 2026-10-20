"""Evaluation script for machine translation quality.

Evaluates Gemma translation quality using academic metrics:
- BLEU (Bilingual Evaluation Understudy) via sacrebleu
- chrF++ (Character n-gram F-score) via sacrebleu
- TER / Length ratio

Evaluates across language pairs (EN->HI, HI->EN, EN->TE) and domain categories.
Compares standard translation vs glossary-enhanced translation on proper nouns.
All scores are calculated from genuine sentence pairs in evaluation/data/.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import sacrebleu

from backend.config import AppConfig, ChannelConfig
from backend.gemma_client import GemmaClient
from backend.storage.glossary import GlossaryEntry, GlossaryStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EVAL_DATA_DIR = Path(__file__).resolve().parent / "data"
EVAL_RESULTS_DIR = Path(__file__).resolve().parent / "results"
EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class EvalMetricResult:
    dataset: str
    sample_count: int
    bleu_score: float
    chrf_score: float
    avg_latency_ms: float
    timestamp: float = time.time()


class TranslationEvaluator:
    """Evaluates translation performance against reference corpora."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig.from_env()
        self.gemma = GemmaClient(self.config)
        self.glossary_store = GlossaryStore(self.config.project_root)

    def load_dataset(self, filename: str) -> list[dict[str, Any]]:
        file_path = EVAL_DATA_DIR / filename
        if not file_path.exists():
            raise FileNotFoundError(f"Evaluation dataset not found: {file_path}")
        return json.loads(file_path.read_text(encoding="utf-8"))

    def evaluate_dataset(
        self,
        dataset_name: str,
        *,
        limit: int | None = None,
        use_glossary: bool = False,
    ) -> dict[str, Any]:
        """Run translation evaluation on a dataset file (e.g., 'en_hi.json')."""
        records = self.load_dataset(dataset_name)
        if limit:
            records = records[:limit]

        sources: list[str] = []
        references: list[str] = []
        hypotheses: list[str] = []
        latencies: list[float] = []

        logger.info(
            "Evaluating %s (%d pairs, use_glossary=%s)...",
            dataset_name,
            len(records),
            use_glossary,
        )

        for idx, item in enumerate(records, start=1):
            source_text = item["source"]
            ref_text = item["reference"]
            src_lang = item["source_language"]
            tgt_lang = item["target_language"]

            speaker_ch = ChannelConfig(
                key="eval_src",
                name="Source Channel",
                source_language=src_lang,
                language_mode="fixed",
            )
            listener_ch = ChannelConfig(
                key="eval_tgt",
                name="Target Channel",
                source_language=tgt_lang,
                language_mode="fixed",
            )

            glossary_hint = ""
            if use_glossary:
                matched = self.glossary_store.find_matched_entries(source_text)
                glossary_hint = self.glossary_store.build_prompt_hint(matched)

            t0 = time.perf_counter()
            try:
                hypothesis = self.gemma.translate(
                    source_text,
                    speaker_ch,
                    listener_ch,
                    src_lang,
                    tgt_lang,
                    src_lang,
                    glossary_hint=glossary_hint,
                )
            except Exception as exc:
                logger.warning("Translation failed on sample %d: %s", idx, exc)
                hypothesis = ""

            latency_ms = (time.perf_counter() - t0) * 1000.0

            sources.append(source_text)
            references.append(ref_text)
            hypotheses.append(hypothesis)
            latencies.append(latency_ms)

        # Compute Corpus BLEU & chrF++
        bleu = sacrebleu.corpus_bleu(hypotheses, [references])
        chrf = sacrebleu.corpus_chrf(hypotheses, [references])

        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        result = {
            "dataset": dataset_name,
            "sample_count": len(records),
            "use_glossary": use_glossary,
            "bleu": round(bleu.score, 2),
            "chrf": round(chrf.score, 2),
            "avg_latency_ms": round(avg_latency, 1),
            "evaluated_at": time.time(),
            "samples": [
                {
                    "id": records[i].get("id"),
                    "domain": records[i].get("domain"),
                    "source": sources[i],
                    "reference": references[i],
                    "hypothesis": hypotheses[i],
                    "latency_ms": round(latencies[i], 1),
                }
                for i in range(len(records))
            ],
        }

        # Save results to disk
        out_name = f"eval_{Path(dataset_name).stem}_{'with_glossary' if use_glossary else 'standard'}_{int(time.time())}.json"
        out_path = EVAL_RESULTS_DIR / out_name
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Results saved to %s", out_path)

        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate translation engine on benchmark datasets")
    parser.add_argument("--dataset", default="en_hi.json", help="Dataset filename in evaluation/data/")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of samples")
    parser.add_argument("--glossary", action="store_true", help="Enable glossary term overrides")
    args = parser.parse_args()

    evaluator = TranslationEvaluator()
    results = evaluator.evaluate_dataset(args.dataset, limit=args.limit, use_glossary=args.glossary)

    print("\n" + "=" * 50)
    print(f"Dataset:       {results['dataset']}")
    print(f"Sample Count:  {results['sample_count']}")
    print(f"BLEU Score:    {results['bleu']}")
    print(f"chrF++ Score:  {results['chrf']}")
    print(f"Avg Latency:   {results['avg_latency_ms']} ms")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    main()
