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
  it('follows the playhead again once playback starts after the user scrolled away', () => {
    const { wf } = makeWave();
    let ph = { ms: 1000, playing: false };
    wf.cb.getPlayhead = () => ph;
    wf.zoom(0.2, 5000);  // 2 s visible
    wf.scrollTo(0.7);
    expect(wf.follow).toBe(false);
    (wf as any).draw();
    expect(wf.viewStart).toBeCloseTo(7000);  // paused: the view stays where the user put it
    ph = { ms: 1000, playing: true };
    (wf as any).draw();
    expect(wf.follow).toBe(true);
    expect(wf.viewStart).toBeLessThanOrEqual(1000);  // back to the playhead
    wf.scrollTo(0.7);
    wf.reveal(3000, 3500);
    expect(wf.follow).toBe(true);
    wf.dispose();
  });

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

  it('two or more selected units: dragged together, stretched by their outer edges', () => {
    const { cb, canvas, overlays } = makeWave();
    overlays.units.push({ id: 'u2', start: 2000, end: 3000, label: 'み', color: 'ok', editable: true },
      { id: 'u3', start: 5000, end: 6000, label: 'と', color: 'ok', editable: true });
    overlays.selectedUnitIds = ['u1', 'u2'];
    const onRetime = vi.fn();
    (cb as any).onRetimeUnits = onRetime;
    // inside the group (1000–3000 ms = 100–300 px): the whole group moves
    mouse(canvas, 'mousedown', 200);
    mouse(window, 'mousemove', 250);
    mouse(window, 'mouseup', 250);
    expect(onRetime).toHaveBeenLastCalledWith(['u1', 'u2'], 1500, null);
    // its right edge: stretched onto a new span
    mouse(canvas, 'mousedown', 300);
    mouse(window, 'mousemove', 400);
    mouse(window, 'mouseup', 400);
    expect(onRetime).toHaveBeenLastCalledWith(['u1', 'u2'], 1000, 4000);
    expect(cb.onEditUnit).not.toHaveBeenCalled();
    // a click inside without moving still seeks
    mouse(canvas, 'mousedown', 150);
    mouse(window, 'mouseup', 150);
    expect(cb.onSeek).toHaveBeenLastCalledWith(1500);
    expect(onRetime).toHaveBeenCalledTimes(2);
  });

  it('a dragged group stops at the units next to it', () => {
    const { cb, canvas, overlays } = makeWave();
    overlays.units.push({ id: 'u2', start: 2000, end: 3000, label: 'み', color: 'ok', editable: true },
      { id: 'u3', start: 3200, end: 4000, label: 'と', color: 'ok', editable: true });
    overlays.selectedUnitIds = ['u1', 'u2'];
    const onRetime = vi.fn();
    (cb as any).onRetimeUnits = onRetime;
    mouse(canvas, 'mousedown', 200);
    mouse(window, 'mousemove', 300);  // +1000 ms asked; 'と' starts 200 ms after the group
    mouse(window, 'mouseup', 300);
    expect(onRetime).toHaveBeenLastCalledWith(['u1', 'u2'], 1200, null);
    mouse(canvas, 'mousedown', 300);  // the right edge, stretched past 'と': stops at its start
    mouse(window, 'mousemove', 380);
    mouse(window, 'mouseup', 380);
    expect(onRetime).toHaveBeenLastCalledWith(['u1', 'u2'], 1000, 3200);
  });

  it('Shift / ⌘ clicks on a unit extend the selection instead of seeking', () => {
    const { cb, canvas } = makeWave();
    const click = (init: MouseEventInit) => {
      canvas.dispatchEvent(new MouseEvent('mousedown', { clientX: 150, clientY: 170, bubbles: true, ...init }));
      window.dispatchEvent(new MouseEvent('mouseup', { clientX: 150, clientY: 170, bubbles: true, ...init }));
    };
    click({ shiftKey: true });
    expect(cb.onSelectUnit).toHaveBeenLastCalledWith('u1', 'range');
    click({ metaKey: true });
    expect(cb.onSelectUnit).toHaveBeenLastCalledWith('u1', 'toggle');
    expect(cb.onSeek).not.toHaveBeenCalled();
    click({});
    expect(cb.onSelectUnit).toHaveBeenLastCalledWith('u1', 'single');
    expect(cb.onSeek).toHaveBeenCalledWith(1500);
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
