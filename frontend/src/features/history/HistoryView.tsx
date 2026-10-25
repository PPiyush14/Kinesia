import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowUpRight,
  Bookmark,
  Clock,
  History,
  MessageCircle,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { roomService } from '@/services/api';
import type { Room, SavedMoment } from '@/types';
import { SUPPORTED_LANGUAGES } from '@/types';
import { Button } from '@/components/ui/Button';
import { useSavedMoments } from '@/hooks/useSavedMoments';
import { cn, formatRelativeTime } from '@/lib/utils';
import { ConversationReview } from '@/features/insights/ConversationReview';

interface HistoryViewProps {
  onSelectRoom: (room: Room, isExistingLive: boolean) => void;
  onToast: (message: string, tone?: 'success' | 'error' | 'info') => void;
  onBackToHome?: () => void;
  language?: string;
  currentUserId?: string | number;
  initialRoomCode?: string;
}

export function HistoryView({
  onSelectRoom,
  onToast,
  onBackToHome,
  language = 'en',
  currentUserId,
  initialRoomCode,
}: HistoryViewProps) {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Room | null>(null);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<'conversations' | 'moments'>('conversations');
  const [output, setOutput] = useState(language);
  const [modal, setModal] = useState<'history' | 'transcript' | null>(null);
  const [busy, setBusy] = useState(false);
  const [reviewRevision, setReviewRevision] = useState(0);
  const requests = useRef(0);
  const selectionRequests = useRef(0);
  const initialApplied = useRef(false);
  const selectRoom = useCallback((room: Room | null) => {
    initialApplied.current = true;
    selectionRequests.current++;
    setSelected(room);
  }, []);
  useEffect(() => {
    if (!initialApplied.current && initialRoomCode && rooms.length) {
      const room = rooms.find((entry) => entry.room_code === initialRoomCode);
      if (room) {
        selectRoom(room);
      }
    }
  }, [rooms, initialRoomCode, selectRoom]);
  const moments = useSavedMoments(undefined, (message) =>
    onToast(message, 'error'),
  );

  const refresh = async () => {
    const generation = ++requests.current;
    setLoading(true);
    setError(null);
    moments.refresh();
    try {
      const data = await roomService.listRooms();
      if (generation === requests.current)
        setRooms(Array.isArray(data) ? data : []);
    } catch (reason) {
      if (generation === requests.current)
        setError(
          reason instanceof Error
            ? reason.message
            : 'History could not be loaded.',
        );
    } finally {
      if (generation === requests.current) setLoading(false);
    }
  };
  useEffect(() => {
    void refresh();
    return () => {
      requests.current++;
      selectionRequests.current++;
    };
  }, []);

  const remove = async (room: Room) => {
    setBusy(true);
    requests.current++;
    selectionRequests.current++;
    try {
      await roomService.deleteRoom(room.room_code);
      setRooms((previous) =>
        previous.filter((entry) => entry.room_code !== room.room_code),
      );
      setSelected((previous) =>
        previous?.room_code === room.room_code ? null : previous,
      );
      onToast('Removed from your history. Other participants keep their copy.');
    } catch (reason) {
      onToast(
        reason instanceof Error
          ? reason.message
          : 'Could not remove this history entry.',
        'error',
      );
    } finally {
      setBusy(false);
      setLoading(false);
    }
  };
  const confirm = async () => {
    setBusy(true);
    requests.current++;
    selectionRequests.current++;
    try {
      if (modal === 'history') {
        await roomService.clearAllRooms();
        setRooms([]);
        setSelected(null);
        onToast(
          'Your history entries were removed. Saved moments and shared conversations remain available.',
        );
      } else if (selected) {
        await roomService.clearTranscripts(selected.room_code);
        setReviewRevision((value) => value + 1);
        moments.refresh();
        onToast('Shared transcript, notes and saved moments were cleared.');
      }
      setModal(null);
    } catch (reason) {
      onToast(
        reason instanceof Error
          ? reason.message
          : 'This action could not be completed.',
        'error',
      );
    } finally {
      setBusy(false);
      setLoading(false);
    }
  };
  const openMoment = async (moment: SavedMoment) => {
    initialApplied.current = true;
    const generation = ++selectionRequests.current;
    try {
      const room = await roomService.getRoom(moment.room_code);
      if (generation === selectionRequests.current) setSelected(room);
    } catch (reason) {
      if (generation === selectionRequests.current)
        onToast(
          reason instanceof Error
            ? reason.message
            : 'Conversation unavailable.',
          'error',
        );
    }
  };
  const needle = query.toLocaleLowerCase().trim();
  const filtered = rooms.filter((room) =>
    (room.title + ' ' + room.room_code).toLocaleLowerCase().includes(needle),
  );
  const saved = moments.moments.filter((moment) =>
    (
      moment.original_text +
      ' ' +
      moment.title +
      ' ' +
      Object.values(moment.translations).join(' ')
    )
      .toLocaleLowerCase()
      .includes(needle),
  );
  const host =
    selected?.is_host ||
    (selected?.host_id != null &&
      String(selected.host_id) === String(currentUserId));

  return (
    <div className="mx-auto max-w-7xl px-4 py-7 sm:px-8 sm:py-10">
      <header className="mb-7 flex flex-wrap items-start justify-between gap-5">
        <div>
          {onBackToHome && (
            <button
              onClick={onBackToHome}
              className="mb-4 inline-flex items-center gap-1 text-xs text-ink-muted hover:text-ink-primary"
            >
              <ArrowLeft size={13} /> Home
            </button>
          )}
          <p className="mb-2 text-2xs font-semibold uppercase tracking-[0.2em] text-accent">
            Your space
          </p>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-ink-primary sm:text-3xl">
            Good conversations stay with you.
          </h1>
          <p className="mt-3 text-sm text-ink-muted">
            Revisit a conversation. Find a detail. Keep a moment.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void refresh()}
            disabled={busy || loading}
            leftIcon={
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            }
          >
            Refresh history
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setModal('history')}
            disabled={busy || rooms.length === 0}
            leftIcon={<Trash2 size={13} />}
          >
            Clear my history
          </Button>
        </div>
      </header>

      <div className="grid items-start gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside
          className={cn('min-w-0 space-y-4', selected && 'hidden lg:block')}
        >
          <div className="flex rounded-xl border border-border bg-surface-1 p-1">
            <button
              className={cn(
                'flex flex-1 items-center justify-center gap-2 rounded-lg p-2 text-xs',
                tab === 'conversations'
                  ? 'bg-surface-3 text-ink-primary'
                  : 'text-ink-muted',
              )}
              onClick={() => setTab('conversations')}
            >
              <History size={13} /> Conversations
            </button>
            <button
              className={cn(
                'flex flex-1 items-center justify-center gap-2 rounded-lg p-2 text-xs',
                tab === 'moments'
                  ? 'bg-surface-3 text-ink-primary'
                  : 'text-ink-muted',
              )}
              onClick={() => setTab('moments')}
            >
              <Bookmark size={13} /> Saved moments
            </button>
          </div>
          <label className="flex items-center gap-2 rounded-xl border border-border bg-surface-1 px-3 text-ink-muted">
            <Search size={14} />
            <input
              aria-label="Search history and saved moments"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find a conversation or a phrase"
              className="min-w-0 flex-1 bg-transparent py-3 text-xs text-ink-primary outline-none placeholder:text-ink-faint"
            />
          </label>
          {error && (
            <p role="alert" className="text-xs text-warn">
              {error}
            </p>
          )}
          {tab === 'conversations' ? (
            loading ? (
              <p
                role="status"
                className="p-6 text-center text-xs text-ink-muted"
              >
                Loading conversations…
              </p>
            ) : filtered.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-border p-7 text-center text-ink-muted">
                <MessageCircle
                  size={24}
                  className="mx-auto mb-3 text-ink-faint"
                />
                <p className="text-sm">No sessions found</p>
                <p className="mt-2 text-xs">
                  {query
                    ? 'Try another title or room code.'
                    : 'Your conversations will appear here.'}
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {filtered.map((room) => (
                  <div
                    key={room.room_code}
                    className={cn(
                      'group flex items-start gap-2 rounded-2xl border p-4 transition-colors',
                      selected?.room_code === room.room_code
                        ? 'border-accent/40 bg-accent/5'
                        : 'border-border bg-surface-1 hover:bg-surface-2',
                    )}
                  >
                    <button
                      onClick={() => selectRoom(room)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className="mb-2 flex items-center gap-2 text-2xs">
                        <span
                          className={cn(
                            'h-1.5 w-1.5 rounded-full',
                            room.is_active ? 'bg-accent' : 'bg-ink-faint',
                          )}
                        />
                        <span className="text-ink-muted">
                          {room.is_active ? 'Room open' : 'Ended'}
                          {room.is_host ? ' · You host' : ''}
                        </span>
                      </div>
                      <h3 className="truncate text-sm font-semibold text-ink-primary">
                        {room.title}
                      </h3>
                      <div className="mt-2 flex items-center gap-2 text-2xs text-ink-muted">
                        <Clock size={11} />
                        {room.created_at
                          ? formatRelativeTime(room.created_at)
                          : room.room_code}
                      </div>
                    </button>
                    <button
                      aria-label={'Remove ' + room.title + ' from my history'}
                      title="Remove this session from my history"
                      className="rounded-md p-1 text-ink-faint hover:bg-danger/10 hover:text-danger"
                      disabled={busy}
                      onClick={() => void remove(room)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )
          ) : moments.loading ? (
            <p role="status" className="p-6 text-xs text-ink-muted">
              Loading your saved moments…
            </p>
          ) : saved.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-border p-7 text-center text-sm leading-relaxed text-ink-muted">
              Save a line from a conversation to find it here. Your moments are
              private.
            </div>
          ) : (
            <div className="space-y-3">
              {saved.map((moment) => (
                <article
                  key={moment.turn_id}
                  className="rounded-2xl border border-border bg-surface-1 p-4"
                >
                  <p className="mb-2 text-2xs font-medium text-accent">
                    {moment.speaker_name} · {moment.title}
                  </p>
                  <p className="text-sm leading-relaxed text-ink-secondary">
                    {moment.translations[output] || moment.original_text}
                  </p>
                  <div className="mt-3 flex items-center justify-between">
                    <button
                      className="inline-flex items-center gap-1 text-2xs text-accent"
                      onClick={() => void openMoment(moment)}
                    >
                      Open conversation <ArrowUpRight size={12} />
                    </button>
                    <button
                      aria-label="Remove saved moment"
                      disabled={moments.busy.includes(moment.turn_id)}
                      className="text-ink-muted hover:text-danger"
                      onClick={() =>
                        void moments.toggle(
                          moment.room_code,
                          moment.turn_id,
                          true,
                        )
                      }
                    >
                      <X size={13} />
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </aside>

        <main className="min-w-0">
          {selected ? (
            <section className="rounded-3xl border border-border bg-surface-0/60 p-4 sm:p-6">
              <button
                onClick={() => selectRoom(null)}
                className="mb-5 inline-flex items-center gap-1 text-xs text-ink-muted lg:hidden"
              >
                <ArrowLeft size={13} /> All conversations
              </button>
              <header className="mb-6 space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="mb-2 font-mono text-2xs text-ink-muted">
                      {selected.room_code}
                    </p>
                    <h2 className="font-display text-xl font-semibold text-ink-primary">
                      {selected.title}
                    </h2>
                  </div>
                  <button
                    aria-label="Close conversation details"
                    className="p-1 text-ink-muted"
                    onClick={() => selectRoom(null)}
                  >
                    <X size={17} />
                  </button>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {(selected.is_active || selected.status === 'live') && (
                    <Button
                      size="sm"
                      onClick={() => onSelectRoom(selected, true)}
                      leftIcon={<MessageCircle size={13} />}
                    >
                      Re-join
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    title="Remove from your history; other participants keep their copy"
                    onClick={() => void remove(selected)}
                  >
                    Remove from my history
                  </Button>
                  {host && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      className="text-danger"
                      onClick={() => setModal('transcript')}
                    >
                      Clear shared transcript
                    </Button>
                  )}
                  <select
                    aria-label="Review language"
                    value={output}
                    onChange={(event) => setOutput(event.target.value)}
                    className="ml-auto rounded-lg border border-border bg-surface-1 px-2 py-1.5 text-xs text-ink-secondary"
                  >
                    {SUPPORTED_LANGUAGES.map((entry) => (
                      <option key={entry.code} value={entry.code}>
                        {entry.name}
                      </option>
                    ))}
                  </select>
                </div>
              </header>
              <ConversationReview
                key={selected.room_code + ':' + reviewRevision}
                room={selected}
                language={output}
                onToast={onToast}
                onMomentsChanged={moments.refresh}
              />
            </section>
          ) : (
            <div className="hidden min-h-[480px] flex-col items-center justify-center rounded-3xl border border-dashed border-border bg-surface-1/30 p-10 text-center lg:flex">
              <div className="mb-5 rounded-2xl border border-border bg-surface-2 p-5">
                <MessageCircle size={28} className="text-accent" />
              </div>
              <h2 className="font-display text-xl text-ink-primary">
                Pick up the thread.
              </h2>
              <p className="mt-3 max-w-sm text-sm leading-relaxed text-ink-muted">
                Choose a conversation for its transcript and recap, or revisit
                something you saved.
              </p>
            </div>
          )}
        </main>
      </div>

      {modal && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="history-confirm-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
        >
          <div className="w-full max-w-md rounded-2xl border border-border bg-surface-1 p-6">
            <h2 id="history-confirm-title" className="text-base font-semibold">
              {modal === 'history'
                ? 'Clear your history?'
                : 'Clear the shared transcript?'}
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-ink-muted">
              {modal === 'history'
                ? 'This removes your history entries only. Other participants keep their copies, and active conversations continue. Your Saved Moments remain available. Rejoining a room adds it back to your history.'
                : 'This removes the transcript, recaps and saved moments for every participant. In an active room, saving resumes with the next new phrase.'}
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => setModal(null)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                variant="danger"
                loading={busy}
                onClick={() => void confirm()}
              >
                {modal === 'history'
                  ? 'Clear my history'
                  : 'Clear for everyone'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
