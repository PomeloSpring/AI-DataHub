import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import DesignCard from '../DesignCard';

/**
 * 历史 ```design 块：设计卡片已退役（2026-11-17 用户反馈两次）。
 *
 * 第一次要求：把「打开设计面板」换成「预览」看板详情；
 * 第二次要求：界面上别再出现这张"设计卡片"（点开啥卡片啊）。
 * 现在的口径：
 *   - 有看板 → 只渲染一个「预览看板」按钮，不贴设计名/版本/业务域这类元信息；
 *   - 没看板（已取消/未发布）→ **整块不渲染**，不摆"该设计未生成看板"的卡片；
 *   - 设计读取失败 → 一行可重试的小字（不静默吞入口）。
 */
const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), getDesign: vi.fn() }));
vi.mock('../../api/client', () => ({ default: mocks }));
vi.mock('../../api/dashboardDesign', async importOriginal => ({
  ...await importOriginal<object>(), getDesign: mocks.getDesign,
}));
// 详情窗口直渲看板画布（与浏览页/大屏同一组件）：本用例只关心"打开的是哪块板"，画布本身桩掉
vi.mock('@/components/DashboardCanvas', () => ({
  ResponsiveDashboardCanvas: ({ dashboard }: { dashboard: { name: string } }) => <div>看板画布 {dashboard.name}</div>,
}));
vi.mock('@/hooks/useVisLibrary', () => ({
  useVisLibrary: () => ({ items: [], error: '', loading: false, loaded: true, refresh: vi.fn() }),
}));

const designId = 'a'.repeat(32);
const design = {
  design_id: designId, version: 2, status: 'cancelled', name: '案例数量分析看板',
  request: '做个案例经营大屏', operation: 'create' as const,
  selection: undefined as undefined | { datasource_name: string; knowledge_base_ids: number[]; dashboard_id?: number },
  widgets: [], steps: [], questions: [], answers: {}, preview_valid: false,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('历史设计块：只留预览看板，不再是设计卡片', () => {
  it('已生成看板 → 只出一个「预览看板」按钮；点开是看板详情，页面上没有设计卡片元信息', async () => {
    mocks.getDesign.mockResolvedValue({
      ...design, status: 'published',
      selection: { datasource_name: '中国站', knowledge_base_ids: [] },
      result: { success: true, url: '/dashboard/editor/10', dashboard_id: 10, chart_ids: [1, 2] },
    });
    mocks.get.mockResolvedValue({
      data: { id: 10, name: '案例经营大屏', description: '案例口径', charts: [{ id: 1 }, { id: 2 }] },
    });
    const { container } = render(<DesignCard designId={designId} />);
    fireEvent.click(await screen.findByRole('button', { name: '预览看板' }));

    // 设计卡片已退役：标题/状态/版本/业务域这些"卡片元信息"一个都不许回来
    expect(container.textContent).not.toContain('仪表盘设计');
    expect(container.textContent).not.toContain('版本');
    expect(container.textContent).not.toContain('业务域');
    expect(container.textContent).not.toContain('请先选择目标仪表盘');
    // 看板详情 = 与浏览页/大屏同一套画布（不套播放页 iframe：顶栏/返回/Esc 会把嵌帧导航走）
    expect(await screen.findByText('看板画布 案例经营大屏')).toBeInTheDocument();
    expect(screen.getByText(/2 个图表/)).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith('/dashboard/10');
    // 去大屏看这块板：播放页地址必须带 group（缺了会被守卫导航回列表）
    expect(screen.getByRole('link', { name: /在新窗口打开/ }))
      .toHaveAttribute('href', '/screen/10?group=0&from=/system/dashboards');
    // 设计面板已删除：SQL 编辑 / 确认发布这类入口一个都不许回来
    expect(screen.queryByRole('button', { name: /确认并发布/ })).toBeNull();
    expect(screen.queryByRole('tab', { name: /SQL/ })).toBeNull();
  });

  it('追加/改图的设计 → 预览目标看板（selection.dashboard_id）', async () => {
    mocks.getDesign.mockResolvedValue({
      ...design, status: 'published', operation: 'append',
      selection: { datasource_name: '中国站', knowledge_base_ids: [], dashboard_id: 12 },
    });
    mocks.get.mockResolvedValue({ data: { id: 12, name: '既有看板', charts: [] } });
    render(<DesignCard designId={designId} />);
    fireEvent.click(await screen.findByRole('button', { name: '预览看板' }));
    expect(await screen.findByText('看板画布 既有看板')).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith('/dashboard/12');
  });

  it('老版本设计只有 result.url（无 dashboard_id）→ 仍能从路径找到看板', async () => {
    mocks.getDesign.mockResolvedValue({
      ...design, status: 'published',
      result: { success: true, url: '/screen/1789970841605', chart_ids: [1] } as any,
    });
    mocks.get.mockResolvedValue({ data: { id: 1789970841605, name: '案件分析驾驶舱', charts: [] } });
    render(<DesignCard designId={designId} />);
    fireEvent.click(await screen.findByRole('button', { name: '预览看板' }));
    expect(await screen.findByText('看板画布 案件分析驾驶舱')).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith('/dashboard/1789970841605');
  });

  it('没生成过看板（已取消）→ 整块不渲染：无按钮、无"未生成看板"卡片', async () => {
    mocks.getDesign.mockResolvedValue(design);
    const { container } = render(<DesignCard designId={designId} />);
    await act(async () => { await Promise.resolve(); });
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole('button')).toBeNull();
    expect(mocks.get).not.toHaveBeenCalled();   // 没看板就不必探详情
  });

  it('设计读取失败 → 一行可重试的小字；重试成功后出现预览入口', async () => {
    mocks.getDesign.mockRejectedValueOnce({ response: { status: 404 } });
    mocks.getDesign.mockResolvedValue({
      ...design, status: 'published',
      result: { success: true, url: '/dashboard/editor/11', dashboard_id: 11, chart_ids: [] },
    });
    mocks.get.mockResolvedValue({ data: { id: 11, name: '失败恢复看板', charts: [] } });
    render(<DesignCard designId={designId} />);
    fireEvent.click(await screen.findByRole('button', { name: /看板入口加载失败/ }));
    fireEvent.click(await screen.findByRole('button', { name: '预览看板' }));
    expect(await screen.findByText('看板画布 失败恢复看板')).toBeInTheDocument();
  });
});