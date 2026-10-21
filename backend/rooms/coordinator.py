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
from dataclasses import dataclass, field
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
    update_participant_languages,
)
from backend.gemma_client import GemmaClient
from backend.nlp.entity_extractor import EntityExtractor
from backend.nlp.summarizer import ConversationSummarizer
from backend.tts_client import TtsClient

logger = logging.getLogger(__name__)


@dataclass
class ParticipantSession:
    user_id: str
    user_name: str
    avatar_color: str
    speaking_lang: str
    listening_lang: str
    is_muted: bool = False
    event_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=100))


@dataclass
class RoomSession:
    room_code: str
    room_id: str
    title: str
    record_session: bool
    participants: dict[str, ParticipantSession] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class RoomCoordinator:
    """Central engine managing distributed real-time rooms and fan-out translation."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self.active_rooms: dict[str, RoomSession] = {}

        # AI Engines
        self.gemma = GemmaClient(config)
        self.summarizer = ConversationSummarizer(self.gemma)
        try:
            self.entity_extractor = EntityExtractor()
        except Exception as exc:
            logger.warning("EntityExtractor could not be loaded: %s", exc)
            self.entity_extractor = None

        # Speech Synthesis & Transcription
        self.tts = TtsClient(config)
        from backend.groq_whisper_client import GroqWhisperClient
        self.stt = GroqWhisperClient(config)

    def get_or_load_room(self, room_code: str) -> RoomSession | None:
        """Fetch active room in memory or load from database."""
        clean_code = room_code.strip().lower()
        with self._lock:
            if clean_code in self.active_rooms:
                return self.active_rooms[clean_code]

            room_data = get_room_by_code(clean_code)
            if not room_data or not room_data.get("is_active"):
                return None

            room = RoomSession(
                room_code=clean_code,
                room_id=room_data["id"],
                title=room_data["title"],
                record_session=room_data["record_session"],
            )
            self.active_rooms[clean_code] = room
            return room

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
        user_name = user_profile.get("display_name") or user_profile.get("username", "Guest")
        avatar_color = user_profile.get("avatar_color", "#10B981")

        with self._lock:
            participant = ParticipantSession(
                user_id=user_id,
                user_name=user_name,
                avatar_color=avatar_color,
                speaking_lang=speaking_lang,
                listening_lang=listening_lang,
            )
            room.participants[user_id] = participant

            # Persist to DB
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

    def leave_room(self, room_code: str, user_id: str) -> None:
        """Handle participant leaving the room."""
        clean_code = room_code.strip().lower()
        with self._lock:
            room = self.active_rooms.get(clean_code)
            if not room or user_id not in room.participants:
                return

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

            dead_users = []
            for uid, participant in room.participants.items():
                if exclude_user_id and uid == exclude_user_id:
                    continue
                try:
                    participant.event_queue.put_nowait({"type": event_type, "data": payload})
                except queue.Full:
                    logger.debug("Queue full for participant %s in room %s", uid, clean_code)

    def process_audio_utterance(
        self,
        room_code: str,
        speaker_id: str,
        speaker_name: str,
        audio_bytes: bytes,
        source_lang: str,
        mime_type: str = "audio/webm",
    ) -> dict[str, Any]:
        """Transcribe speech audio via Groq Whisper and fan out translations."""
        transcribed_text = self.stt.transcribe(
            audio_bytes,
            language=source_lang,
            filename="speech.webm" if "webm" in mime_type else "speech.wav",
            mime_type=mime_type,
        )
        if not transcribed_text:
            return {}
        return self.process_utterance(
            room_code=room_code,
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            text=transcribed_text,
            source_lang=source_lang,
            confidence=1.0,
        )

    def process_utterance(
        self,
        room_code: str,
        speaker_id: str,
        speaker_name: str,
        text: str,
        source_lang: str,
        confidence: float = 1.0,
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
                )

            # Determine all distinct listening languages across room participants
            target_langs = {
                p.listening_lang
                for p in room.participants.values()
                if p.listening_lang
            }

        # Create dummy channel configurations for translation prompts
        speaker_ch = ChannelConfig(key="speaker", name=speaker_name, source_language=source_lang, language_mode="fixed")

        translations: dict[str, str] = {source_lang: clean_text}
        audio_payloads: dict[str, str] = {}  # lang -> base64 data url

        # Detect if incoming text is primarily Latin characters
        has_latin_chars = any('a' <= ch.lower() <= 'z' for ch in clean_text)

        for target_lang in target_langs:
            # If target language uses non-Latin script (Hindi, Marathi, etc.) but the text is Latin/English,
            # or if target language is different from claimed source language, we MUST translate.
            is_non_latin_target = target_lang in {"hi", "mr", "bn", "ta", "te", "gu", "kn", "ml", "pa", "ar", "ru", "ja", "ko", "zh"}
            needs_translation = (target_lang != source_lang) or (is_non_latin_target and has_latin_chars)

            if not needs_translation:
                translations[target_lang] = clean_text
            else:
                listener_ch = ChannelConfig(key="listener", name="Listener", source_language=target_lang, language_mode="fixed")
                try:
                    translated = self.gemma.translate(
                        clean_text,
                        speaker_ch,
                        listener_ch,
                        source_lang,
                        target_lang,
                        source_lang,
                    )
                    translations[target_lang] = translated.strip() if translated else clean_text
                except Exception as exc:
                    logger.warning("Translation to %s failed: %s", target_lang, exc)
                    translations[target_lang] = clean_text

            # Synthesize speech for this language
            try:
                synth_ch = ChannelConfig(
                    key="synth",
                    name="Synthesizer",
                    source_language=target_lang,
                    language_mode="fixed",
                    cartesia_voice_id=self.config.channels.get("1", speaker_ch).cartesia_voice_id,
                )
                wav_bytes = self.tts.synthesize_for_language(translations[target_lang], synth_ch, target_lang)
                if wav_bytes:
                    b64_audio = base64.b64encode(wav_bytes).decode("utf-8")
                    mime = "audio/mpeg" if wav_bytes[:3] == b"ID3" or wav_bytes[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") else "audio/wav"
                    audio_payloads[target_lang] = f"data:{mime};base64,{b64_audio}"
            except Exception as exc:
                logger.debug("TTS synthesis failed for lang %s: %s", target_lang, exc)

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
        with self._lock:
            for uid, participant in room.participants.items():
                p_lang = participant.listening_lang
                localized_text = translations.get(p_lang, clean_text)
                localized_audio = audio_payloads.get(p_lang)

                turn_event = {
                    "turn_id": turn_record.get("id") or f"turn_{int(now * 1000)}",
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

    def clear_transcripts(self, room_code: str, user_id: str, user_name: str = '') -> None:
        clean_code = room_code.strip().lower()
        room = self.get_or_load_room(clean_code)
        if not room:
            raise ValueError('Room not found.')
        clear_room_transcripts(room.room_id)
        self.broadcast_event(
            clean_code,
            'transcripts_cleared',
            {'cleared_by_user_id': user_id, 'cleared_by_user_name': user_name},
        )

    def conclude_session(self, room_code: str) -> dict[str, Any]:
        """Conclude a room session, generate MoM/AI Insights, and notify all members."""
        clean_code = room_code.strip().lower()
        room = self.get_or_load_room(clean_code)
        if not room:
            raise ValueError(f"Room '{clean_code}' not found.")

        # Conclude in database
        end_room(clean_code)

        if not room.record_session:
            executive_summary = f"Session '{room.title}' concluded. Recording was disabled for this dialogue, so no transcripts or MoM were persisted."
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

        # Notify participants that session concluded
        self.broadcast_event(clean_code, "session_concluded", insights_data)
        self.broadcast_event(clean_code, "session_ended", insights_data)

        # Remove from active memory
        with self._lock:
            self.active_rooms.pop(clean_code, None)

        return insights_data
