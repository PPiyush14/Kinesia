---
title: Kinesia
emoji: 🌐
colorFrom: green
colorTo: indigo
sdk: docker
app_port: 7860
---

# Kinesia

Multilingual audio meetings with streamed Soniox transcription/translation, Cartesia speech, live captions, and participant-only transcript history. Session notes quote recorded discussion and possible commitments without an additional LLM. Latency and recognition quality depend on the language pair, network, and provider load; no sub-second guarantee is claimed.

For the current two-service Render deployment, follow [Render deployment and test checklist](docs/RENDER_DEPLOYMENT.md). Do not upload `.env` to GitHub. The earlier audit is recorded in [KINESIA_AUDIT_FIXES.md](docs/KINESIA_AUDIT_FIXES.md).

## Repository layout

```
kinesia/
├── frontend/                 # React 18 + TS + Vite + Tailwind
│   └── src/
│       ├── components/       # brand, navigation, ui
│       ├── features/         # auth, workspace, lobby, dialogue, insights
│       ├── hooks/            # SSE, STT dictation, audio meter
│       ├── services/         # REST client
│       ├── lib/ + types/
├── backend/                  # Python backend
│   ├── api/ db/ rooms/ nlp/ storage/ core/ web/
│   ├── dashboard_server.py   # HTTP + SSE
│   ├── soniox / cartesia / gemma / TTS clients
├── tests/  docs/  evaluation/
├── requirements.txt  pyproject.toml  run_translator.py
├── package.json  .env.example  README.md
```

## Tech stack

| Layer | Technology |
|-------|------------|
| Frontend | React 18, TypeScript, Vite 7, Tailwind CSS, Framer Motion, Lucide |
| Backend | Python 3.10+, FastAPI/Uvicorn; existing REST implementation on a loopback adapter |
| Realtime | Authenticated WebSocket, binary PCM, AudioWorklet microphone capture; legacy SSE retained |
| STT | Soniox Real-Time WebSocket |
| Translation | Soniox native speech translation |
| TTS | Cartesia streaming WebSocket; no hidden third-provider fallback in meeting mode |
| NLP | Extractive session notes; optional spaCy entity extraction when its model is installed |
| DB | SQLite (default) / PostgreSQL |

## Prerequisites

- Node.js 20.19+ (or a supported newer LTS)
- Python 3.10+
- API keys for Soniox and Cartesia (see `.env.example`)

## Setup

```bash
# Clone / extract
cd kinesia

# Backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in keys

# Frontend
cd frontend && npm install && cd ..
```

## Development

Terminal 1 — API + streaming audio server (default `http://127.0.0.1:8765`):

```bash
source .venv/bin/activate
python -m backend.dashboard_server
# or: npm run server
```

Terminal 2 — Vite dev server with API proxy:

```bash
npm run dev
# opens http://localhost:5173
```

## Production build

```bash
npm run build
# Frontend bundles into backend/web/
# Serve with the same Python process (static + API)
python -m backend.dashboard_server
```

## Tests

```bash
pip install pytest pytest-cov
npm run test
# or: python -m pytest tests/ -q
npm --prefix frontend test
npm --prefix frontend run test:e2e
```

## API overview

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/auth/register` | Create account |
| POST | `/api/v1/auth/login` | Login → token |
| GET | `/api/v1/auth/me` | Current user |
| GET/POST | `/api/v1/rooms` | List / create rooms |
| GET | `/api/v1/rooms/:code` | Room details |
| POST | `/api/v1/rooms/:code/verify` | Passcode check |
| POST | `/api/v1/rooms/:code/speak` | Submit utterance |
| POST | `/api/v1/rooms/:code/end` | End session → insights |
| GET | `/api/v1/rooms/:code/transcripts` | Transcript history |
| GET | `/api/v1/rooms/:code/insights` | MoM payload |
| SSE | `/events/rooms/:code` | Live events |
| WebSocket | `/ws/rooms/:code` | Authenticated live microphone, captions, PCM audio and room events |

Auth: `Authorization: Bearer <token>`.

## Documentation

- `docs/setup.md` — environment and deployment
- `docs/architecture.md` — system design
- `docs/api.md` — endpoint reference
- `docs/evaluation.md` — latency / quality eval

## License

Proprietary — all rights reserved.
