// The 演唱者 page: selecting lines / words, number keys (1 + 2 = one undo step), undo, the singer list.

import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { player } from '@/audio/player';
import type { KaraokeStyle, ProjectView } from '@/lib/types';
import { useApp } from '@/store/app';
import { undo } from '@/store/edits';
import { flushSingers } from '@/store/singers';
import { stepStatus } from '@/store/steps';
import { fixturePV, mockApi, renderUI, seedStore } from '@/test/helpers';
import { plainStyle } from '@/test/style';
import { SingersPage } from './Singers';

const PID = 'proj';

function withSingers(): KaraokeStyle {
  const st = plainStyle();
  st.singers = {
    members: [
      { name: 'Ann', color: '#ED35B3', color_unsung: '', color_sung: '', outline_color: '', glow_unsung: '', glow_sung: '' },
      { name: 'Bo', color: '#2F80ED', color_unsung: '', color_sung: '', outline_color: '', glow_unsung: '', glow_sung: '' },
    ],
    mix: 'split', direction: 'vertical',
  };
  return st;
}

/** A server that stores what is sent (the project view follows every assignment). */
function server() {
  const pv: ProjectView = fixturePV();
  pv.project.karaoke = withSingers();
  const api = mockApi({
    [`GET /api/projects/${PID}/karaoke`]: () => pv.project.karaoke,
    [`PUT /api/projects/${PID}/karaoke/singers`]: (c) => { pv.project.karaoke = { ...pv.project.karaoke!, singers: c.body }; return pv.project.karaoke; },
    [`DELETE /api/projects/${PID}/karaoke/singers/`]: () => {
      pv.project.karaoke!.singers!.members.shift();
      return { ...structuredClone(pv), changed: 0 };
    },
    [`PUT /api/projects/${PID}/singers`]: (c) => {
      for (const x of c.body.lines) {
        const l = pv.project.lyrics.lines.find((y) => y.id === x.line_id)!;
        l.singers = x.singers;
        l.singer_spans = x.spans;
      }
      return structuredClone(pv);
    },
    'POST /api/karaoke/singer-colors': () => [],
    [`POST /api/projects/${PID}/karaoke/preview`]: () => new Response(new Blob(['png']), { status: 200 }),
  });
  return { api, pv };
}

const press = (key: string, opts: Partial<KeyboardEventInit> = {}) => act(() => { fireEvent.keyDown(window, { key, ...opts }); });

beforeEach(() => {
  player.reset();
});

