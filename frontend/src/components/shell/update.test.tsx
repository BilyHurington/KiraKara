import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useUpdate } from '@/store/update';
import { renderUI } from '@/test/helpers';
import { UpdateBadge, updateHowTo } from './UpdateBadge';

describe('update badge', () => {
  it('only when a newer version is out; says how to update', () => {
    useUpdate.setState({ info: { current: '1.1.0', latest: '1.1.0', newer: false } });
    const { container, unmount } = renderUI(<UpdateBadge />);
    expect(container).toBeEmptyDOMElement();
    unmount();
    useUpdate.setState({ info: { current: '1.1.0', latest: '1.2.0', newer: true, portable: true, updater: '更新.command', url: 'https://x' } });
    renderUI(<UpdateBadge />);
    expect(screen.getByRole('link', { name: /有新版本 1.2.0/ })).toHaveAttribute('href', 'https://x');
    expect(updateHowTo({ portable: true, updater: '更新.command' })).toContain('更新.command');
    expect(updateHowTo({ portable: false })).toContain('git pull');
  });
});
