import { createElement, useState } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import DashboardEditor from '@/pages/DashboardEditor';
import { act, cleanup, fireEvent, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as G2 from '@antv/g2';
import DashboardChart, { CHART_TYPES } from '../DashboardChart';
import { CanvasRulers, DashboardCanvas, DashboardChartCard, ResponsiveDashboardCanvas } from '../DashboardCanvas';
import PageLayoutEditor from '../editor/PageLayoutEditor';
import { PAGE_COLUMNS, PAGE_ROW, pageColumnPx, resolvePageLayout, movePageItem, pageCanvasSize, type PageBreakpoint } from '@/lib/dashboardPageLayout';
import type { DropGhost } from '@/lib/editorDrag';
import Preview, { ShapePreview } from '../VisComponentPreview';
import { useCanvasInteraction } from '@/hooks/useCanvasInteraction';
import { useEditorCharts } from '@/hooks/useEditorCharts';
import { applyScreenComponent, resolveDashboardDesign } from '@/lib/dashboardDesign';
import { parseTime } from '@/lib/timePresets';
import { useDashboardStore, type Dashboard, type DashboardChart as ChartModel } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';
import client from '@/api/client';

const editorLibrary = vi.hoisted(() => ({ items: [], error: null, refresh: vi.fn() }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => editorLibrary }));
vi.mock('@/components/ChartConfigPanel', () => ({ default: () => null }));
// 模板弹窗/左侧库换成探针：空画布引导用例要分辨“弹模板”还是“切到数据集页签”
vi.mock('@/components/DashboardTemplates', () => ({ default: ({ open }: { open: boolean }) => (open ? <div data-testid="dashboard-templates" /> : null) }));
vi.mock('@/components/editor/ComponentLibrary', () => ({ default: ({ tab }: { tab: string }) => <div data-testid="component-library" data-tab={tab} /> }));
vi.mock('@/components/editor/PropertyPanel', () => ({ default: () => <input aria-label="属性输入" /> }));

const chart = { id: 7, name: '回归图表', chart_type: 'bar', config: {}, position: { x: 100, y: 100, w: 400, h: 300 },
  data_cache: JSON.stringify({ columns: ['name', 'value'], rows: [{ name: 'A', value: 10 }] }) } as ChartModel;
const pack = { id: 5, name: '回归主题', code: 'tp_test', category: 'theme_pack', style_config: { mode: 'dark', palette: ['#22d3ee', '#3b82f6'],
  background: { backgroundColor: '#0a1220' }, card: { cardBg: '#112233' },
  widgets: { default: { backgroundColor: '#112233', textColor: '#ddeeff', borderRadius: 11 } }, charts: { default: { lineWidth: 3 } } },
  is_active: 1, is_builtin: 1, source: 'system', sort_order: 0 } as VisComponent;
let frames: Map<number, FrameRequestCallback>;
let frameId: number;
let callbacks: ResizeObserverCallback[];
const originalObserver = globalThis.ResizeObserver;
const flush = () => act(() => { const batch = [...frames.values()]; frames.clear(); batch.forEach(fn => fn(0)); });

beforeEach(() => {
  frames = new Map(); frameId = 0; callbacks = [];
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => { frames.set(++frameId, fn); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  globalThis.ResizeObserver = class { constructor(fn: ResizeObserverCallback) { callbacks.push(fn); } observe() {} unobserve() {} disconnect() {} };
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(300);
  vi.spyOn(G2, 'Chart').mockImplementation(function (this: any) { this.options = vi.fn(); this.render = vi.fn(); this.destroy = vi.fn(); } as any);
  vi.stubGlobal('PointerEvent', class extends MouseEvent {
    pointerId: number; isPrimary = true;
    constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; }
  });
});
afterEach(() => { cleanup(); globalThis.ResizeObserver = originalObserver; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function interaction(options: Partial<Parameters<typeof useCanvasInteraction>[0]> = {}) {
  const onDragEnd = vi.fn(), onResizeEnd = vi.fn(), onSelectElement = vi.fn();
  let state!: ReturnType<typeof useCanvasInteraction>;
  function Harness() {
    state = useCanvasInteraction({ allCharts: [chart], onDragEnd, onResizeEnd, onSelectElement, ...options });
    return createElement('div', { ref: state.canvasRef, 'data-testid': 'viewport', onPointerDown: state.handlePanStart },
      createElement('div', { className: 'adh-cell', 'data-testid': 'cell', onPointerDown: (e: any) => state.handleDragStart(e, 7) },
        createElement('span', { 'data-testid': 'resize', onPointerDown: (e: any) => state.handleResizeStart(e, 7) }),
        createElement('button', { 'data-testid': 'settings' }, '配置')));
  }
  const view = render(createElement(Harness));
  return { ...view, onDragEnd, onResizeEnd, onSelectElement, state: () => state };
}
function pageInteraction(dashboard = { id: 88, filters: {}, charts: [chart] } as Dashboard, autoFit = false, interactive = false) {
  let state!: ReturnType<typeof useCanvasInteraction>;
  let changeBreakpoint!: (bp: PageBreakpoint) => void;
  let toggleGrid!: () => void;
  const onChange = vi.fn(), onSelect = vi.fn(), onConfig = vi.fn(), onDelete = vi.fn();
  const items: VisComponent[] = [];
  let setGhost!: (ghost: DropGhost | null) => void;
  function Harness() {
    const [bp, setBp] = useState<PageBreakpoint>('desktop');
    const [grid, setGrid] = useState(true);
    const [ghost, setGhostState] = useState<DropGhost | null>(null);
    setGhost = setGhostState;
    changeBreakpoint = setBp; toggleGrid = () => setGrid(value => !value);
    state = useCanvasInteraction({ allCharts: dashboard.charts, onDragEnd: vi.fn(), onResizeEnd: vi.fn(), onSelectElement: onSelect,
      autoFit, viewportKey: bp, viewportSize: pageCanvasSize(resolvePageLayout(dashboard, bp), bp) });
    return <PageLayoutEditor dashboard={dashboard} items={items} breakpoint={bp} selectedIds={[7]} canvas={state} showGrid={grid} panMode={false} interactive={interactive} showRulers
      dropGhost={ghost}
      onSelect={onSelect} onConfig={onConfig} onDelete={onDelete} onChange={onChange} />;
  }
  const view = render(<Harness />);
  return { ...view, onChange, onSelect, onConfig, onDelete, state: () => state, setGhost: (g: DropGhost | null) => act(() => setGhost(g)), changeBreakpoint: (bp: PageBreakpoint) => changeBreakpoint(bp), toggleGrid: () => toggleGrid() };
}
const pointer = (target: Element | Window, type: string, x: number, y: number, extra: PointerEventInit = {}) =>
  fireEvent(target, new PointerEvent(type, { bubbles: true, pointerId: 1, button: 0, clientX: x, clientY: y, ...extra }));

describe('指针交互回归', () => {
  it('点击不移动、不吸附、不产生位置保存', () => {
    const h = interaction();
    pointer(h.getByTestId('cell'), 'pointerdown', 200, 200);
    pointer(window, 'pointerup', 201, 201);
    // 选中回调带上原始事件，Shift/Ctrl 多选才能在这里分辨加选/改选
    expect(h.onSelectElement).toHaveBeenCalledWith(chart, expect.objectContaining({ shiftKey: false }));
    expect(h.onDragEnd).not.toHaveBeenCalled();
    expect(h.state().draggingChart).toBeNull();
  });
  it('连续事件逐帧更新，缩放后拖动距离正确，松手提交最后位置一次', () => {
    const h = interaction();
    act(() => { h.state().setScale(0.5); h.state().setSnapEnabled(false); });
    pointer(h.getByTestId('cell'), 'pointerdown', 200, 200);
    pointer(window, 'pointermove', 210, 210); pointer(window, 'pointermove', 230, 220);
    expect(frames.size).toBe(1); flush();
    expect(h.state().dragPosition).toMatchObject({ x: 160, y: 140 });
    pointer(window, 'pointermove', 240, 225);
    pointer(window, 'pointerup', 250, 230);
    expect(h.onDragEnd).toHaveBeenCalledExactlyOnceWith(7, { x: 200, y: 160 });
    expect(frames.size).toBe(0);
  });
  it.each(['pointercancel', 'lostpointercapture', 'Escape', 'blur'])('%s 取消交互不落草稿，释放待执行帧', kind => {
    const h = interaction();
    pointer(h.getByTestId('cell'), 'pointerdown', 200, 200);
    pointer(window, 'pointermove', 230, 220);
    if (kind === 'Escape') fireEvent.keyDown(window, { key: 'Escape' });
    else if (kind === 'blur') fireEvent(window, new Event('blur'));
    else pointer(window, kind, 230, 220);
    pointer(window, 'pointerup', 230, 220);
    expect(h.onDragEnd).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
  });
  it('配置按钮不启动拖动，其他指针不能结束当前操作', () => {
    const h = interaction();
    pointer(h.getByTestId('settings'), 'pointerdown', 200, 200);
    expect(h.onSelectElement).not.toHaveBeenCalled();
    pointer(h.getByTestId('cell'), 'pointerdown', 200, 200);
    pointer(window, 'pointerup', 250, 250, { pointerId: 2 });
    expect(h.onDragEnd).not.toHaveBeenCalled();
    pointer(window, 'pointerup', 250, 250);
    expect(h.onDragEnd).toHaveBeenCalledTimes(1);
  });
  it('缩放不会将图表压成零高，只防塌陷不再按类型抬高', () => {
    const h = interaction();
    act(() => h.state().setSnapEnabled(false));
    pointer(h.getByTestId('resize'), 'pointerdown', 500, 400);
    pointer(window, 'pointerup', -100, -100);
    // 下限只保留 1×1 防塌陷，不再夹回 200×150 那种“最小”
    expect(h.onResizeEnd).toHaveBeenCalledWith(7, expect.objectContaining({ w: 1, h: 1 }));
    expect(h.onDragEnd).not.toHaveBeenCalled();
  });
  it('滚轮取消页面滚动且不在画布外触发缩放', () => {
    const h = interaction();
    const e = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -100 });
    fireEvent(h.getByTestId('viewport'), e);
    expect(e.defaultPrevented).toBe(true);
    const scale = h.state().scale;
    fireEvent.wheel(document.body, { deltaY: -100 });
    expect(h.state().scale).toBe(scale);
  });
});

