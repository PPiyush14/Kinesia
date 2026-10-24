import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Plus, LogIn, RefreshCw, Clock, Users, ArrowRight, ArrowLeft } from 'lucide-react';
import { roomService } from '@/services/api';
import type { Room } from '@/types';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import { Card, CardContent, CardHeader } from '@/components/ui/Card';
import { formatRelativeTime } from '@/lib/utils';

interface WorkspaceViewProps {
  defaultJoinCode?: string;
  onSelectRoom: (room: Room, isExistingLive: boolean) => void;
  onToast: (msg: string, tone?: 'success' | 'error' | 'info') => void;
  onBackToHome?: () => void;
}

export function WorkspaceView({ defaultJoinCode, onSelectRoom, onToast, onBackToHome }: WorkspaceViewProps) {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [loadingRooms, setLoadingRooms] = useState(true);

  const [createTitle, setCreateTitle] = useState('');
  const [createPasscode, setCreatePasscode] = useState('');
  const [recordSession, setRecordSession] = useState(true);
  const [creating, setCreating] = useState(false);

  const [joinCode, setJoinCode] = useState(defaultJoinCode ? defaultJoinCode.toUpperCase() : '');
  const [joinPasscode, setJoinPasscode] = useState('');
  const [joining, setJoining] = useState(false);

  const fetchRooms = async () => {
    setLoadingRooms(true);
    try {
      const data = await roomService.listRooms();
      setRooms(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error(err);
      onToast('Could not load sessions', 'error');
    } finally {
      setLoadingRooms(false);
    }
  };

  useEffect(() => {
    void fetchRooms();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createTitle.trim()) {
      onToast('Give your session a title', 'error');
      return;
    }
    setCreating(true);
    try {
      const room = await roomService.createRoom({
        title: createTitle.trim(),
        passcode: createPasscode.trim() || null,
        record_session: recordSession,
      });
      onToast('Session created');
      onSelectRoom(room, false);
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to create session', 'error');
    } finally {
      setCreating(false);
    }
  };

  const handleJoin = async (e: React.FormEvent) => {
    e.preventDefault();
    const code = joinCode.trim().toUpperCase();
    if (!code) {
      onToast('Enter a room code', 'error');
      return;
    }
    setJoining(true);
    try {
      const room = await roomService.getRoom(code);
      if (room.passcode_required) {
        const valid = await roomService.verifyPasscode(code, joinPasscode || null);
        if (!valid) {
          onToast('Invalid passcode', 'error');
          setJoining(false);
          return;
        }
      }
      onSelectRoom(room, true);
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Room not found', 'error');
    } finally {
      setJoining(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-8 sm:py-10 space-y-8">
      {onBackToHome && (
        <button
          type="button"
          onClick={onBackToHome}
          className="inline-flex items-center gap-1.5 text-xs text-ink-muted hover:text-ink-primary transition-colors"
        >
          <ArrowLeft size={14} /> Back to Home
        </button>
      )}
      {/* Hero */}
      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        className="relative overflow-hidden rounded-3xl border border-border bg-surface-1 p-8 sm:p-10"
      >
        <div className="absolute inset-0 bg-gradient-to-br from-accent/10 via-transparent to-accent-cyan/8 pointer-events-none" />
        <div className="relative z-10 max-w-xl space-y-3">
          <Badge tone="accent" dot>
            Conversations
          </Badge>
          <h1 className="font-display text-title sm:text-[2rem] text-ink-primary tracking-tight">
            Start a conversation that everyone understands
          </h1>
          <p className="text-sm sm:text-base text-ink-secondary leading-relaxed">
            Create a room or join someone you know. Choose your languages, check your audio,
            and bring everyone into the conversation.
          </p>
        </div>
      </motion.section>

      {/* Create + Join */}
      <div className="grid lg:grid-cols-2 gap-5">
        <Card elevated>
          <CardHeader>
            <div className="flex items-center gap-2.5">
              <div className="h-9 w-9 rounded-xl bg-accent-soft flex items-center justify-center">
                <Plus size={18} className="text-accent" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-ink-primary">Create session</h2>
                <p className="text-2xs text-ink-muted">Host a new multilingual room</p>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-3.5">
              <Input
                label="Session title"
                value={createTitle}
                onChange={(e) => setCreateTitle(e.target.value)}
                placeholder="E.g. Open discussion, podcast, or chat"
              />
              <Input
                label="Passcode (optional)"
                type="password"
                value={createPasscode}
                onChange={(e) => setCreatePasscode(e.target.value)}
                placeholder="Leave blank for open access"
              />
              <label className="flex items-center gap-2.5 pt-1 cursor-pointer select-none text-xs text-ink-secondary">
                <input
                  type="checkbox"
                  checked={recordSession}
                  onChange={(e) => setRecordSession(e.target.checked)}
                  className="rounded border-border text-accent focus:ring-accent/30"
                />
                <span>Save transcript and generate conversation summary</span>
              </label>
              <Button type="submit" loading={creating} className="w-full mt-3" rightIcon={<ArrowRight size={16} />}>
                Create and enter lobby
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card elevated>
          <CardHeader>
            <div className="flex items-center gap-2.5">
              <div className="h-9 w-9 rounded-xl bg-accent-cyan-soft flex items-center justify-center">
                <LogIn size={18} className="text-accent-cyan" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-ink-primary">Join session</h2>
                <p className="text-2xs text-ink-muted">Enter using a room code (e.g. kin-123-abc)</p>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleJoin} className="space-y-3.5">
              <Input
                label="Room code"
                value={joinCode}
                onChange={(e) => setJoinCode(e.target.value.trim().toLowerCase())}
                placeholder="e.g. kin-123-abc"
                className="font-mono text-center text-base tracking-wider"
                maxLength={24}
              />
              <Input
                label="Passcode (if required)"
                type="password"
                value={joinPasscode}
                onChange={(e) => setJoinPasscode(e.target.value)}
                placeholder="Passcode"
              />
              <Button
                type="submit"
                variant="secondary"
                loading={joining}
                className="w-full mt-3"
                rightIcon={<ArrowRight size={16} />}
              >
                Join room
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>

      {/* Recent rooms list */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-ink-primary">Active and recent rooms</h2>
            <Badge tone="default">{rooms.length}</Badge>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={fetchRooms}
            leftIcon={<RefreshCw size={13} className={loadingRooms ? 'animate-spin' : ''} />}
          >
            Refresh
          </Button>
        </div>

        {loadingRooms && rooms.length === 0 ? (
          <div className="py-12 text-center text-xs text-ink-muted">Loading rooms...</div>
        ) : rooms.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border p-10 text-center space-y-2">
            <p className="text-xs text-ink-muted">No rooms created yet. Start one above!</p>
          </div>
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {rooms.map((room) => {
              const live = room.status === 'live';
              return (
                <Card
                  key={room.room_code}
                  interactive
                  onClick={() => onSelectRoom(room, live)}
                  className="p-4 space-y-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="text-sm font-medium text-ink-primary truncate">
                      {room.title || 'Untitled session'}
                    </h3>
                    <Badge tone={live ? 'accent' : 'default'}>
                      {live ? 'Live' : room.status || 'Active'}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-3 text-2xs text-ink-muted">
                    <span className="font-mono tracking-wider font-semibold text-ink-secondary">
                      {room.room_code}
                    </span>
                    {room.participant_count != null && (
                      <span className="flex items-center gap-1">
                        <Users size={12} /> {room.participant_count}
                      </span>
                    )}
                    {room.created_at && (
                      <span className="flex items-center gap-1">
                        <Clock size={12} /> {formatRelativeTime(room.created_at)}
                      </span>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
