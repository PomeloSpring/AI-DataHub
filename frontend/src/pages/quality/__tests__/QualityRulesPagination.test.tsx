/**
 * 质量规则清单服务端**真分页**回归（.qoder/rules/list-pagination-server-side.md）：
 * - 分页条总数渲染自服务端 total（不是当前页长度），翻页带 page/page_size 重新请求；
 * - 搜索防抖后带 q 重新请求并回第 1 页（不在前端对全量 filter+slice）；
 * - 末页删空时收敛到前一个有效页，不停在空白页。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import QualityRules from '../QualityRules';
import { qualityApi } from '@/api/quality';

vi.mock('@/api/quality', () => ({
  qualityApi: {
    getRules: vi.fn(),
    createRule: vi.fn(),
    updateRule: vi.fn(),
    deleteRule: vi.fn(),
    executeRule: vi.fn(),
  },
}));

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(async () => ({ data: [] })) },
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const rule = (id: number) => ({
  id,
  name: `质量规则 ${id}`,
  description: '',
  rule_type: 'not_null',
  target_table: 'orders',
  target_column: 'id',
  target_datasource_id: 1,
  severity: 'medium',
  is_active: true,
  rule_config: {},
  last_check_at: null,
  last_check_status: null,
  workspace_id: 0,
  created_at: '2026-11-25 00:00:00',
  updated_at: '2026-11-25 00:00:00',
});

let allRules: ReturnType<typeof rule>[] = [];

/** 服务端分页桩：q 模糊匹配 + page/page_size 切片；total = 过滤后全量长度 */
const stubGetRules = () => {
  vi.mocked(qualityApi.getRules).mockImplementation(async (params: any) => {
    let list = allRules;
    if (params?.q) list = list.filter(r => r.name.includes(String(params.q)));
    const size = Number(params?.page_size) || 20;
    const pg = Number(params?.page) || 1;
    const start = (pg - 1) * size;
    return {
      data: { items: list.slice(start, start + size), total: list.length, page: pg, page_size: size },
    } as never;
  });
};

const lastCall = () => {
  const calls = vi.mocked(qualityApi.getRules).mock.calls;
  return calls[calls.length - 1]?.[0] as any;
};

beforeEach(() => {
  vi.clearAllMocks();
  // id DESC：25..1
  allRules = Array.from({ length: 25 }, (_, i) => rule(25 - i));
  stubGetRules();
  vi.mocked(qualityApi.deleteRule).mockResolvedValue({ data: { message: 'ok' } } as never);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('质量规则清单 — 服务端分页', () => {
  it('总数渲染自服务端 total，翻页带 page/page_size 重新请求', async () => {
    render(<QualityRules />);

    // 首页只列当前页 20 条，分页条显示服务端总数
    expect(await screen.findByText('质量规则 25')).toBeInTheDocument();
    expect(screen.queryByText('质量规则 5')).not.toBeInTheDocument();
    expect(screen.getByText(/共\s*25\s*条/)).toBeInTheDocument();
    expect(lastCall()).toMatchObject({ page: 1, page_size: 20 });

    await userEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(await screen.findByText('质量规则 5')).toBeInTheDocument();
    expect(screen.queryByText('质量规则 25')).not.toBeInTheDocument();
    expect(lastCall()).toMatchObject({ page: 2, page_size: 20 });
  });

  it('搜索防抖后带 q 重新请求并回到第 1 页', async () => {
    render(<QualityRules />);
    await screen.findByText('质量规则 25');

    // 先翻到第 2 页，证明搜索会把页码拉回 1
    await userEvent.click(screen.getByRole('button', { name: '下一页' }));
    await screen.findByText('质量规则 5');
    expect(lastCall().page).toBe(2);

    await userEvent.type(screen.getByPlaceholderText('搜索规则名称...'), '规则 2');

    await waitFor(() => expect(lastCall()?.q).toBe('规则 2'));
    expect(lastCall()).toMatchObject({ page: 1, q: '规则 2' });
    // 服务端过滤后的总数驱动分页条（25 → 规则 2/20~25 共 7 条）
    expect(await screen.findByText(/共\s*7\s*条/)).toBeInTheDocument();
  });

  it('末页删空收敛到前一个有效页，不停在空白页', async () => {
    allRules = Array.from({ length: 21 }, (_, i) => rule(21 - i));
    render(<QualityRules />);
    await screen.findByText('质量规则 21');

    await userEvent.click(screen.getByRole('button', { name: '下一页' }));
    await screen.findByText('质量规则 1');
    expect(lastCall()).toMatchObject({ page: 2 });

    // 第 2 页只剩 1 条：删除后应回第 1 页重新取数（fireEvent 同步派发，避开重渲染换节点的点击竞态）
    fireEvent.click(screen.getByTitle('删除'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }));

    await waitFor(() => expect(lastCall()?.page).toBe(1));
    expect(await screen.findByText('质量规则 20')).toBeInTheDocument();
  });
});