describe('共享画布与渲染稳定性', () => {
  it('仅选择图层时图表对象及数组引用不变', () => {
    const old = useDashboardStore.getState().dashboards;
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [chart], filters: {} } as Dashboard] });
    const h = renderHook(() => useEditorCharts(88));
    const before = h.result.current.allCharts;
    act(() => h.result.current.handleSelectElement(chart));
    expect(h.result.current.allCharts).toBe(before);
    expect(h.result.current.allCharts[0]).toBe(chart);
    expect(h.result.current.hasUnsavedChanges).toBe(false);
    h.unmount(); useDashboardStore.setState({ dashboards: old });
  });
  it('反复测量同一尺寸不销毁重建 G2，改变画布缩放也不重建', () => {
    const dashboard = { filters: applyScreenComponent({}, pack), charts: [chart] };
    const items = [pack];
    const view = render(createElement(DashboardCanvas, { dashboard, items, draft: true }));
    flush();
    const created = vi.mocked(G2.Chart).mock.calls.length;
    expect(created).toBe(1);
    act(() => callbacks.forEach(fn => fn([], {} as ResizeObserver)));
    flush();
    view.rerender(createElement(DashboardCanvas, { dashboard, items, draft: true, style: { transform: 'scale(0.5)' } }));
    flush();
    expect(vi.mocked(G2.Chart).mock.calls).toHaveLength(created);
    expect(view.container.querySelector('.adh-chart-card')).toHaveStyle({ height: '100%', minHeight: '0' });
  });
  it('表格空数据变成有数据不会改变 Hook 调用顺序', () => {
    const view = render(createElement(DashboardChart, { chartType: 'table_value', data: { columns: [], rows: [] }, draft: true }));
    expect(() => view.rerender(createElement(DashboardChart, { chartType: 'table_value', data: { columns: ['v'], rows: [{ v: 1 }] }, draft: true }))).not.toThrow();
    expect(view.getByRole('table')).toBeTruthy();
  });
  it('图表从空数据加载后能重新测量并渲染', () => {
    const view = render(createElement(DashboardChart, { chartType: 'bar', data: { columns: [], rows: [] }, draft: true }));
    view.rerender(createElement(DashboardChart, { chartType: 'bar', data: { columns: ['name', 'value'], rows: [{ name: 'A', value: 10 }] }, draft: true }));
    flush(); expect(vi.mocked(G2.Chart)).toHaveBeenCalled();
  });
  it.each(CHART_TYPES.filter(t => t.category === 'widget').map(t => t.value))('%s 内部使用主题包而非全局颜色', type => {
    const design = resolveDashboardDesign(applyScreenComponent({}, pack), []);
    const view = render(createElement(DashboardChartCard, { chart: { ...chart, chart_type: type }, design, items: [], draft: true }));
    const element = view.container.querySelector('input,select,button');
    if (element) expect(element).toHaveStyle({ background: '#112233', color: '#ddeeff' });
    else expect(view.getByText('文本标签')).toHaveStyle({ color: '#ddeeff' });
  });
});

