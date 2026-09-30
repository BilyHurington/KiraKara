// Global app state. Every mutating endpoint returns a ProjectView which is
// applied with setPV(); the result shown in review is derived from it.

import { useMemo } from 'react';
import { create } from 'zustand';
import { api, ApiError, readableError } from '@/lib/api';
import { errorHint } from '@/lib/errorHints';
import type { AlignmentResult, Info, Job, ManualEdit, ProjectListItem, ProjectView } from '@/lib/types';
import type { LineSingers } from '@/lib/singers';

export type Step = 'mode' | 'input' | 'enhance' | 'calibrate' | 'align' | 'review' | 'singers' | 'karaoke' | 'export';

export const STEPS: { id: Step; label: string; hint: string; optional?: boolean; lrcOnly?: boolean }[] = [
  { id: 'mode', label: '选择模式', hint: '普通 / LRC 增强' },
  { id: 'input', label: '音频与歌词', hint: '粘贴、上传或音乐链接' },
  { id: 'enhance', label: '注音与分离', hint: 'AI 注音 · 人声分离', optional: true },
  { id: 'calibrate', label: '首音校准', hint: '确认开头位置（全局偏移）', lrcOnly: true },
  { id: 'align', label: '对齐', hint: '运行模型' },
  { id: 'review', label: '人工检查', hint: '修正与锁定' },
  { id: 'singers', label: '演唱者', hint: '多人演唱时分色', optional: true },
  { id: 'karaoke', label: '卡拉OK字幕', hint: '样式 · 预览 · 生成视频', optional: true },
  { id: 'export', label: '导出', hint: 'JSON · LRC · 混音' },
];

export interface Toast { id: number; kind: 'ok' | 'error' | 'info' | 'warn'; title: string; body?: string; ttl: number }

export interface UndoEntry {
  rid: string; uid: string; before: ManualEdit | null; after: ManualEdit | null; label: string;
  /** several units changed as one step (a whole line moved): undone / redone together */
  items?: { uid: string; before: ManualEdit | null; after: ManualEdit | null }[];
  /** who sings which lines (the 演唱者 page; no result: `rid` / `uid` are empty); `key`: the next
   * step with the same key replaces this one (typing 1 + 2 is one step) */
  singers?: { before: LineSingers[]; after: LineSingers[]; key?: string };
}

interface State {
  info: Info | null;
  projects: ProjectListItem[];
  pid: string | null;
  pv: ProjectView | null;
  resultId: string | null;
  step: Step;
  selLineId: string | null;
  selUnitId: string | null;
  /** more units selected together with selUnitId (Shift / ⌘ click); only counts while it contains selUnitId */
  selUnitIds: string[];
  calibLineId: string | null;
  /** the review page's line filter (simple mode's "建议检查" opens it on 有问题) */
  reviewFilter: 'all' | 'issues' | 'manual';
  compareWithId: string | null;
  candidateId: string | null;
  undo: UndoEntry[];
  redo: UndoEntry[];
  /** operations followed live (the top-right list); finished ones drop out after a while */
  jobs: Record<string, Job>;
  /** every operation seen, kept with its output (download links, AI report, suggestion …) */
  jobHistory: Record<string, Job>;
  toasts: Toast[];
  theme: 'light' | 'dark';
  dockOpen: boolean;
  waveHeight: number;
  reviewListWidth: number;
}

export const WAVE_HEIGHT = { min: 96, max: 520, default: 188 };
export const REVIEW_LIST_WIDTH = { min: 220, max: 560, default: 320 };

function storedNumber(key: string, d: number, min: number, max: number) {
  try {
    const v = Number(localStorage.getItem(key));
    return Number.isFinite(v) && v >= min && v <= max ? v : d;
  } catch {
    return d;
  }
}

/** Resizable layout sizes, remembered per browser. */
export function setLayoutSize(key: 'waveHeight' | 'reviewListWidth', v: number) {
  set({ [key]: v } as Partial<State>);
  try { localStorage.setItem(`kara.${key}`, String(v)); } catch { /* ignore */ }
}

