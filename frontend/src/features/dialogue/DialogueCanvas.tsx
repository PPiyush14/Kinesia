import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Check,
  Copy,
  Download,
  FileText,
  Headphones,
  Mic,
  MicOff,
  PhoneOff,
  Radio,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  Users,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react';
import type { Participant, Room, SessionInsights, TranscriptTurn, User } from '@/types';
import { SUPPORTED_LANGUAGES, langLabel, langShort } from '@/types';
import { roomService } from '@/services/api';
import { useRealtimeRoom } from '@/hooks/useRealtimeRoom';
import { Button } from '@/components/ui/Button';
import { cn, initials } from '@/lib/utils';
import { mergeTranscriptHistory } from '@/lib/transcripts';
import { TranscriptItem } from '@/features/insights/TranscriptItem';
import { useSavedMoments } from '@/hooks/useSavedMoments';

interface DialogueCanvasProps {
  room: Room;
  currentUser: User;
  token: string;
  speakingLang: string;
  listeningLang: string;
  onUpdateSpeakingLang?: (lang: string) => void;
  onUpdateListeningLang: (lang: string) => void;
  onConcludeSession: (insights: SessionInsights) => void;
  onLeaveRoom: () => void;
  onToast: (message: string, tone?: 'success' | 'error' | 'info') => void;
  onCopyInvite: () => void;
}

