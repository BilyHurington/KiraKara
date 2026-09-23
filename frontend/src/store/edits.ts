// Manual unit edits with an undo/redo stack. Every change goes through the
// server (which keeps the model prediction and a history); undo/redo restores
// the exact previous ManualEdit via /restore.

import { api } from '@/lib/api';
import type { AlignmentResult, ManualEdit, UnitTiming } from '@/lib/types';
import { currentResult, patchResult, ppath, run, toast, useApp, type UndoEntry } from './app';

function applyUnit(rid: string, ut: UnitTiming) {
  const pv = useApp.getState().pv;
  const r = pv?.project.results.find((x) => x.id === rid);
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

async function restore(rid: string, uid: string, manual: ManualEdit | null) {
  const ut = await api.post<UnitTiming>(ppath(`/results/${rid}/units/${uid}/restore`), { manual });
  applyUnit(rid, ut);
}

/** Set start/end of a unit (locks it). */
export async function setUnitTimes(uid: string, start: number | null, end: number | null, rid = currentResult()?.id) {
  if (!rid) return;
  const before = unitOf(rid, uid)?.manual ?? null;
  await run(async () => {
    const ut = await api.put<UnitTiming>(ppath(`/results/${rid}/units/${uid}`), { start_ms: start, end_ms: end, locked: true });
    applyUnit(rid, ut);
    push({ rid, uid, before, after: ut.manual, label: `修改 ${ut.reading}` });
  }, '修改失败');
}

export async function setUnitLock(uid: string, locked: boolean, rid = currentResult()?.id) {
  if (!rid) return;
  const before = unitOf(rid, uid)?.manual ?? null;
  await run(async () => {
    const ut = await api.post<UnitTiming>(ppath(`/results/${rid}/units/${uid}/lock`), { locked });
    applyUnit(rid, ut);
    push({ rid, uid, before, after: ut.manual, label: locked ? `锁定 ${ut.reading}` : `解锁 ${ut.reading}` });
  });
}

export async function clearUnitManual(uid: string, rid = currentResult()?.id) {
  if (!rid) return;
  const before = unitOf(rid, uid)?.manual ?? null;
  await run(async () => {
    const ut = await api.del<UnitTiming>(ppath(`/results/${rid}/units/${uid}/manual`));
    applyUnit(rid, ut);
    push({ rid, uid, before, after: null, label: `恢复模型时间 ${ut.reading}` });
  });
}

export async function undo() {
  const e = useApp.getState().undo.at(-1);
  if (!e) return;
  useApp.setState((s) => ({ undo: s.undo.slice(0, -1) }));
  await run(async () => {
    await restore(e.rid, e.uid, e.before);
    useApp.setState((s) => ({ redo: [...s.redo, e] }));
    toast('info', `已撤销：${e.label}`, undefined, 1800);
  }, '撤销失败');
}

export async function redo() {
  const e = useApp.getState().redo.at(-1);
  if (!e) return;
  useApp.setState((s) => ({ redo: s.redo.slice(0, -1) }));
  await run(async () => {
    await restore(e.rid, e.uid, e.after);
    useApp.setState((s) => ({ undo: [...s.undo, e] }));
    toast('info', `已重做：${e.label}`, undefined, 1800);
  }, '重做失败');
}
