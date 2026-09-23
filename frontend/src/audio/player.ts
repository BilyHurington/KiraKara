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

import { useEffect, useState, useSyncExternalStore } from 'react';
import type { ProjectView, Role, Source } from '@/lib/types';

const ROLES: Role[] = ['original', 'vocals', 'instrumental'];

interface Node { role: Role; src: AudioBufferSourceNode; gain: GainNode }

class Player {
  ctx: AudioContext | null = null;
  monitor: GainNode | null = null;
  buffers: Partial<Record<Role, AudioBuffer>> = {};
  bufferIds: Partial<Record<Role, string>> = {};
  loading: Partial<Record<Role, string>> = {};
  source: Source = 'original';
  rate = 1;
  playing = false;
  offsetMs = 0;
  t0 = 0;
  nodes: Node[] = [];
  generation = 0;
  loop: { on: boolean; start: number | null; end: number | null } = { on: false, start: null, end: null };
  mix = { p: 100, q: 100, master: 1, bus: 1 };
  monitorVolume = 0.9;
  durationMs = 0;
  error: string | null = null;

  // --- subscription (React) -------------------------------------------------
  private version = 0;
  private listeners = new Set<() => void>();
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getVersion = () => this.version;
  emit() {
    this.version += 1;
    for (const fn of this.listeners) fn();
  }

