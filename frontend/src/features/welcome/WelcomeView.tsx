import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Languages,
  Sparkles,
  FileCheck2,
  ShieldCheck,
  ArrowRight,
  User,
  Radio,
  Clock,
  Users,
  Zap,
  Globe2,
} from 'lucide-react';
import type { Room, User as UserType } from '@/types';
import { langLabel } from '@/types';
import { authService, roomService } from '@/services/api';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { formatRelativeTime } from '@/lib/utils';

interface WelcomeViewProps {
  user: UserType;
  onStartConversation: () => void;
  onJoinWithCode: () => void;
  onOpenWorkspace: () => void;
  onOpenProfile: () => void;
}

const features = [
  {
    icon: Sparkles,
    color: 'text-accent',
    bg: 'bg-accent/10',
    title: 'Live Speech Translation',
    desc: 'Speak naturally in your language. Everyone hears and reads in theirs — simultaneously.',
  },
  {
    icon: Radio,
    color: 'text-accent-cyan',
    bg: 'bg-accent-cyan/10',
    title: 'Live Captions',
    desc: 'See floating subtitles on your screen in real-time as people speak across languages.',
  },
  {
    icon: FileCheck2,
    color: 'text-warn',
    bg: 'bg-warn/10',
    title: 'Conversation Intelligence',
    desc: 'Review saved transcripts, selected discussion excerpts, and possible commitments after your session.',
  },
  {
    icon: ShieldCheck,
    color: 'text-ink-secondary',
    bg: 'bg-surface-3',
    title: 'Private & Secure',
    desc: 'Protect a room with a passcode. Transcripts and session notes are available to its participants.',
  },
];

