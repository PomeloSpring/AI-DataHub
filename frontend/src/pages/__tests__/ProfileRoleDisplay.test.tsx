import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Profile from '@/pages/Profile';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

/**
 * 个人设置「账号信息 → 角色」显示回归。
 *
 * 历史缺陷：/users/me 的账号列叫 user_role，本页却读 profile.role，
 * 取不到值就渲染成一枚空白徽标（用户看到的「角色」是空的）。
 * 现口径：显示名由服务端按真值源（adh_user_roles ⋈ adh_roles.display_name）下发 role_name，
 * 自定义角色显示业务名；没有生效角色时如实标注，不留空白。
 */
const renderProfile = (me: Record<string, unknown>) => {
  vi.mocked(client.get).mockImplementation(((url: string) => {
    if (url === '/users/me') return Promise.resolve({ data: me });
    if (url === '/users/me/preferences') return Promise.resolve({ data: { cloud_sync_enabled: true } });
    return Promise.resolve({ data: [] });
  }) as never);
  render(
    <MemoryRouter initialEntries={['/ws/7/profile']}>
      <Routes>
        <Route path="/ws/:workspaceId/profile" element={<Profile />} />
      </Routes>
    </MemoryRouter>,
  );
};

const roleCell = async () => {
  const label = await screen.findByText('角色', { selector: 'div' });
  return label.parentElement as HTMLElement;
};

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('个人设置的角色一格', () => {
  it('按服务端下发的角色可读名显示，不再渲染空白徽标', async () => {
    renderProfile({
      id: 1, username: 'admin', status: 'active', user_role: 'admin',
      role: 'admin', role_name: '管理员', roles: [{ name: 'admin', display_name: '管理员' }],
      created_at: '2026-05-07 19:55:27', last_login: '2026-11-12 19:25:08',
    });
    expect(await roleCell()).toHaveTextContent('管理员');
  });

  it('自定义角色显示业务名（运营管理），不露内部英文名', async () => {
    renderProfile({
      id: 4, username: 'lisi', status: 'active', user_role: 'Operations Manager',
      role: 'Operations Manager', role_name: '运营管理',
      roles: [{ name: 'Operations Manager', display_name: '运营管理' }],
    });
    const cell = await roleCell();
    expect(cell).toHaveTextContent('运营管理');
    expect(cell.textContent).not.toContain('Operations Manager');
  });

  it('账号没有生效角色时如实标注，而不是留一个空白徽标', async () => {
    renderProfile({
      id: 2, username: 'ws_test_user', status: 'active', user_role: 'viewer',
      role: '', role_name: '', roles: [],
    });
    expect(await roleCell()).toHaveTextContent('未绑定角色');
  });
});
