import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// semantic color tokens must be known to tailwind-merge so e.g. text-muted
// and text-sm do not override each other
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      color: ['canvas', 'surface', 'surface-2', 'surface-3', 'line', 'line-strong', 'fg', 'muted', 'subtle', 'accent', 'accent-fg',
        'accent-soft', 'ok', 'ok-soft', 'warn', 'warn-soft', 'danger', 'danger-soft', 'info', 'info-soft'],
    },
  },
});

export const cn = (...v: ClassValue[]) => twMerge(clsx(v));

/** m:ss.mmm (original-audio ms). */
export function fmtMs(ms: number | null | undefined, withMs = true): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  const neg = ms < 0;
  const a = Math.abs(Math.round(ms));
  const m = Math.floor(a / 60000);
  const s = Math.floor((a % 60000) / 1000);
  const r = a % 1000;
  const body = withMs ? `${m}:${String(s).padStart(2, '0')}.${String(r).padStart(3, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  return neg ? `-${body}` : body;
}

export function fmtSigned(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  return `${ms > 0 ? '+' : ''}${ms} ms`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso).getTime();
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return fmtDate(iso);
}

export const ROLE_LABEL: Record<string, string> = {
  original: '原曲',
  vocals: '人声',
  instrumental: '伴奏',
  mix: '自定义混音',
};

export const STATUS_LABEL: Record<string, string> = {
  ok: '正常',
  failed: '失败',
  unaligned: '未对齐',
  skipped: '跳过',
};

/** Read a File as text (uploads share the paste parsing path). */
export function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result ?? ''));
    r.onerror = () => reject(r.error ?? new Error('读取文件失败'));
    r.readAsText(file, 'utf-8');
  });
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
