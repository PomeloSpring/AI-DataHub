/** 取数未完成（含数据源并发排队超限）必须在图表卡片上留下可见标记。

背景：数据源现在按 `extra_config.max_concurrency` 限并发，满员先排队、排不到就**显式拒绝**
（`backend/core/query_gate.py`）。拒绝文案是"排队中，请稍后重试 + 可操作下一步"，
但如果前端只在 toast 里一闪而过，用户看到的仍是"图没变"——那等于把功能失败
伪装成"没数据"（no-silent-degradation）。这里锁四件事（池本身的行为由 chartQueryPool 用例覆盖，
本文件只验 store 对三种失败结果的处置）：
1. 服务端 400 的 `detail`（容量排队原文）必须进 `paramDiagnostics[id].notice` 并出现在 toast 里；
2. 下一次取数成功后标记自动清除（不能挂着旧告警像"还没生效"）；
3. 前端超时也要给标记，且提示里说清"可能在排队"；
4. 被拒的图不得写取数覆盖层：留旧数据 + 标记才诚实，抹成空图会被读成"数据没了"。
*/
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useDashboardStore, type Dashboard, type DashboardChart } from '../dashboardStore';
import { chartQueryPool } from '@/lib/chartQueryPool';
import { toast } from '@/lib/toast';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
// 池按契约把任务结果归一成 ChartQueryResult；这里用等价实现，避免依赖真实排队/定时器
vi.mock('@/lib/chartQueryPool', () => ({
  chartQueryPool: {
    cancelAll: vi.fn(),
    run: vi.fn(async (task: (signal: AbortSignal) => Promise<any>) => {
      try {
        return { ok: true, value: await task(new AbortController().signal) };
      } catch (error) {
        return { ok: false, reason: 'error', error };
      }
    }),
  },
}));

const QUEUE_MSG = '数据源「prd-cn-mysql」当前查询较多：并发上限 8 已占满，排队 10.0 秒仍未轮到。'
  + '请稍后重试；也可以缩小时间范围、减少分组基数，或减少同时刷新的图表数。';

function makeChart(overrides: Partial<DashboardChart> = {}): DashboardChart {
  return {
    id: 1, dashboard_id: 10, name: '订单量趋势', chart_type: 'bar', sql_query: '',
    config: {}, position: { x: 0, y: 0, w: 400, h: 300 },
    source_type: 'dataset', source_id: 5, data_cache: null,
    created_at: '', updated_at: '', ...overrides,
  } as DashboardChart;
}

function mount(chart: DashboardChart) {
  const dashboard = {
    id: 10, name: 'D', description: '', layout: [], filters: {}, params: [], page_params: [],
    tags: [], status: 'published', owner_id: 1, is_public: false, is_default: false,
    carousel_interval: 0, sort_order: 0, charts: [chart], created_at: '', updated_at: '',
  } as unknown as Dashboard;
  useDashboardStore.setState({ dashboards: [dashboard], currentId: 10, chartCache: {}, paramDiagnostics: {} });
}

const noticeOf = (chartId: number) => useDashboardStore.getState().paramDiagnostics[chartId]?.notice;
const toasts = () => vi.mocked(toast.error).mock.calls.flat().join(' ');

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('取数未完成标记（容量排队/超时）', () => {
  it('并发排队被拒：服务端原文进卡片标记，toast 也带上原因，且不覆盖旧数据', async () => {
    mount(makeChart());
    vi.mocked(client.post).mockRejectedValue(Object.assign(
      new Error('Request failed with status code 400'),
      { response: { data: { detail: QUEUE_MSG } } },
    ));

    const ok = await useDashboardStore.getState().refreshSingleChart(1, { chartName: '订单量趋势' });
    expect(ok).toBe(false);
    expect(noticeOf(1)).toBe(QUEUE_MSG);
    expect(toasts()).toContain('并发上限 8 已占满');
    expect(useDashboardStore.getState().chartCache[1]).toBeUndefined();
  });

  it('取数成功后标记自动清除', async () => {
    mount(makeChart());
    vi.mocked(client.post).mockRejectedValue(Object.assign(
      new Error('boom'), { response: { data: { detail: QUEUE_MSG } } },
    ));
    await useDashboardStore.getState().refreshSingleChart(1);
    expect(noticeOf(1)).toBeTruthy();

    vi.mocked(client.post).mockResolvedValue({ data: { columns: ['a'], rows: [{ a: 1 }] } } as any);
    await useDashboardStore.getState().refreshSingleChart(1);
    expect(noticeOf(1)).toBeUndefined();
    expect(useDashboardStore.getState().chartCache[1]).toBeTruthy();
  });

  it('前端超时：标记里说明"可能在排队"，不只说"刷新失败"', async () => {
    mount(makeChart());
    vi.mocked(chartQueryPool.run).mockResolvedValueOnce({
      ok: false, reason: 'timeout', seconds: 75,
    } as any);

    expect(await useDashboardStore.getState().refreshSingleChart(1)).toBe(false);
    expect(noticeOf(1)).toContain('75 秒未返回，已取消');
    expect(noticeOf(1)).toContain('排队');
    expect(noticeOf(1)).toContain('缩小时间范围');
  });

  it('无 detail 的网络故障：标记与 toast 都给"稍后重试"，不留空白', async () => {
    mount(makeChart());
    vi.mocked(client.post).mockRejectedValue(new Error('Network Error'));
    await expect(useDashboardStore.getState().refreshSingleChart(1)).resolves.toBe(false);
    expect(noticeOf(1)).toContain('请稍后重试');
    expect(toasts()).toContain('请稍后重试');
  });
});