describe('singers page', () => {
  it('whole lines by number keys; 1 + 2 is one undo step; undo sends what was there before', async () => {
    const pv = seedStore('singers');
    const { api } = server();
    renderUI(<SingersPage />);
    expect(await screen.findByRole('heading', { level: 1, name: '演唱者' })).toBeInTheDocument();
    expect(await screen.findByDisplayValue('Ann')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '选择第 1 行' }));
    expect(screen.getByRole('status')).toHaveTextContent('已选 1 行');
    press('1');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(1));
    expect(api.find('PUT', `${PID}/singers`)[0].body.lines).toEqual([{ line_id: 'L0001', text: pv.project.lyrics.lines[0].text, singers: [1], spans: [] }]);
    press('+');
    press('2');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(2));
    expect(api.find('PUT', `${PID}/singers`)[1].body.lines[0].singers).toEqual([1, 2]);
    expect(useApp.getState().undo).toHaveLength(1);
    expect(useApp.getState().undo[0].label).toBe('指定 Ann + Bo');
    expect(useApp.getState().pv!.project.lyrics.lines[0].singers).toEqual([1, 2]);

    await act(() => undo());
    const last = api.find('PUT', `${PID}/singers`).at(-1)!;
    expect(last.body.lines).toEqual([{ line_id: 'L0001', text: pv.project.lyrics.lines[0].text, singers: [], spans: [] }]);
    expect(useApp.getState().redo).toHaveLength(1);
  });

  it('words across lines (Shift), a singer the style lacks, clearing', async () => {
    seedStore('singers');
    const { api } = server();
    const { container } = renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    const word = (li: number, wi: number) => container.querySelector(`[data-word="${li}:${wi}"]`)!;
    // 道 (the last word of line 1) … 夜空 (the first of line 2)
    fireEvent.mouseDown(word(0, 4), { button: 0 });
    fireEvent.mouseUp(window);
    fireEvent.mouseDown(word(1, 0), { button: 0, shiftKey: true });
    expect(screen.getByRole('status')).toHaveTextContent('已选 2 行中的部分歌词');
    press('3');  // only two singers
    press('2');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(1));
    const lines = api.find('PUT', `${PID}/singers`)[0].body.lines;
    expect(lines.map((l: any) => [l.line_id, l.singers, l.spans])).toEqual([
      ['L0001', [], [{ start: 5, end: 6, singers: [2] }]],
      ['L0002', [], [{ start: 0, end: 2, singers: [2] }]],
    ]);
    press('0');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(2));
    expect(api.find('PUT', `${PID}/singers`)[1].body.lines.every((l: any) => !l.spans.length)).toBe(true);
    expect(useApp.getState().undo).toHaveLength(2);
  });

  it('1 + 2 can be saved to key 3, which then assigns both', async () => {
    seedStore('singers');
    const { api } = server();
    renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    fireEvent.click(screen.getByRole('button', { name: '选择第 1 行' }));
    press('1');
    // "+" and "2" typed faster than the page re-renders
    act(() => { fireEvent.keyDown(window, { key: '+' }); fireEvent.keyDown(window, { key: '2' }); });
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(2));
    expect(api.find('PUT', `${PID}/singers`)[1].body.lines[0].singers).toEqual([1, 2]);
    fireEvent.click(await screen.findByRole('button', { name: /把 1\+2 存到 3/ }));
    await act(() => flushSingers());
    expect(api.find('PUT', '/karaoke/singers').at(-1)!.body.combos).toEqual([{ key: 3, singers: [1, 2] }]);
    fireEvent.click(screen.getByRole('button', { name: '选择第 2 行' }));
    press('3');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(3));
    expect(api.find('PUT', `${PID}/singers`)[2].body.lines[0]).toMatchObject({ line_id: 'L0002', singers: [1, 2] });
    expect(screen.getByRole('textbox', { name: '快捷键 3 的演唱者' })).toHaveValue('1+2');
  });

  it('double-clicking a word goes there; the row being sung is marked', async () => {
    seedStore('singers');
    server();
    const { container } = renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    // 星 in line 2 (夜空に星が光る) is sung from 6180 ms
    fireEvent.doubleClick(container.querySelector('[data-word="1:2"]')!);
    expect(player.positionMs()).toBe(6180);
    await waitFor(() => expect(container.querySelector('[data-row][data-playing]')?.getAttribute('data-row')).toBe('L0002'));
  });

  it('the singer list is saved; the step shows who sings', async () => {
    seedStore('singers');
    const { api, pv } = server();
    renderUI(<SingersPage />);
    const name = await screen.findByDisplayValue('Bo');
    fireEvent.change(name, { target: { value: 'Bob' } });
    await act(() => flushSingers());
    const put = api.find('PUT', '/karaoke/singers');
    expect(put.at(-1)!.body.members[1].name).toBe('Bob');
    pv.project.lyrics.lines[0].singers = [2];
    expect(stepStatus('singers', pv, new Set())).toEqual({ state: 'done', note: '2 位演唱者 · 1 行' });
    fireEvent.click(screen.getByRole('button', { name: '添加演唱者' }));
    await act(() => flushSingers());
    expect(api.find('PUT', '/karaoke/singers').at(-1)!.body.members).toHaveLength(3);
  });

  it('lyrics that name their singers: pick the real names, assign and take them out', async () => {
    const pv = fixturePV();
    pv.view.singer_markers = 2;
    seedStore('singers', pv);
    const routes = mockApi({
      [`GET /api/projects/${PID}/karaoke`]: () => withSingers(),
      'POST /api/karaoke/singer-colors': () => [],
      [`GET /api/projects/${PID}/singers/markers`]: () => ({
        lines: [
          { line_id: 'L0001', text: 'A：君と歩いた道', prefix: 'A：', names: ['A'], everyone: false },
          { line_id: 'L0002', text: 'Hey：夜空に星が光る', prefix: 'Hey：', names: ['Hey'], everyone: false },
        ],
        names: ['A', 'Hey'], existing: [],
      }),
      [`POST /api/projects/${PID}/singers/markers`]: () => ({ ...fixturePV(), messages: ['已按标记给 1 行指定演唱者：A'] }),
    });
    renderUI(<SingersPage />);
    fireEvent.click(await screen.findByRole('button', { name: '识别并指定…' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Hey' }));
    fireEvent.click(screen.getByRole('button', { name: /指定 2 行/ }));
    await waitFor(() => expect(routes.find('POST', '/singers/markers')).toHaveLength(1));
    expect(routes.find('POST', '/singers/markers')[0].body).toEqual({ names: ['A'], strip: true });
  });
});
