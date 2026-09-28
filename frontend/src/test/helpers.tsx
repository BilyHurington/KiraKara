// Test helpers: a routed fetch mock and a store seeded with a real ProjectView.

import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import { TooltipProvider } from '@/components/ui';
import type { Info, ProjectView } from '@/lib/types';
import { setPV, useApp, type Step } from '@/store/app';
import { clearDrafts } from '@/store/drafts';
import infoJson from './fixtures/info.json';
import pvJson from './fixtures/projectView.json';

export const fixturePV = () => structuredClone(pvJson) as unknown as ProjectView;
export const fixtureInfo = () => structuredClone(infoJson) as unknown as Info;

export interface Call { method: string; url: string; body: any }
type Handler = (call: Call) => unknown;

/** Install a fetch mock. Routes: "METHOD /path-prefix" → handler (first match wins). */
export function mockApi(routes: Record<string, Handler> = {}) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    let body: any = init?.body;
    if (typeof body === 'string') {
      try { body = JSON.parse(body); } catch { /* keep text */ }
    }
    const call = { method, url: String(url), body };
    calls.push(call);
    const key = Object.keys(routes).find((k) => {
      const [m, p] = k.split(' ');
      return m === method && call.url.startsWith(p);
    });
    if (!key) return new Response(JSON.stringify({ detail: `unmocked ${method} ${url}` }), { status: 404 });
    const out = await routes[key](call);
    if (out instanceof Response) return out;
    return new Response(JSON.stringify(out ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fn);
  return { calls, fn, find: (m: string, p: string) => calls.filter((c) => c.method === m && c.url.includes(p)) };
}

/** Reset the store and open the fixture project on a given step. */
export function seedStore(step: Step = 'mode', pv = fixturePV()) {
  useApp.setState({
    info: fixtureInfo(), projects: [{ id: pv.project.id, name: pv.project.name, mode: pv.project.mode, updated: pv.project.updated }],
    pid: null, pv: null, resultId: null, step, selLineId: null, selUnitId: null, selUnitIds: [], calibLineId: null,
    compareWithId: null, candidateId: null, undo: [], redo: [], jobs: {}, jobHistory: {}, toasts: [],
  });
  clearDrafts(pv.project.id);
  setPV(pv);
  useApp.setState({ step });
  return pv;
}

export function renderUI(ui: ReactElement) {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
}

export const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
