// Right column: timing summary of the selected line and its unit table.

import { Lock, LockOpen, Play, RefreshCw, RotateCcw } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { player } from '@/audio/player';
import { revealOnWaveform } from '@/audio/waveformRef';
import { cn, fmtMs, fmtSigned, ROLE_LABEL, STATUS_LABEL } from '@/lib/format';
import type { AlignmentResult, UnitTiming } from '@/lib/types';
import { toast, useApp } from '@/store/app';
import { clearUnitManual, setUnitLock, setUnitTimes } from '@/store/edits';
import { Badge, Button, IconButton, NumberInput, Table, Td, Th, Tip } from '@/components/ui';
import { flagHelp, flagLabel, unitsRange, type LineStats, type UnitInfo } from './helpers';

const STATUS_TONE = { ok: 'ok', failed: 'danger', unaligned: 'warn', skipped: 'neutral' } as const;

export function playUnit(u: UnitTiming) {
  if (u.start_ms === null || u.end_ms === null) {
    toast('info', '该单元没有时间', u.reason ?? '模型未给出区间；可以手动填写起止时间');
    return;
  }
  useApp.setState({ selUnitId: u.unit_id, selLineId: u.line_id });
  revealOnWaveform(u.start_ms, u.end_ms);
  player.playRange(u.start_ms, u.end_ms, { loop: true, padMs: 150 });
}

