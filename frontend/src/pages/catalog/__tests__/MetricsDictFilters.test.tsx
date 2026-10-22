import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MetricsCenter from '../MetricsCenter';
import DimensionsDictTab from '../DimensionsDictTab';
import { metricsApi } from '@/api/metrics';

/**
 * 指标字典 / 维度字典筛选回归。
 *
 * 钉住四件事：
 * 1. 筛选选项来自后端 facet（真实值域），不是前端写死枚举 —— 历史缺陷是写死
 *    basic/derived/composite 当 category 值域，与库内 可观测/客户/案例… 无交集，
 *    选任何一项都查空（能点但永远无结果的死筛选）；
 * 2. 指标列表服务端分页 → 筛选必须带进请求参数（不得前端筛当前页）；
 * 3. 维度列表不分页 → 筛选在全量集上精确生效；
 * 4. facet/列表加载失败必须显式可见（不静默降级成"没有这类值"/"暂无数据"），
 *    归属对象显示业务名而非裸 key（ui-resource-display）。
 */

vi.mock('@/api/metrics', () => ({
  metricsApi: {
    list: vi.fn(), filters: vi.fn(), listAllDimensions: vi.fn(),
    getDimensions: vi.fn(), addDimension: vi.fn(), updateDimension: vi.fn(),
    create: vi.fn(), update: vi.fn(), delete: vi.fn(), deleteDimension: vi.fn(),
    assetPool: vi.fn(),
  },
}));

const mockList = vi.mocked(metricsApi.list);
const mockFilters = vi.mocked(metricsApi.filters);
const mockDims = vi.mocked(metricsApi.listAllDimensions);

const FACETS = {
  metrics: {
    total: 36, active: 34, inactive: 2, certified: 1, draft: 35, unbound_objects: 3,
    with_target_table: 30, without_target_table: 6, with_target_column: 9,
    categories: [{ value: '可观测', count: 14 }, { value: '__unset__', count: 22 }],
    agg_types: [{ value: 'COUNT', count: 34 }, { value: 'COUNT_DISTINCT', count: 2 }],
  },
  dimensions: {
    total: 3, active: 3, inactive: 0, certified: 1, draft: 2, unbound_objects: 1,
    with_target_table: 2, without_target_table: 1, with_target_column: 2,
    with_value_labels: 1, without_value_labels: 2,
    categories: [{ value: '属性', count: 1 }],
  },
  objects: [
    { key: 'rum_event', display_name: 'RUM观测事件', metric_count: 14, dimension_count: 1 },
    { key: 'case', display_name: '', metric_count: 6, dimension_count: 1 },
  ],
};

const metric = (over: Record<string, unknown> = {}) => ({
  id: 1, name: '事件总数', display_name: '事件总数', name_en: 'event_count',
  description: '', metric_type: '可观测', calculation_type: 'COUNT',
  expression: 'COUNT(pc_case.id)', unit: '个', target_table: 'rum_event',
  target_column: '', granularity: '', owner: '', tags: [],
  bound_object_key: 'rum_event', aliases: '["异常数"]', certified: 0, is_active: 1,
  created_at: '', updated_at: '', ...over,
});

const dimension = (over: Record<string, unknown> = {}) => ({
  id: 1, name: '事件类型', name_en: 'event_type', category: '属性', level: 0, hierarchy: '',
  target_table: 'rum_event', target_column: 'type', bound_object_key: 'rum_event',
  aliases: ['类型'], value_labels: null, certified: 0, datasource_id: 0, description: '',
  ...over,
});

const DIMENSIONS = [
  dimension(),
  dimension({
    id: 2, name: '案例状态', name_en: 'case_status', category: '状态',
    bound_object_key: 'case', value_labels: { 0: '待处理' }, certified: 1,
  }),
  dimension({
    id: 3, name: '创建日期', name_en: 'create_date', category: '时间',
    bound_object_key: '', target_table: '', target_column: '',
  }),
];

