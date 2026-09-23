// Every page renders against a real project fixture; key interactions send
// the expected API requests (mocked fetch).

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { player } from '@/audio/player';
import { StudioDock } from '@/components/shell/StudioDock';
import { Sidebar } from '@/components/shell/Sidebar';
import { currentResult, useApp, WAVE_HEIGHT } from '@/store/app';
import { fixturePV, mockApi, renderUI, seedStore } from '@/test/helpers';
import { AlignPage } from './Align';
import { CalibratePage } from './Calibrate';
import { EnhancePage } from './Enhance';
import { ExportPage } from './Export';
import { HomePage } from './Home';
import { InputPage } from './Input';
import { ModePage } from './Mode';
import { ReviewPage } from './Review';

const PID = 'proj';

/** Routes that behave like the server for the fixture project. */
function serverLike() {
  const pv = fixturePV();
  const viewResp = () => fixturePV();
  return mockApi({
    [`GET /api/projects/${PID}/results/`]: (c) => pv.project.results.find((r) => c.url.includes(r.id)),
    [`GET /api/projects/${PID}/export/`]: (c) => ({ filename: 'aligned-unit.lrc', media_type: 'text/plain', content: `[00:02.00]<00:02.00>君 ${c.url}`, warnings: ['增强 LRC 以片段为单位合并'] }),
    [`GET /api/projects/${PID}/audio/`]: () => ({ per_second: 200, mins: [0], maxs: [0], duration_ms: 16000, sample_rate: 44100 }),
    [`GET /api/projects/${PID}`]: viewResp,
    'GET /api/projects': () => [{ id: PID, name: 'proj', mode: 'lrc', updated: pv.project.updated }],
    [`PUT /api/projects/${PID}/results/`]: (c) => {
      const r = currentResult()!;
      const u = structuredClone(r.units.find((x) => c.url.includes(x.unit_id))!);
      u.manual = { start_ms: c.body.start_ms, end_ms: c.body.end_ms, locked: true, at: '', note: '' };
      u.start_ms = c.body.start_ms;
      u.end_ms = c.body.end_ms;
      return u;
    },
    [`POST /api/projects/${PID}/align`]: () => ({ id: 'job1', kind: 'align', project_id: PID, status: 'queued', progress: 0, message: '', error: null, created: 'z', finished: null, output: null }),
    [`POST /api/projects/${PID}/mix/preview-gain`]: () => ({ bus_gain: 1, peak_before: 0.5 }),
    [`POST /api/projects/${PID}/lyrics/parse`]: () => ({ preview_id: 'pv1', detected: 'lrc', warnings: ['示例警告'], error: null, doc: fixturePV().project.lyrics, extra_tracks: {} }),
    [`POST /api/projects/${PID}/ai/prompt`]: () => ({ prompt: 'PROMPT TEXT', snapshot_id: 'snap1', roundtrip_id: 'ai1' }),
    [`POST /api/projects/${PID}/ai/validate`]: () => ({ report_id: 'rep1', report: { ok: true, snapshot: 'snap1', roundtrip_id: 'ai1', warnings: [], errors: [], missing_line_ids: [], lines: [] } }),
    [`POST /api/projects/${PID}/calibration/`]: viewResp,
    [`POST /api/projects/${PID}/`]: viewResp,
    [`PATCH /api/projects/${PID}`]: viewResp,
    'GET /api/jobs/': () => ({ id: 'job1', kind: 'align', project_id: PID, status: 'running', progress: 0.4, message: '解码', error: null, created: 'z', finished: null, output: null }),
  });
}

beforeEach(() => {
  player.reset();
});

describe('pages render without crashing', () => {
  const pages = [
    ['mode', ModePage, '选择对齐模式'],
    ['input', InputPage, '音频与歌词'],
    ['enhance', EnhancePage, '注音与人声分离'],
    ['calibrate', CalibratePage, '首音校准'],
    ['align', AlignPage, '对齐'],
    ['review', ReviewPage, '人工检查'],
    ['export', ExportPage, '导出'],
  ] as const;
  for (const [step, Page, title] of pages) {
    it(step, async () => {
      seedStore(step);
      serverLike();
      renderUI(<Page />);
      expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument();
    });
  }

  it('home (no project)', () => {
    useApp.setState({ pid: null, pv: null, projects: [] });
    serverLike();
    renderUI(<HomePage />);
    expect(screen.getByText('新建项目')).toBeInTheDocument();
  });

  it('plain-mode calibrate explains it is not needed', () => {
    const pv = fixturePV();
    pv.project.mode = 'plain';
    seedStore('calibrate', pv);
    serverLike();
    renderUI(<CalibratePage />);
    expect(screen.getAllByText(/普通模式/).length).toBeGreaterThan(0);
  });

  it('review without results shows an empty state', () => {
    const pv = fixturePV();
    pv.project.results = [];
    pv.project.active_result_id = null;
    pv.view.results = [];
    seedStore('review', pv);
    serverLike();
    renderUI(<ReviewPage />);
    expect(screen.getAllByRole('button', { name: /对齐/ }).length).toBeGreaterThan(0);
  });
});

