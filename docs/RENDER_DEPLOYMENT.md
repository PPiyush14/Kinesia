# Soniox + Cartesia meetings on Render

The meeting pipeline is browser AudioWorklet → authenticated backend WebSocket
→ Soniox recognition and native speech translation → Cartesia streaming TTS
→ listener PCM playback. Provider keys stay on the backend. Final tokens drive
speech; partial hypotheses only update captions. Stable stream IDs and sequence
numbers prevent replay. Revised transcript rows merge by source audio time and
stable row ID, preserving legitimate repeated sentences.

The existing accounts, rooms, passcodes, PostgreSQL tables, REST endpoints, and
legacy SSE transport are retained. The meeting screen keeps the dark/green/cyan
theme with language controls, participant activity, captions, a transcript drawer,
export, audio recovery, and mobile controls.

## Backend service

Keep the existing service and Docker deployment. Its existing command
`python -m backend.dashboard_server` selects the ASGI streaming server when
`TRANSLATION_PROVIDER=soniox`. A native Python service can use the same command
after `pip install -r requirements.txt`.

Set these in the backend service's **Environment** tab before manual deployment:

| Variable | Value |
| --- | --- |
| `SONIOX_API_KEY` | Your current Soniox key |
| `CARTESIA_API_KEY` | Your current Cartesia key |
| `DATABASE_URL` | The Render PostgreSQL **internal** connection URL, copied privately from the database service |
| `JWT_SECRET` | A persistent random secret of at least 32 characters |
| `TRANSLATION_PROVIDER` | `soniox` |
| `TTS_PROVIDER` | `cartesia` (replace an old `edge` setting) |
| `SONIOX_MODEL` | `stt-rt-v5` |
| `CARTESIA_MODEL_ID` | `sonic-3.6-2026-08-27` |
| `CARTESIA_VERSION` | `2026-08-14` |
| `CARTESIA_VOICE_ID` | `95d51f79-c397-46f9-b49a-23763d3eaa2d`, or your compatible multilingual voice |
| `CORS_ORIGINS` | Your frontend HTTPS origin, without a trailing slash; comma-separated if needed |

Generate a JWT secret locally, for example with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`, and paste the
output only into Render. Preserve it across deployments to keep existing logins
valid. Rotate the database password that was shared in chat and copy its new
internal URL into Render. The internal hostname is for services on Render's
private network; local tests use an isolated SQLite database instead.

Let Render supply `PORT`; bind to `HOST=0.0.0.0`. Keep the backend and database
in a compatible Render region/network. Set the health check path to
`/api/v1/health`. On Render the server rejects missing PostgreSQL configuration
or a missing/short JWT secret, avoiding accidental ephemeral production storage.

Optional tuning is documented in [.env.example](../.env.example). Start with
`SONIOX_MAX_ENDPOINT_DELAY_MS=1000`, `CARTESIA_SPEED=1.0`, and neutral emotion.
`CARTESIA_VOICE_MAP` accepts a JSON object of language-to-voice overrides.
Remove old `CHANNEL_1_CARTESIA_*` / `CHANNEL_2_CARTESIA_*` overrides if you want
the global Cartesia model and voice settings to apply; compatibility overrides
still take precedence.

Use **one worker and one backend instance**. Room queues and live audio routes
are process-local. The current admission limits are eight participants and four
listening languages per room, configurable with `REALTIME_MAX_LANGUAGES` for
languages. This is a guardrail, not a scale guarantee: Soniox opens a stream per
speaker/target language and shares its result among listeners. Provider concurrency
quotas and credits can impose lower practical limits. No Redis or paid Render
upgrade is required for the current single-instance setup.

## Frontend service

Set `VITE_API_BASE_URL` to the backend HTTPS **origin**, for example
`https://your-backend.onrender.com`, without `/api` or a trailing slash. This is a
build-time setting: rebuild the frontend after changing it. The client derives
the matching `wss://` room endpoint from the same origin.

Use Node 20.19+ or a supported newer LTS. From the repository root:

```sh
npm --prefix frontend ci --include=dev
npm run build
```

The publish directory is `backend/web`. Keep/add the static site's SPA rewrite
`/*` → `/index.html`. The build includes `/audio-worklet.js`; it must be publicly
served from the frontend origin. Never add speech keys, the database URL, or the
JWT secret to a `VITE_*` variable. Local `.env` is excluded from Git and Docker.

## Deploy and test

1. Manually deploy the backend after saving its environment. Open its health URL
   and let the free instance wake up. Expect `transport: "websocket"`,
   `database: "postgresql"`, and `speech_configured: true`. The last field checks
   configuration presence, not provider validity or credit balance.
2. Manually rebuild/deploy the frontend with the backend origin above.
3. Use two browser profiles or devices with different accounts and headphones.
   Create a room, join from the second account, and allow microphone access.
   Set one listener to Hindi and the other to English. Speak normal short turns,
   then longer sentences. Verify captions and translated speech in both directions.
4. Include names, numbers, negation, mixed languages, and repeated sentences.
   Repeating a sentence should produce another turn; a single turn should not
   replay audio when its transcript is revised. Check source captions separately
   from the translation to identify recognition versus translation errors.
5. Test mute/unmute, listen-only joining, output mute, language changes, permission
   denial/retry, network loss/recovery, refreshing a tab, and another tab joining
   the same account. The newer tab owns the subscription; the older tab stops.
6. End the room as host; verify microphones/playback stop and notes appear.
   Reopen history after refresh. Test a passcode room and a room with recording
   disabled. Verify non-host room deletion is rejected. Account/room deletion
   controls perform real deletes only when you explicitly invoke them.

Free-tier cold starts are expected. A temporarily unavailable backend preserves
the saved login and offers retry. Active streaming quality, real acoustic echo,
device compatibility, Render PostgreSQL connectivity, and end-to-end speech
latency still require the manual deployment test. No remote database mutation or
Render deployment was performed during local verification.

## Feature and latency limits

With only these two providers, notes are **extractive transcript excerpts** and
possible quoted commitments. They do not invent owners/deadlines or call a third
LLM. Optional spaCy entities require its model; they are absent if it is not
installed. Typed text translation is preserved through a Cartesia-to-Soniox voice
bridge, which is slower and can change wording; live microphone speech is the
primary path. Both speech APIs require available credits.

Microphone PCM travels in 100-ms frames. Speech starts from stable clauses or a
final endpoint; translation needs linguistic context. Playback and upload queues
are bounded; stale output is discarded visibly instead of replayed much later.
No fixed accuracy or sub-second end-to-end latency claim is made.

## Reproduce local checks

```sh
python -m pytest -q
npm --prefix frontend test
npm run build
npm --prefix frontend audit
npm --prefix frontend exec playwright install chromium
npm --prefix frontend run test:e2e
```

Activate `.venv` and install pytest first. Browser tests use mocked room/provider
events and a fake browser microphone, including real AudioWorklet capture; they
need no API keys or production database. On Windows, installed Chrome can be used
with `$env:PLAYWRIGHT_CHANNEL='chrome'` before the browser command.

An additional optional check consumes a few seconds of provider credits and uses
only synthetic speech, without connecting to the database:

```sh
python scripts/check_speech_providers.py --live --language hi
```

This has successfully exercised Cartesia English synthesis → Soniox English
recognition/Hindi translation → Cartesia Hindi synthesis using the local keys.
Its generation timing is not a microphone-to-listener latency measurement.

Verification on 2026-10-24: 91 backend tests passed; 14 frontend unit regressions
and 5 Chrome browser tests passed; TypeScript/Vite production build passed;
`npm audit` reported zero vulnerabilities. Static checks passed for the new
streaming modules and tests, plus undefined-name/duplicate-definition checks
across the backend. The backend suite has one dependency deprecation warning
from Starlette's httpx test adapter. The repository/build secret scan found no
local provider keys, credential-bearing PostgreSQL URLs, or private keys.