const initialTheme = (): 'light' | 'dark' =>
  typeof document !== 'undefined' && document.documentElement.classList.contains('dark') ? 'dark' : 'light';

export const useApp = create<State>(() => ({
  info: null,
  projects: [],
  pid: null,
  pv: null,
  resultId: null,
  step: 'mode',
  selLineId: null,
  selUnitId: null,
  selUnitIds: [],
  calibLineId: null,
  reviewFilter: 'all',
  compareWithId: null,
  candidateId: null,
  undo: [],
  redo: [],
  jobs: {},
  jobHistory: {},
  toasts: [],
  theme: initialTheme(),
  dockOpen: true,
  waveHeight: storedNumber('kara.waveHeight', WAVE_HEIGHT.default, WAVE_HEIGHT.min, WAVE_HEIGHT.max),
  reviewListWidth: storedNumber('kara.reviewListWidth', REVIEW_LIST_WIDTH.default, REVIEW_LIST_WIDTH.min, REVIEW_LIST_WIDTH.max),
}));

const set = useApp.setState;
const get = useApp.getState;

/** Everything that belongs to one open project (selection, undo …). */
const PROJECT_RESET = {
  resultId: null, undo: [], redo: [], selLineId: null, selUnitId: null, selUnitIds: [], calibLineId: null,
  reviewFilter: 'all', compareWithId: null, candidateId: null,
} satisfies Partial<State>;

// ------------------------------------------------------------------ selectors

export const ppath = (p = '') => `/api/projects/${get().pid}${p}`;

/** A job / answer belongs to the project that is open now. */
export const isOpenProject = (pid: string | null | undefined) => !!pid && get().pid === pid;

export function useProject() {
  return useApp((s) => s.pv?.project ?? null);
}
export function useView() {
  return useApp((s) => s.pv?.view ?? null);
}

/** Result currently selected for review, with the fresh stale flag from view.results. */
export function useResult(): AlignmentResult | null {
  const pv = useApp((s) => s.pv);
  const rid = useApp((s) => s.resultId);
  return useMemo(() => resultFrom(pv, rid), [pv, rid]);
}

/** The project's active (当前) result, with the fresh stale flag. */
export function useActiveResult(): AlignmentResult | null {
  const pv = useApp((s) => s.pv);
  return useMemo(() => resultFrom(pv, pv?.project.active_result_id ?? null), [pv]);
}

/** Any result by id (e.g. a local rerun to compare with), with fresh stale flag. */
export function useResultById(rid: string | null): AlignmentResult | null {
  const pv = useApp((s) => s.pv);
  return useMemo(() => resultFrom(pv, rid), [pv, rid]);
}

export function resultFrom(pv: ProjectView | null, rid: string | null): AlignmentResult | null {
  if (!pv || !rid) return null;
  const r = pv.project.results.find((x) => x.id === rid);
  if (!r) return null;
  const sum = pv.view.results.find((x) => x.id === rid);
  return sum ? { ...r, stale: sum.stale, stale_reason: sum.stale_reason } : r;
}

export function currentResult(): AlignmentResult | null {
  return resultFrom(get().pv, get().resultId);
}

/** The selected units: the multi-selection when it holds the current unit, else just that unit. */
export function selectedUnitIds(s: Pick<State, 'selUnitId' | 'selUnitIds'> = get()): string[] {
  if (!s.selUnitId) return [];
  return s.selUnitIds.length > 1 && s.selUnitIds.includes(s.selUnitId) ? s.selUnitIds : [s.selUnitId];
}

/** Select a unit: alone, adding / removing it (⌘ / Ctrl), or every unit from the current one to it
 * in time order (Shift, also across lines). */
