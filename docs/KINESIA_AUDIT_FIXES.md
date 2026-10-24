# Kinesia pipeline audit and fixes

Provider decision after this audit: use Soniox for recognition/native speech
translation and Cartesia for speech output, preserving the current UI colours.
The provider migration is now implemented. The current meeting UI streams
microphone PCM over an authenticated WebSocket to Soniox native translation,
then streams Cartesia PCM to listeners. It no longer waits for complete uploaded
utterances or uses Groq/Edge in meeting mode. The existing REST/SSE contracts
remain available for compatibility.

See [Render deployment and current validation](RENDER_DEPLOYMENT.md) for the
current architecture, configuration, verification, and remaining manual checks.
The sections below retain the earlier audit snapshot and its test results;
their Groq/Edge pipeline description and configuration are historical.

Verified against the working repository on 2026-10-24. This records implemented changes and actual checks, rather than treating the earlier summary's claims as test results.

## Actual web pipeline

Browser Web Audio/VAD captures an entire utterance, downsamples it to 16 kHz PCM WAV, and POSTs it to `/api/v1/rooms/{code}/speak-audio`. The coordinator opens a Soniox WebSocket for that finite clip, gathers final tokens, calls Groq through the existing OpenAI-compatible completion client, and synthesizes complete audio through the existing TTS router. Edge audio chunks are concatenated into one MP3. Per-user SSE `turn` events carry subtitles and an audio data URL. The listener plays provider audio or browser-speech fallback through one FIFO.

The browser does not stream microphone chunks directly to Soniox, and Groq/Edge playback is not incremental. No new model, provider, framework, deployment target, or database schema was introduced.

## Confirmed defects and implemented fixes

| Cause in the original source | Implemented behavior |
| --- | --- |
| `useSonioxAudioCapture` included the onset frame in both pre-roll and the initial utterance array. | Each frame appears once in the encoded WAV. |
| Late `getUserMedia` completions could install a capture processor after cleanup or supersession. | Capture generations invalidate stale permission results, stop late streams, and ignore callbacks from old processors. |
| Finite Soniox clips returned at the first `<end>`/`<fin>` and could drop later finalized text. Receives had no application deadline. | Finite clips disable endpoint detection, send an empty text end-of-audio frame, collect all final segments through `finished`, reject early close, and use bounded receives with worker cleanup. Partial tokens never become translations. |
| The live parser returned before consuming trailing tokens; an empty finalization did not release paused capture. Stop-watcher threads could outlive a connection. | Consume the full token response, acknowledge empty finalizations, stop connection-specific watchers, and allow cancellation during retry backoff. |
| Autoplay unlock called `play()` and then started the same queue head again. Browser speech used a separate queue. Old callbacks survived mute/unmount. | One FIFO owns each playback attempt, retries a blocked head once per unlock, sequences browser fallback with provider audio, skips failed media, and cancels queued/current output on mute, disconnect, session end, room change, or unmount. |
| SSE had no turn-ID deduplication, used stale language options, accepted callbacks from superseded sources, and allowed repeated retry scheduling. Speaking payload field names also disagreed. | Guard callbacks and timers by connection generation, deduplicate stable IDs across reconnects, use current language options, handle both speaker field formats and terminal event names, and reapply language/mute settings on open. |
| Rejoining replaced a participant queue, but an older SSE handler unconditionally removed the user on disconnect. Initial-write failures escaped cleanup. | Disconnect cleanup checks queue identity; reconnect preserves current settings and moves queued events; the entire stream write lifecycle is covered by cleanup. |
| Text-based deduplication deleted legitimate repeated words, repeated sentences, substrings, and prefix extensions. | Preserve transcript text; deduplicate optional `utterance_id` values per speaker before STT/translation/TTS. New browser requests supply UUIDs. Clients without IDs remain accepted. |
| Unrecorded-room turn IDs were millisecond timestamps. | Use unique UUIDs; recorded turns continue using their database IDs. |
| Concurrent turns could finish out of order or race session summary generation. Translation and TTS ran serially across languages and synthesized audio the speaker never receives. | Serialize complete turns within each room using a separate reentrant lock, keep other rooms/membership operations independent, run up to four language jobs concurrently, skip speaker-only synthesis, and wait for the current commit before summarizing. Ending rooms reject new work. |
| A full SSE queue could drop or greatly delay session termination. | Clear queued stale events before the first terminal event, preserve both existing terminal names, and close finished streams. |
| A null completion became the spoken string `None`; raw reasoning/truncated completions could reach TTS. Translation failure reused source text with the target voice. | Treat null as empty, request hidden reasoning for Groq Qwen3, strip explicit reasoning blocks defensively, reject token-limit truncation, and retain original captions without speaking a failed translation. |
| Edge synthesis used a broad `RuntimeError` catch that could call the provider again, and the router retried Edge as its own fallback. | Each Edge call gets a closed event loop and a 15-second async deadline; provider errors do not repeat synthesis. Configured voices and premium-provider fallbacks remain. |
| Unsupported compressed audio could be silently submitted as raw PCM. | Decode supported formats or use the existing Whisper fallback; never label undecodable container bytes as PCM. The browser's standard 16-bit WAV path is unchanged. |
| Late history loads replaced live turns; toggling mic/language reran history loading. | Fetch initial history on room/user changes, merge by event/database ID, preserve live records, and localize historical text from saved translations. |
| Private-room SSE/speech did not enforce a verified passcode grant. Insights were public, JWT fallback used a public constant, and logs could include SSE query tokens/transcript text. | Persist successful authenticated verification using existing membership records; enforce private-room access before provider calls; require membership for insights; use a random process signing key when unset; redact query tokens and remove transcript-content logging from room STT. |

