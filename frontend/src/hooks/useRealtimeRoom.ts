import { useCallback, useEffect, useRef, useState } from 'react';
import type { TranscriptTurn } from '@/types';
import type { RoomSSEHandlers } from './useRoomSSE';
import { API_BASE } from '@/services/api';
import { RealtimeAudioEngine } from '@/lib/realtimeAudio';

interface Handlers extends RoomSSEHandlers {
  onCaption?: (turn: TranscriptTurn) => void;
}

export function useRealtimeRoom(
  roomCode: string,
  token: string,
  handlers: Handlers,
  options: { speakingLang: string; listeningLang: string; micMuted: boolean; audioMuted: boolean }
) {
  const handlersRef = useRef(handlers),
    optionsRef = useRef(options);
  handlersRef.current = handlers;
  optionsRef.current = options;
  const socketRef = useRef<WebSocket | null>(null);
  const engineRef = useRef<RealtimeAudioEngine | null>(null);
  const generationRef = useRef(0);
  const [status, setStatus] = useState<'connecting' | 'connected' | 'reconnecting' | 'disconnected'>(
    'connecting'
  );
  const [error, setError] = useState<string | null>(null);
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);
  const [isPlayingAudio, setIsPlayingAudio] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [level, setLevel] = useState(0);
  const [voiceStartMs, setVoiceStartMs] = useState<number | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    const engine = new RealtimeAudioEngine(setIsPlayingAudio, setAutoplayBlocked, setError);
    engineRef.current = engine;
    return () => {
      engine.dispose();
      if (engineRef.current === engine) engineRef.current = null;
    };
  }, [roomCode]);

  useEffect(() => {
    const generation = ++generationRef.current;
    let socket: WebSocket | null = null,
      timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false,
      ended = false,
      retries = 0;
    const seen = new Set<string>();
    const connect = () => {
      if (disposed || ended) return;
      if (timer) clearTimeout(timer);
      const endpoint = new URL(API_BASE || window.location.origin);
      endpoint.protocol = endpoint.protocol === 'https:' ? 'wss:' : 'ws:';
      endpoint.pathname = `/ws/rooms/${encodeURIComponent(roomCode)}`;
      endpoint.search = '';
      endpoint.hash = '';
      socket = new WebSocket(endpoint.toString());
      socketRef.current = socket;
      socket.binaryType = 'arraybuffer';
      const current = socket;
      const active = () => !disposed && generationRef.current === generation && socketRef.current === current;
      current.onopen = () => {
        if (!active()) return;
        const currentOptions = optionsRef.current;
        current.send(
          JSON.stringify({
            type: 'authenticate',
            token,
            speaking_lang: currentOptions.speakingLang,
            listening_lang: currentOptions.listeningLang,
            is_muted: currentOptions.micMuted,
          })
        );
      };
      current.onmessage = (event) => {
        if (!active()) return;
        const engine = engineRef.current;
        if (event.data instanceof ArrayBuffer) {
          engine?.receive(event.data);
          return;
        }
        let message;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        const data = message.data;
        if (message.type === 'connected') {
          retries = 0;
          setStatus('connected');
          setError(null);
          handlersRef.current.onOpen?.();
        } else if (message.type === 'participants')
          handlersRef.current.onParticipants?.(Array.isArray(data) ? data : []);
        else if (message.type === 'participant_joined') handlersRef.current.onParticipantJoined?.(data);
        else if (message.type === 'participant_left') handlersRef.current.onParticipantLeft?.(data.user_id);
        else if (message.type === 'speaking' && data.is_speaking !== false)
          handlersRef.current.onSpeaking?.(data.speaker_id, data.speaker_name);
        else if (message.type === 'caption') handlersRef.current.onCaption?.(data);
        else if (message.type === 'turn') {
          const key = `${data.turn_id ?? data.id}:${data.revision ?? 0}`;
          if (seen.has(key)) return;
          seen.add(key);
          if (seen.size > 1000) seen.delete(seen.values().next().value!);
          handlersRef.current.onTurn?.(data);
        } else if (message.type === 'audio_start') {
          if (data.language !== optionsRef.current.listeningLang) return;
          try {
            engine?.ensure();
            if (engine?.player) {
              engine.player.muted = optionsRef.current.audioMuted;
              engine.player.start(data);
            }
            setVoiceStartMs(data.voice_start_ms ?? null);
          } catch {
            setAutoplayBlocked(true);
          }
        } else if (message.type === 'audio_end') engine?.player?.end(data.stream_id);
        else if (message.type === 'audio_reset') engine?.player?.reset();
        else if (message.type === 'pipeline_error')
          setError(data?.message || 'Translation is temporarily unavailable.');
        else if (message.type === 'transcripts_cleared') handlersRef.current.onTranscriptsCleared?.();
        else if (message.type === 'session_concluded' || message.type === 'session_ended') {
          ended = true;
          engine?.stopMicrophone();
          engine?.player?.reset();
          handlersRef.current.onSessionConcluded?.(data);
          current.close();
        }
      };
      current.onerror = (event) => {
        if (active()) handlersRef.current.onError?.(event);
      };
      current.onclose = (event) => {
        if (!active()) return;
        socketRef.current = null;
        engineRef.current?.stopMicrophone();
        engineRef.current?.player?.reset();
        setIsSpeaking(false);
        setLevel(0);
        if (disposed || ended) return;
        if ([4009, 4400, 4401, 4403].includes(event.code)) {
          setStatus('disconnected');
          setError(
            event.code === 4009
              ? 'This account joined from another tab. Close the other tab, then reconnect.'
              : event.reason || 'Unable to join. Check your login and room access.'
          );
          return;
        }
        setStatus('reconnecting');
        handlersRef.current.onError?.(event);
        timer = setTimeout(connect, Math.min(1000 * 2 ** retries++, 15000));
      };
    };
    setStatus('connecting');
    connect();
    return () => {
      disposed = true;
      generationRef.current++;
      if (timer) clearTimeout(timer);
      socket?.close();
      if (socketRef.current === socket) socketRef.current = null;
      engineRef.current?.stopMicrophone();
      engineRef.current?.player?.reset();
    };
  }, [roomCode, token, retryCount]);

  useEffect(() => {
    const socket = socketRef.current;
    if (status === 'connected' && socket?.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({
          type: 'configure',
          speaking_lang: options.speakingLang,
          listening_lang: options.listeningLang,
          is_muted: options.micMuted,
        })
      );
    }
  }, [status, options.speakingLang, options.listeningLang, options.micMuted]);

  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    if (status !== 'connected' || options.micMuted) {
      engine.stopMicrophone();
      setIsSpeaking(false);
      setLevel(0);
      return;
    }
    let disposed = false;
    void engine
      .startMicrophone(
        (data) => {
          const socket = socketRef.current;
          if (socket?.readyState !== WebSocket.OPEN) return;
          if (socket.bufferedAmount > 64000) {
            socket.close(4008, 'Audio network is falling behind.');
            return;
          }
          socket.send(data);
        },
        (active) => {
          if (disposed) return;
          setIsSpeaking(active);
          // Local speech takes priority; do not globally mute microphone capture.
          if (active) engine.player?.reset();
          const socket = socketRef.current;
          if (socket?.readyState === WebSocket.OPEN)
            socket.send(JSON.stringify({ type: 'speaking', active }));
        },
        (value) => {
          if (!disposed) setLevel(value);
        }
      )
      .catch((reason) => {
        if (!disposed)
          setError(
            reason instanceof Error ? reason.message : 'Microphone unavailable. Check browser permissions.'
          );
      });
    return () => {
      disposed = true;
      engine.stopMicrophone();
    };
  }, [status, options.micMuted, roomCode, retryCount]);

  useEffect(() => {
    const player = engineRef.current?.player;
    if (player) {
      player.muted = options.audioMuted;
      if (options.audioMuted) player.reset();
    }
  }, [options.audioMuted]);

  const unlockAudio = useCallback(() => {
    const engine = engineRef.current;
    if (!engine) return;
    try {
      engine.ensure();
      void engine.player?.unlock().catch(() => setAutoplayBlocked(true));
    } catch {
      setError('Audio is unavailable in this browser.');
    }
  }, []);
  const playEncoded = useCallback((url: string) => {
    if (!optionsRef.current.audioMuted)
      void engineRef.current?.encoded(url).catch(() => setError('Translated audio could not be played.'));
  }, []);
  const cancelAudio = useCallback(() => engineRef.current?.player?.reset(), []);
  const reconnect = useCallback(() => {
    setError(null);
    setRetryCount((value) => value + 1);
  }, []);
  return {
    status,
    connected: status === 'connected',
    error,
    autoplayBlocked,
    isPlayingAudio,
    isSpeaking,
    level,
    voiceStartMs,
    unlockAudio,
    playEncoded,
    cancelAudio,
    reconnect,
  };
}
