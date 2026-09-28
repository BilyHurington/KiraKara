// Manual unit edits with an undo/redo stack. Every change goes through the
// server (which keeps the model prediction and a history); undo/redo restores
// the exact previous ManualEdit via /restore.
//
// Edits, undo and redo run one after another (a fast ⌘Z ⌘Z never lands out of
// order); a failed undo / redo puts its entry back; entries of a result that
// became stale or was replaced are dropped with a message instead of silently
// changing an old result.

import { api } from '@/lib/api';
import type { AlignmentResult, ManualEdit, UnitTiming } from '@/lib/types';
import { currentResult, patchResult, resultFrom, run, toast, useApp, type UndoEntry } from './app';

function applyUnit(pid: string, rid: string, ut: UnitTiming) {
  const pv = useApp.getState().pv;
  if (!pv || pv.project.id !== pid) return;  // another project was opened meanwhile
  const r = pv.project.results.find((x) => x.id === rid);
  if (!r) return;
  const units = r.units.map((u) => (u.unit_id === ut.unit_id ? ut : u));
  // keep the line range in sync (the server does the same)
  const lineUnits = units.filter((u) => u.line_id === ut.line_id);
  const starts = lineUnits.map((u) => u.start_ms).filter((x): x is number => x !== null);
  const ends = lineUnits.map((u) => u.end_ms).filter((x): x is number => x !== null);
  const lines = r.lines.map((l) => (l.line_id === ut.line_id
    ? { ...l, start_ms: starts.length ? Math.min(...starts) : null, end_ms: ends.length ? Math.max(...ends) : null }
    : l));
  patchResult({ ...r, units, lines } as AlignmentResult);
}

function unitOf(rid: string, uid: string): UnitTiming | undefined {
  return useApp.getState().pv?.project.results.find((r) => r.id === rid)?.units.find((u) => u.unit_id === uid);
}

function push(entry: UndoEntry) {
  useApp.setState((s) => ({ undo: [...s.undo.slice(-199), entry], redo: [] }));
}

// one edit at a time, in the order they were made
let queue: Promise<unknown> = Promise.resolve();
function serial<T>(fn: () => Promise<T>): Promise<T> {
  const next = queue.then(fn, fn);
  queue = next.catch(() => undefined);
  return next;
}

const upath = (pid: string, rid: string, uid: string, tail = '') => `/api/projects/${pid}/results/${rid}/units/${uid}${tail}`;

async function edit(rid: string | undefined, uid: string, label: (ut: UnitTiming) => string,
  call: (pid: string, rid: string) => Promise<UnitTiming>, errTitle = '修改失败') {
  const pid = useApp.getState().pid;
  if (!rid || !pid) return;
  await serial(() => run(async () => {
    const before = unitOf(rid, uid)?.manual ?? null;
    const ut = await call(pid, rid);
    applyUnit(pid, rid, ut);
    if (useApp.getState().pid === pid) push({ rid, uid, before, after: ut.manual, label: label(ut) });
  }, errTitle));
}

/** Set start/end of a unit (locks it). */
export function setUnitTimes(uid: string, start: number | null, end: number | null, rid = currentResult()?.id) {
  return edit(rid, uid, (ut) => `修改 ${ut.reading}`,
    (pid, r) => api.put<UnitTiming>(upath(pid, r, uid), { start_ms: start, end_ms: end, locked: true }));
}

export function setUnitLock(uid: string, locked: boolean, rid = currentResult()?.id) {
  return edit(rid, uid, (ut) => (locked ? `锁定 ${ut.reading}` : `解锁 ${ut.reading}`),
    (pid, r) => api.post<UnitTiming>(upath(pid, r, uid, '/lock'), { locked }), '操作失败');
}

export function clearUnitManual(uid: string, rid = currentResult()?.id) {
  return edit(rid, uid, (ut) => `恢复模型时间 ${ut.reading}`,
    (pid, r) => api.del<UnitTiming>(upath(pid, r, uid, '/manual')), '操作失败');
}