beforeEach(() => {
  mockList.mockResolvedValue({ data: { total: 36, items: [metric(), metric({
    id: 2, name: '退号数', display_name: '退号数',
    bound_object_key: 'case', is_active: 0, calculation_type: 'COUNT_DISTINCT', aliases: '[]',
  })] } } as never);
  mockFilters.mockResolvedValue({ data: FACETS } as never);
  mockDims.mockResolvedValue({ data: { total: DIMENSIONS.length, items: DIMENSIONS } } as never);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

/** 渲染指标字典并等数据落地（筛选栏的“共 N 条”由列表响应驱动，适合当就绪信号） */
async function renderMetrics(embedded = false, modelId?: number) {
  render(<MetricsCenter embedded={embedded} modelId={modelId} />);
  await screen.findByText('共 36 条');
}

describe('指标字典筛选 — 选项来自 facet', () => {
  it('分类下拉渲染库内真实值域与条数（不是写死的三档）', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '指标分类' }));
    expect(await screen.findByRole('option', { name: '可观测（14）' })).toBeTruthy();
    // 空值桶显式可选（库里 22 条没有分类，不能说"没有这个值"）
    expect(screen.getByRole('option', { name: '未分类（22）' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: '基础指标' })).toBeNull();
  });

  it('选择分类/计算方式后把筛选下推请求参数并回到第 1 页', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '指标分类' }));
    await user.click(await screen.findByRole('option', { name: '可观测（14）' }));
    await vi.waitFor(() => {
      const last = mockList.mock.calls[mockList.mock.calls.length - 1][0];
      expect(last).toMatchObject({ category: '可观测', page: 1 });
    });
    await user.click(screen.getByRole('combobox', { name: '计算方式' }));
    await user.click(await screen.findByRole('option', { name: '去重计数（2）' }));
    await vi.waitFor(() => {
      const last = mockList.mock.calls[mockList.mock.calls.length - 1][0];
      expect(last).toMatchObject({ category: '可观测', agg_type: 'COUNT_DISTINCT' });
    });
  });

  it('归属对象从清单中选（显示业务名），选中后按 key 过滤', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '归属对象' }));
    expect(await screen.findByRole('option', { name: /RUM观测事件/ })).toBeTruthy();
    await user.click(screen.getByRole('option', { name: /RUM观测事件/ }));
    await vi.waitFor(() => {
      const last = mockList.mock.calls[mockList.mock.calls.length - 1][0];
      expect(last).toMatchObject({ bound_object_key: 'rum_event' });
    });
  });

  it('认证/口径状态/物理表按 facet 计数提供选项', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '口径状态' }));
    expect(await screen.findByRole('option', { name: '已停用（2）' })).toBeTruthy();
    await user.click(screen.getByRole('option', { name: '已停用（2）' }));
    await vi.waitFor(() => {
      const last = mockList.mock.calls[mockList.mock.calls.length - 1][0];
      expect(last).toMatchObject({ is_active: '0' });
    });
  });

  it('有筛选时显示清除筛选，点击后请求回到无过滤', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '认证状态' }));
    await user.click(await screen.findByRole('option', { name: '草稿（35）' }));
    const clear = await screen.findByRole('button', { name: '清除筛选' });
    await user.click(clear);
    await vi.waitFor(() => {
      const last = mockList.mock.calls[mockList.mock.calls.length - 1][0];
      expect(last && !('certified' in last)).toBeTruthy();
    });
  });

  it('归属对象列显示业务名而非裸 key，未认证/已停用可见', async () => {
    await renderMetrics();
    const nameCell = await screen.findByText('RUM观测事件');
    // key 只作次要信息(title)供排查, 不作为主展示文本
    expect(nameCell.getAttribute('title')).toBe('rum_event');
    expect(screen.getByText('已停用')).toBeTruthy();
    expect(screen.getByText('case')).toBeTruthy();
  });

  it('词表加载失败显式提示 + 下拉禁用（不伪装成"字典里没这类值"）', async () => {
    mockFilters.mockRejectedValue({ response: { data: { detail: '数据库不可用' } } } as never);
    await renderMetrics();
    expect(await screen.findByText(/筛选项加载失败：数据库不可用/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: '指标分类' })).toBeDisabled();
    // 关键词搜索仍可用（不是整页瘫掉）
    expect(screen.getByPlaceholderText('搜索名称/英文名/别名/口径...')).toBeEnabled();
  });

  it('列表加载失败与"暂无数据"可区分', async () => {
    mockList.mockRejectedValue({ response: { data: { detail: '502 Bad Gateway' } } } as never);
    render(<MetricsCenter />);
    expect(await screen.findByText(/指标字典加载失败：502 Bad Gateway/)).toBeTruthy();
    expect(screen.queryByText('暂无指标数据')).toBeNull();
  });

  it('嵌入模型工作区时词表同作用域请求', async () => {
    await renderMetrics(true, 7);
    await vi.waitFor(() => {
      expect(mockFilters).toHaveBeenCalledWith({ model_id: 7, scope: 'model' });
    });
  });
});

