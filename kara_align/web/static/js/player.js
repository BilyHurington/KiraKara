// Synchronized multi-stem player on ONE AudioContext.
//
// All stems are decoded from the server's `playback.wav` (same decoder as the
// alignment), so buffer time 0 == original audio time 0. Every stem needed for
// the current source is started by the same `start(when, offset)` call, which
// keeps them sample-synced. Position is always expressed in original-audio ms:
//     position = offset + (ctx.currentTime - t0) * rate
// Slow playback only changes `rate`; marks and cursors stay in original time.
//
// Mix rule (identical to the export): mix = master × (p/100·V + q/100·I),
// times the common bus gain reported by /mix/preview-gain. The monitor volume
// is a separate node that is never part of the export.

import { emit } from './state.js';

const ROLES = ['original', 'vocals', 'instrumental'];

export const player = {
  ctx: null,
  monitor: null,
  buffers: {},      // role -> AudioBuffer
  bufferIds: {},    // role -> asset id loaded
  loading: {},      // role -> bool
  source: 'original',
  rate: 1,
  playing: false,
  offsetMs: 0,
  t0: 0,
  nodes: [],        // [{role, src, gain}]
  generation: 0,
  loop: { on: false, start: null, end: null },
  mix: { p: 100, q: 100, master: 1, bus: 1 },
  monitorVolume: 0.9,
  durationMs: 0,
};

function ensureCtx() {
  if (!player.ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    player.ctx = new AC();
    player.monitor = player.ctx.createGain();
    player.monitor.gain.value = player.monitorVolume;
    player.monitor.connect(player.ctx.destination);
  }
  return player.ctx;
}

