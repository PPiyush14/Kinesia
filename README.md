---
title: Kinesia
emoji: 🌐
colorFrom: green
colorTo: indigo
sdk: docker
app_port: 7860
---

# Kinesia

Multilingual conversations with streamed Soniox transcription/translation, Cartesia speech, live captions, private Saved Moments, and participant-only transcript history. Optional Groq AI recaps summarize ended, recorded conversations with links and exact quotes from the source transcript. Recognition quality and end-to-end latency depend on the language pair, network, devices, and provider load.

The current Render deployment uses one Docker web service for both frontend and backend, plus PostgreSQL. Follow the [Render deployment and test checklist](docs/RENDER_DEPLOYMENT.md). Local `.env` values are excluded from Git and Docker. Implementation details and actual verification results are recorded in [KINESIA_AUDIT_FIXES.md](docs/KINESIA_AUDIT_FIXES.md).

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
├── requirements.txt  pyproject.toml  run_kinesia.py
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
| Recaps | On-demand Groq structured output with validated source IDs and exact quotes; legacy extractive notes API retained |
| Saved Moments | Private transcript bookmarks per account, available during calls and in history |
| DB | SQLite (default) / PostgreSQL |

## Prerequisites

- Node.js 20.19+ (or a supported newer LTS)
- Python 3.10+
- API keys for Soniox and Cartesia; optional separate Groq key for AI recaps (see `.env.example`)

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
| GET | `/api/v1/rooms/:code/insights` | Legacy extractive notes payload |
| GET/POST | `/api/v1/rooms/:code/recap` | Read/request a cited AI recap after the conversation ends |
| GET | `/api/v1/moments` | Current account's private saved moments |
| GET/POST | `/api/v1/rooms/:code/moments` | List/save private transcript bookmarks |
| DELETE | `/api/v1/rooms/:code/moments/:turn_id` | Remove the caller's bookmark |
| SSE | `/events/rooms/:code` | Live events |
| WebSocket | `/ws/rooms/:code` | Authenticated live microphone, captions, PCM audio and room events |

Auth: `Authorization: Bearer <token>`.

## Conversation flow and recaps

Stable translated words feed Cartesia in short clauses, with a shared speech
context for clauses in the same utterance. Partial recognition hypotheses update
captions only. Intentional acknowledgements are preserved. Interruption handling
uses sustained voice activity plus fresh recognized speech; noise alone cannot
cancel someone else's translated audio. This is a conservative heuristic, and
real acoustic overlap still needs testing with your devices.

Bookmark a transcript line with **Save moment**. It remains private to your
account and is available in **History & Moments**. Personal history removal does
not delete shared conversations. Host-only shared transcript clearing removes
derived recaps and bookmarks for everyone.

After ending a recorded conversation, choose **Generate AI recap**. The backend
sends the saved source transcript to Groq only on that explicit request, outside
the live speech pipeline. Set `GROQ_API_KEY` in Render; the default
`GROQ_RECAP_MODEL` is `openai/gpt-oss-120b`. Missing configuration, provider errors,
and free-tier limits are shown with retry. Source evidence helps review the result
but cannot guarantee semantic accuracy. Conversations with recording disabled
have no saved moments or recaps.

## Documentation

- `docs/setup.md` — environment and deployment
- `docs/architecture.md` — system design
- `docs/api.md` — endpoint reference
- `docs/evaluation.md` — latency / quality eval

## License

Proprietary — all rights reserved.
