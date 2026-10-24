import { useEffect, useState } from 'react';
import {
  ArrowRight,
  Clock,
  Headphones,
  Languages,
  MessageCircle,
  Plus,
  ArrowUpRight,
} from 'lucide-react';
import type { Room, User } from '@/types';
import { langLabel } from '@/types';
import { roomService } from '@/services/api';
import { Button } from '@/components/ui/Button';
import { formatRelativeTime } from '@/lib/utils';

interface WelcomeViewProps {
  user: User;
  onStartConversation: () => void;
  onJoinWithCode: () => void;
  onOpenWorkspace: () => void;
  onOpenProfile: () => void;
  onResumeConversation?: (room: Room, live: boolean) => void;
}

export function WelcomeView({
  user,
  onStartConversation,
  onJoinWithCode,
  onOpenWorkspace,
  onOpenProfile,
  onResumeConversation,
}: WelcomeViewProps) {
  const [recent, setRecent] = useState<Room[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let disposed = false;
    roomService
      .listRooms()
      .then((rooms) => {
        if (!disposed) setRecent(Array.isArray(rooms) ? rooms.slice(0, 4) : []);
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, []);
  return (
    <div className="mx-auto max-w-6xl space-y-9 px-4 py-8 sm:px-8 sm:py-12">
      <section className="relative overflow-hidden rounded-[28px] border border-border bg-surface-1 p-6 sm:p-10">
        <div className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full bg-accent/10 blur-3xl" />
        <div className="relative grid items-center gap-9 lg:grid-cols-[1fr_280px]">
          <div>
            <p className="mb-5 text-xs text-ink-muted">
              Welcome back,{' '}
              <span className="font-medium text-ink-secondary">
                {user.display_name || user.username}
              </span>
            </p>
            <h1 className="max-w-2xl font-display text-3xl font-semibold leading-tight tracking-tight text-ink-primary sm:text-5xl">
              Your words.
              <br />
              <span className="text-accent">Their language.</span>
            </h1>
            <p className="mt-5 max-w-lg text-sm leading-relaxed text-ink-muted sm:text-base">
              Bring people into the same conversation. Speak your language, hear
              theirs in yours, and keep the moments that matter.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <Button
                onClick={onStartConversation}
                size="lg"
                leftIcon={<Plus size={17} />}
              >
                Start a conversation
              </Button>
              <Button
                onClick={onJoinWithCode}
                size="lg"
                variant="secondary"
                rightIcon={<ArrowRight size={16} />}
              >
                Join with a code
              </Button>
            </div>
          </div>
          <div className="rounded-2xl border border-border bg-canvas/60 p-5">
            <div className="mb-5 flex items-center gap-2 text-2xs font-semibold uppercase tracking-wider text-ink-muted">
              <Languages size={13} /> Your conversation setup
            </div>
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-ink-muted">You speak</span>
                <span className="text-sm font-medium text-ink-primary">
                  {langLabel(user.default_input_lang || 'en')}
                </span>
              </div>
              <div className="h-px bg-border" />
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-2 text-xs text-ink-muted">
                  <Headphones size={13} /> You hear
                </span>
                <span className="text-sm font-medium text-accent">
                  {langLabel(user.default_output_lang || 'hi')}
                </span>
              </div>
            </div>
            <button
              onClick={onOpenProfile}
              className="mt-6 inline-flex items-center gap-1 text-xs text-ink-muted hover:text-ink-primary"
            >
              Language and audio settings <ArrowUpRight size={12} />
            </button>
            <p className="mt-4 text-2xs leading-relaxed text-ink-faint">
              You can change these languages before joining or during a
              conversation.
            </p>
          </div>
        </div>
      </section>

      <section>
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-lg font-semibold text-ink-primary">
              Pick up where you left off
            </h2>
            <p className="mt-1 text-xs text-ink-muted">
              Your recent conversations.
            </p>
          </div>
          <button
            onClick={onOpenWorkspace}
            className="inline-flex items-center gap-1 text-xs text-accent"
          >
            All conversations <ArrowRight size={13} />
          </button>
        </div>
        {loading ? (
          <p
            role="status"
            className="rounded-2xl border border-border p-7 text-sm text-ink-muted"
          >
            Loading your conversations…
          </p>
        ) : failed ? (
          <p
            role="alert"
            className="rounded-2xl border border-border p-6 text-sm text-ink-muted"
          >
            Your recent conversations could not be loaded.{' '}
            <button onClick={onOpenWorkspace} className="text-accent">
              Open conversations
            </button>
          </p>
        ) : recent.length === 0 ? (
          <div className="flex flex-wrap items-center gap-5 rounded-2xl border border-dashed border-border p-7">
            <div className="rounded-2xl bg-surface-2 p-4 text-accent">
              <MessageCircle size={23} />
            </div>
            <div>
              <p className="text-sm font-medium text-ink-secondary">
                Your first conversation starts here.
              </p>
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                Create a room, share its invite, and choose the language you
                want to hear.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {recent.map((room) => (
              <button
                key={room.room_code}
                onClick={() =>
                  onResumeConversation
                    ? onResumeConversation(room, room.is_active === true)
                    : onOpenWorkspace()
                }
                className="group rounded-2xl border border-border bg-surface-1 p-5 text-left transition-colors hover:border-accent/30 hover:bg-surface-2"
              >
                <div className="mb-4 flex items-center justify-between gap-3 text-2xs text-ink-muted">
                  <span className="inline-flex items-center gap-2">
                    <span
                      className={
                        room.is_active
                          ? 'h-1.5 w-1.5 rounded-full bg-accent'
                          : 'h-1.5 w-1.5 rounded-full bg-ink-faint'
                      }
                    />
                    {room.is_active ? 'Room open' : 'Ended'}
                  </span>
                  <ArrowUpRight size={14} className="group-hover:text-accent" />
                </div>
                <h3 className="truncate text-sm font-semibold text-ink-primary">
                  {room.title}
                </h3>
                <p className="mt-3 inline-flex items-center gap-2 text-2xs text-ink-muted">
                  <Clock size={11} />
                  {room.created_at
                    ? formatRelativeTime(room.created_at)
                    : room.room_code}
                  {room.is_host ? ' · You host' : ''}
                </p>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
