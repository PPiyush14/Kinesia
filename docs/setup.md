# Installation & Setup Guide — Kinesia

> **Industry-Grade Real-Time Bilingual Speech Translation Platform**  
> *Target Environments: Local Workstation · Edge Gateways · Server Deployments*

---

## 1. System Requirements

- **Operating System:** Windows 10/11 (64-bit), Linux (Ubuntu 20.04+), or macOS (12+)
- **Python Runtime:** Python `3.10` or `3.11`
- **Memory & Storage:** Minimum 4 GB RAM, 2 GB free disk space
- **Audio Hardware:**
  - **Speaker A:** Dedicated microphone and headphones/speaker
  - **Speaker B:** Dedicated microphone and headphones/speaker
  - *For single-machine software testing: Virtual audio cables (e.g. VB-Audio Virtual Cable) or system default devices are supported.*

---

## 2. Installation Steps

### Step 1: Clone & Navigate
```bash
git clone https://github.com/your-org/kinesia.git
cd kinesia
```

### Step 2: Create & Activate Virtual Environment
On Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On Linux / macOS (bash):
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install Package Dependencies
Install the package in editable mode with development and evaluation dependencies:
```bash
pip install --upgrade pip
pip install -e .
pip install pytest pytest-cov sacrebleu jiwer matplotlib spacy
```

### Step 4: Download spaCy Multilingual NER Models
Download the lightweight multilingual and English NER packages:
```bash
python -m spacy download xx_ent_wiki_sm
python -m spacy download en_core_web_sm
```

---

## 3. Environment Configuration (`.env`)

Create a `.env` file in the project root directory (you can copy `.env.example` as a template):

```ini
# ============================================================
# Speech-to-Text (STT) — Soniox Real-Time Streaming v5
# ============================================================
SONIOX_API_KEY=your_soniox_api_key_here
SONIOX_MODEL=stt-rt-v5
SONIOX_WEBSOCKET_URL=wss://stt-rt.soniox.com/transcribe-websocket
SONIOX_MAX_ENDPOINT_DELAY_MS=1500
SONIOX_KEEPALIVE_SECONDS=8.0
SONIOX_SESSION_RETRY_ATTEMPTS=6

# ============================================================
# Large Language Model (LLM) — Google Gemma-2-9B-IT
# ============================================================
GEMMA_RESPONSE_BASE_URL=http://43.242.226.49:8100/v1
GEMMA_RESPONSE_API_KEY=your_gemma_api_key_here
GEMMA_MODEL=google/gemma-2-9b-it
GEMMA_TEMPERATURE=0.0
GEMMA_MAX_TOKENS=128
GEMMA_REQUEST_TIMEOUT_SECONDS=45.0

# ============================================================
# Text-to-Speech (TTS) — Cartesia Sonic-3.5 or Google Cloud TTS
# ============================================================
TTS_PROVIDER=cartesia
CARTESIA_API_KEY=your_cartesia_api_key_here
CARTESIA_API_URL=https://api.cartesia.ai
CARTESIA_WEBSOCKET_URL=wss://api.cartesia.ai/tts/websocket
CARTESIA_VERSION=2026-03-01
CARTESIA_SPEED=0.9
CARTESIA_CONTEXT_PREFIX=kinesia

# ============================================================
# Dual Channel Hardware & Language Configuration
# ============================================================
CHANNEL_1_NAME="Participant A (English)"
CHANNEL_1_SOURCE_LANGUAGE=en
CHANNEL_1_LANGUAGE_MODE=auto
CHANNEL_1_INPUT_DEVICE=0
CHANNEL_1_OUTPUT_DEVICE=0
CHANNEL_1_CARTESIA_VOICE_ID=a0e99841-438c-4a64-b679-ae501e7d6091

CHANNEL_2_NAME="Participant B (Hindi)"
CHANNEL_2_SOURCE_LANGUAGE=hi
CHANNEL_2_LANGUAGE_MODE=auto
CHANNEL_2_INPUT_DEVICE=1
CHANNEL_2_OUTPUT_DEVICE=1
CHANNEL_2_CARTESIA_VOICE_ID=79a125e8-cd45-4c13-8a67-188112f4dd22

# ============================================================
# Optional Advanced Features
# ============================================================
# Opt-in uncompressed 16kHz audio archiving for auditing
SAVE_AUDIO=true

# Post-session automation webhook
WEBHOOK_URL=
WEBHOOK_SECRET=

# Logging level (DEBUG, INFO, WARNING, ERROR)
APP_LOG_LEVEL=INFO
```

---

## 4. Running the Platform

### Running the Web Studio Dashboard (Recommended)
Launch the modern glassmorphic web dashboard with real-time analytics and controls:
```powershell
$env:PYTHONPATH="src"
python -m pi_translator.dashboard_server
```
Then open your browser to **`http://127.0.0.1:8765`**.

### Running the CLI Speech Translator
For pure command-line execution without browser UI:
```powershell
$env:PYTHONPATH="src"
python -m pi_translator.cli translate
```

### Audio Device Diagnostic Tool
To list all active audio input and output hardware indices recognized by PortAudio:
```powershell
$env:PYTHONPATH="src"
python -m pi_translator.cli devices
```

---

## 5. Verification & Testing

### Running the Complete Automated Test Suite
Execute the 30 unit and integration tests:
```powershell
$env:PYTHONPATH="src"
pytest -v
```

### Running Academic Evaluation Benchmarks
Benchmark translation quality (BLEU, chrF) and measure latency:
```powershell
# Run BLEU / chrF evaluation on 50 English-Hindi sentence pairs
python evaluation/translation_eval.py --dataset en_hi.json --limit 10

# Generate latency telemetry statistics and matplotlib CDF plot
python evaluation/latency_report.py
```
Output charts are saved to `evaluation/results/latency_telemetry.png`.
