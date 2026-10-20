import { AnimatePresence, motion } from 'framer-motion';
import { CheckCircle2, AlertCircle, Info } from 'lucide-react';
import { cn } from '@/lib/utils';

export type ToastTone = 'success' | 'error' | 'info';

interface ToastProps {
  message: string | null;
  tone?: ToastTone;
}

export function Toast({ message, tone = 'success' }: ToastProps) {
  const Icon = tone === 'error' ? AlertCircle : tone === 'info' ? Info : CheckCircle2;
  return (
    <AnimatePresence>
      {message && (
        <motion.div
          initial={{ opacity: 0, y: 16, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 12, scale: 0.96 }}
          transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
          className={cn(
            'fixed bottom-6 right-6 z-50 max-w-sm glass-strong rounded-2xl px-4 py-3 flex items-start gap-3 shadow-dock',
            tone === 'success' && 'border-accent/30',
            tone === 'error' && 'border-danger/30',
            tone === 'info' && 'border-accent-cyan/30'
          )}
        >
          <Icon
            className={cn(
              'h-4.5 w-4.5 mt-0.5 shrink-0',
              tone === 'success' && 'text-accent',
              tone === 'error' && 'text-danger',
              tone === 'info' && 'text-accent-cyan'
            )}
            size={18}
          />
          <p className="text-sm text-ink-primary leading-snug">{message}</p>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
