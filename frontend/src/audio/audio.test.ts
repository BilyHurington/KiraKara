import { describe, expect, it, vi } from 'vitest';
import { fmtMs, parseTime } from '@/lib/format';
import { Waveform, type Overlays } from './waveform';

function makeWave(width = 1000, height = 188) {
  const canvas = document.createElement('canvas');
  Object.defineProperty(canvas, 'clientWidth', { value: width });
  Object.defineProperty(canvas, 'clientHeight', { value: height });
  canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width, height, right: width, bottom: height, x: 0, y: 0, toJSON() {} });
  document.body.appendChild(canvas);
  const overlays: Overlays = {
    units: [{ id: 'u1', start: 1000, end: 2000, label: 'き', color: 'ok', editable: true }],
    candUnits: [], lineStarts: [], loop: { on: false, start: null, end: null }, selectedUnitId: 'u1', marks: [],
  };
  const cb = {
    onSeek: vi.fn(), onSelectUnit: vi.fn(), onEditUnit: vi.fn(), onLoop: vi.fn(),
    getOverlays: () => overlays, getPlayhead: () => ({ ms: 0, playing: false }),
  };
  const wf = new Waveform(canvas, cb);
  wf.setDuration(10000);
  return { wf, cb, canvas, overlays };
}

const mouse = (target: EventTarget, type: string, x: number, y = 100) =>
  target.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y, bubbles: true }));

describe('Waveform', () => {
  it('maps ms ⇄ px and fits the whole song initially', () => {
    const { wf } = makeWave();
    expect(wf.msPerPx).toBeCloseTo(10);
    expect(wf.xOf(5000)).toBeCloseTo(500);
    expect(wf.msAt(250)).toBeCloseTo(2500);
  });

  it('zoom keeps the anchor under the cursor and clamps the view', () => {
    const { wf } = makeWave();
    wf.zoom(0.5, 5000);
    expect(wf.xOf(5000)).toBeCloseTo(500);
    wf.zoom(100);
    expect(wf.viewStart).toBe(0);
    expect(wf.spanMs).toBeLessThanOrEqual(10000 + 1e-6);
  });

  it('click seeks; drag selects a loop', () => {
    const { wf, cb, canvas } = makeWave();
    mouse(canvas, 'mousedown', 300);
    mouse(window, 'mouseup', 300);
    expect(cb.onSeek).toHaveBeenCalledWith(3000);
    mouse(canvas, 'mousedown', 400);
    mouse(window, 'mousemove', 600);
    mouse(window, 'mouseup', 600);
    expect(cb.onLoop).toHaveBeenCalledWith(4000, 6000);
    wf.dispose();
  });

  it('dragging a selected unit edge edits the unit', () => {
    const { cb, canvas } = makeWave();
    mouse(canvas, 'mousedown', 100); // unit start at 1000 ms = 100 px
    mouse(window, 'mousemove', 80);
    mouse(window, 'mouseup', 80);
    expect(cb.onEditUnit).toHaveBeenCalledWith('u1', 800, 2000);
  });

  it('reveal brings a range into view', () => {
    const { wf } = makeWave();
    wf.zoom(0.1, 0);
    wf.reveal(8000, 8500);
    expect(wf.viewStart).toBeLessThanOrEqual(8000);
    expect(wf.viewStart + wf.spanMs).toBeGreaterThanOrEqual(8500);
  });
});

describe('format', () => {
  it('fmtMs', () => {
    expect(fmtMs(61235)).toBe('1:01.235');
    expect(fmtMs(-500)).toBe('-0:00.500');
    expect(fmtMs(null)).toBe('—');
  });
});

describe('parseTime', () => {
  it('accepts m:ss.mmm, seconds and plain ms', () => {
    expect(parseTime('0:48.990')).toBe(48990);
    expect(parseTime('1:02.3')).toBe(62300);
    expect(parseTime('48.99')).toBe(48990);
    expect(parseTime('48990')).toBe(48990);
    expect(parseTime('2:31')).toBe(151000);
    expect(parseTime('abc')).toBeNull();
  });
});
