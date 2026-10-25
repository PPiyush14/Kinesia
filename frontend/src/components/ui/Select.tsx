import React from 'react';
import { cn } from '@/lib/utils';
import { ChevronDown } from 'lucide-react';

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  options: { value: string; label: string }[];
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, label, options, id, ...props }, ref) => {
    const generatedId = React.useId();
    const selectId = id ?? props.name ?? generatedId;
    return (
      <div className="w-full space-y-1.5">
        {label && (
          <label
            htmlFor={selectId}
            className="block text-xs font-medium text-ink-secondary tracking-wide"
          >
            {label}
          </label>
        )}
        <div className="relative">
          <select
            ref={ref}
            id={selectId}
            className={cn(
              'w-full h-11 appearance-none rounded-xl bg-surface-0 border border-border text-ink-primary text-sm',
              'px-3.5 pr-10 transition-colors duration-150',
              'focus:outline-none focus:border-accent/60 focus:ring-2 focus:ring-accent/20',
              className,
            )}
            {...props}
          >
            {options.map((o) => (
              <option
                key={o.value}
                value={o.value}
                className="bg-surface-2 text-ink-primary"
              >
                {o.label}
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-muted pointer-events-none" />
        </div>
      </div>
    );
  },
);
Select.displayName = 'Select';