describe('interactions', () => {
  it('review: editing a start time saves it (PUT) and is undoable', async () => {
    seedStore('review');
    const api = serverLike();
    renderUI(<ReviewPage />);
    const input = (await screen.findAllByRole('spinbutton'))[0] as HTMLInputElement;
    const orig = Number(input.value);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: String(orig - 15) } });
    fireEvent.blur(input);
    await waitFor(() => expect(api.find('PUT', '/units/')).toHaveLength(1));
    expect(api.find('PUT', '/units/')[0].body.start_ms).toBe(orig - 15);
    await waitFor(() => expect(useApp.getState().undo).toHaveLength(1));
  });

  it('review: end before start is rejected locally', async () => {
    seedStore('review');
    const api = serverLike();
    renderUI(<ReviewPage />);
    const inputs = await screen.findAllByRole('spinbutton');
    fireEvent.change(inputs[1], { target: { value: '1' } });
    fireEvent.blur(inputs[1]);
    await new Promise((r) => setTimeout(r, 20));
    expect(api.find('PUT', '/units/')).toHaveLength(0);
    expect(useApp.getState().toasts.at(-1)?.kind).toBe('error');
  });

  it('calibrate: M before audio is loaded asks to load audio', async () => {
    seedStore('calibrate');
    const api = serverLike();
    renderUI(<CalibratePage />);
    await screen.findByRole('heading', { level: 1, name: '首音校准' });
    fireEvent.keyDown(document.body, { key: 'm', code: 'KeyM' });
    expect(api.find('POST', '/calibration/mark')).toHaveLength(0);
    expect(useApp.getState().toasts.at(-1)?.title).toMatch(/音频/);
  });

  it('calibrate: M marks the playhead position', async () => {
    seedStore('calibrate');
    const api = serverLike();
    renderUI(<CalibratePage />);
    await screen.findByRole('heading', { level: 1, name: '首音校准' });
    player.durationMs = 16000; // audio loaded
    player.offsetMs = 2097;
    fireEvent.keyDown(document.body, { key: 'm', code: 'KeyM' });
    await waitFor(() => expect(api.find('POST', '/calibration/mark')).toHaveLength(1));
    expect(api.find('POST', '/calibration/mark')[0].body).toMatchObject({ marked_ms: 2097 });
  });

  it('align: start sends a job request and tracks it', async () => {
    seedStore('align');
    const api = serverLike();
    renderUI(<AlignPage />);
    await userEvent.click(await screen.findByRole('button', { name: /开始对齐/ }));
    await waitFor(() => expect(api.find('POST', '/align')).toHaveLength(1));
    await waitFor(() => expect(Object.keys(useApp.getState().jobs)).toContain('job1'));
  });

  it('export: preview opens a dialog with the content and loss warnings', async () => {
    seedStore('export');
    serverLike();
    renderUI(<ExportPage />);
    const buttons = await screen.findAllByRole('button', { name: /预览/ });
    await userEvent.click(buttons[0]);
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/\[00:02.00\]/)).toBeInTheDocument();
  });

  it('input: paste → parse → preview → apply', async () => {
    seedStore('input');
    const api = serverLike();
    renderUI(<InputPage />);
    const ta = await screen.findByPlaceholderText(/每行一句歌词/);
    fireEvent.change(ta, { target: { value: '[00:01.00]きみと' } });
    await userEvent.click(screen.getByRole('button', { name: /解析预览/ }));
    await waitFor(() => expect(api.find('POST', '/lyrics/parse')).toHaveLength(1));
    expect(api.find('POST', '/lyrics/parse')[0].body).toMatchObject({ text: '[00:01.00]きみと', origin: 'paste' });
    await userEvent.click(await screen.findByRole('button', { name: /应用到项目/ }));
    await waitFor(() => expect(api.find('POST', '/lyrics/apply')[0]?.body).toEqual({ preview_id: 'pv1' }));
  });

  it('enhance: prompt → validate', async () => {
    seedStore('enhance');
    const api = serverLike();
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => {}) } });
    renderUI(<EnhancePage />);
    await userEvent.click(await screen.findByRole('button', { name: /复制 AI 提示词/ }));
    await waitFor(() => expect(api.find('POST', '/ai/prompt')).toHaveLength(1));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('PROMPT TEXT');
    const reply = screen.getByPlaceholderText(/网页聊天的回复/);
    fireEvent.change(reply, { target: { value: '{"format":"kara-align/reading-patch"}' } });
    await userEvent.click(screen.getByRole('button', { name: /^校验$/ }));
    await waitFor(() => expect(api.find('POST', '/ai/validate')).toHaveLength(1));
  });

  it('mode: switching sends PATCH', async () => {
    seedStore('mode');
    const api = serverLike();
    renderUI(<ModePage />);
    await userEvent.click(screen.getByRole('radio', { name: /普通模式/ }));
    await waitFor(() => expect(api.find('PATCH', `/projects/${PID}`)[0].body).toEqual({ mode: 'plain' }));
  });

  it('sidebar navigates between steps', async () => {
    seedStore('mode');
    serverLike();
    renderUI(<Sidebar />);
    await userEvent.click(screen.getByRole('button', { name: /导出/ }));
    expect(useApp.getState().step).toBe('export');
  });
});

