import { useEffect, useRef, useCallback } from 'react';
import type { Participant, TranscriptTurn } from '@/types';

export interface RoomSSEHandlers {
  onSpeaking?: (userId: number | string, name?: string) => void;
  onTurn?: (turn: TranscriptTurn) => void;
  onParticipants?: (participants: Participant[]) => void;
  onParticipantJoined?: (participant: Participant) => void;
  onParticipantLeft?: (userId: string | number) => void;
  onSessionConcluded?: (payload?: unknown) => void;
  onTranscriptsCleared?: () => void;
  onError?: (err: Event) => void;
  onOpen?: () => void;
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

/**
 * Subscribe to room Server-Sent Events.
 * Cleans up on unmount / room change. Reconnects with backoff on error.
 */
export function useRoomSSE(
  roomCode: string | null,
  token: string | null,
  handlers: RoomSSEHandlers,
  options?: { speakingLang?: string; listeningLang?: string }
) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const generationRef = useRef(0);
  const seenTurnsRef = useRef(new Set<string>());

  const connect = useCallback(() => {
    if (!roomCode) return;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const generation = ++generationRef.current;

    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    const params = new URLSearchParams();
    if (token) params.set('token', token);
    if (optionsRef.current?.speakingLang) params.set('speaking_lang', optionsRef.current.speakingLang);
    if (optionsRef.current?.listeningLang) params.set('listening_lang', optionsRef.current.listeningLang);
    const path = `/events/rooms/${encodeURIComponent(roomCode)}`;
    const url = `${API_BASE}${path}${params.toString() ? `?${params}` : ''}`;

    const es = new EventSource(url);
    esRef.current = es;
    const active = () => generation === generationRef.current && esRef.current === es;
    const deliverTurn = (turn: TranscriptTurn) => {
      const id = turn.turn_id ?? turn.id;
      if (id != null) {
        const key = String(id);
        if (seenTurnsRef.current.has(key)) return;
        seenTurnsRef.current.add(key);
        if (seenTurnsRef.current.size > 1000) {
          seenTurnsRef.current.delete(seenTurnsRef.current.values().next().value!);
        }
      }
      handlersRef.current.onTurn?.(turn);
    };

    es.onopen = () => {
      if (!active()) return;
      retryRef.current = 0;
      handlersRef.current.onOpen?.();
    };

    es.addEventListener('speaking', (e) => {
      if (!active()) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        handlersRef.current.onSpeaking?.(data.speaker_id ?? data.user_id ?? data.userId, data.speaker_name ?? data.user_name ?? data.name);
      } catch {
        /* ignore malformed */
      }
    });

    es.addEventListener('turn', (e) => {
      if (!active()) return;
      try {
        const data = JSON.parse((e as MessageEvent).data) as TranscriptTurn;
        deliverTurn(data);
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('connected', (e) => {
      if (!active()) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        if (data && Array.isArray(data.participants)) {
          handlersRef.current.onParticipants?.(data.participants);
        }
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('participant_joined', (e) => {
      if (!active()) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        if (data && data.user_id) {
          handlersRef.current.onParticipantJoined?.(data);
        }
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('participant_left', (e) => {
      if (!active()) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        if (data && data.user_id) {
          handlersRef.current.onParticipantLeft?.(data.user_id);
        }
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('participants', (e) => {
      if (!active()) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        const list = Array.isArray(data) ? data : data.participants ?? [];
        handlersRef.current.onParticipants?.(list);
      } catch {
        /* ignore */
      }
    });

    const conclude = (e: Event) => {
      if (!active()) return;
      generationRef.current += 1;
      es.close();
      esRef.current = null;
      let data: unknown;
      try {
        data = (e as MessageEvent).data ? JSON.parse((e as MessageEvent).data) : undefined;
      } catch { /* allow an empty conclusion payload */ }
      handlersRef.current.onSessionConcluded?.(data);
    };
    es.addEventListener('session_concluded', conclude);
    es.addEventListener('session_ended', conclude);

    // Some backends send generic message events
    es.onmessage = (e) => {
      if (!active()) return;
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'turn' || data.localized_text) {
          deliverTurn(data.data ?? data);
        } else if (data.type === 'speaking') {
          handlersRef.current.onSpeaking?.(data.speaker_id ?? data.user_id, data.speaker_name ?? data.user_name);
        }
      } catch {
        /* ignore */
      }
    };

    es.addEventListener('transcripts_cleared', () => {
      if (!active()) return;
      handlersRef.current.onTranscriptsCleared?.();
    });

    es.onerror = (err) => {
      if (!active()) return;
      handlersRef.current.onError?.(err);
      es.close();
      esRef.current = null;
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000);
      retryRef.current += 1;
      timerRef.current = setTimeout(() => {
        if (generation === generationRef.current) connect();
      }, delay);
    };
  }, [roomCode, token]);

  useEffect(() => {
    retryRef.current = 0;
    seenTurnsRef.current.clear();
    connect();
    return () => {
      generationRef.current += 1;
      if (timerRef.current) clearTimeout(timerRef.current);
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [connect]);
}
