import { useEffect, useRef, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Mic,
  MicOff,
  Volume2,
  VolumeX,
  FileText,
  Share2,
  PhoneOff,
  Send,
  X,
  Languages,
  ArrowLeft,
  Trash2,
  AlertTriangle,
  LogOut,
  Copy,
} from 'lucide-react';
import type { Participant, Room, TranscriptTurn, User, SessionInsights } from '@/types';
import { SUPPORTED_LANGUAGES, langShort } from '@/types';
import { roomService } from '@/services/api';
import { useRoomSSE } from '@/hooks/useRoomSSE';
import { useSpeechDictation, mapLang } from '@/hooks/useSpeechDictation';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { cn, initials } from '@/lib/utils';

interface DialogueCanvasProps {
  room: Room;
  currentUser: User;
  token: string;
  speakingLang: string;
  listeningLang: string;
  onUpdateListeningLang: (lang: string) => void;
  onConcludeSession: (insights: SessionInsights) => void;
  onLeaveRoom: () => void;
  onToast: (msg: string, tone?: 'success' | 'error' | 'info') => void;
  onCopyInvite: () => void;
}

export function DialogueCanvas({
  room,
  currentUser,
  token,
  speakingLang,
  listeningLang,
  onUpdateListeningLang,
  onConcludeSession,
  onLeaveRoom,
  onToast,
  onCopyInvite,
}: DialogueCanvasProps) {
  const isHost = Boolean(room.host_id && String(room.host_id) === String(currentUser.id));
  const [participants, setParticipants] = useState<Participant[]>([
    {
      user_id: currentUser.id,
      user_name: currentUser.display_name || currentUser.username,
      avatar_color: currentUser.avatar_color || '#10B981',
      speaking_lang: speakingLang,
      listening_lang: listeningLang,
    },
  ]);
  const [speakingUserId, setSpeakingUserId] = useState<string | number | null>(null);
  const [isMicMuted, setIsMicMuted] = useState(false);
  const [isAudioMuted, setIsAudioMuted] = useState(false);
  const [inputText, setInputText] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [transcripts, setTranscripts] = useState<TranscriptTurn[]>([]);
  const [activeSubtitle, setActiveSubtitle] = useState<TranscriptTurn | null>(null);
  const [ending, setEnding] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [showLeaveModal, setShowLeaveModal] = useState(false);
  const [showEndModal, setShowEndModal] = useState(false);
  const [showClearModal, setShowClearModal] = useState(false);
  const [connected, setConnected] = useState(false);

  const speakingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const subtitleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const transcriptScrollRef = useRef<HTMLDivElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Load existing transcripts & participants on mount
  useEffect(() => {
    roomService
      .getTranscripts(room.room_code)
      .then((data) => {
        if (Array.isArray(data)) setTranscripts(data);
      })
      .catch((err) => {
        console.error('Could not fetch transcripts', err);
      });

    roomService
      .getParticipants(room.room_code)
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          const seen = new Set<string>();
          const deduped: Participant[] = [];
          for (const item of data) {
            const sid = String(item.user_id);
            if (!seen.has(sid)) {
              seen.add(sid);
              deduped.push(item);
            }
          }
          setParticipants(deduped);
        }
      })
      .catch((err) => {
        console.warn('Could not fetch initial participants', err);
      });
  }, [room.room_code]);

  // Audio queue for incoming TTS
  const playAudio = useCallback(
    (audioDataUrl: string) => {
      if (isAudioMuted || !audioDataUrl) return;
      try {
        if (!audioRef.current) {
          audioRef.current = new Audio();
        }
        const savedOutput = typeof localStorage !== 'undefined' ? localStorage.getItem('kinesia_audio_output') : null;
        if (savedOutput && savedOutput !== 'default' && typeof (audioRef.current as any).setSinkId === 'function') {
          (audioRef.current as any).setSinkId(savedOutput).catch(() => {});
        }
        audioRef.current.src = audioDataUrl;
        audioRef.current.play().catch(() => {});
      } catch (err) {
        console.error('Playback error:', err);
      }
    },
    [isAudioMuted]
  );

  const handleTranscriptTurn = useCallback(
    (turn: TranscriptTurn) => {
      setTranscripts((prev) => [...prev, turn]);
      setActiveSubtitle(turn);

      if (turn.speaker_id != null) {
        setSpeakingUserId(turn.speaker_id);
        if (speakingTimerRef.current) clearTimeout(speakingTimerRef.current);
        speakingTimerRef.current = setTimeout(() => setSpeakingUserId(null), 2500);
      }

      if (subtitleTimerRef.current) clearTimeout(subtitleTimerRef.current);
      subtitleTimerRef.current = setTimeout(() => setActiveSubtitle(null), 6000);

      const isMe = String(turn.speaker_id) === String(currentUser.id);
      if (turn.audio_data_url && !isMe) {
        playAudio(turn.audio_data_url);
      } else if (
        !isAudioMuted &&
        turn.localized_text &&
        !isMe &&
        typeof window !== 'undefined' &&
        'speechSynthesis' in window
      ) {
        try {
          const utterance = new SpeechSynthesisUtterance(turn.localized_text);
          utterance.lang = mapLang(listeningLang || turn.listening_lang || 'en');
          window.speechSynthesis.speak(utterance);
        } catch {
          /* browser speech synthesis error ignored */
        }
      }
    },
    [currentUser.id, isAudioMuted, listeningLang, playAudio]
  );

  useRoomSSE(
    room.room_code,
    token,
    {
      onOpen: () => setConnected(true),
      onError: () => setConnected(false),
      onTurn: handleTranscriptTurn,
      onParticipants: (list: Participant[]) => {
        if (Array.isArray(list) && list.length > 0) {
          const seen = new Set<string>();
          const deduped: Participant[] = [];
          for (const item of list) {
            const sid = String(item.user_id);
            if (!seen.has(sid)) {
              seen.add(sid);
              deduped.push(item);
            }
          }
          setParticipants(deduped);
        }
      },
      onParticipantJoined: (p: Participant) => {
        setParticipants((prev) => {
          const exists = prev.some((x) => String(x.user_id) === String(p.user_id));
          return exists ? prev.map((x) => (String(x.user_id) === String(p.user_id) ? p : x)) : [...prev, p];
        });
        onToast(`${p.user_name} joined the conversation`, 'info');
      },
      onParticipantLeft: (uid: string | number) => {
        setParticipants((prev) => prev.filter((x) => String(x.user_id) !== String(uid)));
      },
      onSpeaking: (uid: number | string) => {
        setSpeakingUserId(uid);
        if (speakingTimerRef.current) clearTimeout(speakingTimerRef.current);
        speakingTimerRef.current = setTimeout(() => setSpeakingUserId(null), 2500);
      },
      onSessionConcluded: (res) => {
        onToast('Conversation concluded by host', 'info');
        onConcludeSession(res as SessionInsights);
      },
      onTranscriptsCleared: () => {
        setTranscripts([]);
        setActiveSubtitle(null);
        onToast('Conversation logs cleared', 'info');
      },
    },
    { speakingLang, listeningLang }
  );

  // Speech dictation
  const handleDictatedText = useCallback(
    async (text: string) => {
      if (!text.trim() || isMicMuted) return;
      try {
        await roomService.speak(room.room_code, {
          text: text.trim(),
          source_lang: speakingLang,
        });
      } catch (err) {
        onToast(err instanceof Error ? err.message : 'Failed to send speech', 'error');
      }
    },
    [isMicMuted, room.room_code, speakingLang, onToast]
  );

  const { listening, supported } = useSpeechDictation({
    lang: speakingLang,
    enabled: !isMicMuted,
    onResult: (text: string) => void handleDictatedText(text),
  });

  useEffect(() => {
    if (!supported) {
      onToast('Speech recognition not supported in this browser; text chat available', 'info');
    }
  }, [supported, onToast]);

  // Scroll transcripts to bottom
  useEffect(() => {
    if (drawerOpen && transcriptScrollRef.current) {
      transcriptScrollRef.current.scrollTop = transcriptScrollRef.current.scrollHeight;
    }
  }, [drawerOpen, transcripts]);

  const handleListeningLangChange = (newLang: string) => {
    onUpdateListeningLang(newLang);
    roomService.updateLanguages(room.room_code, { listening_lang: newLang }).catch(() => {});
  };

  const handleSendText = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    const text = inputText.trim();
    setInputText('');
    try {
      await roomService.speak(room.room_code, {
        text,
        source_lang: speakingLang,
      });
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to send text', 'error');
    }
  };

  const handleLeave = async () => {
    setLeaving(true);
    try {
      await roomService.leaveRoom(room.room_code);
    } catch {
      /* ignore if already disconnected */
    } finally {
      setLeaving(false);
      setShowLeaveModal(false);
      onLeaveRoom();
    }
  };

  const handleConclude = async () => {
    setEnding(true);
    try {
      const res = await roomService.endSession(room.room_code);
      setShowEndModal(false);
      onConcludeSession(res);
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Could not conclude session', 'error');
    } finally {
      setEnding(false);
    }
  };

  const handleClearTranscripts = async () => {
    setClearing(true);
    try {
      await roomService.clearTranscripts(room.room_code);
      setTranscripts([]);
      setActiveSubtitle(null);
      setShowClearModal(false);
      onToast('Logs cleared successfully', 'success');
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to clear logs', 'error');
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-canvas">
      {/* Top bar */}
      <header className="shrink-0 h-14 border-b border-border-subtle bg-surface-0/80 backdrop-blur-xl flex items-center justify-between px-4 sm:px-6">
        <div className="flex items-center gap-3 min-w-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowLeaveModal(true)}
            leftIcon={<ArrowLeft size={16} />}
            className="text-ink-muted hover:text-ink-primary -ml-2"
          >
            Leave
          </Button>

          <div className="h-4 w-px bg-border" />

          <div className="min-w-0">
            <h1 className="text-sm font-semibold text-ink-primary truncate">{room.title}</h1>
            <div className="flex items-center gap-2 text-2xs text-ink-muted">
              <span className="font-mono tracking-wider">{room.room_code}</span>
              <Badge tone={connected ? 'accent' : 'warn'} dot className="scale-90 origin-left">
                {connected ? 'Live' : 'Reconnecting'}
              </Badge>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <Button variant="ghost" size="icon" onClick={onCopyInvite} title="Copy invite">
            <Share2 size={16} />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setDrawerOpen(true)}
            title="View Logs / Transcript"
            className="relative"
          >
            <FileText size={16} />
            {transcripts.length > 0 && (
              <span className="absolute top-1 right-1 h-2 w-2 rounded-full bg-accent" />
            )}
          </Button>
          <div className="hidden sm:flex items-center gap-1.5 ml-1">
            <Languages size={14} className="text-ink-muted" />
            <select
              value={listeningLang}
              onChange={(e) => handleListeningLangChange(e.target.value)}
              className="h-8 rounded-lg bg-surface-2 border border-border text-2xs text-ink-secondary px-2 focus:outline-none focus:border-accent/50 cursor-pointer"
            >
              {SUPPORTED_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  Hear {l.short}
                </option>
              ))}
            </select>
          </div>
        </div>
      </header>

      {/* Stage */}
      <main className="flex-1 relative overflow-hidden flex flex-col items-center justify-center p-4 sm:p-8">
        <div
          className={cn(
            'w-full max-w-4xl grid gap-4 sm:gap-5',
            participants.length <= 1 && 'max-w-md',
            participants.length === 2 && 'grid-cols-1 sm:grid-cols-2 max-w-2xl',
            participants.length >= 3 && 'grid-cols-2 sm:grid-cols-3'
          )}
        >
          {participants.map((p) => {
            const isSpeaking = speakingUserId != null && String(speakingUserId) === String(p.user_id);
            const isMe = String(p.user_id) === String(currentUser.id);
            const isMuted = isMe ? isMicMuted : Boolean(p.is_muted);

            return (
              <motion.div
                key={p.user_id}
                layout
                className={cn(
                  'relative rounded-2xl bg-surface-1 border border-border p-6 flex flex-col items-center justify-center min-h-[170px] transition-all',
                  isSpeaking && 'border-accent shadow-glow scale-[1.02]'
                )}
              >
                <div
                  className="h-16 w-16 rounded-2xl flex items-center justify-center text-lg font-bold text-white shadow-elevation-2 mb-3 relative"
                  style={{ backgroundColor: p.avatar_color || '#10B981' }}
                >
                  {initials(p.user_name)}
                  {isSpeaking && (
                    <span className="absolute -inset-1 rounded-2xl border-2 border-accent animate-ping-soft" />
                  )}
                  {isMuted && (
                    <span
                      title="Microphone is muted"
                      className="absolute -bottom-1 -right-1 h-5 w-5 rounded-full bg-danger text-white flex items-center justify-center shadow-sm"
                    >
                      <MicOff size={11} />
                    </span>
                  )}
                </div>
                <div className="text-center min-w-0 max-w-full">
                  <span className="text-sm font-semibold text-ink-primary block truncate">
                    {p.user_name} {isMe && '(You)'}
                  </span>
                  <div className="flex items-center justify-center gap-1.5 text-2xs text-ink-muted mt-0.5">
                    <span className="font-medium text-ink-secondary">{langShort(p.speaking_lang || 'en')}</span>
                    <span className="text-accent font-bold">→</span>
                    <span className="font-medium text-ink-secondary">{langShort(p.listening_lang || 'hi')}</span>
                  </div>
                </div>
              </motion.div>
            );
          })}
        </div>

        {/* Waiting indicator when alone in room */}
        {participants.length <= 1 && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 flex flex-col items-center gap-2 text-center"
          >
            <div className="flex items-center gap-2 rounded-full bg-surface-2 border border-border px-3.5 py-1.5 text-xs text-ink-muted">
              <span className="h-2 w-2 rounded-full bg-accent animate-pulse" />
              <span>Waiting for others to join...</span>
            </div>
            <button
              type="button"
              onClick={onCopyInvite}
              className="text-xs text-accent hover:underline flex items-center gap-1.5 transition-colors cursor-pointer"
            >
              <Copy size={13} />
              <span>Copy invite link ({room.room_code})</span>
            </button>
          </motion.div>
        )}

        {/* Live Subtitle HUD */}
        <AnimatePresence>
          {activeSubtitle && (
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              className="absolute bottom-28 left-4 right-4 sm:left-auto sm:right-auto sm:max-w-xl mx-auto z-20 pointer-events-none"
            >
              <div className="rounded-2xl bg-surface-0/95 backdrop-blur-xl border border-border shadow-elevation-3 p-4 space-y-1">
                <div className="flex items-center justify-between text-2xs text-ink-muted">
                  <span className="font-semibold text-accent">{activeSubtitle.speaker_name}</span>
                  <span className="font-mono">{langShort(activeSubtitle.source_lang)}</span>
                </div>
                <p className="text-sm sm:text-base font-medium text-ink-primary leading-snug">
                  {activeSubtitle.localized_text || activeSubtitle.original_text}
                </p>
                {activeSubtitle.original_text !== activeSubtitle.localized_text && (
                  <p className="text-xs text-ink-muted italic">{activeSubtitle.original_text}</p>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      {/* Control bar */}
      <footer className="shrink-0 safe-pb border-t border-border-subtle bg-surface-0/80 backdrop-blur-xl p-3 sm:p-4">
        <div className="mx-auto max-w-2xl flex flex-col sm:flex-row items-center gap-3">
          <form onSubmit={handleSendText} className="flex-1 w-full flex items-center gap-2">
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="Type message to translate & speak aloud..."
              className="flex-1 h-10 rounded-xl bg-surface-2 border border-border px-3.5 text-sm text-ink-primary placeholder:text-ink-faint focus:outline-none focus:border-accent/60"
            />
            <Button type="submit" size="sm" disabled={!inputText.trim()}>
              <Send size={15} />
            </Button>
          </form>

          <div className="flex items-center gap-2">
            <Button
              variant={isMicMuted ? 'danger' : listening ? 'primary' : 'secondary'}
              size="sm"
              onClick={() => {
                const next = !isMicMuted;
                setIsMicMuted(next);
                roomService.updateLanguages(room.room_code, { is_muted: next }).catch(() => {});
              }}
              title={isMicMuted ? 'Unmute microphone' : 'Mute microphone'}
            >
              {isMicMuted ? <MicOff size={16} /> : <Mic size={16} />}
              <span className="hidden sm:inline text-xs ml-1.5">
                {isMicMuted ? 'Muted' : 'Mic On'}
              </span>
            </Button>

            <Button
              variant={isAudioMuted ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => setIsAudioMuted((v) => !v)}
              title={isAudioMuted ? 'Unmute incoming audio' : 'Mute incoming audio'}
            >
              {isAudioMuted ? <VolumeX size={16} /> : <Volume2 size={16} />}
            </Button>

            {isHost ? (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setShowLeaveModal(true)}
                  leftIcon={<ArrowLeft size={15} />}
                >
                  Leave
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setShowEndModal(true)}
                  leftIcon={<PhoneOff size={15} />}
                >
                  End for All
                </Button>
              </>
            ) : (
              <Button
                variant="danger"
                size="sm"
                onClick={() => setShowLeaveModal(true)}
                leftIcon={<PhoneOff size={15} />}
              >
                Leave Room
              </Button>
            )}
          </div>
        </div>
      </footer>

      {/* Transcript / Logs Drawer */}
      <AnimatePresence>
        {drawerOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
              onClick={() => setDrawerOpen(false)}
            />
            <motion.aside
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ type: 'spring', damping: 28, stiffness: 320 }}
              className="fixed top-0 right-0 bottom-0 z-50 w-full max-w-md bg-surface-1 border-l border-border shadow-elevation-3 flex flex-col"
            >
              <div className="h-14 flex items-center justify-between px-5 border-b border-border">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-ink-primary">Session Logs & Transcript</h2>
                  <Badge tone="default">{transcripts.length}</Badge>
                </div>
                <div className="flex items-center gap-1.5">
                  {transcripts.length > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setShowClearModal(true)}
                      className="text-danger hover:text-danger hover:bg-danger/10 text-xs h-8 px-2.5"
                      leftIcon={<Trash2 size={13} />}
                    >
                      Clear
                    </Button>
                  )}
                  <Button variant="ghost" size="icon" onClick={() => setDrawerOpen(false)}>
                    <X size={16} />
                  </Button>
                </div>
              </div>

              <div ref={transcriptScrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
                {transcripts.length === 0 ? (
                  <div className="text-center py-16 space-y-2">
                    <p className="text-sm text-ink-muted">No logs recorded yet.</p>
                    <p className="text-2xs text-ink-faint">
                      Transcripts are private and visible only to participants in this room.
                    </p>
                  </div>
                ) : (
                  transcripts.map((t, i) => (
                    <div key={t.id ?? i} className="rounded-xl bg-surface-0 border border-border p-3 space-y-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-accent">{t.speaker_name}</span>
                        <span className="text-2xs text-ink-faint font-mono">
                          {langShort(t.source_lang)}
                        </span>
                      </div>
                      <p className="text-sm text-ink-primary leading-snug">
                        {t.localized_text || t.original_text}
                      </p>
                      {t.original_text !== t.localized_text && (
                        <p className="text-2xs text-ink-muted italic">{t.original_text}</p>
                      )}
                    </div>
                  ))
                )}
              </div>
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      {/* Leave Room Modal */}
      <AnimatePresence>
        {showLeaveModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="w-full max-w-sm rounded-2xl border border-border bg-surface-1 p-6 space-y-4 shadow-elevation-3"
            >
              <div className="flex items-center gap-3 text-ink-primary">
                <div className="h-10 w-10 rounded-xl bg-surface-2 flex items-center justify-center text-accent">
                  <ArrowLeft size={18} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm">Leave Session?</h3>
                  <p className="text-2xs text-ink-muted">You can rejoin anytime with code {room.room_code}</p>
                </div>
              </div>
              <p className="text-xs text-ink-secondary leading-relaxed">
                Leaving will disconnect your microphone and audio stream. The room remains active for other participants.
              </p>
              <div className="flex items-center justify-end gap-2 pt-2">
                <Button variant="secondary" size="sm" onClick={() => setShowLeaveModal(false)} disabled={leaving}>
                  Stay in room
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  loading={leaving}
                  onClick={handleLeave}
                >
                  Leave room
                </Button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* End Room Confirmation Modal (Host Only) */}
      <AnimatePresence>
        {showEndModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="w-full max-w-sm rounded-2xl border border-border bg-surface-1 p-6 space-y-4 shadow-elevation-3"
            >
              <div className="flex items-center gap-3 text-ink-primary">
                <div className="h-10 w-10 rounded-xl bg-danger/10 flex items-center justify-center text-danger">
                  <PhoneOff size={18} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm">End Session for Everyone?</h3>
                  <p className="text-2xs text-ink-muted">Room {room.room_code}</p>
                </div>
              </div>
              <p className="text-xs text-ink-secondary leading-relaxed">
                As host, ending the session will disconnect all participants and immediately generate an AI summary and action items.
              </p>
              <div className="flex items-center justify-end gap-2 pt-2">
                <Button variant="secondary" size="sm" onClick={() => setShowEndModal(false)} disabled={ending}>
                  Keep Room Active
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  loading={ending}
                  onClick={handleConclude}
                >
                  End Session
                </Button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* Clear Logs Confirmation Modal */}
      <AnimatePresence>
        {showClearModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="w-full max-w-sm rounded-2xl border border-border bg-surface-1 p-6 space-y-4 shadow-elevation-3"
            >
              <div className="flex items-center gap-3 text-ink-primary">
                <div className="h-10 w-10 rounded-xl bg-danger/10 flex items-center justify-center text-danger">
                  <AlertTriangle size={18} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm">Clear Conversation Logs?</h3>
                  <p className="text-2xs text-ink-muted">This will erase all turns for this room</p>
                </div>
              </div>
              <p className="text-xs text-ink-secondary leading-relaxed">
                All recorded speech transcripts for this session will be permanently cleared for everyone in this room.
              </p>
              <div className="flex items-center justify-end gap-2 pt-2">
                <Button variant="secondary" size="sm" onClick={() => setShowClearModal(false)}>
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  loading={clearing}
                  onClick={handleClearTranscripts}
                >
                  Clear logs
                </Button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
