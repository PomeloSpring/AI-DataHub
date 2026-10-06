import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RLSManagement from '../RLSManagement';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/admin/rls-policies') return { data: { total: 0, items: [] } };
    return { data: [] };
  });
  vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('数据安全策略 — 资源显示用 name', () => {
  it('策略卡片显示数据源 name 而非裸 ID，悬空数据源标注已删除', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/datasources/') return { data: [{ id: 1, name: 'test-alb', db_type: 'mysql' }] };
      if (url === '/admin/rls-policies') return {
        data: {
          total: 2,
          items: [
            { id: 1, name: '策略A', description: '', workspace_id: 0, datasource_id: 1, table_name: 'orders', policy_type: 'row', filter_type: 'condition', filter_expr: '', user_attribute: '', is_active: 1, created_by: 1, created_at: '', updated_at: '' },
            { id: 2, name: '策略B', description: '', workspace_id: 0, datasource_id: 325991609814964, table_name: 'orders', policy_type: 'row', filter_type: 'condition', filter_expr: '', user_attribute: '', is_active: 1, created_by: 1, created_at: '', updated_at: '' },
          ],
        },
      };
      return { data: [] };
    });
    render(<RLSManagement />);
    await screen.findByText('test-alb');
    // 悬空数据源给可读标注, 不回退成裸 ID
    expect(screen.getByText('数据源已删除')).toBeInTheDocument();
    expect(screen.queryByText(/ID:/)).not.toBeInTheDocument();
  });
});

describe('数据安全策略 — 用户属性页签已退役', () => {
  it('属性统一由角色权限管理，不再提供按用户配置的属性页签', async () => {
    render(<RLSManagement />);
    await screen.findByRole('tab', { name: /行策略/ });
    expect(screen.queryByRole('tab', { name: /用户属性/ })).not.toBeInTheDocument();
    // 旧的按用户配置接口也不再被调用
    expect(client.get).not.toHaveBeenCalledWith('/admin/rls-user-attributes/10', expect.anything());
  });
});

describe('数据安全策略 — 审计日志详情', () => {
  it('拒绝访问记录同样提供查看详情（含拒绝原因），用户显示 name 而非裸 ID', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/admin/rls-policies') return { data: { total: 0, items: [] } };
      if (url === '/users/') return { data: { items: [{ id: 7778154925, username: 'alice' }] } };
      if (url === '/admin/rls-audit-logs') return {
        data: {
          total: 1,
          items: [{
            id: 1, user_id: 7778154925, workspace_id: 0, policy_id: null,
            policy_name: 'permission_enforcer', table_name: '', action: 'deny',
            original_sql: 'select 1', filtered_sql: '', deny_reason: '无权访问该数据源',
            created_at: '2026-10-04T17:06:15',
          }],
        },
      };
      return { data: [] };
    });
    render(<RLSManagement />);
    // Radix 页签切换响应 mousedown，需完整指针事件序列激活
    const user = userEvent.setup();
    await user.click(await screen.findByRole('tab', { name: /审计日志/ }));
    // 拒绝记录不再因 filtered_sql 为空而丢掉详情入口
    const summary = await screen.findByText('查看详情');
    fireEvent.click(summary);
    expect(screen.getByText(/无权访问该数据源/)).toBeInTheDocument();
    expect(screen.getByText(/select 1/)).toBeInTheDocument();
    // 用户显示 name，不裸显 id
    expect(screen.getByText(/用户: alice/)).toBeInTheDocument();
  });
});
