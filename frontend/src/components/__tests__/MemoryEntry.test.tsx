import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkspaceLayout from '../WorkspaceLayout';
import DataPlatformLayout from '../DataPlatformLayout';
import Profile from '@/pages/Profile';
import MemorySettings from '@/pages/MemorySettings';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { useAuthStore } from '@/stores/authStore';
import { useWorkspaceStore, type Workspace } from '@/stores/workspaceStore';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

/**
 * 记忆入口回归。
 *
 * 背景：ChatBI 回答「已记住 X」后，用户在日常场景（工作站聊天 / 数据中台 / 首页）
 * 的用户下拉里只看到「个人设置 / 退出登录」——「我的记忆」只挂在系统配置页，
 * 等于用户无法核对与删除 AI 写入的内容（只能手输 URL）。入口必须与承诺同场景可见。
 */

const ws = { id: 7, name: '空间 7', user_default: true } as Workspace;

const brandAndMe = async (url: string) => ({
  data: url === '/admin/brand'
    ? { show_icon: true, show_text: true, app_name: 'AI-DataHub' }
    : url === '/users/me'
      ? { username: 'test', email: 't@example.com', phone: '', role: 'viewer' }
      : [],
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(brandAndMe as any);
  useAuthStore.setState({ user: { id: 1, username: 'test', role: 'viewer' } as any });
  useWorkspaceStore.setState({ loaded: true, workspaces: [ws], currentWorkspaceId: 7 } as any);
  usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function openUserMenu() {
  await userEvent.click(await screen.findByLabelText('用户菜单'));
}

describe('用户菜单里的「我的记忆」入口', () => {
  it('工作站（Chat 场景）可进入 /ws/7/memory', async () => {
    render(
      <TooltipProvider>
        <MemoryRouter initialEntries={['/ws/7/chat']}>
          <Routes>
            <Route path="/ws/:workspaceId" element={<WorkspaceLayout />}>
              <Route path="chat" element={<div>对话主体</div>} />
              <Route path="memory" element={<div>记忆页</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </TooltipProvider>,
    );
    await openUserMenu();
    await userEvent.click(await screen.findByText('我的记忆'));
    expect(await screen.findByText('记忆页')).toBeInTheDocument();
  });

  it('数据中台可进入 /data/memory', async () => {
    render(
      <TooltipProvider>
        <MemoryRouter initialEntries={['/data/datasources']}>
          <Routes>
            <Route path="/data" element={<DataPlatformLayout />}>
              <Route path="datasources" element={<div>数据源页</div>} />
              <Route path="memory" element={<div>记忆页</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </TooltipProvider>,
    );
    await openUserMenu();
    await userEvent.click(await screen.findByText('我的记忆'));
    expect(await screen.findByText('记忆页')).toBeInTheDocument();
  });

  it('个人设置页头提供「我的记忆」跳转，且不跳框架', async () => {
    render(
      <TooltipProvider>
        <MemoryRouter initialEntries={['/ws/7/profile']}>
          <Routes>
            <Route path="/ws/:workspaceId/profile" element={<Profile />} />
            <Route path="/ws/:workspaceId/memory" element={<MemorySettings />} />
          </Routes>
        </MemoryRouter>
      </TooltipProvider>,
    );
    const btn = await screen.findByRole('button', { name: /我的记忆/ });
    await userEvent.click(btn);
    // 记忆页自身标题渲染在同一框架路径下（不是 /system/memory）
    expect(await screen.findByText('我的记忆', { selector: 'h1' })).toBeInTheDocument();
  });
});