describe('维度字典筛选 — 全量集上前端精确过滤', () => {
  it('分类下拉来自已加载的真实值域，选中后计数与行都收敛', async () => {
    render(<DimensionsDictTab facets={FACETS} />);
    await screen.findByText('事件类型');
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '维度分类' }));
    expect(await screen.findByRole('option', { name: '属性（1）' })).toBeTruthy();
    expect(screen.getByRole('option', { name: '状态（1）' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: '未分类（0）' })).toBeNull();
    await user.click(screen.getByRole('option', { name: '状态（1）' }));
    expect(await screen.findByText('共 1 / 3 条')).toBeTruthy();
    expect(screen.queryByText('事件类型')).toBeNull();
  });

  it('认证/枚举/物理列绑定/归属对象四个维度可筛', async () => {
    render(<DimensionsDictTab facets={FACETS} />);
    await screen.findByText('事件类型');
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '认证状态' }));
    await user.click(await screen.findByRole('option', { name: '已认证（1）' }));
    expect(await screen.findByText('案例状态')).toBeTruthy();
    expect(screen.queryByText('事件类型')).toBeNull();

    await user.click(screen.getByRole('button', { name: '清除筛选' }));
    await user.click(screen.getByRole('combobox', { name: '枚举标签' }));
    await user.click(await screen.findByRole('option', { name: '已有码值（1）' }));
    expect(await screen.findByText('共 1 / 3 条')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: '清除筛选' }));
    await user.click(screen.getByRole('combobox', { name: '物理列绑定' }));
    await user.click(await screen.findByRole('option', { name: '未绑定（1）' }));
    expect(await screen.findByText('创建日期')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: '清除筛选' }));
    await user.click(screen.getByRole('combobox', { name: '归属对象' }));
    await user.click(await screen.findByRole('option', { name: /未归属/ }));
    expect(await screen.findByText('共 1 / 3 条')).toBeTruthy();
  });

  it('绑定对象列显示业务名，名字取不到时回退 key（不编造也不显裸 id）', async () => {
    render(<DimensionsDictTab facets={FACETS} />);
    await screen.findByText('事件类型');
    expect(await screen.findByText('RUM观测事件')).toBeTruthy();
    expect(screen.getByText('case')).toBeTruthy();
    expect(screen.getByText('未归属')).toBeTruthy();
  });

  it('搜索覆盖别名与枚举业务名', async () => {
    render(<DimensionsDictTab facets={FACETS} />);
    await screen.findByText('事件类型');
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('搜索名称/英文名/别名/枚举/对象...'), '待处理');
    expect(await screen.findByText('共 1 / 3 条')).toBeTruthy();
  });

  it('加载失败显式报错，不当成"暂无维度数据"', async () => {
    mockDims.mockRejectedValue({ response: { data: { detail: '连接超时' } } } as never);
    render(<DimensionsDictTab />);
    expect(await screen.findByText(/维度字典加载失败：连接超时/)).toBeTruthy();
    expect(screen.queryByText('暂无维度数据')).toBeNull();
  });
});
