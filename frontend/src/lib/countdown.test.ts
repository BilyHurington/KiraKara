import { describe, expect, it } from 'vitest';
import { countdownPlan } from './countdown';

describe('countdown lines', () => {
  const lines = [
    { id: 'a', start: 5000, end: 8000 },
    { id: 'b', start: 9000, end: 12000 },
    { id: 'c', start: 20000, end: 23000 },  // after an 8 s pause
    { id: 'd', start: 26000, end: 28000, countdown: true },  // its own setting
    { id: 'e', start: 40000, end: 42000, countdown: false },
  ];
  it('the first line and lines after a long pause, unless a line says otherwise', () => {
    const p = countdownPlan(lines);
    expect([...p.values()].map((x) => x.on)).toEqual([true, false, true, true, false]);
    expect(p.get('e')).toEqual({ auto: true, on: false, gap: 12000 });
    const off = countdownPlan(lines, { intro: false, interlude: true, min_gap_ms: 10000, dots: 3 });
    expect([...off.values()].map((x) => x.auto)).toEqual([false, false, false, false, true]);
  });
});
