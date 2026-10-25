# Kinesia — REST & SSE API Reference

> **Version:** `1.0.0`  
> **Base Path:** `/api/v1/`  
> **Data Transport:** JSON over HTTP/1.1 · Server-Sent Events (SSE)

## Current browser API

The deployed browser product uses `/ws/rooms/{room_code}` for authenticated
microphone PCM, captions, streaming translated audio, and room events. REST
routes manage accounts, rooms, transcript history, recaps, and saved moments.
See the [current route table](../README.md#api-overview) and the TypeScript
client in `frontend/src/services/api.ts` for request/response types.

- `GET/POST /api/v1/rooms/{code}/recap`: read/request a recap. GET accepts
  `output_language`; POST accepts `{ "language": "en" }`. Generation requires
  an ended, recorded room and membership. Pending, ready, and failed states are
  explicit; generated citations resolve to real transcript passages.
- `GET /api/v1/moments` and `GET/POST /api/v1/rooms/{code}/moments`: private
  bookmarks; POST accepts `{ "turn_id": "..." }`. DELETE the turn-ID suffix to
  remove the caller's bookmark.
- `DELETE /api/v1/rooms/{code}` and `/api/v1/rooms/clear-all`: personal history
  removal. `DELETE /api/v1/rooms/{code}/transcripts` clears shared source and
  derived data and requires the host. These operations have different scopes.
- `POST /api/v1/rooms/{code}/end`: host-only and retryable after the room has
  ended. Extractive session notes are separate from an explicitly generated
  AI recap.

API calls use `Authorization: Bearer <token>`. The WebSocket authenticates in
its first JSON message, rather than putting credentials in the URL. Current
WebSocket controls and binary framing are implemented in
`backend/realtime_server.py`, `frontend/src/hooks/useRealtimeRoom.ts`, and
`frontend/src/lib/realtimeAudio.ts`.

Most REST routes use the envelope below. ASGI authentication/validation errors
may instead return `{ "detail": "..." }`; the browser client handles both.
Health configuration flags indicate presence of settings, not provider credits
or successful live synthesis. See [deployment checks](RENDER_DEPLOYMENT.md).

## REST/SSE compatibility reference

The workstation hardware/channel routes below are retained for compatibility.
They do not describe the current browser microphone transport. Their REST
responses use this envelope:

```json
{
  "ok": true,
  "data": { ... },
  "error": null
}
```

When an error occurs (`HTTP 4xx` or `5xx`), `ok` is `false`, `data` is `null`, and `error` contains a descriptive diagnostic message:

```json
{
  "ok": false,
  "data": null,
  "error": "Turn index 99 out of range for session '20261020-053000-a1b2c3'"
}
```

---

## 1. System & Hardware Endpoints

### `GET /api/v1/bootstrap`
Returns static system definitions, available host audio hardware, supported language mappings, and current glossary entries.

- **Response `data`:**
  - `devices`: `{"inputs": [...], "outputs": [...], "error": null, "refreshed_at": float}`
  - `languages`: Array of supported languages `[{"code": "en", "name": "English"}, {"code": "hi", "name": "Hindi"}, ...]`.
  - `glossary`: Current glossary entries array.
  - `defaults`: Default system configuration constants.

---

### `GET /api/v1/state`
Returns the real-time operational status of the dual channels, active session metadata, and learned participant languages.

- **Response `data`:**
  - `channels`: Dictionary keyed by channel (`"1"`, `"2"`) containing:
    - `running`: boolean
    - `input_device`: integer or string
    - `output_device`: integer or string
    - `language_mode`: `"auto"` | `"fixed"`
    - `language_code`: ISO language code
    - `current_language`: Resolved language code
    - `has_learned_language`: boolean
    - `live_status`: `"idle"` | `"listening"` | `"speaking"` | `"translating"`
    - `live_transcript`: Real-time streaming partial transcript
  - `session`: `{"session_id": str, "started_at": float, "updated_at": float, "ended_at": float | null}`
  - `audio_saving`: boolean

---

### `POST /api/v1/settings`
Updates channel device bindings and language configurations at runtime.

- **Request Body:**
  ```json
  {
    "channel_1_input_device": 1,
    "channel_1_output_device": 3,
    "channel_1_language_mode": "auto",
    "channel_1_source_language": "en",
    "channel_2_input_device": 2,
    "channel_2_output_device": 4,
    "channel_2_language_mode": "auto",
    "channel_2_source_language": "hi"
  }
  ```

---

### `POST /api/v1/devices/refresh`
Triggers an immediate re-scan of the operating system's audio input and output devices.

---

## 2. Channel & Session Lifecycle

### `POST /api/v1/channels/start-all`
Activates background listening threads for both channels.

### `POST /api/v1/channels/stop-all`
Gracefully stops listening loops across both channels.

### `POST /api/v1/channels/{channel_key}/start`
Starts a specific channel's speech capture loop (`"1"` or `"2"`).

### `POST /api/v1/channels/{channel_key}/stop`
Stops a specific channel's speech capture loop (`"1"` or `"2"`).

### `POST /api/v1/session/new`
Forces generation of a new session identifier and resets participant language memory.

### `POST /api/v1/session/end`
Marks the current session as ended, automatically generates an LLM session summary, and dispatches an asynchronous webhook (if configured).

---

## 3. Conversations & Turns

### `GET /api/v1/sessions`
Lists metadata for all persisted sessions stored in `session_conversations/`, sorted latest first.

- **Response `data`:**
  ```json
  [
    {
      "session_id": "20261020-053000-a1b2c3",
      "started_at": 1729402200.0,
      "ended_at": 1729403100.0,
      "updated_at": 1729403100.0,
      "turn_count": 18
    }
  ]
  ```

---

### `GET /api/v1/sessions/{session_id}`
Retrieves the full JSON transcript for a specific session, including turn timestamps, recognized text, translations, STT confidence scores, and extracted entities.

- **Response `data`:**
  ```json
  {
    "session_id": "20261020-053000-a1b2c3",
    "started_at": 1729402200.0,
    "ended_at": null,
    "turns": [
      {
        "turn_index": 0,
        "timestamp": 1729402205.12,
        "speaker_channel_key": "ch1",
        "listener_channel_key": "ch2",
        "detected_source_language": "en",
        "listener_language": "hi",
        "stt_confidence": 0.96,
        "transcript": "Hello, we need to approve the budget by Friday.",
        "translation": "नमस्ते, हमें शुक्रवार तक बजट को मंजूरी देनी होगी।",
        "audio_file": "turn_0000_ch1.wav",
        "entities": {
          "DATE": ["Friday"],
          "ORG": [],
          "MONEY": []
        },
        "telemetry": {
          "stt_latency_ms": 120.5,
          "translation_latency_ms": 45.2,
          "tts_latency_ms": 82.0,
          "total_latency_ms": 247.7
        }
      }
    ]
  }
  ```

---

### `PATCH /api/v1/sessions/{session_id}/turns/{turn_index}`
Overrides the detected source language on a turn and optionally requests the LLM to re-translate the original speech into the target language.

- **Request Body:**
  ```json
  {
    "detected_source_language": "hi",
    "retranslate": true
  }
  ```
- **Response `data`:** The updated turn object.

---

### `GET /api/v1/sessions/search?q={query}&lang={lang}`
Full-text search across all saved sessions and turns with optional language filtering.

- **Query Parameters:**
  - `q` (required): Search keyword or phrase (case-insensitive).
  - `lang` (optional): Two-letter ISO language code (e.g. `"en"` or `"hi"`).

---

### `GET /api/v1/audio/{session_id}/{filename}`
Streams recorded raw PCM WAV audio for a turn.

- **Headers:** `Content-Type: audio/wav`, `Cache-Control: public, max-age=86400`
- **Status:** `200 OK` on success, `404 Not Found` if audio was not recorded.

---

## 4. Intelligence & BI Analytics

### `GET /api/v1/insights/{session_id}`
Computes and returns comprehensive conversational business intelligence for the session.

- **Response `data`:**
  - `entity_frequencies`: Array of `{entity, type, count}` sorted by frequency.
  - `entity_types`: Breakdown count per category (`ORG`, `PER`, `LOC`, `DATE`, `MONEY`).
  - `timeline`: Turn-by-turn entity occurrences with speaker channels.
  - `language_distribution`: Turn count per language.
  - `confidence`: `{mean, min, max, low_confidence_turn_count}`.
  - `latency`: `{mean_ms, p50_ms, p90_ms, p99_ms}`.

---

### `GET /api/v1/insights/{session_id}/summary`
Fetches the saved AI executive summary, key discussion points, and action items.

- **Response `data`:**
  ```json
  {
    "session_id": "20261020-053000-a1b2c3",
    "executive_summary": "The discussion centered on Q3 financial targets...",
    "key_points": [
      "Budget authorization finalized for next quarter",
      "Vendor agreements scheduled for review"
    ],
    "action_items": [
      "Piyush to send revised timeline by Monday"
    ],
    "entities_discovered": { ... }
  }
  ```

---

### `POST /api/v1/insights/{session_id}/summarize`
Forces the Gemma LLM to re-summarize the session on demand.

---

## 5. Domain Translation Glossary

### `GET /api/v1/glossary`
Returns all registered glossary entries.

- **Response `data`:** Array of `{term: str, target_lang: str, translation: str, preserve: bool}`.

---

### `POST /api/v1/glossary`
Replaces the persistent glossary (`glossary.json`) with an updated list.

- **Request Body:** Array of entry objects or `{ "glossary": [ ... ] }`.

---

## 6. Real-Time Server-Sent Events (SSE)

### `GET /api/v1/events` (and `/api/events`)
Opens a persistent HTTP streaming connection with real-time UI updates.

- **Headers:** `Content-Type: text/event-stream`, `Cache-Control: no-cache`
- **Event Types:**
  - `state`: Streamed whenever channel status, audio devices, or active sessions change.
  - `turn`: Emitted immediately upon completion of a bilingual turn with transcript, translation, confidence, and entities.
  - `log`: Channel diagnostics, speech detection markers, and system events.
  - `refresh`: Emitted when an entity, glossary, or turn update requires the UI to refresh cached views.
  - `: keepalive`: Heartbeat comment sent every 15 seconds to prevent client timeout.
