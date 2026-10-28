import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { toast } from '@/lib/toast';
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

describe('控件样式面板 — 生效值与来源', () => {
  // 历史缺陷：面板只读本图 config.widgetStyle，显示的是写死的回退值（圆角 8/字号 14），
  // 与画布上字模的真实生效值不一致；且本图覆盖优先于字模，用户选完字模看不出为何“没反应”。
  const styleProps = (selectedChart: any, extra: Record<string, any> = {}) =>
    ({ ...panelProps(selectedChart), tab: 'appearance', ...extra });

  it('未覆盖时按字模基线显示生效值，并标明基线来源', () => {
    render(<PropertyPanel {...styleProps(widget('widget_select', { paramKey: 'site' }),
      { widgetStyleBase: { fontSize: 13, borderRadius: 8 }, widgetVisName: '下拉框·AS' })} />);
    expect(screen.getByText(/基线来源：控件字模「下拉框·AS」/)).toBeTruthy();
    expect(screen.getByText('13px')).toBeTruthy();          // 字号取字模的 13，不是面板写死的 14
    expect(screen.getByText(/本图未覆盖任何项/)).toBeTruthy();
  });

  it('本图覆盖的键被显式列出，可逐项释放回跟随字模', () => {
    const onWidgetConfigChange = vi.fn();
    render(<PropertyPanel {...styleProps(widget('widget_select', { paramKey: 'site', widgetStyle: { fontSize: 20 } }),
      { widgetStyleBase: { fontSize: 13 }, widgetVisName: '下拉框·AS', onWidgetConfigChange })} />);
    expect(screen.getByText(/本图覆盖（优先于字模）/)).toBeTruthy();
    expect(screen.getByText('20px')).toBeTruthy();
    fireEvent.click(screen.getByTitle('点击让该项跟随字模'));
    expect(onWidgetConfigChange).toHaveBeenCalledWith(21, 'widgetStyle', {});
  });

  it('无字模时按主题包基线说明，并保留一键清除本图覆盖', () => {
    const onWidgetConfigChange = vi.fn();
    render(<PropertyPanel {...styleProps(widget('widget_text', { paramKey: 'kw', widgetStyle: { padding: 12 } }),
      { widgetStyleBase: { fontSize: 13 }, onWidgetConfigChange })} />);
    expect(screen.getByText(/基线来源：主题包控件基线/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '清除本图覆盖（全部跟随字模）' }));
    expect(onWidgetConfigChange).toHaveBeenCalledWith(21, 'widgetStyle', {});
  });
});

describe('控件样式旋钮被真实消费（挂了字模要看得见）', () => {
  const styleOf = (el: HTMLElement) => el.getAttribute('style') || '';
  it('描边/圆角/字号落在控件内联样式上', () => {
    const view = render(widgetChart('widget_text', { paramKey: 'kw', showLabel: false,
      widgetStyle: { backgroundColor: '#ffffff', borderColor: '#E9ECF8', borderRadius: 8, fontSize: 13 } }));
    const style = styleOf(view.getByRole('textbox'));
    expect(style).toContain('border-radius: 8px');
    expect(style).toContain('font-size: 13px');
    expect(style).toContain('border: 1px solid rgb(233, 236, 248)');   // #E9ECF8
    expect(style).toContain('background: rgb(255, 255, 255)');
  });
  it('「控件高」不再只给多选框用：无标签控件按高度居中，不拉满卡片', () => {
    const view = render(widgetChart('widget_select', { paramKey: 'site', showLabel: false, widgetStyle: { height: 32 } }));
    const style = styleOf(view.getByRole('combobox'));
    expect(style).toContain('height: 32px');
    expect(style).toContain('margin: auto 0');
  });
  it('有标签时高度落在内层输入上', () => {
    const view = render(widgetChart('widget_text', { paramKey: 'kw', label: '关键词', widgetStyle: { height: 28 } }));
    expect(styleOf(view.getByRole('textbox'))).toContain('height: 28px');
  });
  it('搜索按钮吃主色与控件高', () => {
    const view = render(widgetChart('widget_search', { label: '查询', widgetStyle: { backgroundColor: '#0037CB', height: 32 } }));
    const style = styleOf(view.getByRole('button'));
    expect(style).toContain('height: 32px');
    expect(style).toContain('background: rgb(0, 55, 203)');            // 主色 #0037CB
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
