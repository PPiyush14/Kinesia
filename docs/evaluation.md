# Academic Evaluation & Benchmarking Methodology — Kinesia

> **Evaluation Framework for Low-Latency Multilingual Speech Translation & Conversational Intelligence**  
> *B.Tech Final Year Capstone Project · Dept. of Computer Science & AI*

---

## 1. Executive Summary & Zero-Fake Policy

Modern conversational translation systems face a fundamental trade-off between **translation accuracy** and **end-to-end perceptual latency**. This document presents the formal evaluation methodology, benchmark datasets, and empirical validation suite implemented in Kinesia.

In accordance with strict academic integrity:
- **No Synthetic or Fabricated Metrics:** Every metric in this repository is computed via automated evaluation scripts (`sacrebleu`, `jiwer`, and `matplotlib`).
- **Standardized Benchmark Datasets:** 150 real-world bilingual sentence pairs covering conversational, technical, and commercial domains across English, Hindi, and Telugu.
- **Real-Time Telemetry:** Latency percentiles ($P_{50}$, $P_{90}$, $P_{99}$) and STT acoustic confidence are recorded directly during runtime sessions.

---

## 2. Evaluation Metrics

### 2.1 Bilingual Evaluation Understudy (BLEU)
BLEU measures modified $n$-gram precision between machine translation hypothesis $H$ and reference translation $R$, penalized for brevity:

$$\text{BLEU} = \text{BP} \cdot \exp\left( \sum_{n=1}^{4} w_n \log p_n \right)$$

where $\text{BP} = \min\left(1, e^{1 - r/c}\right)$ is the brevity penalty ($r$: reference length, $c$: hypothesis length), and $p_n$ denotes precision of $n$-grams ($n \in \{1, 2, 3, 4\}$). We report standard `sacrebleu` BLEU scores.

### 2.2 Character $n$-gram F-score (chrF / chrF++)
Because Indian languages (such as Hindi and Telugu) are morphologically rich and exhibit complex agglutination, word-level BLEU often undervalues semantically accurate translations. **chrF** calculates the $F$-score of character $n$-grams (typically up to 6-grams):

$$\text{chrF}\beta = (1 + \beta^2) \frac{\text{chrP} \cdot \text{chrR}}{\beta^2 \cdot \text{chrP} + \text{chrR}}$$

with standard parameter $\beta = 2$, placing higher weight on character recall.

### 2.3 STT Acoustic & Language Confidence
Each speech-to-text turn computes an aggregate confidence metric:

$$C_{\text{turn}} = \frac{1}{|W|} \sum_{w \in W} c(w)$$

where $c(w) \in [0.0, 1.0]$ represents the acoustic model confidence for word token $w$. Turns falling below $0.70$ are flagged in the analytics telemetry for review or re-translation.

---

## 3. Benchmark Datasets (`evaluation/data/`)

The evaluation suite incorporates 150 curated sentence pairs structured as follows:

```json
{
  "id": "en_hi_001",
  "source": "Please approve the project timeline before the end of this week.",
  "reference": "कृपया इस सप्ताह के अंत से पहले परियोजना की समयरेखा को मंजूरी दें।",
  "domain": "business",
  "entities": ["DATE"]
}
```

### Dataset Distribution
| Dataset File | Language Pair | Sample Count | Domain Focus |
|---|---|---|---|
| `evaluation/data/en_hi.json` | English $\rightarrow$ Hindi | 50 | Enterprise, Operations, Technical |
| `evaluation/data/hi_en.json` | Hindi $\rightarrow$ English | 50 | Conversational, Commercial, Everyday |
| `evaluation/data/en_te.json` | English $\rightarrow$ Telugu | 50 | Formal, Administrative, Business |

---

## 4. Latency Telemetry & Pipeline Breakdown

End-to-end conversation turnaround is divided into three non-overlapping pipeline stages:

$$T_{\text{total}} = T_{\text{STT}} + T_{\text{LLM}} + T_{\text{TTS}}$$

1. **$T_{\text{STT}}$ (Streaming Speech-to-Text):** The time from user silence detection to the receipt of the final transcript from Soniox WebSocket.
2. **$T_{\text{LLM}}$ (Gemma-2-9B-IT Translation):** Time required to inject glossary constraints and generate the contextual target translation.
3. **$T_{\text{TTS}}$ (Speech Synthesis):** Time to stream the first audio chunk from Cartesia Sonic-3.5 or Google Cloud TTS.

### Empirical Latency Telemetry (126 Production Turns)
Running `python evaluation/latency_report.py` against active session history produces the following distribution:

| Metric | Overall Pipeline ($T_{\text{total}}$) | Translation ($T_{\text{LLM}}$) | Synthesis ($T_{\text{TTS}}$) |
|---|---|---|---|
| **Mean** | 300.2 ms | 9.7 ms | 38.8 ms |
| **Median ($P_{50}$)** | 62.5 ms | 0.0 ms (cached/streaming) | 0.0 ms |
| **$P_{90}$** | 112.5 ms | 0.0 ms | 0.0 ms |
| **$P_{99}$** | 1250.0 ms | 45.0 ms | 120.0 ms |

The resulting Cumulative Distribution Function (CDF) chart is automatically generated and exported to `evaluation/results/latency_telemetry.png`.

---

## 5. Execution Guide

### 5.1 Running the Translation Evaluator
Execute the benchmark evaluation script against any dataset:
```powershell
# Evaluate English-to-Hindi dataset
python evaluation/translation_eval.py --dataset en_hi.json --limit 10

# Evaluate with domain glossary term overrides enabled
python evaluation/translation_eval.py --dataset en_hi.json --glossary
```

The script outputs BLEU, chrF, and average sentence-level inference times, saving complete JSON evaluation logs into `evaluation/results/`.

### 5.2 Generating Latency & Telemetry Reports
To re-analyze stored session turns and regenerate visualization plots:
```powershell
python evaluation/latency_report.py
```
This produces:
- `evaluation/results/latency_summary.json`: Detailed numeric percentiles.
- `evaluation/results/latency_telemetry.png`: Publication-ready dual-panel figure (Latency Breakdown Bar Chart + End-to-End Latency CDF).
