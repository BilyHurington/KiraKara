// Global app state. Every mutating endpoint returns a ProjectView which is
// applied with setPV(); the result shown in review is derived from it.

import { useMemo } from 'react';
import { create } from 'zustand';
import { api, ApiError } from '@/lib/api';
import type { AlignmentResult, Info, Job, ManualEdit, ProjectListItem, ProjectView } from '@/lib/types';

export type Step = 'mode' | 'input' | 'enhance' | 'calibrate' | 'align' | 'review' | 'karaoke' | 'export';

export const STEPS: { id: Step; label: string; hint: string; optional?: boolean; lrcOnly?: boolean }[] = [
  { id: 'mode', label: '选择模式', hint: '普通 / LRC 增强' },
  { id: 'input', label: '音频与歌词', hint: '粘贴、上传或音乐链接' },
  { id: 'enhance', label: '注音与分离', hint: 'AI 注音 · 人声分离', optional: true },
  { id: 'calibrate', label: '首音校准', hint: '全局偏移', lrcOnly: true },
  { id: 'align', label: '对齐', hint: '运行模型' },
  { id: 'review', label: '人工检查', hint: '修正与锁定' },
  { id: 'karaoke', label: '卡拉OK字幕', hint: '样式 · 预览 · 烧录', optional: true },
  { id: 'export', label: '导出', hint: 'JSON · LRC · 混音' },
];

export interface Toast { id: number; kind: 'ok' | 'error' | 'info' | 'warn'; title: string; body?: string; ttl: number }

export interface UndoEntry { rid: string; uid: string; before: ManualEdit | null; after: ManualEdit | null; label: string }

interface State {
  info: Info | null;
  projects: ProjectListItem[];
  pid: string | null;
  pv: ProjectView | null;
  resultId: string | null;
  step: Step;
  selLineId: string | null;
  selUnitId: string | null;
  calibLineId: string | null;
  compareWithId: string | null;
  candidateId: string | null;
  undo: UndoEntry[];
  redo: UndoEntry[];
  jobs: Record<string, Job>;
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
  calibLineId: null,
  compareWithId: null,
  candidateId: null,
  undo: [],
  redo: [],
  jobs: {},
  toasts: [],
  theme: initialTheme(),
  dockOpen: true,
  waveHeight: storedNumber('kara.waveHeight', WAVE_HEIGHT.default, WAVE_HEIGHT.min, WAVE_HEIGHT.max),
  reviewListWidth: storedNumber('kara.reviewListWidth', REVIEW_LIST_WIDTH.default, REVIEW_LIST_WIDTH.min, REVIEW_LIST_WIDTH.max),
}));

const set = useApp.setState;
const get = useApp.getState;

// ------------------------------------------------------------------ selectors

export const ppath = (p = '') => `/api/projects/${get().pid}${p}`;

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
    toast('error', errTitle, e?.message ?? String(e));
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
  const prev = get().pv;
  const results = pv.project.results;
  let rid = get().resultId;
  if (!rid || !results.some((r) => r.id === rid) || prev?.project.active_result_id !== pv.project.active_result_id) {
    rid = pv.project.active_result_id ?? (results.length ? results[results.length - 1].id : null);
  }
  set({ pv, pid: pv.project.id, resultId: rid });
  // remember the open project across reloads (also for newly created ones)
  try { localStorage.setItem('kara.pid', pv.project.id); } catch { /* ignore */ }
}

export async function openProject(pid: string) {
  const pv = await api.get<ProjectView>(`/api/projects/${pid}`);
  set({
    pid, resultId: null, undo: [], redo: [], selLineId: null, selUnitId: null, calibLineId: null,
    compareWithId: null, candidateId: null,
  });
  setPV(pv);
  const hasLyrics = pv.project.lyrics.lines.length > 0;
  set({ step: pv.project.results.length ? 'review' : hasLyrics ? 'input' : 'mode' });
  void resumeJobs(pid);
  try { localStorage.setItem('kara.pid', pid); } catch { /* ignore */ }
}

export function closeProject() {
  set({ pid: null, pv: null, resultId: null, step: 'mode' });
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
  if (pid) setPV(await api.get<ProjectView>(`/api/projects/${pid}`));
}

/** Replace one result inside the current project view (after unit edits etc.). */
export function patchResult(r: AlignmentResult) {
  const pv = get().pv;
  if (!pv) return;
  const results = pv.project.results.map((x) => (x.id === r.id ? r : x));
  set({ pv: { ...pv, project: { ...pv.project, results } } });
}

export async function loadResult(rid: string) {
  const r = await api.get<AlignmentResult>(ppath(`/results/${rid}`));
  patchResult(r);
  return r;
}

export function selectResult(rid: string) {
  set({ resultId: rid, compareWithId: null, candidateId: null });
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

export function trackJob(job: Job, opts: { label: string; onDone?: (j: Job) => void | Promise<void> }) {
  job.label = opts.label;
  set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));
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
    j.label = opts.label;
    set((s) => ({ jobs: { ...s.jobs, [j.id]: j } }));
    if (TERMINAL.has(j.status)) {
      if (j.status === 'succeeded') toast('ok', `${opts.label}完成`);
      else if (j.status === 'failed') toast('error', `${opts.label}失败`, j.error || j.message);
      else toast('info', `${opts.label}已取消`);
      try {
        await opts.onDone?.(j);
      } catch (e: any) {
        toast('error', '更新失败', e?.message);
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

const JOB_LABELS: Record<string, string> = { align: '对齐', separate: '人声分离', mix: '混音导出', video: '视频导出', burn: '字幕烧录', ai: 'AI 注音', calibrate: '自动匹配' };

/** Re-attach to jobs still running on the server (e.g. after a page reload). */
export async function resumeJobs(pid: string) {
  let jobs: Job[] = [];
  try {
    jobs = await api.get<Job[]>(`/api/projects/${pid}/jobs`);
  } catch {
    return;
  }
  for (const j of jobs) {
    if (TERMINAL.has(j.status) || get().jobs[j.id]) continue;
    trackJob(j, { label: JOB_LABELS[j.kind] ?? j.kind, onDone: () => refreshProject() });
  }
}

export async function cancelJob(id: string) {
  const j = await api.post<Job>(`/api/jobs/${id}/cancel`);
  j.label = get().jobs[id]?.label;
  set((s) => ({ jobs: { ...s.jobs, [id]: j } }));
}

export function useJobRunning(kind: string) {
  return useApp((s) => Object.values(s.jobs).some((j) => j.kind === kind && !TERMINAL.has(j.status) && j.project_id === s.pid));
}

export function useJob(kind: string): Job | null {
  return useApp((s) => Object.values(s.jobs).filter((j) => j.kind === kind && j.project_id === s.pid)
    .sort((a, b) => b.created.localeCompare(a.created))[0] ?? null);
}
