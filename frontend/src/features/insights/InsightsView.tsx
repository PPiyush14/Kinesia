import { ArrowLeft, Check, Copy, MessageCircle } from 'lucide-react';
import { useState } from 'react';
import type { Room, SessionInsights } from '@/types';
import { Button } from '@/components/ui/Button';
import { ConversationReview } from './ConversationReview';

interface InsightsViewProps {
  room: Room;
  insights: SessionInsights | null;
  language?: string;
  onReturnToWorkspace: () => void;
  onReturnToHome?: () => void;
  onToast: (message: string, tone?: 'success' | 'error' | 'info') => void;
}

export function InsightsView({
  room,
  language = 'en',
  onReturnToWorkspace,
  onToast,
}: InsightsViewProps) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8 sm:py-12">
      <button
        onClick={onReturnToWorkspace}
        className="mb-8 inline-flex items-center gap-2 text-xs text-ink-muted hover:text-ink-primary"
      >
        <ArrowLeft size={14} /> Back to conversations
      </button>
      <header className="mb-8 flex flex-wrap items-start justify-between gap-5">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-accent/20 bg-accent/5 px-3 py-1 text-2xs font-medium text-accent">
            <Check size={12} /> Conversation ended
          </div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-ink-primary sm:text-3xl">
            {room.title}
          </h1>
          <p className="mt-3 max-w-lg text-sm leading-relaxed text-ink-muted">
            {room.record_session === false
              ? 'Saving was off for this conversation.'
              : 'Revisit the conversation, save something useful, or create a recap in your language.'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            leftIcon={<Copy size={13} />}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(room.room_code);
                setCopied(true);
              } catch {
                onToast('Could not copy the room code.', 'error');
              }
            }}
          >
            {copied ? 'Copied' : room.room_code}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onReturnToWorkspace}
            leftIcon={<MessageCircle size={14} />}
          >
            New conversation
          </Button>
        </div>
      </header>
      <div className="rounded-3xl border border-border bg-surface-0/70 p-4 sm:p-7">
        <ConversationReview
          room={{ ...room, is_active: false, status: 'ended' }}
          language={language}
          onToast={onToast}
        />
      </div>
    </div>
  );
}
