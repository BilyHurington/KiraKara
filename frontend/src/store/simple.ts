// Simple mode (极简模式): which shell is shown, app-wide settings and the task queue.
// Settings and tasks live on the server; this store only mirrors them.

import { create } from 'zustand';
import { api, readableError, uploadWithProgress } from '@/lib/api';
import type { AiProviderInfo, AppSettings, KaraokeStyle, PipelineTask, SettingsPatch, TaskStyleOptions } from '@/lib/types';
import { loadProjects, openProject, refreshProject, run, setStep, toast, useApp, type Step } from './app';

export type Ui = 'simple' | 'pro';
export type SimplePage = 'home' | 'settings';

interface SimpleState {
  ui: Ui;
  page: SimplePage;
  settings: AppSettings | null;
  /** reading the settings failed (shown with a retry) */
  settingsError: string | null;
  providers: AiProviderInfo[] | null;
  tasks: PipelineTask[];
  /** tasks added from this browser: their offset dialog opens by itself when they are ready */
  ownTasks: string[];
}

function storedUi(): Ui {
  try {
    return localStorage.getItem('kara.ui') === 'pro' ? 'pro' : 'simple';
  } catch {
    return 'simple';
  }
}

export const useSimple = create<SimpleState>(() => ({ ui: storedUi(), page: 'home', settings: null, settingsError: null, providers: null, tasks: [], ownTasks: [] }));
const set = useSimple.setState;
const get = useSimple.getState;

export function setUi(ui: Ui) {
  set({ ui });
  try { localStorage.setItem('kara.ui', ui); } catch { /* ignore */ }
  // simple-mode tasks change projects in the background: refresh the list and the open project
  if (ui === 'pro') void run(async () => { await loadProjects(); await refreshProject(); }, '读取项目列表失败');
}

export function setSimplePage(page: SimplePage) {
  set({ page, ui: 'simple' });
  try { localStorage.setItem('kara.ui', 'simple'); } catch { /* ignore */ }
}

/** Open a task's project in the detailed mode. */
export async function openInDetail(pid: string, step: Step = 'review') {
  const ok = await run(async () => { await openProject(pid); return true; }, '打开项目失败');
  if (!ok) return;
  setStep(step);
  setUi('pro');
}

// ------------------------------------------------------------------ settings

export async function loadSettings() {
  try {
    set({ settings: await api.get<AppSettings>('/api/settings'), settingsError: null });
  } catch (e: any) {
    set({ settingsError: readableError(e?.message ?? e) });
    throw e;
  }
}

// settings saves go out one after another, so an older answer never lands last
let saving: Promise<unknown> = Promise.resolve();
let pendingSaves = 0;
export function saveSettings(patch: SettingsPatch): Promise<AppSettings> {
  pendingSaves += 1;
  const next = saving.then(async () => {
    try {
      const s = await api.put<AppSettings>('/api/settings', patch);
      set({ settings: s, settingsError: null });
      return s;
    } finally {
      pendingSaves -= 1;
    }
  });
  saving = next.catch(() => undefined);
  return next;
}

/** Wait for settings saves still on their way (e.g. before testing the AI connection). */
export function settingsSaved(): Promise<unknown> {
  return pendingSaves ? saving : Promise.resolve();
}

/** Make a style the simple mode's default *and* have the next tasks use it as a whole: step ④ switches to
 * "设置里的样式" and its ruby / translation / title card switches follow the style again. */
export async function setSimpleDefault(style: KaraokeStyle) {
  if (!get().settings) await loadSettings();
  const cur = get().settings?.simple.task_style;
  if (!cur) throw new Error('无法读取设置，请稍后重试');
  const taskStyle: TaskStyleOptions = { ...(taskStyleDraft ?? cur), source: 'default', translation: null, song_info: null, ruby: 'style', ruby_target: null };
  taskStyleDraft = null;
  return saveSettings({ simple: { karaoke: style, task_style: taskStyle } });
}

// the new-task form's step ④ choices while their save is on its way (so returning to the form shows them)
let taskStyleDraft: TaskStyleOptions | null = null;
export const getTaskStyleDraft = () => taskStyleDraft;
export function saveTaskStyle(opts: TaskStyleOptions) {
  taskStyleDraft = opts;
  return saveSettings({ simple: { task_style: opts } }).then((s) => {
    if (taskStyleDraft === opts) taskStyleDraft = null;
    return s;
  });
}

export async function loadProviders(refresh = false) {
  set({ providers: await api.get<AiProviderInfo[]>(`/api/ai/providers${refresh ? '?refresh=1' : ''}`) });
}

// ------------------------------------------------------------------ tasks

const ACTIVE = new Set(['preparing', 'queued', 'running']);

/** What in a task can change the project it works on (a finished stage, the status). */
const footprint = (t: PipelineTask) => `${t.status}|${t.stages.map((x) => x.status).join(',')}`;

