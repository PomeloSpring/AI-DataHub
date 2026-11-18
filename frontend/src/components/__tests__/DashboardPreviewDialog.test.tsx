/**
 * 会话内「预览大屏」弹窗的取数口径（2026-11-18 反馈）：
 *
 * 旧行为是**每次打开都逐图重查**（GET 只回 data_cache，代码又主动 POST 一遍全部图），
 * 于是同一块板被反复压到数据源上、打开还要等一轮；用户要的是"看板已经有缓存，预览先用缓存，
 * 要最新数字我自己点刷新"。锁定三件事：
 *  1. 带缓存的图**不发起**重查（打开即出图）；
 *  2. 从来没有缓存的图仍补查一次（发布期/老数据 cache 为空，不补就留白被当成"没数据"）；
 *  3. 刷新按钮是唯一整板重查入口，且缓存态在弹窗里说在明处。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import client from '@/api/client';
import DashboardPreviewDialog from '@/components/asbot/DashboardPreviewDialog';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({
  useVisLibrary: () => ({ items: [], error: '', refresh: vi.fn() }),
}));
vi.mock('@/components/DashboardCanvas', () => ({
  ResponsiveDashboardCanvas: () => <div>响应式网格</div>,
}));

const cache = JSON.stringify({ columns: ['站点', '数量'], rows: [['中国', 12]] });

const chart = (id: number, cached: boolean) => ({
  id, name: `图 ${id}`, chart_type: 'bar', source_type: 'dataset', source_id: 100 + id,
  data_cache: cached ? cache : null, config: {}, position: { x: 0, y: 0, w: 480, h: 300 },
});

const board = (charts: any[]) => ({
  id: 9, name: '今年案例数量总览 · 五站合一', status: 'enabled', charts,
  description: '', filters: {}, params: [], page_params: [], carousel_interval: 0,
});

const getMock = () => (client.get as any);
const postMock = () => (client.post as any);

const open = (charts: any[]) => {
  getMock().mockResolvedValue({ data: board(charts) });
  postMock().mockResolvedValue({ data: { columns: ['站点', '数量'], rows: [['中国', 12]] } });
  render(<DashboardPreviewDialog dashboardId={9} onClose={() => {}} />);
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('DashboardPreviewDialog 缓存优先', () => {
  it('带 data_cache 的图打开时不重查（不把看板反复压到数据源）', async () => {
    open([chart(11, true), chart(12, true)]);
    expect(await screen.findByText('响应式网格')).toBeInTheDocument();
    // 给 refreshCharts 的排队/执行留一拍，确认确实没有发出 refresh 请求
    await new Promise(r => setTimeout(r, 20));
    expect(postMock()).not.toHaveBeenCalled();
    // 缓存态说在明处：用户知道要看新数得点刷新
    expect(screen.getByText(/2 个图表显示缓存数据/)).toBeInTheDocument();
  });

  it('从来没有缓存的图仍补查一次（否则留白会被当成“这块板没数据”）', async () => {
    open([chart(11, true), chart(12, false)]);
    await waitFor(() => expect(postMock()).toHaveBeenCalledTimes(1));
    expect(postMock().mock.calls[0][0]).toBe('/dashboard/9/charts/12/refresh');
  });

  it('「刷新数据」是唯一整板重查入口：点了才逐图 POST', async () => {
    open([chart(11, true), chart(12, true)]);
    await screen.findByText('响应式网格');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await waitFor(() => expect(postMock()).toHaveBeenCalledTimes(2));
    expect(postMock().mock.calls.map((c: any[]) => c[0]).sort()).toEqual([
      '/dashboard/9/charts/11/refresh',
      '/dashboard/9/charts/12/refresh',
    ]);
  });
});
