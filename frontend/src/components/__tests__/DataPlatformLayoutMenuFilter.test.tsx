// 数据中台侧栏的菜单显隐回归（重点锁新加入的 data:decisions）。
//
// 口径来源 menu-permission-sync §2.4/§2.5：
// * 显隐一律走 `menuAccess.allowed(toMenuKey(path))` + `pruneEmptySections`，
//   不允许页面自己再写一套"我看得到吗"的判断；
// * 角色页的菜单分组/名称/顺序必须与实际导航逐项一致 —— 所以这里同时锁
//   「决策建议」在「数据目录」段内的位置（与 adh_menu_registry 的 sort=10 对齐）。
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import DataPlatformLayout from '../DataPlatformLayout';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { useAuthStore } from '@/stores/authStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

function Path() {
  const location = useLocation();
  return <output data-testid="path">{location.pathname}</output>;
}

function renderLayout(path = '/data/metrics') {
  return render(
    <TooltipProvider>
      <MemoryRouter initialEntries={[path]}>
        <Path />
        <Routes>
          <Route path="/data" element={<DataPlatformLayout />}>
            <Route path="metrics" element={<div>页面：指标与维度</div>} />
            <Route path="knowledge-graph" element={<div>页面：关系图谱</div>} />
            <Route path="decisions" element={<div>页面：决策树配置</div>} />
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
  useAuthStore.setState({ user: { id: 1, username: 'test', role: 'analyst' } as any });
});
afterEach(() => cleanup());

const CATALOG = ['data:metrics', 'data:knowledge-graph', 'data:decisions'];

describe('数据中台侧栏按菜单码显隐', () => {
  it('未授权 data:decisions 时不出现「决策建议」（菜单码就是唯一真值源）', () => {
    usePermissionStore.setState({
      loaded: true, unrestricted: false, allowedMenus: ['data:metrics', 'data:knowledge-graph'], perms: [],
    });
    renderLayout();
    expect(screen.getByText('指标与维度')).toBeInTheDocument();
    expect(screen.queryByText('决策建议')).not.toBeInTheDocument();
  });

  it('授权后出现在「数据目录」段最后，并可点进 /data/decisions', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: CATALOG, perms: ['decision:manage'] });
    renderLayout();
    const nav = screen.getByRole('navigation', { name: '数据中台导航' });
    const labels = within(nav).getAllByRole('button').map(b => b.textContent?.trim()).filter(Boolean);
    expect(labels).toEqual(['指标与维度', '关系图谱', '决策建议']);
    fireEvent.click(within(nav).getByText('决策建议').closest('button')!);
    expect(screen.getByTestId('path')).toHaveTextContent('/data/decisions');
  });

  it('只授决策建议时「数据目录」段标题仍出现（pruneEmptySections 只删空段）', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['data:decisions'], perms: ['decision:manage'] });
    renderLayout();
    expect(screen.getByText('数据目录')).toBeInTheDocument();
    expect(screen.getByText('决策建议')).toBeInTheDocument();
    expect(screen.queryByText('指标与维度')).not.toBeInTheDocument();
  });

  it('权限未就绪时只渲染骨架，不先把无权限菜单画出来再抽走', () => {
    usePermissionStore.setState({ loaded: false, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    expect(screen.getByLabelText('菜单加载中')).toBeInTheDocument();
    expect(screen.queryByText('决策建议')).not.toBeInTheDocument();
    expect(screen.queryByText('数据目录')).not.toBeInTheDocument();
  });

  it('admin（unrestricted）全部可见，无需逐个勾码', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    expect(screen.getByText('决策建议')).toBeInTheDocument();
    expect(screen.getByText('关系图谱')).toBeInTheDocument();
  });
});
