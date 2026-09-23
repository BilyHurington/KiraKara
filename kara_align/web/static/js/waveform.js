// Canvas waveform with zoom/scroll, seek, loop selection and overlays.
// All coordinates are original-audio ms.

const RULER_H = 18;
const UNIT_H = 34;
const HANDLE_PX = 6;

const STATUS_COLORS = {
  ok: '#3b82f6',
  manual: '#16a34a',
  failed: '#dc2626',
  unaligned: '#f59e0b',
  skipped: '#9ca3af',
  candidate: '#a855f7',
};

export class Waveform {
  constructor(canvas, scrollbar, callbacks) {
    this.canvas = canvas;
    this.scrollbar = scrollbar;
    this.cb = callbacks; // {onSeek, onSelectUnit, onEditUnit, onLoop, getOverlays, getPlayhead}
    this.peaks = null;   // {per_second, mins, maxs, duration_ms}
    this.durationMs = 0;
    this.viewStart = 0;
    this.msPerPx = 20;
    this.follow = true;
    this.dirty = true;
    this.drag = null;
    this.hover = null;
    this._bind();
    this.resize();
    window.addEventListener('resize', () => this.resize());
  }

  setPeaks(peaks) {
    this.peaks = peaks ? {
      per_second: peaks.per_second,
      mins: Float32Array.from(peaks.mins),
      maxs: Float32Array.from(peaks.maxs),
      duration_ms: peaks.duration_ms,
    } : null;
    if (peaks) this.setDuration(Math.max(this.durationMs, peaks.duration_ms));
    this.dirty = true;
  }

  setDuration(ms) {
    const first = !this.durationMs;
    this.durationMs = ms || 0;
    if (first && ms) this.msPerPx = Math.max(1, ms / Math.max(200, this.width));
    this.clampView();
    this.dirty = true;
  }

  get width() { return this.canvas.clientWidth || 800; }
  get height() { return this.canvas.clientHeight || 170; }
  get spanMs() { return this.width * this.msPerPx; }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(this.width * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.dirty = true;
  }

  xOf(ms) { return (ms - this.viewStart) / this.msPerPx; }
  msAt(x) { return this.viewStart + x * this.msPerPx; }

  clampView() {
    const maxStart = Math.max(0, this.durationMs - this.spanMs);
    this.viewStart = Math.min(Math.max(0, this.viewStart), maxStart);
    const maxZoom = Math.max(1, this.durationMs / Math.max(100, this.width));
    this.msPerPx = Math.min(Math.max(0.25, this.msPerPx), Math.max(maxZoom, 0.25));
    this.updateScrollbar();
  }

  updateScrollbar() {
    const maxStart = Math.max(0, this.durationMs - this.spanMs);
    this.scrollbar.max = String(Math.max(1, Math.round(maxStart)));
    this.scrollbar.value = String(Math.round(this.viewStart));
    this.scrollbar.disabled = maxStart <= 0;
  }

  zoom(factor, anchorMs = this.viewStart + this.spanMs / 2) {
    const ax = this.xOf(anchorMs);
    this.msPerPx *= factor;
    this.clampView();
    this.viewStart = anchorMs - ax * this.msPerPx;
    this.clampView();
    this.follow = false;
    this.dirty = true;
  }

  zoomAll() {
    this.msPerPx = this.durationMs / Math.max(100, this.width);
    this.viewStart = 0;
    this.clampView();
    this.dirty = true;
  }

  /** Show [a, b] with some padding. */
  reveal(a, b = a) {
    const span = Math.max(b - a, 200);
    if (span * 1.3 > this.spanMs) this.msPerPx = (span * 1.6) / this.width;
    if (a < this.viewStart || b > this.viewStart + this.spanMs) this.viewStart = a - this.spanMs * 0.2;
    this.clampView();
    this.dirty = true;
  }

