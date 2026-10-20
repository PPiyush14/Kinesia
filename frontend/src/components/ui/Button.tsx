import React from 'react';
import { cn } from '@/lib/utils';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline';
type Size = 'sm' | 'md' | 'lg' | 'icon';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

const variants: Record<Variant, string> = {
  primary:
    'bg-accent text-canvas font-semibold shadow-glow hover:bg-accent-hover active:scale-[0.98] disabled:opacity-50',
  secondary:
    'bg-surface-3 text-ink-primary border border-border-medium hover:bg-surface-4 hover:border-border-strong active:scale-[0.98]',
  ghost: 'bg-transparent text-ink-secondary hover:bg-surface-2 hover:text-ink-primary',
  danger:
    'bg-danger text-white font-semibold shadow-[0_0_24px_rgba(239,68,68,0.25)] hover:bg-danger-hover active:scale-[0.98]',
  outline:
    'bg-transparent border border-border-medium text-ink-primary hover:border-border-strong hover:bg-surface-2',
};

const sizes: Record<Size, string> = {
  sm: 'h-8 px-3 text-xs rounded-lg gap-1.5',
  md: 'h-10 px-4 text-sm rounded-xl gap-2',
  lg: 'h-12 px-6 text-sm rounded-xl gap-2.5',
  icon: 'h-10 w-10 rounded-xl inline-flex items-center justify-center',
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = 'primary',
      size = 'md',
      loading,
      disabled,
      leftIcon,
      rightIcon,
      children,
      ...props
    },
    ref
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          'inline-flex items-center justify-center font-medium transition-all duration-200 focus-ring disabled:pointer-events-none',
          variants[variant],
          sizes[size],
          className
        )}
        {...props}
      >
        {loading ? (
          <span className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
        ) : (
          leftIcon
        )}
        {children}
        {!loading && rightIcon}
      </button>
    );
  }
);
Button.displayName = 'Button';
