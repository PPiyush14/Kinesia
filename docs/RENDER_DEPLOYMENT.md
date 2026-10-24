# Kinesia conversations on Render

The current deployment is **one Docker web service serving both frontend and
backend**, plus Render PostgreSQL. The built frontend is committed under
`backend/web`; the existing Dockerfile serves the UI, API, and WebSocket on the
same public origin. Keep this setup. A separate frontend service or
`VITE_API_BASE_URL` is unnecessary for the combined service.

Live speech follows browser AudioWorklet → authenticated backend WebSocket →
Soniox recognition/native translation → Cartesia streaming TTS → listener PCM
playback. Partial recognition updates captions; only stable text is voiced.
Groq is separate and runs only for explicitly requested, post-conversation AI
recaps. Provider keys stay on the backend.

## Environment

Keep the existing Docker service and its command,
`python -m backend.dashboard_server`. With `TRANSLATION_PROVIDER=soniox`, this
selects the ASGI streaming server. Set the following in Render's Environment tab:

| Variable | Value |
| --- | --- |
| `SONIOX_API_KEY` | Your Soniox key |
| `CARTESIA_API_KEY` | Your Cartesia key |
| `GROQ_API_KEY` | Your separate Groq key; required only for AI recaps |
| `DATABASE_URL` | Render PostgreSQL **internal** URL, copied privately from the database service |
| `JWT_SECRET` | Your existing persistent random secret, at least 32 characters |
| `TRANSLATION_PROVIDER` | `soniox` |
| `TTS_PROVIDER` | `cartesia` |
| `SONIOX_MODEL` | `stt-rt-v5` |
| `CARTESIA_MODEL_ID` | `sonic-3.6-2026-08-27` |
| `CARTESIA_VERSION` | `2026-08-14` |
| `CARTESIA_VOICE_ID` | `95d51f79-c397-46f9-b49a-23763d3eaa2d`, or your compatible multilingual voice |
| `HOST` | `0.0.0.0` |

Optional defaults: `GROQ_RECAP_MODEL=openai/gpt-oss-120b`,
`SPEECH_CLAUSE_WAIT_MS=350`, `SONIOX_MAX_ENDPOINT_DELAY_MS=1000`,
`CARTESIA_SAMPLE_RATE=24000`, and `CARTESIA_SPEED=1.0`. Leave emotion neutral.
See [.env.example](../.env.example) for the full configuration.
`CARTESIA_VOICE_MAP` accepts language-to-voice JSON overrides. Old
`CHANNEL_1_CARTESIA_*` / `CHANNEL_2_CARTESIA_*` overrides take precedence over
global settings; remove them if you intend to use the global voice/model.

Let Render supply `PORT`. Keep the service and database on a compatible private
network/region. Health check: `/api/v1/health`. Render startup rejects missing
PostgreSQL configuration and missing/short JWT secrets. Preserve `JWT_SECRET`
across deploys to preserve existing logins. Internal database hostnames are for
Render services; local tests use isolated SQLite.

Local `.env` is excluded from Git and Docker; updating it does **not** update
Render. Never put provider keys, JWT secrets, or database URLs into `VITE_*`.
For an optional separate frontend, restrict `CORS_ORIGINS` to its HTTPS origin.

## Manual deployment and call test

1. Add `GROQ_API_KEY` to the existing Render service; retain your working speech,
   database, and JWT settings. Manually deploy **latest main**. Its committed
   frontend bundle is included, so this updates both UI and backend.
2. Open the health URL and let the free instance wake up. Expect
   `transport: "websocket"`, `database: "postgresql"`,
   `speech_configured: true`, and `recap_configured: true` when Groq is set.
   These booleans check configuration presence, not provider validity or credits.
3. Use two accounts on different devices/browser profiles with headphones.
   Test English↔Hindi, short turns, long turns, names, numbers, negation,
   self-corrections, repeated sentences, and mixed-language speech. Compare
   original captions with translation to distinguish recognition errors.
4. Test overlapping speech, intentional "hmm", coughs, and deliberate
   interruptions. Acknowledgements should remain audible; noise alone should
   not cancel playback. Sustained recognized speech can cancel stale audio
   from other speakers in that room. Check whether the delay feels natural.
5. Test mute, output mute, listen-only, language switching, denied permission
   and retry, network recovery, refresh, and joining the same account in a
   second tab. The newer tab owns the subscription.
