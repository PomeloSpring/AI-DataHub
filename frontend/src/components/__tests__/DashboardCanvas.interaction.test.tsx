import { createElement, useState } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import DashboardEditor from '@/pages/DashboardEditor';
import { act, cleanup, fireEvent, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as G2 from '@antv/g2';
import DashboardChart, { CHART_TYPES } from '../DashboardChart';
import { DashboardCanvas, DashboardChartCard, ResponsiveDashboardCanvas } from '../DashboardCanvas';
import PageLayoutEditor from '../editor/PageLayoutEditor';
import { resolvePageLayout, movePageItem, pageCanvasSize, pagePixelRect, type PageBreakpoint } from '@/lib/dashboardPageLayout';
import Preview, { ShapePreview } from '../VisComponentPreview';
import { useCanvasInteraction } from '@/hooks/useCanvasInteraction';
import { useEditorCharts } from '@/hooks/useEditorCharts';
import { applyScreenComponent, resolveDashboardDesign } from '@/lib/dashboardDesign';
import { useDashboardStore, type Dashboard, type DashboardChart as ChartModel } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';
import client from '@/api/client';

const editorLibrary = vi.hoisted(() => ({ items: [], error: null, refresh: vi.fn() }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => editorLibrary }));
vi.mock('@/components/ChartConfigPanel', () => ({ default: () => null }));
vi.mock('@/components/DashboardTemplates', () => ({ default: () => null }));
vi.mock('@/components/editor/ComponentLibrary', () => ({ default: () => null }));
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
function pageInteraction(dashboard = { id: 88, filters: {}, charts: [chart] } as Dashboard, autoFit = false) {
  let state!: ReturnType<typeof useCanvasInteraction>;
  let changeBreakpoint!: (bp: PageBreakpoint) => void;
  let toggleGrid!: () => void;
  const onChange = vi.fn(), onAdd = vi.fn(), onSelect = vi.fn(), onConfig = vi.fn(), onDelete = vi.fn();
  const items: VisComponent[] = [];
  function Harness() {
    const [bp, setBp] = useState<PageBreakpoint>('desktop');
    const [grid, setGrid] = useState(true);
    changeBreakpoint = setBp; toggleGrid = () => setGrid(value => !value);
    state = useCanvasInteraction({ allCharts: dashboard.charts, onDragEnd: vi.fn(), onResizeEnd: vi.fn(), onSelectElement: onSelect,
      autoFit, viewportKey: bp, viewportSize: pageCanvasSize(resolvePageLayout(dashboard, bp), bp) });
    return <PageLayoutEditor dashboard={dashboard} items={items} breakpoint={bp} selectedId={7} canvas={state} showGrid={grid} panMode={false}
      onSelect={onSelect} onConfig={onConfig} onDelete={onDelete} onChange={onChange} onAdd={onAdd} />;
  }
  const view = render(<Harness />);
  return { ...view, onChange, onAdd, onSelect, onConfig, onDelete, state: () => state, changeBreakpoint: (bp: PageBreakpoint) => changeBreakpoint(bp), toggleGrid: () => toggleGrid() };
}
const pointer = (target: Element | Window, type: string, x: number, y: number, extra: PointerEventInit = {}) =>
  fireEvent(target, new PointerEvent(type, { bubbles: true, pointerId: 1, button: 0, clientX: x, clientY: y, ...extra }));

