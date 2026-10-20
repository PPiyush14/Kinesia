import React from 'react';
import { cn } from '@/lib/utils';

type Tone = 'default' | 'accent' | 'cyan' | 'danger' | 'warn' | 'muted';

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  dot?: boolean;
}

const tones: Record<Tone, string> = {
  default: 'bg-surface-3 text-ink-secondary border-border',
  accent: 'bg-accent-soft text-accent border-accent/20',
  cyan: 'bg-accent-cyanSoft text-accent-cyan border-accent-cyan/20',
  danger: 'bg-danger-soft text-danger border-danger/20',
  warn: 'bg-warn-soft text-warn border-warn/20',
  muted: 'bg-surface-2 text-ink-muted border-border-subtle',
};

export function Badge({ className, tone = 'default', dot, children, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-2xs font-medium tracking-wide',
        tones[tone],
        className
      )}
      {...props}
    >
      {dot && (
        <span
          className={cn(
            'h-1.5 w-1.5 rounded-full',
            tone === 'accent' && 'bg-accent animate-pulse-soft',
            tone === 'cyan' && 'bg-accent-cyan',
            tone === 'danger' && 'bg-danger',
            tone === 'warn' && 'bg-warn',
            (tone === 'default' || tone === 'muted') && 'bg-ink-muted'
          )}
        />
      )}
      {children}
    </span>
  );
}