describe('编辑态控件可操作性', () => {
  const widgetCard = (type: string, extra: Record<string, any> = {}) => ({
    ...chart, id: 21, chart_type: type, config: { paramKey: 'site', label: '控件', ...extra },
  }) as ChartModel;
  const design = () => resolveDashboardDesign({}, []);

  it.each(['widget_search', 'widget_reset', 'widget_export', 'widget_text', 'widget_number', 'widget_select', 'widget_multi_select', 'widget_date', 'widget_daterange', 'widget_label'])(
    '%s 编辑态默认 inert（pointerdown 不落在表单元素上），试运行解除', type => {
      const view = render(createElement(DashboardChartCard, { chart: widgetCard(type), design: design(), items: [], draft: true, interactive: false }));
      expect(view.container.querySelector('[inert]')).toBeTruthy();
      view.rerender(createElement(DashboardChartCard, { chart: widgetCard(type), design: design(), items: [], draft: true, interactive: true }));
      expect(view.container.querySelector('[inert]')).toBeNull();
    });

  it('阅读页与非控件卡不加 inert', () => {
    const view = render(createElement(DashboardChartCard, { chart: widgetCard('widget_search'), design: design(), items: [] }));
    expect(view.container.querySelector('[inert]')).toBeNull();
    view.unmount();
    const bar = render(createElement(DashboardChartCard, { chart, design: design(), items: [], draft: true }));
    expect(bar.container.querySelector('[inert]')).toBeNull();
  });

  it('试运行下搜索按钮真实触发刷新，不再被 draft 静默丢弃', () => {
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshCharts').mockResolvedValue(true);
    const view = render(createElement(DashboardChart, { chartType: 'widget_search', data: { columns: [], rows: [] }, config: { label: '搜索' }, draft: true }));
    fireEvent.click(view.getByRole('button', { name: '搜索' }));
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('编辑态文本控件的输入写入参数值（不再静默丢弃）', () => {
    const old = useDashboardStore.getState();
    useDashboardStore.setState({ paramValues: {} });
    const view = render(createElement(DashboardChart, { chartType: 'widget_text', data: { columns: [], rows: [] }, config: { paramKey: 'site' }, draft: true }));
    act(() => { fireEvent.change(view.container.querySelector('input')!, { target: { value: '上海' } }); });
    expect(useDashboardStore.getState().paramValues.site).toBe('上海');
    useDashboardStore.setState(old);
  });

  it('控件内层表单元素真实消费 widgetStyle 覆盖（修「存了不生效」）', () => {
    const style = { borderRadius: 2, padding: '1px 2px', lineHeight: 16, fontSize: 11, gap: 0 };
    const view = render(createElement(DashboardChart, { chartType: 'widget_date', data: { columns: [], rows: [] },
      config: { paramKey: 'd', label: '日期', widgetStyle: style }, draft: true }));
    const input = view.container.querySelector('input')!;
    expect(input).toHaveStyle({ borderRadius: '2px', padding: '1px 2px' });
    view.unmount();
    const multi = render(createElement(DashboardChart, { chartType: 'widget_multi_select', data: { columns: [], rows: [] },
      config: { paramKey: 'm', label: '多选', options: [{ label: 'A', value: 'A' }], widgetStyle: { height: 24 } }, draft: true }));
    expect(multi.container.querySelector('select')!).toHaveStyle({ minHeight: '24px' });
  });
});

describe('时间区间快捷选项', () => {
  const range = (config: Record<string, any> = {}) => render(createElement(DashboardChart, {
    chartType: 'widget_daterange', data: { columns: [], rows: [] }, config: { paramKey: 'range', label: '时间区间', ...config }, draft: false,
  }));

  it('默认渲染 7 个预设 + 自由选择，当前预设从参数值里读回', () => {
    const state = useDashboardStore.getState();
    useDashboardStore.setState({ paramValues: { range: { start: '2026-10-15', end: '2026-10-22', preset: '7d' } }, pageParamValues: {} });
    const view = range();
    const select = view.getByLabelText('时间区间快捷选项');
    for (const label of ['近 1 小时', '近 6 小时', '近 12 小时', '近 1 天', '近 7 天', '近一个月', '近 3 个月', '自由选择']) {
      expect(view.getByText(label)).toBeTruthy();
    }
    expect(select).toHaveValue('7d');
    // 天级预设保持按天粒度（输入框为 date、值只到日）
    const inputs = Array.from(view.container.querySelectorAll('input'));
    expect(inputs.map(i => i.type)).toEqual(['date', 'date']);
    expect(view.container.querySelector('input')!.value).toBe('2026-10-15');
    useDashboardStore.setState(state);
    view.unmount();
  });

  it('选「近 1 小时」写入本地时区的时分秒区间，并切到 datetime 输入', () => {
    const state = useDashboardStore.getState();
    useDashboardStore.setState({ paramValues: {}, pageParamValues: {} });
    const view = range();
    const before = Date.now();
    fireEvent.change(view.getByLabelText('时间区间快捷选项'), { target: { value: '1h' } });
    const value = useDashboardStore.getState().paramValues.range;
    expect(value.preset).toBe('1h');
    expect(value.start).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(value.end).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    // 规范值只到秒，因此允许 1s 的取整误差
    expect(parseTime(value.end)!.getTime()).toBeGreaterThanOrEqual(before - 1000);
    expect(parseTime(value.start)!.getTime()).toBeLessThanOrEqual(Date.now());
    const span = parseTime(value.end)!.getTime() - parseTime(value.start)!.getTime();
    expect(span).toBeGreaterThan(3_600_000 - 2000);
    expect(span).toBeLessThanOrEqual(3_600_000);
    // 小时级必须带时分秒输入框，且值以 T 分隔呈现
    const start = view.container.querySelector('input')!;
    expect(start.type).toBe('datetime-local');
    expect(start.value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    useDashboardStore.setState(state);
    view.unmount();
  });

  it('手改结束日期后预设回落自由选择，不默默挂在旧预设上', () => {
    const state = useDashboardStore.getState();
    useDashboardStore.setState({ paramValues: { range: { start: '2026-10-15', end: '2026-10-22', preset: '7d' } }, pageParamValues: {} });
    const view = range();
    fireEvent.change(view.getByLabelText('时间区间结束'), { target: { value: '2026-10-21' } });
    const value = useDashboardStore.getState().paramValues.range;
    expect(value.preset).toBe('custom');
    expect(value.end).toBe('2026-10-21');
    expect(view.getByLabelText('时间区间快捷选项')).toHaveValue('custom');
    useDashboardStore.setState(state);
    view.unmount();
  });

  it('bind_param 仍只把 start/end 写进两个页面参数（preset 字段不污染）', () => {
    const state = useDashboardStore.getState();
    useDashboardStore.setState({ paramValues: {}, pageParamValues: {}, pageParams: [] });
    const view = range({ bind_param: 'date_start,date_end' });
    fireEvent.change(view.getByLabelText('时间区间快捷选项'), { target: { value: '1d' } });
    const page = useDashboardStore.getState().pageParamValues;
    expect(Object.keys(page).sort()).toEqual(['date_end', 'date_start']);
    expect(page.date_start).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(page.date_end).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    useDashboardStore.setState(state);
    view.unmount();
  });

  it('内容行必须可收缩可换行（居中布局下不可收缩会向卡片两侧同时溢出）', () => {
    const state = useDashboardStore.getState();
    useDashboardStore.setState({ paramValues: { range: { start: '2026-10-22 03:10:00', end: '2026-10-22 15:10:00', preset: '12h' } }, pageParamValues: {} });
    const view = range();
    const row = view.getByLabelText('时间区间快捷选项').parentElement as HTMLElement;
    expect(row.style.getPropertyValue('min-width')).toMatch(/^0(px)?$/);
    expect(row.style.getPropertyValue('flex-wrap')).toBe('wrap');
    for (const input of Array.from(row.querySelectorAll('input'))) {
      expect(input.style.getPropertyValue('min-width')).toMatch(/^0(px)?$/);
      // 换行后不能每行占满容器高，否则垂直方向接着溢出
      expect(input.style.height).toBe('');
    }
    // 小时级预设已把两个输入框撑到 datetime，仍不得写死 height
    expect((row.querySelector('input') as HTMLInputElement).type).toBe('datetime-local');
    useDashboardStore.setState(state);
    view.unmount();
  });

  it('可只保留部分预设，全部取消后不再渲染快捷下拉', () => {
    const state = useDashboardStore.getState();
    useDashboardStore.setState({ paramValues: {}, pageParamValues: {} });
    const view = range({ presets: ['7d', '3mo'] });
    expect(view.queryByText('近 1 小时')).toBeNull();
    expect(view.getByText('近 7 天')).toBeTruthy();
    view.unmount();
    const none = range({ presets: [] });
    expect(none.queryByLabelText('时间区间快捷选项')).toBeNull();
    expect(none.getByLabelText('时间区间开始')).toBeTruthy();
    useDashboardStore.setState(state);
    none.unmount();
  });
});

describe('双布局草稿与页面交互', () => {
  it('仅修改指定断点，配置面板保存、恢复自动适配和删除共享同一草稿', () => {
    const old = useDashboardStore.getState();
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [chart], filters: {} } as Dashboard] });
    const h = renderHook(() => useEditorCharts(88));
    const items = resolvePageLayout(h.result.current.draftDashboard!, 'tablet');
    act(() => h.result.current.updatePageLayout('tablet', movePageItem(items, 7, { x: 0, y: 0, w: 6, h: 20 }, 'tablet')));
    expect(h.result.current.allCharts[0].position).toEqual(chart.position);
    expect(h.result.current.allCharts[0].config.pageLayout.desktop).toBeUndefined();
    expect(h.result.current.allCharts[0].config.pageLayout.tablet.w).toBe(6);
    expect(h.result.current.draftDashboard?.filters.design.page.version).toBe(3);
    act(() => h.result.current.setSelectedChart(chart));
    act(() => h.result.current.handleSaveChartConfig({ xCol: 'name', yCol: 'value' }));
    expect(h.result.current.allCharts[0].config.pageLayout.tablet.w).toBe(6);
    act(() => h.result.current.updatePageLayout('tablet'));
    expect(h.result.current.allCharts[0].config.pageLayout.tablet).toBeUndefined();
    act(() => h.result.current.handleDeleteSelected(chart));
    expect(h.result.current.allCharts).toHaveLength(0);
    h.unmount(); useDashboardStore.setState(old);
  });
  it('新图表临时 ID 转正式 ID 保留布局；整板一次提交，失败保留草稿可重试', async () => {
    const old = useDashboardStore.getState();
    const put = vi.spyOn(client, 'put')
      .mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValueOnce({ data: { success: true, metadata_saved: true, created: {},
        results: [{ op: 'create', index: 0, chart_id: 91, ok: true }] } } as any);
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [], filters: {} } as unknown as Dashboard], loadDashboards: vi.fn().mockResolvedValue(undefined) });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.handleAddFromPanel(CHART_TYPES[0], { x: 0, y: 0 }));
    act(() => h.result.current.updatePageLayout('mobile', resolvePageLayout(h.result.current.draftDashboard!, 'mobile')));
    let saved: boolean | undefined;
    await act(async () => { saved = await h.result.current.saveAllChanges(); });
    expect(saved).toBe(false);
    expect(h.result.current.hasUnsavedChanges).toBe(true);
    await act(async () => { saved = await h.result.current.saveAllChanges(); });
    expect(saved).toBe(true);
    // 一次请求提交整板，不再逐图表 N 次往返
    expect(put).toHaveBeenCalledTimes(2);
    expect(put.mock.calls[0][0]).toBe('/dashboard/88/batch');
    expect((put.mock.calls[0][1] as any).added[0].config.pageLayout.mobile.w).toBe(1);
    expect(h.result.current.hasUnsavedChanges).toBe(false);
    h.unmount(); useDashboardStore.setState(old);
  });
  it('响应式预览复用正式网格，不查询、不写库、不整体缩放', () => {
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshSingleChart');
    const get = vi.spyOn(client, 'get'), post = vi.spyOn(client, 'post'), put = vi.spyOn(client, 'put');
    const dashboard = { filters: {}, charts: [chart, { ...chart, id: 8, chart_type: 'widget_input' }] };
    const view = render(<ResponsiveDashboardCanvas dashboard={dashboard} breakpoint="mobile" draft />);
    expect(view.container.querySelector('.adh-page-canvas')).toHaveAttribute('data-breakpoint', 'mobile');
    expect(view.container.querySelector('.adh-page-canvas > .grid')).toHaveStyle({ gridTemplateColumns: 'repeat(1, minmax(0, 1fr))' });
    expect(view.container.innerHTML).not.toContain('scale(');
    expect(refresh).not.toHaveBeenCalled();
    expect(get).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled(); expect(put).not.toHaveBeenCalled();
    view.rerender(<ResponsiveDashboardCanvas dashboard={dashboard} breakpoint="desktop" />);
    expect(view.container.querySelector('.adh-page-canvas')).toHaveAttribute('data-breakpoint', 'desktop');
  });
  it('页面图表新增自动记录版本，保存重载后布局及大屏坐标不变', async () => {
    const old = useDashboardStore.getState();
    let saved = { id: 88, charts: [] as ChartModel[], filters: {} } as Dashboard;
    vi.spyOn(client, 'put').mockImplementation(async (_url, payload) => {
      const body = payload as any;
      saved = { ...saved, ...(body.metadata || {}),
        charts: [...saved.charts, ...body.added.map((c: ChartModel, i: number) => ({ ...c, id: 91 + i }))] };
      return { data: { success: true, metadata_saved: !!body.metadata, created: {},
        results: body.added.map((_: unknown, i: number) => ({ op: 'create', index: i, chart_id: 91 + i, ok: true })) } };
    });
    vi.spyOn(client, 'get').mockImplementation(async () => ({ data: [structuredClone(saved)] }));
    useDashboardStore.setState({ dashboards: [saved], currentWorkspaceId: 0 });
    const h = renderHook(() => useEditorCharts(88));
    const pageLayout = { version: 1, desktop: { x: 0, y: 0, w: 6, h: 20 }, mobile: { x: 0, y: 0, w: 1, h: 20 } };
    act(() => { h.result.current.handleAddFromPanel(CHART_TYPES[0], { x: 120, y: 640 }, { pageLayout }); });
    expect(h.result.current.draftDashboard?.filters.design.page.version).toBe(3);
    await act(async () => { expect(await h.result.current.saveAllChanges()).toBe(true); });
    expect(h.result.current.hasUnsavedChanges).toBe(false);
    h.unmount();
    const reloaded = renderHook(() => useEditorCharts(88));
    expect(reloaded.result.current.allCharts[0].id).toBe(91);
    expect(reloaded.result.current.allCharts[0].config.pageLayout).toEqual(pageLayout);
    expect(reloaded.result.current.allCharts[0].position).toMatchObject({ x: 120, y: 640 });
    expect(reloaded.result.current.draftDashboard?.filters.design.page.version).toBe(3);
    reloaded.unmount(); useDashboardStore.setState(old);
  });
  it('水平滚动计入拖动距离，其他指针取消事件不终止当前手势', () => {
    const dashboard = { id: 88, filters: {}, charts: [chart] } as Dashboard;
    const view = pageInteraction(dashboard);
    const { onChange } = view;
    const cell = view.container.querySelector('[data-chart-id="7"]')!;
    const viewport = view.getByLabelText('页面布局编辑区');
    const original = resolvePageLayout(dashboard, 'desktop')[0].rect;
    pointer(cell, 'pointerdown', 20, 20);
    viewport.scrollLeft = (1440 + 16) / PAGE_COLUMNS.desktop * view.state().scale;
    pointer(window, 'pointercancel', 20, 20, { pointerId: 2 });
    pointer(window, 'pointerup', 20, 20);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0][0].rect.x).toBe(original.x + 1);
    pointer(cell, 'pointerdown', 20, 20);
    pointer(window, 'pointermove', 120, 68);
    pointer(window, 'pointercancel', 120, 68);
    pointer(window, 'pointerup', 120, 68);
    expect(onChange).toHaveBeenCalledTimes(1);
  });
  it('页面拖动只提交网格配置，取消手势不保存，桌面使用真实宽度', () => {
    const view = pageInteraction();
    const { onChange } = view;
    const cell = view.container.querySelector('[data-chart-id="7"]')!;
    expect(view.container.querySelector('[style*="1440px"]')).toBeTruthy();
    pointer(cell, 'pointerdown', 20, 20); pointer(window, 'pointermove', 120, 68); pointer(window, 'pointerup', 120, 68);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0][0].chart.position).toEqual(chart.position);
    pointer(cell, 'pointerdown', 20, 20); pointer(window, 'pointermove', 120, 68); fireEvent.keyDown(window, { key: 'Escape' }); pointer(window, 'pointerup', 120, 68);
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});