  private ensureCtx(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.monitor = this.ctx.createGain();
      this.monitor.gain.value = this.monitorVolume;
      this.monitor.connect(this.ctx.destination);
    }
    return this.ctx;
  }

  /** Load / unload buffers so they match the project's assets. */
  async syncAssets(pid: string, pv: ProjectView) {
    const ctx = this.ensureCtx();
    const wanted: Partial<Record<Role, string>> = {};
    for (const role of ROLES) {
      const a = pv.project.audio.find((x) => x.role === role);
      if (a && pv.view.audio[role]?.available) wanted[role] = a.id;
    }
    for (const role of ROLES) {
      if (!wanted[role] && this.buffers[role]) {
        delete this.buffers[role];
        delete this.bufferIds[role];
      }
    }
    const jobs: Promise<void>[] = [];
    for (const [role, id] of Object.entries(wanted) as [Role, string][]) {
      if (this.bufferIds[role] === id || this.loading[role] === id) continue;
      this.loading[role] = id;
      this.emit();
      jobs.push((async () => {
        try {
          const res = await fetch(`/api/projects/${pid}/audio/${id}/playback.wav`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const buf = await ctx.decodeAudioData(await res.arrayBuffer());
          if (this.loading[role] === id) {
            this.buffers[role] = buf;
            this.bufferIds[role] = id;
          }
        } catch (e: any) {
          this.error = `加载音频失败：${e?.message ?? e}`;
        } finally {
          if (this.loading[role] === id) delete this.loading[role];
          this.recomputeDuration();
          this.emit();
        }
      })());
    }
    this.recomputeDuration();
    await Promise.all(jobs);
    const avail = this.availableSources();
    if (avail.length && !avail.includes(this.source)) this.setSource(avail[0]);
  }

  reset() {
    this.stopNodes();
    this.playing = false;
    this.buffers = {};
    this.bufferIds = {};
    this.loading = {};
    this.offsetMs = 0;
    this.durationMs = 0;
    this.loop = { on: false, start: null, end: null };
    this.emit();
  }

  private recomputeDuration() {
    let d = 0;
    for (const b of Object.values(this.buffers)) if (b) d = Math.max(d, b.duration * 1000);
    this.durationMs = d;
  }

  availableSources(): Source[] {
    const out: Source[] = ROLES.filter((r) => this.buffers[r]);
    if (this.buffers.vocals && this.buffers.instrumental) out.push('mix');
    return out;
  }

  get isLoading() {
    return Object.keys(this.loading).length > 0;
  }

  private rolesFor(source: Source): Role[] {
    return source === 'mix' ? ['vocals', 'instrumental'] : [source];
  }

  private gainFor(role: Role) {
    if (this.source !== 'mix') return 1;
    const { p, q, master, bus } = this.mix;
    return (role === 'vocals' ? p / 100 : q / 100) * master * bus;
  }

  positionMs(): number {
    if (!this.playing || !this.ctx) return this.offsetMs;
    const elapsed = Math.max(0, this.ctx.currentTime - this.t0) * this.rate * 1000;
    let p = this.offsetMs + elapsed;
    const L = this.loop;
    if (L.on && L.start !== null && L.end !== null && L.end > L.start && p >= L.end) {
      p = L.start + ((p - L.start) % (L.end - L.start));
    }
    return Math.min(p, this.durationMs || p);
  }

  private stopNodes() {
    this.generation += 1;
    for (const n of this.nodes) {
      try { n.src.onended = null; n.src.stop(); } catch { /* already stopped */ }
      try { n.src.disconnect(); n.gain.disconnect(); } catch { /* ignore */ }
    }
    this.nodes = [];
  }

  play(fromMs = this.positionMs()) {
    const ctx = this.ensureCtx();
    const roles = this.rolesFor(this.source).filter((r) => this.buffers[r]);
    if (!roles.length) {
      this.error = '当前音源尚未加载';
      this.emit();
      return;
    }
    this.stopNodes();
    void ctx.resume();
    const L = this.loop;
    const loopOn = L.on && L.start !== null && L.end !== null && L.end - L.start > 20;
    let from = Math.max(0, fromMs);
    if (loopOn && (from < L.start! || from >= L.end!)) from = L.start!;
    if (from >= this.durationMs - 5) from = loopOn ? L.start! : 0;
    const when = ctx.currentTime + 0.03;
    const gen = this.generation;
    for (const role of roles) {
      const src = ctx.createBufferSource();
      src.buffer = this.buffers[role]!;
      src.playbackRate.value = this.rate;
      if (loopOn) {
        src.loop = true;
        src.loopStart = L.start! / 1000;
        src.loopEnd = L.end! / 1000;
      }
      const gain = ctx.createGain();
      gain.gain.value = this.gainFor(role);
      src.connect(gain).connect(this.monitor!);
      src.start(when, from / 1000);
      this.nodes.push({ role, src, gain });
    }
    this.nodes[0].src.onended = () => {
      if (gen !== this.generation) return;
      this.playing = false;
      this.offsetMs = this.durationMs;
      this.nodes = [];
      this.emit();
    };
    this.offsetMs = from;
    this.t0 = when;
    this.playing = true;
    this.error = null;
    this.emit();
  }

  pause() {
    if (!this.playing) return;
    this.offsetMs = this.positionMs();
    this.stopNodes();
    this.playing = false;
    this.emit();
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  seek(ms: number) {
    const t = Math.max(0, Math.min(ms, this.durationMs || ms));
    if (this.playing) this.play(t);
    else {
      this.offsetMs = t;
      this.emit();
    }
  }

  private restartIfPlaying() {
    if (this.playing) this.play(this.positionMs());
    else this.emit();
  }

  setSource(source: Source) {
    this.source = source;
    this.restartIfPlaying();
  }

  setRate(rate: number) {
    const pos = this.positionMs();
    this.rate = rate;
    if (this.playing) this.play(pos);
    else this.emit();
  }

  setLoop(start: number | null, end: number | null) {
    if (start === null || end === null || end - start < 20) {
      this.loop = { on: false, start: null, end: null };
    } else {
      this.loop = { on: true, start: Math.max(0, Math.round(start)), end: Math.round(end) };
    }
    this.restartIfPlaying();
  }

  toggleLoop(on = !this.loop.on): boolean {
    if (on && (this.loop.start === null || this.loop.end === null)) return false;
    this.loop.on = on;
    this.restartIfPlaying();
    return true;
  }

  /** Play [startMs, endMs) looped (review / candidate listening). */
  playRange(startMs: number, endMs: number, { loop = true, padMs = 0 } = {}) {
    const s = Math.max(0, startMs - padMs);
    const e = endMs + padMs;
    if (loop) this.loop = { on: e - s > 20, start: Math.round(s), end: Math.round(e) };
    this.play(s);
  }

  /** Gains only: never triggers any server work. */
  setMix(partial: Partial<Player['mix']>) {
    Object.assign(this.mix, partial);
    if (this.ctx) {
      const now = this.ctx.currentTime;
      for (const n of this.nodes) n.gain.gain.setTargetAtTime(this.gainFor(n.role), now, 0.01);
    }
    this.emit();
  }

  setMonitorVolume(v: number) {
    this.monitorVolume = v;
    if (this.monitor && this.ctx) this.monitor.gain.setTargetAtTime(v, this.ctx.currentTime, 0.01);
    this.emit();
  }
}

export const player = new Player();

/** Re-render on player state changes (not on every animation frame). */
export function usePlayer() {
  useSyncExternalStore(player.subscribe, player.getVersion);
  return player;
}

/** Position updated every animation frame while playing. */
export function usePlayhead(): number {
  usePlayer();
  const [ms, setMs] = useState(player.positionMs());
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      setMs(player.positionMs());
      if (player.playing) raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [player.playing, player.offsetMs, player.rate]);
  return ms;
}