Protocol references checked: [Soniox WebSocket API](https://soniox.com/docs/api-reference/stt/websocket-api), [Soniox manual finalization](https://soniox.com/docs/stt/rt/manual-finalization), and [Groq reasoning formats](https://console.groq.com/docs/reasoning). Provider-protocol changes are covered by simulated responses; live provider behavior remains a separate check.

## Changed files

- `backend/soniox_client.py`: finite-clip protocol, final/partial accumulation, deadlines, live finalization/cancellation/thread cleanup.
- `backend/rooms/coordinator.py`: request IDs, transcript preservation, turn ordering, parallel fan-out, queue ownership/backpressure, session completion, private-room authorization, and audio/translation failure behavior.
- `backend/dashboard_server.py`: SSE lifecycle, passcode grants, access enforcement, utterance ID forwarding, and token redaction.
- `backend/gemma_client.py`: safe completion content and scoped Groq reasoning configuration.
- `backend/edge_tts_client.py`, `backend/tts_client.py`: bounded synthesis, event-loop cleanup, and removal of duplicate provider attempts.
- `backend/db/auth.py`, `.env.example`: random fallback signing key and documentation of `JWT_SECRET`.
- `frontend/src/hooks/useSonioxAudioCapture.ts`, `useRoomSSE.ts`, new `useAudioPlaybackQueue.ts`: capture, reconnect, deduplication, and playback lifecycle.
- `frontend/src/features/dialogue/DialogueCanvas.tsx`, `frontend/src/lib/transcripts.ts`, `frontend/src/services/api.ts`, `frontend/src/types/index.ts`: integrate the fixes, preserve history, send IDs/authentication, and align event fields.
- `frontend/package.json`, new `frontend/tests/realtime.test.cjs`: dependency-free Node test command using the existing TypeScript compiler and browser/React lifecycle fakes.
- `tests/unit/test_soniox_client.py`, `test_voice_providers.py`, `test_room_coordinator.py`, `test_db_and_auth.py`, `tests/integration/test_kinesia_api.py`: protocol, provider, concurrency, request-retry, HTTP security, and cleanup regressions.
- `tests/conftest.py`: isolate all test database access in temporary SQLite paths, including older server integration tests.
- `backend/web/index.html` and its generated application JS: rebuilt production frontend; the previous hashed application bundle is replaced. Other deployment files are unchanged.
- This document: findings, commands, verification, and follow-up checks.

## Actual verification results

| Command/check | Result |
| --- | --- |
| Initial `git status --short` | No tracked modifications; user files `KINESIA_FULL_SUMMARY.md` and `kinesia_source_dump.txt` were untracked and left untouched. |
| Initial `python -m pytest tests/ -q` | Failed: global Python had no pytest. Used the existing project virtual environment. |
| Baseline `.\.venv\Scripts\python.exe -m pytest tests/ -q` | 34 passed. |
| Baseline `npm run typecheck` and `npm run build` | Passed. |
| First `npm --prefix frontend test` attempt | Failed because Node 20 on Windows did not expand the wildcard argument. The script now uses the explicit test filename. |
| Intermediate Soniox/coordinator/provider pytest runs | Passed: 15, 25, 30, and 22 tests at their respective stages. |
| Intermediate full backend suite | 62 passed before the final shutdown/parser regressions. |
| Final `.\.venv\Scripts\python.exe -m pytest tests/ -q` | **68 passed in 39.78 seconds**, including the final HTTP logging regression. |
| Final `npm --prefix frontend test` | **9 passed**, using browser/React lifecycle fakes. |
| Final `npm run typecheck` | Passed. |
| Final `npm run build` | Passed, TypeScript project build plus Vite; generated 1,956 modules. |
| `.\.venv\Scripts\python.exe -m compileall -q backend tests` | Passed. |
| `.\.venv\Scripts\python.exe -m ruff --version` | Unavailable: ruff is not installed. No lint result is claimed. No dependencies were installed. |
| Backend entry-point check | `python -m backend.dashboard_server --help` starts the server because this entry point has no argument parser. Local startup on port 8765 succeeded; it was stopped immediately. Generated empty-session artifacts were removed and pre-existing session-state contents restored. |
| `git diff --check` and final diff review | Passed. Only the superseded generated JS bundle is deleted; no source files were deleted. |
| Secret review | No tracked `.env`/private-key files. A filename-only scan of 107 tracked text files found no matching long provider-key/private-key patterns; this is not a proof that every possible secret format is absent. New test data uses placeholders. |

External provider calls are mocked in the new regressions. No production deployment or destructive database operation was performed.

## Configuration and local verification

Keep the existing `SONIOX_API_KEY`, `GEMMA_RESPONSE_API_KEY`, model/base-URL settings, and `TTS_PROVIDER=edge`. Set a unique random `JWT_SECRET` for persistent authentication. If unset or blank, tokens are process-local and restarting signs users out. Existing configured signing keys retain their behavior. No additional credentials are required.

From the repository root on Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
npm --prefix frontend test
npm run typecheck
npm run build
.\.venv\Scripts\python.exe run_kinesia.py --web --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` in two separate browser profiles with different accounts and listening languages. Speak a sentence with a short internal pause, then intentionally repeat it. Each intended turn should have one transcript and one playback. Check that intentional repetitions survive. Block autoplay, allow it through the banner, and verify the current utterance starts once. Change language/mute settings, interrupt the network, reconnect, and verify settings and single playback persist. End a session with queued speech and confirm playback stops and insights appear. Attempt access to a private room from a third account before and after correct passcode verification.

## Remaining limits and live checks

- Real microphones, echo/ducking behavior, mobile autoplay, provider accuracy, API quotas, current voice availability, and actual end-to-end latency require live testing. The hook tests simulate lifecycle behavior rather than running a real browser.
- Default VAD still waits 1,100 ms of silence. Non-streaming STT upload/completion/TTS and provider retries add latency; sub-second end-to-end delivery is not established. Serialized turns can queue during simultaneous speech; cross-language jobs are bounded at four per turn.
- Room state, recent event IDs, and the last 1,000 idempotent request results remain in memory. Restart/multi-process replay, durable delivery, and exact-once processing across crashes are not provided. Under ordinary queue overload non-terminal events can still be dropped.
- PostgreSQL, Docker/Render runtime behavior, proxy/SSE behavior, and load capacity were not exercised. Existing deployment configuration is retained.
- `ScriptProcessorNode`, fixed-threshold VAD, plaintext transcript storage, and provider data handling remain architectural limits. The fixes do not establish suitability for sensitive medical/legal use.
- Passcode grants use existing historical room membership. Previously stored memberships remain authorized; this change does not retroactively revoke them.
- The repository retains legacy single-process dashboard routes; this work does not constitute a full authorization redesign of every legacy route. Review exposure before hosting the legacy dashboard on an untrusted network.