/** Load / unload buffers so they match the project's assets. */
export async function syncAssets(pid, projectObj, viewObj) {
  const ctx = ensureCtx();
  const wanted = {};
  for (const role of ROLES) {
    const a = (projectObj?.audio || []).find((x) => x.role === role);
    if (a && viewObj?.audio?.[role]?.available) wanted[role] = a.id;
  }
  for (const role of ROLES) {
    if (!wanted[role] && player.buffers[role]) {
      delete player.buffers[role];
      delete player.bufferIds[role];
    }
  }
  const jobs = [];
  for (const [role, id] of Object.entries(wanted)) {
    if (player.bufferIds[role] === id || player.loading[role] === id) continue;
    player.loading[role] = id;
    emit('player');
    jobs.push((async () => {
      try {
        const res = await fetch(`/api/projects/${pid}/audio/${id}/playback.wav`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const buf = await ctx.decodeAudioData(await res.arrayBuffer());
        if (player.loading[role] === id) {
          player.buffers[role] = buf;
          player.bufferIds[role] = id;
        }
      } catch (e) {
        emit('player-error', `加载${roleLabel(role)}音频失败：${e.message}`);
      } finally {
        if (player.loading[role] === id) delete player.loading[role];
        recomputeDuration();
        emit('player');
      }
    })());
  }
  recomputeDuration();
  await Promise.all(jobs);
  // keep the chosen source if it is still available, otherwise fall back
  const avail = availableSources();
  if (avail.length && !avail.includes(player.source)) setSource(avail[0]);
}

export function roleLabel(role) {
  return { original: '原曲', vocals: '人声', instrumental: '伴奏', mix: '自定义混音' }[role] || role;
}

function recomputeDuration() {
  let d = 0;
  for (const b of Object.values(player.buffers)) d = Math.max(d, b.duration * 1000);
  player.durationMs = d;
}

export function availableSources() {
  const out = [];
  for (const r of ROLES) if (player.buffers[r]) out.push(r);
  if (player.buffers.vocals && player.buffers.instrumental) out.push('mix');
  return out;
}

function rolesFor(source) {
  return source === 'mix' ? ['vocals', 'instrumental'] : [source];
}

function gainFor(role) {
  if (player.source !== 'mix') return 1;
  const { p, q, master, bus } = player.mix;
  return (role === 'vocals' ? p / 100 : q / 100) * master * bus;
}

export function positionMs() {
  if (!player.playing || !player.ctx) return player.offsetMs;
  const elapsed = Math.max(0, player.ctx.currentTime - player.t0) * player.rate * 1000;
  let p = player.offsetMs + elapsed;
  const L = player.loop;
  if (L.on && L.start !== null && L.end !== null && L.end > L.start && p >= L.end) {
    p = L.start + ((p - L.start) % (L.end - L.start));
  }
  return Math.min(p, player.durationMs || p);
}

function stopNodes() {
  player.generation += 1;
  for (const n of player.nodes) {
    try { n.src.onended = null; n.src.stop(); } catch (e) { /* already stopped */ }
    try { n.src.disconnect(); n.gain.disconnect(); } catch (e) { /* ignore */ }
  }
  player.nodes = [];
}

export function play(fromMs = positionMs()) {
  const ctx = ensureCtx();
  const roles = rolesFor(player.source).filter((r) => player.buffers[r]);
  if (!roles.length) {
    emit('player-error', '当前音源尚未加载');
    return;
  }
  stopNodes();
  ctx.resume();
  const L = player.loop;
  const loopOn = L.on && L.start !== null && L.end !== null && L.end - L.start > 20;
  let from = Math.max(0, fromMs);
  if (loopOn && (from < L.start || from >= L.end)) from = L.start;
  if (from >= player.durationMs - 5) from = loopOn ? L.start : 0;
  const when = ctx.currentTime + 0.03;
  const gen = player.generation;
  for (const role of roles) {
    const src = ctx.createBufferSource();
    src.buffer = player.buffers[role];
    src.playbackRate.value = player.rate;
    if (loopOn) {
      src.loop = true;
      src.loopStart = L.start / 1000;
      src.loopEnd = L.end / 1000;
    }
    const gain = ctx.createGain();
    gain.gain.value = gainFor(role);
    src.connect(gain).connect(player.monitor);
    src.start(when, from / 1000);
    player.nodes.push({ role, src, gain });
  }
  player.nodes[0].src.onended = () => {
    if (gen !== player.generation) return;
    player.playing = false;
    player.offsetMs = player.durationMs;
    player.nodes = [];
    emit('player');
  };
  player.offsetMs = from;
  player.t0 = when;
  player.playing = true;
  emit('player');
}

export function pause() {
  if (!player.playing) return;
  player.offsetMs = positionMs();
  stopNodes();
  player.playing = false;
  emit('player');
}

export function toggle() {
  if (player.playing) pause(); else play();
}

export function seek(ms) {
  const t = Math.max(0, Math.min(ms, player.durationMs || ms));
  if (player.playing) play(t);
  else {
    player.offsetMs = t;
    emit('player');
  }
}

function restartIfPlaying() {
  if (player.playing) play(positionMs());
  else emit('player');
}

export function setSource(source) {
  player.source = source;
  restartIfPlaying();
}

export function setRate(rate) {
  const pos = positionMs();
  player.rate = rate;
  if (player.playing) play(pos); else emit('player');
}

export function setLoop(start, end) {
  if (start === null || end === null || end - start < 20) {
    player.loop.start = null;
    player.loop.end = null;
    player.loop.on = false;
  } else {
    player.loop.start = Math.max(0, Math.round(start));
    player.loop.end = Math.round(end);
    player.loop.on = true;
  }
  restartIfPlaying();
}

export function toggleLoop(on = !player.loop.on) {
  if (on && (player.loop.start === null || player.loop.end === null)) {
    emit('player-error', '请先在波形上拖选循环区间');
    return;
  }
  player.loop.on = on;
  restartIfPlaying();
}

/** Play [startMs, endMs) looped (used by review / candidate listening). */
export function playRange(startMs, endMs, { loop = true, padMs = 0 } = {}) {
  const s = Math.max(0, startMs - padMs);
  const e = endMs + padMs;
  if (loop) setLoopSilently(s, e);
  play(s);
}

function setLoopSilently(s, e) {
  player.loop.start = Math.round(s);
  player.loop.end = Math.round(e);
  player.loop.on = e - s > 20;
}

/** Gains only: never triggers any server work. */
export function setMix(partial) {
  Object.assign(player.mix, partial);
  if (!player.ctx) return;
  const now = player.ctx.currentTime;
  for (const n of player.nodes) n.gain.gain.setTargetAtTime(gainFor(n.role), now, 0.01);
  emit('mix');
}

export function setMonitorVolume(v) {
  player.monitorVolume = v;
  if (player.monitor) player.monitor.gain.setTargetAtTime(v, player.ctx.currentTime, 0.01);
}
