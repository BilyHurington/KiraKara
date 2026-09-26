// A download that says why it failed (a toast instead of an error page) and
// cannot be clicked through while disabled.  Text exports are saved from the
// response; `big` files (videos, WAV, project packages) are checked first and
// then streamed to disk by the browser.

import { Loader2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { downloadFile } from '@/lib/api';
import { toast } from '@/store/app';
import { Button, buttonClass, type ButtonProps } from '@/components/ui';

export function DownloadButton({ href, big, check, filename, errorTitle = '下载失败', before, children, ...rest }: Omit<ButtonProps, 'onClick'> & {
  href: string;
  big?: boolean;
  /** false: built on request (project package) — no check first, it would be built twice */
  check?: boolean;
  filename?: string;
  errorTitle?: string;
  /** runs first (e.g. save a style edit that is still waiting) */
  before?: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const click = async () => {
    setBusy(true);
    try {
      await before?.();
      await downloadFile(href, { big, filename, check });
    } catch (e: any) {
      toast('error', errorTitle, e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button {...rest} loading={busy || rest.loading} onClick={() => void click()}>
      {children}
    </Button>
  );
}

/**
 * The same as a real link (it can be copied or saved with a right click), but
 * a normal click checks the file first and reports a readable error.
 */
export function DownloadLink({ href, filename, children, icon, variant = 'primary', size = 'sm', className }: {
  href: string; filename?: string; children: ReactNode; icon?: ReactNode;
  variant?: Parameters<typeof buttonClass>[0]; size?: Parameters<typeof buttonClass>[1]; className?: string;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <a href={href} download={filename ?? ''} className={buttonClass(variant, size, className)} aria-busy={busy || undefined}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;  // the browser's own link actions
        e.preventDefault();
        setBusy(true);
        downloadFile(href, { big: true, filename })
          .catch((err: any) => toast('error', '下载失败', err?.message ?? String(err)))
          .finally(() => setBusy(false));
      }}>
      {busy ? <Loader2 className="size-4 animate-spin" /> : icon}
      {children}
    </a>
  );
}
