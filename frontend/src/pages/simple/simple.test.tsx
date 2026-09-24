import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import type { AppSettings, PipelineTask } from '@/lib/types';
import { useApp } from '@/store/app';
import { useSimple } from '@/store/simple';
import { fixtureInfo, fixturePV, mockApi, renderUI } from '@/test/helpers';
import { detectLyrics, SimpleHome } from './SimpleHome';
import { SimpleSettings } from './SimpleSettings';
import { SimpleApp } from './SimpleApp';

const SETTINGS: AppSettings = {
  version: 1,
  ai: { provider: 'none', model: '', base_url: 'https://api.openai.com/v1', api_key_env: 'OPENAI_API_KEY', timeout_s: 600, has_api_key: false, env_key_present: false },
  simple: {
    default_mode: 'lrc', ai_readings: true, separate: true, separation_preset: 'melband-roformer', separation_device: 'auto',
    karaoke_preset: 'classic', ruby: true, ruby_script: 'hiragana', auto_export: true, video_audio: 'original',
    vocal_keep_pct: 20, quality: 'standard',
  },
};

const stages = (upto: number, running = false) => ['import', 'lyrics', 'readings', 'separate', 'calibrate', 'align', 'export'].map((key, i) => ({
  key, label: key, progress: i < upto ? 1 : 0.4, message: '',
  status: (i < upto ? 'done' : i === upto && running ? 'running' : 'pending') as PipelineTask['stages'][number]['status'],
}));

const task = (over: Partial<PipelineTask>): PipelineTask => ({
  id: 't1', created: '2026-09-24T10:00:00+00:00', finished: null, name: '初恋', mode: 'lrc', media_filename: 'a.mp4',
  lyrics_kind: 'link', lyrics_input: 'https://music.163.com/song?id=1', status: 'queued', project_id: null,
  stages: stages(0), progress: 0, message: '', error: null, detail: null, warnings: [], outputs: {}, ...over,
});

function seed(settings = SETTINGS) {
  useSimple.setState({ ui: 'simple', page: 'home', settings: structuredClone(settings), providers: null, tasks: [] });
  useApp.setState({ info: fixtureInfo(), pid: null, pv: null, jobs: {}, toasts: [] });
}

beforeEach(() => localStorage.clear());

describe('lyrics detection', () => {
  it('tells links, LRC and plain text apart', () => {
    expect(detectLyrics('').kind).toBe('empty');
    expect(detectLyrics('https://music.163.com/song?id=423314091&uct2=x')).toMatchObject({ kind: 'link', label: expect.stringContaining('网易云') });
    expect(detectLyrics('分享歌曲 https://y.qq.com/n/ryqq/songDetail/abc').label).toContain('QQ');
    expect(detectLyrics('[00:01.00]きみと\n[00:02.00]あるいた')).toMatchObject({ kind: 'lrc', label: 'LRC 歌词 · 2 行带时间' });
    expect(detectLyrics('君と\n歩いた')).toMatchObject({ kind: 'text' });
  });
});

describe('simple mode home', () => {
  it('adds a task from a video and a music link', async () => {
    seed();
    const api = mockApi({
      'GET /api/tasks': () => [],
      'POST /api/tasks': () => task({ status: 'queued' }),
      'PUT /api/settings': (c) => ({ ...SETTINGS, simple: { ...SETTINGS.simple, ...c.body.simple } }),
    });
    const { container } = renderUI(<SimpleHome />);
    const start = screen.getByRole('button', { name: /开始制作/ });
    expect(start).toBeDisabled();
    const input = container.querySelector('input[type=file]') as HTMLInputElement;
    await userEvent.upload(input, new File(['x'], '初恋.mp4', { type: 'video/mp4' }));
    expect(screen.getByText('初恋.mp4')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '音乐链接或歌词' }), { target: { value: 'https://music.163.com/song?id=1' } });
    expect(screen.getByText(/网易云音乐链接/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: /普通/ }));
    await waitFor(() => expect(api.find('PUT', '/api/settings')[0]?.body).toEqual({ simple: { default_mode: 'plain' } }));
    await userEvent.click(start);
    await waitFor(() => expect(api.find('POST', '/api/tasks')).toHaveLength(1));
    const fd = api.find('POST', '/api/tasks')[0].body as FormData;
    expect(fd.get('lyrics')).toBe('https://music.163.com/song?id=1');
    expect(fd.get('mode')).toBe('plain');
    expect((fd.get('file') as File).name).toBe('初恋.mp4');
    // the form is ready for the next song
    expect(screen.getByRole('button', { name: /开始制作/ })).toBeDisabled();
  });

  it('shows the queue with progress and opens a finished task in the detailed mode', async () => {
    seed();
    const pv = fixturePV();
    const running = task({ id: 't2', name: '夜に駆ける', status: 'running', progress: 0.42, stages: stages(3, true), message: '人声分离 · 42%', project_id: pv.project.id });
    const done = task({ id: 't1', status: 'succeeded', progress: 1, stages: stages(7), project_id: pv.project.id,
      outputs: { video: { filename: 'a-karaoke.mp4', url: '/api/projects/p/exports/a-karaoke.mp4' } }, warnings: ['有 1 处可能需要人工检查'] });
    mockApi({
      'GET /api/tasks': () => [running, done],
      [`GET /api/projects/${pv.project.id}/jobs`]: () => [],
      [`GET /api/projects/${pv.project.id}`]: () => pv,
    });
    renderUI(<SimpleHome />);
    const rows = await screen.findAllByRole('listitem', { name: undefined });
    expect(await screen.findByText('人声分离 · 42%')).toBeInTheDocument();
    expect(screen.getByText('42%')).toBeInTheDocument();
    expect(screen.getByText('有 1 处可能需要人工检查')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /下载视频/ })).toHaveAttribute('href', '/api/projects/p/exports/a-karaoke.mp4');
    expect(rows.length).toBeGreaterThan(2);
    const doneRow = screen.getByRole('button', { name: '初恋' }).closest('li')!;
    await userEvent.click(within(doneRow).getByRole('button', { name: /详细模式/ }));
    await waitFor(() => expect(useSimple.getState().ui).toBe('pro'));
    expect(useApp.getState().pid).toBe(pv.project.id);
    expect(useApp.getState().step).toBe('karaoke');
  });
});

