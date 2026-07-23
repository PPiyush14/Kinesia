# Raspberry Pi Voice Translator

Small Python project for a Raspberry Pi 3 that captures speech from a microphone, sends it to Soniox for speech recognition, translates the transcript with a Gemma chat-completions endpoint, and plays the translated speech with either Cartesia or Google Cloud Text-to-Speech.

This project treats the two requested channels as two participant stations:

- Channel 1: person 1 microphone + person 1 fallback language + person 1 speaker
- Channel 2: person 2 microphone + person 2 fallback language + person 2 speaker

Each person should ideally have their own microphone and speaker, both connected to the same Raspberry Pi. The main runtime now uses two separate commands:

- one process for channel 1
- one process for channel 2

Each process listens only to its own microphone, streams live interim transcript text from Soniox to the console, translates that speaker's final utterance, and plays the translated speech on the opposite speaker. Each turn now uses a fresh Soniox live session instead of trying to keep one websocket open forever, which is more stable for long-running Linux use. The two processes share a small session-state file so each side can still translate into the other person's latest detected language.

## Flow

1. Run one terminal for `--channel 1` and another terminal for `--channel 2`.
2. Each process keeps only its own microphone armed and streams speech live to Soniox.
3. Soniox interim transcript updates are printed live so you can confirm the channel is hearing speech.
4. When Soniox finalizes the utterance, the speaker language is auto-detected for that turn and saved to the shared session-state file.
5. Translate into the other participant's latest detected language, or their configured fallback language if they have not spoken yet.
6. Synthesize that translated speech with the other participant's configured TTS provider.
7. Play the returned WAV audio on the other participant's speaker.

## Project layout

```text
pi_translator/
  run_translator.py
  src/pi_translator/
    audio.py
    cartesia_client.py
    cli.py
    config.py
    conversation_store.py
    dashboard_server.py
    gemma_client.py
    google_tts_client.py
    pipeline.py
    soniox_client.py
    tts_client.py
    web/
      app.js
      index.html
      styles.css
  .env
  .env.example
  requirements.txt
```

## Raspberry Pi 3 setup

Install system packages first on Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev portaudio19-dev libportaudio2 libasound2-dev ffmpeg
```

Create a virtual environment and install dependencies:

```bash
cd pi_translator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure `.env`

Edit [`.env`](./.env) and fill in:

- `SONIOX_API_KEY`
- `GEMMA_MODEL`
- `TTS_PROVIDER`
- `GOOGLE_TTS_CREDENTIALS_PATH` if `TTS_PROVIDER=google`
- `CARTESIA_API_KEY` if `TTS_PROVIDER=cartesia`
- `CHANNEL_1_CARTESIA_VOICE_ID` and `CHANNEL_2_CARTESIA_VOICE_ID` if `TTS_PROVIDER=cartesia`
- `CHANNEL_1_LANGUAGE_MODE` and `CHANNEL_2_LANGUAGE_MODE` if you want the non-dashboard runtime to default to `auto` or `fixed`

The Gemma base URL already matches the endpoint you provided:

```env
GEMMA_RESPONSE_BASE_URL=http://43.242.226.49:8100/v1
```

If your Gemma gateway requires a bearer token, put it in `GEMMA_RESPONSE_API_KEY`. If it does not, you can leave that field empty.

Set `TTS_PROVIDER=cartesia` or `TTS_PROVIDER=google` in `.env`.

If you use Google TTS, point `GOOGLE_TTS_CREDENTIALS_PATH` to your service-account JSON file. By default the runtime now uses the listener's output language for Google TTS and auto-selects these voices:

- Hindi: `hi-IN-Standard-E`
- Telugu: `te-IN-Standard-A`
- All other resolved output languages: `<language-code>-Wavenet-A`

You can still override this with `CHANNEL_X_GOOGLE_TTS_LANGUAGE_CODE`, `CHANNEL_X_GOOGLE_TTS_VOICE_NAME`, and `CHANNEL_X_GOOGLE_TTS_VOICE_GENDER` if needed.

For Cartesia voices, query your account voices or public voices and pick IDs that match each participant's usual fallback language. The example `.env` uses:

- Channel 1: Person 1 default language = English
- Channel 2: Person 2 default language = Hindi

Set `CHANNEL_1_SOURCE_LANGUAGE` and `CHANNEL_2_SOURCE_LANGUAGE` to the two participants' likely fallback languages. These values are only used before a participant has spoken yet, or if Soniox does not return a language for a turn. After each detected utterance, that participant's active language is updated to the newly detected language. `CHANNEL_X_SONIOX_LANGUAGE_HINTS` now defaults to Indian-language hints: `hi,bn,ta,te,gu,kn,ml,mr,pa`.

The Soniox and TTS pieces now follow the same style as the root [cartesia.py](</C:/Users/marky/OneDrive/Desktop/New folder/cartesia.py>) and [cartesiaWs.py](</C:/Users/marky/OneDrive/Desktop/New folder/cartesiaWs.py>):

- Soniox uses websocket STT with `enable_language_identification`, endpoint detection, periodic keepalive, and explicit `finalize` messages.
- Cartesia uses the same `CartesiaSyncWS`-style websocket lifecycle as [cartesiaWs.py](</C:/Users/marky/OneDrive/Desktop/New folder/cartesiaWs.py>), including bearer auth headers, one-request-per-connection streaming, and chunk decoding from websocket messages.
- Google TTS uses service-account credentials from `.env` and synthesizes PCM/WAV-ready audio directly for local playback.

Useful tuning values in `.env`:

- `SONIOX_MAX_ENDPOINT_DELAY_MS`
- `SONIOX_KEEPALIVE_SECONDS`
- `STT_STREAM_CHUNK_MS`
- `SESSION_STATE_FILE`
- `SESSION_JOIN_WINDOW_SECONDS`
- `CHANNEL_X_SONIOX_LANGUAGE_HINTS` defaults to Indian-language hints: `hi,bn,ta,te,gu,kn,ml,mr,pa`
- `CHANNEL_X_INPUT_SAMPLE_RATE` is optional; leave it blank to auto-detect a supported mic rate for that device
- `WAIT_FOR_SPEECH_SECONDS` set to `0` to keep the mic waiting indefinitely for the next speaker
- `GEMMA_MAX_TOKENS` defaults to `128`
- `CHANNEL_X_LANGUAGE_MODE` where `auto` uses detected speech language and `fixed` pins the configured language
- `TTS_PROVIDER`
- `GOOGLE_TTS_CREDENTIALS_PATH`
- `GOOGLE_TTS_SPEAKING_RATE`
- `GOOGLE_TTS_PITCH`
- `CARTESIA_WEBSOCKET_URL`
- `CARTESIA_SPEED`
- `CARTESIA_CONTEXT_PREFIX`
- `CARTESIA_EMOTIONS`

## Usage

List audio devices:

```bash
python run_translator.py --list-devices
```

Run channel 1 in one terminal:

```bash
python run_translator.py --channel 1
```

Run channel 2 in another terminal:

```bash
python run_translator.py --channel 2
```

Optional older queued mode:

```bash
python run_translator.py --auto-detect
```

Run the local dashboard frontend:

```bash
python run_translator.py --web
```

Optional custom bind address:

```bash
python run_translator.py --web --host 127.0.0.1 --port 8765
```

The dashboard lets you:

- start or stop each channel separately or both together
- pick input microphones and output speakers from the browser
- choose per-channel language mode as `auto detect` or `fixed language`
- create a new session or end the current one
- watch live per-channel logs and inspect the raw JSON conversation for the current session

## Notes

- Raspberry Pi 3 is not a good target for running Gemma locally, so this project assumes Soniox, Gemma, and Cartesia are remote services.
- The main runtime is now two separate commands: `python run_translator.py --channel 1` and `python run_translator.py --channel 2`.
- On a single Raspberry Pi, you should still set both `CHANNEL_1_INPUT_DEVICE` and `CHANNEL_2_INPUT_DEVICE`, plus both output devices, so each process can use its own input and the opposite output.
- On Debian or Raspberry Pi OS, if you see an error like `Invalid number of channels`, the selected ALSA device usually has `inputs=0` or `outputs=0`. Run `python run_translator.py --list-devices` and set each `CHANNEL_X_INPUT_DEVICE` to a device with `inputs>=1` and each `CHANNEL_X_OUTPUT_DEVICE` to a device with `outputs>=1`.
- On Linux, ALSA card ids and PortAudio device indexes are often different. `CHANNEL_X_INPUT_DEVICE` and `CHANNEL_X_OUTPUT_DEVICE` can now be either the exact `--list-devices` index or a unique device-name fragment.
- The per-channel mode uses Soniox live streaming and prints live interim transcript text to the console so you can confirm the mic is picking up speech before translation finishes.
- The live channel process now handles one utterance per Soniox session, then starts a fresh live session for the next utterance. This is slower than one endless websocket, but much more reliable for long-running unattended use.
- Each translated turn now logs Gemma latency, TTS latency, playback time, and total post-transcript turn time so you can tune real-world responsiveness.
- The live channel process pauses its own capture while it is processing and playing back that turn, which helps avoid self-overlap on the same direction.
- If a live channel stream fails temporarily, that channel process stays alive and retries instead of exiting immediately. Watch the console for `Warning:` lines naming the failing channel.
- `SESSION_STATE_FILE` is created automatically and stores the latest detected language for each channel so the two channel processes can stay in sync.
- `session_conversations/` is created automatically by the dashboard mode and stores one JSON file per session with transcript, translation, language metadata, and latency details for each turn.
- A new session is created automatically when the saved session is stale. By default, if the previous session file has been inactive for more than `300` seconds, learned languages are cleared and the conversation starts again from the fallback source languages in `.env`.
- If each microphone prefers a different rate, leave `CHANNEL_X_INPUT_SAMPLE_RATE` blank for automatic detection, or set a fixed per-channel value like `48000` or `16000`.
- Each new utterance can update that speaker's active language, so if a person switches from Hindi to English on a later turn, future translations to that person will follow the latest detected language instead of staying stuck on the first one.
- Cartesia now normalizes locale-like language values such as `hi-IN` or `en-IN` down to supported base codes before synthesis, and falls back safely if Soniox returns a variant Cartesia does not accept.
- Physical speaker-to-mic bleed can still happen in open-air deployments. For best real-world simultaneous behavior, keep each mic close to the speaker's mouth, keep playback volume moderate, or use directional mics or headsets. Full echo cancellation is not implemented in this scaffold.
- The microphone detector is intentionally simple. If your room is noisy, tune `VOICE_ACTIVITY_THRESHOLD` and `VOICE_SILENCE_SECONDS` in `.env`.
- `WAIT_FOR_SPEECH_SECONDS=0` means the channel never times out while waiting for speech. Set a positive number if you want it to give up after that many seconds of silence before anyone starts talking.
- Soniox realtime STT and Cartesia realtime websocket TTS in this scaffold were updated to match the reference patterns in the root [cartesia.py](</C:/Users/marky/OneDrive/Desktop/New folder/cartesia.py>), [cartesiaWs.py](</C:/Users/marky/OneDrive/Desktop/New folder/cartesiaWs.py>), and the official docs current on July 16, 2026.
