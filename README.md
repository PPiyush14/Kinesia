---
title: Kinesia
emoji: 🌐
colorFrom: green
colorTo: indigo
sdk: docker
app_port: 7860
---

# Kinesia

Real-time multilingual dialogue platform. Participants speak in their native language; others receive translated subtitles and synthesized speech with sub-second latency. Sessions conclude with autonomous conversation intelligence summaries, key points, and action items.

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
| Frontend | React 18, TypeScript, Vite 5, Tailwind CSS, Framer Motion, Lucide |
| Backend | Python 3.10+, ThreadingHTTPServer |
| Realtime | Server-Sent Events (`text/event-stream`) |
| STT | Soniox Real-Time WebSocket |
| Translation | Google Gemma (OpenAI-compatible API) |
| TTS | Cartesia WebSocket (+ Google TTS fallback) |
| NLP | BIAggregator, EntityExtractor, Summarizer |
| DB | SQLite (default) / PostgreSQL |

## Prerequisites

- Node.js 18+
- Python 3.10+
- API keys for Soniox, Cartesia, and the LLM provider (see `.env.example`)

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

Terminal 1 — API + SSE server (default `http://127.0.0.1:8765`):

```bash
source .venv/bin/activate
python run_kinesia.py
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
python run_kinesia.py
```

## Tests

```bash
pip install -r requirements-dev.txt
npm run test
# or: python -m pytest tests/ -q
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

Auth: `Authorization: Bearer <token>`.

## Documentation

- `docs/setup.md` — environment and deployment
- `docs/architecture.md` — system design
- `docs/api.md` — endpoint reference
- `docs/evaluation.md` — latency / quality eval

## License

Proprietary — all rights reserved.
