// Canvas waveform: zoom/scroll, seek, drag-to-loop, unit handles, several units moved together, overlays.
// All coordinates are original-audio ms.

export interface OverlayUnit {
  id: string;
  start: number | null;
  end: number | null;
  label: string;
  color: keyof typeof STATUS_COLORS;
  editable?: boolean;
  locked?: boolean;
}
export interface LineStart { id: string; ms: number; label: string; kind: 'soft' | 'hard'; selected?: boolean }
export interface Overlays {
  units: OverlayUnit[];
  candUnits: OverlayUnit[];
  lineStarts: LineStart[];
  loop: { on: boolean; start: number | null; end: number | null };
  selectedUnitId: string | null;
  /** units selected together (Shift / ⌘ click); two or more: dragged as a group */
  selectedUnitIds?: string[];
  marks: { ms: number; label: string }[];
}
export interface Peaks { per_second: number; mins: number[]; maxs: number[]; duration_ms: number }

interface Callbacks {
  onSeek: (ms: number) => void;
  /** single: click; toggle: ⌘ / Ctrl click; range: Shift click */
  onSelectUnit: (id: string, mode: 'single' | 'toggle' | 'range') => void;
  onEditUnit: (id: string, start: number, end: number) => void;
  /** a group moved (end null) or stretched onto [start, end) */
  onRetimeUnits?: (ids: string[], start: number, end: number | null) => void;
  onLoop: (a: number, b: number) => void;
  getOverlays: () => Overlays;
  getPlayhead: () => { ms: number; playing: boolean };
  onViewChange?: () => void;
}

const RULER_H = 22;
const UNIT_H = 38;
const HANDLE_PX = 7;

export const STATUS_COLORS = {
  ok: '#6366f1',
  manual: '#10b981',
  failed: '#ef4444',
  unaligned: '#f59e0b',
  skipped: '#64748b',
  candidate: '#d946ef',
  compare: '#06b6d4',
} as const;

type Drag =
  | { kind: 'start' | 'end'; unit: OverlayUnit; start: number; end: number }
  | { kind: 'pending' | 'loop'; x0: number; y0: number; ms0: number; ms1?: number }
  /** a group: moved as a whole or stretched by its outer edges ([s0, e0] → [start, end]) */
  | { kind: 'gmove' | 'gstart' | 'gend'; ids: string[]; s0: number; e0: number; start: number; end: number; x0: number; y0: number; ms0: number;
      lo: number; hi: number };

/** ``lo`` / ``hi``: the room around the group up to the nearest units not in it (a group never slides into them) */
interface Group { ids: string[]; start: number; end: number; lo: number; hi: number }

export class Waveform {
  canvas: HTMLCanvasElement;
  cb: Callbacks;
  peaks: { per_second: number; mins: Float32Array; maxs: Float32Array } | null = null;
  durationMs = 0;
  viewStart = 0;
  msPerPx = 20;
  /** keep the playhead in view while playing; off once the user scrolls, on again when playback starts */
  follow = true;
  dirty = true;
  /** drawing happens only while active (dock open, page visible) */
  active = true;
  /** frames drawn so far (tests / diagnostics) */
  frames = 0;
  private raf = 0;
  private lastPlayhead = -1;
  private wasPlaying = false;
  private colors: Record<string, string> | null = null;
  private drag: Drag | null = null;
  private hoverX: number | null = null;
  private ro: ResizeObserver;
  private disposers: (() => void)[] = [];

  constructor(canvas: HTMLCanvasElement, cb: Callbacks) {
    this.canvas = canvas;
    this.cb = cb;
    this.bind();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(canvas);
    this.resize();
  }

  dispose() {
    this.ro.disconnect();
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    for (const d of this.disposers) d();
  }

  /**
   * Something visible changed: draw on the next frame.  Frames are only
   * requested while something changes or the audio plays; nothing runs while
   * the waveform is hidden (dock collapsed, page in the background).
   */
  invalidate() {
    this.dirty = true;
    this.schedule();
  }

