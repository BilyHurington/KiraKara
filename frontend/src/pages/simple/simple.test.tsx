import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import type { AppSettings, KaraokeStyle, PipelineTask } from '@/lib/types';
import { useApp } from '@/store/app';
import { useSimple } from '@/store/simple';
import { fixtureInfo, fixturePV, mockApi, renderUI } from '@/test/helpers';
import { detectLyrics, SimpleHome } from './SimpleHome';
import { SimpleSettings } from './SimpleSettings';
import { SimpleApp } from './SimpleApp';

const STYLE: KaraokeStyle = {
  version: 1, preset: 'custom',
  layout: { position: 'bottom', lines: 2, arrangement: 'alternate', margin_v: 40, line_spacing: 0, margin_h: 240, alternate_indent: 240, shrink_long_lines: true, show_translation: false, translation_size_pct: 50 },
  text: { font: '', size: 88, bold: true, color_unsung: '#FFFFFF', color_sung: '#ED35B3', outline_color: '#0B1F3A', outline: 4.5, shadow: 2, shadow_color: '#000000', shadow_opacity: 45 },
  ruby: { enabled: true, script: 'romaji', target: 'all', size_pct: 45, gap: 2, fit: 'widen', follow_colors: true, font: '', color_unsung: '#FFFFFF', color_sung: '#2F80ED', outline_color: '#0B1F3A', outline: 3 },
  timing: { lead_in_ms: 4000, hold_ms: 2000, highlight: 'sweep', early_show: true, early_max_ms: 6000 },
  output: { vocal_keep_pct: 20 },
};

