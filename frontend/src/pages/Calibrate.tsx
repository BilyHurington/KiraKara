// Step 4 (LRC mode): first-onset calibration of the global shift.
//
// base_i = imported_start_i + embedded_shift, effective_i = base_i + user_shift.
// Marking line k at marked_ms recomputes user_shift = marked_ms - base_k
// (never accumulated). Audio is never moved or trimmed.

import {
  ArrowRight, ChevronLeft, ChevronRight, Crosshair, Flag, MapPin, PlayCircle, RotateCcw, Timer, Undo2,
} from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { api } from '@/lib/api';
import { cn, fmtMs, fmtSigned } from '@/lib/format';
import type { Line, ProjectView } from '@/lib/types';
import { player } from '@/audio/player';
import { revealOnWaveform } from '@/audio/waveformRef';
import { ppath, run, setPV, setStep, toast, useApp, useProject, useView } from '@/store/app';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, Kbd, KV, NumberInput, PageHeader, Tip,
} from '@/components/ui';

export function CalibratePage() {
  const project = useProject()!;
  if (project.mode !== 'lrc') return <PlainModeNotice />;
  return <Calibration />;
}

function PlainModeNotice() {
  const switchMode = () => run(async () => {
    setPV(await api.patch<ProjectView>(ppath(''), { mode: 'lrc' }));
    toast('ok', '已切换到 LRC 增强模式', '输入与人工修改均已保留');
  });
  return (
    <>
      <PageHeader eyebrow="第 4 步" title="首音校准" description="仅在 LRC 增强模式下使用：用 LRC 的行时间作为锚点前，先校准全局偏移。" />
      <EmptyState
        icon={<Timer className="size-5" />}
        title="普通模式不需要校准"
        description="普通模式不使用任何外部时间锚点，直接对整段已知歌词做有序对齐。"
        action={
          <div className="flex flex-wrap justify-center gap-2">
            <Button onClick={switchMode}>切换到 LRC 增强模式</Button>
            <Button variant="primary" onClick={() => setStep('align')} icon={<ArrowRight className="size-4" />}>去对齐</Button>
          </div>
        }
      />
    </>
  );
}

