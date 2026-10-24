import { Bookmark, BookmarkCheck } from 'lucide-react';
import type { TranscriptTurn } from '@/types';
import { langShort } from '@/types';
import { cn } from '@/lib/utils';

export function TranscriptItem({
  turn,
  language,
  original = true,
  saved,
  busy,
  onToggle,
  disabled,
  highlighted,
}: {
  turn: TranscriptTurn;
  language: string;
  original?: boolean;
  saved?: boolean;
  busy?: boolean;
  disabled?: boolean;
  highlighted?: boolean;
  onToggle?: () => void;
}) {
  const localized =
    turn.translations?.[language] ||
    ((!turn.listening_lang || turn.listening_lang === language) &&
      turn.localized_text) ||
    turn.original_text;
  return (
    <article
      id={`turn-${turn.turn_id ?? turn.id}`}
      className={cn(
        'scroll-mt-6 rounded-2xl border bg-surface-1 p-4 transition-colors',
        highlighted ? 'border-accent bg-accent/5' : 'border-border',
      )}
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-2xs">
          <span className="truncate font-semibold text-ink-secondary">
            {turn.speaker_name}
          </span>
          <span className="text-ink-faint">{langShort(turn.source_lang)}</span>
        </div>
        {onToggle && (
          <button
            onClick={onToggle}
            disabled={busy || disabled}
            aria-pressed={Boolean(saved)}
            aria-label={saved ? 'Remove saved moment' : 'Save moment'}
            title={
              disabled
                ? 'Moments require a saved transcript'
                : saved
                  ? 'Saved privately to your moments'
                  : 'Save this moment for yourself'
            }
            className={cn(
              'rounded-md p-1.5 transition-colors disabled:opacity-30',
              saved
                ? 'bg-accent/10 text-accent'
                : 'text-ink-muted hover:bg-surface-3 hover:text-accent',
            )}
          >
            {saved ? <BookmarkCheck size={15} /> : <Bookmark size={15} />}
          </button>
        )}
      </div>
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-primary">
        {localized}
      </p>
      {original && localized !== turn.original_text && (
        <p className="mt-3 border-t border-border-subtle pt-3 text-xs leading-relaxed text-ink-muted">
          {turn.original_text}
        </p>
      )}
      {turn.confidence != null && turn.confidence < 0.75 && (
        <p className="mt-2 text-2xs text-warn">
          Recognition was uncertain here. Confirm important details.
        </p>
      )}
    </article>
  );
}
