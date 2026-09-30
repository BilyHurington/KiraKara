// The singers of the karaoke style: colour and name of each, and how parts sung together look.

import { ChevronDown, Keyboard, Plus, RotateCcw, Trash2, Users, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/format';
import { idsKey, keysLeft, MAX_SINGERS, mixBackground, parseCombo, singerLabel, withCombo, withNewSinger } from '@/lib/singers';
import type { KaraokeSinger, KaraokeSingers, SingerColors } from '@/lib/types';
import { Button, Card, CardBody, CardHeader, ConfirmButton, Input, Segmented, Tip } from '@/components/ui';
import { ColorField } from '@/components/karaoke/StylePanel';

type Detail = 'color_unsung' | 'color_sung' | 'outline_color' | 'glow_unsung' | 'glow_sung';
const DETAILS: [Detail, keyof SingerColors, string][] = [
  ['color_unsung', 'unsung', '未唱'], ['color_sung', 'sung', '已唱（扫光）'], ['outline_color', 'outline', '描边'],
  ['glow_unsung', 'glow_unsung', '荧光 · 未唱时'], ['glow_sung', 'glow_sung', '荧光 · 唱过后'],
];

export function SingerList({ singers, onChange, onRemove, usage, glow }: {
  singers: KaraokeSingers;
  onChange: (next: KaraokeSingers) => void;
  onRemove: (n: number) => void;
  /** lines each singer (1-based) sings */
  usage: (n: number) => number;
  /** the style has the glow edge on (its colours matter) */
  glow: boolean;
}) {
  const { members } = singers;
  const [open, setOpen] = useState<number | null>(null);
  const [resolved, setResolved] = useState<SingerColors[]>([]);
  // every colour with the derived ones filled in (shown in the details)
  const key = JSON.stringify(members);
  useEffect(() => {
    if (!members.length) return;
    let stop = false;
    const t = setTimeout(() => {
      void api.post<SingerColors[]>('/api/karaoke/singer-colors', { members })
        .then((r) => { if (!stop) setResolved(r); }).catch(() => undefined);
    }, 250);
    return () => { stop = true; clearTimeout(t); };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const edit = (i: number, patch: Partial<KaraokeSinger>) =>
    onChange({ ...singers, members: members.map((m, j) => (j === i ? { ...m, ...patch } : m)) });
  const add = () => onChange(withNewSinger(singers));
  const combos = singers.combos ?? [];
  const full = keysLeft(singers) <= 0;
  const addCombo = () => onChange(withCombo(singers, members.slice(0, 2).map((_, i) => i + 1)).next);
  const sample = members.slice(0, 3).map((m) => m.color);

  return (
    <Card>
      <CardHeader icon={<Users className="size-4" />} title="演唱者"
        description={`最多 ${MAX_SINGERS} 位。每人选一个主色，未唱、描边、荧光的颜色会自动搭配。`} />
      <CardBody className="space-y-3">
        {members.length === 0 && <p className="text-[13px] text-muted">还没有演唱者。添加后，在左边选中歌词，按数字键指定是谁唱的。</p>}
        <ol className="space-y-2">
          {members.map((m, i) => (
            <li key={i} className="rounded-xl border border-line p-2.5">
              <div className="flex items-center gap-2">
                <span className="grid size-6 shrink-0 place-items-center rounded-md text-xs font-bold text-white shadow-sm" style={{ background: m.color }}>{i + 1}</span>
                <label className="relative size-7 shrink-0 cursor-pointer overflow-hidden rounded-md ring-1 ring-line-strong" style={{ background: m.color }}>
                  <input type="color" aria-label={`${singerLabel(members, i + 1)}的颜色`} value={m.color}
                    onChange={(e) => edit(i, { color: e.target.value.toUpperCase() })} className="absolute inset-0 cursor-pointer opacity-0" />
                </label>
                <Input className="h-8 min-w-0 flex-1" value={m.name} maxLength={40} placeholder={`演唱者 ${i + 1}`}
                  aria-label={`演唱者 ${i + 1} 的名字`} onChange={(e) => edit(i, { name: e.target.value })} />
                <span className="shrink-0 text-xs text-subtle" title="唱到的行数">{usage(i + 1)} 行</span>
                <button type="button" className="focus-ring rounded-md p-1 text-muted hover:bg-surface-2" aria-expanded={open === i}
                  aria-label={`${singerLabel(members, i + 1)}的详细颜色`} onClick={() => setOpen(open === i ? null : i)}>
                  <ChevronDown className={cn('size-4 transition', open === i && 'rotate-180')} />
                </button>
              </div>
              {open === i && (
                <div className="mt-3 space-y-2 border-t border-line pt-3">
                  {DETAILS.filter(([f]) => glow || !f.startsWith('glow')).map(([field, role, label]) => (
                    <ColorField key={field} label={`${label}${m[field] ? '' : '（自动）'}`} value={m[field] || resolved[i]?.[role] || m.color}
                      onChange={(v) => edit(i, { [field]: v } as Partial<KaraokeSinger>)} />
                  ))}
                  <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                    <Button size="xs" variant="ghost" icon={<RotateCcw className="size-3.5" />}
                      disabled={DETAILS.every(([f]) => !m[f])}
                      onClick={() => edit(i, { color_unsung: '', color_sung: '', outline_color: '', glow_unsung: '', glow_sung: '' })}>
                      全部自动
                    </Button>
                    <ConfirmButton size="xs" variant="ghost" icon={<Trash2 className="size-3.5" />}
                      question={usage(i + 1) ? `${usage(i + 1)} 行里这位演唱者唱的部分会回到默认配色，后面的编号前移` : '删除这位演唱者？'}
                      confirmLabel="删除" onConfirm={() => onRemove(i + 1)}>
                      删除
                    </ConfirmButton>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ol>
        <Button size="sm" variant="outline" icon={<Plus className="size-4" />} disabled={full} onClick={add}
          title={full ? '数字键 1–9 已经用完：删掉一个组合或演唱者' : undefined}>
          添加演唱者{members.length >= MAX_SINGERS ? `（最多 ${MAX_SINGERS} 位）` : full ? '（数字键已用完）' : ''}
        </Button>

        {members.length >= 2 && (
          <div className="space-y-2 border-t border-line pt-3">
            <div className="flex items-center gap-1.5 text-[13px] font-medium"><Keyboard className="size-4 text-muted" />组合快捷键</div>
            <p className="text-xs text-subtle">常一起唱的几个人可以存到一个数字键上，例如把 1+2 存到 {Math.min(members.length + 1, MAX_SINGERS)}：以后按一下就指定为两人一起唱。</p>
            {combos.map((c) => (
              <ComboRow key={c.key} combo={c} count={members.length} colors={c.singers.map((n) => members[n - 1]?.color).filter(Boolean) as string[]}
                mix={singers.mix} direction={singers.direction}
                onChange={(ids) => onChange({ ...singers, combos: combos.map((x) => (x.key === c.key ? { ...x, singers: ids } : x)) })}
                onRemove={() => onChange({ ...singers, combos: combos.filter((x) => x.key !== c.key) })} />
            ))}
            <Button size="xs" variant="outline" icon={<Plus className="size-3.5" />} disabled={full} onClick={addCombo}>
              添加组合{full ? '（数字键已用完）' : ''}
            </Button>
          </div>
        )}

        <div className="space-y-2.5 border-t border-line pt-3">
          <div className="text-[13px] font-medium">几个人一起唱的部分</div>
          <div className="flex flex-wrap items-center gap-2">
            <Segmented size="sm" label="一起唱的效果" value={singers.mix} onChange={(v) => onChange({ ...singers, mix: v })}
              options={[{ value: 'split', label: '分色' }, { value: 'gradient', label: '渐变' }]} />
            <Segmented size="sm" label="一起唱的方向" value={singers.direction} onChange={(v) => onChange({ ...singers, direction: v })}
              options={[{ value: 'vertical', label: '上下' }, { value: 'horizontal', label: '左右' }]} />
            {sample.length >= 2 && (
              <Tip content="示意：实际效果看预览">
                <span aria-hidden className="ml-auto text-2xl leading-none font-black"
                  style={{ backgroundImage: mixBackground(sample, singers.mix, singers.direction), WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>
                  {singers.direction === 'vertical' ? '歌' : '一起唱'}
                </span>
              </Tip>
            )}
          </div>
          <p className="text-xs text-subtle">
            {singers.direction === 'vertical'
              ? `每个字${singers.mix === 'split' ? '上下分成几段' : '从上到下渐变'}：第一个人在上，依次往下（如 1+2：上半 1 号、下半 2 号）。注音字小，用最上面那个人的颜色。`
              : `一起唱的一整段${singers.mix === 'split' ? '从左到右分成几段' : '从左到右渐变'}：第一个人在左，依次往右；注音跟着所在的位置。`}
            翻译用第一个人的颜色。
          </p>
        </div>
      </CardBody>
    </Card>
  );
}

/** One saved combination: its key and who sings together (edited as "1+2"). */
function ComboRow({ combo, count, colors, mix, direction, onChange, onRemove }: {
  combo: { key: number; singers: number[] }; count: number; colors: string[];
  mix: 'split' | 'gradient'; direction: 'vertical' | 'horizontal';
  onChange: (ids: number[]) => void; onRemove: () => void;
}) {
  const [text, setText] = useState(combo.singers.join('+'));
  useEffect(() => { setText(combo.singers.join('+')); }, [idsKey(combo.singers)]); // eslint-disable-line react-hooks/exhaustive-deps
  const commit = () => {
    const ids = parseCombo(text, count);
    if (ids.length >= 2 && idsKey(ids) !== idsKey(combo.singers)) onChange(ids);
    else setText(combo.singers.join('+'));
  };
  return (
    <div className="flex items-center gap-2">
      <span className="grid size-6 shrink-0 place-items-center rounded-md text-xs font-bold text-white shadow-sm"
        style={{ background: colors.length ? mixBackground(colors, mix, direction) : 'var(--color-line)' }}>{combo.key}</span>
      <span className="text-xs text-muted">=</span>
      <Input className="h-8 w-28" value={text} aria-label={`快捷键 ${combo.key} 的演唱者`} placeholder="如 1+2"
        onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }} />
      <button type="button" className="focus-ring ml-auto rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg"
        aria-label={`删除快捷键 ${combo.key}`} onClick={onRemove}>
        <X className="size-4" />
      </button>
    </div>
  );
}