  _bind() {
    const c = this.canvas;
    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = c.getBoundingClientRect();
      const ms = this.msAt(e.clientX - rect.left);
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        const d = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) * this.msPerPx;
        this.viewStart += d;
        this.follow = false;
        this.clampView();
        this.dirty = true;
      } else {
        this.zoom(e.deltaY > 0 ? 1.25 : 0.8, ms);
      }
    }, { passive: false });

    this.scrollbar.addEventListener('input', () => {
      this.viewStart = Number(this.scrollbar.value);
      this.follow = false;
      this.dirty = true;
    });

    c.addEventListener('mousedown', (e) => {
      const rect = c.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const ov = this.cb.getOverlays();
      const sel = ov.units.find((u) => u.id === ov.selectedUnitId);
      if (sel && sel.editable && sel.start !== null && sel.end !== null) {
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
    });

    window.addEventListener('mousemove', (e) => {
      const rect = c.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const d = this.drag;
      if (!d) {
        const inside = x >= 0 && x <= rect.width && e.clientY >= rect.top && e.clientY <= rect.bottom;
        const hv = inside ? this._hitHandle(x) : null;
        c.style.cursor = hv ? 'ew-resize' : 'crosshair';
        return;
      }
      const ms = Math.max(0, Math.min(this.durationMs, this.msAt(x)));
      if (d.kind === 'start') {
        d.start = Math.min(ms, d.end - 1);
      } else if (d.kind === 'end') {
        d.end = Math.max(ms, d.start + 1);
      } else if (d.kind === 'pending' && Math.abs(x - d.x0) > 4) {
        d.kind = 'loop';
      }
      if (d.kind === 'loop') d.ms1 = ms;
      this.dirty = true;
    });

    window.addEventListener('mouseup', (e) => {
      const d = this.drag;
      if (!d) return;
      this.drag = null;
      const rect = c.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      if (d.kind === 'start' || d.kind === 'end') {
        if (Math.round(d.start) !== d.unit.start || Math.round(d.end) !== d.unit.end) {
          this.cb.onEditUnit(d.unit.id, Math.round(d.start), Math.round(d.end));
        }
      } else if (d.kind === 'loop') {
        const a = Math.min(d.ms0, d.ms1);
        const b = Math.max(d.ms0, d.ms1);
        this.cb.onLoop(a, b);
      } else if (d.kind === 'pending') {
        const ms = this.msAt(x);
        if (y > this.height - UNIT_H) {
          const hit = this._hitUnit(ms);
          if (hit) this.cb.onSelectUnit(hit.id);
        }
        this.cb.onSeek(Math.max(0, Math.min(this.durationMs, ms)));
      }
      this.dirty = true;
    });
  }

  _hitHandle(x) {
    const ov = this.cb.getOverlays();
    const sel = ov.units.find((u) => u.id === ov.selectedUnitId);
    if (!sel || !sel.editable || sel.start === null || sel.end === null) return null;
    if (Math.abs(x - this.xOf(sel.start)) <= HANDLE_PX) return 'start';
    if (Math.abs(x - this.xOf(sel.end)) <= HANDLE_PX) return 'end';
    return null;
  }

  _hitUnit(ms) {
    const ov = this.cb.getOverlays();
    return ov.units.find((u) => u.start !== null && u.end !== null && ms >= u.start && ms < u.end) || null;
  }

  draw() {
    const ctx = this.canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const W = this.width;
    const H = this.height;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const css = getComputedStyle(document.documentElement);
    const bg = css.getPropertyValue('--wave-bg').trim() || '#0f172a';
    const fg = css.getPropertyValue('--wave-fg').trim() || '#64748b';
    const text = css.getPropertyValue('--wave-text').trim() || '#cbd5e1';
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    const playhead = this.cb.getPlayhead();
    if (this.follow && playhead.playing && (playhead.ms > this.viewStart + this.spanMs * 0.92 || playhead.ms < this.viewStart)) {
      this.viewStart = playhead.ms - this.spanMs * 0.08;
      this.clampView();
    }

    const waveTop = RULER_H;
    const waveH = H - RULER_H - UNIT_H;
    const mid = waveTop + waveH / 2;
    const ov = this.cb.getOverlays();

    // loop region
    const loop = ov.loop;
    if (loop && loop.start !== null && loop.end !== null) {
      ctx.fillStyle = loop.on ? 'rgba(250, 204, 21, 0.18)' : 'rgba(250, 204, 21, 0.07)';
      ctx.fillRect(this.xOf(loop.start), RULER_H, (loop.end - loop.start) / this.msPerPx, H - RULER_H);
    }
    if (this.drag && this.drag.kind === 'loop') {
      ctx.fillStyle = 'rgba(250, 204, 21, 0.25)';
      const a = Math.min(this.drag.ms0, this.drag.ms1);
      const b = Math.max(this.drag.ms0, this.drag.ms1);
      ctx.fillRect(this.xOf(a), RULER_H, (b - a) / this.msPerPx, H - RULER_H);
    }

    // peaks
    if (this.peaks) {
      const { per_second: ps, mins, maxs } = this.peaks;
      ctx.fillStyle = fg;
      const n = mins.length;
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
        const y1 = mid - hi * (waveH / 2);
        const y2 = mid - lo * (waveH / 2);
        ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
      }
    } else {
      ctx.fillStyle = text;
      ctx.font = '12px sans-serif';
      ctx.fillText('尚无波形（请先上传音频）', 10, mid);
    }

    // ruler
    ctx.fillStyle = 'rgba(0,0,0,0.35)';
    ctx.fillRect(0, 0, W, RULER_H);
    ctx.fillStyle = text;
    ctx.font = '10px sans-serif';
    const step = niceStep(this.msPerPx * 90);
    const first = Math.ceil(this.viewStart / step) * step;
    for (let t = first; t < this.viewStart + this.spanMs; t += step) {
      const x = this.xOf(t);
      ctx.fillRect(x, RULER_H - 5, 1, 5);
      ctx.fillText(fmtRuler(t, step), x + 2, 11);
    }

    // line starts (lrc effective anchors)
    ctx.font = '11px sans-serif';
    for (const ls of ov.lineStarts) {
      const x = this.xOf(ls.ms);
      if (x < -200 || x > W + 5) continue;
      ctx.strokeStyle = ls.selected ? '#f97316' : (ls.kind === 'hard' ? '#22c55e' : 'rgba(249, 115, 22, 0.55)');
      ctx.setLineDash(ls.kind === 'hard' ? [] : [4, 3]);
      ctx.beginPath();
      ctx.moveTo(x + 0.5, RULER_H);
      ctx.lineTo(x + 0.5, H - UNIT_H);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = ls.selected ? '#fdba74' : text;
      ctx.fillText(ls.label, x + 3, RULER_H + 12, 180);
    }

    // units band
    const bandTop = H - UNIT_H;
    ctx.fillStyle = 'rgba(0,0,0,0.25)';
    ctx.fillRect(0, bandTop, W, UNIT_H);
    const drawUnits = (units, yOff, h, alpha) => {
      for (const u of units) {
        if (u.start === null || u.end === null) continue;
        let s = u.start;
        let e = u.end;
        if (this.drag && (this.drag.kind === 'start' || this.drag.kind === 'end') && this.drag.unit.id === u.id) {
          s = this.drag.start;
          e = this.drag.end;
        }
        const x1 = this.xOf(s);
        const x2 = this.xOf(e);
        if (x2 < 0 || x1 > W) continue;
        const color = STATUS_COLORS[u.color] || STATUS_COLORS.ok;
        ctx.globalAlpha = alpha;
        ctx.fillStyle = color;
        ctx.fillRect(x1, yOff, Math.max(1, x2 - x1 - 1), h);
        ctx.globalAlpha = 1;
        if (u.id === ov.selectedUnitId) {
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 2;
          ctx.strokeRect(x1, yOff, Math.max(1, x2 - x1), h);
          ctx.lineWidth = 1;
          if (u.editable) {
            ctx.fillStyle = '#fff';
            ctx.fillRect(x1 - 2, bandTop - waveH, 3, waveH + UNIT_H);
            ctx.fillRect(x2 - 1, bandTop - waveH, 3, waveH + UNIT_H);
          }
        }
        if (x2 - x1 > 14) {
          ctx.fillStyle = '#fff';
          ctx.font = '11px sans-serif';
          ctx.fillText(u.label, x1 + 2, yOff + h - 4, x2 - x1 - 3);
        }
        if (u.locked && x2 - x1 > 8) {
          ctx.fillStyle = '#fef08a';
          ctx.fillRect(x1 + 1, yOff + 1, 4, 4);
        }
      }
    };
    drawUnits(ov.units, bandTop + 3, ov.candUnits.length ? 14 : UNIT_H - 6, 0.85);
    if (ov.candUnits.length) drawUnits(ov.candUnits, bandTop + 19, 12, 0.85);

    // markers of failed units without time: none (never invent a position)

    // playhead
    const px = this.xOf(playhead.ms);
    if (px >= 0 && px <= W) {
      ctx.fillStyle = '#f43f5e';
      ctx.fillRect(px - 0.5, 0, 2, H);
    }
    this.dirty = false;
  }
}

function niceStep(ms) {
  const steps = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000];
  for (const s of steps) if (s >= ms) return s;
  return 120000;
}

function fmtRuler(t, step) {
  const m = Math.floor(t / 60000);
  const s = (t % 60000) / 1000;
  const digits = step < 100 ? 2 : step < 1000 ? 1 : 0;
  return `${m}:${s.toFixed(digits).padStart(digits ? 3 + digits : 2, '0')}`;
}

export { STATUS_COLORS };