describe('图层与多选', () => {
  const second = { ...chart, id: 8, name: '第二个图表', position: { x: 600, y: 100, w: 400, h: 300 } } as ChartModel;
  it('Shift 点选累加选中集，普通点选改选，Escape 清空', () => {
    const old = useDashboardStore.getState();
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [chart, second], filters: {} } as Dashboard] });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.handleSelectElement(chart));
    expect(h.result.current.selectedIds).toEqual([7]);
    act(() => h.result.current.handleSelectElement(second, { shiftKey: true, stopPropagation: () => {} } as any));
    expect(h.result.current.selectedIds).toEqual([7, 8]);
    expect(h.result.current.selectedCharts).toHaveLength(2);
    act(() => h.result.current.handleSelectElement(second, { stopPropagation: () => {} } as any));
    expect(h.result.current.selectedIds).toEqual([8]);
    act(() => h.result.current.setSelection([]));
    expect(h.result.current.selectedIds).toEqual([]);
    h.unmount(); useDashboardStore.setState(old);
  });
  it('一次 Delete 批量删除选中集，只产生一条历史', () => {
    const old = useDashboardStore.getState();
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [chart, second], filters: {} } as Dashboard] });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.setSelection([7, 8]));
    act(() => h.result.current.handleDeleteMany(h.result.current.selectedCharts));
    expect(h.result.current.allCharts).toHaveLength(0);
    expect(h.result.current.canUndo).toBe(true);
    act(() => h.result.current.undo());
    expect(h.result.current.allCharts).toHaveLength(2);
    h.unmount(); useDashboardStore.setState(old);
  });
  it('锁定/隐藏的图层不参与拖动，阅读态不渲染隐藏卡片', () => {
    const locked = { ...chart, config: { locked: true } } as ChartModel;
    const hidden = { ...chart, config: { hidden: true } } as ChartModel;
    const view = render(<ResponsiveDashboardCanvas dashboard={{ filters: {}, charts: [hidden] } as any} breakpoint="desktop" />);
    expect(view.container.querySelector('[data-chart-id="7"]')).toBeNull();
    view.unmount();
    const draft = render(<ResponsiveDashboardCanvas dashboard={{ filters: {}, charts: [hidden] } as any} breakpoint="desktop" draft />);
    expect(draft.container.querySelector('[data-chart-id="7"]')).toBeTruthy();
    draft.unmount();
    // 锁定的卡片在网格编辑区不能开始拖拽手势
    const h = pageInteraction({ id: 88, filters: {}, charts: [locked] } as Dashboard);
    pointer(h.container.querySelector('[data-chart-id="7"]')!, 'pointerdown', 20, 20);
    expect(h.state().isInteracting()).toBe(false);
  });
});