/** Move a whole line: a start alone shifts it, a start and end stretch it (every unit locked); one undo step. */
export function retimeLine(lineId: string, start: number | null, end: number | null, rid = currentResult()?.id) {
  const pid = useApp.getState().pid;
  if (!rid || !pid) return Promise.resolve();
  return serial(() => run(async () => {
    const r = useApp.getState().pv?.project.results.find((x) => x.id === rid);
    const before = new Map((r?.units ?? []).filter((u) => u.line_id === lineId).map((u) => [u.unit_id, u.manual ?? null]));
    const res = await api.post<{ units: UnitTiming[] }>(`/api/projects/${pid}/results/${rid}/lines/${lineId}/retime`,
      { start_ms: start, end_ms: end });
    for (const ut of res.units) applyUnit(pid, rid, ut);
    if (useApp.getState().pid !== pid || !res.units.length) return;
    const items = res.units.map((ut) => ({ uid: ut.unit_id, before: before.get(ut.unit_id) ?? null, after: ut.manual }));
    const what = res.units[0].manual?.note === '整行伸缩' ? '整行伸缩' : '整行平移';
    push({ rid, uid: items[0].uid, before: items[0].before, after: items[0].after, label: what, items });
  }, '调整失败'));
}

/** Units of these lines were replaced (adopting a rerun / candidate): their undo steps no longer apply. */
export function forgetEdits(rid: string, lineIds: string[]) {
  const lines = new Set(lineIds);
  const r = useApp.getState().pv?.project.results.find((x) => x.id === rid);
  const units = new Set((r?.units ?? []).filter((u) => lines.has(u.line_id)).map((u) => u.unit_id));
  const keep = (e: UndoEntry) => !(e.rid === rid && (units.has(e.uid) || !!e.items?.some((i) => units.has(i.uid))));
  useApp.setState((s) => ({ undo: s.undo.filter(keep), redo: s.redo.filter(keep) }));
}

/** Why an entry cannot be applied any more (null: it can). */
function blocked(e: UndoEntry): string | null {
  const r = resultFrom(useApp.getState().pv, e.rid);
  if (!r) return '该修改所在的对齐结果已不存在';
  if (r.stale) return '该修改所在的对齐结果已过期（输入已修改），不能再改它的时间';
  if (!r.units.some((u) => u.unit_id === e.uid)) return '该单元已不在结果中';
  return null;
}

function step(from: 'undo' | 'redo') {
  return serial(async () => {
    const s = useApp.getState();
    const e = s[from].at(-1);
    const pid = s.pid;
    if (!e || !pid) return;
    const to = from === 'undo' ? 'redo' : 'undo';
    useApp.setState((st) => ({ [from]: st[from].slice(0, -1) }) as any);
    const why = blocked(e);
    if (why) {
      toast('warn', `无法${from === 'undo' ? '撤销' : '重做'}：${e.label}`, `${why}；这一步已从记录中移除`);
      return;
    }
    const items = e.items ?? [{ uid: e.uid, before: e.before, after: e.after }];
    try {
      for (const it of items) {
        const manual: ManualEdit | null = from === 'undo' ? it.before : it.after;
        const ut = await api.post<UnitTiming>(upath(pid, e.rid, it.uid, '/restore'), { manual });
        applyUnit(pid, e.rid, ut);
      }
      if (useApp.getState().pid !== pid) return;
      useApp.setState((st) => ({ [to]: [...st[to], e] }) as any);
      if (e.rid !== useApp.getState().resultId) {
        toast('info', `已${from === 'undo' ? '撤销' : '重做'}：${e.label}`, '这一步属于另一个对齐结果', 2500);
      } else {
        toast('info', `已${from === 'undo' ? '撤销' : '重做'}：${e.label}`, undefined, 1800);
      }
    } catch (err: any) {
      // keep the step so it can be tried again
      if (useApp.getState().pid === pid) useApp.setState((st) => ({ [from]: [...st[from], e] }) as any);
      toast('error', `${from === 'undo' ? '撤销' : '重做'}失败`, err?.message ?? String(err));
    }
  });
}

export const undo = () => step('undo');
export const redo = () => step('redo');
