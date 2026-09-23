// Retry candidates of the selected line and local reruns of the current result.
// Neither is ever applied automatically: the user listens, compares and adopts.

import { Eye, EyeOff, GitCompareArrows, Play, Sparkles } from 'lucide-react';
import { api } from '@/lib/api';
import { fmtMs, fmtRelative } from '@/lib/format';
import type { AlignmentResult, Candidate } from '@/lib/types';
import { player } from '@/audio/player';
import { revealOnWaveform } from '@/audio/waveformRef';
import { ppath, refreshProject, run, toast, useApp, useResultById } from '@/store/app';
import { Badge, Button, EmptyState, IconButton, Tip } from '@/components/ui';
import { unitsRange } from './helpers';

function listen(units: { start_ms: number | null; end_ms: number | null }[]) {
  const r = unitsRange(units);
  if (!r) return toast('info', '没有可试听的时间');
  revealOnWaveform(r[0], r[1]);
  player.playRange(r[0], r[1], { loop: true, padMs: 250 });
}

async function adopt(rid: string, body: Record<string, unknown>, what: string) {
  await run(async () => {
    await api.post<AlignmentResult>(ppath(`/results/${rid}/adopt`), body);
    await refreshProject();
    toast('ok', `已采用${what}`, '人工锁定的单元保持不变');
  }, '采用失败');
}

export function CandidatesPanel({ result, lineId }: { result: AlignmentResult; lineId: string }) {
  const candidateId = useApp((s) => s.candidateId);
  const cands = result.candidates.filter((c) => c.line_id === lineId);
  if (!cands.length) {
    return <div className="rounded-xl border border-dashed border-line-strong px-4 py-5 text-center text-xs text-muted">本行没有保留的候选</div>;
  }
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">候选明显分歧时保留在这里供人工试听，不会取平均时间，也不会自动替换。</p>
      {cands.map((c) => <CandidateRow key={c.id} c={c} result={result} shown={candidateId === c.id} />)}
    </div>
  );
}

function CandidateRow({ c, result, shown }: { c: Candidate; result: AlignmentResult; shown: boolean }) {
  const range = unitsRange(c.units);
  const summary = Object.entries(c.summary ?? {}).filter(([, v]) => typeof v !== 'object').slice(0, 4);
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2.5">
      <Sparkles className="size-4 shrink-0 text-fuchsia-500" />
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium">{c.label}</div>
        <div className="tabular mt-0.5 font-mono text-[11px] text-muted">
          {range ? `${fmtMs(range[0])} – ${fmtMs(range[1])}` : '无时间'}
          {summary.map(([k, v]) => <span key={k} className="ml-2">{k}: {String(v)}</span>)}
        </div>
      </div>
      <IconButton size="xs" label="循环试听候选" onClick={() => listen(c.units)} disabled={!range}><Play className="size-3 fill-current" /></IconButton>
      <Button size="xs" variant={shown ? 'soft' : 'ghost'} icon={shown ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
        onClick={() => useApp.setState({ candidateId: shown ? null : c.id, compareWithId: null })}>
        {shown ? '隐藏对比' : '在波形上对比'}
      </Button>
      <Button size="xs" variant="outline" disabled={result.stale}
        onClick={() => void adopt(result.id, { candidate_id: c.id, line_ids: [c.line_id] }, `候选「${c.label}」`)}>
        采用
      </Button>
    </div>
  );
}

/** Partial results produced by local reruns of the current result. */
export function RerunsPanel({ result, lineId }: { result: AlignmentResult; lineId: string | null }) {
  const view = useApp((s) => s.pv?.view);
  const compareWithId = useApp((s) => s.compareWithId);
  const reruns = (view?.results ?? []).filter((r) => r.parent_result_id === result.id && !r.coverage.full)
    .sort((a, b) => b.created.localeCompare(a.created));
  if (!reruns.length) {
    return (
      <EmptyState
        className="py-8"
        icon={<GitCompareArrows className="size-5" />}
        title="还没有局部重跑"
        description="勾选左侧的行后点击“局部重跑所选行”。重跑只生成新的局部结果，采用需手动确认。"
      />
    );
  }
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">采用时只复制未锁定单元的时间；人工锁定的单元永远不会被覆盖。</p>
      {reruns.map((r) => (
        <RerunRow key={r.id} rid={r.id} parent={result} lineId={lineId} comparing={compareWithId === r.id} created={r.created} stale={r.stale} />
      ))}
    </div>
  );
}

function RerunRow({ rid, parent, lineId, comparing, created, stale }: {
  rid: string; parent: AlignmentResult; lineId: string | null; comparing: boolean; created: string; stale: boolean;
}) {
  const r = useResultById(rid);
  if (!r) return null;
  const lines = r.coverage.line_ids.length ? r.coverage.line_ids : [...new Set(r.units.map((u) => u.line_id))];
  const hasLine = !!lineId && lines.includes(lineId);
  const lineUnits = hasLine ? r.units.filter((u) => u.line_id === lineId) : [];
  const failed = r.units.filter((u) => u.status !== 'ok').length;
  return (
    <div className="rounded-xl border border-line px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <GitCompareArrows className="size-4 text-cyan-500" />
        <span className="text-[13px] font-medium">局部重跑 · {fmtRelative(created)}</span>
        <Badge>{lines.length} 行</Badge>
        {failed > 0 && <Badge tone="danger">{failed} 无时间</Badge>}
        {stale && <Badge tone="warn">已过期</Badge>}
        <div className="ml-auto flex gap-1.5">
          <Button size="xs" variant={comparing ? 'soft' : 'ghost'} icon={comparing ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
            onClick={() => useApp.setState({ compareWithId: comparing ? null : rid, candidateId: null })}>
            {comparing ? '隐藏对比' : '对比'}
          </Button>
        </div>
      </div>
      {hasLine ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-surface-2/60 px-2.5 py-2">
          <span className="text-xs text-muted">本行重跑结果</span>
          <span className="tabular font-mono text-xs">
            {(() => { const x = unitsRange(lineUnits); return x ? `${fmtMs(x[0])} – ${fmtMs(x[1])}` : '无时间'; })()}
          </span>
          <IconButton size="xs" label="循环试听重跑结果" onClick={() => listen(lineUnits)}><Play className="size-3 fill-current" /></IconButton>
          <Tip content={parent.stale ? '当前结果已过期，不能采用' : '把本行未锁定单元的时间替换为重跑结果'}>
            <span className="ml-auto">
              <Button size="xs" variant="outline" disabled={parent.stale || stale}
                onClick={() => void adopt(parent.id, { from_result_id: rid, line_ids: [lineId] }, '重跑结果')}>
                采用重跑结果
              </Button>
            </span>
          </Tip>
        </div>
      ) : (
        <div className="mt-1.5 text-xs text-muted">不包含当前选中的行；在左侧选择重跑过的行即可试听和采用。</div>
      )}
    </div>
  );
}
