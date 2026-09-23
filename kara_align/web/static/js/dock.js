// Bottom dock: transport, source/mix controls and the waveform.

import { GET, PATCH, POST, run } from './api.js';
import {
  availableSources, pause, play, player, positionMs, roleLabel, seek, setLoop, setMix,
  setMonitorVolume, setRate, setSource, syncAssets, toggle, toggleLoop,
} from './player.js';
import { S, assetByRole, emit, on, ppath, project, trackJob, view } from './state.js';
import { clear, debounce, fmtMs, h, toast } from './util.js';
import { Waveform } from './waveform.js';

export let wave = null;
let peaksKey = null;
let waveRole = 'auto';
let busInfo = { bus_gain: 1, peak_before: null };

export function initDock(getOverlays, callbacks) {
  wave = new Waveform(document.getElementById('wave'), document.getElementById('wave-scroll'), {
    getOverlays,
    getPlayhead: () => ({ ms: positionMs(), playing: player.playing }),
    onSeek: (ms) => { seek(ms); callbacks.onSeek?.(ms); },
    onLoop: (a, b) => setLoop(a, b),
    onSelectUnit: (id) => callbacks.onSelectUnit?.(id),
    onEditUnit: (id, s, e) => callbacks.onEditUnit?.(id, s, e),
  });
  const loop = () => {
    if (wave.dirty || player.playing) {
      wave.draw();
      updateClock();
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  on('player', () => { renderControls(); wave.setDuration(Math.max(wave.durationMs, player.durationMs)); wave.dirty = true; });
  on('player-error', (msg) => toast(msg));
  on('mix', () => updateMixLabels());
  on('project', () => onProject());
  on('result', () => { if (wave) wave.dirty = true; });
  renderControls();
}

async function onProject() {
  const p = project();
  const dock = document.getElementById('dock');
  const wasHidden = dock.hidden;
  dock.hidden = !p;
  if (!p) return;
  if (wasHidden) wave.resize();
  // keep player mix in sync with the stored project settings (first load only)
  if (!player._mixLoadedFor || player._mixLoadedFor !== p.id) {
    player._mixLoadedFor = p.id;
    const m = p.mix || {};
    setMix({ p: m.vocal_keep_pct ?? 100, q: m.instrumental_pct ?? 100, master: m.master ?? 1 });
  }
  const stemsKey = `${assetByRole('vocals')?.id}|${assetByRole('instrumental')?.id}`;
  if (player._stemsKey !== stemsKey) {
    player._stemsKey = stemsKey;
    refreshBusGain();
  }
  syncAssets(S.pid, p, view()).catch((e) => toast(e.message));
  loadPeaks();
  wave.dirty = true;
  renderControls();
}

function chooseWaveRole() {
  if (waveRole !== 'auto' && view()?.audio?.[waveRole]?.available) return waveRole;
  for (const r of ['original', 'vocals', 'instrumental']) if (view()?.audio?.[r]?.available) return r;
  return null;
}

async function loadPeaks() {
  const role = chooseWaveRole();
  const a = role ? assetByRole(role) : null;
  const key = a ? `${S.pid}:${a.id}` : null;
  if (key === peaksKey) return;
  peaksKey = key;
  if (!a) { wave.setPeaks(null); return; }
  try {
    const pk = await GET(ppath(`/audio/${a.id}/peaks?per_second=200`));
    if (peaksKey === key) wave.setPeaks(pk);
  } catch (e) {
    toast(`加载波形失败：${e.message}`);
  }
}

let clockEl = null;
function updateClock() {
  if (clockEl) clockEl.textContent = `${fmtMs(positionMs())} / ${fmtMs(player.durationMs)}`;
}

let mixLabelEls = {};
function updateMixLabels() {
  const m = player.mix;
  if (mixLabelEls.p) mixLabelEls.p.textContent = `${Math.round(m.p)}%`;
  if (mixLabelEls.q) mixLabelEls.q.textContent = `${Math.round(m.q)}%`;
  if (mixLabelEls.master) mixLabelEls.master.textContent = `×${m.master.toFixed(2)}`;
  if (mixLabelEls.bus) {
    mixLabelEls.bus.textContent = `母线增益 ×${(busInfo.bus_gain ?? 1).toFixed(3)}` +
      (busInfo.peak_before != null ? `（混音峰值 ${busInfo.peak_before.toFixed(3)}）` : '');
  }
}

function mixSettings() {
  const lim = project()?.mix?.limiter || 'normalize_peak';
  return { vocal_keep_pct: player.mix.p, instrumental_pct: player.mix.q, master: player.mix.master, limiter: lim };
}

const refreshBusGain = debounce(async () => {
  if (!S.pid || !(view()?.audio?.vocals?.available && view()?.audio?.instrumental?.available)) {
    busInfo = { bus_gain: 1, peak_before: null };
    setMix({ bus: 1 });
    updateMixLabels();
    return;
  }
  try {
    busInfo = await POST(ppath('/mix/preview-gain'), mixSettings());
    setMix({ bus: busInfo.bus_gain ?? 1 });
  } catch (e) {
    toast(`计算母线增益失败：${e.message}`);
  }
  updateMixLabels();
}, 350);

const persistMix = debounce(async () => {
  if (!S.pid) return;
  try {
    const pv = await PATCH(ppath(), { mix: mixSettings() });
    if (pv?.project && S.pv) S.pv.project.mix = pv.project.mix;
  } catch (e) { /* not critical */ }
}, 800);

function slider(key, min, max, step, onInput) {
  const el = h('input', { type: 'range', min, max, step, value: key === 'master' ? player.mix.master : player.mix[key] });
  el.addEventListener('input', () => onInput(Number(el.value)));
  el.addEventListener('change', () => { refreshBusGain(); persistMix(); });
  return el;
}

function renderControls() {
  const box = document.getElementById('player-controls');
  if (!box) return;
  clear(box);
  const avail = availableSources();
  const loading = Object.keys(player.loading);
  const hasStems = avail.includes('mix');
  const onlyOriginal = !player.buffers.vocals && !player.buffers.instrumental;

  const src = h('select', { title: '试听音源', onchange: (e) => setSource(e.target.value), value: player.source },
    ['original', 'vocals', 'instrumental', 'mix'].map((r) =>
      h('option', { value: r, disabled: !avail.includes(r) }, roleLabel(r) + (avail.includes(r) ? '' : '（无）'))));

  clockEl = h('span', { class: 'clock' });
  updateClock();

  const rate = h('select', { title: '慢速试听：游标与标记仍对应原音频时间（音高会随速度降低）', onchange: (e) => setRate(Number(e.target.value)), value: String(player.rate) },
    [1, 0.75, 0.5].map((r) => h('option', { value: String(r) }, r === 1 ? '1× 正常' : `${r}× 慢速`)));

  const loopBtn = h('button', { class: player.loop.on ? 'on' : '', title: 'L：开关循环（在波形上拖选区间）', onclick: () => toggleLoop() },
    player.loop.on ? `循环 ${fmtMs(player.loop.start)}–${fmtMs(player.loop.end)}` : '循环');
  const clearLoop = h('button', { title: '清除循环区间', onclick: () => setLoop(null, null), disabled: player.loop.start === null }, '清除区间');

  const waveSel = h('select', { title: '波形显示的音轨', onchange: (e) => { waveRole = e.target.value; loadPeaks(); }, value: waveRole },
    h('option', { value: 'auto' }, '波形：自动'),
    ['original', 'vocals', 'instrumental'].map((r) => h('option', { value: r, disabled: !view()?.audio?.[r]?.available }, `波形：${roleLabel(r)}`)));

  mixLabelEls = { p: h('span', { class: 'num' }), q: h('span', { class: 'num' }), master: h('span', { class: 'num' }), bus: h('span', { class: 'muted small' }) };
  const mixBox = h('div', { class: 'mix' + (player.source === 'mix' ? ' active' : '') },
    h('label', { title: onlyOriginal ? '只有原曲时无法单独降低完整混音中的人声；请先分离或导入人声/伴奏' : '人声轨线性幅度 ×p/100（不是主观响度）' },
      '人声保留 ', mixLabelEls.p,
      slider('p', 0, 100, 1, (v) => { setMix({ p: v }); updateMixLabels(); })),
    h('label', { title: '伴奏轨线性幅度 ×q/100（默认 100%）' }, '伴奏 ', mixLabelEls.q,
      slider('q', 0, 100, 1, (v) => { setMix({ q: v }); updateMixLabels(); })),
    h('label', { title: '母线主增益（写入导出）' }, '主增益 ', mixLabelEls.master,
      slider('master', 0, 2, 0.01, (v) => { setMix({ master: v }); updateMixLabels(); })),
    mixLabelEls.bus,
    h('button', { onclick: exportMix, disabled: !hasStems, title: '按相同混音规则导出正常速度、原始时长与原点的 WAV' }, '导出混音 WAV'),
  );
  if (!hasStems) {
    for (const inp of mixBox.querySelectorAll('input')) inp.disabled = true;
    mixBox.appendChild(h('span', { class: 'muted small' }, onlyOriginal
      ? '只有原曲：无法独立调节人声比例（需要人声/伴奏分轨）'
      : '需要同时具备人声与伴奏才能自定义混音'));
  } else {
    mixBox.appendChild(h('span', { class: 'muted small', title: '分离残留' }, '注：0% 时伴奏中仍可能残留人声'));
  }

  const monitor = h('input', { type: 'range', min: 0, max: 1, step: 0.01, value: player.monitorVolume, title: '监听音量（不写入导出）' });
  monitor.addEventListener('input', () => setMonitorVolume(Number(monitor.value)));

  box.append(
    h('div', { class: 'transport' },
      h('button', { class: 'primary', onclick: () => toggle(), title: '空格：播放/暂停', disabled: !avail.length }, player.playing ? '⏸ 暂停' : '▶ 播放'),
      h('button', { onclick: () => { pause(); seek(0); }, title: '回到开头' }, '⏮'),
      clockEl, src, rate, loopBtn, clearLoop,
      h('span', { class: 'sep' }),
      h('button', { onclick: () => wave.zoom(0.5), title: '放大（滚轮）' }, '＋'),
      h('button', { onclick: () => wave.zoom(2), title: '缩小（滚轮）' }, '－'),
      h('button', { onclick: () => wave.zoomAll(), title: '显示全曲' }, '全曲'),
      h('label', { class: 'small' }, h('input', { type: 'checkbox', checked: wave.follow, onchange: (e) => { wave.follow = e.target.checked; } }), '跟随'),
      waveSel,
      h('label', { class: 'small', title: '监听音量（不写入导出）' }, '监听', monitor),
      loading.length ? h('span', { class: 'muted small' }, `加载音频：${loading.map(roleLabel).join('、')}…`) : null,
    ),
    mixBox,
  );
  updateMixLabels();
}

async function exportMix() {
  const job = await run(() => POST(ppath('/mix/export'), mixSettings()));
  if (!job) return;
  trackJob(job, {
    label: '导出混音',
    onDone: (j) => {
      if (j.status === 'succeeded' && j.output?.url) {
        S.lastMixExport = j.output;
        emit('mix-exported', j.output);
        const a = h('a', { href: j.output.url, download: j.output.filename || 'mix.wav' });
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    },
  });
}

export function refreshMixGain() {
  refreshBusGain();
}

export { play, pause };