function Calibration() {
  const project = useProject()!;
  const view = useView()!;
  const calibLineId = useApp((s) => s.calibLineId);
  const cal = project.calibration;
  const doc = project.lyrics;

  const sung = useMemo(() => doc.lines.filter((l) => l.sing && l.kind === 'lyric'), [doc.lines]);
  const timed = useMemo(() => sung.filter((l) => view.effective_starts[l.id]), [sung, view.effective_starts]);
  const lineNo = useMemo(() => new Map(doc.lines.map((l, i) => [l.id, i + 1])), [doc.lines]);

  // default selection: reference line, else first sung line with a time
  useEffect(() => {
    if (!calibLineId || !sung.some((l) => l.id === calibLineId)) {
      const def = (cal.reference_line_id && sung.find((l) => l.id === cal.reference_line_id)) || timed[0] || sung[0];
      if (def) useApp.setState({ calibLineId: def.id });
    }
  }, [calibLineId, sung, timed, cal.reference_line_id]);

  const line = sung.find((l) => l.id === calibLineId) ?? null;
  const eff = line ? view.effective_starts[line.id] : undefined;

  const post = (path: string, body?: unknown, okMsg?: (pv: ProjectView) => string) => run(async () => {
    const pv = await api.post<ProjectView>(ppath(path), body);
    setPV(pv);
    if (okMsg) toast('ok', okMsg(pv));
  });

  const mark = () => {
    if (!line) return;
    if (!player.durationMs) {
      toast('info', '请先上传并加载音频');
      return;
    }
    const ms = Math.round(player.positionMs());
    void post('/calibration/mark', { line_id: line.id, marked_ms: ms },
      (pv) => `已标记 ${fmtMs(ms)} · 全局平移 ${fmtSigned(pv.project.calibration.user_shift_ms)}`);
  };

  const setShift = (v: number) => post('/calibration/shift', { user_shift_ms: Math.round(v) },
    (pv) => `全局平移 ${fmtSigned(pv.project.calibration.user_shift_ms)}`);

  // keyboard M = mark at playhead
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key.toLowerCase() === 'm') {
        e.preventDefault();
        mark();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const select = (l: Line | undefined) => {
    if (!l) return;
    useApp.setState({ calibLineId: l.id });
    const st = view.effective_starts[l.id];
    if (st) revealOnWaveform(Math.max(0, st.ms - 1500), st.ms + 3000);
  };
  const idx = line ? sung.indexOf(line) : -1;
  const quick = (where: 'first' | 'mid' | 'last') => {
    const pool = timed.length ? timed : sung;
    if (!pool.length) return;
    select(where === 'first' ? pool[0] : where === 'last' ? pool[pool.length - 1] : pool[Math.floor(pool.length / 2)]);
  };

  const playBefore = () => {
    if (!eff) return;
    const from = Math.max(0, eff.ms - 2000);
    revealOnWaveform(from, eff.ms + 3000);
    player.play(from);
  };

  const noTimes = timed.length === 0;

  return (
    <>
      <PageHeader
        eyebrow="第 4 步 · LRC 增强"
        title="首音校准"
        description="选择一句歌词，在它第一处实际发音的位置标记，程序据此计算全局偏移。音频不会被移动或剪掉前奏，只计算歌词锚点。"
        actions={<Button variant="primary" onClick={() => setStep('align')} icon={<ArrowRight className="size-4" />}>下一步：对齐</Button>}
      />

      {noTimes && (
        <Callout tone="danger" title="歌词没有有效的行时间" className="mb-6"
          actions={<Button size="sm" onClick={() => setStep('input')}>去补充时间</Button>}>
          LRC 增强模式需要带行时间的歌词。请导入带时间的 LRC，或切换到普通模式；程序不会静默降级。
        </Callout>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader
              icon={<Crosshair className="size-4" />}
              title="标记首个发音"
              description="默认使用首条演唱歌词；也可以选择任意更清楚的句子作为参考。"
            />
            <CardBody className="space-y-5">
              <Callout tone="info" title="标记的是所选歌词的第一处实际发音">
                不是歌曲的第一声，也不是字幕提前出现的时刻。可放大波形、慢速（0.5×）试听或在波形上拖选区间循环。
              </Callout>

              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" variant="ghost" disabled={idx <= 0} onClick={() => select(sung[idx - 1])} icon={<ChevronLeft className="size-4" />}>上一行</Button>
                <div className="min-w-0 flex-1 rounded-xl border border-line bg-surface-2/60 px-4 py-3">
                  {line ? (
                    <div className="flex items-center gap-3">
                      <span className="tabular grid size-7 shrink-0 place-items-center rounded-lg bg-accent-soft text-xs font-semibold text-accent">{lineNo.get(line.id)}</span>
                      <span className="min-w-0 flex-1 truncate text-[15px] font-medium">{line.text}</span>
                      <span className="tabular shrink-0 font-mono text-sm text-muted">{eff ? fmtMs(eff.ms) : '无时间'}</span>
                      {eff?.kind === 'hard' && <Badge tone="ok">人工锚点</Badge>}
                    </div>
                  ) : <span className="text-sm text-muted">没有参与对齐的歌词行</span>}
                </div>
                <Button size="sm" variant="ghost" disabled={idx < 0 || idx >= sung.length - 1} onClick={() => select(sung[idx + 1])}>
                  下一行<ChevronRight className="size-4" />
                </Button>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted">快速选择</span>
                <Button size="xs" variant="outline" onClick={() => quick('first')}>首句</Button>
                <Button size="xs" variant="outline" onClick={() => quick('mid')}>中段</Button>
                <Button size="xs" variant="outline" onClick={() => quick('last')}>末段</Button>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <Button variant="outline" onClick={playBefore} disabled={!eff} icon={<PlayCircle className="size-4" />}>
                  从有效句首前 2 秒播放
                </Button>
                <Button variant="primary" size="lg" onClick={mark} disabled={!line || noTimes} icon={<Flag className="size-4" />}>
                  在播放头标记首个发音 <Kbd>M</Kbd>
                </Button>
              </div>

              <div className="rounded-xl border border-line p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="text-[13px] font-medium">数值微调全局平移</div>
                    <div className="text-xs text-muted">正值表示歌词后移；每次标记都会重新计算，不会叠加旧值</div>
                  </div>
                  <Badge tone={cal.confirmed ? 'ok' : 'warn'} dot>{cal.confirmed ? '已确认' : '未确认'}</Badge>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {[-50, -10].map((d) => (
                    <Button key={d} size="sm" variant="secondary" onClick={() => setShift(cal.user_shift_ms + d)}>{d} ms</Button>
                  ))}
                  <NumberInput className="w-32" value={cal.user_shift_ms} suffix="ms" onCommit={(v) => v !== null && setShift(v)} />
                  {[10, 50].map((d) => (
                    <Button key={d} size="sm" variant="secondary" onClick={() => setShift(cal.user_shift_ms + d)}>+{d} ms</Button>
                  ))}
                  <span className="mx-1 h-6 w-px bg-line" />
                  <Tip content="确认当前 LRC 时间无需平移（零偏移也需要明确确认）">
                    <Button size="sm" variant="soft" onClick={() => post('/calibration/confirm-zero', undefined, () => '已确认零偏移')} icon={<RotateCcw className="size-3.5" />}>
                      确认零偏移
                    </Button>
                  </Tip>
                  <Button size="sm" variant="ghost" disabled={!cal.history.length}
                    onClick={() => post('/calibration/undo', undefined, (pv) => `已撤销 · 平移 ${fmtSigned(pv.project.calibration.user_shift_ms)}`)}
                    icon={<Undo2 className="size-3.5" />}>
                    撤销
                  </Button>
                </div>
              </div>
            </CardBody>
          </Card>

          <FormulaCard line={line} />
          <ChecksCard line={line} lineNo={lineNo} />
        </div>

        <LinePicker sung={sung} lineNo={lineNo} selected={calibLineId} onSelect={select} />
      </div>

    </>
  );
}

function FormulaCard({ line }: { line: Line | null }) {
  const project = useProject()!;
  const view = useView()!;
  const cal = project.calibration;
  const doc = project.lyrics;
  const raw = line?.imported_start_ms ?? null;
  const base = raw === null ? null : raw + doc.embedded_shift_ms;
  const eff = line ? view.effective_starts[line.id] : undefined;
  const refLine = cal.reference_line_id ? doc.lines.find((l) => l.id === cal.reference_line_id) : null;

  return (
    <Card>
      <CardHeader title="时间计算" description="base = 原始句首 + 内嵌平移；effective = base + 人工全局平移" />
      <CardBody className="space-y-3">
        <KV rows={[
          ['原始句首（LRC 中写的时间）', fmtMs(raw)],
          ['内嵌 [offset]', doc.embedded_offset_raw
            ? <span>{doc.embedded_offset_raw} <span className="text-muted">→ 规范化平移</span> {fmtSigned(doc.embedded_shift_ms)} <Badge tone="info" className="ml-1">只应用一次</Badge></span>
            : <span className="text-muted">无</span>,
          doc.embedded_offset_note || undefined],
          ['base', fmtMs(base)],
          ['人工全局平移 user_shift', fmtSigned(cal.user_shift_ms), '正值表示歌词后移'],
          ['有效句首 effective', <span className="text-accent">{fmtMs(eff?.ms)}</span>,
            line?.anchor ? '该行设置了人工绝对锚点（原音频时间），不随全局平移移动' : undefined],
          ['校准状态', cal.confirmed ? <Badge tone="ok">已确认</Badge> : <Badge tone="warn">未确认</Badge>,
            refLine ? `参考行：${refLine.text}${cal.marked_ms !== null ? `，标记于 ${fmtMs(cal.marked_ms)}` : ''}` : undefined],
        ]} />
        <p className="text-xs text-subtle">例：原句首 12,300 标在 12,950 得 +650；改标 13,000 得 +700（重新计算，不叠加）。</p>
      </CardBody>
    </Card>
  );
}

function ChecksCard({ line, lineNo }: { line: Line | null; lineNo: Map<string, number> }) {
  const project = useProject()!;
  const view = useView()!;
  const cal = project.calibration;
  const byId = new Map(project.lyrics.lines.map((l) => [l.id, l]));

  const addCheck = () => {
    if (!line) return;
    const ms = Math.round(player.positionMs());
    void run(async () => {
      const pv = await api.post<ProjectView>(ppath('/calibration/check'), { line_id: line.id, marked_ms: ms });
      setPV(pv);
      const c = pv.project.calibration.checks.find((x) => x.line_id === line.id);
      toast(c && Math.abs(c.residual_ms) > 250 ? 'warn' : 'ok', `已添加检查点 ${fmtMs(ms)}`, c ? `残差 ${fmtSigned(c.residual_ms)}` : undefined);
    });
  };

  return (
    <Card>
      <CardHeader
        icon={<MapPin className="size-4" />}
        title="中段 / 末段检查"
        description="单点只能确定整体平移。在中段、末段句子的首个发音处添加检查点，核对整曲是否一致。"
        actions={<Button size="sm" onClick={addCheck} disabled={!line}>以播放头为所选行添加检查点</Button>}
      />
      <CardBody className="space-y-3">
        {cal.checks.length === 0 ? (
          <p className="text-[13px] text-muted">暂无检查点。</p>
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line">
            {cal.checks.map((c) => {
              const ok = Math.abs(c.residual_ms) <= 250;
              return (
                <li key={`${c.line_id}-${c.marked_ms}`} className="flex items-center gap-3 px-4 py-2.5 text-[13px]">
                  <span className="tabular w-8 text-xs text-muted">#{lineNo.get(c.line_id)}</span>
                  <span className="min-w-0 flex-1 truncate">{byId.get(c.line_id)?.text ?? c.line_id}</span>
                  <span className="tabular font-mono text-xs text-muted">{fmtMs(c.marked_ms)}</span>
                  <Badge tone={ok ? 'ok' : 'warn'}>残差 {fmtSigned(c.residual_ms)}</Badge>
                </li>
              );
            })}
          </ul>
        )}
        {view.calibration_issues.map((i, k) => (
          <Callout key={k} tone={i.severity === 'error' ? 'danger' : i.severity === 'warning' ? 'warn' : 'info'}>{i.message}</Callout>
        ))}
        <p className="text-xs leading-5 text-subtle">
          中段 / 末段残差仍然较大时，可能是歌词版本或速度与音频不一致：可在“音频与歌词”页为个别行添加单行锚点。程序不会自动拉伸整曲时间。
        </p>
      </CardBody>
    </Card>
  );
}

function LinePicker({ sung, lineNo, selected, onSelect }: {
  sung: Line[]; lineNo: Map<string, number>; selected: string | null; onSelect: (l: Line) => void;
}) {
  const view = useView()!;
  const cal = useProject()!.calibration;
  const checked = new Set(cal.checks.map((c) => c.line_id));
  return (
    <Card className="self-start lg:sticky lg:top-4">
      <CardHeader title="歌词行" description={`${sung.length} 行参与对齐`} />
      <ul className="max-h-[60vh] overflow-y-auto p-2">
        {sung.map((l) => {
          const st = view.effective_starts[l.id];
          const on = l.id === selected;
          return (
            <li key={l.id}>
              <button
                onClick={() => onSelect(l)}
                className={cn(
                  'focus-ring flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition',
                  on ? 'bg-accent-soft' : 'hover:bg-surface-2',
                )}
              >
                <span className={cn('tabular w-6 shrink-0 text-right text-xs', on ? 'font-semibold text-accent' : 'text-subtle')}>{lineNo.get(l.id)}</span>
                <span className={cn('min-w-0 flex-1 truncate text-[13px]', on && 'font-medium text-accent')}>{l.text}</span>
                {l.id === cal.reference_line_id && <Tip content="参考行"><Flag className="size-3.5 shrink-0 text-accent" /></Tip>}
                {checked.has(l.id) && <Tip content="已添加检查点"><MapPin className="size-3.5 shrink-0 text-info" /></Tip>}
                <span className={cn('tabular shrink-0 font-mono text-[11px]', st ? 'text-muted' : 'text-danger')}>{st ? fmtMs(st.ms) : '无时间'}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
