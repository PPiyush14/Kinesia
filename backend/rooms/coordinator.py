"""Multi-party Room Coordinator & Fan-Out Translation Engine for Kinesia.

Coordinates distributed participants, manages real-time SSE queues,
transcribes incoming speech, translates to all participants' target languages in parallel,
synthesizes localized audio, and generates post-call intelligence.
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from functools import wraps
from typing import Any

from backend.config import AppConfig, ChannelConfig
from backend.db.rooms import (
    add_participant,
    clear_room_transcripts,
    is_room_member,
    add_transcript,
    end_room,
    get_room_by_code,
    get_room_transcripts,
    get_session_insights,
    remove_participant,
    save_session_insights,
    save_stream_transcript,
    update_participant_languages,
)
from backend.gemma_client import GemmaClient
from backend.nlp.entity_extractor import EntityExtractor
from backend.nlp.summarizer import ConversationSummarizer
from backend.nlp.extractive_summary import ExtractiveSummarizer
from backend.tts_client import TtsClient

logger = logging.getLogger(__name__)


@dataclass
class ParticipantSession:
    user_id: str
    user_name: str
    avatar_color: str
    speaking_lang: str
    listening_lang: str
    gender: str = "female"
    is_muted: bool = False
    event_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=100))


@dataclass
class RoomSession:
    room_code: str
    room_id: str
    title: str
    record_session: bool
    participants: dict[str, ParticipantSession] = field(default_factory=dict)
    turn_lock: Any = field(default_factory=threading.RLock, repr=False)
    processed_utterances: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    ending: bool = False
    created_at: float = field(default_factory=time.time)
    stream_turns: dict[str, dict[str, Any]] = field(default_factory=dict)
    host_id: str = ""


def _ordered_utterance(method):
    """Serialize turns within a room and deduplicate request IDs, never spoken text.

    Provider calls do not hold the coordinator's global membership lock, so other
    rooms and SSE reconnects continue independently. The lock is reentrant because
    audio transcription delegates to the text pipeline.
    """
    @wraps(method)
    def process(self, room_code, speaker_id, *args, **kwargs):
        room = self.get_or_load_room(room_code)
        if not room:
            raise ValueError("Room does not exist or has ended.")
        self.ensure_room_access(room_code, speaker_id)
        utterance_id = kwargs.get("utterance_id")
        if utterance_id is not None and (not isinstance(utterance_id, str) or not 1 <= len(utterance_id) <= 128):
            raise ValueError("utterance_id must be a non-empty string of at most 128 characters.")
        key = (speaker_id, utterance_id) if utterance_id else None
        with room.turn_lock:
            if room.ending:
                raise ValueError("Room has ended.")
            if key and key in room.processed_utterances:
                return room.processed_utterances[key]
            result = method(self, room_code, speaker_id, *args, **kwargs)
            if key:
                room.processed_utterances[key] = result
                if len(room.processed_utterances) > 1000:
                    del room.processed_utterances[next(iter(room.processed_utterances))]
            return result
    return process


class RoomCoordinator:
    """Central engine managing distributed real-time rooms and fan-out translation."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self.active_rooms: dict[str, RoomSession] = {}
        self._removed_room_codes: dict[str, None] = {}

        # AI Engines
        self.gemma = GemmaClient(config)
        self.summarizer = ExtractiveSummarizer() if config.translation_provider == "soniox" else ConversationSummarizer(self.gemma)
        try:
            self.entity_extractor = EntityExtractor()
        except Exception as exc:
            logger.warning("EntityExtractor could not be loaded: %s", exc)
            self.entity_extractor = None

        # Speech Synthesis & Transcription (Soniox as primary STT)
        self.tts = TtsClient(config)
        from backend.soniox_client import SonioxClient
        self.soniox = SonioxClient(config)
        try:
            from backend.groq_whisper_client import GroqWhisperClient
            self.whisper_fallback = GroqWhisperClient(config) if config.translation_provider != "soniox" else None
        except Exception:
            self.whisper_fallback = None

    def get_or_load_room(self, room_code: str) -> RoomSession | None:
        """Fetch active room in memory or load from database."""
        clean_code = room_code.strip().lower()
        with self._lock:
            if clean_code in self._removed_room_codes:
                return None
            if clean_code in self.active_rooms:
                room = self.active_rooms[clean_code]
                return None if room.ending else room

            room_data = get_room_by_code(clean_code)
            if not room_data or not room_data.get("is_active"):
                return None

            room = RoomSession(
                room_code=clean_code,
                room_id=room_data["id"],
                title=room_data["title"],
                record_session=room_data["record_session"],
                host_id=room_data["host_id"],
            )
            self.active_rooms[clean_code] = room
            return room

    @staticmethod
    def ensure_room_access(room_code: str, user_id: str) -> None:
        """A passcode check must grant membership before any private-room IO."""
        record = get_room_by_code(room_code)
        if not record:
            raise ValueError("Room not found.")
        if record.get("has_passcode") and not is_room_member(record["id"], user_id):
            raise ValueError("Passcode verification required for this room.")

    def join_room(
        self,
        room_code: str,
        user_profile: dict[str, Any],
        speaking_lang: str = "en",
        listening_lang: str = "hi",
    ) -> tuple[RoomSession, queue.Queue]:
        """Join a participant into a room session."""
        room = self.get_or_load_room(room_code)
        if not room:
            raise ValueError(f"Room '{room_code}' does not exist or has ended.")

        user_id = user_profile["id"]
        self.ensure_room_access(room_code, user_id)
        user_name = user_profile.get("display_name") or user_profile.get("username", "Guest")
        avatar_color = user_profile.get("avatar_color", "#10B981")

        user_gender = user_profile.get("gender") or "female"

        with self._lock:
            if room.ending:
                raise ValueError("Room has ended.")
            previous = room.participants.get(user_id)
            participant = ParticipantSession(
                user_id=user_id,
                user_name=user_name,
                avatar_color=avatar_color,
                speaking_lang=speaking_lang,
                listening_lang=listening_lang,
                gender=user_gender,
                is_muted=previous.is_muted if previous else False,
            )
            if previous:
                # The latest REST language/mute settings survive a transport reconnect.
                participant.speaking_lang = previous.speaking_lang
                participant.listening_lang = previous.listening_lang
                # Move unsent events; the old connection retains its own empty queue.
                while True:
                    try:
                        participant.event_queue.put_nowait(previous.event_queue.get_nowait())
                    except queue.Empty:
                        break
            room.participants[user_id] = participant

            # Persist to DB
            if not previous:
                add_participant(
                    room.room_id,
                    user_id,
                    user_name,
                    speaking_lang=speaking_lang,
                    listening_lang=listening_lang,
                )

            # Broadcast full updated participant list to all room members
            participants_list = [
                {
                    "user_id": p.user_id,
                    "user_name": p.user_name,
                    "avatar_color": p.avatar_color,
                    "speaking_lang": p.speaking_lang,
                    "listening_lang": p.listening_lang,
                    "gender": getattr(p, "gender", "female"),
                    "is_muted": p.is_muted,
                }
                for p in room.participants.values()
            ]
            part_count = len(room.participants)

        self.broadcast_event(
            room_code,
            "participants",
            participants_list,
        )
        if not previous:
            self.broadcast_event(
                room_code,
                "participant_joined",
                {
                    "user_id": user_id,
                    "user_name": user_name,
                    "avatar_color": avatar_color,
                    "speaking_lang": speaking_lang,
                    "listening_lang": listening_lang,
                    "is_muted": False,
                    "participant_count": part_count,
                },
                exclude_user_id=user_id,
            )

        return room, participant.event_queue

    def update_participant_status(
        self,
        room_code: str,
        user_id: str,
        speaking_lang: str | None = None,
        listening_lang: str | None = None,
        is_muted: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Update participant dialect or mute state and notify room."""
        clean_code = room_code.strip().lower()
        with self._lock:
            room = self.active_rooms.get(clean_code)
            if not room or user_id not in room.participants:
                return []
            p = room.participants[user_id]
            if speaking_lang:
                p.speaking_lang = speaking_lang
            if listening_lang:
                p.listening_lang = listening_lang
            if is_muted is not None:
                p.is_muted = is_muted

            update_participant_languages(
                room.room_id,
                user_id,
                speaking_lang=speaking_lang,
                listening_lang=listening_lang,
            )

            participants_list = [
                {
                    "user_id": part.user_id,
                    "user_name": part.user_name,
                    "avatar_color": part.avatar_color,
                    "speaking_lang": part.speaking_lang,
                    "listening_lang": part.listening_lang,
                    "is_muted": part.is_muted,
                }
                for part in room.participants.values()
            ]

        self.broadcast_event(clean_code, "participants", participants_list)
        return participants_list

    def leave_room(self, room_code: str, user_id: str, event_queue: queue.Queue | None = None) -> None:
        """Handle participant leaving the room."""
        clean_code = room_code.strip().lower()
        with self._lock:
            room = self.active_rooms.get(clean_code)
            if not room or user_id not in room.participants:
                return
            if event_queue is not None and room.participants[user_id].event_queue is not event_queue:
                return  # A superseded SSE handler must not remove the new connection.

            leaving = room.participants.pop(user_id)
            remove_participant(room.room_id, user_id)

            remaining_count = len(room.participants)
            remaining_list = [
                {
                    "user_id": p.user_id,
                    "user_name": p.user_name,
                    "avatar_color": p.avatar_color,
                    "speaking_lang": p.speaking_lang,
                    "listening_lang": p.listening_lang,
                    "is_muted": p.is_muted,
                }
                for p in room.participants.values()
            ]

        self.broadcast_event(
            clean_code,
            "participants",
            remaining_list,
        )
        self.broadcast_event(
            clean_code,
            "participant_left",
            {
                "user_id": user_id,
                "user_name": leaving.user_name,
                "participant_count": remaining_count,
            },
        )

    def broadcast_event(
        self,
        room_code: str,
        event_type: str,
        payload: dict[str, Any],
        exclude_user_id: str | None = None,
    ) -> None:
        """Broadcast an event payload to room participants' SSE queues."""
        clean_code = room_code.strip().lower()
        with self._lock:
            room = self.active_rooms.get(clean_code)
            if not room:
                return

            for uid, participant in room.participants.items():
                if exclude_user_id and uid == exclude_user_id:
                    continue
                if event_type == "session_concluded":
                    # Session termination must not sit behind (or be dropped by)
                    # a slow participant's queued audio. Keep both terminal event
                    # names for existing clients; only the first clears backlog.
                    while True:
                        try:
                            participant.event_queue.get_nowait()
                        except queue.Empty:
                            break
                try:
                    participant.event_queue.put_nowait({"type": event_type, "data": payload})
                except queue.Full:
                    logger.debug("Queue full for participant %s in room %s", uid, clean_code)

    @staticmethod
    def _extract_pcm16le(audio_bytes: bytes, mime_type: str = "") -> tuple[bytes, int, int]:
        """Extract or convert audio bytes to 16-bit PCM, sample rate, and channels."""
        # 1. Parse standard RIFF/WAVE header
        if audio_bytes.startswith(b"RIFF") and b"WAVE" in audio_bytes[:12]:
            try:
                import io
                import wave
                import numpy as np

                with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                    sample_rate = wf.getframerate()
                    channels = wf.getnchannels()
                    sample_width = wf.getsampwidth()
                    frames = wf.readframes(wf.getnframes())
                    if sample_width == 2:
                        if channels == 1:
                            return frames, sample_rate, 1
                        arr = np.frombuffer(frames, dtype=np.int16).reshape(-1, channels)
                        mono = arr.mean(axis=1).astype(np.int16)
                        return mono.tobytes(), sample_rate, 1
            except Exception as exc:
                logger.debug("Failed parsing WAV via wave module: %s", exc)

        # 2. Try pydub/ffmpeg for webm/ogg/mp3 if available
        try:
            import io
            from pydub import AudioSegment
            seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
            seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            return seg.raw_data, 16000, 1
        except Exception:
            pass

        # Only explicitly raw audio may be treated as PCM. Compressed container
        # bytes mislabeled as PCM produce meaningless Soniox transcripts.
        if mime_type.split(";", 1)[0].strip().lower() in {"", "audio/pcm", "audio/x-pcm"}:
            if len(audio_bytes) % 2:
                raise ValueError("16-bit PCM audio must contain complete samples.")
            return audio_bytes, 16000, 1
        raise ValueError("Unable to decode audio; upload a 16-bit PCM WAV file.")

    @_ordered_utterance
    def process_audio_utterance(
        self,
        room_code: str,
        speaker_id: str,
        speaker_name: str,
        audio_bytes: bytes,
        source_lang: str,
        mime_type: str = "audio/wav",
        utterance_id: str | None = None,
    ) -> dict[str, Any]:
        """Transcribe speech audio via Soniox STT and fan out translations."""
        if not audio_bytes:
            return {}
        if self.config.translation_provider == "soniox":
            pcm, rate, channels = self._extract_pcm16le(audio_bytes, mime_type)
            result = self.soniox.transcribe_pcm16(pcm, session_language_hint=source_lang, sample_rate=rate, channels=channels)
            if not result.transcript:
                return {}
            return self.process_utterance(room_code, speaker_id, speaker_name, result.transcript,
                                          result.detected_language or source_lang, confidence=result.confidence,
                                          native_audio=(pcm, rate, channels))

        transcribed_text = ""
        detected_lang = source_lang
        confidence = 1.0

        try:
            pcm_bytes, sample_rate, channels = self._extract_pcm16le(audio_bytes, mime_type)
            res = self.soniox.transcribe_pcm16(
                pcm_bytes,
                session_language_hint=source_lang,
                sample_rate=sample_rate,
                channels=channels,
            )
            transcribed_text = res.transcript.strip()
            if res.detected_language:
                detected_lang = res.detected_language
            confidence = res.confidence
            logger.debug(
                "Soniox STT completed (lang=%s, conf=%.2f, chars=%d)",
                detected_lang,
                confidence,
                len(transcribed_text),
            )
        except Exception as exc:
            logger.error("Soniox STT transcription failed: %s", exc)
            if self.whisper_fallback:
                try:
                    transcribed_text = self.whisper_fallback.transcribe(
                        audio_bytes,
                        language=source_lang,
                        filename="speech.wav",
                        mime_type=mime_type,
                    )
                except Exception as whisper_exc:
                    logger.error("Fallback Groq Whisper also failed: %s", whisper_exc)

        if not transcribed_text:
            return {}

        return self.process_utterance(
            room_code=room_code,
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            text=transcribed_text,
            source_lang=detected_lang or source_lang,
            confidence=confidence,
        )

    @_ordered_utterance
    def process_utterance(
        self,
        room_code: str,
        speaker_id: str,
        speaker_name: str,
        text: str,
        source_lang: str,
        confidence: float = 1.0,
        utterance_id: str | None = None,
        native_audio: tuple[bytes, int, int] | None = None,
    ) -> dict[str, Any]:
        """Process an utterance and perform fan-out translation for all listeners.

        1. Gathers all distinct listening languages across room participants.
        2. Performs LLM translation for each distinct target language.
        3. Synthesizes localized TTS speech for each language.
        4. Broadcasts custom audio & subtitles to every participant.
        """
        clean_code = room_code.strip().lower()
        room = self.get_or_load_room(clean_code)
        if not room:
            raise ValueError(f"Active room '{clean_code}' not found.")

        clean_text = text.strip()
        if not clean_text:
            return {}

        now = time.time()

        # Emit speaking event
        self.broadcast_event(
            clean_code,
            "speaking",
            {"speaker_id": speaker_id, "speaker_name": speaker_name, "is_speaking": True},
        )

        with self._lock:
            # Auto-register speaker if not yet in memory due to connection race
            if speaker_id not in room.participants:
                from backend.db.auth import get_user_profile
                u_prof = get_user_profile(speaker_id)
                room.participants[speaker_id] = ParticipantSession(
                    user_id=speaker_id,
                    user_name=speaker_name,
                    avatar_color=(u_prof or {}).get("avatar_color", "#10B981"),
                    speaking_lang=source_lang,
                    listening_lang=source_lang,
                    gender=(u_prof or {}).get("gender", "female"),
                )

            # Determine all distinct listening languages across room participants
            target_langs = {
                p.listening_lang
                for p in room.participants.values()
                if p.listening_lang
            }
            audio_langs = {
                p.listening_lang for uid, p in room.participants.items()
                if uid != speaker_id and p.listening_lang
            }
            speaker_gender = room.participants[speaker_id].gender

        # Create dummy channel configurations for translation prompts
        speaker_ch = ChannelConfig(key="speaker", name=speaker_name, source_language=source_lang, language_mode="fixed")

        translations: dict[str, str] = {source_lang: clean_text}
        audio_payloads: dict[str, str] = {}  # lang -> base64 data url

        # Detect if incoming text is primarily Latin characters
        has_latin_chars = any('a' <= ch.lower() <= 'z' for ch in clean_text)
        if self.config.translation_provider == "soniox" and native_audio is None and any(language != source_lang for language in target_langs):
            # Soniox translates speech, not standalone text. Preserve the text
            # fallback using a voice bridge rather than introducing another LLM.
            source_channel = replace(next(iter(self.config.channels.values())), source_language=source_lang)
            source_audio = self.tts.synthesize_for_language(clean_text, source_channel, source_lang, gender=speaker_gender)
            if not source_audio:
                raise ValueError("The text-to-speech bridge is unavailable; please use your microphone.")
            native_audio = self._extract_pcm16le(source_audio, "audio/wav")

        def localize(target_lang: str) -> None:
            # If target language uses non-Latin script (Hindi, Marathi, etc.) but the text is Latin/English,
            # or if target language is different from claimed source language, we MUST translate.
            is_non_latin_target = target_lang in {"hi", "mr", "bn", "ta", "te", "gu", "kn", "ml", "pa", "ar", "ru", "ja", "ko", "zh"}
            needs_translation = (target_lang != source_lang) or (is_non_latin_target and has_latin_chars)

            if not needs_translation:
                translations[target_lang] = clean_text
            else:
                listener_ch = ChannelConfig(key="listener", name="Listener", source_language=target_lang, language_mode="fixed")
                try:
                    if self.config.translation_provider == "soniox":
                        if native_audio is None:
                            translated = clean_text
                        else:
                            pcm, rate, channels = native_audio
                            result = self.soniox.transcribe_pcm16(pcm, session_language_hint=source_lang,
                                                                 sample_rate=rate, channels=channels, target_language=target_lang)
                            translated = result.translation
                            if not translated:
                                raise ValueError("Soniox returned no translation for this language.")
                    else:
                        translated = self.gemma.translate(clean_text, speaker_ch, listener_ch, source_lang, target_lang, source_lang)
                    translations[target_lang] = translated.strip() if translated else ""
                except Exception as exc:
                    logger.warning("Translation to %s failed: %s", target_lang, exc)
                    translations[target_lang] = ""

            # The speaker receives subtitles but never hears their own TTS.
            if target_lang not in audio_langs or not translations[target_lang]:
                return

            try:
                configured_channel = next(iter(self.config.channels.values()), speaker_ch)
                synth_ch = replace(configured_channel, key="synth", name="Synthesizer", source_language=target_lang,
                                   cartesia_voice_id=self.config.cartesia_voice_map.get(target_lang, configured_channel.cartesia_voice_id))
                wav_bytes = self.tts.synthesize_for_language(
                    translations[target_lang],
                    synth_ch,
                    target_lang,
                    gender=speaker_gender,
                )
                if wav_bytes:
                    b64_audio = base64.b64encode(wav_bytes).decode("utf-8")
                    mime = "audio/mpeg" if wav_bytes[:3] == b"ID3" or wav_bytes[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") else "audio/wav"
                    audio_payloads[target_lang] = f"data:{mime};base64,{b64_audio}"
            except Exception as exc:
                logger.debug("TTS synthesis failed for lang %s: %s", target_lang, exc)

        # Bound concurrent provider work; latency is no longer the sum of every
        # listener language's translation and synthesis time.
        if target_langs:
            with ThreadPoolExecutor(max_workers=min(4, len(target_langs))) as pool:
                list(pool.map(localize, target_langs))

        # Extract entities if enabled
        entities: dict[str, list[str]] = {}
        if self.entity_extractor:
            try:
                entities = self.entity_extractor.extract(clean_text)
            except Exception as exc:
                logger.debug("Entity extraction failed: %s", exc)

        # Persist to database only if session recording is enabled
        turn_record: dict[str, Any] = {}
        if room.record_session:
            turn_record = add_transcript(
                room_id=room.room_id,
                speaker_id=speaker_id,
                speaker_name=speaker_name,
                original_text=clean_text,
                source_lang=source_lang,
                translations=translations,
                confidence=confidence,
            )

        # Broadcast customized turn payload to every room participant
        turn_id = turn_record.get("id") or str(uuid.uuid4())
        with self._lock:
            for uid, participant in room.participants.items():
                p_lang = participant.listening_lang
                localized_text = translations.get(p_lang, clean_text)
                localized_audio = audio_payloads.get(p_lang)

                turn_event = {
                    "turn_id": turn_id,
                    "speaker_id": speaker_id,
                    "speaker_name": speaker_name,
                    "source_lang": source_lang,
                    "confidence": confidence,
                    "original_text": clean_text,
                    "localized_text": localized_text,
                    "listening_lang": p_lang,
                    "audio_data_url": localized_audio if uid != speaker_id else None,
                    "timestamp": now,
                    "entities": entities,
                }
                try:
                    participant.event_queue.put_nowait({"type": "turn", "data": turn_event})
                except queue.Full:
                    pass

        return turn_record

    def record_stream_segment(self, room_code, speaker_id, speaker_name, segment):
        room = self.get_or_load_room(room_code)
        if not room:
            return
        with room.turn_lock:
            if room.ending:
                return
            # Align results from parallel language streams by source-audio time,
            # never by repeated words. Repeated utterances remain distinct.
            match = None
            if segment.end_ms > segment.start_ms:
                for previous in reversed(list(room.stream_turns.values())[-20:]):
                    if previous["speaker_id"] != speaker_id:
                        continue
                    overlap = min(previous["_end"], segment.end_ms) - max(previous["_start"], segment.start_ms)
                    duration = min(previous["_end"] - previous["_start"], segment.end_ms - segment.start_ms)
                    if duration > 0 and overlap / duration > 0.65 and abs(previous["_start"] - segment.start_ms) < 500:
                        match = previous
                        break
            if match is None:
                identifier = str(uuid.uuid4())
                match = {"id": identifier, "room_id": room.room_id, "speaker_id": speaker_id,
                         "speaker_name": speaker_name, "original_text": segment.original,
                         "source_lang": segment.source_lang, "confidence": segment.confidence,
                         "translations": {}, "timestamp": time.time(), "revision": 0,
                         "_start": segment.start_ms, "_end": segment.end_ms}
                room.stream_turns[identifier] = match
                if len(room.stream_turns) > 200:
                    del room.stream_turns[next(iter(room.stream_turns))]
            if segment.target_lang in match["translations"] and match["translations"][segment.target_lang] == segment.translated:
                return
            match["translations"][segment.source_lang] = segment.original
            if segment.translated:
                match["translations"][segment.target_lang] = segment.translated
            match["revision"] += 1
            if room.record_session:
                save_stream_transcript(match)
            with self._lock:
                for uid, participant in room.participants.items():
                    language = participant.listening_lang
                    if uid != speaker_id and language not in match["translations"]:
                        continue
                    event = {key: value for key, value in match.items() if not key.startswith("_")}
                    event["translations"] = dict(match["translations"])
                    event.update(turn_id=match["id"], listening_lang=language,
                                 localized_text=match["translations"].get(language, match["original_text"]),
                                 audio_data_url=None)
                    try:
                        participant.event_queue.put_nowait({"type": "turn", "data": event})
                    except queue.Full:
                        logger.debug("Slow transcript listener in room %s", room_code)

    def remove_cached_room(self, room_code: str) -> None:
        """Stop active streams before a caller removes the room's database rows."""
        clean_code = room_code.strip().lower()
        with self._lock:
            room = self.active_rooms.get(clean_code)
        if room is None:
            return
        with room.turn_lock:
            room.ending = True
            self.broadcast_event(clean_code, "session_ended", {
                "executive_summary": "The room was removed by its host.",
                "key_points": [], "action_items": [], "summary_method": "extractive",
            })
            with self._lock:
                # Block reloads in the short interval before the caller commits
                # the database deletion. Persisted deletion handles restarts.
                self._removed_room_codes[clean_code] = None
                if len(self._removed_room_codes) > 1000:
                    del self._removed_room_codes[next(iter(self._removed_room_codes))]
                if self.active_rooms.get(clean_code) is room:
                    del self.active_rooms[clean_code]

    def remove_hosted_cached_rooms(self, user_id: str) -> None:
        with self._lock:
            codes = [code for code, room in self.active_rooms.items() if str(room.host_id) == str(user_id)]
        for code in codes:
            self.remove_cached_room(code)

    def clear_transcripts(self, room_code: str, user_id: str, user_name: str = '') -> None:
        clean_code = room_code.strip().lower()
        room = self.get_or_load_room(clean_code)
        if not room:
            raise ValueError('Room not found.')
        with room.turn_lock:
            clear_room_transcripts(room.room_id)
            room.stream_turns.clear()
        self.broadcast_event(
            clean_code,
            'transcripts_cleared',
            {'cleared_by_user_id': user_id, 'cleared_by_user_name': user_name},
        )

    def conclude_session(self, room_code: str) -> dict[str, Any]:
        """End a conversation, save its notes, and notify all participants."""
        clean_code = room_code.strip().lower()
        room = self.get_or_load_room(clean_code)
        if not room:
            raise ValueError(f"Room '{clean_code}' not found.")

        with self._lock:
            if room.ending:
                raise ValueError("Room is already ending.")
            room.ending = True
        # Wait for the current turn's commit before taking the summary snapshot.
        # New turns and joins are rejected as soon as ending is set.
        with room.turn_lock:
            return self._conclude_session(room)

    def _conclude_session(self, room: RoomSession) -> dict[str, Any]:
        clean_code = room.room_code

        # Conclude in database
        end_room(clean_code)

        if not room.record_session:
            executive_summary = f"Conversation '{room.title}' ended. Saving was disabled, so no transcript or conversation notes were stored."
            insights_data = {
                "room_id": room.room_id,
                "executive_summary": executive_summary,
                "action_items": [],
                "key_points": [],
                "entities": {},
                "recording_enabled": False,
            }
        else:
            # Fetch all transcripts
            transcripts = get_room_transcripts(room.room_id)

            # Generate AI summary & action items if turns exist
            if transcripts:
                summary_turns = [
                    {
                        "speaker_channel_name": t["speaker_name"],
                        "transcript": t["original_text"],
                        "translation": t["translations"].get("en", t["original_text"]),
                        "detected_source_language": t["source_lang"],
                    }
                    for t in transcripts
                ]
                try:
                    ai_result = self.summarizer.summarize(clean_code, summary_turns)
                    if hasattr(ai_result, "executive_summary") and isinstance(ai_result.executive_summary, str):
                        executive_summary = ai_result.executive_summary
                    elif hasattr(ai_result, "summary") and isinstance(ai_result.summary, str):
                        executive_summary = ai_result.summary
                    else:
                        executive_summary = str(getattr(ai_result, "executive_summary", "") or getattr(ai_result, "summary", ""))
                    action_items = list(getattr(ai_result, "action_items", []))
                    key_points = list(getattr(ai_result, "key_points", []))
                except Exception as exc:
                    logger.warning("Summarization failed on session end: %s", exc)
                    executive_summary = f"Session {room.title} concluded with {len(transcripts)} turns."
                    action_items = []
                    key_points = []
            else:
                executive_summary = f"Session '{room.title}' concluded with no spoken dialogue."
                action_items = []
                key_points = []

            # Extract combined entities across transcripts
            all_entities: dict[str, list[str]] = {}
            if self.entity_extractor and transcripts:
                combined_text = " ".join(t["original_text"] for t in transcripts)
                try:
                    all_entities = self.entity_extractor.extract(combined_text)
                except Exception as exc:
                    logger.debug("Entity extraction failed: %s", exc)

            # Save into session_insights table
            insights_data = save_session_insights(
                room_id=room.room_id,
                executive_summary=executive_summary,
                action_items=action_items,
                key_points=key_points,
                entities=all_entities,
            )

        insights_data["summary_method"] = "extractive" if self.config.translation_provider == "soniox" else "llm"
        # Notify participants that session concluded
        self.broadcast_event(clean_code, "session_concluded", insights_data)
        self.broadcast_event(clean_code, "session_ended", insights_data)

        # Remove from active memory
        with self._lock:
            self.active_rooms.pop(clean_code, None)

        return insights_data