describe('指针交互回归', () => {
  it('点击不移动、不吸附、不产生位置保存', () => {
    const h = interaction();
    pointer(h.getByTestId('cell'), 'pointerdown', 200, 200);
    pointer(window, 'pointerup', 201, 201);
    expect(h.onSelectElement).toHaveBeenCalledWith(chart);
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
  it('缩放不会将图表压成零高，最小尺寸受保护', () => {
    const h = interaction();
    act(() => h.state().setSnapEnabled(false));
    pointer(h.getByTestId('resize'), 'pointerdown', 500, 400);
    pointer(window, 'pointerup', -100, -100);
    expect(h.onResizeEnd).toHaveBeenCalledWith(7, expect.objectContaining({ w: 200, h: 150 }));
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
    expect(h.result.current.draftDashboard?.filters.design.page.version).toBe(1);
    act(() => h.result.current.setSelectedChart(chart));
    act(() => h.result.current.handleSaveChartConfig({ xCol: 'name', yCol: 'value' }));
    expect(h.result.current.allCharts[0].config.pageLayout.tablet.w).toBe(6);
    act(() => h.result.current.updatePageLayout('tablet'));
    expect(h.result.current.allCharts[0].config.pageLayout.tablet).toBeUndefined();
    act(() => h.result.current.handleDeleteSelected(chart));
    expect(h.result.current.allCharts).toHaveLength(0);
    h.unmount(); useDashboardStore.setState(old);
  });
  it('新图表临时 ID 转正式 ID 保留布局；失败重试不重复创建已成功图表', async () => {
    const old = useDashboardStore.getState();
    const addChart = vi.fn().mockResolvedValue(91), updateDashboard = vi.fn().mockRejectedValueOnce(new Error('fail')).mockResolvedValue(undefined);
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [], filters: {} } as unknown as Dashboard], addChart, updateDashboard, loadDashboards: vi.fn().mockResolvedValue(undefined) });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.handleAddFromPanel(CHART_TYPES[0], { x: 0, y: 0 }));
    act(() => h.result.current.updatePageLayout('mobile', resolvePageLayout(h.result.current.draftDashboard!, 'mobile')));
    let saved: boolean | undefined;
    await act(async () => { saved = await h.result.current.saveAllChanges(); });
    expect(saved).toBe(false);
    expect(h.result.current.hasUnsavedChanges).toBe(true);
    expect(addChart.mock.calls[0][1].config.pageLayout.mobile.w).toBe(1);
    await act(async () => { saved = await h.result.current.saveAllChanges(); });
    expect(saved).toBe(true);
    expect(addChart).toHaveBeenCalledTimes(1);
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
    vi.spyOn(client, 'post').mockImplementation(async (_url, payload) => {
      saved = { ...saved, charts: [{ ...(payload as ChartModel), id: 91 }] };
      return { data: { id: 91 } };
    });
    vi.spyOn(client, 'put').mockImplementation(async (_url, payload) => { saved = { ...saved, ...(payload as Partial<Dashboard>) }; return { data: saved }; });
    vi.spyOn(client, 'get').mockImplementation(async () => ({ data: [structuredClone(saved)] }));
    useDashboardStore.setState({ dashboards: [saved], currentWorkspaceId: 0 });
    const h = renderHook(() => useEditorCharts(88));
    const pageLayout = { version: 1, desktop: { x: 0, y: 0, w: 6, h: 20 }, mobile: { x: 0, y: 0, w: 1, h: 20 } };
    act(() => { h.result.current.handleAddFromPanel(CHART_TYPES[0], { x: 120, y: 640 }, { pageLayout }); });
    expect(h.result.current.draftDashboard?.filters.design.page.version).toBe(1);
    await act(async () => { expect(await h.result.current.saveAllChanges()).toBe(true); });
    expect(h.result.current.hasUnsavedChanges).toBe(false);
    h.unmount();
    const reloaded = renderHook(() => useEditorCharts(88));
    expect(reloaded.result.current.allCharts[0].id).toBe(91);
    expect(reloaded.result.current.allCharts[0].config.pageLayout).toEqual(pageLayout);
    expect(reloaded.result.current.allCharts[0].position).toMatchObject({ x: 120, y: 640 });
    expect(reloaded.result.current.draftDashboard?.filters.design.page.version).toBe(1);
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
    viewport.scrollLeft = (1440 + 16) / 12 * view.state().scale;
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
    pointer(window, 'pointermove', 20, 20 + rect.y * 24 * h.state().scale); flush();
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
    pointer(window, 'pointerup', 20 + (1440 + 16) / 12 / 2, 44);
    expect(h.onChange).toHaveBeenCalledTimes(1);
    expect(h.onChange.mock.calls[0][0][0].rect).toMatchObject({ x: original.x + 1, y: original.y + 2 });
    expect(frames.size).toBe(0); expect(h.state().isInteracting()).toBe(false);
    expect(h.getByLabelText('页面网格线')).toBeTruthy();
    act(() => h.toggleGrid()); expect(h.queryByLabelText('页面网格线')).toBeNull();
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
  it('页面调整尺寸不逐帧重建图表，缩放和平移后新增落点正确', () => {
    const h = pageInteraction();
    act(() => { h.state().zoomTo(0.5); h.state().setPanOffset({ x: 70, y: 90 }); }); flush();
    const created = vi.mocked(G2.Chart).mock.calls.length;
    pointer(h.getByLabelText('调整页面图表大小'), 'pointerdown', 200, 200);
    pointer(window, 'pointermove', 230, 224); flush();
    expect(vi.mocked(G2.Chart).mock.calls).toHaveLength(created);
    pointer(window, 'pointerup', 230, 224);
    expect(h.onChange).toHaveBeenCalledTimes(1);
    const original = resolvePageLayout({ filters: {}, charts: [chart] }, 'desktop')[0].rect;
    expect(h.onChange.mock.calls[0][0][0].rect.h).toBe(original.h + 2);
    vi.spyOn(h.getByTestId('page-editor-surface'), 'getBoundingClientRect').mockReturnValue({ left: 50, top: 60, width: 720 } as DOMRect);
    const position = pagePixelRect({ x: 2, y: 4, w: 1, h: 1 }, 'desktop');
    fireEvent(h.getByLabelText('页面布局编辑区'), new MouseEvent('drop', { bubbles: true, clientX: 51 + position.x / 2, clientY: 61 + position.y / 2 }));
    expect(h.onAdd).toHaveBeenCalledExactlyOnceWith({ x: 2, y: 4 });
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
    let fail!: (reason: Error) => void;
    const addChart = vi.fn().mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; })).mockResolvedValue(91);
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [], filters: {} } as unknown as Dashboard], addChart,
      updateDashboard: vi.fn().mockRejectedValue(new Error('失败')), loadDashboards: vi.fn().mockResolvedValue(undefined) });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.handleAddFromPanel(CHART_TYPES[0], { x: 0, y: 0 }));
    act(() => h.result.current.updateMetadata({ name: '待保存' }));
    let pending!: Promise<boolean>;
    act(() => { pending = h.result.current.saveAllChanges(); });
    act(() => h.result.current.undo()); expect(h.result.current.draftDashboard?.name).toBe('待保存');
    await act(async () => { fail(new Error('失败')); await pending; }); expect(h.result.current.canUndo).toBe(true);
    await act(async () => { expect(await h.result.current.saveAllChanges()).toBe(false); });
    expect(h.result.current.canUndo).toBe(false); expect(h.result.current.canRedo).toBe(false);
    act(() => h.result.current.undo()); expect(h.result.current.allCharts).toHaveLength(0);
    expect(h.result.current.hasUnsavedChanges).toBe(true);
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
    fireEvent.keyDown(window, { key: 'g' }); expect(h.queryByLabelText('页面网格线')).toBeNull();
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
