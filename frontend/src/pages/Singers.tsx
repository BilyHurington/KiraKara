// Step 7 (optional): singers — for songs with several voices, who sings which line / word, and
// each singer's colours.  The whole lyrics in one view: click line numbers (Shift / ⌘ for more)
// or drag over the words (across lines too), then press 1–9; 1 + 2 = sung together; 0 clears.
// Assignments are saved at once (one undo step each); the singer list lives in the style.

import { ArrowRight, Eraser, Loader2, Play, Subtitles, Tags, Users } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { api } from '@/lib/api';
import { cn, fmtMs } from '@/lib/format';
import { ignoreShortcut, isEnter, MOD_KEY } from '@/lib/keys';
import {
  effective, idsKey, isSelected, lineSingers, mixBackground, parseCombo, singerLabel, union, usage, wordRange, wordsOf,
  type Selection, type Word,
} from '@/lib/singers';
import type { KaraokeSingers, KaraokeStyle, Line } from '@/lib/types';
import { player } from '@/audio/player';
import { revealOnWaveform } from '@/audio/waveformRef';
import { run, setStep, toast, useActiveResult, useApp, useProject } from '@/store/app';
import { assignSingers, flushSingers, removeSinger, saveSingers } from '@/store/singers';
import { Badge, Button, Callout, Card, CardBody, CardHeader, Input, Kbd, PageHeader, Segmented } from '@/components/ui';
import { MarkersDialog } from './singers/MarkersDialog';
import { SingerList } from './singers/SingerList';

const NO_SINGERS: KaraokeSingers = { members: [], mix: 'split', direction: 'vertical' };

interface Row { line: Line; index: number; words: Word[] }

let keySeq = 0;