const SETTINGS: AppSettings = {
  version: 1,
  ai: { provider: 'none', model: '', base_url: 'https://api.openai.com/v1', api_key_env: 'OPENAI_API_KEY', timeout_s: 600, has_api_key: false, env_key_present: false },
  simple: {
    default_mode: 'lrc', ai_readings: true, separate: true, separation_preset: 'melband-roformer', separation_device: 'auto',
    karaoke: STYLE, auto_export: true, video_audio: 'original',
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

describe('default subtitle style', () => {
  it('shows the style, changes colours without touching layout or timing, resets and copies from a project', async () => {
    seed();
    useApp.setState({ projects: [{ id: 'p1', name: '初恋组曲 Karaoke', mode: 'lrc', updated: 'z' }] });
    const sakura = { ...structuredClone(STYLE), preset: 'sakura', text: { ...STYLE.text, color_sung: '#FF5C8A', outline_color: '#3D0B24' }, timing: { ...STYLE.timing, lead_in_ms: 1000 } };
    const theirs = { ...structuredClone(STYLE), ruby: { ...STYLE.ruby, script: 'katakana' as const } };
    const api = mockApi({
      'GET /api/karaoke/presets': () => [{ name: 'sakura', label: '樱花', description: '', style: sakura }],
      'GET /api/ai/providers': () => [],
      'GET /api/projects/p1/karaoke': () => theirs,
      'PUT /api/settings': (c) => {
        const s = structuredClone(useSimple.getState().settings!);
        if (c.body.simple?.reset_karaoke) s.simple.karaoke = structuredClone(STYLE);
        else Object.assign(s.simple, c.body.simple ?? {});
        return s;
      },
    });
    renderUI(<SimpleSettings />);
    expect(screen.getByText('注音：罗马音（全部）')).toBeInTheDocument();
    expect(screen.getByText('提前 4 秒出现 · 唱完停留 2 秒')).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('radio', { name: /樱花/ }));
    await waitFor(() => expect(api.find('PUT', '/api/settings')).toHaveLength(1));
    const sent = api.find('PUT', '/api/settings')[0].body.simple.karaoke as KaraokeStyle;
    expect(sent.text.color_sung).toBe('#FF5C8A');
    expect(sent.timing.lead_in_ms).toBe(4000);  // the preset's timing is not taken
    expect(sent.layout.margin_v).toBe(40);
    await userEvent.click(screen.getByRole('button', { name: /恢复默认/ }));
    await waitFor(() => expect(api.find('PUT', '/api/settings').at(-1)!.body).toEqual({ simple: { reset_karaoke: true } }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '从项目复制样式' }), 'p1');
    await waitFor(() => expect(api.find('PUT', '/api/settings').at(-1)!.body.simple.karaoke.ruby.script).toBe('katakana'));
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

describe('confirming the start before the task continues', () => {
  const cal = {
    line_id: 'L1', line_text: 'きみと', lrc_ms: 1500, lines: [{ id: 'L1', text: 'きみと', lrc_ms: 1500 }, { id: 'L2', text: 'あるいた', lrc_ms: 3500 }],
    check_line: { id: 'L2', text: 'あるいた', lrc_ms: 3500 }, asset_id: 'a1', duration_ms: 60000,
  };
  const waiting = () => task({ id: 'tw', status: 'waiting', project_id: 'p', calibration: cal,
    stages: stages(2).map((s, i) => (i === 2 ? { ...s, status: 'waiting' as const } : s)) });

  it('opens by itself for the task just added, and the marked position is sent', async () => {
    seed();
    const api = mockApi({
      'GET /api/tasks': () => useSimple.getState().tasks,
      'POST /api/tasks/tw/calibration': () => ({ ...waiting(), status: 'queued' }),
      'POST /api/tasks': () => task({ id: 'tw', status: 'preparing' }),
      'GET /api/projects/p/audio/a1/peaks': () => ({ per_second: 100, mins: [], maxs: [] }),
    });
    const { container } = renderUI(<SimpleHome />);
    await userEvent.upload(container.querySelector('input[type=file]') as HTMLInputElement, new File(['x'], 'a.mp4', { type: 'video/mp4' }));
    fireEvent.change(screen.getByRole('textbox', { name: '音乐链接或歌词' }), { target: { value: '[00:01.50]きみと' } });
    await userEvent.click(screen.getByRole('button', { name: /开始制作/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks')).toHaveLength(1));
    // import + lyrics are done: the task now waits for the user
    useSimple.setState({ tasks: [waiting()] });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('きみと')).toBeInTheDocument();
    expect(within(dialog).getByRole('textbox', { name: '标记时间' })).toHaveValue('0:01.500');
    expect(within(dialog).getByRole('button', { name: /从标记处播放/ })).toBeEnabled();
    expect(within(dialog).getByRole('button', { name: /标记前 2 秒开始/ })).toBeEnabled();
    expect(within(dialog).getByRole('button', { name: /试听中间一句「あるいた」/ })).toBeEnabled();
    await userEvent.click(within(dialog).getByRole('button', { name: '−100 ms' }));
    await userEvent.click(within(dialog).getByRole('button', { name: /确认并继续/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks/tw/calibration')).toHaveLength(1));
    expect(api.find('POST', '/api/tasks/tw/calibration')[0].body).toEqual({ marked_ms: 1400 });  // −100 ms nudge
  });

  it('a waiting task shows the button; plain mode is one click', async () => {
    seed();
    useSimple.setState({ tasks: [waiting()] });
    const api = mockApi({
      'GET /api/tasks': () => [waiting()],
      'POST /api/tasks/tw/calibration': () => ({ ...waiting(), status: 'queued' }),
      'GET /api/projects/p/audio/a1/peaks': () => ({ per_second: 100, mins: [], maxs: [] }),
    });
    renderUI(<SimpleHome />);
    expect(await screen.findByText(/需要你确认第一句「きみと」/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /确认开头位置/ }));
    const dialog = await screen.findByRole('dialog');
    const input = within(dialog).getByRole('textbox', { name: '标记时间' });
    await userEvent.clear(input);
    await userEvent.type(input, '0:02.250{Enter}');
    expect(within(dialog).getByText('+750 ms')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /改普通模式/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks/tw/calibration')[0]?.body).toEqual({ plain: true }));
  });
});

describe('detailed calibration page', () => {
  it('plays from the calibrated start and applies the automatic match only on request', async () => {
    const { seedStore } = await import('@/test/helpers');
    const { CalibratePage } = await import('@/pages/Calibrate');
    const { player } = await import('@/audio/player');
    const { vi } = await import('vitest');
    const pv = seedStore('calibrate');
    const play = vi.spyOn(player, 'play').mockImplementation(() => {});
    const line = pv.project.lyrics.lines.find((l) => pv.view.effective_starts[l.id])!;
    const eff = pv.view.effective_starts[line.id].ms;
    const base = line.imported_start_ms! + pv.project.lyrics.embedded_shift_ms;
    const api = mockApi({
      [`POST /api/projects/${pv.project.id}/calibration/suggest`]: () => ({ id: 'jc', kind: 'calibrate', project_id: pv.project.id, status: 'running', progress: 0, message: '', error: null, created: 'z', finished: null, output: null }),
      'GET /api/jobs/jc': () => ({ id: 'jc', kind: 'calibrate', project_id: pv.project.id, status: 'succeeded', progress: 1, message: '完成', error: null, created: 'z', finished: 'z',
        output: { shift_ms: -180, agree: 0.92, lines_checked: 48, audio_role: 'vocals', vocal_onset_ms: 12000, line_starts: {} } }),
      [`POST /api/projects/${pv.project.id}/calibration/shift`]: () => pv,
    });
    renderUI(<CalibratePage />);
    await userEvent.click(await screen.findByRole('button', { name: /从校准点播放/ }));
    expect(play).toHaveBeenLastCalledWith(eff);
    await userEvent.click(screen.getByRole('button', { name: /从有效句首前 2 秒播放/ }));
    expect(play).toHaveBeenLastCalledWith(Math.max(0, eff - 2000));
    await userEvent.click(screen.getByRole('button', { name: /自动匹配/ }));
    expect(await screen.findByText('44/48 行一致', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(api.find('POST', '/calibration/shift')).toHaveLength(0);  // nothing changes by itself
    await userEvent.click(screen.getByRole('button', { name: /试听建议位置/ }));
    expect(play).toHaveBeenLastCalledWith(Math.max(0, base - 180));
    await userEvent.click(screen.getByRole('button', { name: '应用建议' }));
    await waitFor(() => expect(api.find('POST', '/calibration/shift')[0]?.body).toEqual({ user_shift_ms: -180 }));
    play.mockRestore();
  });
});
