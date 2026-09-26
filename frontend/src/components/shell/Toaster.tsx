import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { cn } from '@/lib/format';
import { dismissToast, useApp } from '@/store/app';

const ICON = { ok: CheckCircle2, error: XCircle, info: Info, warn: AlertTriangle };
const COLOR = { ok: 'text-ok', error: 'text-danger', info: 'text-info', warn: 'text-warn' };

/**
 * Notifications, below the top bar (so they never cover its buttons).  Errors
 * are announced as alerts, the rest politely.
 */
export function Toaster() {
  const toasts = useApp((s) => s.toasts);
  return (
    <div className="pointer-events-none fixed top-16 right-4 z-[60] flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2" aria-live="polite">
      {toasts.map((t) => {
        const Icon = ICON[t.kind];
        return (
          <div key={t.id} role={t.kind === 'error' ? 'alert' : 'status'}
            className="pointer-events-auto flex animate-slide-up gap-3 rounded-xl border border-line bg-surface px-4 py-3 shadow-[var(--shadow-pop)]">
            <Icon className={cn('mt-0.5 size-4 shrink-0', COLOR[t.kind])} aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold break-words">{t.title}</div>
              {t.body && <div className="mt-0.5 text-xs break-words whitespace-pre-wrap text-muted">{t.body}</div>}
            </div>
            <button onClick={() => dismissToast(t.id)} className="focus-ring shrink-0 self-start rounded text-muted hover:text-fg" aria-label="关闭通知"><X className="size-3.5" /></button>
          </div>
        );
      })}
    </div>
  );
}
