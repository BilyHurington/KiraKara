// Global app state, events and job tracking.

import { GET, POST } from './api.js';
import { toast } from './util.js';

export const S = {
  info: null,          // /api/info
  projects: [],
  pid: null,
  pv: null,            // ProjectView {project, view}
  resultId: null,      // result shown in review (defaults to active)
  result: null,        // full AlignmentResult (fresh stale flag)
  step: 'mode',
  selLineId: null,     // selected lyric line (calibration / review)
  selUnitId: null,     // selected unit (review / waveform)
  calibLineId: null,
  undo: [],            // [{rid, uid, before, after}]
  redo: [],
  jobs: {},            // id -> Job
  lyricsPreview: null, // LyricsPreview awaiting apply
  extraTracks: null,   // {kind: text} offered by a fetched song
  song: null,          // fetched song preview
  collection: null,    // fetched collection listing
  trackPreview: null,
  aiPrompt: null,
  aiReport: null,
  compareWith: null,   // partial rerun result id to compare with
  candidateView: null, // candidate shown on waveform
};

const listeners = {};
export function on(ev, fn) {
  (listeners[ev] ||= []).push(fn);
}
export function emit(ev, data) {
  for (const fn of listeners[ev] || []) {
    try { fn(data); } catch (e) { console.error(e); }
  }
}

export const project = () => S.pv?.project || null;
export const view = () => S.pv?.view || null;
export const ppath = (p = '') => `/api/projects/${S.pid}${p}`;

export function lines() {
  return project()?.lyrics?.lines || [];
}
export function lineById(id) {
  return lines().find((l) => l.id === id) || null;
}
export function sungLines() {
  return lines().filter((l) => l.sing && l.kind === 'lyric');
}
export function lineIndex(id) {
  return lines().findIndex((l) => l.id === id);
}
export function assetByRole(role) {
  return (project()?.audio || []).find((a) => a.role === role) || null;
}
export function audioAvailable(role) {
  return !!view()?.audio?.[role]?.available;
}

export async function loadProjects() {
  S.projects = await GET('/api/projects');
  emit('projects');
}

/** Apply a ProjectView returned by any mutating endpoint. */
export function setPV(pv) {
  if (!pv || !pv.project) return;
  const prevActive = S.pv?.project?.active_result_id;
  S.pv = pv;
  S.pid = pv.project.id;
  const results = pv.project.results || [];
  if (!S.resultId || !results.some((r) => r.id === S.resultId) || prevActive !== pv.project.active_result_id) {
    S.resultId = pv.project.active_result_id || (results.length ? results[results.length - 1].id : null);
  }
  syncResultFromProject();
  emit('project');
}

/** Use the result embedded in the project, with the fresh stale flag from view.results */
function syncResultFromProject() {
  const p = project();
  const r = (p?.results || []).find((x) => x.id === S.resultId) || null;
  if (r) {
    const sum = (view()?.results || []).find((x) => x.id === r.id);
    if (sum) {
      r.stale = sum.stale;
      r.stale_reason = sum.stale_reason;
    }
  }
  S.result = r;
}

export async function openProject(pid) {
  S.pid = pid;
  S.resultId = null;
  S.undo = [];
  S.redo = [];
  S.lyricsPreview = null;
  S.song = null;
  S.collection = null;
  S.aiReport = null;
  S.aiPrompt = null;
  S.selLineId = null;
  S.selUnitId = null;
  S.calibLineId = null;
  const pv = await GET(ppath());
  setPV(pv);
  try { localStorage.setItem('kara.pid', pid); } catch (e) { /* ignore */ }
}

export async function refreshProject() {
  if (!S.pid) return;
  setPV(await GET(ppath()));
}

export async function loadResult(rid = S.resultId) {
  if (!rid) return null;
  const r = await GET(ppath(`/results/${rid}`));
  const p = project();
  if (p) {
    const i = p.results.findIndex((x) => x.id === r.id);
    if (i >= 0) p.results[i] = r; else p.results.push(r);
  }
  if (rid === S.resultId) S.result = r;
  emit('result');
  return r;
}

export function selectResult(rid) {
  S.resultId = rid;
  syncResultFromProject();
  S.compareWith = null;
  emit('result');
}

export function resultById(rid) {
  return (project()?.results || []).find((r) => r.id === rid) || null;
}

// ---------------------------------------------------------------- jobs

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);

/** Track a job until it ends; onDone(job) runs for any terminal state. */
export function trackJob(job, { label, onDone } = {}) {
  job.label = label || job.kind;
  S.jobs[job.id] = job;
  emit('jobs');
  const tick = async () => {
    let j;
    try {
      j = await GET(`/api/jobs/${job.id}`);
    } catch (e) {
      setTimeout(tick, 1500);
      return;
    }
    j.label = job.label;
    S.jobs[job.id] = j;
    emit('jobs');
    if (TERMINAL.has(j.status)) {
      if (j.status === 'succeeded') toast(`${j.label}：完成`, 'ok', 3000);
      else if (j.status === 'failed') toast(`${j.label} 失败：${j.error || j.message}`);
      else toast(`${j.label}：已取消`, 'info', 3000);
      try { await onDone?.(j); } catch (e) { console.error(e); toast(e.message); }
      setTimeout(() => { delete S.jobs[j.id]; emit('jobs'); }, j.status === 'failed' ? 30000 : 8000);
      return;
    }
    setTimeout(tick, 500);
  };
  setTimeout(tick, 300);
  return job;
}

export async function cancelJob(id) {
  const j = await POST(`/api/jobs/${id}/cancel`);
  j.label = S.jobs[id]?.label || j.kind;
  S.jobs[id] = j;
  emit('jobs');
}

export function isJobRunning(kind) {
  return Object.values(S.jobs).some((j) => j.kind === kind && !TERMINAL.has(j.status) && j.project_id === S.pid);
}
