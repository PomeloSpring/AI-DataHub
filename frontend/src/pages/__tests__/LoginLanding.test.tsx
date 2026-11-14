import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import Login from '../Login';
import { WorkspaceEntry } from '@/components/WorkspaceRoute';
import { invalidateCatalogCache } from '@/lib/dashboardCatalog';
import { toast } from '@/lib/toast';
import { useAuthStore } from '@/stores/authStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

/** 登录后落点写到 <output> 上，便于断言“默认进工作空间” */
function Path() {
  const location = useLocation();
  return <output data-testid="path">{location.pathname}</output>;
}

// 工作空间默认页在真路由里是 <Navigate to="chat">，此处按同一落点直接挂桩页，
// 断言的是“落到哪儿”，不是那一页渲染了什么
const myWorkspace = {
  id: 7, name: 'alice 的工作空间', description: '', icon: '', color: '',
  owner_id: 1, is_default: true, user_default: true, role: 'owner', created_at: '',
};

const renderLogin = () => render(<MemoryRouter initialEntries={['/login']}>
  <Path />
  <Routes>
    <Route path="/login" element={<Login />} />
    <Route path="/" element={<div>首页</div>} />
    <Route path="/workspace" element={<WorkspaceEntry module="workspace" />} />
    <Route path="/ws/:workspaceId/chat" element={<div>智能问数</div>} />
    <Route path="/ws/:workspaceId/dashboards" element={<div>工作空间看板清单</div>} />
  </Routes>
</MemoryRouter>);

/** 可见清单里有已启用看板：更早的口径会据此把人直接送去数据看板 */
const enabledCatalog = [{
  id: 7, name: '经营组', description: '', sort: 0,
  dashboards: [{ id: 3, name: '经营看板', description: '', status: 'enabled', is_default: 1, sort_order: 1 }],
}];

const fillAndSubmit = async () => {
  // 字段现在同时接受用户名与邮箱（2026-11-14 邮箱登录），label 文案为「用户名 / 邮箱」；
  // 按完整 label 取元素，文案漂移时这里会直接报错而不是静默取不到
  await userEvent.type(screen.getByLabelText('用户名 / 邮箱'), 'alice');
  await userEvent.type(screen.getByLabelText('密码'), 'pw123456');
  await userEvent.click(screen.getByRole('button', { name: /登录/ }));
};

const pathOf = (el: HTMLElement) => el.textContent;

/** 工作空间清单按指定行返回（默认给一个属于当前用户的空间） */
const stubWorkspaces = (rows: unknown[] = [myWorkspace]) => {
  vi.mocked(client.get).mockImplementation(((url: string) => {
    if (url === '/users/me') return Promise.resolve({ data: { id: 1, username: 'alice', user_role: 'viewer' } });
    if (url === '/workspaces') return Promise.resolve({ data: rows });
    if (url === '/dashboard/groups/visible') return Promise.resolve({ data: enabledCatalog });
    return Promise.resolve({ data: [] });
  }) as never);
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  invalidateCatalogCache();
  useAuthStore.setState({ token: null, user: null } as never);
  useWorkspaceStore.getState().reset();
  stubWorkspaces();
  vi.mocked(client.post).mockResolvedValue({
    data: { access_token: 'at', refresh_token: 'rt', user: { id: 1, username: 'alice', role: 'viewer' } },
  } as never);
});
afterEach(() => { cleanup(); });

describe('登录成功后的落点', () => {
  it('默认进工作空间：解析出默认空间后落智能问数（工作空间默认页）', async () => {
    renderLogin();
    await fillAndSubmit();
    await waitFor(() => expect(pathOf(screen.getByTestId('path'))).toBe('/ws/7/chat'));
    expect(await screen.findByText('智能问数')).toBeInTheDocument();
    expect(screen.queryByText('首页')).not.toBeInTheDocument();
    expect(screen.queryByText('工作空间看板清单')).not.toBeInTheDocument();
  });

  it('不再拉看板目录做落点判定（落点只看工作空间，与有没有已启用看板无关）', async () => {
    renderLogin();
    await fillAndSubmit();
    await waitFor(() => expect(pathOf(screen.getByTestId('path'))).toBe('/ws/7/chat'));
    const urls = vi.mocked(client.get).mock.calls.map(([u]) => u);
    expect(urls).not.toContain('/dashboard/groups/visible');
  });

  it('登录后工作空间清单被重置，不会沿用上一个账号的空间串到别处', async () => {
    useWorkspaceStore.setState({ workspaces: [{ ...myWorkspace, id: 99 }], currentWorkspaceId: 99, loaded: true });
    localStorage.setItem('currentWorkspace', '99');
    renderLogin();
    await fillAndSubmit();
    await waitFor(() => expect(pathOf(screen.getByTestId('path'))).toBe('/ws/7/chat'));
  });

  it('无可访问工作空间时显式说明原因并给出返回首页入口，不静默停空白页', async () => {
    stubWorkspaces([]);
    renderLogin();
    await fillAndSubmit();
    await waitFor(() => expect(pathOf(screen.getByTestId('path'))).toBe('/workspace'));
    expect(await screen.findByText('暂无可访问的工作空间，请联系管理员分配权限')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/');
  });

  it('登录失败留在登录页并提示原因，不做跳转', async () => {
    vi.mocked(client.post).mockRejectedValue({ response: { data: { detail: '用户名或密码错误' } } } as never);
    renderLogin();
    await fillAndSubmit();
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('用户名或密码错误'));
    expect(pathOf(screen.getByTestId('path'))).toBe('/login');
  });
});
