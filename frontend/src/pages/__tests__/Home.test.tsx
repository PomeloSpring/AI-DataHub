import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import Home from '../Home';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const groupBoard = (id: number, status = 'enabled') => ({ id, name: `看板 ${id}`, status, is_default: 0, sort_order: id });
function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname}</output>; }
function renderHome() {
  return render(<MemoryRouter initialEntries={['/']}><Path /><Routes>
    <Route path="/" element={<Home />} />
    <Route path="/dashboards" element={<div>数据看板</div>} />
  </Routes></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: { id: 1, username: 'alice', role: 'viewer' } } as any);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
});

describe('默认首页', () => {
  it('已分配仪表盘时不再显示未分配提示(或直达数据看板)', async () => {
    vi.mocked(client.get).mockResolvedValue({
      data: [{ id: 7, name: '经营组', description: '', sort: 0, dashboards: [groupBoard(3)] }],
    });
    renderHome();
    await waitFor(() => {
      const atDashboards = screen.getByTestId('path').textContent === '/dashboards';
      const emptyHint = screen.queryByText('还没有分配仪表盘');
      expect(atDashboards || !emptyHint).toBe(true);
    }, { timeout: 3000 });
  });

  it('未分配仪表盘时展示默认首页与快捷入口', async () => {
    vi.mocked(client.get).mockResolvedValue({ data: [] });
    renderHome();
    expect(await screen.findByRole('heading', { name: /alice/ }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText('还没有分配仪表盘')).toBeInTheDocument();
    expect(screen.getByText('工作空间')).toBeInTheDocument();
    expect(screen.getByText('数据中台')).toBeInTheDocument();
    // 非 admin 不显示系统配置入口与配置按钮
    expect(screen.queryByText('系统配置')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /去配置仪表盘/ })).not.toBeInTheDocument();
  });

  it('admin 未分配时提供仪表盘配置引导', async () => {
    useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin' } } as any);
    vi.mocked(client.get).mockResolvedValue({ data: [] });
    renderHome();
    expect(await screen.findByRole('button', { name: /去配置仪表盘/ }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText('系统配置')).toBeInTheDocument();
  });

  it('快速开始按权限码显隐：无对应功能的入口不展示', async () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, perms: [], allowedMenus: ['workspace:chat'] });
    vi.mocked(client.get).mockResolvedValue({ data: [] });
    renderHome();
    expect(await screen.findByRole('heading', { name: /alice/ }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText('工作空间')).toBeInTheDocument();
    // 无 data:* 菜单 → 不展示数据中台入口(点进去只剩空布局)
    expect(screen.queryByText('数据中台')).not.toBeInTheDocument();
  });

  it('全部入口被权限裁掉时不渲染快速开始整段', async () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, perms: [], allowedMenus: [] });
    vi.mocked(client.get).mockResolvedValue({ data: [] });
    renderHome();
    expect(await screen.findByText('还没有分配仪表盘', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText('快速开始')).not.toBeInTheDocument();
    expect(screen.queryByText('工作空间')).not.toBeInTheDocument();
  });

  it('组内仅有未启用看板时不进入数据看板(浏览仅认启用)', async () => {
    vi.mocked(client.get).mockResolvedValue({
      data: [{ id: 0, name: '未分组', description: '', sort: 9999, dashboards: [groupBoard(2, 'designing')] }],
    });
    renderHome();
    expect(await screen.findByText('还没有分配仪表盘', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByTestId('path')).toHaveTextContent('/');
  });
});
