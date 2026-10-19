import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import WorkspaceLayout from '../WorkspaceLayout';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { useAuthStore } from '@/stores/authStore';
import { useWorkspaceStore, type Workspace } from '@/stores/workspaceStore';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

const ws = { id: 7, name: '空间 7', user_default: true } as Workspace;

function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname}</output>; }

function renderLayout() {
  return render(
    <TooltipProvider>
      <MemoryRouter initialEntries={['/ws/7/chat']}>
        <Path />
        <Routes>
          <Route path="/ws/:workspaceId" element={<WorkspaceLayout />}>
            <Route path="chat" element={<div>对话主体</div>} />
            <Route path="dashboards" element={<div>看板清单</div>} />
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
  useAuthStore.setState({ user: { id: 1, username: 'test', role: 'viewer' } as any });
  useWorkspaceStore.setState({ loaded: true, workspaces: [ws], currentWorkspaceId: 7 } as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('WorkspaceLayout 菜单按权限码过滤', () => {
  it('未授权菜单不渲染（workspace:chat/reports/scheduled 口径）', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat'], perms: [] });
    renderLayout();
    expect(screen.getByText('智能问数')).toBeInTheDocument();
    expect(screen.queryByText('报表中心')).not.toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
  });

  it('授权菜单按 allowedMenus 可见', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat', 'workspace:reports'], perms: [] });
    renderLayout();
    expect(screen.getByText('智能问数')).toBeInTheDocument();
    expect(screen.getByText('报表中心')).toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
  });

  it('unrestricted（admin/未限制）全部可见', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    expect(screen.getByText('智能问数')).toBeInTheDocument();
    expect(screen.getByText('报表中心')).toBeInTheDocument();
    expect(screen.getByText('任务调度')).toBeInTheDocument();
  });

  it('数据看板入口随菜单配置显隐（workspace:dashboards ↔ dashboard:read）', () => {
    // 菜单登记与角色权限同步后: 未授予菜单 → 不渲染; 授予后 → 可进, 落看板清单
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat'], perms: [] });
    const view = renderLayout();
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument();
    view.unmount();
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat', 'workspace:dashboards'], perms: ['dashboard:read'] });
    renderLayout();
    const entry = screen.getByText('数据看板').closest('button');
    expect(entry).toBeInTheDocument();
    fireEvent.click(entry!);
    expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards');
  });
});

describe('权限未就绪时不得闪现无权限菜单', () => {
  it('权限清单未加载时只渲染骨架（isMenuAllowed 未加载时 fail-open，不能直接画全量菜单）', () => {
    usePermissionStore.setState({ loaded: false, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    expect(screen.getByLabelText('菜单加载中')).toBeInTheDocument();
    expect(screen.queryByText('自动化')).not.toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument();
  });

  it('只授报表中心时：加载完成后「自动化」分组与任务调度不出现（先闪现后消失即此回归）', () => {
    usePermissionStore.setState({ loaded: false, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    act(() => usePermissionStore.setState({
      loaded: true, unrestricted: false,
      allowedMenus: ['workspace:dashboards', 'workspace:chat', 'workspace:reports'],
      perms: ['report:read'],
    }));
    expect(screen.getByText('报表中心')).toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
    expect(screen.queryByText('自动化')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('菜单加载中')).not.toBeInTheDocument();
  });
});
