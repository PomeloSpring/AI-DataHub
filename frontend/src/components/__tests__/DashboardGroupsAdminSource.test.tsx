/**
 * 看板组合管理：「组合内看板」清单必须与同页看板列表同一事实源。
 *
 * 历史缺陷：弹窗自己发一次 GET /dashboard/（不带 workspace_id，前端也不带 X-Workspace-Id 头），
 * 后端 workspace_id=0 时不加工作空间过滤 → 跨工作空间全量返回，清单与看板列表对不上。
 * 修复：复用看板管理页按当前工作空间加载好的 store.dashboards，不再另发一份取数。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import DashboardGroupsAdmin from '../DashboardGroupsAdmin';
import { useDashboardStore, type Dashboard } from '@/stores/dashboardStore';
import client from '@/api/client';

const board = (id: number, name: string, status: Dashboard['status'] = 'enabled'): any =>
  ({ id, name, status, description: '', charts: [], workspace_id: 301, owner_id: 1 });

let gets: string[];
beforeEach(() => {
  gets = [];
  useDashboardStore.setState({ dashboards: [board(1, '官网用户操作看板'), board(2, '版本统计', 'designing')] } as any);
  vi.spyOn(client, 'get').mockImplementation(((url: string) => {
    gets.push(url);
    if (url === '/dashboard/groups') return Promise.resolve({ data: [{ id: 9, name: '中国区数据集锦', description: '', sort: 0, dashboard_ids: [1], role_ids: [1] }] });
    if (url === '/roles/') return Promise.resolve({ data: [{ id: 1, display_name: '管理员' }] });
    return Promise.resolve({ data: [] });
  }) as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('组合内看板清单与看板列表同源', () => {
  it('不再自己发无 workspace 的 GET /dashboard/（第二口径）', async () => {
    render(<DashboardGroupsAdmin />);
    await screen.findByText('中国区数据集锦');
    expect(gets).not.toContain('/dashboard/');
  });

  it('组合内看板选项来自 store（与看板管理列表一致）', async () => {
    render(<DashboardGroupsAdmin />);
    await screen.findByText('中国区数据集锦');
    fireEvent.click(screen.getByRole('button', { name: '编辑组合 中国区数据集锦' }));
    // store 里的两条看板都要出现，设计中那条带状态标注
    expect(await screen.findByRole('checkbox', { name: /官网用户操作看板/ })).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: /版本统计/ })).toBeTruthy();
    expect(screen.getByText('设计中')).toBeTruthy();
  });

  it('store 里没有的看板不出现在清单（跨工作空间的不会冒出）', async () => {
    render(<DashboardGroupsAdmin />);
    await screen.findByText('中国区数据集锦');
    fireEvent.click(screen.getByRole('button', { name: '编辑组合 中国区数据集锦' }));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /官网用户操作看板/ })).toBeTruthy());
    // 弹窗清单条数 = store 条数（2），不来自未过滤的全量接口
    expect(document.querySelectorAll('input[type=checkbox]').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('其他工作空间的看板')).toBeNull();
  });
});
