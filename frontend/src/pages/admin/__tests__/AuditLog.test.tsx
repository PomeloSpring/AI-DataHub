import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AuditLog from '../AuditLog';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn() } }));

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('审计日志页 — 接口契约', () => {
  it('请求后端真实路由 /audit/logs（不是历史 /auth/audit-logs）并渲染记录', async () => {
    vi.mocked(client.get).mockResolvedValue({
      data: {
        total: 1,
        items: [{
          id: 1, user_id: 9, username: 'admin', action: 'login', module: 'auth',
          target_type: '', target_id: 0, detail: '登录成功', ip_address: '127.0.0.1',
          created_at: '2026-11-18T09:00:00',
        }],
      },
    } as any);

    render(<AuditLog />);

    await screen.findByText('登录成功');
    expect(client.get).toHaveBeenCalledWith(
      '/audit/logs',
      expect.objectContaining({ params: expect.objectContaining({ page: 1 }) }),
    );
    // 历史缺陷：请求 /auth/audit-logs 404，页面却显示“暂无审计日志”
    expect(client.get).not.toHaveBeenCalledWith('/auth/audit-logs', expect.anything());
    expect(screen.getByText('admin')).toBeInTheDocument();
  });

  it('接口失败时显式报错，不得静默成“暂无审计日志”', async () => {
    vi.mocked(client.get).mockRejectedValue({
      response: { data: { detail: '审计日志查询失败' } },
      message: 'Request failed with status code 404',
    });

    render(<AuditLog />);

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('审计日志查询失败');
    expect(screen.queryByText('暂无审计日志')).not.toBeInTheDocument();
  });
});
