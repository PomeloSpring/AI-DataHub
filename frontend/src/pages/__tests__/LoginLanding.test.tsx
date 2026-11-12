import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import Login from '../Login';
import { invalidateCatalogCache } from '@/lib/dashboardCatalog';
import { toast } from '@/lib/toast';
import { useAuthStore } from '@/stores/authStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

/** 登录后落点写到 <output> 上，便于断言"恒进首页" */
function Path() {
  const location = useLocation();
  return <output data-testid="path">{location.pathname}</output>;
}

const renderLogin = () => render(<MemoryRouter initialEntries={['/login']}>
  <Path />
  <Routes>
    <Route path="/login" element={<Login />} />
    <Route path="/" element={<div>首页</div>} />
    <Route path="/dashboards" element={<div>数据看板</div>} />
  </Routes>
</MemoryRouter>);

/** 目录里已有启用看板：旧口径会据此把人直接送去 /dashboards */
const enabledCatalog = [{
  id: 7, name: '经营组', description: '', sort: 0,
  dashboards: [{ id: 3, name: '经营看板', description: '', status: 'enabled', is_default: 1, sort_order: 1 }],
}];

const fillAndSubmit = async () => {
  await userEvent.type(screen.getByLabelText('用户名'), 'alice');
  await userEvent.type(screen.getByLabelText('密码'), 'pw123456');
  await userEvent.click(screen.getByRole('button', { name: /登录/ }));
};

const pathOf = (el: HTMLElement) => el.textContent;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  invalidateCatalogCache();
  useAuthStore.setState({ token: null, user: null } as never);
  vi.mocked(client.post).mockResolvedValue({
    data: { access_token: 'at', refresh_token: 'rt', user: { id: 1, username: 'alice', role: 'viewer' } },
  } as never);
  vi.mocked(client.get).mockImplementation(((url: string) => {
    if (url === '/users/me') return Promise.resolve({ data: { id: 1, username: 'alice', user_role: 'viewer' } });
    if (url === '/dashboard/groups/visible') return Promise.resolve({ data: enabledCatalog });
    return Promise.resolve({ data: [] });
  }) as never);
});
afterEach(() => { cleanup(); });

describe('登录成功后的落点', () => {
  it('恒进首页，即使可见清单里有已启用看板', async () => {
    renderLogin();
    await fillAndSubmit();
    await waitFor(() => expect(pathOf(screen.getByTestId('path'))).toBe('/'));
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument();
    expect(screen.getByText('首页')).toBeInTheDocument();
  });

  it('不再拉看板目录做落点判定（落点判定已取消，首页自带看板区）', async () => {
    renderLogin();
    await fillAndSubmit();
    await waitFor(() => expect(pathOf(screen.getByTestId('path'))).toBe('/'));
    const urls = vi.mocked(client.get).mock.calls.map(([u]) => u);
    expect(urls).not.toContain('/dashboard/groups/visible');
  });

  it('登录失败留在登录页并提示原因，不做跳转', async () => {
    vi.mocked(client.post).mockRejectedValue({ response: { data: { detail: '用户名或密码错误' } } } as never);
    renderLogin();
    await fillAndSubmit();
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('用户名或密码错误'));
    expect(pathOf(screen.getByTestId('path'))).toBe('/login');
  });
});