export function SingersPage() {
  const project = useProject()!;
  const pid = project.id;
  const markers = useApp((s) => s.pv?.view.singer_markers ?? 0);
  const [style, setStyle] = useState<KaraokeStyle | null>(project.karaoke ?? null);
  const [markersOpen, setMarkersOpen] = useState(false);

  useEffect(() => {
    let stop = false;
    void api.get<KaraokeStyle>(`/api/projects/${pid}/karaoke`)
      .then((k) => { if (!stop) setStyle(k); })
      .catch(() => { if (!stop) setStyle((cur) => cur ?? project.karaoke ?? null); });
    return () => { stop = true; };
  }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { void flushSingers(); }, []);

  const singers = style?.singers ?? NO_SINGERS;
  const members = singers.members;
  const changeSingers = (next: KaraokeSingers) => {
    setStyle((s) => (s ? { ...s, singers: next } : s));
    saveSingers(pid, next);
  };
  const remove = (n: number) => run(async () => {
    const pv = await removeSinger(pid, n);
    if (pv.project.karaoke?.singers) setStyle((s) => (s ? { ...s, singers: pv.project.karaoke!.singers! } : s));
    toast('ok', `已删除${singerLabel(members, n)}`, pv.changed ? `${pv.changed} 行的指定已随之调整` : undefined);
  }, '删除演唱者失败');
  const afterMarkers = () => {
    const k = useApp.getState().pv?.project.karaoke;
    if (k?.singers) setStyle((s) => (s ? { ...s, singers: k.singers! } : s));
  };

  // ------------------------------------------------------------------ rows and selection

  const rows: Row[] = useMemo(() => project.lyrics.lines
    .map((line, i) => ({ line, index: i + 1, words: wordsOf(line) }))
    .filter((r) => r.line.kind === 'lyric' && r.line.sing), [project.lyrics.lines]);
  const [sel, setSel] = useState<Selection>(new Map());
  const anchor = useRef<[number, number] | null>(null);
  const lineAnchor = useRef<number | null>(null);
  const drag = useRef<{ from: [number, number]; base: Selection } | null>(null);
  // the last assignment by key (1 → + → 2 makes it 1+2, one undo step)
  const [combo, setCombo] = useState<{ ids: number[]; key: string; plus: boolean } | null>(null);

  // a selection of lines that no longer exist (merged, split, new lyrics) is dropped
  useEffect(() => {
    setSel((s) => {
      const ids = new Set(rows.map((r) => r.line.id));
      const texts = new Map(rows.map((r) => [r.line.id, r.line.text.length]));
      const next: Selection = new Map([...s].filter(([id, r]) => ids.has(id) && r.every(([, b]) => b <= texts.get(id)!)));
      return next.size === s.size ? s : next;
    });
  }, [rows]);

  const select = (next: Selection) => { setSel(next); setCombo(null); };
  const wholeLine = (r: Row): Selection => new Map([[r.line.id, [[0, r.line.text.length]]]]);

  const clickLine = (li: number, e: ReactMouseEvent) => {
    const r = rows[li];
    const mod = e.metaKey || e.ctrlKey;
    if (e.shiftKey && lineAnchor.current !== null) {
      const [a, b] = [Math.min(lineAnchor.current, li), Math.max(lineAnchor.current, li)];
      let next: Selection = mod ? new Map(sel) : new Map();
      for (let i = a; i <= b; i++) next = union(next, wholeLine(rows[i]));
      select(next);
      return;
    }
    lineAnchor.current = li;
    anchor.current = [li, 0];
    if (mod) {
      const next = new Map(sel);
      const full = (sel.get(r.line.id) ?? []).some(([x, y]) => x <= 0 && y >= r.line.text.length);
      if (full) next.delete(r.line.id); else next.set(r.line.id, [[0, r.line.text.length]]);
      select(next);
    } else {
      select(wholeLine(r));
    }
  };

  const downOnWord = (li: number, wi: number, e: ReactMouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const mod = e.metaKey || e.ctrlKey;
    if (e.shiftKey && anchor.current) {
      select(union(mod ? sel : new Map(), wordRange(rows, anchor.current, [li, wi])));
      return;
    }
    anchor.current = [li, wi];
    lineAnchor.current = li;
    drag.current = { from: [li, wi], base: mod ? sel : new Map() };
    select(union(drag.current.base, wordRange(rows, [li, wi], [li, wi])));
  };

  useEffect(() => {
    const move = (e: MouseEvent) => {
      const d = drag.current;
      if (!d) return;
      const el = (document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null)?.closest<HTMLElement>('[data-word]');
      if (!el) return;
      const [li, wi] = el.dataset.word!.split(':').map(Number);
      setSel(union(d.base, wordRange(rows, d.from, [li, wi])));
    };
    const up = () => { drag.current = null; };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, [rows]);

  // ------------------------------------------------------------------ assigning

  const label = (ids: number[]) => (ids.length ? ids.map((n) => singerLabel(members, n)).join(' + ') : '默认配色');
  const apply = useCallback((ids: number[], key?: string) => {
    if (!sel.size) {
      toast('info', '先选中歌词', '点行号选整行，或在歌词上拖动选择几个词');
      return;
    }
    void assignSingers(sel, ids, ids.length ? `指定 ${label(ids)}` : '清除演唱者', key);
  }, [sel, members]); // eslint-disable-line react-hooks/exhaustive-deps

  const pressNumber = (n: number) => {
    if (n > members.length) {
      toast('info', members.length ? `只有 ${members.length} 位演唱者` : '还没有演唱者', '先在右边添加演唱者');
      return;
    }
    if (combo?.plus) {
      const ids = combo.ids.includes(n) ? combo.ids : [...combo.ids, n];
      setCombo({ ids, key: combo.key, plus: false });
      apply(ids, combo.key);
      return;
    }
    const key = `k${++keySeq}`;
    setCombo({ ids: [n], key, plus: false });
    apply([n], key);
  };

  // ------------------------------------------------------------------ listening / preview times

  const result = useActiveResult();
  const times = useMemo(() => new Map((result?.units ?? []).filter((u) => u.start_ms !== null && u.end_ms !== null)
    .map((u) => [u.unit_id, [u.start_ms!, u.end_ms!] as [number, number]])), [result]);
  const selTimes = useMemo((): [number, number] | null => {
    let a = Infinity;
    let b = -Infinity;
    for (const r of rows) {
      const ranges = sel.get(r.line.id);
      if (!ranges) continue;
      let pos = 0;
      for (const seg of r.line.segments) {
        const s0 = pos;
        pos += seg.surface.length;
        if (!ranges.some(([x, y]) => x < pos && s0 < y)) continue;
        for (const u of seg.units) {
          const t = times.get(u.id);
          if (t) { a = Math.min(a, t[0]); b = Math.max(b, t[1]); }
        }
      }
    }
    return a < b ? [a, b] : null;
  }, [sel, rows, times]);
  const listen = () => {
    if (!selTimes) {
      toast('info', result ? '选中的部分还没有时间' : '还没有对齐结果', result ? undefined : '对齐后可以试听和预览');
      return;
    }
    revealOnWaveform(selTimes[0], selTimes[1]);
    player.playRange(selTimes[0], selTimes[1], { loop: true, padMs: 300 });
  };

  // ------------------------------------------------------------------ keyboard

  const keys = useRef({ pressNumber, apply, combo, rows, listen });
  keys.current = { pressNumber, apply, combo, rows, listen };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (ignoreShortcut(e)) return;
      const k = keys.current;
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'a') {
        e.preventDefault();
        let all: Selection = new Map();
        for (const r of k.rows) all = union(all, new Map([[r.line.id, [[0, r.line.text.length]]]]));
        setSel(all);
        setCombo(null);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (/^[1-9]$/.test(e.key)) {
        e.preventDefault();
        k.pressNumber(Number(e.key));
      } else if (e.key === '+' || e.key === '=' || e.code === 'NumpadAdd') {
        if (k.combo) {
          e.preventDefault();
          setCombo({ ...k.combo, plus: true });
        }
      } else if (e.key === '0' || e.key === 'Backspace' || e.key === 'Delete') {
        e.preventDefault();
        setCombo(null);
        k.apply([]);
      } else if (e.key === 'Escape') {
        setSel(new Map());
        setCombo(null);
      } else if (e.key.toLowerCase() === 'p') {
        e.preventDefault();
        k.listen();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // ------------------------------------------------------------------ view

  const [comboText, setComboText] = useState('');
  const applyComboText = () => {
    const ids = parseCombo(comboText, members.length);
    if (!ids.length) {
      toast('info', '请输入演唱者编号', `如 1+2（现有 ${members.length} 位）`);
      return;
    }
    apply(ids);
    setComboText('');
  };
  const nLines = [...sel.values()].length;
  const full = rows.filter((r) => (sel.get(r.line.id) ?? []).some(([a, b]) => a <= 0 && b >= r.line.text.length)).length;
  const summary = !nLines ? '未选择：点行号选整行，或在歌词上拖动选择几个词（可以跨行）'
    : full === nLines ? `已选 ${nLines} 行` : `已选 ${nLines} 行中的部分歌词`;
  const colorOf = (n: number) => members[n - 1]?.color;
  // the side column's height: what is visible of the scrolling page (above the dock)
  const side = useRef<HTMLDivElement>(null);
  const [sideMax, setSideMax] = useState<number | null>(null);
  useEffect(() => {
    const main = side.current?.closest('main');
    if (!main || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setSideMax(main.clientHeight - 32));
    ro.observe(main);
    return () => ro.disconnect();
  }, []);
  const assigned = rows.filter((r) => (r.line.singers?.length ?? 0) || (r.line.singer_spans?.length ?? 0)).length;

  return (
    <>
      <PageHeader
        eyebrow="第 7 步（可选）"
        title="演唱者"
        description="多人演唱时，给每位歌手不同的字幕颜色：先在右边添加演唱者，再选中歌词按数字键指定。几个人一起唱的部分，每个字分成几种颜色（上下或左右，也可以渐变）。单人演唱的歌曲不需要这一步。"
        actions={<Button onClick={() => setStep('karaoke')} icon={<ArrowRight className="size-4" />}>下一步：卡拉OK字幕</Button>}
      />
      {markers > 0 && (
        <Callout tone="info" className="mb-4" title={`有 ${markers} 行歌词开头写着演唱者（如「A：」「【成员】」）`}
          actions={<Button size="sm" variant="secondary" icon={<Tags className="size-4" />} onClick={() => setMarkersOpen(true)}>识别并指定…</Button>}>
          可以按这些名字自动建立演唱者、给这些行指定，并把名字从歌词里去掉。
        </Callout>
      )}
      <MarkersDialog open={markersOpen} onOpenChange={setMarkersOpen} onApplied={afterMarkers} />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(300px,340px)] xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card className="min-w-0">
          <div className="sticky top-0 z-10 space-y-2.5 rounded-t-[var(--radius-card)] border-b border-line bg-surface px-5 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 text-[13px] text-muted" role="status">{summary}</span>
              {combo?.plus && <Badge tone="accent">{combo.ids.join('+')}+ …</Badge>}
              <Button size="xs" variant="ghost" icon={<Play className="size-3.5" />} onClick={listen} disabled={!nLines}>试听</Button>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {members.map((m, i) => (
                <Button key={i} size="xs" variant="outline" disabled={!nLines} onClick={() => pressNumber(i + 1)}
                  title={`指定为 ${singerLabel(members, i + 1)}（按 ${i + 1}）`}>
                  <span className="grid size-4 place-items-center rounded text-[10px] font-bold text-white" style={{ background: m.color }}>{i + 1}</span>
                  <span className="max-w-24 truncate">{singerLabel(members, i + 1)}</span>
                </Button>
              ))}
              {members.length > 1 && (
                <span className="inline-flex items-center gap-1">
                  <Input className="h-7 w-20 text-xs" placeholder="如 1+2" value={comboText} aria-label="一起唱的演唱者"
                    onChange={(e) => setComboText(e.target.value)} onKeyDown={(e) => { if (isEnter(e)) applyComboText(); }} />
                  <Button size="xs" variant="outline" disabled={!nLines || !comboText.trim()} onClick={applyComboText}>一起唱</Button>
                </span>
              )}
              <Button size="xs" variant="ghost" icon={<Eraser className="size-3.5" />} disabled={!nLines} onClick={() => apply([])}>清除</Button>
            </div>
          </div>
          <CardBody className="space-y-0.5 px-3">
            <p className="px-2 pb-2 text-xs leading-5 text-subtle">
              <Kbd>1</Kbd>–<Kbd>9</Kbd> 指定 · 先按 <Kbd>1</Kbd> 再按 <Kbd>+</Kbd> <Kbd>2</Kbd> 为 1+2 一起唱 · <Kbd>0</Kbd> 清除 ·
              {' '}<Kbd>P</Kbd> 试听 · <Kbd>Esc</Kbd> 取消选择 · <Kbd>{MOD_KEY}</Kbd>+<Kbd>A</Kbd> 全选 · <Kbd>{MOD_KEY}</Kbd>+<Kbd>Z</Kbd> 撤销；
              行号 Shift / {MOD_KEY} 点选多行，在歌词上按住 {MOD_KEY} 拖动可以追加
            </p>
            {rows.length === 0 && <p className="px-2 py-6 text-center text-sm text-muted">还没有歌词</p>}
            {rows.map((r, li) => (
              <LyricRow key={r.line.id} row={r} li={li} sel={sel} colorOf={colorOf} singers={singers}
                onLine={clickLine} onWord={downOnWord} names={(ids) => label(ids)} />
            ))}
          </CardBody>
        </Card>

        {/* sticky beside the lyrics, scrolling on its own when taller than the visible part of the page */}
        <div ref={side} className="min-w-0 space-y-6 lg:sticky lg:top-4 lg:overflow-y-auto lg:overscroll-contain"
          style={sideMax ? { maxHeight: sideMax } : undefined}>
          <PreviewCard style={style} times={selTimes} hasResult={!!result} refresh={`${assigned}-${JSON.stringify(project.lyrics.lines.map((l) => [l.singers, l.singer_spans]))}`} />
          {style && (
            <SingerList singers={singers} onChange={changeSingers} onRemove={(n) => void remove(n)} glow={style.glow.enabled}
              usage={(n) => usage(project.lyrics.lines, n)} />
          )}
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ one line of the lyrics

function LyricRow({ row, li, sel, colorOf, singers, onLine, onWord, names }: {
  row: Row; li: number; sel: Selection; colorOf: (n: number) => string | undefined; singers: KaraokeSingers;
  onLine: (li: number, e: ReactMouseEvent) => void; onWord: (li: number, wi: number, e: ReactMouseEvent) => void;
  names: (ids: number[]) => string;
}) {
  const { line, words, index } = row;
  const chars = effective(lineSingers(line));
  const own = (line.singers ?? []).filter((n) => colorOf(n));
  const full = (sel.get(line.id) ?? []).some(([a, b]) => a <= 0 && b >= line.text.length);
  const paint = (ids: number[]) => {
    const cols = ids.map(colorOf).filter((c): c is string => !!c);
    if (!cols.length) return undefined;
    if (cols.length === 1) return { color: cols[0] };
    return { backgroundImage: mixBackground(cols, singers.mix, singers.direction), WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' };
  };
  return (
    <div className={cn('group flex scroll-mt-32 items-start gap-2 rounded-lg px-2 py-1', full ? 'bg-accent-soft' : 'hover:bg-surface-2/60')}>
      <button type="button" onClick={(e) => onLine(li, e)} aria-pressed={full} aria-label={`选择第 ${index} 行`}
        className="focus-ring mt-1 w-8 shrink-0 rounded text-right font-mono text-xs text-subtle hover:text-fg">
        {index}
      </button>
      <span aria-hidden className="mt-1.5 h-5 w-1.5 shrink-0 rounded-full"
        style={{ background: own.length ? mixBackground(own.map((n) => colorOf(n)!), singers.mix, 'vertical') : 'var(--color-line)' }} />
      <div className="min-w-0 flex-1 cursor-text text-[17px] leading-8 font-medium select-none">
        {words.map((w, wi) => {
          // runs of characters with the same singers (a word normally has one)
          const runs: { a: number; b: number; ids: number[] }[] = [];
          for (let i = w.start; i < w.end; i++) {
            const last = runs[runs.length - 1];
            if (last && idsKey(last.ids) === idsKey(chars[i] ?? [])) last.b = i + 1;
            else runs.push({ a: i, b: i + 1, ids: chars[i] ?? [] });
          }
          const picked = isSelected(sel, line.id, w.start, w.end) && !full;
          return (
            <span key={wi} data-word={`${li}:${wi}`} onMouseDown={(e) => onWord(li, wi, e)}
              className={cn('rounded-[3px] py-0.5', picked && 'bg-accent/20 ring-1 ring-accent/50')}>
              {runs.map((r, ri) => {
                const text = line.text.slice(r.a, r.b);
                const style = paint(r.ids);
                if (r.ids.length > 1 && singers.direction === 'horizontal') {
                  // side by side inside each character
                  return <span key={ri}>{[...text].map((ch, ci) => <span key={ci} style={style}>{ch}</span>)}</span>;
                }
                return <span key={ri} style={style}>{text}</span>;
              })}
            </span>
          );
        })}
      </div>
      {own.length > 0 && <span className="mt-1.5 max-w-40 shrink-0 truncate text-xs text-subtle">{names(own)}</span>}
    </div>
  );
}

// ------------------------------------------------------------------ preview

function PreviewCard({ style, times, hasResult, refresh }: {
  style: KaraokeStyle | null; times: [number, number] | null; hasResult: boolean; refresh: string;
}) {
  const pid = useApp((s) => s.pid);
  const [when, setWhen] = useState<'mid' | 'before' | 'after'>('mid');
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const t = times ? (when === 'before' ? times[0] - 200 : when === 'after' ? times[1] + 150 : Math.round((times[0] + times[1]) / 2)) : null;
  const look = JSON.stringify({ ...style, output: undefined });

  useEffect(() => {
    if (!style || t === null || !pid) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await fetch(`/api/projects/${pid}/karaoke/preview`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ style, t_ms: Math.max(0, t), background: 'auto' }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        setUrl((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(blob); });
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 400);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [look, t, refresh, pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const pic = useApp((s) => s.pv?.view.picture);
  return (
    <Card>
      <CardHeader icon={<Subtitles className="size-4" />} title="预览"
        actions={<Segmented size="sm" label="预览时刻" value={when} onChange={setWhen}
          options={[{ value: 'before', label: '唱之前' }, { value: 'mid', label: '唱到一半' }, { value: 'after', label: '唱完' }]} />} />
      <CardBody className="space-y-2">
        {!hasResult ? (
          <p className="text-[13px] text-muted">对齐后可以在这里预览字幕效果（指定演唱者不需要对齐结果）。</p>
        ) : t === null ? (
          <p className="text-[13px] text-muted"><Users className="mr-1 inline size-4" />选中歌词后，这里显示它唱到时的画面。</p>
        ) : (
          <div className="relative overflow-hidden rounded-xl bg-black ring-1 ring-line" style={{ aspectRatio: `${pic?.width ?? 1920} / ${pic?.height ?? 1080}` }}>
            {url && <img src={url} alt={`${fmtMs(t)} 的字幕预览`} className="absolute inset-0 size-full object-contain" />}
            {loading && (
              <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-black/60 px-2 py-0.5 text-xs text-white/80">
                <Loader2 className="size-3.5 animate-spin" />渲染中
              </div>
            )}
            {error && <div className="absolute inset-x-2 bottom-2 rounded-lg bg-danger/90 px-2 py-1 text-xs text-white">{error}</div>}
            <div className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 font-mono text-[11px] text-white/85">{fmtMs(t)}</div>
          </div>
        )}
        <p className="text-xs text-subtle">与烧录使用同一渲染器；其他样式在“卡拉OK字幕”里调整。</p>
      </CardBody>
    </Card>
  );
}