describe('simple mode settings', () => {
  it('saves each change and never shows the API key', async () => {
    seed();
    const api = mockApi({
      'GET /api/karaoke/presets': () => [],
      'GET /api/ai/providers': () => [
        { id: 'claude', label: 'Claude Code', available: true, version: '2.1 (Claude Code)', detail: '/bin/claude' },
        { id: 'codex', label: 'Codex', available: false, version: null, detail: '' },
        { id: 'openai', label: 'OpenAI 兼容 API', available: true, version: null, detail: '' },
      ],
      'PUT /api/settings': (c) => {
        const s = structuredClone(useSimple.getState().settings!);
        Object.assign(s.ai, c.body.ai ?? {});
        Object.assign(s.simple, c.body.simple ?? {});
        if (c.body.ai?.api_key) { s.ai.has_api_key = true; delete (s.ai as any).api_key; }
        return s;
      },
    });
    renderUI(<SimpleSettings />);
    const claude = await screen.findByRole('radio', { name: /Claude Code/ });
    expect(within(claude).getByText('已安装')).toBeInTheDocument();
    expect(within(screen.getByRole('radio', { name: /Codex/ })).getByText('未找到')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: /OpenAI 兼容 API/ }));
    await waitFor(() => expect(useSimple.getState().settings!.ai.provider).toBe('openai'));
    const key = screen.getByPlaceholderText('sk-…');
    await userEvent.type(key, 'sk-test{Enter}');
    await waitFor(() => expect(api.find('PUT', '/api/settings').some((c) => c.body.ai?.api_key === 'sk-test')).toBe(true));
    expect(key).toHaveValue('');
    expect(await screen.findByPlaceholderText('••••••••（已保存）')).toBeInTheDocument();
    // reduced vocals needs separation; its level shows only when chosen
    expect(screen.queryByRole('textbox', { name: '人声保留（输入数值）' })).toBeNull();
    await userEvent.click(screen.getByRole('radio', { name: '降低人声（伴唱）' }));
    await waitFor(() => expect(useSimple.getState().settings!.simple.video_audio).toBe('mix'));
    expect(screen.getByRole('textbox', { name: '人声保留（输入数值）' })).toHaveValue('20');
  });
});

describe('simple mode shell', () => {
  it('switches between the pages and to the detailed mode', async () => {
    seed();
    mockApi({ 'GET /api/tasks': () => [], 'GET /api/karaoke/presets': () => [], 'GET /api/ai/providers': () => [] });
    renderUI(<SimpleApp />);
    expect(await screen.findByText('做一首卡拉OK')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '设置' }));
    expect(await screen.findByRole('heading', { level: 1, name: '设置' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /详细模式/ }));
    expect(useSimple.getState().ui).toBe('pro');
    expect(localStorage.getItem('kara.ui')).toBe('pro');
  });
});

describe('one-click AI readings in the detailed mode', () => {
  it('sends the prompt through the configured CLI and shows the validated preview', async () => {
    const { seedStore } = await import('@/test/helpers');
    const { AiRoundtripCard } = await import('@/pages/enhance/AiRoundtrip');
    const pv = seedStore('enhance');
    useSimple.setState({ settings: { ...structuredClone(SETTINGS), ai: { ...SETTINGS.ai, provider: 'claude', model: 'sonnet' } }, providers: [] });
    const line = pv.project.lyrics.lines.find((l) => l.sing)!;
    const report = { ok: true, snapshot: 'snap-1', roundtrip_id: 'rt', errors: [], warnings: [], missing_line_ids: [],
      lines: [{ line_id: line.id, status: 'ok', reasons: [], segments: [], diff: [{ surface: '君', old_reading: 'くん', new_reading: 'きみ', old_units: ['く', 'ん'], new_units: ['き', 'み'], changed: true, locked: false }] }] };
    const api = mockApi({
      [`POST /api/projects/${pv.project.id}/ai/auto`]: () => ({ id: 'jai', kind: 'ai', project_id: pv.project.id, status: 'running', progress: 0.1, message: '等待 Claude Code 回复…', error: null, created: 'z', finished: null, output: null }),
      'GET /api/jobs/jai': () => ({ id: 'jai', kind: 'ai', project_id: pv.project.id, status: 'succeeded', progress: 1, message: '完成', error: null, created: 'z', finished: 'z',
        output: { report_id: 'rep1', report, meta: { provider: 'claude', attempts: [{ provider: 'claude', model: 'sonnet', elapsed_s: 12.3, cost_usd: 0.02 }], cost_usd: 0.02 } } }),
    });
    renderUI(<AiRoundtripCard />);
    expect(screen.getByText('Claude Code · sonnet')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /开始 AI 注音/ }));
    await waitFor(() => expect(api.find('POST', '/ai/auto')).toHaveLength(1));
    expect(await screen.findByText(/已收到回复：12 秒 · sonnet · \$0\.020/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /应用所选 1 行/ })).toBeEnabled();
  });
});