6. Save a transcript line with **Save moment**. End the room as host and verify
   capture/playback stop and the conversation review opens. Choose **Generate
   AI recap**. Check language, corrections, follow-ups, quotes, source links,
   export, and reload persistence. Ending the room itself does not wait for Groq.
7. Reopen **History & Moments**. Bookmarks are private per account. Individual
   and bulk history removal affect only the caller; saved moments and shared
   conversations remain. Explicit rejoining restores a hidden history entry.
8. Leave as host, rejoin after a friend, and verify **End conversation** remains
   available to the persisted creator. Guests cannot end the room or clear
   everyone's transcript. Shared transcript clearing invalidates recaps and
   bookmarks for everyone, and the UI explains that distinction.
9. Test passcode protection and a room with recording disabled. Non-recorded
   conversations have no persisted transcript, saved moments, or AI recaps.
   Verify missing Groq configuration and free-tier failures show a retryable
   error rather than invented AI output.

No Render deployment or production database operation was performed during local
verification. PostgreSQL connectivity, real acoustic echo, mobile device behavior,
and complete microphone-to-listener latency still need this deployment test.

## Behavior and limits

Use **one worker and one backend instance**: live room state is process-local.
Current admission limits are eight participants and four listening languages
(`REALTIME_MAX_LANGUAGES`). These are guardrails, not capacity guarantees.
Soniox opens a stream per speaker/target language, shared among listeners.
Provider concurrency limits and credits can lower practical capacity.

Microphone PCM travels in 100-ms frames. Stable word-safe clauses can be released
after a 350-ms wait when enough finalized text exists; Cartesia continues the
same speech context across clauses in an utterance. Partial STT is never voiced.
Translation still needs linguistic context. Bounded queues visibly skip stale
audio during overload. No fixed accuracy or sub-second end-to-end latency is
promised.

Interruption handling requires sustained microphone activity plus fresh recognized
text. Short acknowledgements are translated/spoken but do not erase another
speaker's audio. Local speech gently lowers incoming playback. Intent detection
is a conservative heuristic; already-played sound cannot be undone.

Startup adds `saved_moments` and `conversation_recaps` tables without replacing
existing tables. Recaps run in one separate worker, with at most three pending
jobs and persisted status. Restarted jobs become retryable failures.
Large transcripts are processed in batches rather than silently truncated.
The current limit is 48 batches of roughly 5,500 UTF-8 bytes, with a generation
deadline and explicit errors. Free-tier rate limits may delay or reject requests.
Source IDs and exact quotes are validated; semantic accuracy still requires
review. Saved-moment lists return up to 500 entries.

The legacy insights endpoint retains extractive notes for API compatibility.
Typed translation uses the existing Cartesia-to-Soniox voice bridge, which is
slower and can change wording. Live microphone speech is the primary path.

## Optional separate frontend

Only if you deliberately use a separate static service, set `VITE_API_BASE_URL`
to the backend HTTPS **origin**, without `/api` or a trailing slash, and rebuild.
The client derives its `wss://` endpoint from that origin. Use Node 20.19+ or a
supported newer LTS:

```sh
npm --prefix frontend ci --include=dev
npm run build
```

Publish `backend/web`; configure the SPA rewrite `/*` → `/index.html`.
`/audio-worklet.js` must be served publicly from the frontend origin.

## Reproduce local checks

```sh
python -m pytest -q
npm --prefix frontend test
npm run build
npm --prefix frontend audit
npm --prefix frontend exec playwright install chromium
npm --prefix frontend run test:e2e
```

Activate `.venv` and install test dependencies first. Browser tests use mocked
room/provider events and a fake browser microphone, including AudioWorklet
capture. On Windows, installed Chrome can be used with
`$env:PLAYWRIGHT_CHANNEL='chrome'` before running the browser suite.

Opt-in provider checks consume credits/quota using synthetic content only;
they do not access the database:

```sh
python scripts/check_speech_providers.py --live --language hi --streaming
python scripts/check_recap_provider.py --live --language hi
```

Observed on 2026-10-24: the streaming check preserved an intentional acknowledgement,
a number correction, and negation. Three stable phrases produced first PCM after
187, 172, and 156 ms respectively, with voiced text matching the complete final
translation. These timings start **after stable translated text is available**;
they exclude recognition, Render transport, and browser playback. The synthetic
Hindi Groq recap took 1,359 ms, with validated quotes and corrected numbers.
These are individual successful probes, not averages or production benchmarks.

See [the audit and verification record](KINESIA_AUDIT_FIXES.md) for final
automated test results and the preserved working checkpoint.