describe('studio dock', () => {
  it('renders the long mix sliders and a draggable divider', async () => {
    seedStore('review');
    serverLike();
    renderUI(<StudioDock />);
    const sep = await screen.findByRole('separator', { name: /波形高度/ });
    const h0 = useApp.getState().waveHeight;
    // keyboard resizing (the handle sits above the dock: ArrowUp grows it)
    act(() => { fireEvent.keyDown(sep, { key: 'ArrowUp' }); });
    expect(useApp.getState().waveHeight).toBe(Math.min(WAVE_HEIGHT.max, h0 + 10));
    act(() => { fireEvent.doubleClick(sep); });
    expect(useApp.getState().waveHeight).toBe(WAVE_HEIGHT.default);
    // pointer dragging
    act(() => { fireEvent.pointerDown(sep, { clientY: 500 }); });
    act(() => { window.dispatchEvent(new MouseEvent('pointermove', { clientY: 440 }) as any); });
    act(() => { window.dispatchEvent(new MouseEvent('pointerup') as any); });
    expect(useApp.getState().waveHeight).toBe(WAVE_HEIGHT.default + 60);
  });
});

describe('video in / reduced-vocal video out', () => {
  const withVideo = () => {
    const pv = fixturePV();
    const orig = pv.project.audio.find((a) => a.role === 'original')!;
    pv.project.video = {
      id: 'v1', sha256: 'abc', path: 'assets/abc.mp4', filename: 'clip.mp4', container: '.mp4', duration_ms: 16000,
      width: 1920, height: 1080, fps: 29.97, video_codec: 'h264', audio_codec: 'aac', audio_offset_s: 0, audio_sha256: orig.sha256,
    };
    return pv;
  };

  it('the original upload accepts video files', async () => {
    seedStore('input');
    serverLike();
    const { container } = renderUI(<InputPage />);
    const inputs = [...container.querySelectorAll('input[type=file]')] as HTMLInputElement[];
    expect(inputs.some((i) => i.accept.includes('.mp4') && i.accept.includes('video/*'))).toBe(true);
  });

  it('shows where the original audio came from', () => {
    seedStore('input', withVideo());
    serverLike();
    renderUI(<InputPage />);
    expect(screen.getByText(/来自视频 clip.mp4/)).toBeInTheDocument();
  });

  it('export offers the video button only with a video, and posts the mix settings', async () => {
    seedStore('export');
    serverLike();
    const first = renderUI(<ExportPage />);
    expect(screen.queryByRole('button', { name: /导出降低人声的视频/ })).toBeNull();
    first.unmount();

    const pv = withVideo();
    pv.view.audio.vocals = { asset_id: 'x', available: true, duration_ms: 16000, sample_rate: 44100 };
    pv.view.audio.instrumental = { asset_id: 'y', available: true, duration_ms: 16000, sample_rate: 44100 };
    seedStore('export', pv);
    const api = serverLike();
    renderUI(<ExportPage />);
    const btn = await screen.findByRole('button', { name: /导出降低人声的视频/ });
    await userEvent.click(btn);
    await waitFor(() => expect(api.find('POST', '/video/export')).toHaveLength(1));
    expect(api.find('POST', '/video/export')[0].body).toMatchObject({ vocal_keep_pct: pv.project.mix.vocal_keep_pct });
  });

  it('the dock keeps only a volume control (no vocal / instrumental sliders)', () => {
    seedStore('review');
    serverLike();
    renderUI(<StudioDock />);
    expect(screen.queryByRole('textbox', { name: /人声保留/ })).toBeNull();
    expect(screen.queryByRole('textbox', { name: /伴奏/ })).toBeNull();
    expect(screen.getByRole('textbox', { name: /音量/ })).toBeInTheDocument();
  });
});
