import { useEffect, useState } from 'react';
import { Bookmark, Check, Download, FileText, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { roomService } from '@/services/api';
import { useSavedMoments } from '@/hooks/useSavedMoments';
import type { Room, TranscriptTurn } from '@/types';
import { cn } from '@/lib/utils';
import { RecapPanel } from './RecapPanel';
import { TranscriptItem } from './TranscriptItem';

export function ConversationReview({
  room,
  language = 'en',
  onToast,
  onMomentsChanged,
}: {
  room: Room;
  language?: string;
  onToast: (message: string, tone?: 'success' | 'error' | 'info') => void;
  onMomentsChanged?: () => void;
}) {
  const [tab, setTab] = useState('recap');
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [original, setOriginal] = useState(true);
  const [source, setSource] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const moments = useSavedMoments(room.room_code, (message) =>
    onToast(message, 'error'),
  );
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setTurns([]);
    setError(null);
    setSource(null);
    setTab(room.is_active || room.status === 'live' ? 'transcript' : 'recap');
    roomService
      .getTranscripts(room.room_code)
      .then((data) => {
        if (!disposed) setTurns(Array.isArray(data) ? data : []);
      })
      .catch((reason) => {
        if (!disposed)
          setError(
            reason instanceof Error
              ? reason.message
              : 'The transcript could not be loaded.',
          );
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [room.room_code, retry]);
  useEffect(() => {
    if (tab === 'transcript' && source)
      document
        .getElementById(`turn-${source}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [source, tab]);
  const jump = (id: string) => {
    if (!turns.some((turn) => String(turn.id ?? turn.turn_id) === id)) {
      onToast(
        'That source is no longer available in the saved transcript.',
        'info',
      );
      return;
    }
    setSource(id);
    setTab('transcript');
  };
  const exportText = () => {
    const text =
      `${room.title}\n${room.room_code}\n\n` +
      turns
        .map(
          (turn) =>
            `${turn.speaker_name}: ${turn.original_text}\n${turn.translations?.[language] || ''}`,
        )
        .join('\n\n');
    const url = URL.createObjectURL(
      new Blob([text], { type: 'text/plain;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = `kinesia-${room.room_code}.txt`;
    link.click();
    URL.revokeObjectURL(url);
  };
  const rows =
    tab === 'moments'
      ? turns.filter((turn) =>
          moments.saved.has(String(turn.id ?? turn.turn_id)),
        )
      : turns;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        <div
          className="flex flex-wrap gap-1"
          role="tablist"
          aria-label="Conversation content"
        >
          {[
            ['recap', 'Recap', Sparkles],
            ['transcript', 'Transcript', FileText],
            ['moments', 'Saved moments', Bookmark],
          ].map(([key, label, Icon]) => {
            const TabIcon = Icon as typeof FileText;
            return (
              <button
                role="tab"
                aria-selected={tab === key}
                key={String(key)}
                onClick={() => setTab(String(key))}
                className={cn(
                  'inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs transition-colors',
                  tab === key
                    ? 'bg-accent/10 font-semibold text-accent'
                    : 'text-ink-muted hover:bg-surface-2 hover:text-ink-primary',
                )}
              >
                <TabIcon size={13} />
                {String(label)}
              </button>
            );
          })}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={exportText}
          disabled={!turns.length}
          leftIcon={<Download size={13} />}
        >
          Export transcript
        </Button>
      </div>
      {tab === 'recap' ? (
        <RecapPanel room={room} language={language} onSource={jump} />
      ) : (
        <>
          <div className="flex items-center justify-between gap-3 text-xs text-ink-muted">
            <span>
              {tab === 'moments'
                ? 'Your bookmarks. Only you can see them.'
                : `${turns.length} saved conversation turns`}
            </span>
            <button
              className="flex items-center gap-1 hover:text-ink-primary"
              aria-pressed={original}
              onClick={() => setOriginal(!original)}
            >
              Show original text {original && <Check size={12} />}
            </button>
          </div>
          {error && (
            <p role="alert" className="text-xs text-warn">
              {error}{' '}
              <button
                className="underline"
                onClick={() => setRetry((value) => value + 1)}
              >
                Retry
              </button>
            </p>
          )}
          {loading ? (
            <p
              role="status"
              className="py-8 text-center text-sm text-ink-muted"
            >
              Loading the conversation…
            </p>
          ) : rows.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm leading-relaxed text-ink-muted">
              {room.record_session === false
                ? 'This conversation did not save a transcript.'
                : tab === 'moments'
                  ? 'Save a line from the transcript to keep it here. Saved moments are private to you.'
                  : 'There are no saved transcript turns.'}
            </div>
          ) : (
            <div className="space-y-3">
              {rows.map((turn) => {
                const id = String(turn.id ?? turn.turn_id);
                return (
                  <TranscriptItem
                    key={id}
                    turn={turn}
                    language={language}
                    original={original}
                    saved={moments.saved.has(id)}
                    busy={moments.busy.includes(id)}
                    highlighted={source === id}
                    onToggle={() =>
                      void moments
                        .toggle(room.room_code, id, moments.saved.has(id))
                        .then(onMomentsChanged)
                    }
                  />
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
