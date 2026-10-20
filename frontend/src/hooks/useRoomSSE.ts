import { useEffect, useRef, useCallback } from 'react';
import type { Participant, TranscriptTurn } from '@/types';

export interface RoomSSEHandlers {
  onSpeaking?: (userId: number, name?: string) => void;
  onTurn?: (turn: TranscriptTurn) => void;
  onParticipants?: (participants: Participant[]) => void;
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

  const connect = useCallback(() => {
    if (!roomCode) return;

    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    const params = new URLSearchParams();
    if (token) params.set('token', token);
    if (options?.speakingLang) params.set('speaking_lang', options.speakingLang);
    if (options?.listeningLang) params.set('listening_lang', options.listeningLang);
    const path = `/events/rooms/${encodeURIComponent(roomCode)}`;
    const url = `${API_BASE}${path}${params.toString() ? `?${params}` : ''}`;

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => {
      retryRef.current = 0;
      handlersRef.current.onOpen?.();
    };

    es.addEventListener('speaking', (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data);
        handlersRef.current.onSpeaking?.(data.user_id ?? data.userId, data.user_name ?? data.name);
      } catch {
        /* ignore malformed */
      }
    });

    es.addEventListener('turn', (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as TranscriptTurn;
        handlersRef.current.onTurn?.(data);
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('participants', (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data);
        const list = Array.isArray(data) ? data : data.participants ?? [];
        handlersRef.current.onParticipants?.(list);
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('session_concluded', (e) => {
      try {
        const data = (e as MessageEvent).data ? JSON.parse((e as MessageEvent).data) : undefined;
        handlersRef.current.onSessionConcluded?.(data);
      } catch {
        handlersRef.current.onSessionConcluded?.();
      }
    });

    // Some backends send generic message events
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'turn' || data.localized_text) {
          handlersRef.current.onTurn?.(data);
        } else if (data.type === 'speaking') {
          handlersRef.current.onSpeaking?.(data.user_id, data.user_name);
        }
      } catch {
        /* ignore */
      }
    };

    es.addEventListener('transcripts_cleared', () => {
      handlersRef.current.onTranscriptsCleared?.();
    });

    es.onerror = (err) => {
      handlersRef.current.onError?.(err);
      es.close();
      esRef.current = null;
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000);
      retryRef.current += 1;
      timerRef.current = setTimeout(connect, delay);
    };
  }, [roomCode, token]);

  useEffect(() => {
    connect();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [connect]);
}
