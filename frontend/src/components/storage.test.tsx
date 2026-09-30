import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fmtBytes } from '@/lib/format';
import type { StorageInfo, StorageProject } from '@/lib/types';
import { useApp } from '@/store/app';
import { mockApi, renderUI } from '@/test/helpers';
import { StorageDialog } from './StorageDialog';

const MB = 1024 * 1024;

function project(id: string, name: string, over: Partial<StorageProject> = {}): StorageProject {
  return {
    id, name, mode: 'lrc', updated: '2026-09-29T10:00:00Z', size: 500 * MB, stems: true, busy: false,
    parts: { media: 200 * MB, stems: 80 * MB, background: 0, exports: 220 * MB, unused: 0, other: 0 },
    exports: [{ filename: `${name}-karaoke.mp4`, size: 180 * MB, modified: 2 }, { filename: `${name}-mix.flac`, size: 40 * MB, modified: 1 }],
    ...over,
  };
}

function info(over: Partial<StorageInfo> = {}): StorageInfo {
  const projects = [project('p1', 'わたぐも'), project('p2', '初恋', { size: 300 * MB, busy: true })];
  return {
    root: '/home/u/.kara_align/projects', disk: { total: 500 * 1024 * MB, free: 40 * 1024 * MB }, projects,
    projects_size: 800 * MB, cache: { size: 250 * MB, parts: { playback: 240 * MB } }, models: { size: 2400 * MB, path: '/app/models' },
    leftovers: { size: 160 * MB, parts: { upload: 150 * MB, asset: 10 * MB } }, working: false, ...over,
  };
}

describe('disk space', () => {
  it('formats sizes', () => {
    expect(fmtBytes(0)).toBe('0 B');
    expect(fmtBytes(1536)).toBe('2 KB');
    expect(fmtBytes(719 * MB)).toBe('719 MB');
    expect(fmtBytes(4.7 * 1024 * MB)).toBe('4.70 GB');
  });

  it('shows what takes the space and cleans it up', async () => {
    useApp.setState({ pid: null, toasts: [] });
    let state = info();
    const api = mockApi({
      'GET /api/storage': () => state,
      'POST /api/storage/clean': () => { state = info({ leftovers: { size: 0, parts: {} }, freed: 160 * MB }); return state; },
      'POST /api/projects/p1/storage/clean': () => ({ ...state, freed: 180 * MB }),
      'DELETE /api/projects/p1': () => ({ ok: true }),
      'GET /api/projects': () => [],
      'GET /api/tasks': () => [],
    });
    renderUI(<StorageDialog open onOpenChange={() => undefined} />);
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('1.18 GB')).toBeInTheDocument();  // projects + cache + leftovers
    expect(within(dialog).getByText(/磁盘剩余/)).toHaveTextContent('40.0 GB');
    expect(within(dialog).getByText(/已导入任务的上传副本 150 MB · 被替换的旧音频 10.0 MB/)).toBeInTheDocument();
    expect(within(dialog).getByText(/另有模型 2.34 GB/)).toBeInTheDocument();

    // leftovers
    const leftRow = within(dialog).getByText('残留文件', { selector: 'div' }).closest('div.rounded-xl') as HTMLElement;
    await userEvent.click(within(leftRow).getByRole('button', { name: '清理' }));
    await waitFor(() => expect(api.find('POST', '/api/storage/clean')[0]?.body).toEqual({ leftovers: true }));
    await waitFor(() => expect(useApp.getState().toasts.map((t) => t.title)).toContain('已释放 160 MB'));

    // one project's exported video
    await userEvent.click(within(dialog).getByRole('button', { name: /わたぐも/ }));
    await userEvent.click(within(dialog).getByRole('button', { name: '删除 わたぐも-karaoke.mp4' }));
    await waitFor(() => expect(api.find('POST', '/api/projects/p1/storage/clean')[0]?.body).toEqual({ exports: ['わたぐも-karaoke.mp4'] }));

    // a busy project cannot be chosen; deleting the other asks first
    expect(within(dialog).getByRole('checkbox', { name: '选择 初恋' })).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('checkbox', { name: '选择 わたぐも' }));
    await userEvent.click(within(dialog).getByRole('button', { name: /删除所选 1 个/ }));
    await userEvent.click(within(dialog).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(api.find('DELETE', '/api/projects/p1')).toHaveLength(1));
    await waitFor(() => expect(useApp.getState().toasts.map((t) => t.title)).toContain('已删除 1 个项目，释放 500 MB'));
  });

  it('the cache waits while something runs', async () => {
    mockApi({ 'GET /api/storage': () => info({ working: true }) });
    renderUI(<StorageDialog open onOpenChange={() => undefined} />);
    const dialog = await screen.findByRole('dialog');
    const cacheRow = (await within(dialog).findByText('缓存', { selector: 'div' })).closest('div.rounded-xl') as HTMLElement;
    expect(within(cacheRow).getByRole('button', { name: '清理' })).toBeDisabled();
  });
});
