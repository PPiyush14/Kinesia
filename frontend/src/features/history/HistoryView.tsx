import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FileText,
  Clock,
  Users,
  Copy,
  Download,
  ArrowRight,
  ArrowLeft,
  RefreshCw,
  Search,
  CheckCircle2,
  Calendar,
  MessageSquare,
  Sparkles,
  Trash2,
  AlertTriangle,
} from 'lucide-react';
import type { Room, SessionInsights, TranscriptTurn } from '@/types';
import { roomService } from '@/services/api';
import { formatRelativeTime } from '@/lib/utils';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Card, CardContent, CardHeader } from '@/components/ui/Card';

interface HistoryViewProps {
  onSelectRoom: (room: Room, isExistingLive: boolean) => void;
  onToast: (msg: string, tone?: 'success' | 'error' | 'info') => void;
  onBackToHome?: () => void;
}

export function HistoryView({ onSelectRoom, onToast, onBackToHome }: HistoryViewProps) {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedRoom, setSelectedRoom] = useState<Room | null>(null);
  const [insights, setInsights] = useState<SessionInsights | null>(null);
  const [transcripts, setTranscripts] = useState<TranscriptTurn[]>([]);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showClearModal, setShowClearModal] = useState(false);
  const [showClearAllModal, setShowClearAllModal] = useState(false);
  const [clearingLogs, setClearingLogs] = useState(false);
  const [clearingAll, setClearingAll] = useState(false);
  const [deletingCode, setDeletingCode] = useState<string | null>(null);

  const fetchRooms = async () => {
    setLoading(true);
    try {
      const data = await roomService.listRooms();
      setRooms(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error(err);
      onToast('Could not load session history', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchRooms();
  }, []);

  const handleSelectSession = async (room: Room) => {
    setSelectedRoom(room);
    setLoadingDetails(true);
    setInsights(null);
    setTranscripts([]);

    try {
      const [ins, tr] = await Promise.allSettled([
        roomService.getInsights(room.room_code),
        roomService.getTranscripts(room.room_code),
      ]);

      if (ins.status === 'fulfilled' && ins.value) {
        setInsights(ins.value);
      }
      if (tr.status === 'fulfilled' && Array.isArray(tr.value)) {
        setTranscripts(tr.value);
      }
    } catch (err) {
      console.error('Error loading session details', err);
    } finally {
      setLoadingDetails(false);
    }
  };

  const handleDeleteRoom = async (roomCode: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setDeletingCode(roomCode);
    try {
      await roomService.deleteRoom(roomCode);
      setRooms((prev) => prev.filter((r) => r.room_code !== roomCode));
      if (selectedRoom?.room_code === roomCode) {
        setSelectedRoom(null);
        setInsights(null);
        setTranscripts([]);
      }
      onToast(`Session ${roomCode} deleted from history`, 'success');
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to delete session', 'error');
    } finally {
      setDeletingCode(null);
    }
  };

  const handleClearAllHistory = async () => {
    setClearingAll(true);
    try {
      await roomService.clearAllRooms();
      setRooms([]);
      setSelectedRoom(null);
      setInsights(null);
      setTranscripts([]);
      setShowClearAllModal(false);
      onToast('All session history cleared', 'success');
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to clear history', 'error');
    } finally {
      setClearingAll(false);
    }
  };

  const handleClearTranscripts = async () => {
    if (!selectedRoom) return;
    setClearingLogs(true);
    try {
      await roomService.clearTranscripts(selectedRoom.room_code);
      setTranscripts([]);
      setShowClearModal(false);
      onToast('Conversation logs cleared for this session', 'success');
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to clear logs', 'error');
    } finally {
      setClearingLogs(false);
    }
  };

  const filteredRooms = rooms.filter((r) => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) return true;
    return r.title.toLowerCase().includes(q) || r.room_code.toLowerCase().includes(q);
  });

  const buildMarkdown = () => {
    if (!selectedRoom) return '';
    const summary = insights?.executive_summary || 'No summary available.';
    const actions = insights?.action_items || [];
    const keys = insights?.key_points || [];

    return `# Conversation Summary: ${selectedRoom.title}
Room Code: ${selectedRoom.room_code}
Date: ${selectedRoom.created_at ? new Date(typeof selectedRoom.created_at === 'number' ? selectedRoom.created_at * 1000 : selectedRoom.created_at).toLocaleString() : 'N/A'}

## Overview & Key Takeaways
${summary}

## Key Discussion Points
${keys.length > 0 ? keys.map((k) => `- ${k}`).join('\n') : 'No key points recorded.'}

## Next Steps & Action Items
${actions.length > 0 ? actions.map((a) => `- [ ] ${a}`).join('\n') : 'No action items recorded.'}

## Dialogue Turns (${transcripts.length})
${transcripts.map((t) => `**${t.speaker_name}**: ${t.localized_text || t.original_text}`).join('\n\n')}
`;
  };

  const copyMarkdown = async () => {
    const md = buildMarkdown();
    try {
      await navigator.clipboard.writeText(md);
      setCopied(true);
      onToast('Copied Conversation Summary to clipboard', 'success');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      onToast('Could not copy', 'error');
    }
  };

  const downloadMarkdown = () => {
    const md = buildMarkdown();
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `kinesia-${selectedRoom?.room_code || 'session'}-notes.md`;
    a.click();
    URL.revokeObjectURL(url);
    onToast('Downloaded Markdown export');
  };

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-8 sm:py-10 space-y-8">
      {/* Top Breadcrumb / Back Button */}
      {onBackToHome && (
        <div>
          <button
            type="button"
            onClick={onBackToHome}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted hover:text-ink-primary transition-colors px-2 py-1 -ml-2 rounded-lg hover:bg-surface-2"
          >
            <ArrowLeft size={14} /> Back to Home
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl sm:text-3xl font-bold tracking-tight text-ink-primary">
            Session History & Notes
          </h1>
          <p className="text-xs sm:text-sm text-ink-muted mt-1">
            Browse past dialogues, review AI-generated conversation summaries, and manage recorded logs.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {rooms.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowClearAllModal(true)}
              leftIcon={<Trash2 size={13} className="text-danger" />}
              className="text-danger hover:bg-danger/10"
            >
              Clear All History
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={fetchRooms}
            leftIcon={<RefreshCw size={13} className={loading ? 'animate-spin' : ''} />}
          >
            Refresh
          </Button>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left Column: Room Sessions List */}
        <div className="lg:col-span-5 space-y-3">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
            <input
              type="text"
              placeholder="Search sessions by title or code..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-2 text-xs rounded-xl bg-surface-1 border border-border text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent/40"
            />
          </div>

          {loading ? (
            <div className="py-12 text-center text-xs text-ink-muted">Loading conversation logs...</div>
          ) : filteredRooms.length === 0 ? (
            <Card className="border border-border bg-surface-1/50 p-8 text-center space-y-2">
              <FileText size={24} className="mx-auto text-ink-muted" />
              <p className="text-xs font-medium text-ink-secondary">No sessions found</p>
              <p className="text-2xs text-ink-muted">
                {searchQuery ? 'Try a different search term' : 'Start or join a conversation to build your history'}
              </p>
            </Card>
          ) : (
            filteredRooms.map((room) => {
              const isSelected = selectedRoom?.id === room.id;
              const isLive = room.status === 'live';

              return (
                <Card
                  key={room.id}
                  onClick={() => handleSelectSession(room)}
                  className={`cursor-pointer transition-all border p-4 ${
                    isSelected
                      ? 'border-accent bg-accent/5 ring-1 ring-accent/30'
                      : 'border-border bg-surface-1 hover:border-border-subtle hover:bg-surface-2/60'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="font-semibold text-xs sm:text-sm text-ink-primary line-clamp-1">
                      {room.title || 'Untitled Session'}
                    </h3>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <Badge tone={isLive ? 'accent' : 'default'}>
                        {isLive ? 'Live' : 'Archived'}
                      </Badge>
                      <button
                        type="button"
                        onClick={(e) => void handleDeleteRoom(room.room_code, e)}
                        className="p-1 rounded-md text-ink-muted hover:text-danger hover:bg-danger/10 transition-colors"
                        title="Delete this session from history"
                        disabled={deletingCode === room.room_code}
                      >
                        <Trash2 size={13} className={deletingCode === room.room_code ? 'animate-pulse' : ''} />
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 text-2xs text-ink-muted mt-2">
                    <span className="font-mono font-semibold text-ink-secondary tracking-wider">
                      {room.room_code}
                    </span>
                    {room.created_at && (
                      <span className="flex items-center gap-1">
                        <Clock size={12} /> {formatRelativeTime(room.created_at)}
                      </span>
                    )}
                  </div>
                </Card>
              );
            })
          )}
        </div>

        {/* Selected Session Details Viewer */}
        <div className="lg:col-span-7">
          {!selectedRoom ? (
            <Card className="border border-border bg-surface-1/40 p-12 text-center space-y-3">
              <div className="h-12 w-12 rounded-2xl bg-surface-2 flex items-center justify-center mx-auto text-accent">
                <Sparkles size={22} />
              </div>
              <h3 className="font-semibold text-sm text-ink-primary">Select a Session</h3>
              <p className="text-xs text-ink-muted max-w-sm mx-auto">
                Click any session from the list on the left to view its executive summary, key points, action items, and complete transcripts.
              </p>
            </Card>
          ) : loadingDetails ? (
            <Card className="border border-border bg-surface-1 p-12 text-center space-y-3">
              <div className="h-8 w-8 rounded-full border-2 border-accent border-t-transparent animate-spin mx-auto" />
              <p className="text-xs text-ink-muted">Retrieving session notes & transcripts...</p>
            </Card>
          ) : (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25 }}
              className="space-y-5"
            >
              {/* Header card */}
              <Card className="border border-border bg-surface-1 p-5">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-bold text-ink-primary">{selectedRoom.title}</h2>
                      <span className="font-mono text-2xs px-2 py-0.5 rounded bg-surface-2 border border-border text-ink-secondary">
                        {selectedRoom.room_code}
                      </span>
                    </div>
                    {selectedRoom.created_at && (
                      <p className="text-2xs text-ink-muted mt-1 flex items-center gap-1">
                        <Calendar size={12} /> {new Date(typeof selectedRoom.created_at === 'number' ? selectedRoom.created_at * 1000 : selectedRoom.created_at).toLocaleString()}
                      </p>
                    )}
                  </div>

                  <div className="flex items-center flex-wrap gap-2">
                    <Button variant="secondary" size="sm" onClick={copyMarkdown} leftIcon={<Copy size={13} />}>
                      {copied ? 'Copied' : 'Copy Summary'}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={downloadMarkdown} leftIcon={<Download size={13} />}>
                      Download
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void handleDeleteRoom(selectedRoom.room_code)}
                      leftIcon={<Trash2 size={13} className="text-danger" />}
                      className="text-danger hover:bg-danger/10"
                      title="Permanently delete this session"
                    >
                      Delete
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setShowClearModal(true)}
                      leftIcon={<Trash2 size={13} />}
                      title="Clear recorded logs for this session"
                    >
                      Clear Logs
                    </Button>
                    {selectedRoom.status === 'live' && (
                      <Button
                        size="sm"
                        onClick={() => onSelectRoom(selectedRoom, true)}
                        rightIcon={<ArrowRight size={13} />}
                      >
                        Re-join
                      </Button>
                    )}
                  </div>
                </div>
              </Card>

              {/* Executive Summary Card */}
              <Card className="border border-border bg-surface-1">
                <CardHeader className="border-b border-border-subtle p-4 flex items-center gap-2">
                  <Sparkles size={16} className="text-accent" />
                  <h3 className="text-xs font-semibold text-ink-primary uppercase tracking-wider">
                    Executive Summary
                  </h3>
                </CardHeader>
                <CardContent className="p-4">
                  <p className="text-xs sm:text-sm text-ink-secondary leading-relaxed">
                    {insights?.executive_summary || 'No summary was generated for this session.'}
                  </p>
                </CardContent>
              </Card>

              {/* Action Items */}
              {insights?.action_items && insights.action_items.length > 0 && (
                <Card className="border border-border bg-surface-1">
                  <CardHeader className="border-b border-border-subtle p-4 flex items-center gap-2">
                    <CheckCircle2 size={16} className="text-accent-cyan" />
                    <h3 className="text-xs font-semibold text-ink-primary uppercase tracking-wider">
                      Action Items ({insights.action_items.length})
                    </h3>
                  </CardHeader>
                  <CardContent className="p-4 space-y-2">
                    {insights.action_items.map((item, i) => (
                      <div key={i} className="flex items-start gap-2.5 text-xs text-ink-secondary">
                        <div className="h-4 w-4 rounded-md border border-border flex items-center justify-center shrink-0 mt-0.5 text-accent">
                          ?
                        </div>
                        <span>{item}</span>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}

              {/* Transcript Turns Timeline */}
              <Card className="border border-border bg-surface-1">
                <CardHeader className="border-b border-border-subtle p-4 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <MessageSquare size={16} className="text-ink-muted" />
                    <h3 className="text-xs font-semibold text-ink-primary uppercase tracking-wider">
                      Dialogue Turns ({transcripts.length})
                    </h3>
                  </div>
                  {transcripts.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setShowClearModal(true)}
                      className="text-2xs text-danger hover:underline font-medium flex items-center gap-1"
                    >
                      <Trash2 size={12} /> Clear turns
                    </button>
                  )}
                </CardHeader>
                <CardContent className="p-4 max-h-80 overflow-y-auto space-y-2.5">
                  {transcripts.length === 0 ? (
                    <p className="text-xs text-ink-muted text-center py-6">No transcript turns recorded (logs may have been cleared).</p>
                  ) : (
                    transcripts.map((t, idx) => (
                      <div key={t.id ?? idx} className="rounded-xl bg-surface-0 border border-border p-3 space-y-1">
                        <div className="flex items-center justify-between text-2xs">
                          <span className="font-semibold text-accent">{t.speaker_name}</span>
                          <span className="font-mono text-ink-muted">{t.source_lang?.toUpperCase()}</span>
                        </div>
                        <p className="text-xs text-ink-primary leading-snug">{t.localized_text || t.original_text}</p>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>
            </motion.div>
          )}
        </div>
      </div>

      {/* Clear Logs Confirmation Modal */}
      <AnimatePresence>
        {showClearModal && selectedRoom && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-md rounded-2xl bg-surface-1 border border-border p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center gap-3 text-danger">
                <div className="h-10 w-10 rounded-xl bg-danger/10 border border-danger/20 flex items-center justify-center shrink-0">
                  <AlertTriangle size={20} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-ink-primary">Clear Conversation Logs?</h3>
                  <p className="text-2xs text-ink-muted font-mono">{selectedRoom.room_code}</p>
                </div>
              </div>
              <p className="text-xs text-ink-secondary leading-relaxed">
                This will permanently delete all recorded dialogue turns for <strong>{selectedRoom.title}</strong>. Only participants of this room have access to these logs.
              </p>
              <div className="flex items-center justify-end gap-2 pt-2 border-t border-border-subtle">
                <Button variant="secondary" size="sm" onClick={() => setShowClearModal(false)}>
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  loading={clearingLogs}
                  onClick={handleClearTranscripts}
                >
                  Clear Transcripts
                </Button>
              </div>
            </motion.div>
          </div>
        )}

        {/* Clear All Sessions Confirmation Modal */}
        {showClearAllModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-md rounded-2xl bg-surface-1 border border-border p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center gap-3 text-danger">
                <div className="h-10 w-10 rounded-xl bg-danger/10 border border-danger/20 flex items-center justify-center shrink-0">
                  <AlertTriangle size={20} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-ink-primary">Clear All Session History?</h3>
                  <p className="text-2xs text-ink-muted">Permanently delete all sessions ({rooms.length})</p>
                </div>
              </div>
              <p className="text-xs text-ink-secondary leading-relaxed">
                This will permanently delete all past rooms, transcripts, and conversation notes from your account. This action cannot be undone.
              </p>
              <div className="flex items-center justify-end gap-2 pt-2 border-t border-border-subtle">
                <Button variant="secondary" size="sm" onClick={() => setShowClearAllModal(false)}>
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  loading={clearingAll}
                  onClick={handleClearAllHistory}
                >
                  Clear All History
                </Button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
