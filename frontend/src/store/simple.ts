// Simple mode (极简模式): which shell is shown, app-wide settings and the task queue.
// Settings and tasks live on the server; this store only mirrors them.

import { create } from 'zustand';
import { api } from '@/lib/api';
import type { AiProviderInfo, AppSettings, KaraokeStyle, PipelineTask, SettingsPatch, TaskStyleOptions } from '@/lib/types';
import { loadProjects, openProject, refreshProject, run, setStep, toast, useApp, type Step } from './app';

export type Ui = 'simple' | 'pro';
export type SimplePage = 'home' | 'settings';

interface SimpleState {
  ui: Ui;
  page: SimplePage;
  settings: AppSettings | null;
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

export const useSimple = create<SimpleState>(() => ({ ui: storedUi(), page: 'home', settings: null, providers: null, tasks: [], ownTasks: [] }));
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
  set({ settings: await api.get<AppSettings>('/api/settings') });
}

export async function saveSettings(patch: SettingsPatch) {
  const s = await api.put<AppSettings>('/api/settings', patch);
  set({ settings: s });
  return s;
}

/** Make a style the simple mode's default *and* have the next tasks use it as a whole: step ④ switches to
 * "设置里的样式" and its ruby / translation / title card switches follow the style again. */
export async function setSimpleDefault(style: KaraokeStyle) {
  if (!get().settings) await loadSettings();
  const cur = get().settings!.simple.task_style;
  const taskStyle: TaskStyleOptions = { ...cur, source: 'default', translation: null, song_info: null, ruby: 'style', ruby_target: null };
  return saveSettings({ simple: { karaoke: style, task_style: taskStyle } });
}

export async function loadProviders(refresh = false) {
  set({ providers: await api.get<AiProviderInfo[]>(`/api/ai/providers${refresh ? '?refresh=1' : ''}`) });
}

// ------------------------------------------------------------------ tasks

const ACTIVE = new Set(['preparing', 'queued', 'running']);

/** What in a task can change the project it works on (a finished stage, the status). */
const footprint = (t: PipelineTask) => `${t.status}|${t.stages.map((x) => x.status).join(',')}`;

export async function loadTasks() {
  const tasks = await api.get<PipelineTask[]>('/api/tasks');
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

export async function addTask(file: File, lyrics: string, mode: string, name: string, style?: TaskStyleOptions) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  fd.append('lyrics', lyrics);
  fd.append('mode', mode);
  fd.append('name', name);
  if (style) fd.append('style', JSON.stringify(style));
  const t = await api.post<PipelineTask>('/api/tasks', fd);
  set({ tasks: [t, ...get().tasks.filter((x) => x.id !== t.id)] });
  return t;
}

export async function confirmCalibration(id: string, body: { marked_ms?: number; plain?: boolean }) {
  await api.post(`/api/tasks/${id}/calibration`, body);
  await loadTasks();
}

export async function taskAction(id: string, action: 'cancel' | 'retry' | 'delete') {
  if (action === 'delete') await api.del(`/api/tasks/${id}`);
  else await api.post(`/api/tasks/${id}/${action}`);
  await loadTasks();
}
