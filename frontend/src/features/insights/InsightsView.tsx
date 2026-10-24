import { useState } from 'react';
import { motion } from 'framer-motion';
import { CheckCircle2, ListTodo, FileText, Download, Copy, ArrowLeft } from 'lucide-react';
import type { Room, SessionInsights } from '@/types';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Card, CardContent, CardHeader } from '@/components/ui/Card';

interface InsightsViewProps {
  room: Room;
  insights: SessionInsights | null;
  onReturnToWorkspace: () => void;
  onReturnToHome?: () => void;
  onToast: (msg: string, tone?: 'success' | 'error' | 'info') => void;
}

export function InsightsView({ room, insights, onReturnToWorkspace, onToast }: InsightsViewProps) {
  const [copied, setCopied] = useState(false);

  const summary = insights?.executive_summary?.trim() || 'No summary was generated for this session.';
  const keyPoints = insights?.key_points?.filter(Boolean) ?? [];
  const actionItems = insights?.action_items?.filter(Boolean) ?? [];

  const markdown = buildMarkdown(room, summary, keyPoints, actionItems);

  const copyMarkdown = async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      onToast('Copied summary to clipboard');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      onToast('Could not copy', 'error');
    }
  };

  const downloadMarkdown = () => {
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `kinesia-${room.room_code}-summary.md`;
    a.click();
    URL.revokeObjectURL(url);
    onToast('Downloaded Markdown export');
  };

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-8 sm:py-12 space-y-8">
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
        className="space-y-6"
      >
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div className="space-y-2">
            <Badge tone="accent" dot>
              Session complete
            </Badge>
            <h1 className="font-display text-title text-ink-primary tracking-tight">{room.title}</h1>
            <p className="text-sm text-ink-muted">
              Room <span className="font-mono tracking-wider text-ink-secondary">{room.room_code}</span>
              {' · '}Conversation Summary & Notes
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Button variant="secondary" size="sm" onClick={() => void copyMarkdown()} leftIcon={<Copy size={14} />}>
              {copied ? 'Copied' : 'Copy'}
            </Button>
            <Button variant="secondary" size="sm" onClick={downloadMarkdown} leftIcon={<Download size={14} />}>
              Export
            </Button>
          </div>
        </div>

        {room.record_session === false && (
          <div className="rounded-2xl border border-border bg-surface-1 px-4 py-3 flex items-center gap-3 text-xs text-ink-muted">
            <Badge tone="muted">Recording disabled</Badge>
            <span>Transcripts and post-session notes were not persisted for this private dialogue.</span>
          </div>
        )}

        <Card elevated>
          <CardHeader>
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-accent" />
              <h2 className="text-sm font-semibold text-ink-primary">Conversation overview</h2>
            </div>
          </CardHeader>
          <CardContent>
            {insights?.summary_method === 'extractive' && <p className="mb-3 text-xs leading-relaxed text-ink-muted">Notes are selected from the actual transcript. Possible commitments are quoted for your review, without inferred owners or deadlines.</p>}
            <p className="text-sm text-ink-secondary leading-relaxed whitespace-pre-wrap">{summary}</p>
          </CardContent>
        </Card>

        {keyPoints.length > 0 && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <CheckCircle2 size={16} className="text-accent-cyan" />
                <h2 className="text-sm font-semibold text-ink-primary">Key points</h2>
              </div>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2.5">
                {keyPoints.map((p, i) => (
                  <li key={i} className="flex gap-2.5 text-sm text-ink-secondary">
                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-accent-cyan shrink-0" />
                    <span className="leading-relaxed">{p}</span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {actionItems.length > 0 && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <ListTodo size={16} className="text-warn" />
                <h2 className="text-sm font-semibold text-ink-primary">{insights?.summary_method === 'extractive' ? 'Possible follow-ups — review' : 'Follow-ups'}</h2>
              </div>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2.5">
                {actionItems.map((item, i) => (
                  <li
                    key={i}
                    className="flex gap-3 items-start rounded-xl bg-surface-0 border border-border px-3.5 py-2.5 text-sm text-ink-secondary"
                  >
                    <span className="mt-0.5 h-4 w-4 rounded border border-border-medium shrink-0" />
                    <span className="leading-relaxed">{item}</span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {!keyPoints.length && !actionItems.length && (
          <p className="text-center text-sm text-ink-muted py-4">
            No key points or action items were extracted for this session.
          </p>
        )}

        <div className="pt-2">
          <Button variant="secondary" onClick={onReturnToWorkspace} leftIcon={<ArrowLeft size={16} />}>
            Back to workspace
          </Button>
        </div>
      </motion.div>
    </div>
  );
}

function buildMarkdown(
  room: Room,
  summary: string,
  keyPoints: string[],
  actionItems: string[]
): string {
  const lines = [
    `# ${room.title || 'Session'} — Conversation Summary`,
    '',
    `**Room:** \`${room.room_code}\``,
    `**Generated by:** Kinesia`,
    '',
    '## Overview & Key Takeaways',
    '',
    summary,
    '',
  ];
  if (keyPoints.length) {
    lines.push('## Key Points', '', ...keyPoints.map((p) => `- ${p}`), '');
  }
  if (actionItems.length) {
    lines.push('## Possible Follow-ups', '', ...actionItems.map((a) => `- [ ] ${a}`), '');
  }
  return lines.join('\n');
}