export function selectUnit(id: string, mode: 'single' | 'toggle' | 'range' = 'single') {
  const s = get();
  const r = currentResult();
  const unit = r?.units.find((u) => u.unit_id === id);
  const lineId = unit?.line_id ?? s.selLineId;
  const current = selectedUnitIds(s);
  if (mode === 'single' || !s.selUnitId || !r) {
    set({ selUnitId: id, selUnitIds: [id], selLineId: lineId });
  } else if (mode === 'toggle') {
    const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
    const primary = next.includes(id) ? id : next[next.length - 1] ?? null;
    set({ selUnitId: primary, selUnitIds: next, selLineId: primary ? r.units.find((u) => u.unit_id === primary)?.line_id ?? lineId : lineId });
  } else {
    const order = r.units.filter((u) => u.start_ms !== null).sort((a, b) => a.start_ms! - b.start_ms!).map((u) => u.unit_id);
    const a = order.indexOf(s.selUnitId);
    const b = order.indexOf(id);
    if (a < 0 || b < 0) return set({ selUnitId: id, selUnitIds: [id], selLineId: lineId });
    set({ selUnitId: s.selUnitId, selUnitIds: order.slice(Math.min(a, b), Math.max(a, b) + 1) });
  }
}

// ------------------------------------------------------------------ toasts

let toastSeq = 1;
export function toast(kind: Toast['kind'], title: string, body?: string, ttl = kind === 'error' ? 8000 : 3500) {
  const t: Toast = { id: toastSeq++, kind, title, body, ttl };
  set((s) => ({ toasts: [...s.toasts.slice(-4), t] }));
  if (ttl > 0) setTimeout(() => dismissToast(t.id), ttl);
}
export function dismissToast(id: number) {
  set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
}

/** Run an async action and surface failures as a toast. Returns undefined on error. */
export async function run<T>(fn: () => Promise<T>, errTitle = '操作失败'): Promise<T | undefined> {
  try {
    return await fn();
  } catch (e: any) {
    toast('error', errTitle, readableError(e?.message ?? e));
    return undefined;
  }
}

// ------------------------------------------------------------------ actions

export async function loadInfo() {
  set({ info: await api.get<Info>('/api/info') });
}

export async function loadProjects() {
  set({ projects: await api.get<ProjectListItem[]>('/api/projects') });
}

export function setPV(pv: ProjectView | null | undefined) {
  if (!pv?.project) return;
  // an answer about another project (the user switched meanwhile) must not switch back
  const open = get().pid;
  if (open && open !== pv.project.id) return;
  const prev = get().pv;
  const results = pv.project.results;
  const other = prev && prev.project.id !== pv.project.id;
  let rid = other ? null : get().resultId;
  if (!rid || !results.some((r) => r.id === rid) || prev?.project.active_result_id !== pv.project.active_result_id) {
    rid = pv.project.active_result_id ?? (results.length ? results[results.length - 1].id : null);
  }
  const ids = new Set(results.map((r) => r.id));
  const keep = (e: UndoEntry) => !!e.singers || ids.has(e.rid);
  set((s) => ({
    ...(other ? PROJECT_RESET : {}),
    pv, pid: pv.project.id, resultId: rid,
    // undo entries of results that are gone can never be undone
    undo: other ? [] : s.undo.filter(keep), redo: other ? [] : s.redo.filter(keep),
    compareWithId: s.compareWithId && ids.has(s.compareWithId) && !other ? s.compareWithId : null,
  }));
  // remember the open project across reloads (also for newly created ones)
  try { localStorage.setItem('kara.pid', pv.project.id); } catch { /* ignore */ }
}

/** Start working on a project: fresh selection and undo history, then show it. */
export function adoptProject(pv: ProjectView, step?: Step) {
  set({ ...PROJECT_RESET, pid: pv.project.id, pv: null });
  setPV(pv);
  const hasLyrics = pv.project.lyrics.lines.length > 0;
  set({ step: step ?? (pv.project.results.length ? 'review' : hasLyrics ? 'input' : 'mode') });
  void resumeJobs(pv.project.id);
}

