import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { toast } from 'sonner';
import DashboardChart from '../DashboardChart';
import PropertyPanel from '../editor/PropertyPanel';
import ChartConfigPanel from '../ChartConfigPanel';
import { useDashboardStore, type Dashboard, type DashboardChart as ChartModel } from '@/stores/dashboardStore';
import { useEditorCharts } from '@/hooks/useEditorCharts';
import client from '@/api/client';

const bar = { id: 7, name: '省份用户分布', chart_type: 'bar', config: {}, position: { x: 0, y: 0, w: 400, h: 300 },
  data_cache: JSON.stringify({ columns: ['province', 'uv'], rows: [{ province: '上海市', uv: 1 }] }) } as ChartModel;
const widget = (type: string, config: Record<string, any> = {}) => ({ id: 21, name: '控件', chart_type: type, config,
  position: { x: 0, y: 0, w: 300, h: 60 } }) as unknown as ChartModel;
const widgetChart = (type: string, config: Record<string, any> = {}) =>
  <DashboardChart chartType={type} data={{ columns: [], rows: [] }} config={config} draft={false} />;
const restore = () => useDashboardStore.getState();

function panelProps(selectedChart: any) {
  return {
    isOpen: true, onClose: () => {}, selectedChart, selectedElementType: 'control' as const,
    canvasSize: { width: 1920, height: 1080 }, setCanvasSize: () => {}, canvasBgColor: '', setCanvasBgColor: () => {},
    gridSize: 20, setGridSize: () => {}, scale: 1, setScale: () => {}, allCharts: [bar, selectedChart],
    isNewChart: (id: number) => id < 0, onPropertyChange: () => {}, onPositionChange: () => {},
    onWidgetConfigChange: () => {}, onDelete: () => {}, onChartConfig: () => {}, tab: 'data', onTab: () => {},
  };
}

beforeEach(() => {
  vi.spyOn(toast, 'error').mockReturnValue(0);
  vi.spyOn(client, 'get').mockResolvedValue({ data: {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('控件配置唯一入口', () => {
  it('日期范围控件可配最大跨度、绑定参数与标签位置', () => {
    render(<PropertyPanel {...panelProps(widget('widget_daterange', { paramKey: 'd' }))} />);
    expect(screen.getByText('最大跨度（天）')).toBeTruthy();
    expect(screen.getByText('绑定开始参数')).toBeTruthy();
    expect(screen.getByText('绑定结束参数')).toBeTruthy();
    expect(screen.getByText('标签位置')).toBeTruthy();
    expect(screen.getByRole('switch')).toBeTruthy();
  });

  it('下拉控件可配标签文字与显示开关', () => {
    render(<PropertyPanel {...panelProps(widget('widget_select', { paramKey: 'site' }))} />);
    expect(screen.getByRole('switch')).toBeTruthy();
    expect(screen.getByText('显示标签')).toBeTruthy();
    expect(screen.getByText('标签文字')).toBeTruthy();
  });

  it('导出控件的目标图表用 name 显示，未选择时给出可见告警', () => {
    render(<PropertyPanel {...panelProps(widget('widget_export', {}))} />);
    expect(screen.getByText('导出目标图表')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toMatch(/未选择目标图表/);
  });

  it('目标图表已被删除时提示重新选择，而不是回退到别的图表', () => {
    render(<PropertyPanel {...panelProps(widget('widget_export', { targetChartId: 999 }))} />);
    expect(screen.getByRole('alert').textContent).toMatch(/已不在当前看板/);
  });

  it('ChartConfigPanel 不再接收控件（控件配置单入口）', () => {
    const { container } = render(<ChartConfigPanel open chart={widget('widget_select')} onClose={() => {}} onSave={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('导出按钮去静默', () => {
  it('未配置目标图表时按钮置灰，不导出任何数据', () => {
    const state = restore();
    useDashboardStore.setState({ dashboards: [{ id: 1, charts: [bar] } as unknown as Dashboard], currentId: 1 });
    const view = render(widgetChart('widget_export', {}));
    const button = view.getByRole('button');
    expect(button).toBeDisabled();
    expect(button.getAttribute('title')).toMatch(/属性面板/);
    expect(toast.error).not.toHaveBeenCalled();
    useDashboardStore.setState(state);
  });

  it('目标图表不存在时明确报错，不回退到第一个图表', () => {
    const state = restore();
    useDashboardStore.setState({ dashboards: [{ id: 1, charts: [bar] } as unknown as Dashboard], currentId: 1 });
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    const view = render(widgetChart('widget_export', { targetChartId: 999 }));
    fireEvent.click(view.getByRole('button'));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('999'));
    expect(create).not.toHaveBeenCalled();
    useDashboardStore.setState(state);
  });

  it('目标图表无缓存数据时提示先刷新，不静默产出空文件', () => {
    const state = restore();
    const empty = { ...bar, id: 8, data_cache: null } as ChartModel;
    useDashboardStore.setState({ dashboards: [{ id: 1, charts: [empty] } as unknown as Dashboard], currentId: 1 });
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    const view = render(widgetChart('widget_export', { targetChartId: 8 }));
    fireEvent.click(view.getByRole('button'));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('暂无数据'));
    expect(create).not.toHaveBeenCalled();
    useDashboardStore.setState(state);
  });
});

describe('图表卡与控件卡禁止跨类改型', () => {
  it('把图表改成 widget_ 类型被拒并提示，类型不变', () => {
    const state = restore();
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [bar], filters: {} } as unknown as Dashboard] });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.handlePropertyChange(7, 'chart_type', 'widget_select'));
    expect(h.result.current.allCharts[0].chart_type).toBe('bar');
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('同类内换型'));
    // 同类内换型仍然放行
    act(() => h.result.current.handlePropertyChange(7, 'chart_type', 'line'));
    expect(h.result.current.allCharts[0].chart_type).toBe('line');
    h.unmount(); useDashboardStore.setState(state);
  });
});
