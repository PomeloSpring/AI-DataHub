import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import KbSyncButton from '../KbSyncButton';
import client from '@/api/client';
import { toast } from '@/lib/toast';
import { usePermissionStore } from '@/stores/permissionStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

/**
 * 「同步知识库」按钮（本体建模工具栏）口径回归。
 *
 * 背景：这条动作原先只在「关系图谱」独立页里，独立页下线后必须回到建模页工具栏，
 * 且与状态徽章同屏（做完立刻看到回显）。锁住四件事：
 * 1. 治理动作按 kb:manage 裁决：无权限不渲染按钮，也不去拉候选库（不给必 403 的死开关）；
 * 2. 仅 active 模型可同步：draft/archived 显式禁用并给原因（服务端会 skip，不假报成功）；
 * 3. 成败都把**库名**说给用户；跳过原因翻成可读文案，不向用户回英文码；
 * 4. 动作结束后回调调用方重取状态徽章（成败都要回显服务端真实状态）；
 * 5. 候选库拉取失败要显式标注（仍可按默认路由同步），不装成“没有库”。
 */
const mockGet = vi.mocked(client.get);
const mockPost = vi.mocked(client.post);

beforeEach(() => {
  vi.clearAllMocks();
  usePermissionStore.setState({ perms: null, allowedMenus: null, unrestricted: true, loaded: false });
  mockGet.mockResolvedValue({ data: { items: [{ id: 11, name: '系统知识库' }] } } as never);
  mockPost.mockResolvedValue({ data: { synced: 1, targets: ['系统知识库'] } } as never);
});
afterEach(() => cleanup());

describe('KbSyncButton', () => {
  it('无 kb:manage 权限不渲染按钮，也不去拉候选库（不给必 403 的死开关）', () => {
    usePermissionStore.setState({ perms: ['ontology:read'], unrestricted: false, loaded: true });
    render(<KbSyncButton modelId={901} active />);
    expect(screen.queryByRole('button', { name: /同步知识库/ })).toBeNull();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('生效模型：按默认路由同步，并把目标库名回报给用户', async () => {
    const onSynced = vi.fn();
    render(<KbSyncButton modelId={901} active onSynced={onSynced} />);
    await userEvent.click(screen.getByRole('button', { name: /同步知识库/ }));
    await waitFor(() => expect(onSynced).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith(
      '/catalog/ontology/models/901/kb-sync', null, { params: {} });
    expect(vi.mocked(toast.success)).toHaveBeenCalledWith('已同步到知识库：系统知识库');
  });

  it('草案/归档模型不可同步：按钮禁用并给出原因（服务端会 skip，不假报成功）', () => {
    render(<KbSyncButton modelId={901} active={false} />);
    const btn = screen.getByRole('button', { name: /同步知识库/ });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', '仅生效模型可同步知识库');
  });

  it('服务端 skip：原因翻成可读文案，不向用户回英文码', async () => {
    mockPost.mockResolvedValue({ data: { skipped: 'no_kb_bound' } } as never);
    render(<KbSyncButton modelId={901} active />);
    await userEvent.click(screen.getByRole('button', { name: /同步知识库/ }));
    await waitFor(() => expect(vi.mocked(toast.warning))
      .toHaveBeenCalledWith('未同步：模型未绑定目标知识库'));
  });

  it('候选库拉取失败显式标注（仍可按默认路由同步），不装成“没有库”', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    render(<KbSyncButton modelId={901} active />);
    await waitFor(() => expect(screen.getByTitle(
      '目标知识库清单加载失败：只能按模型默认路由同步')).toBeTruthy());
    // 默认路由仍可执行
    expect(screen.getByRole('button', { name: /同步知识库/ })).not.toBeDisabled();
  });
});