let openSeq = 0;
export async function openProject(pid: string) {
  const seq = ++openSeq;
  const pv = await api.get<ProjectView>(`/api/projects/${pid}`);
  if (seq !== openSeq) return;  // another project was opened meanwhile
  adoptProject(pv);
}

export function closeProject() {
  openSeq += 1;
  set({ ...PROJECT_RESET, pid: null, pv: null, step: 'mode' });
  try { localStorage.removeItem('kara.pid'); } catch { /* ignore */ }
}

/** Delete a project with its audio, stems and exports (the server refuses while it is in use). */
export async function deleteProject(pid: string) {
  await api.del(`/api/projects/${pid}`);
  if (get().pid === pid) closeProject();
  await loadProjects();
}

export async function refreshProject() {
  const pid = get().pid;
  if (!pid) return;
  const pv = await api.get<ProjectView>(`/api/projects/${pid}`);
  if (get().pid === pid) setPV(pv);  // another project was opened meanwhile: this answer is stale
}

/** Replace one result inside the current project view (after unit edits etc.); its summary counts follow. */
export function patchResult(r: AlignmentResult) {
  const pv = get().pv;
  if (!pv || !pv.project.results.some((x) => x.id === r.id)) return;
  const results = pv.project.results.map((x) => (x.id === r.id ? r : x));
  const summaries = pv.view.results.map((x) => (x.id === r.id ? {
    ...x,
    n_units: r.units.length,
    n_failed: r.units.filter((u) => u.status !== 'ok').length,
    n_manual: r.units.filter((u) => u.manual != null).length,
    n_issues: r.issues.length,
  } : x));
  set({ pv: { ...pv, project: { ...pv.project, results }, view: { ...pv.view, results: summaries } } });
}

export async function loadResult(rid: string) {
  const pid = get().pid;
  const r = await api.get<AlignmentResult>(ppath(`/results/${rid}`));
  if (get().pid === pid) patchResult(r);
  return r;
}

/** Show another result of the open project in review (ignored for a result of another project). */
export function selectResult(rid: string): boolean {
  if (!get().pv?.project.results.some((r) => r.id === rid)) return false;
  set({ resultId: rid, compareWithId: null, candidateId: null });
  return true;
}

export function setStep(step: Step) {
  set({ step });
}

export function setTheme(theme: 'light' | 'dark') {
  document.documentElement.classList.toggle('dark', theme === 'dark');
  try { localStorage.setItem('kara.theme', theme); } catch { /* ignore */ }
  set({ theme });
}

// ------------------------------------------------------------------ jobs

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
export const isLive = (j: Job | null | undefined) => !!j && !TERMINAL.has(j.status);

/** Job texts from the server made readable ("ValueError: …" → "…"). */
function clean(j: Job, label?: string): Job {
  return { ...j, label: label ?? j.label, error: j.error ? readableError(j.error) : j.error };
}

function remember(j: Job) {
  set((s) => ({ jobHistory: { ...s.jobHistory, [j.id]: j } }));
}

