import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import GraphRebuildButton, { graphScopeForModel } from '../GraphRebuildButton';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

/**
 * 图谱「重建」按钮的口径回归（本体建模工作台工具栏；与之同屏的还有「同步知识库」，见 KbSyncButton.test.tsx）。
 *
 * 锁住三件事：
 * 1. 作用域与看图同源：system→系统图（ds 0 + system_scope）；business→聚合大图（ds 0，kind 必带）；
 *    不得把 business 的雪花 datasource_id 当图作用域（ds:那个 id 根本不存在，重建出来是空图）；
 * 2. 治理动作按 graph:manage 把关：无权限不渲染按钮（不给点了必 403 的死开关）；
 * 3. 成功后回调调用方重取图与徽章（否则「需重建」还挂着、图仍是旧的）。
 */
describe('GraphRebuildButton', () => {
  beforeEach(() => {
    vi.mocked(client.post).mockResolvedValue({ data: { success: true, message: '图谱已重建' } } as any);
    usePermissionStore.setState({ perms: null, allowedMenus: null, unrestricted: true, loaded: false });
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it('作用域按 kind 分域：system/business 落到 ds 0 且 kind 随请求上送', () => {
    expect(graphScopeForModel('system', 0)).toEqual({ datasource_id: 0, system_scope: true, kind: 'system' });
    expect(graphScopeForModel('business', 1794535612029))
      .toEqual({ datasource_id: 0, system_scope: false, kind: 'business' });
    expect(graphScopeForModel('source', 1794535612029))
      .toEqual({ datasource_id: 1794535612029, system_scope: false, kind: 'source' });
  });

  it('系统本体：点「图谱生成」按系统图重建，并回调调用方刷新图/徽章', async () => {
    const onRebuilt = vi.fn();
    render(<GraphRebuildButton kind="system" datasourceId={0} onRebuilt={onRebuilt} />);
    await userEvent.click(screen.getByRole('button', { name: /图谱生成/ }));
    await waitFor(() => expect(onRebuilt).toHaveBeenCalledTimes(1));
    expect(vi.mocked(client.post).mock.calls[0][0]).toBe('/graph/sync');
    expect(vi.mocked(client.post).mock.calls[0][2]).toEqual({
      params: { datasource_id: 0, system_scope: true, kind: 'system' },
    });
  });

  it('业务本体：重建聚合大图，不拿模型自己的 datasource_id 当图作用域', async () => {
    render(<GraphRebuildButton kind="business" datasourceId={1794535612029} />);
    await userEvent.click(screen.getByRole('button', { name: /图谱生成/ }));
    await waitFor(() => expect(client.post).toHaveBeenCalled());
    expect(vi.mocked(client.post).mock.calls[0][2]).toEqual({
      params: { datasource_id: 0, system_scope: false, kind: 'business' },
    });
  });

  it('无 graph:manage 权限不渲染按钮（不给必 403 的死开关）', () => {
    usePermissionStore.setState({ perms: ['ontology:read'], unrestricted: false, loaded: true });
    render(<GraphRebuildButton kind="system" datasourceId={0} />);
    expect(screen.queryByRole('button', { name: /图谱生成/ })).toBeNull();
  });
});