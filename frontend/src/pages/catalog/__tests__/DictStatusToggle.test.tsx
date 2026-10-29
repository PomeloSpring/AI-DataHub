import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MetricsCenter from '../MetricsCenter';
import DimensionsDictTab from '../DimensionsDictTab';
import { metricsApi } from '@/api/metrics';
import { toast } from '@/lib/toast';

/**
 * 口径状态（is_active）在字典页必须可见可改 —— 锁定「已停用行看得见却恢复不了」这一缺陷。
 *
 * 历史缺陷：停用只有一个来源（脚本退役伪指标 / AS-BOT 未发布把行写成 is_active=0），
 * 页面上没有任何修改状态的入口，用户只能改库；维度字典更糟（列表硬过滤 is_active=1，
 * 停用行连看见都做不到）。本用例钉住：
 * 1. 停用行有「启用」按钮，启用行有「停用」按钮，两个方向都要确认（都改变线上取数答案）；
 * 2. 状态变更走部分更新 {is_active}，不得整份覆盖（会抹掉口径/表达式/别名）；
 * 3. 启用确认必须带伪指标警示（现存量停用行多为主动退役的行级派生视图）；
 * 4. 编辑表单回填并显示「口径状态」，停用态说明可见；
 * 5. 维度字典请求带 is_active='all'（停用行可见），编辑弹窗可勾回启用；
 * 6. 写失败显式报错，不伪装成"已提交"。
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
const mockUpdate = vi.mocked(metricsApi.update);
const mockDims = vi.mocked(metricsApi.listAllDimensions);
const mockUpdateDim = vi.mocked(metricsApi.updateDimension);

const FACETS = {
  metrics: {
    total: 2, active: 1, inactive: 1, certified: 0, draft: 2, unbound_objects: 0,
    with_target_table: 2, without_target_table: 0, with_target_column: 0,
    categories: [{ value: '可观测', count: 2 }],
    agg_types: [{ value: 'COUNT', count: 2 }],
    tags: [{ value: '基础', count: 2 }],
  },
  dimensions: {
    total: 2, active: 1, inactive: 1, certified: 0, draft: 2, unbound_objects: 0,
    with_target_table: 2, without_target_table: 0, with_target_column: 2,
    with_value_labels: 0, without_value_labels: 2,
    categories: [{ value: '属性', count: 2 }],
  },
  objects: [{ key: 'rum_event', display_name: 'RUM观测事件', metric_count: 2, dimension_count: 2 }],
};

const metric = (over: Record<string, unknown> = {}) => ({
  id: 1, name: '事件总数', display_name: '事件总数', name_en: 'event_count',
  description: '', category: '可观测', tags: ['基础'], calculation_type: 'COUNT',
  expression: 'COUNT(id)', unit: '个', target_table: 'rum_event',
  target_column: '', granularity: '', owner: '',
  bound_object_key: 'rum_event', aliases: '["异常数"]', certified: 0, is_active: 1,
  created_at: '', updated_at: '', ...over });

const dimension = (over: Record<string, unknown> = {}) => ({
  id: 1, name: '事件类型', name_en: 'event_type', category: '属性', level: 0, hierarchy: '',
  target_table: 'rum_event', target_column: 'type', bound_object_key: 'rum_event',
  aliases: ['类型'], value_labels: null, certified: 0, datasource_id: 0, description: '',
  is_active: 1, ...over });

/** 停用行 = 脚本主动退役的行级派生视图（布尔标记），形状与真指标一样在列表里 */
const RETIRED = metric({
  id: 2, name: '是否活跃', display_name: '是否活跃', name_en: 'is_active_flag',
  expression: 'status = 1', calculation_type: 'MAX', is_active: 0,
});

beforeEach(() => {
  mockList.mockResolvedValue({ data: { total: 2, items: [metric(), RETIRED] } } as never);
  mockFilters.mockResolvedValue({ data: FACETS } as never);
  mockUpdate.mockResolvedValue({ data: { success: true } } as never);
  mockDims.mockResolvedValue({ data: { total: 2, items: [dimension(), dimension({
    id: 2, name: '是否在线', name_en: 'is_online', is_active: 0,
  })] } } as never);
  mockUpdateDim.mockResolvedValue({ data: { success: true } } as never);
  vi.spyOn(toast, 'success').mockReturnValue(0);
  vi.spyOn(toast, 'error').mockReturnValue(0);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });

async function renderMetrics() {
  render(<MetricsCenter embedded />);
  await screen.findByText('共 2 条');
}

const rowOf = async (text: string) => {
  const cell = (await screen.findAllByText(text))[0];
  return within(cell.closest('tr') as HTMLElement);
};