export function trackJob(job: Job, opts: {
  label: string; onDone?: (j: Job) => void | Promise<void>;
  /** toast text on success (default “<label>完成”) */
  doneText?: string;
}) {
  const first = clean(job, opts.label);
  set((s) => ({ jobs: { ...s.jobs, [job.id]: first }, jobHistory: { ...s.jobHistory, [job.id]: first } }));
  const tick = async () => {
    let j: Job;
    try {
      j = await api.get<Job>(`/api/jobs/${job.id}`);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {  // server unreachable for a moment: keep trying
        setTimeout(tick, 1500);
        return;
      }
      // jobs live in the server's memory: after a restart this one is gone
      j = { ...job, status: 'failed', error: '本地服务已重启，这个操作被中断了，请重新开始', message: '' };
    }
    j = clean(j, opts.label);
    set((s) => ({ jobs: { ...s.jobs, [j.id]: j }, jobHistory: { ...s.jobHistory, [j.id]: j } }));
    if (TERMINAL.has(j.status)) {
      const where = j.project_id && !isOpenProject(j.project_id) ? projectName(j.project_id) : null;
      const suffix = where ? `（${where}）` : '';
      if (j.status === 'succeeded') toast('ok', `${opts.doneText ?? `${opts.label}完成`}${suffix}`);
      else if (j.status === 'failed') {
        const hint = errorHint(j.error);
        toast('error', `${opts.label}失败${suffix}`, `${j.error || j.message}${hint ? `\n${hint}` : ''}`);
      }
      else toast('info', `${opts.label}已取消${suffix}`);
      try {
        await opts.onDone?.(j);
      } catch (e: any) {
        toast('error', '更新失败', readableError(e?.message));
      }
      setTimeout(() => set((s) => {
        const jobs = { ...s.jobs };
        delete jobs[j.id];
        return { jobs };
      }), j.status === 'failed' ? 30000 : 6000);
      return;
    }
    setTimeout(tick, 500);
  };
  setTimeout(tick, 300);
  return job;
}

export function projectName(pid: string | null | undefined): string | null {
  if (!pid) return null;
  if (get().pv?.project.id === pid) return get().pv!.project.name;
  return get().projects.find((p) => p.id === pid)?.name ?? null;
}

export const JOB_LABELS: Record<string, string> = {
  align: '对齐', separate: '人声分离', mix: '混音导出', video: '视频导出', burn: '生成视频（烧录字幕）', ai: 'AI 注音', calibrate: '自动匹配偏移',
};
const DONE_TEXT: Record<string, string> = { ai: 'AI 注音已返回结果：请在“注音与分离 → AI 注音”第 3 步预览并应用' };

/**
 * Re-attach to jobs still running on the server (e.g. after a page reload) and
 * keep the finished ones with their outputs (download links, reports …).
 */
export async function resumeJobs(pid: string) {
  let jobs: Job[] = [];
  try {
    jobs = await api.get<Job[]>(`/api/projects/${pid}/jobs`);
  } catch {
    return;
  }
  if (!Array.isArray(jobs)) return;
  for (const raw of jobs) {
    const j = clean(raw, JOB_LABELS[raw.kind] ?? raw.kind);
    if (TERMINAL.has(j.status)) {
      remember(j);
      continue;
    }
    if (get().jobs[j.id]) continue;
    trackJob(j, { label: j.label!, doneText: DONE_TEXT[j.kind], onDone: () => (isOpenProject(pid) ? refreshProject() : undefined) });
  }
}

export async function cancelJob(id: string) {
  const j = clean(await api.post<Job>(`/api/jobs/${id}/cancel`), get().jobs[id]?.label ?? get().jobHistory[id]?.label);
  set((s) => ({ jobs: { ...s.jobs, [id]: j }, jobHistory: { ...s.jobHistory, [id]: j } }));
}

function latest(s: State, kind: string): Job | null {
  let best: Job | null = null;
  for (const j of Object.values(s.jobHistory)) {
    if (j.kind !== kind || j.project_id !== s.pid) continue;
    if (!best || j.created > best.created) best = j;
  }
  return best;
}

export function useJobRunning(kind: string) {
  return useApp((s) => isLive(latest(s, kind)));
}

/** The latest job of a kind on the open project — running or finished (with its output). */
export function useJob(kind: string): Job | null {
  return useApp((s) => latest(s, kind));
}

/** Succeeded jobs of these kinds on the open project, newest first (e.g. recent exports). */
export function useFinishedJobs(kinds: string[]): Job[] {
  const history = useApp((s) => s.jobHistory);
  const pid = useApp((s) => s.pid);
  const key = kinds.join(',');
  return useMemo(() => Object.values(history)
    .filter((j) => j.project_id === pid && j.status === 'succeeded' && key.split(',').includes(j.kind))
    .sort((a, b) => b.created.localeCompare(a.created)), [history, pid, key]);
}