  private schedule() {
    if (this.raf || !this.active || typeof requestAnimationFrame === 'undefined') return;
    this.raf = requestAnimationFrame(() => {
      this.raf = 0;
      if (!this.active) return;
      const ph = this.cb.getPlayhead();
      if (this.dirty || ph.playing || ph.ms !== this.lastPlayhead) this.draw();
      if (ph.playing) this.schedule();  // follow the playhead while playing
    });
  }

  /** Show / hide: hidden waveforms stop requesting frames. */
  setActive(on: boolean) {
    if (on === this.active) return;
    this.active = on;
    if (!on && this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
    if (on) this.invalidate();
  }

  /** Re-read the theme colours (on a theme switch, not on every frame). */
  refreshColors() {
    this.colors = null;
    this.invalidate();
  }

  private themeColors() {
    if (!this.colors) {
      const css = getComputedStyle(document.documentElement);
      const v = (n: string, d: string) => css.getPropertyValue(n).trim() || d;
      this.colors = {
        bg: v('--wave-bg', '#101322'), fg: v('--wave-fg', '#7c83ff'), fg2: v('--wave-fg-2', '#a5b4fc'),
        text: v('--wave-text', '#c7cbe0'), grid: v('--wave-grid', 'rgba(255,255,255,0.06)'),
        played: v('--wave-played', 'rgba(124,131,255,0.18)'),
      };
    }
    return this.colors;
  }

  setPeaks(p: Peaks | null) {
    this.peaks = p ? { per_second: p.per_second, mins: Float32Array.from(p.mins), maxs: Float32Array.from(p.maxs) } : null;
    if (p) this.setDuration(Math.max(this.durationMs, p.duration_ms));
    this.invalidate();
  }

  setDuration(ms: number) {
    const first = !this.durationMs;
    this.durationMs = ms || 0;
    if (first && ms) this.msPerPx = Math.max(1, ms / Math.max(200, this.width));
    this.clampView();
    this.invalidate();
  }

  get width() { return this.canvas.clientWidth || 800; }
  get height() { return this.canvas.clientHeight || 180; }
  get spanMs() { return this.width * this.msPerPx; }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(this.width * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.clampView();
    this.invalidate();
  }

  xOf(ms: number) { return (ms - this.viewStart) / this.msPerPx; }
  msAt(x: number) { return this.viewStart + x * this.msPerPx; }

  clampView() {
    const maxZoom = Math.max(1, this.durationMs / Math.max(100, this.width));
    this.msPerPx = Math.min(Math.max(0.25, this.msPerPx), Math.max(maxZoom, 0.25));
    const maxStart = Math.max(0, this.durationMs - this.spanMs);
    this.viewStart = Math.min(Math.max(0, this.viewStart), maxStart);
    this.cb.onViewChange?.();
  }

  /** Fraction of the song visible and scroll position, for the scrollbar. */
  get scrollState() {
    const total = Math.max(1, this.durationMs);
    return { start: this.viewStart / total, size: Math.min(1, this.spanMs / total) };
  }

  scrollTo(frac: number) {
    this.viewStart = frac * this.durationMs;
    this.follow = false;
    this.clampView();
    this.invalidate();
  }

  zoom(factor: number, anchorMs = this.viewStart + this.spanMs / 2) {
    const ax = this.xOf(anchorMs);
    this.msPerPx *= factor;
    this.clampView();
    this.viewStart = anchorMs - ax * this.msPerPx;
    this.clampView();
    this.invalidate();
  }

  zoomAll() {
    this.msPerPx = this.durationMs / Math.max(100, this.width);
    this.viewStart = 0;
    this.clampView();
    this.invalidate();
  }

  /** Show [a, b] with some padding. */
  reveal(a: number, b = a) {
    const span = Math.max(b - a, 200);
    if (span * 1.3 > this.spanMs) this.msPerPx = (span * 1.6) / this.width;
    if (a < this.viewStart || b > this.viewStart + this.spanMs) this.viewStart = a - this.spanMs * 0.2;
    this.follow = true;
    this.clampView();
    this.invalidate();
  }

  private listen<K extends keyof WindowEventMap>(target: Window, ev: K, fn: (e: WindowEventMap[K]) => void) {
    target.addEventListener(ev, fn as EventListener);
    this.disposers.push(() => target.removeEventListener(ev, fn as EventListener));
  }

  private bind() {
    const c = this.canvas;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = c.getBoundingClientRect();
      const ms = this.msAt(e.clientX - rect.left);
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        const d = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) * this.msPerPx;
        this.viewStart += d;
        this.follow = false;
        this.clampView();
        this.invalidate();
      } else {
        this.zoom(e.deltaY > 0 ? 1.2 : 1 / 1.2, ms);
      }
    };
    c.addEventListener('wheel', onWheel, { passive: false });
    this.disposers.push(() => c.removeEventListener('wheel', onWheel));

    const onDown = (e: MouseEvent) => {
      const rect = c.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const ov = this.cb.getOverlays();
      const g = this.group();
      if (g) {
        const base = { ids: g.ids, s0: g.start, e0: g.end, start: g.start, end: g.end, x0: x, y0: y, ms0: this.msAt(x), lo: g.lo, hi: g.hi };
        if (Math.abs(x - this.xOf(g.start)) <= HANDLE_PX) { this.drag = { kind: 'gstart', ...base }; return; }
        if (Math.abs(x - this.xOf(g.end)) <= HANDLE_PX) { this.drag = { kind: 'gend', ...base }; return; }
        if (x > this.xOf(g.start) && x < this.xOf(g.end) && y > RULER_H) { this.drag = { kind: 'gmove', ...base }; return; }
      }
      const sel = ov.units.find((u) => u.id === ov.selectedUnitId);
      if (sel?.editable && sel.start !== null && sel.end !== null) {
        if (Math.abs(x - this.xOf(sel.start)) <= HANDLE_PX) {
          this.drag = { kind: 'start', unit: sel, start: sel.start, end: sel.end };
          return;
        }
        if (Math.abs(x - this.xOf(sel.end)) <= HANDLE_PX) {
          this.drag = { kind: 'end', unit: sel, start: sel.start, end: sel.end };
          return;
        }
      }
      this.drag = { kind: 'pending', x0: x, y0: y, ms0: this.msAt(x) };
    };
    c.addEventListener('mousedown', onDown);
    this.disposers.push(() => c.removeEventListener('mousedown', onDown));

    const onLeave = () => { this.hoverX = null; this.invalidate(); };
    c.addEventListener('mouseleave', onLeave);
    this.disposers.push(() => c.removeEventListener('mouseleave', onLeave));

    this.listen(window, 'mousemove', (e) => {
      const rect = c.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const inside = x >= 0 && x <= rect.width && e.clientY >= rect.top && e.clientY <= rect.bottom;
      const d = this.drag;
      if (!d) {
        const hx = inside ? x : null;
        if (hx === this.hoverX) return;
        this.hoverX = hx;
        if (inside) c.style.cursor = this.hitHandle(x) ? 'ew-resize' : this.inGroup(x) ? 'grab' : 'crosshair';
        this.invalidate();
        return;
      }
      const ms = Math.max(0, Math.min(this.durationMs, this.msAt(x)));
      if (d.kind === 'gmove') {
        const delta = Math.max(d.lo - d.s0, Math.min(d.hi - d.e0, ms - d.ms0));
        d.start = d.s0 + delta;
        d.end = d.e0 + delta;
        c.style.cursor = 'grabbing';
      } else if (d.kind === 'gstart') d.start = Math.max(d.lo, Math.min(ms, d.end - 10));
      else if (d.kind === 'gend') d.end = Math.min(d.hi, Math.max(ms, d.start + 10));
      else if (d.kind === 'start') d.start = Math.min(ms, d.end - 1);
      else if (d.kind === 'end') d.end = Math.max(ms, d.start + 1);
      else if (d.kind === 'pending' && Math.abs(x - d.x0) > 4) (d as any).kind = 'loop';
      if (d.kind === 'loop') d.ms1 = ms;
      this.invalidate();
    });

    this.listen(window, 'mouseup', (e) => {
      const d = this.drag;
      if (!d) return;
      this.drag = null;
      const rect = c.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const mode = e.shiftKey ? 'range' : (e.metaKey || e.ctrlKey) ? 'toggle' : 'single';
      const click = (ms: number) => {
        if (y > this.height - UNIT_H) {
          const hit = this.hitUnit(ms);
          if (hit) this.cb.onSelectUnit(hit.id, mode);
        }
        if (mode === 'single') this.cb.onSeek(Math.max(0, Math.min(this.durationMs, ms)));
      };
      if (d.kind === 'start' || d.kind === 'end') {
        if (Math.round(d.start) !== d.unit.start || Math.round(d.end) !== d.unit.end) {
          this.cb.onEditUnit(d.unit.id, Math.round(d.start), Math.round(d.end));
        }
      } else if (d.kind === 'gmove' || d.kind === 'gstart' || d.kind === 'gend') {
        c.style.cursor = 'grab';
        const moved = Math.abs(x - d.x0) > 2 && (Math.round(d.start) !== Math.round(d.s0) || Math.round(d.end) !== Math.round(d.e0));
        if (!moved) click(this.msAt(x));  // a click inside the group: select / seek as usual
        else if (d.kind === 'gmove') this.cb.onRetimeUnits?.(d.ids, Math.round(d.start), null);
        else this.cb.onRetimeUnits?.(d.ids, Math.round(d.start), Math.round(d.end));
      } else if (d.kind === 'loop' && d.ms1 !== undefined) {
        this.cb.onLoop(Math.min(d.ms0, d.ms1), Math.max(d.ms0, d.ms1));
      } else if (d.kind === 'pending') {
        click(this.msAt(x));
      }
      this.invalidate();
    });
  }

  /** Two or more editable units selected together, with times: their ids and outer span. */
  private group(): Group | null {
    const ov = this.cb.getOverlays();
    const ids = ov.selectedUnitIds ?? [];
    if (ids.length < 2 || !this.cb.onRetimeUnits) return null;
    const want = new Set(ids);
    const us = ov.units.filter((u) => want.has(u.id) && u.start !== null && u.end !== null);
    if (us.length < 2 || us.some((u) => !u.editable)) return null;
    const start = Math.min(...us.map((u) => u.start!));
    const end = Math.max(...us.map((u) => u.end!));
    const outside = ov.units.filter((u) => !want.has(u.id) && u.start !== null && u.end !== null);
    const before = outside.filter((u) => u.start! < start).map((u) => u.end!);
    const after = outside.filter((u) => u.end! > end).map((u) => u.start!);
    const lo = Math.min(start, before.length ? Math.max(...before) : 0);
    const hi = Math.max(end, after.length ? Math.min(...after) : this.durationMs);
    return { ids: us.map((u) => u.id), start, end, lo, hi };
  }

  private inGroup(x: number) {
    const g = this.group();
    return !!g && x > this.xOf(g.start) && x < this.xOf(g.end);
  }

  private hitHandle(x: number) {
    const g = this.group();
    if (g) {
      if (Math.abs(x - this.xOf(g.start)) <= HANDLE_PX) return 'start';
      if (Math.abs(x - this.xOf(g.end)) <= HANDLE_PX) return 'end';
      return null;
    }
    const ov = this.cb.getOverlays();
    const sel = ov.units.find((u) => u.id === ov.selectedUnitId);
    if (!sel?.editable || sel.start === null || sel.end === null) return null;
    if (Math.abs(x - this.xOf(sel.start)) <= HANDLE_PX) return 'start';
    if (Math.abs(x - this.xOf(sel.end)) <= HANDLE_PX) return 'end';
    return null;
  }

  private hitUnit(ms: number) {
    return this.cb.getOverlays().units.find((u) => u.start !== null && u.end !== null && ms >= u.start && ms < u.end) ?? null;
  }

  draw() {
    this.frames += 1;
    const playhead = this.cb.getPlayhead();
    this.lastPlayhead = playhead.ms;
    if (playhead.playing && !this.wasPlaying) this.follow = true;  // (re)started: follow again
    this.wasPlaying = playhead.playing;
    if (this.follow && playhead.playing && (playhead.ms > this.viewStart + this.spanMs * 0.92 || playhead.ms < this.viewStart)) {
      this.viewStart = playhead.ms - this.spanMs * 0.08;
      this.clampView();
    }
    const ctx = this.canvas.getContext('2d');
    if (!ctx) {
      this.dirty = false;
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    const W = this.width;
    const H = this.height;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const { bg, fg, fg2, text, grid, played } = this.themeColors();
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);


    const waveTop = RULER_H;
    const waveH = H - RULER_H - UNIT_H;
    const mid = waveTop + waveH / 2;
    const ov = this.cb.getOverlays();
    const font = (px: number, w = 500) => `${w} ${px}px Inter, "PingFang SC", system-ui, sans-serif`;

    // grid + ruler ticks
    const step = niceStep(this.msPerPx * 90);
    const first = Math.ceil(this.viewStart / step) * step;
    ctx.fillStyle = grid;
    for (let t = first; t < this.viewStart + this.spanMs; t += step) ctx.fillRect(Math.round(this.xOf(t)), RULER_H, 1, H - RULER_H);

    // played region tint (from view start to the playhead)
    const phx = this.xOf(playhead.ms);
    if (phx > 0) {
      ctx.fillStyle = played;
      ctx.fillRect(0, waveTop, Math.min(W, phx), waveH);
    }

    // loop region
    const loop = ov.loop;
    const drawLoop = (a: number, b: number, alpha: number) => {
      ctx.fillStyle = `rgba(250, 204, 21, ${alpha})`;
      ctx.fillRect(this.xOf(a), RULER_H, (b - a) / this.msPerPx, H - RULER_H);
      ctx.fillStyle = `rgba(250, 204, 21, ${Math.min(1, alpha * 4)})`;
      ctx.fillRect(this.xOf(a), RULER_H, 1.5, H - RULER_H);
      ctx.fillRect(this.xOf(b) - 1.5, RULER_H, 1.5, H - RULER_H);
    };
    if (loop.start !== null && loop.end !== null) drawLoop(loop.start, loop.end, loop.on ? 0.14 : 0.05);
    if (this.drag?.kind === 'loop' && this.drag.ms1 !== undefined) {
      drawLoop(Math.min(this.drag.ms0, this.drag.ms1), Math.max(this.drag.ms0, this.drag.ms1), 0.2);
    }

    // peaks (mirrored bars; played part brighter)
    if (this.peaks) {
      const { per_second: ps, mins, maxs } = this.peaks;
      const n = mins.length;
      const grad = ctx.createLinearGradient(0, waveTop, 0, waveTop + waveH);
      grad.addColorStop(0, fg2);
      grad.addColorStop(0.5, fg);
      grad.addColorStop(1, fg2);
      for (let x = 0; x < W; x++) {
        const t0 = this.viewStart + x * this.msPerPx;
        let i0 = Math.floor((t0 * ps) / 1000);
        let i1 = Math.floor(((t0 + this.msPerPx) * ps) / 1000);
        if (i0 >= n) break;
        if (i1 <= i0) i1 = i0 + 1;
        i0 = Math.max(0, i0);
        i1 = Math.min(n, i1);
        let lo = 0;
        let hi = 0;
        for (let i = i0; i < i1; i++) {
          if (mins[i] < lo) lo = mins[i];
          if (maxs[i] > hi) hi = maxs[i];
        }
        const y1 = mid - hi * (waveH / 2) * 0.94;
        const y2 = mid - lo * (waveH / 2) * 0.94;
        ctx.globalAlpha = x <= phx ? 1 : 0.62;
        ctx.fillStyle = grad;
        ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
      }
      ctx.globalAlpha = 1;
    } else {
      ctx.fillStyle = text;
      ctx.font = font(13);
      ctx.textAlign = 'center';
      ctx.fillText('上传音频后显示波形', W / 2, mid);
      ctx.textAlign = 'left';
    }

    // ruler
    ctx.fillStyle = 'rgba(0,0,0,0.28)';
    ctx.fillRect(0, 0, W, RULER_H);
    ctx.fillStyle = text;
    ctx.font = font(10);
    for (let t = first; t < this.viewStart + this.spanMs; t += step) {
      const x = this.xOf(t);
      ctx.globalAlpha = 0.5;
      ctx.fillRect(x, RULER_H - 6, 1, 6);
      ctx.globalAlpha = 0.85;
      ctx.fillText(fmtRuler(t, step), x + 4, 14);
    }
    ctx.globalAlpha = 1;

    // line starts (effective LRC anchors) with label pills; labels that would
    // collide with the previous one are skipped (the marker line is kept),
    // the selected line's label always wins
    let labelEnd = -Infinity;
    const starts = [...ov.lineStarts].sort((a, b) => a.ms - b.ms);
    const selectedStart = starts.find((l) => l.selected);
    for (const ls of starts) {
      const x = this.xOf(ls.ms);
      if (x < -240 || x > W + 5) continue;
      const color = ls.selected ? '#fb923c' : ls.kind === 'hard' ? '#34d399' : 'rgba(251, 146, 60, 0.6)';
      ctx.strokeStyle = color;
      ctx.lineWidth = ls.selected ? 1.5 : 1;
      ctx.setLineDash(ls.kind === 'hard' ? [] : [4, 3]);
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, RULER_H);
      ctx.lineTo(Math.round(x) + 0.5, H - UNIT_H);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineWidth = 1;
      ctx.font = font(11, ls.selected ? 600 : 500);
      const label = ls.label.length > 22 ? `${ls.label.slice(0, 21)}…` : ls.label;
      const tw = ctx.measureText(label).width;
      const selX = selectedStart && !ls.selected ? this.xOf(selectedStart.ms) : null;
      const hitsSelected = selX !== null && x < selX && x + tw + 16 > selX;
      if (!ls.selected && (x + 3 < labelEnd || hitsSelected)) continue;
      labelEnd = x + tw + 16;
      ctx.fillStyle = ls.selected ? 'rgba(251,146,60,0.95)' : 'rgba(15,17,28,0.72)';
      roundRect(ctx, x + 3, RULER_H + 4, tw + 10, 17, 5);
      ctx.fill();
      ctx.fillStyle = ls.selected ? '#1c0f02' : text;
      ctx.fillText(label, x + 8, RULER_H + 16);
    }

    // extra marks (calibration checks)
    for (const m of ov.marks) {
      const x = this.xOf(m.ms);
      if (x < 0 || x > W) continue;
      ctx.fillStyle = '#38bdf8';
      ctx.fillRect(x - 0.5, RULER_H, 1.5, H - RULER_H - UNIT_H);
      ctx.beginPath();
      ctx.moveTo(x - 4, RULER_H);
      ctx.lineTo(x + 4, RULER_H);
      ctx.lineTo(x, RULER_H + 6);
      ctx.fill();
    }

    // units band
    const bandTop = H - UNIT_H;
    ctx.fillStyle = 'rgba(0,0,0,0.22)';
    ctx.fillRect(0, bandTop, W, UNIT_H);
    const group = this.group();
    const inSel = new Set(group?.ids ?? []);
    const drawUnits = (units: OverlayUnit[], yOff: number, h: number) => {
      for (const u of units) {
        if (u.start === null || u.end === null) continue;
        let s = u.start;
        let e = u.end;
        const d = this.drag;
        if (d && (d.kind === 'start' || d.kind === 'end') && d.unit.id === u.id) {
          s = d.start;
          e = d.end;
        } else if (d && (d.kind === 'gmove' || d.kind === 'gstart' || d.kind === 'gend') && d.ids.includes(u.id)) {
          const k = (d.end - d.start) / Math.max(1, d.e0 - d.s0);  // the group's new span
          s = d.start + (u.start - d.s0) * k;
          e = d.start + (u.end - d.s0) * k;
        }
        const x1 = this.xOf(s);
        const x2 = this.xOf(e);
        if (x2 < 0 || x1 > W) continue;
        const w = Math.max(2, x2 - x1 - 1.5);
        const selected = u.id === ov.selectedUnitId || inSel.has(u.id);
        ctx.fillStyle = STATUS_COLORS[u.color] ?? STATUS_COLORS.ok;
        ctx.globalAlpha = selected ? 1 : 0.82;
        roundRect(ctx, x1, yOff, w, h, Math.min(5, w / 2));
        ctx.fill();
        ctx.globalAlpha = 1;
        if (selected) {
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 2;
          roundRect(ctx, x1, yOff, w, h, Math.min(5, w / 2));
          ctx.stroke();
          ctx.lineWidth = 1;
          if (u.editable && !group) {
            // full-height handles for dragging start / end
            ctx.fillStyle = 'rgba(255,255,255,0.9)';
            ctx.fillRect(x1 - 1.5, waveTop, 3, bandTop - waveTop + h + 3);
            ctx.fillRect(x2 - 1.5, waveTop, 3, bandTop - waveTop + h + 3);
            ctx.fillStyle = 'rgba(255,255,255,0.08)';
            ctx.fillRect(x1, waveTop, x2 - x1, waveH);
          }
        }
        if (w > 16) {
          ctx.fillStyle = '#fff';
          ctx.font = font(12, 600);
          ctx.fillText(u.label, x1 + 5, yOff + h / 2 + 4, w - 8);
        }
        if (u.locked && w > 10) {
          ctx.fillStyle = '#fde047';
          ctx.beginPath();
          ctx.arc(x1 + w - 5, yOff + 5, 2.5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    };
    if (ov.candUnits.length) {
      drawUnits(ov.units, bandTop + 4, 15);
      drawUnits(ov.candUnits, bandTop + 21, 13);
    } else {
      drawUnits(ov.units, bandTop + 5, UNIT_H - 10);
    }
    const gd = this.drag && (this.drag.kind === 'gmove' || this.drag.kind === 'gstart' || this.drag.kind === 'gend') ? this.drag : null;
    const gx1 = group ? this.xOf(gd ? gd.start : group.start) : 0;
    const gx2 = group ? this.xOf(gd ? gd.end : group.end) : 0;
    if (group && gx2 >= 0 && gx1 <= W) {
      // the group's outer edges: drag them to stretch it, drag inside to move it
      const x1 = gx1;
      const x2 = gx2;
      ctx.fillStyle = 'rgba(255,255,255,0.08)';
      ctx.fillRect(x1, waveTop, x2 - x1, waveH);
      ctx.fillStyle = 'rgba(255,255,255,0.95)';
      ctx.fillRect(x1 - 1.5, waveTop, 3, bandTop - waveTop + UNIT_H - 2);
      ctx.fillRect(x2 - 1.5, waveTop, 3, bandTop - waveTop + UNIT_H - 2);
      ctx.font = font(11, 600);
      const label = `${group.ids.length} 个单元 · 拖动整体移动，拖两端按比例伸缩`;
      const tw = ctx.measureText(label).width;
      const lx = Math.max(4, Math.min(W - tw - 12, x1));
      ctx.fillStyle = 'rgba(15,17,28,0.85)';
      roundRect(ctx, lx, waveTop + 4, tw + 10, 18, 4);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.fillText(label, lx + 5, waveTop + 17);
    }

    // hover time
    if (this.hoverX !== null && !this.drag) {
      const hx = this.hoverX;
      ctx.fillStyle = 'rgba(255,255,255,0.35)';
      ctx.fillRect(hx, RULER_H, 1, H - RULER_H);
      const label = fmtRuler(this.msAt(hx), 1);
      ctx.font = font(10, 600);
      const tw = ctx.measureText(label).width;
      const lx = Math.min(W - tw - 10, hx + 4);
      ctx.fillStyle = 'rgba(15,17,28,0.9)';
      roundRect(ctx, lx, 3, tw + 8, 16, 4);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.fillText(label, lx + 4, 14);
    }

    // playhead
    if (phx >= 0 && phx <= W) {
      ctx.fillStyle = '#f43f5e';
      ctx.fillRect(phx - 1, 0, 2, H);
      ctx.beginPath();
      ctx.moveTo(phx - 6, 0);
      ctx.lineTo(phx + 6, 0);
      ctx.lineTo(phx, 8);
      ctx.fill();
    }
    this.dirty = false;
  }
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  if ((ctx as any).roundRect) (ctx as any).roundRect(x, y, w, h, r);
  else ctx.rect(x, y, w, h);
}

function niceStep(ms: number) {
  const steps = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000];
  for (const s of steps) if (s >= ms) return s;
  return 120000;
}

function fmtRuler(t: number, step: number) {
  const m = Math.floor(t / 60000);
  const s = (t % 60000) / 1000;
  const digits = step < 100 ? 3 : step < 1000 ? 1 : 0;
  return `${m}:${s.toFixed(digits).padStart(digits ? 3 + digits : 2, '0')}`;
}
