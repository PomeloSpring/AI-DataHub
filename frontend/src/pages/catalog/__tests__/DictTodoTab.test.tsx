import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DictTodoTab from '../DictTodoTab';
import { metricsApi } from '@/api/metrics';
import { usePermissionStore } from '@/stores/permissionStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/api/metrics', () => ({
  metricsApi: {
    assetPool: vi.fn(),
    filters: vi.fn(),
    update: vi.fn(async () => ({ data: { success: true } })),
    updateDimension: vi.fn(async () => ({ data: { success: true } })),
    delete: vi.fn(async () => ({ data: { success: true } })),
    deleteDimension: vi.fn(async () => ({ data: { success: true } })),
  },
}));

/**
 * 字典待办（进化中心第 3 个页签，2026-11-21 由「建模待办」收敛而来）。
 *
 * 锁四件事：
 * 1. **候选不在本页签**：`/metrics/asset-pool` 的 suggestions 归进化中心「待建」「核绑定」，
 *    两处各留一套候选出口正是本次合并要消除的重复（漂移源）；
 * 2. 一待办一出口，行显示业务名与"为什么在待办"，不裸显 id；
 * 3. 出口成功后重新拉清单，处理掉的行消失（历史缺陷：处理完不刷新，像没反应）；
 * 4. 接口失败 → 红条 + 重试，绝不渲染成"没有待办缺口"（降级掩盖）。
 */

const OBJ_FACETS = [
  { key: 'order_item', display_name: '订单明细', metric_count: 3, dimension_count: 1 },
  { key: 'customer', display_name: '客户', metric_count: 1, dimension_count: 0 },
];

let pool: any;
const dropMetric = (id: number) => { pool.metrics = pool.metrics.filter((r: any) => r.id !== id); };
const dropDimension = (id: number) => { pool.dimensions = pool.dimensions.filter((r: any) => r.id !== id); };

function resetPool() {
  pool = {
    metrics: [
      { id: 401, name: '近30天成交金额', name_en: 'gmv_30d', agg_type: 'SUM', pool_reason: 'unbound', description: '含退款口径' },
      { id: 402, name: '失效指标', bound_object_key: 'gone_obj', pool_reason: 'orphan' },
    ],
    dimensions: [{ id: 501, name: '渠道分布', category: '属性', pool_reason: 'unbound' }],
    terms: [{ id: 601, term_cn: '妥投率', term_en: 'delivery_rate', term_type: '比率', description: '签收量/发出量' }],
    // 候选：归进化中心「待建/核绑定」，本页签必须忽略
    suggestions: [
      { id: 701, term: '流水', kind: 'alias', source: 'parse_fail', hit_count: 5 },
      { id: 702, term: '退单率', kind: 'new_metric', source: 'feedback', hit_count: 3 },
    ],
    counts: {},
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetPool();
  usePermissionStore.setState({ perms: null, allowedMenus: null, unrestricted: true, loaded: false });

  vi.mocked(metricsApi.assetPool).mockImplementation(async () => ({ data: JSON.parse(JSON.stringify(pool)) } as any));
  vi.mocked(metricsApi.filters).mockResolvedValue({ data: { objects: OBJ_FACETS } } as any);
  vi.mocked(metricsApi.update).mockImplementation(async (id: number) => { dropMetric(id); return { data: { success: true } } as any; });
  vi.mocked(metricsApi.delete).mockImplementation(async (id: number) => { dropMetric(id); return { data: { success: true } } as any; });
  vi.mocked(metricsApi.updateDimension).mockImplementation(async (id: number) => { dropDimension(id); return { data: { success: true } } as any; });
});

afterEach(() => cleanup());

const renderPage = () => render(
  <MemoryRouter initialEntries={['/data/ontology']}>
    <DictTodoTab />
  </MemoryRouter>,
);

