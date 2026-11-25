/**
 * 数据标准清单服务端**真分页**回归（.qoder/rules/list-pagination-server-side.md）：
 * - 请求带 page/page_size（参数名与后端 Query 同口径，不能被后端忽略）；
 * - 分页条总数渲染自服务端 total，翻页带新 page 重新请求。
 */
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Standards from '../Standards';
import { governanceApi } from '@/api/governance';

vi.mock('@/api/governance', () => ({
  governanceApi: {
    listStandards: vi.fn(),
    createStandard: vi.fn(),
    updateStandard: vi.fn(),
    deleteStandard: vi.fn(),
  },
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const standard = (id: number) => ({
  id,
  name: `标准 ${id}`,
  standard_type: 'naming',
  description: '',
  rule_config: {},
  is_active: true,
  workspace_id: 0,
  created_at: '2026-11-25 00:00:00',
  updated_at: '2026-11-25 00:00:00',
});

const ALL = Array.from({ length: 25 }, (_, i) => standard(25 - i));

const lastCall = () => {
  const calls = vi.mocked(governanceApi.listStandards).mock.calls;
  return calls[calls.length - 1]?.[0] as any;
};

beforeEach(() => {
  vi.clearAllMocks();
  // 服务端分页桩：page/page_size 切片，total 为全量长度
  vi.mocked(governanceApi.listStandards).mockImplementation(async (params: any) => {
    const size = Number(params?.page_size) || 20;
    const pg = Number(params?.page) || 1;
    const start = (pg - 1) * size;
    return {
      data: { items: ALL.slice(start, start + size), total: ALL.length, page: pg, page_size: size },
    } as never;
  });
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('数据标准清单 — 服务端分页', () => {
  it('请求带 page/page_size，总数来自服务端 total', async () => {
    render(<Standards />);

    expect(await screen.findByText('标准 25')).toBeInTheDocument();
    expect(screen.queryByText('标准 5')).not.toBeInTheDocument();
    expect(screen.getByText(/共\s*25\s*条/)).toBeInTheDocument();
    expect(lastCall()).toMatchObject({ workspace_id: 0, page: 1, page_size: 20 });
  });

  it('翻页带新 page 重新请求并渲染对应页数据', async () => {
    render(<Standards />);
    await screen.findByText('标准 25');

    await userEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(await screen.findByText('标准 5')).toBeInTheDocument();
    expect(screen.queryByText('标准 25')).not.toBeInTheDocument();
    expect(lastCall()).toMatchObject({ page: 2, page_size: 20 });
  });
});