export function LineDetail({ stat, result, info, selUnitId, onRerun, rerunBusy, contextTexts }: {
  stat: LineStats;
  result: AlignmentResult;
  info: Map<string, UnitInfo>;
  selUnitId: string | null;
  onRerun: () => void;
  rerunBusy: boolean;
  contextTexts: Map<string, string>;
}) {
  const lt = result.lines.find((l) => l.line_id === stat.line.id);
  const editable = !result.stale;
  const range = unitsRange(stat.units);
  const tbodyRef = useRef<HTMLTableSectionElement>(null);

  useEffect(() => {
    tbodyRef.current?.querySelector<HTMLElement>(`[data-unit="${selUnitId}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [selUnitId]);

  const playLine = () => {
    if (!range) return toast('info', '该行没有可试听的时间');
    revealOnWaveform(range[0], range[1]);
    player.playRange(range[0], range[1], { loop: true, padMs: 250 });
  };

  const commit = (u: UnitTiming, which: 'start' | 'end', v: number | null) => {
    const start = which === 'start' ? v : u.start_ms;
    const end = which === 'end' ? v : u.end_ms;
    if (start !== null && end !== null && end <= start) {
      toast('error', '结束时间必须晚于开始时间', '区间为 [start, end)');
      return;
    }
    void setUnitTimes(u.unit_id, start, end, result.id);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs text-muted">第 {stat.index + 1} 行</div>
          <div className="mt-0.5 text-lg leading-7 font-semibold">{stat.line.text}</div>
          {stat.line.translation && <div className="text-[13px] text-muted">{stat.line.translation}</div>}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="soft" icon={<Play className="size-3.5 fill-current" />} onClick={playLine} disabled={!range}>循环试听整行</Button>
          <Tip content="只重新对齐本行（及选中的行），生成一个局部结果供对比；不会覆盖当前结果与人工锁定">
            <Button size="sm" icon={<RefreshCw className="size-3.5" />} onClick={onRerun} loading={rerunBusy}>局部重跑</Button>
          </Tip>
        </div>
      </div>

      {lt && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-xl border border-line bg-surface-2/50 px-4 py-3 text-[13px] sm:grid-cols-3">
          <Info label="区间" value={<span className="tabular font-mono">{fmtMs(lt.start_ms)} – {fmtMs(lt.end_ms)}</span>} />
          <Info
            label="锚点"
            value={lt.anchor_ms === null ? '无（普通模式或无时间）' : (
              <span className="tabular font-mono">
                {fmtMs(lt.anchor_ms)} <Badge tone={lt.anchor_kind === 'hard' ? 'ok' : 'neutral'}>{lt.anchor_kind === 'hard' ? '硬' : '软'}</Badge>
              </span>
            )}
          />
          <Info
            label="句首偏差"
            value={lt.anchor_residual_ms === null ? '—' : (
              <span className={cn('tabular font-mono', Math.abs(lt.anchor_residual_ms) > 300 && 'text-warn')}>{fmtSigned(lt.anchor_residual_ms)}</span>
            )}
          />
          <Info label="解码窗口" value={lt.window_ms ? <span className="tabular font-mono">{fmtMs(lt.window_ms[0])} – {fmtMs(lt.window_ms[1])}</span> : '—'} />
          <Info
            label="联合上下文"
            value={lt.context_line_ids.length ? (
              <Tip content={lt.context_line_ids.map((id) => contextTexts.get(id) ?? id).join(' / ')}>
                <span className="cursor-help underline decoration-dotted underline-offset-4">{lt.context_line_ids.length} 行</span>
              </Tip>
            ) : '无'}
          />
          <Info label="音频输入" value={ROLE_LABEL[lt.audio_role ?? ''] ?? lt.audio_role ?? '—'} />
          {lt.candidate && <Info label="已采用" value={<Badge tone="accent">{lt.candidate}</Badge>} />}
          {lt.reason && <Info label="原因" value={<span className="text-danger">{lt.reason}</span>} />}
        </div>
      )}

      {!editable && (
        <div className="rounded-lg bg-warn-soft px-3 py-2 text-xs text-fg/80">
          该结果已过期（输入已修改），只能查看；请重新对齐后再修改时间。
        </div>
      )}

      <Table className="max-h-[460px]">
        <thead>
          <tr>
            <Th className="w-10" />
            <Th>单元</Th>
            <Th>开始 (ms)</Th>
            <Th>结束 (ms)</Th>
            <Th>状态</Th>
            <Th>标记</Th>
            <Th className="text-right">人工</Th>
          </tr>
        </thead>
        <tbody ref={tbodyRef}>
          {stat.units.map((u) => {
            const ui = info.get(u.unit_id);
            const sel = u.unit_id === selUnitId;
            const differs = !!u.manual && (u.manual.start_ms !== u.model_start_ms || u.manual.end_ms !== u.model_end_ms);
            return (
              <tr
                key={u.unit_id}
                data-unit={u.unit_id}
                onClick={() => playUnit(u)}
                className={cn('cursor-pointer transition', sel ? 'bg-accent-soft/70' : 'hover:bg-surface-2/70')}
              >
                <Td>
                  <IconButton size="xs" label="循环试听" onClick={(e) => { e.stopPropagation(); playUnit(u); }}>
                    <Play className="size-3 fill-current" />
                  </IconButton>
                </Td>
                <Td>
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-[15px] font-semibold">{ui?.unitSurface || u.reading}</span>
                    {ui && !ui.unitSurface && (
                      <span className="text-xs text-muted">{ui.segSurface}{ui.count > 1 ? ` ${ui.pos + 1}/${ui.count}` : ''}</span>
                    )}
                    {ui?.unitSurface && ui.unitSurface !== u.reading && <span className="text-xs text-muted">{u.reading}</span>}
                  </div>
                </Td>
                <Td onClick={(e) => e.stopPropagation()}>
                  <NumberInput className="w-28" value={u.start_ms} disabled={!editable} min={0} onCommit={(v) => commit(u, 'start', v)} />
                  {differs && <div className="tabular mt-0.5 font-mono text-[11px] text-subtle" title="模型原始预测">模型 {u.model_start_ms ?? '—'}</div>}
                </Td>
                <Td onClick={(e) => e.stopPropagation()}>
                  <NumberInput className="w-28" value={u.end_ms} disabled={!editable} min={0} onCommit={(v) => commit(u, 'end', v)} />
                  {differs && <div className="tabular mt-0.5 font-mono text-[11px] text-subtle" title="模型原始预测">模型 {u.model_end_ms ?? '—'}</div>}
                  {u.tail && !u.manual && (
                    <Tip content={`尾音修正（${u.tail.method}）：${u.tail.reason}；原值 ${u.tail.original_end_ms ?? '—'}`}>
                      <div className="mt-0.5 cursor-help text-[11px] text-info">尾音已修正</div>
                    </Tip>
                  )}
                </Td>
                <Td>
                  <Badge tone={STATUS_TONE[u.status]}>{STATUS_LABEL[u.status] ?? u.status}</Badge>
                  {u.reason && <div className="mt-1 max-w-48 text-[11px] leading-4 text-danger">{u.reason}</div>}
                </Td>
                <Td>
                  <div className="flex max-w-56 flex-wrap gap-1">
                    {u.flags.map((f) => (
                      <Tip key={f} content={flagHelp(f)}>
                        <span className="cursor-help"><Badge tone={f === 'manual' ? 'ok' : f === 'adopted' ? 'accent' : 'warn'}>{flagLabel(f)}</Badge></span>
                      </Tip>
                    ))}
                  </div>
                </Td>
                <Td className="text-right" onClick={(e) => e.stopPropagation()}>
                  <div className="flex justify-end gap-0.5">
                    <IconButton
                      size="xs"
                      label={u.manual?.locked ? '已锁定：重跑不会覆盖（点击解锁）' : '锁定当前时间'}
                      disabled={!editable || (u.start_ms === null && !u.manual)}
                      onClick={() => void setUnitLock(u.unit_id, !u.manual?.locked, result.id)}
                      className={u.manual?.locked ? 'text-ok' : undefined}
                    >
                      {u.manual?.locked ? <Lock className="size-3.5" /> : <LockOpen className="size-3.5" />}
                    </IconButton>
                    <IconButton
                      size="xs"
                      label="恢复模型时间（保留修改历史）"
                      disabled={!editable || !u.manual}
                      onClick={() => void clearUnitManual(u.unit_id, result.id)}
                    >
                      <RotateCcw className="size-3.5" />
                    </IconButton>
                  </div>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}

function Info({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="mt-0.5 truncate">{value}</div>
    </div>
  );
}