/** 取某条待办的整行容器（行内出口按钮只在 within(row) 里查，避免多行同名词撞车） */
function rowOf(title: string): HTMLElement {
  const el = screen.getByText(title).closest('.rounded-lg');
  if (!(el instanceof HTMLElement)) throw new Error(`待办行未找到：${title}`);
  return el;
}

describe('字典待办', () => {
  it('只列字典缺口：三队列计数 + 业务名与原因，候选不出现在本页签', async () => {
    renderPage();
    expect(await screen.findByText('近30天成交金额')).toBeTruthy();

    // 左栏按类型分桶（候选的三类不在其中）
    expect(within(screen.getByRole('button', { name: /指标缺归属/ })).getByText('2')).toBeTruthy();
    expect(within(screen.getByRole('button', { name: /维度/ })).getByText('1')).toBeTruthy();
    expect(screen.getByRole('button', { name: /术语未映射/ })).toBeTruthy();

    // 行内容：未归属 / 孤儿 的原因徽章可辨
    expect(within(rowOf('近30天成交金额')).getByText('未绑定本体对象')).toBeTruthy();
    expect(within(rowOf('失效指标')).getByText(/指向的对象已失效（gone_obj）/)).toBeTruthy();

    // 候选（流水/退单率）不在这里：它们的处置只归进化中心「待建」「核绑定」
    expect(screen.queryByText('流水')).toBeNull();
    expect(screen.queryByText('退单率')).toBeNull();
  });

  it('单条归属：选对象提交后重新拉清单，该行消失', async () => {
    renderPage();
    const row = await screen.findByText('近30天成交金额');
    await userEvent.click(within(rowOf('近30天成交金额')).getByRole('button', { name: '归属到对象' }));

    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /订单明细/ }));
    await userEvent.click(within(dialog).getByRole('button', { name: '确定' }));

    await waitFor(() => expect(metricsApi.update).toHaveBeenCalledWith(401, { bound_object_key: 'order_item' }));
    await waitFor(() => expect(screen.queryByText('近30天成交金额')).toBeNull());
    expect(row).toBeTruthy();
  });

  it('勾选多条 → 批量归属：指标与维度各走各的写入口', async () => {
    renderPage();
    await screen.findByText('近30天成交金额');
    const boxes = screen.getAllByRole('checkbox');
    expect(boxes.length).toBe(3);   // 只有指标/维度行可勾选
    await userEvent.click(within(rowOf('近30天成交金额')).getByRole('checkbox'));
    await userEvent.click(within(rowOf('渠道分布')).getByRole('checkbox'));

    expect(await screen.findByText(/已选 2 项未归属资产/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: '批量归属到对象' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/本次处理 2 项/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole('button', { name: /订单明细/ }));
    await userEvent.click(within(dialog).getByRole('button', { name: '确定' }));

    await waitFor(() => expect(metricsApi.update).toHaveBeenCalled());
    await waitFor(() => expect(metricsApi.updateDimension).toHaveBeenCalled());
  });

  it('删除孤儿指标：二次确认后行消失', async () => {
    renderPage();
    await screen.findByText('失效指标');
    await userEvent.click(within(rowOf('失效指标')).getByRole('button', { name: /删除字典行/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /删除/ }));

    await waitFor(() => expect(metricsApi.delete).toHaveBeenCalledWith(402));
    await waitFor(() => expect(screen.queryByText('失效指标')).toBeNull());
  });

  it('清单加载失败 → 显式红条 + 重试，不伪装成"待办已清空"', async () => {
    vi.mocked(metricsApi.assetPool).mockRejectedValueOnce({
      response: { data: { detail: '数据库连接失败' } },
    });
    renderPage();
    expect(await screen.findByText('数据库连接失败')).toBeTruthy();
    // 失败时不得出现绿色空态
    expect(screen.queryByText('语义层没有待办缺口')).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: /重试/ }));
    await waitFor(() => expect(screen.getByText('近30天成交金额')).toBeTruthy());
  });
});