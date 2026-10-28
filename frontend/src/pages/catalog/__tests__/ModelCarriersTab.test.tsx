import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ModelCarriersTab } from '../ModelAssetTabs';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockGet = vi.mocked(client.get);
const mockPut = vi.mocked(client.put);

/**
 * 「取数路径（承载）」页签契约（跨源拓扑 M1）。
 *
 * 钉三件事：
 * 1. 显示一律业务名（承载名/数据源名/源本体名/站点名/类型与新鲜度中文），
 *    不得把英文枚举码或裸 id 当主要展示内容（ui-resource-display）；
 * 2. 空清单必须显式引导（不声明就没有全局口径），不得渲染成"看起来没有这页";
 * 3. 保存走整模型替换的 PUT，且无变更时不给保存（防误提交把既有路径整批置废弃）。
 */
const payload = {
  data: {
    model_id: 1,
    types: ['detail', 'global', 'log', 'mirror'],
    freshness_levels: ['realtime', 't_plus_1', 'lagged'],
    objects: [{ key: 'user', name: '用户' }, { key: 'case', name: '案例' }],
    datasources: [{ name: 'Doris', db_type: 'doris', site_code: '' },
                  { name: 'prd-cn', db_type: 'mysql', site_code: 'cn' }],
    sites: [{ site_code: 'cn', display_name: '中国站点' }, { site_code: 'jp', display_name: '日本站点' }],
    source_ontologies: [{ name: '数据仓库模型', datasource: 'Doris',
                          objects: [{ key: 'user_registration_stat', name: '用户注册统计' }] }],
    items: [
      {
        id: 1, model_id: 1, object_key: 'user', object_name: '用户', carrier_key: 'global',
        display_name: '数仓全局汇总', carrier_type: 'global', datasource: 'Doris', sites: [],
        source_model: '数据仓库模型', source_object: 'user_registration_stat', grain: '用户级',
        freshness: 't_plus_1', as_of_note: 'T+1，不含今天', is_authority: 1, priority: 10,
        size_class: '', status: 'active', updated_at: '2026-10-28 01:00:00',
      },
      {
        id: 2, model_id: 1, object_key: 'user', object_name: '用户', carrier_key: 'detail',
        display_name: '各站点明细', carrier_type: 'detail', datasource: '', sites: ['cn', 'jp'],
        source_model: '', source_object: '', grain: '', freshness: 'realtime', as_of_note: '',
        is_authority: 0, priority: 50, size_class: '', status: 'deprecated',
        updated_at: '2026-10-28 01:00:00',
      },
    ],
  },
};

async function renderTab(readonly?: boolean) {
  mockGet.mockResolvedValue(payload as never);
  render(<ModelCarriersTab modelId={1} readonly={readonly} />);
  expect(await screen.findByText('数仓全局汇总')).toBeTruthy();
}

afterEach(() => { cleanup(); mockGet.mockReset(); mockPut.mockReset(); });

describe('ModelCarriersTab — 承载清单显示', () => {
  it('显示业务名与中文枚举，不露裸 id / 英文码', async () => {
    await renderTab();
    // 归属对象显示业务名（不裸显 object_key）
    expect(screen.getByText('用户')).toBeTruthy();
    expect(screen.queryByText(/^user$/)).toBeNull();
    // 类型/新鲜度中文化
    expect(screen.getByText('全局汇总（数仓）')).toBeTruthy();
    expect(screen.getByText(/T\+1（不含今天）/)).toBeTruthy();
    expect(screen.queryByText('t_plus_1')).toBeNull();
    // 路由信息：数据源 + 源本体/源对象 + 时效说明（文案被内联标签拆成多个节点，分开断言）
    expect(screen.getByText('Doris')).toBeTruthy();
    expect(screen.getByText('数据仓库模型')).toBeTruthy();
    expect(screen.getByText(/user_registration_stat/)).toBeTruthy();
    expect(screen.getByText(/T\+1，不含今天/)).toBeTruthy();
    expect(screen.getByText('权威口径')).toBeTruthy();
    expect(screen.getByText('已废弃')).toBeTruthy();
    // 裸内部 id 不得出现在页面上
    expect(screen.queryByText('model_id')).toBeNull();
  });

  it('空清单显式引导"不声明就没有全局口径"（不静默空白）', async () => {
    mockGet.mockResolvedValue({ data: { ...payload.data, items: [] } } as never);
    render(<ModelCarriersTab modelId={1} />);
    expect(await screen.findByText(/尚未声明任何取数路径/)).toBeTruthy();
    expect(screen.getByText(/必须先在数仓建对象/)).toBeTruthy();
  });

  it('只读态不出现新增/保存入口', async () => {
    await renderTab(true);
    expect(screen.queryByText('新增取数路径')).toBeNull();
    expect(screen.queryByText('保存取数路径')).toBeNull();
  });
});

describe('ModelCarriersTab — 保存契约', () => {
  it('无变更时保存按钮禁用；删除一条后放开并按整模型替换提交', async () => {
    await renderTab();
    const save = screen.getByRole('button', { name: '保存取数路径' });
    expect((save as HTMLButtonElement).disabled).toBe(true);

    // 两条承载各有一个移除按钮：移除第二条（detail），剩下 global
    const removeButtons = screen.getAllByTitle(/从清单移除/);
    expect(removeButtons).toHaveLength(2);
    await userEvent.click(removeButtons[1]);
    expect((save as HTMLButtonElement).disabled).toBe(false);

    mockPut.mockResolvedValue({ data: { model_id: 1, saved: 1, carriers: [] } } as never);
    await userEvent.click(save);
    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    const [url, body] = mockPut.mock.calls[0] as [string, { carriers: any[] }];
    expect(url).toBe('/catalog/ontology/models/1/carriers');
    // 整模型替换：提交的是"剩下的全部"，被移除的那条不在其中（服务端会置废弃）
    expect(body.carriers.map((c) => c.carrier_key)).toEqual(['global']);
  });

  it('新增时未选对象/未填标识则确定按钮不可用（防半成品落库）', async () => {
    await renderTab();
    await userEvent.click(screen.getByText('新增取数路径'));
    const confirm = await screen.findByRole('button', { name: /^确定/ });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    expect((confirm as HTMLButtonElement).textContent).toContain('请选择归属对象');
  });
});
