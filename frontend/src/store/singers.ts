// Singers (多人演唱): assigning lines / words to singers (one undo step each, in the shared undo
// stack) and saving the style's singer list.  Saves of the list go out one after another; the
// 卡拉OK字幕 page waits for them before it reads the style.

import { api } from '@/lib/api';
import { assign, lineSingers, sameSingers, type LineSingers, type Selection } from '@/lib/singers';
import type { KaraokeSingers, KaraokeStyle, ProjectView } from '@/lib/types';
import { run, setPV, useApp } from './app';
import { push, serial } from './edits';

/** `ids` sing the selection ([] = back to each line's own singers); returns whether anything changed.
 * `key`: a later assignment with the same key replaces this undo step (1 then +2 is one step). */
export function assignSingers(sel: Selection, ids: number[], label: string, key?: string): Promise<boolean> {
  const pid = useApp.getState().pid;
  if (!pid || !sel.size) return Promise.resolve(false);
  return serial(async () => {
    const lines = useApp.getState().pv?.project.lyrics.lines ?? [];
    const before: LineSingers[] = [];
    const after: LineSingers[] = [];
    for (const l of lines) {
      const ranges = sel.get(l.id);
      if (!ranges?.length) continue;
      const was = lineSingers(l);
      const now = assign(was, ranges, ids);
      if (sameSingers(was, now)) continue;
      before.push(was);
      after.push(now);
    }
    if (!after.length) return false;
    const ok = await run(async () => {
      setPV(await api.put<ProjectView>(`/api/projects/${pid}/singers`, { lines: after }));
      return true;
    }, '指定演唱者失败');
    if (!ok || useApp.getState().pid !== pid) return false;
    const last = useApp.getState().undo.at(-1);
    if (key && last?.singers?.key === key) {
      // the same selection again (1 → 1+2): one step from what it was before the first
      const first = new Map(last.singers.before.map((x) => [x.line_id, x]));
      for (const b of before) if (!first.has(b.line_id)) first.set(b.line_id, b);
      const merged = new Map(last.singers.after.map((x) => [x.line_id, x]));
      for (const a of after) merged.set(a.line_id, a);
      useApp.setState((s) => ({
        undo: [...s.undo.slice(0, -1), { ...last, label, singers: { before: [...first.values()], after: [...merged.values()], key } }],
        redo: [],
      }));
    } else {
      push({ rid: '', uid: '', before: null, after: null, label, singers: { before, after, key } });
    }
    return true;
  });
}

/** Undo / redo steps of singer assignments no longer apply (singers were renumbered). */
export function forgetSingerSteps() {
  useApp.setState((s) => ({ undo: s.undo.filter((e) => !e.singers), redo: s.redo.filter((e) => !e.singers) }));
}

// ------------------------------------------------------------------ the singer list (in the style)

let saving: Promise<unknown> = Promise.resolve();
let pending: { pid: string; singers: KaraokeSingers } | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;

function patchStyle(pid: string, style: KaraokeStyle) {
  const pv = useApp.getState().pv;
  if (!pv || pv.project.id !== pid) return;
  useApp.setState({ pv: { ...pv, project: { ...pv.project, karaoke: style } } });
}

/** Save the singer list (debounced); flushSingers() sends it at once. */
export function saveSingers(pid: string, singers: KaraokeSingers, delay = 500) {
  pending = { pid, singers };
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { void flushSingers(); }, delay);
}

export function flushSingers(): Promise<unknown> {
  if (timer) { clearTimeout(timer); timer = null; }
  const p = pending;
  pending = null;
  if (p) {
    saving = saving.then(() => run(async () => {
      patchStyle(p.pid, await api.put<KaraokeStyle>(`/api/projects/${p.pid}/karaoke/singers`, p.singers));
    }, '保存演唱者失败'));
  }
  return saving;
}

/** Every change of the singer list is saved (the style can be read now). */
export const singersSettled = () => flushSingers();

/** Remove singer `n`: its parts go back to the line's other singers, later singers move up. */
export async function removeSinger(pid: string, n: number) {
  await flushSingers();
  const pv = await api.del<ProjectView & { changed: number }>(`/api/projects/${pid}/karaoke/singers/${n}`);
  setPV(pv);
  forgetSingerSteps();
  return pv;
}

export interface MarkerLine { line_id: string; text: string; prefix: string; names: string[]; everyone: boolean }
export interface Markers { lines: MarkerLine[]; names: string[]; existing: string[] }

export const loadMarkers = (pid: string) => api.get<Markers>(`/api/projects/${pid}/singers/markers`);

/** Assign the lines that name their singers; `strip`: take the names out of the lyrics. */
export async function applyMarkers(pid: string, names: string[], strip: boolean) {
  await flushSingers();
  const pv = await api.post<ProjectView & { messages: string[] }>(`/api/projects/${pid}/singers/markers`, { names, strip });
  setPV(pv);
  forgetSingerSteps();  // texts / numbers may have changed
  return pv;
}