export async function loadTasks() {
  const tasks = (await api.get<PipelineTask[]>('/api/tasks')).map((t) => (t.error ? { ...t, error: readableError(t.error) } : t));
  const old = new Map(get().tasks.map((t) => [t.id, t]));
  const before = new Map(get().tasks.map((t) => [t.id, t.status]));
  // the detailed mode shows a project a task is working on: reload it when the task moves on
  const pid = useApp.getState().pid;
  const touched = tasks.some((t) => t.project_id === pid && old.has(t.id) && footprint(old.get(t.id)!) !== footprint(t));
  const finished = tasks.some((t) => old.has(t.id) && ACTIVE.has(old.get(t.id)!.status) && !ACTIVE.has(t.status));
  for (const t of tasks) {
    const was = before.get(t.id);
    if (t.status === 'waiting' && was !== undefined && was !== 'waiting') {
      toast('warn', `「${t.name || t.media_filename}」需要确认开头位置`,
        get().ui === 'simple' ? '点任务里的“确认开头位置”，确认后自动继续' : '点右上角的“极简模式”确认，确认后自动继续', 10000);
    }
    if (was && ACTIVE.has(was) && !ACTIVE.has(t.status) && t.status !== 'waiting') {
      if (t.status === 'succeeded') toast('ok', `「${t.name || t.media_filename}」已完成`, t.outputs.video ? '视频已生成' : undefined);
      else if (t.status === 'failed') toast('error', `「${t.name || t.media_filename}」失败`, t.error ?? undefined);
    }
  }
  set({ tasks });
  if (pid && touched) await refreshProject().catch(() => undefined);
  if (finished) await loadProjects().catch(() => undefined);
  return tasks;
}

let polling = false;
/** Poll the queue for the whole app (both modes): fast while something runs. */
export function startTaskPolling() {
  if (polling) return;
  polling = true;
  const tick = async () => {
    let list: PipelineTask[] = get().tasks;
    try { list = await loadTasks(); } catch { /* server restarting */ }
    setTimeout(tick, hasActiveTasks(list) || list.some((t) => t.status === 'waiting') ? 1000 : 5000);
  };
  void tick();
}

/** The unfinished simple-mode task working on a project (the detailed mode must wait for it). */
export function taskOnProject(tasks: PipelineTask[], pid: string | null) {
  return pid ? tasks.find((t) => t.project_id === pid && (ACTIVE.has(t.status) || t.status === 'waiting')) ?? null : null;
}

export function markOwnTask(id: string) {
  set({ ownTasks: [...get().ownTasks, id] });
}
export function forgetOwnTask(id: string) {
  set({ ownTasks: get().ownTasks.filter((x) => x !== id) });
}

export function hasActiveTasks(tasks: PipelineTask[]) {
  return tasks.some((t) => ACTIVE.has(t.status));
}

export async function addTask(file: File, lyrics: string, mode: string, name: string, style?: TaskStyleOptions,
  onProgress?: (f: number) => void, background?: File | null) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  // a picture / video played in a loop behind the subtitles (the file is then usually just the song)
  if (background) fd.append('background', background, background.name);
  fd.append('lyrics', lyrics);
  fd.append('mode', mode);
  fd.append('name', name);
  if (style) fd.append('style', JSON.stringify(style));
  const t = onProgress
    ? await uploadWithProgress<PipelineTask>('/api/tasks', fd, onProgress)
    : await api.post<PipelineTask>('/api/tasks', fd);
  set({ tasks: [t, ...get().tasks.filter((x) => x.id !== t.id)] });
  return t;
}

export async function confirmCalibration(id: string, body: { marked_ms?: number; plain?: boolean }) {
  await api.post(`/api/tasks/${id}/calibration`, body);
  // AI readings by hand come right after: open that dialog by itself too
  const t = get().tasks.find((x) => x.id === id);
  if (t && manualReadings(t)) markOwnTask(id);
  await loadTasks();
}

/** A task whose AI readings go through a web chat by hand. */
export const manualReadings = (t: PipelineTask) => !!(t.processing?.ai_readings && t.processing.ai_provider === 'manual');

/** The stage a waiting task waits in ('calibrate' / 'readings'). */
export const waitingFor = (t: PipelineTask) => (t.status === 'waiting' ? t.stages.find((s) => s.status === 'waiting')?.key ?? null : null);

export async function readingsPrompt(id: string) {
  return api.get<{ prompt: string; lines: number; snapshot_id: string }>(`/api/tasks/${id}/readings/prompt`);
}

export async function submitReadings(id: string, body: { text?: string; skip?: boolean }) {
  await api.post(`/api/tasks/${id}/readings`, body);
  await loadTasks();
}

export async function taskAction(id: string, action: 'cancel' | 'retry' | 'delete') {
  if (action === 'delete') await api.del(`/api/tasks/${id}`);
  else await api.post(`/api/tasks/${id}/${action}`);
  await loadTasks();
}