export function DialogueCanvas(props: DialogueCanvasProps) {
  const { room, currentUser, token, speakingLang, listeningLang, onToast } = props;
  const isHost = String(room.host_id) === String(currentUser.id);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [micMuted, setMicMuted] = useState(() => sessionStorage.getItem('kinesia_join_muted') === 'true');
  const [audioMuted, setAudioMuted] = useState(false);
  const [transcripts, setTranscripts] = useState<TranscriptTurn[]>([]);
  const [peerCaptions, setPeerCaptions] = useState<Record<string, TranscriptTurn>>({});
  const [ownCaption, setOwnCaption] = useState('');
  const [speakingIds, setSpeakingIds] = useState<Set<string>>(new Set());
  const [drawer, setDrawer] = useState(false);
  const [original, setOriginal] = useState(true);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [modal, setModal] = useState<'leave' | 'end' | 'clear' | null>(null);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const actions = useRef<{ playEncoded: (url: string) => void; cancelAudio: () => void } | null>(null);
  const completed = useRef(false);
  const historyRevision = useRef(0);
  const scroll = useRef<HTMLDivElement | null>(null);
  const rememberCaption = (turn: TranscriptTurn) => setPeerCaptions((previous) => {
    const key = String(turn.speaker_id);
    const old = previous[key];
    if (old?.audio_end_ms != null && turn.audio_end_ms != null && old.audio_end_ms > turn.audio_end_ms) return previous;
    return { ...previous, [key]: turn };
  });

  const finish = (insights: SessionInsights) => {
    if (completed.current) return;
    completed.current = true;
    actions.current?.cancelAudio();
    props.onConcludeSession(insights);
  };
  const live = useRealtimeRoom(
    room.room_code,
    token,
    {
      onParticipants: setParticipants,
      onParticipantJoined: (participant) => {
        setParticipants((previous) => [
          ...previous.filter((item) => String(item.user_id) !== String(participant.user_id)),
          participant,
        ]);
      },
      onParticipantLeft: (id) =>
        setParticipants((previous) => previous.filter((item) => String(item.user_id) !== String(id))),
      onSpeaking: (id, _name, active = true) => {
        setSpeakingIds((previous) => {
          const next = new Set(previous);
          if (active) next.add(String(id)); else next.delete(String(id));
          return next;
        });
      },
      onCaption: (turn) => {
        if (String(turn.speaker_id) === String(currentUser.id)) setOwnCaption(turn.original_text);
        else if (turn.localized_text && turn.listening_lang === listeningLang) rememberCaption(turn);
      },
      onTurn: (turn) => {
        const id = String(turn.turn_id ?? turn.id);
        setTranscripts((previous) => {
          const index = previous.findIndex((item) => String(item.turn_id ?? item.id) === id);
          return index < 0
            ? [...previous, turn]
            : previous.map((item, position) => (position === index ? turn : item));
        });
        if (String(turn.speaker_id) === String(currentUser.id)) setOwnCaption('');
        else {
          const localized = turn.translations?.[listeningLang] || (turn.listening_lang === listeningLang ? turn.localized_text : '');
          if (localized) rememberCaption({ ...turn, localized_text: localized });
          if (turn.audio_data_url) actions.current?.playEncoded(turn.audio_data_url);
        }
      },
      onSessionConcluded: (insights) => finish(insights as SessionInsights),
      onTranscriptsCleared: () => {
        historyRevision.current++;
        setTranscripts([]);
        setPeerCaptions({});
        savedMoments.refresh();
        onToast('Conversation transcript cleared', 'info');
      },
    },
    { speakingLang, listeningLang, micMuted, audioMuted }
  );
  actions.current = live;
  const savedMoments = useSavedMoments(room.room_code, (message) => onToast(message, 'error'), live.connected && room.record_session !== false);

  useEffect(() => {
    sessionStorage.setItem('kinesia_join_muted', String(micMuted));
  }, [micMuted]);

  useEffect(() => {
    setTranscripts([]);
    setPeerCaptions({});
    completed.current = false;
    const timer = setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => {
      clearInterval(timer);
    };
  }, [room.room_code, currentUser.id]);
  useEffect(() => {
    if (!live.connected) { setSpeakingIds(new Set()); return; }
    let cancelled = false;
    const revision = ++historyRevision.current;
    roomService.getTranscripts(room.room_code).then((history) => {
      if (!cancelled && revision === historyRevision.current) setTranscripts((current) => mergeTranscriptHistory(history, current, listeningLang));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [room.room_code, live.connected, listeningLang]);
  useEffect(() => {
    if (drawer) scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' });
  }, [drawer, transcripts]);
  useEffect(() => {
    const unlock = () => live.unlockAudio();
    window.addEventListener('pointerdown', unlock, { once: true });
    window.addEventListener('keydown', unlock, { once: true });
    return () => {
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('keydown', unlock);
    };
  }, [live.unlockAudio]);

  const me: Participant = {
    user_id: currentUser.id,
    user_name: currentUser.display_name || currentUser.username,
    avatar_color: currentUser.avatar_color,
    speaking_lang: speakingLang,
    listening_lang: listeningLang,
    is_muted: micMuted,
  };
  const roster = [
    me,
    ...participants.filter((participant) => String(participant.user_id) !== String(currentUser.id)),
  ];
  const duration = `${Math.floor(elapsed / 60)
    .toString()
    .padStart(2, '0')}:${(elapsed % 60).toString().padStart(2, '0')}`;
  const sendText = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!text.trim() || sending) return;
    const value = text.trim();
    setSending(true);
    try {
      await roomService.speak(room.room_code, {
        text: value,
        source_lang: speakingLang,
        utterance_id: crypto.randomUUID(),
      });
      setText('');
    } catch (error) {
      onToast(error instanceof Error ? error.message : 'Message could not be translated', 'error');
    } finally {
      setSending(false);
    }
  };
  const confirm = async () => {
    setBusy(true);
    try {
      if (modal === 'end') finish(await roomService.endSession(room.room_code));
      else if (modal === 'clear') {
        await roomService.clearTranscripts(room.room_code);
        historyRevision.current++;
        setTranscripts([]);
        setPeerCaptions({});
      } else {
        await roomService.leaveRoom(room.room_code).catch(() => {});
        props.onLeaveRoom();
      }
      setModal(null);
    } catch (error) {
      onToast(error instanceof Error ? error.message : 'Please try again', 'error');
    } finally {
      setBusy(false);
    }
  };
  const exportTranscript = () => {
    const body = transcripts
      .map(
        (turn) =>
          `${turn.speaker_name}: ${turn.original_text}\n${turn.localized_text !== turn.original_text ? turn.localized_text : ''}`
      )
      .join('\n\n');
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `kinesia-${room.room_code}.txt`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-canvas text-ink-primary">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border-subtle bg-surface-0/90 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="hidden h-10 w-10 items-center justify-center rounded-xl border border-accent/25 bg-accent/10 text-accent sm:flex">
            <Radio size={20} />
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold sm:text-base">{room.title}</h1>
            <div className="mt-1 flex items-center gap-2 text-2xs text-ink-muted">
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  live.connected ? 'bg-accent' : 'bg-warn animate-pulse'
                )}
              />
              <span>
                {live.connected
                  ? 'Live translation'
                  : live.status === 'disconnected'
                    ? 'Disconnected'
                    : 'Connecting'}
              </span>
              <span className="text-border">/</span>
              <span className="font-mono tabular-nums">{duration}</span>
              <span className="hidden font-mono text-ink-faint sm:inline">{room.room_code}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={props.onCopyInvite} leftIcon={<Copy size={14} />}>
            <span className="hidden sm:inline">Invite</span>
          </Button>
          <Button
            variant={drawer ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setDrawer(!drawer)}
            leftIcon={<FileText size={15} />}
          >
            <span className="hidden sm:inline">Transcript</span>
            <span className="font-mono text-2xs">{transcripts.length}</span>
          </Button>
          <Button variant="ghost" size="icon" title="Leave meeting" onClick={() => setModal('leave')}>
            <X size={18} />
          </Button>
        </div>
      </header>

      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border-subtle bg-surface-0/40 px-4 py-2.5 sm:px-6">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-2 text-ink-muted">
            <Mic size={13} />
            <span className="hidden sm:inline">I speak</span>
            <select
              aria-label="Speaking language"
              value={speakingLang}
              onChange={(event) => props.onUpdateSpeakingLang?.(event.target.value)}
              className="max-w-[120px] rounded-lg border border-border bg-surface-1 px-2 py-1.5 text-xs text-ink-primary focus:border-accent"
            >
              {SUPPORTED_LANGUAGES.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <span className="text-ink-faint">→</span>
          <label className="flex items-center gap-2 text-ink-muted">
            <Headphones size={13} />
            <span className="hidden sm:inline">I hear</span>
            <select
              aria-label="Listening language"
              value={listeningLang}
              onChange={(event) => {
                live.cancelAudio();
                setPeerCaptions({});
                props.onUpdateListeningLang(event.target.value);
              }}
              className="max-w-[120px] rounded-lg border border-border bg-surface-1 px-2 py-1.5 text-xs text-ink-primary focus:border-accent"
            >
              {SUPPORTED_LANGUAGES.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="hidden items-center gap-4 text-2xs text-ink-muted md:flex">
          <span className="flex items-center gap-1.5">
            <Users size={13} />
            {roster.length} participant{roster.length === 1 ? '' : 's'}
          </span>
          <span className="flex items-center gap-1.5">
            <ShieldCheck size={13} />
            {room.record_session === false ? 'Not recorded' : 'Transcript saved'}
          </span>
        </div>
      </div>

      {live.error && (
        <div
          role="alert"
          className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-warn/20 bg-warn/10 px-4 py-3 text-xs text-warn sm:px-6"
        >
          <span className="max-w-3xl leading-relaxed">{live.error}</span>
          <Button variant="ghost" size="sm" onClick={live.reconnect} leftIcon={<RefreshCw size={13} />}>
            Reconnect audio
          </Button>
        </div>
      )}
      {live.autoplayBlocked && (
        <div className="flex shrink-0 items-center justify-center gap-3 bg-accent/10 px-4 py-2.5 text-xs text-accent">
          <Volume2 size={15} />
          <span>Your browser needs permission to play translated audio.</span>
          <Button size="sm" onClick={live.unlockAudio}>
            Enable audio
          </Button>
        </div>
      )}

      <div className="relative flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col overflow-y-auto [overflow-anchor:none]">
          <div className="flex shrink-0 flex-col items-center gap-5 px-4 pb-4 pt-6 sm:px-8">
            <div
              className={cn(
                'grid w-full gap-3 sm:gap-4',
                roster.length === 1
                  ? 'max-w-sm'
                  : roster.length === 2
                    ? 'max-w-2xl grid-cols-2'
                    : 'max-w-4xl grid-cols-2 lg:grid-cols-3'
              )}
            >
              {roster.map((participant) => {
                const self = String(participant.user_id) === String(currentUser.id);
                const active = self
                  ? live.isSpeaking && !micMuted
                  : speakingIds.has(String(participant.user_id)) && !participant.is_muted;
                const muted = self ? micMuted : participant.is_muted;
                return (
                  <motion.div
                    key={participant.user_id}
                    data-testid="participant-card"
                    className={cn(
                      'relative flex min-w-0 min-h-[150px] flex-col items-center justify-center rounded-2xl border bg-surface-1 p-4 pt-8 transition-colors sm:min-h-[165px]',
                      active ? 'border-accent/80 bg-accent/5 shadow-glow' : 'border-border'
                    )}
                  >
                    <span className="absolute left-3 top-3 text-2xs text-ink-faint">
                      {self ? 'Your microphone' : active ? 'Speaking' : 'In the room'}
                    </span>
                    <span
                      className={cn(
                        'absolute right-3 top-3',
                        muted ? 'text-danger' : active ? 'text-accent' : 'text-ink-faint'
                      )}
                    >
                      {muted ? <MicOff size={14} /> : <Mic size={14} />}
                    </span>
                    <div
                      className={cn(
                        'mb-3 flex h-12 w-12 items-center justify-center rounded-xl text-base font-bold text-white sm:h-14 sm:w-14',
                        active && 'ring-4 ring-accent/15'
                      )}
                      style={{ backgroundColor: participant.avatar_color || '#10B981' }}
                    >
                      {initials(participant.user_name)}
                    </div>
                    <p className="max-w-full truncate text-sm font-semibold">
                      {participant.user_name}
                      {self && <span className="ml-1.5 text-xs font-normal text-ink-muted">(you)</span>}
                    </p>
                    <p className="mt-1.5 flex items-center gap-2 text-2xs text-ink-muted">
                      <span>{langShort(participant.speaking_lang || 'en')}</span>
                      <span className="text-ink-faint">→</span>
                      <Headphones size={11} />
                      <span>{langShort(participant.listening_lang || 'hi')}</span>
                    </p>
                    {self && (
                      <div className="mt-3 h-1 w-20 overflow-hidden rounded-full bg-surface-3">
                        <div
                          className="h-full rounded-full bg-accent transition-all duration-100"
                          style={{ width: `${micMuted ? 0 : Math.max(2, live.level * 100)}%` }}
                        />
                      </div>
                    )}
                  </motion.div>
                );
              })}
            </div>
            {roster.length === 1 && (
              <div className="max-w-sm space-y-3 text-center">
                <p className="text-sm text-ink-secondary">Your room is ready.</p>
                <p className="text-xs leading-relaxed text-ink-muted">
                  Invite someone to start a conversation. Each person chooses the language they want to hear.
                </p>
                <button
                  onClick={props.onCopyInvite}
                  className="inline-flex items-center gap-2 text-xs font-medium text-accent"
                >
                  <Copy size={13} />
                  Copy invitation
                </button>
              </div>
            )}
          </div>

          <section aria-label="Live captions" className="mx-auto my-auto w-full max-w-4xl flex-1 px-4 py-6 sm:px-6">
            <div className="min-h-[220px] rounded-3xl border border-border bg-gradient-to-b from-surface-1 to-surface-0 p-5 shadow-elevation-1 sm:p-7">
              <div className="mb-3 flex items-center justify-between gap-2 text-2xs text-ink-muted">
                <span className="flex items-center gap-2">
                  <span
                    className={cn(
                      'h-1.5 w-1.5 rounded-full',
                      live.isPlayingAudio ? 'bg-accent animate-pulse' : 'bg-accent/50'
                    )}
                  />
                  Live captions · {langLabel(listeningLang)}
                </span>
                <button
                  onClick={() => setOriginal(!original)}
                  aria-pressed={original}
                  title="Show the speaker's original words beneath the translation"
                  className="flex items-center gap-1 text-ink-muted hover:text-ink-primary"
                >
                  Show original text {original && <Check size={11} />}
                </button>
              </div>
              <div role="status" aria-live="polite" className="space-y-5">
                {Object.values(peerCaptions).slice(-3).map((turn) => <div key={String(turn.speaker_id)} className="border-l-2 border-accent/40 pl-4">
                  <p className="mb-2 text-2xs font-medium text-accent">{turn.speaker_name}</p>
                  <p className="whitespace-pre-wrap text-lg leading-relaxed text-ink-primary sm:text-2xl">{turn.localized_text || turn.original_text}</p>
                  {original && turn.original_text !== turn.localized_text && <p className="mt-3 text-xs leading-relaxed text-ink-muted">{turn.original_text}</p>}
                </div>)}
                {Object.keys(peerCaptions).length === 0 && <div className="py-7">
                  <p className="font-display text-xl text-ink-secondary sm:text-2xl">A conversation, in your language.</p>
                  <p className="mt-3 text-sm leading-relaxed text-ink-muted">Translations appear here as people speak. Original words are always one tap away.</p>
                </div>}
              </div>
              {ownCaption && (
                <p className="mt-3 text-xs text-ink-muted">
                  <span className="font-medium text-accent">You: </span>
                  {ownCaption}
                </p>
              )}
            </div>
          </section>
        </main>

        <AnimatePresence>
          {drawer && (
            <motion.aside
              initial={{ x: 40, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 40, opacity: 0 }}
              className="absolute inset-y-0 right-0 z-20 flex w-full flex-col border-l border-border bg-surface-1 shadow-elevation-3 sm:w-96 xl:relative xl:shrink-0"
            >
              <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <div>
                  <h2 className="text-sm font-semibold">Conversation transcript</h2>
                  <p className="mt-1 text-2xs text-ink-muted">
                    {room.record_session === false
                      ? 'Visible during this session only'
                      : 'Saved for room participants'}
                  </p>
                </div>
                <Button variant="ghost" size="icon" onClick={() => setDrawer(false)} title="Close transcript">
                  <X size={17} />
                </Button>
              </div>
              <div className="flex gap-2 border-b border-border-subtle px-4 py-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={exportTranscript}
                  disabled={!transcripts.length}
                  leftIcon={<Download size={13} />}
                >
                  Export
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setModal('clear')}
                  disabled={!transcripts.length || !isHost}
                  title={isHost ? 'Clear the shared transcript for everyone' : 'Only the host can clear the shared transcript'}
                  className="text-danger"
                  leftIcon={<Trash2 size={13} />}
                >
                  Clear
                </Button>
              </div>
              <div ref={scroll} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
                {!transcripts.length && (
                  <p className="py-10 text-center text-xs leading-relaxed text-ink-muted">
                    Finalized speech will appear here.
                    <br />
                    Live captions may update while someone speaks.
                  </p>
                )}
                {transcripts.map((turn, index) => {
                  const id = String(turn.turn_id ?? turn.id ?? index);
                  return <TranscriptItem key={id} turn={turn} language={listeningLang} original={original}
                    saved={savedMoments.saved.has(id)} busy={savedMoments.busy.includes(id)}
                    disabled={room.record_session === false}
                    onToggle={() => void savedMoments.toggle(room.room_code, id, savedMoments.saved.has(id))} />;
                })}
              </div>
            </motion.aside>
          )}
        </AnimatePresence>
      </div>

      <footer className="shrink-0 border-t border-border bg-surface-0/95 px-4 py-3 safe-pb sm:px-6">
        <div className="mx-auto flex max-w-5xl flex-col items-center gap-3 md:flex-row md:justify-between">
          <form onSubmit={sendText} className="flex w-full max-w-md items-center gap-2">
            <input
              aria-label="Text message to translate"
              value={text}
              onChange={(event) => setText(event.target.value)}
              maxLength={2000}
              placeholder="Or type a message…"
              className="h-10 min-w-0 flex-1 rounded-xl border border-border bg-surface-1 px-3 text-xs focus:border-accent focus:outline-none"
            />
            <Button
              type="submit"
              variant="secondary"
              size="sm"
              loading={sending}
              disabled={!live.connected || !text.trim()}
              title="Send and translate text (may take longer than live speech)"
            >
              <Send size={15} />
            </Button>
          </form>
          <div className="flex items-center justify-center gap-2">
            <Button
              variant={micMuted ? 'danger' : live.isSpeaking ? 'primary' : 'secondary'}
              size="sm"
              onClick={() => setMicMuted(!micMuted)}
              aria-pressed={!micMuted}
              leftIcon={micMuted ? <MicOff size={17} /> : <Mic size={17} />}
            >
              {micMuted ? 'Unmute' : live.isSpeaking ? 'Speaking' : 'Mic on'}
            </Button>
            <Button
              variant={audioMuted ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => setAudioMuted(!audioMuted)}
              aria-pressed={!audioMuted}
              title={audioMuted ? 'Enable translated audio' : 'Mute translated audio'}
            >
              {audioMuted ? <VolumeX size={17} /> : <Volume2 size={17} />}
            </Button>
            <div className="mx-1 h-6 w-px bg-border" />
            <Button
              variant="danger"
              size="sm"
              onClick={() => setModal(isHost ? 'end' : 'leave')}
              leftIcon={<PhoneOff size={16} />}
            >
              {isHost ? 'End conversation' : 'Leave'}
            </Button>
          </div>
        </div>
        <p className="mt-2 hidden text-center text-2xs text-ink-faint sm:block">
          Headphones help prevent echo. Your microphone stays available while translations play.
        </p>
      </footer>

      <AnimatePresence>
        {modal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.97 }}
              animate={{ opacity: 1, scale: 1 }}
              className="w-full max-w-sm rounded-2xl border border-border bg-surface-1 p-6 shadow-elevation-3"
            >
              <h2 className="text-base font-semibold">
                {modal === 'end'
                  ? 'End this conversation for everyone?'
                  : modal === 'clear'
                    ? 'Clear the room transcript?'
                    : 'Leave this conversation?'}
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-ink-muted">
                {modal === 'end'
                  ? 'Everyone will leave the live room. Saved transcripts and session notes will be available afterward.'
                  : modal === 'clear'
                    ? 'This removes the transcript, recaps and saved moments for everyone. Saving resumes with the next new phrase.'
                    : 'Your microphone and audio will disconnect. You can rejoin while the room is active.'}
              </p>
              <div className="mt-6 flex justify-end gap-2">
                <Button variant="secondary" size="sm" onClick={() => setModal(null)} disabled={busy}>
                  Cancel
                </Button>
                <Button variant="danger" size="sm" onClick={() => void confirm()} loading={busy}>
                  {modal === 'end'
                    ? 'End for everyone'
                    : modal === 'clear'
                      ? 'Clear transcript'
                      : 'Leave room'}
                </Button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
