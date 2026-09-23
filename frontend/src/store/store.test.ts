import { beforeEach, describe, expect, it } from 'vitest';
import { fixturePV, mockApi, seedStore, tick } from '@/test/helpers';
import { currentResult, resultFrom, setPV, useApp } from './app';
import { clearUnitManual, redo, setUnitLock, setUnitTimes, undo } from './edits';
import { stepStatus } from './steps';

const unit = () => currentResult()!.units[0];

function unitResponder(calls: { method: string; url: string; body: any }[]) {
  // behave like the server: return the unit with the requested manual state
  return (call: { url: string; body: any; method: string }) => {
    const u = structuredClone(currentResult()!.units.find((x) => call.url.includes(`/units/${x.unit_id}`))!);
    calls.length;
    if (call.url.endsWith('/restore')) {
      u.manual = call.body.manual;
    } else if (call.method === 'DELETE') {
      u.manual = null;
    } else if (call.url.endsWith('/lock')) {
      u.manual = { ...(u.manual ?? { start_ms: u.start_ms, end_ms: u.end_ms, at: '', note: '' }), locked: call.body.locked };
    } else {
      u.manual = { start_ms: call.body.start_ms, end_ms: call.body.end_ms, locked: true, at: '', note: '' };
    }
    u.start_ms = u.manual ? u.manual.start_ms : u.model_start_ms;
    u.end_ms = u.manual ? u.manual.end_ms : u.model_end_ms;
    return u;
  };
}

describe('result selection', () => {
  it('selects the active result and merges fresh stale flags', () => {
    const pv = seedStore('review');
    expect(useApp.getState().resultId).toBe(pv.project.active_result_id);
    const stale = pv.view.results.find((r) => r.stale)!;
    expect(resultFrom(pv, stale.id)!.stale).toBe(true);
  });

  it('keeps the chosen result while the active one is unchanged', () => {
    const pv = seedStore('review');
    const other = pv.project.results.find((r) => r.id !== pv.project.active_result_id)!;
    useApp.setState({ resultId: other.id });
    setPV(fixturePV());
    expect(useApp.getState().resultId).toBe(other.id);
  });
});

describe('manual edits with undo/redo', () => {
  beforeEach(() => seedStore('review'));

  it('edit → undo → redo goes through the server and restores exact states', async () => {
    const calls: any[] = [];
    const respond = unitResponder(calls);
    const api = mockApi({ 'PUT /api/': respond, 'POST /api/': respond });
    const u0 = unit();
    const before = u0.manual;

    await setUnitTimes(u0.unit_id, 1975, 2120);
    expect(api.find('PUT', `/units/${u0.unit_id}`)[0].body).toEqual({ start_ms: 1975, end_ms: 2120, locked: true });
    expect(unit().start_ms).toBe(1975);
    expect(useApp.getState().undo).toHaveLength(1);

    await undo();
    expect(api.find('POST', '/restore').at(-1)!.body).toEqual({ manual: before });
    expect(unit().start_ms).toBe(before ? before.start_ms : u0.model_start_ms);
    expect(useApp.getState().redo).toHaveLength(1);

    await redo();
    expect(unit().start_ms).toBe(1975);
    expect(useApp.getState().undo).toHaveLength(1);
    expect(useApp.getState().redo).toHaveLength(0);
  });

  it('a new edit clears the redo stack', async () => {
    const respond = unitResponder([]);
    mockApi({ 'PUT /api/': respond, 'POST /api/': respond });
    const id = unit().unit_id;
    await setUnitTimes(id, 1900, 2100);
    await undo();
    await setUnitTimes(id, 1950, 2100);
    expect(useApp.getState().redo).toHaveLength(0);
  });

  it('lock and clear are undoable too', async () => {
    const respond = unitResponder([]);
    mockApi({ 'POST /api/': respond, 'DELETE /api/': respond });
    const id = unit().unit_id;
    await setUnitLock(id, false);
    await clearUnitManual(id);
    expect(useApp.getState().undo).toHaveLength(2);
    expect(unit().manual).toBeNull();
  });

  it('line range follows unit edits', async () => {
    const respond = unitResponder([]);
    mockApi({ 'PUT /api/': respond });
    const u = unit();
    await setUnitTimes(u.unit_id, 1500, u.end_ms);
    const line = currentResult()!.lines.find((l) => l.line_id === u.line_id)!;
    expect(line.start_ms).toBe(1500);
  });

  it('a failed edit reports an error and does not push undo', async () => {
    mockApi({});
    await setUnitTimes(unit().unit_id, 1, 2);
    await tick();
    expect(useApp.getState().undo).toHaveLength(0);
    expect(useApp.getState().toasts.at(-1)?.kind).toBe('error');
  });
});

describe('step status', () => {
  it('reflects project state', () => {
    const pv = fixturePV();
    const none = new Set<string>();
    expect(stepStatus('input', pv, none).state).toBe('done');
    expect(stepStatus('calibrate', pv, none).state).toBe('done');
    expect(stepStatus('align', pv, new Set(['align'])).state).toBe('running');
    pv.project.mode = 'plain';
    expect(stepStatus('calibrate', pv, none).state).toBe('skipped');
    pv.project.lyrics.lines = [];
    expect(stepStatus('input', pv, none).state).toBe('todo');
  });
});
