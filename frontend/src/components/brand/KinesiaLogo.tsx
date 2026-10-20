import React from 'react';
import { cn } from '@/lib/utils';

interface KinesiaLogoProps {
  size?: 'sm' | 'md' | 'lg' | 'xl';
  showWordmark?: boolean;
  showTagline?: boolean;
  animated?: boolean;
  onClick?: () => void;
  className?: string;
}

const sizes = {
  sm: { icon: 22, text: 'text-sm', tag: 'text-[9px]' },
  md: { icon: 28, text: 'text-base', tag: 'text-[10px]' },
  lg: { icon: 36, text: 'text-xl', tag: 'text-xs' },
  xl: { icon: 48, text: 'text-2xl', tag: 'text-sm' },
};

export function KinesiaLogo({
  size = 'md',
  showWordmark = true,
  showTagline = false,
  animated = true,
  onClick,
  className,
}: KinesiaLogoProps) {
  const s = sizes[size];
  const uid = React.useId().replace(/:/g, '');

  return (
    <div
      onClick={onClick}
      className={cn(
        'group flex items-center gap-2.5 select-none',
        onClick && 'cursor-pointer hover:opacity-95 transition-opacity',
        className
      )}
    >
      <div className="relative flex items-center justify-center rounded-xl bg-surface-2/80 border border-border p-1.5 shadow-inner-glow group-hover:border-accent/40 transition-all overflow-hidden">
        {animated && (
          <span className="absolute inset-0 rounded-xl bg-gradient-to-br from-accent/15 via-transparent to-accent-cyan/15 animate-pulse-soft pointer-events-none" />
        )}
        <svg
          width={s.icon}
          height={s.icon}
          viewBox="0 0 32 32"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden
        >
          <circle cx="16" cy="16" r="14.5" stroke="currentColor" strokeWidth="1.2" strokeOpacity="0.12" />
          <path
            className={animated ? 'animate-orbit-1 origin-center' : undefined}
            d="M6.5 16C6.5 10.7533 10.7533 6.5 16 6.5"
            stroke={`url(#k-g1-${uid})`}
            strokeWidth="2.5"
            strokeLinecap="round"
          />
          <path
            className={animated ? 'animate-orbit-2 origin-center' : undefined}
            d="M25.5 16C25.5 21.2467 21.2467 25.5 16 25.5"
            stroke={`url(#k-g2-${uid})`}
            strokeWidth="2.5"
            strokeLinecap="round"
          />
          <circle cx="16" cy="16" r="4.5" fill={`url(#k-gc-${uid})`} />
          <circle cx="16" cy="16" r="1.8" fill="#FFFFFF" />
          <defs>
            <linearGradient id={`k-g1-${uid}`} x1="6.5" y1="6.5" x2="16" y2="16" gradientUnits="userSpaceOnUse">
              <stop stopColor="#10B981" />
              <stop offset="1" stopColor="#06B6D4" />
            </linearGradient>
            <linearGradient id={`k-g2-${uid}`} x1="25.5" y1="25.5" x2="16" y2="16" gradientUnits="userSpaceOnUse">
              <stop stopColor="#06B6D4" />
              <stop offset="1" stopColor="#10B981" />
            </linearGradient>
            <linearGradient id={`k-gc-${uid}`} x1="11.5" y1="11.5" x2="20.5" y2="20.5" gradientUnits="userSpaceOnUse">
              <stop stopColor="#10B981" />
              <stop offset="1" stopColor="#06B6D4" />
            </linearGradient>
          </defs>
        </svg>
      </div>

      {showWordmark && (
        <div className="flex flex-col">
          <span className={cn('font-display font-bold tracking-[0.16em] text-ink-primary leading-none', s.text)}>
            KINESIA
          </span>
          {showTagline && (
            <span className={cn('text-ink-muted font-medium tracking-wide leading-tight mt-0.5', s.tag)}>
              Every voice. Understood.
            </span>
          )}
        </div>
      )}
    </div>
  );
}
