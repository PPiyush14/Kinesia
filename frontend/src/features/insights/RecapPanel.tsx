import { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Copy,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Sparkles,
} from 'lucide-react';
import { ApiError, roomService } from '@/services/api';
import { Button } from '@/components/ui/Button';
import type { ConversationRecap, RecapPoint, Room } from '@/types';

export function RecapPanel({
  room,
  language,
  onSource,
}: {
  room: Room;
  language: string;
  onSource: (id: string) => void;
}) {
  const [recap, setRecap] = useState<ConversationRecap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [retry, setRetry] = useState(0);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(Date.now());
  const generation = useRef(0);
  useEffect(() => {
    if (!recap?.retry_at) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [recap?.retry_at]);
  useEffect(() => {
    const current = ++generation.current;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setRecap(null);
    setError(null);
    setRequesting(false);
    setCopied(false);
    const refresh = async () => {
      try {
        const result = await roomService.getRecap(room.room_code, language);
        if (disposed || current !== generation.current) return;
        setRecap(result);
        setError(null);
        if (result.status === 'queued' || result.status === 'generating')
          timer = setTimeout(refresh, 1600);
      } catch (reason) {
        if (!disposed) {
          setError(
            reason instanceof Error
              ? reason.message
              : 'The recap could not be loaded.',
          );
          if (!(reason instanceof ApiError && [401, 403, 404].includes(reason.status)))
            timer = setTimeout(refresh, 3200);
        }
      }
    };
    void refresh();
    return () => {
      disposed = true;
      generation.current++;
      if (timer) clearTimeout(timer);
    };
  }, [room.room_code, language, retry]);

  const generate = async () => {
    const current = generation.current;
    setRequesting(true);
    setError(null);
    try {
      await roomService.generateRecap(room.room_code, language);
      if (current !== generation.current) return;
      setRetry((value) => value + 1);
    } catch (reason) {
      if (current === generation.current)
        setError(
          reason instanceof Error
            ? reason.message
            : 'The recap could not be generated.',
        );
    } finally {
      if (current === generation.current) setRequesting(false);
    }
  };
  const waiting =
    requesting || recap?.status === 'queued' || recap?.status === 'generating';
  const ready = recap?.status === 'ready' && recap.result;
  const coolingDown = !!recap?.retry_at && recap.retry_at * 1000 > now;
  const resumeTime = recap?.retry_at
    ? new Date(recap.retry_at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '';
  const disabled =
    room.record_session === false ||
    recap?.conversation_active ||
    recap?.configured === false;
  const markdown = () => {
    if (!ready) return '';
    const point = (entry: RecapPoint) =>
      `${entry.text}\n\n> ${entry.evidence}\nSources: ${entry.source_ids.join(', ')}`;
    return (
      `# ${room.title} — Conversation recap\n\n${point(ready.summary)}\n\n` +
      (
        [
          ['Worth remembering', ready.highlights],
          ['Follow-ups', ready.follow_ups],
          ['Open questions', ready.open_questions],
        ] as [string, RecapPoint[]][]
      )
        .filter(([, entries]) => entries.length)
        .map(
          ([title, entries]) =>
            `## ${title}\n\n${entries.map(point).join('\n\n')}`,
        )
        .join('\n\n') +
      '\n\nAI-generated. Review the cited transcript for important details.\n'
    );
  };
  return (
    <section className="space-y-5" aria-label="Conversation recap">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-accent">
            <Sparkles size={14} /> Conversation recap
          </div>
          <h2 className="font-display text-xl text-ink-primary">
            Keep what mattered.
          </h2>
          <p className="mt-2 max-w-md text-xs leading-relaxed text-ink-muted">
            A concise recap, useful details and any follow-ups you actually
            discussed.
          </p>
        </div>
        {!ready && (
          <Button
            size="sm"
            onClick={() => void generate()}
            loading={waiting}
            disabled={disabled || !recap || coolingDown}
            leftIcon={<Sparkles size={14} />}
          >
            {waiting
              ? coolingDown ? 'Waiting for recap service' : 'Generating recap'
              : coolingDown ? 'Waiting for quota reset'
              : recap?.status === 'failed'
                ? 'Retry recap'
                : 'Generate AI recap'}
          </Button>
        )}
        {ready && (
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="ghost"
              leftIcon={<Copy size={12} />}
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(markdown());
                  setCopied(true);
                } catch {
                  setError('The recap could not be copied.');
                }
              }}
            >
              {copied ? 'Copied' : 'Copy recap'}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              leftIcon={<Download size={12} />}
              onClick={() => {
                const url = URL.createObjectURL(
                  new Blob([markdown()], {
                    type: 'text/markdown;charset=utf-8',
                  }),
                );
                const link = document.createElement('a');
                link.href = url;
                link.download = `kinesia-${room.room_code}-recap.md`;
                link.click();
                URL.revokeObjectURL(url);
              }}
            >
              Export recap
            </Button>
          </div>
        )}
      </div>
      {(error || (!waiting && recap?.error)) && (
        <div
          role="alert"
          className="rounded-xl border border-warn/25 bg-warn/5 p-4 text-xs leading-relaxed text-warn"
        >
          {error || recap?.error}
          {!error && coolingDown && <p className="mt-2">Retry available at {resumeTime}.</p>}
          {error && (
            <button
              className="ml-3 underline"
              onClick={() => setRetry((value) => value + 1)}
            >
              Reload
            </button>
          )}
        </div>
      )}
      {waiting && (
        <div
          role="status"
          className="flex items-center gap-3 rounded-2xl border border-accent/20 bg-accent/5 p-6 text-sm text-ink-secondary"
        >
          <Loader2 size={18} className="animate-spin text-accent" />
          <div>
            {coolingDown ? 'Waiting for the recap service.' : 'Finding the important parts.'}
            <p className="mt-1 text-xs text-ink-muted">
              {coolingDown
                ? `Generation will resume automatically at ${resumeTime}. You can leave this page and come back.`
                : 'You can leave this page and come back. Longer conversations may take a little longer.'}
            </p>
          </div>
        </div>
      )}
      {!ready && !waiting && (
        <div className="rounded-2xl border border-dashed border-border p-6 text-sm leading-relaxed text-ink-muted">
          <FileText className="mb-3 text-ink-faint" size={23} />
          {room.record_session === false
            ? 'This conversation did not save a transcript, so no AI recap is available.'
            : recap?.conversation_active
              ? 'Your recap will be available after the conversation ends. Your transcript is available now.'
              : recap?.configured === false
                ? 'AI recaps are temporarily unavailable. You can still read the complete transcript and save moments.'
                : 'Generate a recap when you want one. The saved transcript is sent to our AI provider for this request.'}
        </div>
      )}
      {ready && (
        <div className="space-y-4">
          <div className="rounded-2xl border border-accent/20 bg-gradient-to-br from-accent/5 to-transparent p-5">
            <Point point={ready.summary} onSource={onSource} />
          </div>
          {(
            [
              ['Worth remembering', ready.highlights],
              ['Follow-ups', ready.follow_ups],
              ['Open questions', ready.open_questions],
            ] as [string, RecapPoint[]][]
          ).map(
            ([title, points]) =>
              points.length > 0 && (
                <section
                  key={title}
                  className="rounded-2xl border border-border bg-surface-1 p-5"
                >
                  <h3 className="mb-4 text-xs font-semibold uppercase tracking-wider text-ink-muted">
                    {title}
                  </h3>
                  <div className="space-y-5">
                    {points.map((point, index) => (
                      <Point key={index} point={point} onSource={onSource} />
                    ))}
                  </div>
                </section>
              ),
          )}
          <div className="flex flex-wrap items-center justify-between gap-3 text-2xs text-ink-muted">
            <span>
              AI-generated. Check the linked transcript before relying on
              important details.
            </span>
            <button
              onClick={() => setRetry((value) => value + 1)}
              className="inline-flex items-center gap-1 hover:text-ink-primary"
            >
              <RefreshCw size={12} /> Refresh
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function Point({
  point,
  onSource,
}: {
  point: RecapPoint;
  onSource: (id: string) => void;
}) {
  return (
    <div>
      <p className="text-sm leading-relaxed text-ink-secondary">{point.text}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {point.source_ids.map((id, index) => (
          <button
            key={id}
            onClick={() => onSource(id)}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-2xs text-accent hover:bg-accent/10"
          >
            Source {index + 1}
            <ArrowUpRight size={11} />
          </button>
        ))}
        <span
          className="max-w-full truncate text-2xs text-ink-faint"
          title={point.evidence}
        >
          “{point.evidence}”
        </span>
      </div>
    </div>
  );
}
