// Entry point: step navigation, rendering, keyboard shortcuts, waveform wiring.

import { GET, run } from './api.js';
import { initDock, wave } from './dock.js';
import { player, positionMs, seek, toggle, toggleLoop } from './player.js';
import { S, audioAvailable, emit, lines, loadProjects, on, openProject, project, view } from './state.js';
import { clear, h, isTyping, toast } from './util.js';
import { renderAlignPanel } from './panels/align.js';
import { calibOverlayLines, markAtPlayhead, renderCalibrationPanel } from './panels/calibration.js';
import { renderExportPanel } from './panels/export.js';
import { renderInputPanel } from './panels/lyrics.js';
import { renderJobsBar, renderModePanel, renderProjectBar } from './panels/project.js';
import { renderEnhancePanel } from './panels/readings.js';
import { editUnitTimes, markUnitStart, redo, renderReviewPanel, reviewOverlayUnits, selectUnit, undo } from './panels/review.js';

const STEPS = [
  { id: 'mode', label: '选择模式', render: renderModePanel, done: () => !!project() },
  { id: 'input', label: '输入音频和歌词', render: renderInputPanel, done: () => lines().length > 0 && audioAvailable('original') },
  { id: 'enhance', label: 'AI 注音 / 人声分离', optional: true, render: renderEnhancePanel,
    done: () => lines().some((l) => (l.segments || []).some((s) => s.units?.length)) },
  { id: 'calib', label: 'LRC 首音校准', lrcOnly: true, render: renderCalibrationPanel, done: () => !!project()?.calibration?.confirmed },
  { id: 'align', label: '对齐', render: renderAlignPanel, done: () => (view()?.results || []).length > 0 },
  { id: 'review', label: '人工检查', render: renderReviewPanel, done: () => false },
  { id: 'export', label: '导出', render: renderExportPanel, done: () => false },
];

function renderStepBar() {
  const bar = clear(document.getElementById('stepbar'));
  const p = project();
  STEPS.forEach((st, i) => {
    const disabled = !p && st.id !== 'mode';
    const skip = st.lrcOnly && p && p.mode !== 'lrc';
    bar.append(h('button', {
      class: ['step', S.step === st.id ? 'active' : '', st.done() ? 'done' : '', skip ? 'skipped' : ''].join(' '),
      disabled,
      title: skip ? '普通模式下无需校准' : (st.optional ? '可选步骤，可按任意顺序进行' : ''),
      onclick: () => { S.step = st.id; rerender(); },
    }, h('span', { class: 'num' }, i + 1), st.label, st.optional ? h('small', {}, '（可选）') : null, skip ? h('small', {}, '（普通模式跳过）') : null));
  });
}

export function rerender() {
  renderProjectBar();
  renderStepBar();
  const panel = document.getElementById('panel');
  const main = document.getElementById('main');
  const scroll = main.scrollTop;
  clear(panel);
  if (!project() && S.step !== 'mode') S.step = 'mode';
  const st = STEPS.find((s) => s.id === S.step) || STEPS[0];
  try {
    st.render(panel);
  } catch (e) {
    console.error(e);
    panel.append(h('div', { class: 'error-box' }, `界面渲染出错：${e.message}`));
  }
  main.scrollTop = scroll;
  if (wave) wave.dirty = true;
}

function overlays() {
  const { units, candUnits } = reviewOverlayUnits();
  return {
    lineStarts: calibOverlayLines(),
    units,
    candUnits,
    selectedUnitId: S.selUnitId,
    loop: player.loop,
  };
}

function onKey(e) {
  if (isTyping(e)) return;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && (e.key === 'z' || e.key === 'Z')) {
    e.preventDefault();
    if (e.shiftKey) redo(); else undo();
    return;
  }
  if (mod && (e.key === 'y' || e.key === 'Y')) {
    e.preventDefault();
    redo();
    return;
  }
  if (mod || e.altKey) return;
  if (e.code === 'Space') {
    e.preventDefault();
    toggle();
  } else if (e.key === 'l' || e.key === 'L') {
    toggleLoop();
  } else if (e.key === 'm' || e.key === 'M') {
    if (S.step === 'review') markUnitStart();
    else if (project()?.mode === 'lrc') markAtPlayhead();
    else toast('M：在 LRC 增强模式下标记首音；在人工检查中设置所选单元的开始', 'info', 3000);
  } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    const d = (e.shiftKey ? 1000 : 100) * (e.key === 'ArrowLeft' ? -1 : 1);
    seek(Math.max(0, positionMs() + d));
  }
}

async function init() {
  initDock(overlays, {
    onSelectUnit: (id) => selectUnit(id),
    onEditUnit: (id, s, e) => editUnitTimes(id, s, e),
  });
  on('project', rerender);
  on('rerender', rerender);
  on('result', () => { if (['review', 'export', 'align'].includes(S.step)) rerender(); });
  on('projects', renderProjectBar);
  on('jobs', () => {
    renderJobsBar();
    // refresh the job status box, but never while the user is typing in the panel
    const active = document.activeElement;
    const typing = active && document.getElementById('panel').contains(active) && isTyping({ target: active });
    if (S.step === 'align' && !typing) rerender();
  });
  document.addEventListener('keydown', onKey);

  await run(async () => { S.info = await GET('/api/info'); });
  await run(loadProjects);
  let last = null;
  try { last = localStorage.getItem('kara.pid'); } catch (e) { /* ignore */ }
  if (last && S.projects.some((p) => p.id === last)) {
    await run(() => openProject(last));
    S.step = 'input';
  }
  rerender();
  emit('project');
}

init();
