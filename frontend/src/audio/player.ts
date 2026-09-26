// Synchronized multi-stem player on ONE AudioContext.
//
// Only the track(s) the current source needs are decoded (both stems only for
// the mix preview); another project frees them at once.  All stems are decoded
// from the server's `playback.wav` (same decoder as the alignment), so buffer
// time 0 == original audio time 0. Every stem needed for the current source is
// started by the same `start(when, offset)` call, which keeps them
// sample-synced. Position is always expressed in original-audio ms:
//     position = offset + (ctx.currentTime - t0) * rate
// Slow playback only changes `rate`; marks and cursors stay in original time.
//
// Loop: only `playRange` (and the user's own loop selection) turns a loop on;
// an explicit jump outside the loop range (seek, play(from)) turns it off.
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
  /** the open project and its usable audio assets (role → asset id) */
  pid: string | null = null;
  assets: Partial<Record<Role, string>> = {};
  assetDurations: Partial<Record<Role, number>> = {};
  /** decoded buffers: only the roles the current source needs */
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
  /** where playback starts once the buffers it waits for are decoded */
  private pendingPlay: number | null = null;
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

  /**
   * Follow the project's audio assets.  Another project (or a replaced
   * original) stops playback and resets position and loop; only the buffers
   * the current source needs are decoded.
   */
  async syncAssets(pid: string, pv: ProjectView) {
    const wanted: Partial<Record<Role, string>> = {};
    const durations: Partial<Record<Role, number>> = {};
    for (const role of ROLES) {
      const a = pv.project.audio.find((x) => x.role === role);
      const v = pv.view.audio[role];
      if (a && v?.available && !v.outdated) {
        wanted[role] = a.id;
        durations[role] = v.duration_ms || a.duration_ms;
      }
    }
    if (pid !== this.pid) {
      this.reset();
      this.pid = pid;
    } else if (this.assets.original && wanted.original !== this.assets.original) {
      // the original was replaced: the old position and loop mean nothing any more
      this.stopNodes();
      this.playing = false;
      this.pendingPlay = null;
      this.offsetMs = 0;
      this.loop = { on: false, start: null, end: null };
    }
    this.assets = wanted;
    this.assetDurations = durations;
    for (const role of ROLES) {
      if (this.bufferIds[role] && this.bufferIds[role] !== wanted[role]) this.dropBuffer(role);
    }
    const avail = this.availableSources();
    if (avail.length && !avail.includes(this.source)) this.source = avail[0];
    this.recomputeDuration();
    this.emit();
    await this.ensureLoaded();
  }

  private dropBuffer(role: Role) {
    if (this.nodes.some((n) => n.role === role)) {
      const pos = this.positionMs();
      this.stopNodes();
      this.offsetMs = pos;
      this.playing = false;
    }
    delete this.buffers[role];
    delete this.bufferIds[role];
  }

  /** Decode what the current source needs and free what it does not. */
  private async ensureLoaded() {
    const pid = this.pid;
    if (!pid) return;
    const need = new Set(this.rolesFor(this.source));
    for (const role of ROLES) {
      if (!need.has(role) && this.buffers[role] && !this.nodes.some((n) => n.role === role)) {
        delete this.buffers[role];
        delete this.bufferIds[role];
      }
    }
    const jobs: Promise<void>[] = [];
    for (const role of need) {
      const id = this.assets[role];
      if (!id || this.bufferIds[role] === id || this.loading[role] === id) continue;
      this.loading[role] = id;
      this.emit();
      const ctx = this.ensureCtx();
      jobs.push((async () => {
        try {
          const res = await fetch(`/api/projects/${pid}/audio/${id}/playback.wav`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const buf = await ctx.decodeAudioData(await res.arrayBuffer());
          // still wanted: same project, same asset, and the source still needs it
          if (this.loading[role] === id && this.pid === pid && this.assets[role] === id
            && this.rolesFor(this.source).includes(role)) {
            this.buffers[role] = buf;
            this.bufferIds[role] = id;
          }
        } catch (e: any) {
          if (this.pid === pid) {
            this.error = `加载音频失败：${e?.message ?? e}`;
            this.pendingPlay = null;
          }
        } finally {
          if (this.loading[role] === id) delete this.loading[role];
          this.recomputeDuration();
          this.emit();
        }
      })());
    }
    await Promise.all(jobs);
    if (this.pendingPlay !== null && this.pid === pid && this.ready()) {
      const from = this.pendingPlay;
      this.pendingPlay = null;
      this.start(from);
    }
  }

  /** The buffers of a source are decoded. */
  ready(source: Source = this.source) {
    return this.rolesFor(source).every((r) => this.buffers[r]);
  }

  reset() {
    this.stopNodes();
    this.playing = false;
    this.pendingPlay = null;
    this.pid = null;
    this.assets = {};
    this.assetDurations = {};
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
    for (const r of this.rolesFor(this.source)) {
      const b = this.buffers[r];
      d = Math.max(d, b ? b.duration * 1000 : this.assetDurations[r] ?? 0);
    }
    if (!d) for (const r of ROLES) d = Math.max(d, this.assetDurations[r] ?? 0);
    this.durationMs = d;
  }

  /** Sources the project has (decoded on demand). */
  availableSources(): Source[] {
    const out: Source[] = ROLES.filter((r) => this.assets[r]);
    if (this.assets.vocals && this.assets.instrumental) out.push('mix');
    return out;
  }

  get isLoading() {
    return Object.keys(this.loading).length > 0;
  }

  /** Waiting for buffers before playback starts. */
  get starting() {
    return this.pendingPlay !== null;
  }

  private rolesFor(source: Source): Role[] {
    return source === 'mix' ? ['vocals', 'instrumental'] : [source];
  }

  private gainFor(role: Role) {
    if (this.source !== 'mix') return 1;
    const { p, q, master, bus } = this.mix;
    return (role === 'vocals' ? p / 100 : q / 100) * master * bus;
  }

  private loopActive() {
    const L = this.loop;
    return L.on && L.start !== null && L.end !== null && L.end - L.start > 20;
  }

  /** Is `ms` outside the active loop (an explicit jump there ends the loop)? */
  private outsideLoop(ms: number) {
    return this.loopActive() && (ms < this.loop.start! || ms >= this.loop.end!);
  }

  positionMs(): number {
    if (!this.playing || !this.ctx) return this.offsetMs;
    const elapsed = Math.max(0, this.ctx.currentTime - this.t0) * this.rate * 1000;
    let p = this.offsetMs + elapsed;
    const L = this.loop;
    if (this.loopActive() && p >= L.end!) {
      p = L.start! + ((p - L.start!) % (L.end! - L.start!));
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

  /**
   * Play from `fromMs` (default: the current position).  An explicit start
   * outside the loop range turns the loop off (the range is kept for L).
   */
  play(fromMs?: number) {
    if (fromMs !== undefined && this.outsideLoop(fromMs)) this.loop = { ...this.loop, on: false };
    this.start(fromMs ?? this.positionMs());
  }

  private start(fromMs: number) {
    const roles = this.rolesFor(this.source);
    if (!roles.every((r) => this.buffers[r])) {
      if (!roles.every((r) => this.assets[r])) {
        this.error = '当前音源尚未加载';
        this.emit();
        return;
      }
      // not decoded yet: start as soon as it is
      this.stopNodes();
      this.offsetMs = Math.max(0, fromMs);
      this.playing = false;
      this.pendingPlay = this.offsetMs;
      this.emit();
      void this.ensureLoaded();
      return;
    }
    const ctx = this.ensureCtx();
    this.pendingPlay = null;
    this.stopNodes();
    void ctx.resume();
    const L = this.loop;
    const loopOn = this.loopActive();
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
    if (this.pendingPlay !== null) {
      this.pendingPlay = null;
      this.emit();
      return;
    }
    if (!this.playing) return;
    this.offsetMs = this.positionMs();
    this.stopNodes();
    this.playing = false;
    this.emit();
  }

  toggle() {
    if (this.playing || this.pendingPlay !== null) this.pause();
    else this.play();
  }

  /** Jump to `ms`; outside the active loop this turns the loop off. */
  seek(ms: number) {
    const t = Math.max(0, Math.min(ms, this.durationMs || ms));
    if (this.outsideLoop(t)) this.loop = { ...this.loop, on: false };
    if (this.playing || this.pendingPlay !== null) this.start(t);
    else {
      this.offsetMs = t;
      this.emit();
    }
  }

  /** Continue from a position computed *before* a change (loop, rate). */
  private restartAt(pos: number) {
    if (this.playing) {
      this.start(pos);
      return;
    }
    if (this.pendingPlay !== null) this.pendingPlay = pos;
    else this.offsetMs = pos;
    this.emit();
  }

  setSource(source: Source) {
    const pos = this.positionMs();
    const wasPlaying = this.playing || this.pendingPlay !== null;
    this.source = source;
    this.recomputeDuration();
    if (wasPlaying) {
      this.start(pos);
      return;
    }
    this.offsetMs = pos;
    this.emit();
    void this.ensureLoaded();
  }

  setRate(rate: number) {
    const pos = this.positionMs();
    this.rate = rate;
    this.restartAt(pos);
  }

  setLoop(start: number | null, end: number | null) {
    const pos = this.positionMs();  // before the loop changes: positionMs wraps inside the loop
    if (start === null || end === null || end - start < 20) {
      this.loop = { on: false, start: null, end: null };
    } else {
      this.loop = { on: true, start: Math.max(0, Math.round(start)), end: Math.round(end) };
    }
    this.restartAt(pos);
  }

  toggleLoop(on = !this.loop.on): boolean {
    if (on && (this.loop.start === null || this.loop.end === null)) return false;
    const pos = this.positionMs();  // before the loop changes (see setLoop)
    this.loop = { ...this.loop, on };
    this.restartAt(pos);
    return true;
  }

  /** Play [startMs, endMs), looped by default (review / candidate listening). */
  playRange(startMs: number, endMs: number, { loop = true, padMs = 0 } = {}) {
    const s = Math.max(0, startMs - padMs);
    const e = endMs + padMs;
    if (loop) this.loop = { on: e - s > 20, start: Math.round(s), end: Math.round(e) };
    else if (this.outsideLoop(s)) this.loop = { ...this.loop, on: false };
    this.start(s);
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
