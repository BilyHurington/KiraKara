// Simple mode (极简模式): which shell is shown, app-wide settings and the task queue.
// Settings and tasks live on the server; this store only mirrors them.

import { create } from 'zustand';
import { api } from '@/lib/api';
import type { AiProviderInfo, AppSettings, PipelineTask, SettingsPatch, TaskStyleOptions } from '@/lib/types';
import { loadProjects, openProject, run, setStep, toast, type Step } from './app';

export type Ui = 'simple' | 'pro';
export type SimplePage = 'home' | 'settings';

interface SimpleState {
  ui: Ui;
  page: SimplePage;
  settings: AppSettings | null;
  providers: AiProviderInfo[] | null;
  tasks: PipelineTask[];
}

function storedUi(): Ui {
  try {
    return localStorage.getItem('kara.ui') === 'pro' ? 'pro' : 'simple';
  } catch {
    return 'simple';
  }
}

export const useSimple = create<SimpleState>(() => ({ ui: storedUi(), page: 'home', settings: null, providers: null, tasks: [] }));
const set = useSimple.setState;
const get = useSimple.getState;

export function setUi(ui: Ui) {
  set({ ui });
  try { localStorage.setItem('kara.ui', ui); } catch { /* ignore */ }
  // simple-mode tasks create projects in the background: refresh the list for the detailed mode
  if (ui === 'pro') void run(() => loadProjects(), '读取项目列表失败');
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

export async function loadProviders(refresh = false) {
  set({ providers: await api.get<AiProviderInfo[]>(`/api/ai/providers${refresh ? '?refresh=1' : ''}`) });
}

// ------------------------------------------------------------------ tasks

const ACTIVE = new Set(['preparing', 'queued', 'running']);

export async function loadTasks() {
  const tasks = await api.get<PipelineTask[]>('/api/tasks');
  const before = new Map(get().tasks.map((t) => [t.id, t.status]));
  for (const t of tasks) {
    const was = before.get(t.id);
    if (t.status === 'waiting' && was !== undefined && was !== 'waiting') {
      toast('warn', `「${t.name || t.media_filename}」需要确认开头位置`, '点任务里的“确认开头位置”，确认后自动继续', 10000);
    }
    if (was && ACTIVE.has(was) && !ACTIVE.has(t.status) && t.status !== 'waiting') {
      if (t.status === 'succeeded') toast('ok', `「${t.name || t.media_filename}」已完成`, t.outputs.video ? '视频已生成' : undefined);
      else if (t.status === 'failed') toast('error', `「${t.name || t.media_filename}」失败`, t.error ?? undefined);
    }
  }
  set({ tasks });
  return tasks;
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