describe('编辑视口、历史与流畅度回归', () => {
  it('页面自动等比例适应窗口，手动缩放后尺寸变化不覆盖用户视口', () => {
    const h = pageInteraction(undefined, true);
    const fitted = (400 - 64) / 1440;
    expect(h.state().scale).toBeCloseTo(fitted);
    expect(h.getByTestId('page-editor-surface')).toHaveStyle({ width: '1440px', transform: `translate(0px, 0px) scale(${fitted})` });
    act(() => h.state().zoomTo(1));
    act(() => callbacks.forEach(fn => fn([], {} as ResizeObserver)));
    expect(h.state().scale).toBe(1);
    act(() => h.state().resetZoom());
    expect(h.state().scale).toBeCloseTo(fitted);
    act(() => h.changeBreakpoint('mobile'));
    expect(h.state().scale).toBeCloseTo((300 - 64) / 480);
    expect(h.getByTestId('page-editor-surface')).toHaveStyle({ width: '390px' });
  });
  it('画布高度随布局增加后保持屏幕原点，手动缩放不被重置', () => {
    const h = renderHook(({ height }) => useCanvasInteraction({ allCharts: [], onDragEnd: vi.fn(), onResizeEnd: vi.fn(), onSelectElement: vi.fn(),
      viewportSize: { width: 1440, height }, viewportKey: 'page' }), { initialProps: { height: 480 } });
    act(() => h.result.current.zoomTo(0.5));
    const origin = h.result.current.panOffset.y - 480 * h.result.current.scale / 2;
    h.rerender({ height: 960 });
    expect(h.result.current.scale).toBe(0.5);
    expect(h.result.current.panOffset.y - 960 * h.result.current.scale / 2).toBe(origin);
  });
  it('网格避让不改 DOM 排序和图表尺寸，捕获留在卡片上以保留双击配置', () => {
    const dashboard = { id: 88, filters: {}, charts: [chart, { ...chart, id: 8, position: { ...chart.position, y: 500 } }] } as Dashboard;
    const h = pageInteraction(dashboard); flush();
    const first = h.container.querySelector('[data-chart-id="7"]') as HTMLElement;
    const second = h.container.querySelector('[data-chart-id="8"]') as HTMLElement;
    const capture = vi.fn(); Object.defineProperty(first, 'setPointerCapture', { value: capture, configurable: true });
    const rect = resolvePageLayout(dashboard, 'desktop')[1].rect;
    const rowBefore = second.style.gridRow;
    const created = vi.mocked(G2.Chart).mock.calls.length;
    pointer(first, 'pointerdown', 20, 20);
    expect(capture).toHaveBeenCalledWith(1);
    pointer(window, 'pointermove', 20, 20 + rect.y * PAGE_ROW * h.state().scale); flush();
    expect(second.style.gridRow).toBe(rowBefore);
    expect(second.style.transform).not.toBe('translate3d(0px, 0px, 0)');
    expect(vi.mocked(G2.Chart).mock.calls).toHaveLength(created);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(h.onChange).not.toHaveBeenCalled();
    fireEvent.doubleClick(first); expect(h.onConfig).toHaveBeenCalledWith(chart);
  });
  it.each(['space', 'middle', 'hand'])('%s 可以越过图表平移且不改变图表或选择', method => {
    const h = interaction({ panMode: method === 'hand' });
    if (method === 'space') fireEvent.keyDown(window, { code: 'Space', key: ' ' });
    pointer(h.getByTestId('cell'), 'pointerdown', 200, 200, { button: method === 'middle' ? 1 : 0 });
    pointer(window, 'pointermove', 260, 240); flush(); pointer(window, 'pointerup', 260, 240);
    expect(h.state().panOffset).toEqual({ x: 60, y: 40 });
    expect(h.onDragEnd).not.toHaveBeenCalled(); expect(h.onSelectElement).not.toHaveBeenCalled();
    fireEvent.keyUp(window, { code: 'Space' });
  });
  it('页面从卡片上空格平移，松开后可再次拖动图表', () => {
    const h = pageInteraction();
    const cell = h.container.querySelector('[data-chart-id="7"]')!;
    fireEvent.keyDown(window, { code: 'Space' });
    pointer(cell, 'pointerdown', 20, 20); pointer(window, 'pointerup', 80, 80);
    expect(h.state().panOffset).toEqual({ x: 60, y: 60 }); expect(h.onChange).not.toHaveBeenCalled();
    fireEvent.keyUp(window, { code: 'Space' });
    pointer(cell, 'pointerdown', 20, 20); pointer(window, 'pointerup', 120, 68);
    expect(h.onChange).toHaveBeenCalledTimes(1);
  });
  it('半比例页面拖动逐帧连续跟手，网格和 G2 实例保持稳定，松手只提交一次', () => {
    const h = pageInteraction();
    act(() => h.state().zoomTo(0.5)); flush();
    const created = vi.mocked(G2.Chart).mock.calls.length;
    const cell = h.container.querySelector('[data-chart-id="7"]')!;
    const original = resolvePageLayout({ filters: {}, charts: [chart] }, 'desktop')[0].rect;
    const gridColumn = (cell as HTMLElement).style.gridColumn;
    pointer(cell, 'pointerdown', 20, 20);
    pointer(window, 'pointermove', 30, 25); pointer(window, 'pointermove', 60, 44);
    expect(frames.size).toBe(1); flush();
    const translation = (cell as HTMLElement).style.transform.match(/translate3d\(([^p]+)px, ([^p]+)px/)!;
    expect(Number(translation[1])).toBeCloseTo(80); expect(Number(translation[2])).toBeCloseTo(48);
    expect((cell as HTMLElement).style.gridColumn).toBe(gridColumn);
    expect(h.getByLabelText('图表放置预览')).toBeTruthy();
    fireEvent.wheel(h.getByLabelText('页面布局编辑区'), { deltaY: -500 });
    expect(h.state().scale).toBe(0.5);
    expect(vi.mocked(G2.Chart).mock.calls).toHaveLength(created);
    pointer(window, 'pointerup', 20 + (1440 + 16) / PAGE_COLUMNS.desktop * 2, 44);
    expect(h.onChange).toHaveBeenCalledTimes(1);
    // 0.5 缩放下：拖 60.7 屏幕 px = 121 画布 px = 4 列（v3 列宽 30px）；纵向 24 屏幕 px = 48 画布 px = 12 行（1 行 4px）
    expect(h.onChange.mock.calls[0][0][0].rect).toMatchObject({ x: original.x + 4, y: original.y + 12 });
    expect(frames.size).toBe(0); expect(h.state().isInteracting()).toBe(false);
    expect(h.getByLabelText('页面参考点')).toBeTruthy();
    act(() => h.toggleGrid()); expect(h.queryByLabelText('页面参考点')).toBeNull();
  });
  it.each(['pointercancel', 'lostpointercapture', 'Escape', 'blur', 'breakpoint', 'unmount'])('页面 %s 取消时释放帧、捕获和草稿锁', event => {
    const h = pageInteraction(); flush();
    pointer(h.container.querySelector('[data-chart-id="7"]')!, 'pointerdown', 20, 20);
    pointer(window, 'pointermove', 120, 80);
    if (event === 'Escape') fireEvent.keyDown(window, { key: 'Escape' });
    else if (event === 'blur') fireEvent(window, new Event('blur'));
    else if (event === 'breakpoint') act(() => h.changeBreakpoint('tablet'));
    else if (event === 'unmount') h.unmount();
    else pointer(window, event, 120, 80);
    pointer(window, 'pointerup', 120, 80);
    expect(h.onChange).not.toHaveBeenCalled();
    expect(h.state().isInteracting()).toBe(false);
    flush(); expect(frames.size).toBe(0);
  });
  it('拖拽步进已细化：1 列 ≈ 30px、1 行 = 4px（v2 是整列 121px / 整行 20px，只能整块跳档）', () => {
    const h = pageInteraction(); flush();
    act(() => h.state().zoomTo(1)); flush();          // 拖拽量以画布坐标计，先把缩放固定在 1:1
    const cell = h.container.querySelector('[data-chart-id="7"]') as HTMLElement;
    // 以真实渲染的网格位置为基准（CSS grid 的 gridColumn/gridRow 从 1 起算）
    const [x0, w0] = cell.style.gridColumn.split('/ span ').map(Number);
    const [y0, h0] = cell.style.gridRow.split('/ span ').map(Number);
    pointer(cell, 'pointerdown', 20, 20);
    // 只拖 2 列宽 + 3 行高：在 v2 下 12px 纵向位移会被 round 抹平为 0（根本拖不动），现在逐格跟随
    pointer(window, 'pointerup', 20 + Math.round(pageColumnPx('desktop') * 2), 20 + PAGE_ROW * 3);
    expect(h.onChange).toHaveBeenCalledTimes(1);
    expect(h.onChange.mock.calls[0][0][0].rect).toEqual({ x: x0 - 1 + 2, y: y0 - 1 + 3, w: w0, h: h0 });
  });
  it('缩小不被类型下限顶住：预览与提交同口径，且内容不再被非等比拉伸', () => {
    const h = pageInteraction(); flush();
    act(() => h.state().zoomTo(1)); flush();
    const cell = h.container.querySelector('[data-chart-id="7"]') as HTMLElement;
    const [, h0] = cell.style.gridRow.split('/ span ').map(Number);
    const startHeight = h0 * PAGE_ROW;           // 行单位 4px，rowGap=0
    expect(startHeight).toBeGreaterThan(280);
    pointer(h.getByLabelText('调整图表大小'), 'pointerdown', 400, 400);
    pointer(window, 'pointermove', 400, 400 - 90); flush();
    const preview = parseFloat(cell.style.height);
    // 旧下限会把它钉在 280（而网格提交值继续变小，松手就“突然缩一号”）
    expect(preview).toBeLessThan(280);
    expect(preview).toBeCloseTo(startHeight - 90, 0);
    // 内容保持上一次渲染尺寸，不再靠 scale() 拉伸（标题放大/数据点拉成椭圆的根源）
    expect(cell.querySelector('[style*="scale("]')).toBeNull();
    expect(cell.className).toContain('overflow-hidden');   // 内容溢出被裁到框内（Tailwind 类，不是内联样式）
    pointer(window, 'pointerup', 400, 310);
    const committed = h.onChange.mock.calls[0][0][0].rect;
    // 提交值与预览同一量级（差不超过一个行量子）
    expect(Math.abs(committed.h * PAGE_ROW - preview)).toBeLessThanOrEqual(PAGE_ROW);
  });
  it('拖动时渲染对齐与间距参考线，松手后撤掉', () => {
    const dashboard = { id: 88, filters: {}, charts: [chart, { ...chart, id: 8, position: { ...chart.position, y: 500 } }] } as Dashboard;
    const h = pageInteraction(dashboard); flush();
    act(() => h.state().zoomTo(1)); flush();
    const cell = h.container.querySelector('[data-chart-id="7"]')!;
    expect(h.queryByTestId('align-guides')).toBeNull();          // 非拖拽态不出提示
    pointer(cell, 'pointerdown', 20, 20);
    pointer(window, 'pointermove', 20, 20 + 300); flush();       // 往下拖，两卡左边缘齐平
    const guides = h.getByTestId('align-guides');
    expect(guides.querySelectorAll('span').length).toBeGreaterThan(0);
    pointer(window, 'pointerup', 20, 320);
    expect(h.queryByTestId('align-guides')).toBeNull();
  });
  it('页面调整尺寸不逐帧重建图表，缩放和平移后新增落点正确', () => {
    const h = pageInteraction();
    act(() => { h.state().zoomTo(0.5); h.state().setPanOffset({ x: 70, y: 90 }); }); flush();
    const created = vi.mocked(G2.Chart).mock.calls.length;
    pointer(h.getByLabelText('调整图表大小'), 'pointerdown', 200, 200);
    pointer(window, 'pointermove', 230, 224); flush();
    expect(vi.mocked(G2.Chart).mock.calls).toHaveLength(created);
    pointer(window, 'pointerup', 230, 224);
    expect(h.onChange).toHaveBeenCalledTimes(1);
    const original = resolvePageLayout({ filters: {}, charts: [chart] }, 'desktop')[0].rect;
    // 24 屏幕 px / 0.5 缩放 = 48 画布 px = 12 行（v3 行单位 4px，不再是 20px）
    expect(h.onChange.mock.calls[0][0][0].rect.h).toBe(original.h + 12);
    // 从库拖入时 page 模式也有落点预览（旧 HTML5 DnD 路径上这里是一片空白）
    h.setGhost({ mode: 'page', x: 2, y: 4, w: 3, h: 2, chartType: 'widget_search' });
    const ghost = h.getByLabelText('新增落点预览');
    // 网格行列已换算成画布像素：y=4 行 → 16px，h=2 行 → 8px；3 列 → 3×30.33−16 = 75px
    expect(ghost.getAttribute('style')).toContain('top: 16px');
    expect(ghost.getAttribute('style')).toContain('height: 8px');
    // 虚线框内直接标出将占的像素尺寸（网格单位只是实现细节，对用户无意义）
    expect(ghost.textContent).toContain('75 × 8 px');
    h.setGhost(null);
    expect(h.queryByLabelText('新增落点预览')).toBeNull();
  });
  it('选中工具条与手柄渲染在缩放层之外，尺寸不随缩放变化', () => {
    const h = pageInteraction();
    const overlay = h.getByTestId('selection-overlay');
    const surface = h.getByTestId('page-editor-surface');
    // 覆盖层与被缩放的 surface 互为兄弟，不再靠反向缩放补偿
    expect(surface.contains(overlay)).toBe(false);
    expect(overlay.contains(surface)).toBe(false);
    for (const el of Array.from(overlay.querySelectorAll<HTMLElement>('[style]'))) {
      expect(el.getAttribute('style') || '').not.toMatch(/scale\(/);
    }
    // 卡片顶部贴画布上沿时，工具条改放卡片下方而不是遮住标题
    expect(overlay.querySelector('[aria-label="配置图表"]')).toBeTruthy();
    act(() => h.state().zoomTo(0.25));
    expect(h.getByTestId('selection-overlay').querySelector('[aria-label="配置图表"]')).toBeTruthy();
  });
  it('标尺可收起，不常驻占住画布边缘', () => {
    const view = render(<CanvasRulers canvas={{ width: 1440, height: 900 }} scale={0.5} pan={{ x: 0, y: 0 }} />);
    expect(view.queryByLabelText('画布坐标标尺')).toBeTruthy();
    view.rerender(<CanvasRulers canvas={{ width: 1440, height: 900 }} scale={0.5} pan={{ x: 0, y: 0 }} visible={false} />);
    expect(view.queryByLabelText('画布坐标标尺')).toBeNull();
  });
  it('撤销与重做覆盖双布局、配置、新增、删除，选择不入历史，新编辑清空前进分支', () => {
    const old = useDashboardStore.getState();
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [chart], filters: {} } as Dashboard] });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.setSelectedChart(chart)); expect(h.result.current.canUndo).toBe(false);
    act(() => h.result.current.handleDragEnd(7, { x: 240, y: 120 }));
    const screen = h.result.current.allCharts[0].position;
    act(() => h.result.current.updatePageLayout('tablet', resolvePageLayout(h.result.current.draftDashboard!, 'tablet')));
    const page = h.result.current.draftDashboard;
    act(() => h.result.current.undo()); expect(h.result.current.allCharts[0].position).toEqual(screen);
    expect(h.result.current.allCharts[0].config.pageLayout).toBeUndefined();
    act(() => h.result.current.undo()); expect(h.result.current.hasUnsavedChanges).toBe(false);
    act(() => { h.result.current.redo(); h.result.current.redo(); }); expect(h.result.current.draftDashboard).toEqual(page);
    act(() => h.result.current.handleSaveChartConfig({ titleColor: 'red' }));
    act(() => h.result.current.handleAddFromPanel(CHART_TYPES[0], { x: 0, y: 0 }));
    const added = h.result.current.allCharts[1];
    act(() => h.result.current.handleDeleteSelected(chart));
    act(() => h.result.current.undo()); expect(h.result.current.allCharts).toHaveLength(2);
    act(() => h.result.current.undo()); expect(h.result.current.allCharts).toHaveLength(1);
    act(() => h.result.current.redo()); expect(h.result.current.allCharts[1].id).toBe(added.id);
    act(() => h.result.current.undo());
    act(() => h.result.current.updateMetadata({ name: '新分支' })); expect(h.result.current.canRedo).toBe(false);
    h.unmount(); useDashboardStore.setState(old);
  });
  it('批量套用只产生一步历史，不同看板的历史互不影响', () => {
    const old = useDashboardStore.getState();
    useDashboardStore.setState({ dashboards: [88, 89].map(id => ({ id, charts: [chart], filters: {} } as Dashboard)) });
    const h = renderHook(({ id }) => useEditorCharts(id), { initialProps: { id: 88 } });
    act(() => h.result.current.transaction(() => {
      h.result.current.updateMetadata({ name: '批量' }); h.result.current.updateLocalChart(7, { name: '图表更新' });
    }));
    h.rerender({ id: 89 }); expect(h.result.current.canUndo).toBe(false);
    h.rerender({ id: 88 }); act(() => h.result.current.undo()); expect(h.result.current.hasUnsavedChanges).toBe(false);
    act(() => h.result.current.redo()); expect(h.result.current.allCharts[0].name).toBe('图表更新');
    h.unmount(); useDashboardStore.setState(old);
  });
  it('保存期间禁止撤销，全部失败保留历史，部分成功建立新基线防止重复新增', async () => {
    const old = useDashboardStore.getState();
    const put = vi.spyOn(client, 'put')
      .mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValueOnce({ data: { success: false, metadata_saved: false, created: {},
        results: [{ op: 'create', index: 0, chart_id: 91, ok: true }] } } as any);
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [], filters: {} } as unknown as Dashboard], loadDashboards: vi.fn().mockResolvedValue(undefined) });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.handleAddFromPanel(CHART_TYPES[0], { x: 0, y: 0 }));
    act(() => h.result.current.updateMetadata({ name: '待保存' }));
    let pending!: Promise<boolean>;
    act(() => { pending = h.result.current.saveAllChanges(); });
    act(() => h.result.current.undo()); expect(h.result.current.draftDashboard?.name).toBe('待保存');
    await act(async () => { await pending; }); expect(h.result.current.canUndo).toBe(true);
    await act(async () => { expect(await h.result.current.saveAllChanges()).toBe(false); });
    // 部分成功：已落库的新增从草稿剔掉并建立新基线，重试不会重复创建
    expect(h.result.current.canUndo).toBe(false); expect(h.result.current.canRedo).toBe(false);
    expect(h.result.current.allCharts.filter(c => c.id < 0)).toHaveLength(0);
    await act(async () => { h.result.current.saveAllChanges(); });
    expect((put.mock.calls[2][1] as any).added).toHaveLength(0);
    expect((put.mock.calls[2][1] as any).metadata).toEqual({ name: '待保存' });
    h.unmount(); useDashboardStore.setState(old);
  });
  it('编辑器按钮和快捷键贯通两种模式，文本输入保留原生撤销，预览保持真实宽度', () => {
    const old = useDashboardStore.getState();
    useDashboardStore.setState({ dashboards: [{ id: 88, name: '测试看板', charts: [chart], filters: {} } as Dashboard], loadDashboards: vi.fn().mockResolvedValue(undefined) });
    const h = render(<MemoryRouter initialEntries={['/dashboard/editor/88']}><Routes><Route path="/dashboard/editor/:id" element={<DashboardEditor />} /></Routes></MemoryRouter>);
    expect(h.getByRole('button', { name: '撤销' })).toBeDisabled();
    expect(h.getByRole('button', { name: '网格线' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(h.getByRole('button', { name: '恢复原始比例' }));
    const cell = h.container.querySelector('[data-chart-id="7"]')!;
    pointer(cell, 'pointerdown', 20, 20); pointer(window, 'pointerup', 145, 68);
    expect(h.getByRole('button', { name: '撤销' })).not.toBeDisabled();
    fireEvent.keyDown(h.getByLabelText('属性输入'), { key: 'z', ctrlKey: true });
    expect(h.getByRole('button', { name: '撤销' })).not.toBeDisabled();
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true }); expect(h.getByRole('button', { name: '撤销' })).toBeDisabled();
    fireEvent.keyDown(window, { key: 'z', metaKey: true, shiftKey: true }); expect(h.getByRole('button', { name: '撤销' })).not.toBeDisabled();
    fireEvent.click(h.getByRole('button', { name: '撤销' }));
    fireEvent.keyDown(window, { key: 'y', ctrlKey: true }); expect(h.getByRole('button', { name: '重做' })).toBeDisabled();
    fireEvent.keyDown(window, { key: 'g' }); expect(h.queryByLabelText('页面参考点')).toBeNull();
    fireEvent.click(h.getByRole('button', { name: '拖动画布' })); expect(h.getByRole('button', { name: '拖动画布' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(window, { key: 'v' }); expect(h.getByRole('button', { name: '选择图表' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.change(h.getByLabelText('布局模式'), { target: { value: 'screen' } });
    expect(h.container.querySelector('.adh-canvas')).toBeTruthy();
    expect(h.getByRole('button', { name: '适应编辑窗口' })).toBeTruthy();
    fireEvent.change(h.getByLabelText('布局模式'), { target: { value: 'page' } });
    fireEvent.click(h.getByRole('button', { name: '草稿预览' }));
    expect(h.getByRole('dialog').innerHTML).not.toContain('scale(');
    h.unmount(); useDashboardStore.setState(old);
  });
});

describe('空画布引导', () => {
  const renderEmptyEditor = () => {
    const old = useDashboardStore.getState();
    useDashboardStore.setState({ dashboards: [{ id: 88, name: '空看板', charts: [], filters: {} } as unknown as Dashboard],
      loadDashboards: vi.fn().mockResolvedValue(undefined) });
    const view = render(<MemoryRouter initialEntries={['/dashboard/editor/88']}><Routes><Route path="/dashboard/editor/:id" element={<DashboardEditor />} /></Routes></MemoryRouter>);
    return { ...view, restore: () => { view.unmount(); useDashboardStore.setState(old); } };
  };
  // 「从数据集引入」与「套用模板」是两个不同动作：前者只能把左侧库切到数据集页签，不能弹模板弹窗
  it('页面模式下从数据集引入切到数据集页签，不弹模板弹窗', () => {
    const h = renderEmptyEditor();
    expect(h.getByText('这个断点还是空的')).toBeTruthy();
    fireEvent.click(h.getByRole('button', { name: '从数据集引入' }));
    expect(h.getByTestId('component-library').getAttribute('data-tab')).toBe('datasets');
    expect(h.queryByTestId('dashboard-templates')).toBeNull();
    fireEvent.click(h.getByRole('button', { name: '套用模板' }));
    expect(h.getByTestId('dashboard-templates')).toBeTruthy();
    h.restore();
  });
  it('大屏模式下同样不把“从数据集引入”当模板入口', () => {
    const h = renderEmptyEditor();
    fireEvent.change(h.getByLabelText('布局模式'), { target: { value: 'screen' } });
    expect(h.getByText('大屏画布还是空的')).toBeTruthy();
    fireEvent.click(h.getByRole('button', { name: '从数据集引入' }));
    expect(h.getByTestId('component-library').getAttribute('data-tab')).toBe('datasets');
    expect(h.queryByTestId('dashboard-templates')).toBeNull();
    h.restore();
  });
});

describe('全部类型的字模预览', () => {
  it.each(CHART_TYPES.map(t => t.value))('%s 有可识别形状或控件示意，空色板也不会崩溃', type => {
    const view = render(createElement(ShapePreview, { type, palette: [], cfg: {} }));
    expect(view.container.innerHTML).not.toMatch(/NaN|undefined/);
    expect(view.container.textContent).not.toBe(type);
  });
  it('主题包同时展示柱、线、饼而非单个柱状样本', () => {
    const view = render(createElement(Preview, { c: pack }));
    expect(view.container.querySelector('polyline')).toBeTruthy();
    expect(view.container.innerHTML).toContain('conic-gradient');
  });
});
