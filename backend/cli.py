from __future__ import annotations

import argparse
import logging
import time

from backend.audio import MicrophoneRecorder
from backend.config import AppConfig, ChannelConfig, load_config
from backend.pipeline import (
    ListenerWarning,
    TranslationRunResult,
    TranslatorPipeline,
    TurnProcessingFailure,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_translator.py",
        description="Kinesia: Real-Time Dual-Channel Speech Translation & Conversational Intelligence Platform.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List microphone and speaker devices and exit.",
    )
    parser.add_argument(
        "--channel",
        choices=("1", "2"),
        help="Run a single live translation direction for one configured channel.",
    )
    parser.add_argument(
        "--auto-detect",
        action="store_true",
        help="Run the older single-process dual-listener queue mode.",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run the local dashboard frontend and API server (default).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Dashboard host binding when --web is used. Default: 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Dashboard port when --web is used. Default: 8765",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_devices:
        _list_devices()
        return

    if sum(bool(option) for option in (args.channel, args.auto_detect, args.web)) > 1:
        parser.error("Use only one of --channel, --auto-detect, or --web.")
    if not args.channel and not args.auto_detect and not args.web:
        # Default to Web Studio dashboard if no specific mode selected
        args.web = True

    config = load_config()
    _configure_logging(config)
    if args.web:
        _run_dashboard(config, host=args.host, port=args.port)
        return
    _validate_runtime_config(config, parser, args.channel)
    pipeline = TranslatorPipeline(config)

    if args.channel:
        _run_single_channel_loop(pipeline, args.channel)
        return

    _auto_detect_loop(pipeline)


def _validate_runtime_config(
    config: AppConfig,
    parser: argparse.ArgumentParser,
    channel_key: str | None,
) -> None:
    missing = config.missing_runtime_values(channel_key)
    if missing:
        parser.error(
            "Missing required configuration in .env: "
            + ", ".join(sorted(set(missing)))
        )
    _validate_audio_devices(config, parser, channel_key)


def _validate_audio_devices(
    config: AppConfig,
    parser: argparse.ArgumentParser,
    channel_key: str | None,
) -> None:
    if channel_key is None:
        channels = list(config.channels.values())
        for channel in channels:
            _validate_input_device(config, channel, parser)
            _validate_output_device(config, channel, parser)
        return

    speaker_channel = config.get_channel(channel_key)
    listener_channel = config.get_peer_channel(channel_key)
    _validate_input_device(config, speaker_channel, parser)
    _validate_output_device(config, listener_channel, parser)


def _validate_input_device(
    config: AppConfig,
    channel: ChannelConfig,
    parser: argparse.ArgumentParser,
) -> None:
    try:
        input_info = MicrophoneRecorder.get_device_info(
            channel.input_device,
            kind="input",
        )
    except RuntimeError as exc:
        parser.error(
            f"{exc} Set {channel.key}'s input device with "
            f"CHANNEL_{channel.key}_INPUT_DEVICE after checking --list-devices."
        )

    max_input_channels = int(input_info.get("max_input_channels") or 0)
    if max_input_channels < config.mic_channels:
        parser.error(
            f"Configured input device for channel {channel.key} "
            f"({input_info.get('name')}) supports only {max_input_channels} input "
            f"channel(s), but MIC_CHANNELS={config.mic_channels}. "
            "On Debian/ALSA this usually means the wrong device was selected. "
            f"Use --list-devices and set CHANNEL_{channel.key}_INPUT_DEVICE "
            "to a real microphone device."
        )

    try:
        resolved_input_device = MicrophoneRecorder.resolve_device(
            channel.input_device,
            kind="input",
        )
        MicrophoneRecorder.resolve_input_sample_rate(
            resolved_input_device,
            channels=config.mic_channels,
            preferred_sample_rate=channel.input_sample_rate or config.mic_sample_rate,
        )
    except RuntimeError as exc:
        parser.error(
            f"Unable to find a supported input sample rate for channel {channel.key}: {exc} "
            f"Set CHANNEL_{channel.key}_INPUT_SAMPLE_RATE or choose a different input device."
        )


def _validate_output_device(
    config: AppConfig,
    channel: ChannelConfig,
    parser: argparse.ArgumentParser,
) -> None:
    try:
        output_info = MicrophoneRecorder.get_device_info(
            channel.output_device,
            kind="output",
        )
    except RuntimeError as exc:
        parser.error(
            f"{exc} Set {channel.key}'s output device with "
            f"CHANNEL_{channel.key}_OUTPUT_DEVICE after checking --list-devices."
        )

    max_output_channels = int(output_info.get("max_output_channels") or 0)
    if max_output_channels < 1:
        parser.error(
            f"Configured output device for channel {channel.key} "
            f"({output_info.get('name')}) has no output channels. "
            "On Debian/ALSA this usually means an input-only device was selected. "
            f"Use --list-devices and set CHANNEL_{channel.key}_OUTPUT_DEVICE "
            "to a speaker or headphone device."
        )


def _list_devices() -> None:
    devices = MicrophoneRecorder.list_audio_devices()
    for index, device in enumerate(devices):
        host_api = device.get("hostapi")
        print(
            f"[{index}] {device.get('name')} | inputs={device.get('max_input_channels')} "
            f"| outputs={device.get('max_output_channels')} | hostapi={host_api}"
        )


def _configure_logging(config: AppConfig) -> None:
    level_name = (config.log_level or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _run_dashboard(
    config: AppConfig,
    *,
    host: str,
    port: int,
) -> None:
    from backend.dashboard_server import run_dashboard_server

    run_dashboard_server(config, host=host, port=port)


def _run_single_channel_loop(
    pipeline: TranslatorPipeline,
    channel_key: str,
) -> None:
    speaker_channel = pipeline.config.get_channel(channel_key)
    listener_channel = pipeline.config.get_peer_channel(channel_key)
    resolved_input_device = pipeline.recorder.resolve_device(
        speaker_channel.input_device,
        kind="input",
    )
    active_input_sample_rate = pipeline.recorder.resolve_input_sample_rate(
        resolved_input_device,
        channels=pipeline.config.mic_channels,
        preferred_sample_rate=speaker_channel.input_sample_rate or pipeline.config.mic_sample_rate,
    )
    transcript_state = {
        "last": "",
        "showing": False,
    }

    print(f"Single-channel translator for {speaker_channel.name}")
    print(
        f"Listening only on channel {speaker_channel.key} input "
        f"and playing translated speech on channel {listener_channel.key} output."
    )
    print(
        f"Shared session language state file: {pipeline.config.session_state_path}"
    )
    print(
        f"Session id: {pipeline.session_info.session_id} "
        f"({'new' if pipeline.session_info.is_new_session else 'joined existing'})"
    )
    print(
        f"Route: {speaker_channel.name} microphone -> {listener_channel.name} speaker"
    )
    print(f"Input device: {resolved_input_device!r}")
    print(f"Active input sample rate: {active_input_sample_rate} Hz")
    print("Press Ctrl+C to stop.")

    def clear_interim_line() -> None:
        if transcript_state["showing"]:
            print()
            transcript_state["showing"] = False
            transcript_state["last"] = ""

    def handle_interim(text: str) -> None:
        interim = text.strip()
        if interim == transcript_state["last"]:
            return
        transcript_state["last"] = interim
        if not interim:
            clear_interim_line()
            return
        print(
            f"\rLive transcript [{speaker_channel.name}]: {interim}",
            end="",
            flush=True,
        )
        transcript_state["showing"] = True

    def handle_result(result: TranslationRunResult) -> None:
        clear_interim_line()
        if result.detected_source_language:
            print(f"Detected source language: {result.detected_source_language}")
        if result.speaker_language_updated_this_turn:
            print(
                f"Updated {result.speaker_channel_name}'s active language to "
                f"{result.speaker_session_language}."
            )
        print(f"Final transcript: {result.transcript}")
        print(f"Translated for {result.listener_channel_name}: {result.translation}")
        print(
            f"Latency: Gemma {result.gemma_latency_ms:.0f} ms | "
            f"{result.tts_provider} TTS {result.tts_latency_ms:.0f} ms | "
            f"playback {result.playback_latency_ms:.0f} ms | total {result.turn_latency_ms:.0f} ms"
        )
        print(
            f"Played on channel {result.listener_channel_key} output in "
            f"{result.listener_language}.\n"
        )

    while True:
        try:
            result = pipeline.run_once_live(
                channel_key,
                on_interim=handle_interim,
            )
            handle_result(result)
        except KeyboardInterrupt:
            clear_interim_line()
            print(f"\nStopped translator for {speaker_channel.name}.")
            return
        except RuntimeError as exc:
            clear_interim_line()
            print(
                f"\nWarning: {speaker_channel.name}'s live stream failed: {exc}"
            )
            print("Retrying in 2.0s...\n")
            time.sleep(2.0)


def _auto_detect_loop(pipeline: TranslatorPipeline) -> None:
    print("Auto-detect two-person translator")
    print(
        "Listening on both configured microphone channels simultaneously. "
        "Captured turns are queued in the order they start."
    )
    print(
        "Channel 1 input routes to Channel 2 speaker, "
        "and Channel 2 input routes to Channel 1 speaker."
    )
    print(
        f"Shared session language state file: {pipeline.config.session_state_path}"
    )
    print(
        f"Session id: {pipeline.session_info.session_id} "
        f"({'new' if pipeline.session_info.is_new_session else 'joined existing'})"
    )
    print("Press Ctrl+C to stop.")
    queue_state = {
        "captured": 0,
        "processed": 0,
    }

    def handle_turn_captured(detected_turn) -> None:
        queue_state["captured"] += 1
        speaker = pipeline.config.get_channel(detected_turn.speaker_channel_key)
        listener = pipeline.config.get_peer_channel(detected_turn.speaker_channel_key)
        pending = max(0, queue_state["captured"] - queue_state["processed"])
        print(
            f"\nQueued turn from channel {speaker.key}: {speaker.name} -> {listener.name} "
            f"(pending turns: {pending})"
        )

    def handle_result(result: TranslationRunResult) -> None:
        queue_state["processed"] += 1
        if result.detected_source_language:
            print(f"Detected source language: {result.detected_source_language}")
        if result.speaker_language_updated_this_turn:
            print(
                f"Updated {result.speaker_channel_name}'s active language to "
                f"{result.speaker_session_language}."
            )
        print(f"Transcript: {result.transcript}")
        print(f"Translation: {result.translation}")
        print(
            f"Latency: Gemma {result.gemma_latency_ms:.0f} ms | "
            f"{result.tts_provider} TTS {result.tts_latency_ms:.0f} ms | "
            f"playback {result.playback_latency_ms:.0f} ms | total {result.turn_latency_ms:.0f} ms"
        )
        print(
            f"Audio played successfully on {result.listener_channel_name}'s speaker "
            f"in {result.listener_language}. "
            f"(remaining queued turns: {max(0, queue_state['captured'] - queue_state['processed'])})\n"
        )

    def handle_listener_warning(warning: ListenerWarning) -> None:
        channel = pipeline.config.get_channel(warning.channel_key)
        print(
            f"\nWarning: {channel.name}'s microphone listener hit an error and will retry "
            f"in {warning.retry_delay_seconds:.1f}s "
            f"(failure #{warning.consecutive_failures}): {warning.error}"
        )

    def handle_turn_failure(failure: TurnProcessingFailure) -> None:
        speaker = pipeline.config.get_channel(failure.detected_turn.speaker_channel_key)
        listener = pipeline.config.get_peer_channel(failure.detected_turn.speaker_channel_key)
        queue_state["processed"] += 1
        print(
            f"\nWarning: failed to process {speaker.name}'s turn for {listener.name}: "
            f"{failure.error}"
        )
        print(
            f"Continuing to listen. "
            f"(remaining queued turns: {max(0, queue_state['captured'] - queue_state['processed'])})\n"
        )

    try:
        pipeline.run_simultaneous_conversation(
            on_turn_captured=handle_turn_captured,
            on_result=handle_result,
            on_listener_warning=handle_listener_warning,
            on_turn_failure=handle_turn_failure,
        )
    except KeyboardInterrupt:
        print("\nStopped auto-detect translator.")
    except RuntimeError as exc:
        print(f"\nTranslator stopped because of a runtime error: {exc}")


if __name__ == "__main__":
    main()
