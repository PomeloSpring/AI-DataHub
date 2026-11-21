// 系统配置侧栏的菜单显隐回归（重点锁 2026-11-21 进化中心迁出）。
//
// 口径来源 menu-permission-sync §1/§2.5：
// * 页面挪模块 = 路由 + 侧栏 + 菜单登记 + 权限点归属同批改，任一漏改都会留下
//   "两处入口"或"配了看不见"；
// * 这里锁系统侧栏**不再**出现「进化中心」（它已迁到数据中台「数据资产」段，
//   /data/evolution），同时确认系统配置自身的项（评测中心等）没被连带删掉。
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import SystemLayout from '../SystemLayout';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { useAuthStore } from '@/stores/authStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

function renderLayout() {
  return render(
    <TooltipProvider>
      <MemoryRouter initialEntries={['/system/eval']}>
        <Routes>
          <Route path="/system" element={<SystemLayout />}>
            <Route path="eval" element={<div>页面：评测中心</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </TooltipProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => ({
    data: url === '/admin/brand' ? { show_icon: true, show_text: true, app_name: 'AI-DataHub' } : [],
  }));
  // admin：SystemLayout 对非 admin 会直接重定向到工作空间
  useAuthStore.setState({ user: { id: 1, username: 'test', role: 'admin' } as any });
});
afterEach(() => cleanup());

describe('系统配置侧栏不再承载进化中心', () => {
  it('侧栏不出现「进化中心」（已迁至数据中台 /data/evolution）', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    const nav = screen.getByRole('navigation', { name: '系统配置导航' });
    expect(within(nav).queryByText('进化中心')).not.toBeInTheDocument();
  });

  it('系统配置自身的项仍在（迁移只摘一项，不得连带删掉邻居）', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    const nav = screen.getByRole('navigation', { name: '系统配置导航' });
    expect(within(nav).getByText('评测中心')).toBeInTheDocument();
    expect(within(nav).getByText('LLM 可观测')).toBeInTheDocument();
    expect(within(nav).getByText('系统设置')).toBeInTheDocument();
  });

  it('权限未就绪时只渲染骨架，不先把菜单画出来再抽走', () => {
    usePermissionStore.setState({ loaded: false, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    expect(screen.getByLabelText('菜单加载中')).toBeInTheDocument();
    expect(screen.queryByText('评测中心')).not.toBeInTheDocument();
  });
});