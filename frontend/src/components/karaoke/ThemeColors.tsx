// Theme colour picker: one-click swatches plus a custom colour (the colour templates of
// karaoke.themes; used by the style panel and the simple-mode task form).

import { Palette } from 'lucide-react';
import { cn } from '@/lib/format';

export const SWATCHES = ['#ED35B3', '#FF4D6D', '#FF8A1E', '#F5C400', '#3CC46A', '#1FB5C9', '#2F80ED', '#8B5CF6'];
export const TEMPLATE_LABEL = { plain: '朴素', glow: '荧光' } as const;

export function ColorRow({ label, value, onChange, extra }: { label: string; value: string; onChange: (c: string) => void; extra?: React.ReactNode }) {
  const custom = !SWATCHES.includes(value.toUpperCase());
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="w-9 shrink-0 text-[13px] text-muted">{label}</span>
      {SWATCHES.map((c) => (
        <button key={c} type="button" aria-label={`${label} ${c}`} aria-pressed={value.toUpperCase() === c}
          onClick={() => onChange(c)} style={{ background: c }}
          className={cn('focus-ring size-6 rounded-full ring-1 ring-black/10 transition',
            value.toUpperCase() === c ? 'ring-2 ring-fg ring-offset-2 ring-offset-surface' : 'hover:scale-110')} />
      ))}
      <label title="自定义颜色" className={cn('relative grid size-6 cursor-pointer place-items-center overflow-hidden rounded-full ring-1 ring-line-strong',
        custom && 'ring-2 ring-fg ring-offset-2 ring-offset-surface')} style={custom ? { background: value } : undefined}>
        {!custom && <Palette className="size-3.5 text-muted" />}
        <input type="color" aria-label={`自定义${label}`} value={value} onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="absolute inset-0 cursor-pointer opacity-0" />
      </label>
      <span className="ml-1 font-mono text-xs text-subtle">{value.toUpperCase()}</span>
      {extra}
    </div>
  );
}