describe('指标字典 — 行内口径状态入口', () => {
  it('停用行显示「启用」，确认后按部分更新写回 is_active=1', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    const row = await rowOf('是否活跃');
    expect(row.getByText('已停用')).toBeTruthy();
    await user.click(row.getByRole('button', { name: /启用/ }));

    const dlg = await screen.findByRole('dialog', { name: /启用口径「是否活跃」/ });
    // 启用会把口径重新广告给 ChatBI，停用行多为主动退役的伪指标 → 必须警示而非一键生效
    expect(within(dlg).getByText(/行级派生视图/)).toBeTruthy();
    await user.click(within(dlg).getByRole('button', { name: '确认启用' }));
    await vi.waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith(2, { is_active: 1 });
    });
    // 只发状态字段：整份覆盖会抹掉表达式/别名（update_metric 按传入字段写）
    expect(mockUpdate.mock.calls.some(([, body]) => Object.keys(body).length !== 1)).toBe(false);
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('已启用「是否活跃」'));
    // 图谱/知识库只收录生效行 → 提醒同步，否则形成"已启用但图里查不到"的隐形不一致
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('同步'));
  });

  it('启用行显示「停用」，确认文案说明只影响语义层命中且不删数据', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    const row = await rowOf('事件总数');
    await user.click(row.getByRole('button', { name: /停用/ }));
    const dlg = await screen.findByRole('dialog', { name: /停用口径「事件总数」/ });
    expect(within(dlg).getByText(/不会删数据/)).toBeTruthy();
    await user.click(within(dlg).getByRole('button', { name: '确认停用' }));
    await vi.waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(1, { is_active: 0 }));
  });

  it('确认弹窗取消不发请求（不是点一下就生效）', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    const row = await rowOf('是否活跃');
    await user.click(row.getByRole('button', { name: /启用/ }));
    const dlg = await screen.findByRole('dialog', { name: /启用口径/ });
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it('写失败透传服务端错因，不伪装成已启用', async () => {
    mockUpdate.mockRejectedValue({ response: { data: { detail: 'is_active 仅支持 1/0' } } } as never);
    await renderMetrics();
    const user = userEvent.setup();
    const row = await rowOf('是否活跃');
    await user.click(row.getByRole('button', { name: /启用/ }));
    const dlg = await screen.findByRole('dialog', { name: /启用口径/ });
    await user.click(within(dlg).getByRole('button', { name: '确认启用' }));
    await vi.waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('is_active 仅支持 1/0'));
    });
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('编辑表单回填「口径状态」为停用并给出停用态说明', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    const row = await rowOf('是否活跃');
    await user.click(row.getByRole('button', { name: '编辑指标' }));
    const dlg = await screen.findByRole('dialog', { name: '编辑指标' });
    const trigger = within(dlg).getByRole('combobox', { name: '口径状态' });
    expect(trigger.textContent).toContain('停用');
    expect(within(dlg).getByText(/当前为停用态/)).toBeTruthy();
    await user.click(trigger);
    expect(await screen.findByRole('option', { name: /启用（参与语义检索与取数）/ })).toBeTruthy();
  });

  it('详情弹窗显示口径状态与恢复说明', async () => {
    await renderMetrics();
    const user = userEvent.setup();
    await user.click((await screen.findAllByText('是否活跃'))[0]);
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText(/已停用（不参与语义检索与取数）/)).toBeTruthy();
  });
});

describe('维度字典 — 停用行可见可恢复', () => {
  it('列表请求带 is_active=all，停用维度带徽章显示', async () => {
    render(<DimensionsDictTab facets={FACETS} />);
    expect(await screen.findByText('是否在线')).toBeTruthy();
    expect(screen.getByText('已停用')).toBeTruthy();
    expect(mockDims).toHaveBeenCalledWith(expect.objectContaining({ is_active: 'all' }));
  });

  it('编辑停用维度：启用勾选默认未选中，勾上后保存写 is_active=1', async () => {
    render(<DimensionsDictTab facets={FACETS} />);
    await screen.findByText('是否在线');
    const user = userEvent.setup();
    const row = await rowOf('是否在线');
    // 维度行只有图标钮（无名字），取首个 = 编辑
    await user.click(row.getAllByRole('button')[0]);
    const dlg = await screen.findByRole('dialog', { name: /编辑维度 — 是否在线/ });
    const box = within(dlg).getByRole('checkbox', { name: /启用（参与语义检索与取数）/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    expect(within(dlg).getByText(/当前为停用态/)).toBeTruthy();
    await user.click(box);
    await user.click(within(dlg).getByRole('button', { name: '保存' }));
    await vi.waitFor(() => {
      expect(mockUpdateDim).toHaveBeenCalledWith(2, expect.objectContaining({ is_active: 1 }));
    });
  });

  it('口径状态筛选可只看已停用', async () => {
    render(<DimensionsDictTab facets={FACETS} />);
    await screen.findByText('事件类型');
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '口径状态' }));
    await user.click(await screen.findByRole('option', { name: '已停用（1）' }));
    expect(await screen.findByText('共 1 / 2 条')).toBeTruthy();
    expect(screen.queryByText('事件类型')).toBeNull();
  });
});
