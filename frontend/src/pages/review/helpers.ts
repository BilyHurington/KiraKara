// Shared lookups and labels for the review page.

import type { AlignmentResult, Issue, Line, Project, UnitTiming } from '@/lib/types';

export interface UnitInfo {
  segSurface: string;
  unitSurface: string;
  reading: string;
  /** index of this unit within its segment and the segment's unit count */
  pos: number;
  count: number;
}

/** unit id → display info from the lyrics document. */
export function unitInfoMap(project: Project): Map<string, UnitInfo> {
  const m = new Map<string, UnitInfo>();
  for (const ln of project.lyrics.lines) {
    for (const seg of ln.segments) {
      seg.units.forEach((u, i) => m.set(u.id, {
        segSurface: seg.surface, unitSurface: u.surface, reading: u.reading, pos: i, count: seg.units.length,
      }));
    }
  }
  return m;
}

export interface LineStats {
  line: Line;
  index: number;
  start: number | null;
  end: number | null;
  units: UnitTiming[];
  issues: Issue[];
  failed: number;
  manual: number;
  candidates: number;
}

/** Per-line summary, in lyrics order, for the lines contained in the result. */
export function lineStats(project: Project, r: AlignmentResult): LineStats[] {
  const byLine = new Map<string, UnitTiming[]>();
  for (const u of r.units) {
    const a = byLine.get(u.line_id);
    if (a) a.push(u);
    else byLine.set(u.line_id, [u]);
  }
  const timing = new Map(r.lines.map((l) => [l.line_id, l]));
  const out: LineStats[] = [];
  project.lyrics.lines.forEach((line, index) => {
    if (!timing.has(line.id) && !byLine.has(line.id)) return;
    const units = byLine.get(line.id) ?? [];
    const lt = timing.get(line.id);
    out.push({
      line,
      index,
      start: lt?.start_ms ?? null,
      end: lt?.end_ms ?? null,
      units,
      issues: r.issues.filter((i) => i.line_id === line.id),
      failed: units.filter((u) => u.status !== 'ok' && !u.manual).length,
      manual: units.filter((u) => !!u.manual).length,
      candidates: r.candidates.filter((c) => c.line_id === line.id).length,
    });
  });
  return out;
}

export const FLAG_HELP: Record<string, string> = {
  token_gap: '单元内部的 token 被长停顿隔开：读音可能与实际演唱不符',
  short_unit: '区间异常短',
  long_unit: '区间异常长',
  manual: '人工修改过的时间',
  adopted: '已从局部重跑或候选中采用',
  partial_tokens: '部分字符模型词表中没有，只用了其余 token',
  illegal_interval: '区间非法（结束不晚于开始）',
  incomplete: '该行部分单元没有时间',
  edge: '贴近解码窗口边缘',
  line_gap: '与本行前一个单元相隔很久：可能被错放进了间奏',
  in_rest: '所在位置人声分轨几乎无声：可能被错放进了间奏',
  tail_adjusted: '尾音结束时间已按人声能量修正',
  tail_unresolved: '尾音附近找不到可靠的能量边界，保留模型时间',
};

export function flagLabel(flag: string): string {
  if (flag.startsWith('manual-resolved:')) return '人工补齐';
  const map: Record<string, string> = {
    token_gap: '内部停顿', short_unit: '过短', long_unit: '过长', manual: '人工', adopted: '已采用',
    partial_tokens: '部分 token', illegal_interval: '非法区间', line_gap: '行内长停顿', in_rest: '人声无声处',
    tail_adjusted: '尾音已修正', tail_unresolved: '尾音未定',
  };
  return map[flag] ?? flag;
}

export function flagHelp(flag: string): string {
  if (flag.startsWith('manual-resolved:')) return `模型${flag.split(':')[1] === 'failed' ? '失败' : '未对齐'}，已人工补齐时间`;
  return FLAG_HELP[flag] ?? flag;
}

export const SEVERITY_TONE = { error: 'danger', warning: 'warn', info: 'info' } as const;
export const SEVERITY_LABEL = { error: '错误', warning: '警告', info: '提示' } as const;

/** Range covered by some units (for listening), or null. */
export function unitsRange(units: { start_ms: number | null; end_ms: number | null }[]): [number, number] | null {
  const s = units.map((u) => u.start_ms).filter((x): x is number => x !== null);
  const e = units.map((u) => u.end_ms).filter((x): x is number => x !== null);
  if (!s.length || !e.length) return null;
  return [Math.min(...s), Math.max(...e)];
}