export function WelcomeView({
  user: initialUser,
  onStartConversation,
  onJoinWithCode,
  onOpenWorkspace,
  onOpenProfile,
}: WelcomeViewProps) {
  const [currentUser, setCurrentUser] = useState<UserType>(initialUser);
  const [activeRooms, setActiveRooms] = useState<Room[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let mounted = true;
    setLoading(true);

    Promise.allSettled([authService.getMe(), roomService.listRooms()]).then(([userRes, roomsRes]) => {
      if (!mounted) return;
      if (userRes.status === 'fulfilled' && userRes.value) {
        setCurrentUser(userRes.value);
      }
      if (roomsRes.status === 'fulfilled' && Array.isArray(roomsRes.value)) {
        setActiveRooms(roomsRes.value);
      }
      setLoading(false);
    });

    return () => { mounted = false; };
  }, []);

  const speakLang = langLabel(currentUser.default_input_lang || 'en');
  const listenLang = langLabel(currentUser.default_output_lang || 'hi');
  const userInitials = (currentUser.display_name || currentUser.username || 'K')
    .substring(0, 2).toUpperCase();
  const firstName = (currentUser.display_name || currentUser.username || 'there').split(' ')[0];

  const liveRooms = activeRooms.filter(r => Boolean(r.is_active) || r.status === 'live');
  const recentRooms = activeRooms.slice(0, 3);

  return (
    <div className="relative min-h-[calc(100vh-4rem)] overflow-hidden">
      {/* Ambient background lighting */}
      <div className="absolute top-0 left-1/4 w-[600px] h-[500px] rounded-full bg-accent/8 blur-[120px] pointer-events-none -z-10" />
      <div className="absolute top-20 right-0 w-[400px] h-[400px] rounded-full bg-accent-cyan/6 blur-[100px] pointer-events-none -z-10" />
      <div className="absolute bottom-0 left-0 w-[500px] h-[300px] rounded-full bg-accent/5 blur-[100px] pointer-events-none -z-10" />

      <div className="mx-auto max-w-6xl px-4 sm:px-6 py-8 sm:py-14 space-y-12">

        {/* -- HERO --------------------------------------------------- */}
        <motion.section
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="space-y-6"
        >
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-accent" />
            </span>
            <span className="text-xs font-medium text-ink-muted tracking-wide">
              {liveRooms.length > 0 ? `${liveRooms.length} live session${liveRooms.length > 1 ? 's' : ''} active` : 'Kinesia Live · Every voice. Understood.'}
            </span>
          </div>

          <div className="space-y-3">
            <h1 className="font-display font-bold text-[2.8rem] sm:text-[3.5rem] leading-[1.05] tracking-[-0.02em] text-ink-primary">
              Welcome back,{' '}
              <span className="text-gradient">{firstName}</span>
            </h1>
            <p className="text-base sm:text-lg text-ink-secondary leading-relaxed max-w-2xl">
              Speak in your language. Your teammates hear and read in theirs.
              Speak across languages with live translated audio, readable captions, and grounded session notes.
            </p>
          </div>

          {/* Feature capability pills instead of redundant buttons */}
          <div className="flex flex-wrap items-center gap-2.5 pt-1">
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-surface-2/80 border border-border text-xs text-ink-secondary font-medium">
              <Zap size={13} className="text-accent" />
              Low-latency Dual Audio
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-surface-2/80 border border-border text-xs text-ink-secondary font-medium">
              <Globe2 size={13} className="text-accent-cyan" />
              9 Real-time Languages
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-surface-2/80 border border-border text-xs text-ink-secondary font-medium">
              <FileCheck2 size={13} className="text-warn" />
              Conversation Intelligence
            </span>
          </div>
        </motion.section>

        {/* -- STATS ROW (Sensible product metrics, zero language repetition) ---- */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.1 }}
          className="grid grid-cols-2 sm:grid-cols-4 gap-3"
        >
          {[
            { label: 'Total Sessions', value: activeRooms.length.toString(), icon: Globe2, accent: false },
            { label: 'Live Now', value: liveRooms.length.toString(), icon: Radio, accent: liveRooms.length > 0 },
            { label: 'Languages Supported', value: '9 Active', icon: Languages, accent: false },
            { label: 'Intelligence Engine', value: 'Smart Notes', icon: Sparkles, accent: false },
          ].map((stat, i) => (
            <div
              key={i}
              className={`rounded-2xl border p-4 space-y-1 transition-all ${
                stat.accent
                  ? 'border-accent/25 bg-accent/8'
                  : 'border-border bg-surface-1/60 hover:bg-surface-1'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-2xs text-ink-muted font-medium tracking-wide">{stat.label}</span>
                <stat.icon size={14} className={stat.accent ? 'text-accent' : 'text-ink-muted'} />
              </div>
              <p className={`font-display font-semibold text-xl tracking-tight ${stat.accent ? 'text-accent' : 'text-ink-primary'}`}>
                {loading ? <span className="skeleton h-5 w-8 block" /> : stat.value}
              </p>
            </div>
          ))}
        </motion.div>

        {/* -- USER PROFILE BANNER ------------------------------------ */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.18 }}
        >
          <div className="rounded-2xl border border-border bg-surface-1/50 backdrop-blur-md p-5 sm:p-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-5 hover:border-border-medium transition-all">
            <div className="flex items-center gap-4">
              <div
                className="h-12 w-12 rounded-2xl flex items-center justify-center text-sm font-bold text-white shadow-lg ring-2 ring-white/5 shrink-0"
                style={{ backgroundColor: currentUser.avatar_color || '#10B981' }}
              >
                {userInitials}
              </div>
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="font-semibold text-ink-primary">
                    {currentUser.display_name || currentUser.username}
                  </h3>
                  <span className="text-2xs text-ink-muted px-2 py-0.5 rounded-md bg-surface-2 border border-border">
                    @{currentUser.username}
                  </span>
                  <span className="inline-flex items-center gap-1 text-2xs font-medium text-accent bg-accent/10 border border-accent/20 px-2 py-0.5 rounded-full">
                    <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                    Audio Engine Ready
                  </span>
                </div>
                <div className="flex items-center gap-2 text-xs text-ink-secondary mt-1">
                  <span>Channel:</span>
                  <strong className="text-ink-primary font-medium">{speakLang} → {listenLang}</strong>
                </div>
              </div>
            </div>

            <Button
              variant="secondary"
              size="sm"
              onClick={onOpenProfile}
              leftIcon={<User size={14} />}
              className="shrink-0 w-full sm:w-auto"
            >
              Manage Profile
            </Button>
          </div>
        </motion.div>

        {/* -- RECENT SESSIONS --------------------------------------- */}
        {recentRooms.length > 0 && (
          <motion.section
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.22 }}
            className="space-y-4"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-ink-primary">Your Recent Sessions</h2>
              <Button variant="ghost" size="sm" onClick={onOpenWorkspace} rightIcon={<ArrowRight size={13} />}>
                View all
              </Button>
            </div>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {recentRooms.map((room) => {
                const live = room.status === 'live';
                return (
                  <button
                    key={room.room_code}
                    type="button"
                    onClick={onOpenWorkspace}
                    className="text-left rounded-2xl border border-border bg-surface-1/50 hover:bg-surface-1 hover:border-accent/25 p-4 space-y-3 transition-all group"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-ink-primary truncate group-hover:text-accent transition-colors">
                        {room.title || 'Untitled session'}
                      </span>
                      <Badge tone={live ? 'accent' : 'default'}>
                        {live ? 'Live' : 'Ended'}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-3 text-2xs text-ink-muted">
                      <span className="font-mono tracking-wider font-semibold text-ink-secondary">{room.room_code}</span>
                      {room.participant_count != null && (
                        <span className="flex items-center gap-1"><Users size={11} /> {room.participant_count}</span>
                      )}
                      {room.created_at && (
                        <span className="flex items-center gap-1"><Clock size={11} /> {formatRelativeTime(room.created_at)}</span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </motion.section>
        )}

        {/* -- HOW IT WORKS ------------------------------------------- */}
        <motion.section
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.26 }}
          className="space-y-6"
        >
          <div>
            <h2 className="font-display text-xl sm:text-2xl font-semibold text-ink-primary tracking-tight">
              How Kinesia works
            </h2>
            <p className="text-sm text-ink-muted mt-1">
              Zero setup required. Anyone can join with a link or a 6-character room code.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {features.map((f, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.35, delay: 0.28 + i * 0.06 }}
                className="rounded-2xl border border-border bg-surface-1/50 hover:bg-surface-1 hover:border-border-medium p-5 space-y-3 transition-all group"
              >
                <div className={`h-10 w-10 rounded-xl ${f.bg} flex items-center justify-center ${f.color} group-hover:scale-110 transition-transform`}>
                  <f.icon size={20} />
                </div>
                <h3 className="font-semibold text-ink-primary text-sm leading-snug">{f.title}</h3>
                <p className="text-xs text-ink-secondary leading-relaxed">{f.desc}</p>
              </motion.div>
            ))}
          </div>
        </motion.section>

        {/* -- QUICK START CALLOUT ------------------------------------ */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.35 }}
          className="relative overflow-hidden rounded-3xl border border-accent/20 bg-gradient-to-br from-accent/8 via-surface-1 to-accent-cyan/6 p-6 sm:p-8"
        >
          <div className="absolute top-0 right-0 w-64 h-64 rounded-full bg-accent/8 blur-3xl pointer-events-none" />
          <div className="relative z-10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-5">
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <Zap size={16} className="text-accent" />
                <span className="text-xs font-semibold text-accent tracking-wide uppercase">Quick Start</span>
              </div>
              <h3 className="font-display font-semibold text-lg text-ink-primary">
                Ready to break the language barrier?
              </h3>
              <p className="text-sm text-ink-secondary">
                Create a room in seconds. Share the code. Start speaking — Kinesia handles the rest.
              </p>
            </div>
            <Button
              size="lg"
              onClick={onStartConversation}
              rightIcon={<ArrowRight size={16} />}
              className="shrink-0 shadow-glow px-8"
            >
              Start now
            </Button>
          </div>
        </motion.div>

      </div>
    </div>
  );
}
