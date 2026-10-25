import React from 'react';
import { cn } from '@/lib/utils';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  hint?: string;
  leftSlot?: React.ReactNode;
  rightSlot?: React.ReactNode;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  (
    { className, label, error, hint, leftSlot, rightSlot, id, ...props },
    ref,
  ) => {
    const generatedId = React.useId();
    const inputId = id ?? props.name ?? generatedId;
    return (
      <div className="w-full space-y-1.5">
        {label && (
          <label
            htmlFor={inputId}
            className="block text-xs font-medium text-ink-secondary tracking-wide"
          >
            {label}
          </label>
        )}
        <div className="relative">
          {leftSlot && (
            <div className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted pointer-events-none">
              {leftSlot}
            </div>
          )}
          <input
            ref={ref}
            id={inputId}
            aria-invalid={error ? true : undefined}
            aria-describedby={error || hint ? `${inputId}-message` : undefined}
            className={cn(
              'w-full h-11 rounded-xl bg-surface-0 border border-border text-ink-primary text-sm placeholder:text-ink-faint',
              'px-3.5 transition-colors duration-150',
              'focus:outline-none focus:border-accent/60 focus:ring-2 focus:ring-accent/20',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              leftSlot && 'pl-10',
              rightSlot && 'pr-10',
              error &&
                'border-danger/60 focus:border-danger focus:ring-danger/20',
              className,
            )}
            {...props}
          />
          {rightSlot && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-muted">
              {rightSlot}
            </div>
          )}
        </div>
        {error ? (
          <p id={`${inputId}-message`} className="text-2xs text-danger">
            {error}
          </p>
        ) : hint ? (
          <p id={`${inputId}-message`} className="text-2xs text-ink-muted">
            {hint}
          </p>
        ) : null}
      </div>
    );
  },
);
Input.displayName = 'Input';
