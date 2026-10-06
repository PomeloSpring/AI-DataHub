import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkspaceLayout from '../WorkspaceLayout';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { useAuthStore } from '@/stores/authStore';
import { useWorkspaceStore, type Workspace } from '@/stores/workspaceStore';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

const ws = { id: 7, name: '空间 7', user_default: true } as Workspace;

function renderLayout() {
  return render(
    <TooltipProvider>
      <MemoryRouter initialEntries={['/ws/7/chat']}>
        <Routes>
          <Route path="/ws/:workspaceId" element={<WorkspaceLayout />}>
            <Route path="chat" element={<div>对话主体</div>} />
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
    expect(screen.getByText('Chat 智能问答')).toBeInTheDocument();
    expect(screen.queryByText('报表中心')).not.toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
  });

  it('授权菜单按 allowedMenus 可见', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat', 'workspace:reports'], perms: [] });
    renderLayout();
    expect(screen.getByText('Chat 智能问答')).toBeInTheDocument();
    expect(screen.getByText('报表中心')).toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
  });

  it('unrestricted（admin/未限制）全部可见', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    expect(screen.getByText('Chat 智能问答')).toBeInTheDocument();
    expect(screen.getByText('报表中心')).toBeInTheDocument();
    expect(screen.getByText('任务调度')).toBeInTheDocument();
  });
});
