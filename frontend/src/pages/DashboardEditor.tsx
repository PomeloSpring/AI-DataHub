import { useState, useEffect, useContext, useRef, useMemo } from 'react';
import { useParams, useNavigate, useLocation, UNSAFE_NavigationContext } from 'react-router-dom';
import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, pointerWithin, useDroppable, useSensor, useSensors, type DragEndEvent, type DragMoveEvent, type DragStartEvent } from '@dnd-kit/core';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { useDashboardStore } from '@/stores/dashboardStore';
import ChartConfigPanel from '@/components/ChartConfigPanel';
import DashboardTemplates from '@/components/DashboardTemplates';
import ComponentLibrary from '@/components/editor/ComponentLibrary';
import PropertyPanel from '@/components/editor/PropertyPanel';
import EditorToolbar from '@/components/editor/EditorToolbar';
import PageLayoutEditor from '@/components/editor/PageLayoutEditor';
import SelectionOverlay from '@/components/editor/SelectionOverlay';
import CanvasHint from '@/components/editor/CanvasHint';
import { useViewportSize } from '@/hooks/useOverlayPosition';
import { CANVAS_COLORS, backdropStyle, canvasRectToScreen, dotBackgroundStyle } from '@/lib/canvasTheme';
import { LIBRARY_DROP_ZONE, dragPointer, type DropGhost, type LibraryDragData } from '@/lib/editorDrag';
import { useMergedRefs } from '@/lib/mergeRefs';
import { PAGE_WIDTHS, PAGE_LABELS, PAGE_COLUMNS, PAGE_ROW, defaultPageSpan, nextPageRow, pageCanvasSize, pageGap, pageMinHeight, pagePixelRect, pageRows, resolvePageLayout, movePageItem, withPageRect, type PageBreakpoint } from '@/lib/dashboardPageLayout';
import { DashboardCanvas, CanvasRulers, FittedDashboardCanvas, ResponsiveDashboardCanvas, DashboardThumbnail } from '@/components/DashboardCanvas';
import { useCanvasInteraction, DEFAULT_CHART_SIZE, DEFAULT_WIDGET_SIZE } from '@/hooks/useCanvasInteraction';
import { useEditorCharts } from '@/hooks/useEditorCharts';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { useThemeStore } from '@/stores/themeStore';
import { createVisComponent, VIS_CATEGORIES, type VisCategory, type VisComponent } from '@/api/visLibrary';
import { applyChartComponent, applyScreenComponent, applyLayout, chartRect, chartZIndex, clampPosition, compatible, getCanvas, minSizeOf,
  resolveChartDesign, resolveDashboardDesign, sanitizeStyle, sanitizePack, screenToCanvas, visualConfig, legacyCanvasFilters,
  clearChartVisualOverrides, libraryThemeFilters, themePackFor } from '@/lib/dashboardDesign';

function ChangeGroup({ title, charts, onPick }: { title: string; charts: any[]; onPick: (chart: any) => void }) {
  if (!charts.length) return null;
  return <div className="space-y-1">
    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</div>
    {charts.map(c => <button key={c.id} type="button" className="block w-full truncate rounded px-2 py-1 text-left text-xs hover:bg-muted" onClick={() => onPick(c)}>
      {c.name}
    </button>)}
  </div>;
}

function useUnsavedGuard(dirty: boolean, saving: boolean) {
  const { navigator } = useContext(UNSAFE_NavigationContext);
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const savingRef = useRef(saving); savingRef.current = saving;
  useEffect(() => {
    const confirm = () => !savingRef.current && (!dirtyRef.current || window.confirm('有未保存的更改，确定离开并放弃这些更改吗？'));
    const push = navigator.push, replace = navigator.replace;
    navigator.push = (...args) => { if (confirm()) push.apply(navigator, args); };
    navigator.replace = (...args) => { if (confirm()) replace.apply(navigator, args); };
    let index = window.history.state?.idx ?? 0;
    let restoring = false;
    const pop = (e: PopStateEvent) => {
      const next = e.state?.idx ?? 0;
      if (restoring) { restoring = false; e.stopImmediatePropagation(); return; }
      if (!confirm()) {
        e.stopImmediatePropagation(); restoring = true; window.history.go(index - next);
      } else index = next;
    };
    const unload = (e: BeforeUnloadEvent) => { if (dirtyRef.current || savingRef.current) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('popstate', pop, true);
    window.addEventListener('beforeunload', unload);
    return () => { navigator.push = push; navigator.replace = replace; window.removeEventListener('popstate', pop, true); window.removeEventListener('beforeunload', unload); };
  }, [navigator]);
  return () => { dirtyRef.current = false; };
}
export default function DashboardEditor() {
  const { id } = useParams();
  // 路由切换重新挂载工作台，草稿不会串入其他看板。
  return <EditorWorkspace key={id} dashboardId={Number(id)} />;
}
function EditorWorkspace({ dashboardId }: { dashboardId: number }) {
  const navigate = useNavigate();
  const location = useLocation();
  const store = useDashboardStore();
  const charts = useEditorCharts(dashboardId);
  const library = useVisLibrary();
  const { current, draftDashboard, allCharts, selectedChart, selectedElementType, saving, hasUnsavedChanges } = charts;
  const interactive = charts.interactionMode === 'interact';
  const dimensions = getCanvas(draftDashboard?.filters, allCharts);
  const theme = useThemeStore(s => s.theme);
  const design = useMemo(() => resolveDashboardDesign(legacyCanvasFilters(draftDashboard?.filters), library.items, { theme }), [draftDashboard?.filters, library.items, theme]);
  const [leftOpen, setLeftOpen] = useState(() => window.innerWidth >= 1100);
  const [rightOpen, setRightOpen] = useState(() => window.innerWidth >= 1300);
  const [showGrid, setShowGrid] = useState(true);
  const [showRulers, setShowRulers] = useState(true);
  const [panMode, setPanMode] = useState(false);
  const [propertyTab, setPropertyTab] = useState('appearance');
  const [libraryTab, setLibraryTab] = useState('vis');
  const [preview, setPreview] = useState(false);
  const [layoutMode, setLayoutMode] = useState<'page' | 'screen'>('page');
  const [breakpoint, setBreakpoint] = useState<PageBreakpoint>('desktop');
  const pageLayout = useMemo(() => {
    try { return { items: draftDashboard ? resolvePageLayout(draftDashboard, breakpoint) : [], error: '' }; }
    catch (e) { return { items: [], error: e instanceof Error ? e.message : '页面布局无效' }; }
  }, [draftDashboard?.filters, allCharts, breakpoint]);
  const viewportSize = layoutMode === 'page' ? pageCanvasSize(pageLayout.items, breakpoint) : dimensions;
  const canvas = useCanvasInteraction({ allCharts, onDragEnd: charts.handleDragEnd, onResizeEnd: charts.handleResizeEnd,
    onSelectElement: chart => charts.handleSelectElement(chart), autoFit: !!current, panMode, interactionLocked: saving || preview,
    viewportKey: `${dashboardId}:${layoutMode}:${breakpoint}`, viewportSize });
  // 破坏性操作统一走应用内 Dialog（与整体设计一致）；离开页面的拦截仍用原生 confirm，
  // 因为 popstate 必须同步判定，改成异步会丢失「阻止导航」能力。
  const [confirmAction, setConfirmAction] = useState<{ title: string; description: string; actionLabel: string; run: () => void } | null>(null);
  const [changesOpen, setChangesOpen] = useState(false);
  const askConfirm = (title: string, description: string, actionLabel: string, run: () => void) =>
    setConfirmAction({ title, description, actionLabel, run });
  const resetPage = () => {
    askConfirm('恢复自动适配', `将清除${PAGE_LABELS[breakpoint]}断点的手动布局，其他断点与大屏坐标不受影响。`, '恢复自动适配',
      () => charts.updatePageLayout(breakpoint));
  };
  const capturePage = () => {
    if (pageLayout.error) { toast.error(pageLayout.error); return; }
    charts.updatePageLayout(breakpoint, pageLayout.items);
  };
  const [layout, setLayout] = useState<VisComponent | null>(null);
  const [pack, setPack] = useState<VisComponent | null>(null);
  const [followTheme, setFollowTheme] = useState(false);
  const [replaceStyles, setReplaceStyles] = useState(true);
  const [saveVis, setSaveVis] = useState(false);
  const [visName, setVisName] = useState('');
  const [visCategory, setVisCategory] = useState<VisCategory>('chart_style');
  const [visSaving, setVisSaving] = useState(false);
  const [dropGhost, setDropGhost] = useState<DropGhost | null>(null);
  const [overCanvas, setOverCanvas] = useState(false);
  const [libraryDrag, setLibraryDrag] = useState<LibraryDragData | null>(null);
  const dragData = useRef<LibraryDragData | null>(null);
  const dropZone = useDroppable({ id: LIBRARY_DROP_ZONE });
  // 必须是稳定身份的合并 ref：内联 mergeRefs(...) 会每帧新建，
  // React 先用 null 调旧回调会注销掉 dnd-kit 的落点节点，拖拽中就再也对不上 over。
  const canvasDropRef = useMergedRefs<HTMLDivElement>(canvas.canvasRef, dropZone.setNodeRef);
  // Pointer sensor 让触摸设备也能从库拖入（HTML5 DnD 不支持触摸）；Keyboard sensor 供键盘拖拽
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor));
  const approveNavigation = useUnsavedGuard(hasUnsavedChanges, saving);
  useEffect(() => { void store.loadDashboards(0); }, []);
  useEffect(() => { if (current) store.setCurrent(dashboardId); }, [current?.id, dashboardId]);
  useEffect(() => { canvas.setGridSize(dimensions.gridSize); }, [dimensions.gridSize]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const editing = e.target instanceof Element && e.target.closest('input,textarea,select,[contenteditable="true"],.cm-editor');
      if (e.defaultPrevented || saving || canvas.isInteracting() || editing || document.querySelector('[role="dialog"],[role="alertdialog"],[role="menu"]')) return;
      const key = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && !e.altKey) {
        if (key === 'z') { e.preventDefault(); if (e.shiftKey) charts.redo(); else charts.undo(); }
        if (key === 'y') { e.preventDefault(); charts.redo(); }
        return;
      }
      if (e.altKey) return;
      if (key === 'delete' && charts.selectedIds.length) {
        e.preventDefault();
        if (charts.selectedIds.length > 1) charts.handleDeleteMany(charts.selectedCharts);
        else if (selectedChart) charts.handleDeleteSelected(selectedChart);
      }
      // 方向键微调：选中集整体移动（page 按 1 网格单位，screen 按 gridSize；Shift 走最小单位）
      if (charts.selectedIds.length && ['arrowup', 'arrowdown', 'arrowleft', 'arrowright'].includes(key)) {
        e.preventDefault();
        const step = e.shiftKey ? 1 : 0;
        nudge(key === 'arrowleft' ? -1 : key === 'arrowright' ? 1 : 0, key === 'arrowup' ? -1 : key === 'arrowdown' ? 1 : 0, step);
      }
      if (key === 'f') { e.preventDefault(); canvas.resetZoom(); }
      if (key === '+' || key === '=') { e.preventDefault(); canvas.zoomIn(); }
      if (key === '-') { e.preventDefault(); canvas.zoomOut(); }
      if (key === 'h') setPanMode(true);
      if (key === 'v') setPanMode(false);
      if (key === 'i') { e.preventDefault(); charts.setInteractionMode(interactive ? 'select' : 'interact'); }
      if (key === 'g') setShowGrid(value => !value);
      if (key === 'r') setShowRulers(value => !value);
      if (key === 'escape') charts.handleSelectElement(null);
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, [saving, selectedChart, charts.handleDeleteSelected, charts.undo, charts.redo, canvas.resetZoom, canvas.zoomIn, canvas.zoomOut, canvas.isInteracting, interactive]);
  /** 选中集整体移动：page 走网格行列，screen 走像素（step>0 时按该像素量，否则按网格/1 行） */
  const nudge = (dx: number, dy: number, step: number) => {
    if (!charts.selectedIds.length || (dx === 0 && dy === 0)) return;
    if (layoutMode === 'page') {
      if (pageLayout.error) return;
      charts.transaction(() => {
        let items = pageLayout.items;
        for (const chart of charts.selectedCharts) {
          const item = items.find(i => i.chart.id === chart.id);
          if (item) items = movePageItem(items, chart.id, { ...item.rect, x: item.rect.x + dx, y: item.rect.y + dy }, breakpoint);
        }
        charts.updatePageLayout(breakpoint, items);
      });
      return;
    }
    const unit = step > 0 ? step : dimensions.gridSize;
    charts.transaction(() => {
      for (const chart of charts.selectedCharts) {
        const r = chartRect(chart);
        charts.handleDragEnd(chart.id, { x: r.x + dx * unit, y: r.y + dy * unit });
      }
    });
  };
  /** 图层面板动作：叠放顺序（仅大屏）/ 显隐 / 锁定 / 就地重命名 */
  const layerAction = (chart: any, action: 'up' | 'down' | 'hide' | 'show' | 'lock' | 'unlock' | 'rename', name?: string) => {
    if (!draftDashboard || saving) return;
    if (action === 'rename') { if (name) charts.handlePropertyChange(chart.id, 'name', name); return; }
    if (action === 'up' || action === 'down') {
      const ordered = allCharts.slice().sort((a, b) => chartZIndex(a) - chartZIndex(b) || a.id - b.id);
      const from = ordered.findIndex(c => c.id === chart.id);
      const to = action === 'up' ? from + 1 : from - 1;
      if (from < 0 || to < 0 || to >= ordered.length) return;
      charts.transaction(() => {
        const next = ordered.slice();
        next.splice(to, 0, next.splice(from, 1)[0]);
        // 重排后按序号回写，避免 z 值无限增长或并列
        next.forEach((c, index) => {
          if (chartZIndex(c) !== index) charts.updateLocalChart(c.id, { config: { ...(c.config || {}), zIndex: index } });
        });
      });
      return;
    }
    const key = action === 'hide' || action === 'show' ? 'hidden' : 'locked';
    const value = action === 'hide' || action === 'lock';
    charts.updateLocalChart(chart.id, { config: { ...(chart.config || {}), [key]: value } });
  };
  const applyVis = (component: VisComponent) => {
    if (!draftDashboard || saving) return;
    if (component.category === 'layout_template') {
      if (layoutMode === 'page') { toast.info('此模板用于大屏像素布局，请切换到大屏布局后应用'); return; }
      setLayout(component); return;
    }
    if (component.category === 'theme_pack') { setFollowTheme(false); setReplaceStyles(true); setPack(component); return; }
    if (component.category === 'chart_style' || component.category === 'kpi_card' ||
      (selectedChart && ['decoration_frame', 'color_theme'].includes(component.category))) {
      if (!selectedChart) { toast.info('请先选择兼容图表，或将字模拖入画布'); return; }
      if (!compatible(component, selectedChart.chart_type)) { toast.error('字模与当前图表类型不兼容，未改变图表或查询'); return; }
      charts.updateLocalChart(selectedChart.id, { config: applyChartComponent(selectedChart.config, component, library.items) });
    } else charts.updateMetadata({ filters: applyScreenComponent(draftDashboard.filters, component) });
  };
  /**
   * 库→画布统一走 dnd-kit（Pointer + Keyboard sensor），与画布内移动共用一套坐标换算：
   * 旧的 HTML5 DnD 在触摸设备上完全不可用，且 page 模式没有任何落点预览。
   */
  const ghostAt = (clientX: number, clientY: number, data: LibraryDragData): DropGhost | null => {
    const rect = canvas.surfaceRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const point = screenToCanvas({ x: clientX, y: clientY }, rect, canvas.scale);
    if (layoutMode === 'screen') {
      const size = data.item.category === 'widget' ? DEFAULT_WIDGET_SIZE : DEFAULT_CHART_SIZE;
      const pos = clampPosition({ x: point.x - size.w / 2, y: point.y - size.h / 2 }, size, dimensions, dimensions.gridSize);
      return { mode: 'screen', ...pos, w: size.w, h: size.h };
    }
    const gap = pageGap(breakpoint), column = (PAGE_WIDTHS[breakpoint] + gap) / PAGE_COLUMNS[breakpoint], rowUnit = PAGE_ROW + gap;
    const w = defaultPageSpan(data.item.value, breakpoint), h = pageRows(pageMinHeight(data.item.value), breakpoint);
    return { mode: 'page', w, h, chartType: data.item.value,
      x: Math.min(PAGE_COLUMNS[breakpoint] - w, Math.max(0, Math.floor(point.x / column))),
      y: Math.max(0, Math.floor(point.y / rowUnit)) };
  };
  const addItem = (data: LibraryDragData, ghost: DropGhost | null) => {
    const { item, component } = data;
    if (layoutMode === 'page') {
      // rect.y/h 是**行**单位（nextPageRow 明确了这一点）；chartRect 返回的是像素，
      // 混用会把新控件放到几万行外，画布高度狂飙到 2 万像素、看起来像“拖不进画布”
      const rect = ghost && ghost.mode === 'page'
        ? { x: ghost.x, y: ghost.y, w: ghost.w, h: ghost.h }
        : { x: 0, y: nextPageRow(pageLayout.items) + 1, w: defaultPageSpan(item.value, breakpoint), h: pageRows(pageMinHeight(item.value), breakpoint) };
      const config = component ? applyChartComponent({}, component) : {};
      // chart.position 是大屏视图共用的像素坐标，用换算后的值回填，两个视图才对得上
      const px = pagePixelRect(rect, breakpoint);
      charts.handleAddFromPanel(item, { x: px.x, y: px.y }, withPageRect(config, breakpoint, rect));
      return;
    }
    const bottomPx = allCharts.reduce((max, c) => Math.max(max, chartRect(c).y + chartRect(c).h), 0) + 16;
    const position = ghost && ghost.mode === 'screen' ? { x: ghost.x, y: ghost.y } : { x: 0, y: bottomPx };
    charts.handleAddFromPanel(item, position, component ? applyChartComponent({}, component) : {});
  };
  /** 指针是否落在编辑视口内：落点判定以坐标为准，不依赖 dnd-kit 的 over 注册 */
  const pointerInViewport = (x: number, y: number) => {
    const rect = canvas.canvasRef.current?.getBoundingClientRect();
    return !!rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  };
  /** 当前指针的客户端坐标（实时，而非拖拽起点）—— 换算逻辑见 dragPointer */
  const pointerOf = (e: DragMoveEvent | DragEndEvent) => dragPointer(e.activatorEvent, e.delta);
  const onLibraryDragStart = (e: DragStartEvent) => {
    const data = e.active.data.current as LibraryDragData | undefined;
    if (!data) return;
    dragData.current = data; setLibraryDrag(data);
  };
  const onLibraryDragMove = (e: DragMoveEvent) => {
    const data = dragData.current;
    const point = pointerOf(e);
    if (!data || !point || !pointerInViewport(point.x, point.y)) { setDropGhost(null); setOverCanvas(false); return; }
    setOverCanvas(true);
    setDropGhost(ghostAt(point.x, point.y, data));
  };
  const onLibraryDragEnd = (e: DragEndEvent) => {
    const data = dragData.current;
    dragData.current = null; setLibraryDrag(null); setDropGhost(null); setOverCanvas(false);
    if (!data) return;
    const point = pointerOf(e);
    // 松手时自己算落点：不能因为 over 丢失就把图表静默吞掉（曾经“缩回去”的根因）
    if (point) { if (pointerInViewport(point.x, point.y)) addItem(data, ghostAt(point.x, point.y, data)); return; }
    // 键盘拖拽没有坐标，才回落到 dnd-kit 的 over 判定
    if (e.over?.id === LIBRARY_DROP_ZONE) addItem(data, dropGhost);
  };
  // 控件的唯一配置入口是右侧属性面板：双击/齿轮不再弹出一个只配一半字段的对话框
  const openConfig = (chart: any) => {
    charts.setSelectedChart(chart);
    if (String(chart?.chart_type || '').startsWith('widget_')) {
      setPropertyTab('data'); setRightOpen(true);
      if (window.innerWidth < 1100) setLeftOpen(false);
      return;
    }
    charts.setConfigPanelOpen(true);
  };
  const returnTo = typeof location.state?.from === 'string' && /^\/(?!\/)/.test(location.state.from) && !location.state.from.includes('\\')
    ? location.state.from : '/system/dashboards';
  const layoutResult = layout ? applyLayout(allCharts, layout.style_config, dimensions) : null;
  const packFilters = draftDashboard && (pack || followTheme)
    ? replaceStyles || followTheme ? libraryThemeFilters(draftDashboard.filters, pack || undefined) : applyScreenComponent(draftDashboard.filters, pack!) : null;
  const packPreview = draftDashboard && packFilters ? { ...draftDashboard, filters: packFilters,
    charts: replaceStyles ? allCharts.map(c => ({ ...c, config: clearChartVisualOverrides(c.config) })) : allCharts } : null;
  const editorViewportSize = useViewportSize(canvas.canvasRef);
  const activeRect = selectedChart ? { ...chartRect(selectedChart),
    ...(canvas.draggingChart === selectedChart.id ? canvas.dragPosition : {}),
    ...(canvas.resizingChart === selectedChart.id ? canvas.resizePosition : {}) } : undefined;
  // 选中卡片的屏幕矩形：覆盖层（工具条/手柄）渲染在缩放层之外，尺寸不随缩放变化
  const selectedScreen = activeRect ? canvasRectToScreen(activeRect, dimensions, editorViewportSize, canvas.scale, canvas.panOffset) : null;
  // 空画布引导：「从数据集引入」打开左侧库并切到数据集页签（套用模板是另一个按钮，走模板弹窗）
  const openInsertGuide = () => { setLeftOpen(true); setLibraryTab('datasets'); };
  const layoutPreview = draftDashboard && layoutResult ? { ...draftDashboard, filters: { ...draftDashboard.filters,
    design: { ...draftDashboard.filters.design, canvas: { ...dimensions, ...draftDashboard.filters.design?.canvas, height: layoutResult.height } } },
    charts: allCharts.map(c => ({ ...c, position: layoutResult.positions[c.id] || c.position })) } : null;
  const persistVis = async () => {
    if (!visName.trim() || !draftDashboard || visSaving) return;
    if (visCategory === 'chart_style' && (!selectedChart || selectedChart.chart_type.startsWith('widget_'))) { toast.error('请选择图表'); return; }
    if (visCategory === 'kpi_card' && (!selectedChart || !compatible({ category: 'kpi_card' } as VisComponent, selectedChart.chart_type))) { toast.error('请选择指标卡'); return; }
    setVisSaving(true);
    try {
      const resolved = selectedChart ? resolveChartDesign(selectedChart.config, design, library.items, selectedChart.chart_type) : null;
      const style = visCategory === 'screen_background' ? { backgroundColor: '#f8fafc', ...design.background } : visCategory === 'color_theme' ? { palette: ['#111827', '#374151', '#6b7280', '#9ca3af'], ...(resolved?.visual || design.visual) }
        : visCategory === 'layout_template' ? { grid: { canvasW: dimensions.width, canvasH: dimensions.height,
          positions: allCharts.filter(c => !c.chart_type.startsWith('widget_')).map(c => c.position) } }
        : visCategory === 'theme_pack' ? { ...design.pack, ...design.visual,
          background: design.background, card: design.card,
          charts: { ...design.pack?.charts, ...(selectedChart && !selectedChart.chart_type.startsWith('widget_') ? { [selectedChart.chart_type]: visualConfig(resolved?.config) } : {}) },
          widgets: { ...design.pack?.widgets, ...(selectedChart?.chart_type.startsWith('widget_') ? { [selectedChart.chart_type]: resolved?.config.widgetStyle } : {}) } }
        : { config: visualConfig(resolved?.config || design.card) };
      await createVisComponent({ name: visName.trim(), category: visCategory,
        chart_type: visCategory === 'chart_style' ? selectedChart?.chart_type : undefined,
        style_config: visCategory === 'theme_pack' ? sanitizePack(style) : sanitizeStyle(style) });
      await library.refresh(); setSaveVis(false); toast.success('已保存为自定义字模');
    } catch { toast.error('字模保存失败，请重试'); } finally { setVisSaving(false); }
  };
  if (!current || !draftDashboard) return <div className="p-8">{store.loading ? '正在加载…' : '看板不存在'}<Button variant="link" onClick={() => navigate('/system/dashboards')}>返回列表</Button></div>;
  // 可写性服务端裁决: 角色授予的可见权不含编辑权 —— 非属主/非 admin 直接拒入编辑器(fail-loud),
  // 后端写接口同样按 check_dashboard_editable 拦截, 双层都不放行
  if (current.editable === false) return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <p className="text-lg font-medium">仅看板属主或管理员可编辑该看板</p>
      <p className="text-sm text-muted-foreground">该看板是按角色分配给你的，可见权不含编辑权；可以查看与大屏播放，但不能修改。</p>
      <div className="flex items-center gap-2">
        <Button onClick={() => navigate(returnTo)}>返回</Button>
        <Button variant="outline" onClick={() => navigate(`/system/dashboards/${current.id}`)}>查看看板</Button>
      </div>
    </div>
  );
  return <div className="flex h-screen flex-col overflow-hidden bg-background">
    <EditorToolbar dashboardName={draftDashboard.name} scale={canvas.scale} canvasSize={dimensions}
      layoutMode={layoutMode} onLayoutMode={setLayoutMode} breakpoint={breakpoint} onBreakpoint={setBreakpoint} onResetPage={resetPage} onCapturePage={capturePage}
      hasUnsavedChanges={hasUnsavedChanges} pendingCount={charts.pendingCount} pendingNewCount={allCharts.filter(c => c.id < 0).length}
      pendingChangeCount={charts.pendingChangeCount} pendingDeleteCount={charts.pendingDeleteCount}
      onOpenChanges={() => setChangesOpen(true)}
      leftPanelOpen={leftOpen} rightPanelOpen={rightOpen} onZoomIn={canvas.zoomIn} onZoomOut={canvas.zoomOut} onResetZoom={canvas.resetZoom}
      onActualSize={() => canvas.zoomTo(1)} canUndo={charts.canUndo} canRedo={charts.canRedo} onUndo={charts.undo} onRedo={charts.redo} panMode={panMode} onPanMode={setPanMode}
      onOpenTemplates={() => charts.setTemplatesOpen(true)} onSave={() => void charts.saveAllChanges()} onExit={() => navigate(returnTo)}
      onOpenLeftPanel={() => { setLeftOpen(true); if (window.innerWidth < 1100) setRightOpen(false); }} onOpenRightPanel={() => { setRightOpen(true); if (window.innerWidth < 1100) setLeftOpen(false); }} saving={saving}
      showGrid={showGrid} onToggleGrid={() => setShowGrid(v => !v)} onPreview={() => setPreview(true)}
      showRulers={showRulers} onToggleRulers={() => setShowRulers(v => !v)}
      interactionMode={charts.interactionMode} onInteractionMode={charts.setInteractionMode}
      snapEnabled={canvas.snapEnabled} onToggleSnap={() => canvas.setSnapEnabled(v => !v)}
      onUseLibrary={() => { if (!themePackFor(theme, library.items)) { toast.error('主题包尚未加载，请在字模库重试'); return; } setPack(null); setReplaceStyles(true); setFollowTheme(true); }}
      onSaveVis={() => { setVisName(''); setVisCategory(selectedChart ? 'chart_style' : 'screen_background'); setSaveVis(true); }} />
    <DndContext sensors={sensors} collisionDetection={pointerWithin} onDragStart={onLibraryDragStart} onDragMove={onLibraryDragMove} onDragEnd={onLibraryDragEnd}
      onDragCancel={() => { dragData.current = null; setLibraryDrag(null); setDropGhost(null); setOverCanvas(false); }}>
    <div className={`relative flex min-h-0 flex-1 ${saving ? 'pointer-events-none opacity-70' : ''}`}>
      <ComponentLibrary design={design} allCharts={allCharts} selectedChart={selectedChart} isOpen={leftOpen} onClose={() => setLeftOpen(false)}
        tab={libraryTab} onTab={setLibraryTab}
        onAddItem={(item, component) => addItem({ item, component }, null)}
        onSelectChart={charts.handleSelectElement} onApplyVis={applyVis} layoutMode={layoutMode} dirtyIds={charts.dirtyIds} onLayerAction={layerAction}
        onAddFromDataset={(ds) => {
          // 从数据集引入: 放在现有图表下方, 预设随数据集 chart_preset 搬入
          charts.handleAddFromDataset(ds, { x: 0, y: allCharts.reduce((bottom, c) => Math.max(bottom, chartRect(c).y + chartRect(c).h), 0) + 16 });
        }} />
      {layoutMode === 'page' ? <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="px-4 py-2 text-xs text-muted-foreground border-b" role="status">
          {!draftDashboard.filters?.design?.page ? '页面布局尚未保存，由大屏布局生成。' : pageLayout.items.some(item => item.automatic) ? `${PAGE_LABELS[breakpoint]}包含自动适配布局。` : `${PAGE_LABELS[breakpoint]}独立布局。`}
          {' '}设计宽度 {PAGE_WIDTHS[breakpoint]}px · 编辑画布可等比例缩放与平移，草稿预览和阅读页仍按真实宽度展示。
        </div>
        <PageLayoutEditor key={`${dashboardId}:${breakpoint}`} dashboard={draftDashboard} items={library.items} breakpoint={breakpoint} selectedIds={charts.selectedIds} canvas={canvas} showGrid={showGrid} panMode={panMode} interactive={interactive}
          showRulers={showRulers} onInsertFromLibrary={openInsertGuide} onOpenTemplates={() => charts.setTemplatesOpen(true)}
          dropRef={canvasDropRef} dropGhost={dropGhost?.mode === 'page' ? dropGhost : null}
          onSelect={charts.handleSelectElement} onConfig={openConfig} onDelete={charts.handleDeleteSelected}
          onChange={items => charts.updatePageLayout(breakpoint, items)} />
      </div> : <div ref={canvasDropRef} className="adh-canvas-viewport relative min-h-0 min-w-0 flex-1 overflow-hidden"
        data-panning={panMode || undefined} data-drag-over={overCanvas || undefined}
        style={{ touchAction: 'none', userSelect: 'none', ...backdropStyle(), ...dotBackgroundStyle(dimensions.gridSize) }}
        onPointerDownCapture={canvas.handlePanStart}>
        <DashboardCanvas ref={canvas.surfaceRef} dashboard={draftDashboard} items={library.items} showGrid={showGrid} draft interactive={interactive}
          style={{ position: 'absolute', left: '50%', top: '50%', marginLeft: -dimensions.width / 2, marginTop: -dimensions.height / 2,
            transform: `translate(${canvas.panOffset.x}px, ${canvas.panOffset.y}px) scale(${canvas.scale})`, transformOrigin: 'center',
            boxShadow: `0 0 0 1px ${CANVAS_COLORS.surfaceBorder}, ${CANVAS_COLORS.surfaceShadow}` }}
          renderChart={(chart, card) => {
            const dragging = canvas.draggingChart === chart.id, resizing = canvas.resizingChart === chart.id, selected = selectedChart?.id === chart.id;
            const original = chartRect(chart);
            const p = { ...original, ...(dragging ? canvas.dragPosition : {}), ...(resizing ? canvas.resizePosition : {}) };
            return <div key={chart.id} data-chart-id={chart.id} data-selected={charts.selectedIds.includes(chart.id) || undefined}
              className={`adh-cell adh-editable-card absolute left-0 top-0 ${panMode ? '' : 'cursor-move'}`}
              style={{ transform: `translate3d(${p.x}px, ${p.y}px, 0)`, width: p.w, height: p.h, zIndex: selected ? 10 : 1,
                willChange: dragging ? 'transform' : undefined, touchAction: 'none' }}
              onPointerDown={e => canvas.handleDragStart(e, chart.id)} onDoubleClick={() => { if (!panMode) openConfig(chart); }}>
              <div className="pointer-events-none relative min-h-0" style={{ width: original.w, height: original.h,
                transformOrigin: 'top left', transform: resizing ? `scale(${p.w / original.w}, ${p.h / original.h})` : undefined }}>{card}</div>
            </div>;
          }}>
          {dropGhost && dropGhost.mode === 'screen' && <div aria-label="图表放置预览" className="pointer-events-none absolute border-2 border-dashed bg-primary/10"
            style={{ left: dropGhost.x, top: dropGhost.y, width: dropGhost.w, height: dropGhost.h, borderColor: CANVAS_COLORS.dropGhostBorder }} />}
          {canvas.guides.map(g => <div key={`${g.axis}-${g.value}`} className="pointer-events-none absolute z-20"
            style={{ background: CANVAS_COLORS.guide,
              ...(g.axis === 'x' ? { left: g.value, top: 0, width: 1 / canvas.scale, height: dimensions.height } : { top: g.value, left: 0, height: 1 / canvas.scale, width: dimensions.width }) }} />)}
        </DashboardCanvas>
        {allCharts.length === 0 && <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
          <div className="pointer-events-auto max-w-sm rounded-lg border bg-background/95 p-4 text-center shadow-sm">
            <p className="text-sm font-medium">大屏画布还是空的</p>
            <p className="mt-1 text-xs text-muted-foreground">从左侧组件库拖入图表或控件；开启吸附后会自动对齐网格与邻居边缘。</p>
            <div className="mt-3 flex justify-center gap-2">
              <Button size="sm" variant="outline" onClick={openInsertGuide}>从数据集引入</Button>
              <Button size="sm" variant="outline" onClick={() => charts.setTemplatesOpen(true)}>套用模板</Button>
            </div>
          </div>
        </div>}
        {selectedScreen && selectedChart && <SelectionOverlay screen={selectedScreen} size={activeRect ?? undefined} minimum={minSizeOf(selectedChart.chart_type)}
          readout={activeRect ? `${Math.round(activeRect.w)}×${Math.round(activeRect.h)}` : undefined}
          onConfig={() => openConfig(selectedChart)} onDelete={() => charts.handleDeleteSelected(selectedChart)}
          onResizeStart={e => canvas.handleResizeStart(e, selectedChart.id)}
          onNudge={(dx, dy) => { const r = chartRect(selectedChart); charts.handleResizeEnd(selectedChart.id, { ...r, w: r.w + dx, h: r.h + dy }); }} />}
        <CanvasRulers canvas={dimensions} scale={canvas.scale} pan={canvas.panOffset} active={activeRect} visible={showRulers} />
        <CanvasHint
          text={activeRect ? `X ${Math.round(activeRect.x)} · Y ${Math.round(activeRect.y)} · W ${Math.round(activeRect.w)} · H ${Math.round(activeRect.h)}` : '滚轮缩放 · 空格拖动 / 中键平移 · 双击配置 · F 适应窗口'}
          extra={canvas.snapEnabled ? `自动吸附 · 网格 ${dimensions.gridSize}px · Alt 临时关闭` : '自由移动 · 吸附已关闭'} />
      </div>}
      <PropertyPanel pageBreakpoint={layoutMode === 'page' ? breakpoint : undefined} pageRect={pageLayout.items.find(item => item.chart.id === selectedChart?.id)?.rect} onResetPage={resetPage} isOpen={rightOpen} onClose={() => setRightOpen(false)} selectedChart={selectedChart} selectedElementType={selectedElementType}
        canvasSize={dimensions} setCanvasSize={size => charts.updateCanvas(size)} canvasBgColor={design.background.backgroundColor || ''}
        setCanvasBgColor={color => charts.updateCanvas({ backgroundColor: color })} gridSize={dimensions.gridSize} setGridSize={gridSize => charts.updateCanvas({ gridSize })}
        scale={canvas.scale} setScale={canvas.zoomTo} allCharts={allCharts} isNewChart={charts.isNewChart}
        onPropertyChange={charts.handlePropertyChange} onPositionChange={(id, axis, value) => {
          if (layoutMode === 'screen') { charts.handlePositionChange(id, axis, value); return; }
          const item = pageLayout.items.find(item => item.chart.id === id);
          if (item && Number.isFinite(value)) charts.updatePageLayout(breakpoint, movePageItem(pageLayout.items, id, { ...item.rect, [axis]: value }, breakpoint));
        }} onWidgetConfigChange={charts.handleWidgetConfigChange}
        onDelete={charts.handleDeleteSelected} onChartConfig={openConfig} tab={propertyTab} onTab={setPropertyTab}
        pageParams={draftDashboard.page_params || []}
        onCreatePageParam={param => charts.updateMetadata({
          page_params: [...(draftDashboard.page_params || []).filter(p => p.name !== param.name), param],
        })} />
    </div>
    {/* dropAnimation={null}：这是「松手即新建」而不是「换位」，留着默认落点动画会看起来像弹回库里 */}
    <DragOverlay dropAnimation={null}>{libraryDrag && <div className="rounded-md border bg-background px-2 py-1 text-xs shadow-lg">{libraryDrag.item.label}</div>}</DragOverlay>
    </DndContext>
    <ChartConfigPanel open={charts.configPanelOpen} chart={selectedChart} onClose={() => charts.setConfigPanelOpen(false)} onSave={charts.handleSaveChartConfig} />
    <DashboardTemplates open={charts.templatesOpen} onClose={() => charts.setTemplatesOpen(false)} onApply={async template => {
      const applyTemplate = async () => {
        const newId = await charts.createFromTemplate(template); approveNavigation(); navigate(`/dashboard/editor/${newId}`);
      };
      // 创建模板看板会离开当前草稿：有未保存变更时先问，不默默丢弃
      if (hasUnsavedChanges) {
        askConfirm('放弃未保存的更改？', '应用模板会新建一个看板并离开当前草稿，当前未保存的更改将丢失。', '放弃并应用模板', () => { void applyTemplate(); });
        return;
      }
      void applyTemplate();
    }} />
    <Dialog open={preview} onOpenChange={setPreview}><DialogContent className="max-w-[95vw] h-[90vh] flex flex-col"><DialogHeader><DialogTitle>草稿预览</DialogTitle><DialogDescription>仅使用当前草稿与已有数据，不查询、不自动保存；页面内容以真实宽度展示。</DialogDescription></DialogHeader>
      <div className="flex gap-2">
        <select aria-label="预览布局" className="rounded border bg-background p-1" value={layoutMode} onChange={e => setLayoutMode(e.target.value as 'page' | 'screen')}><option value="page">页面布局</option><option value="screen">大屏布局</option></select>
        {layoutMode === 'page' && <select aria-label="预览设备" className="rounded border bg-background p-1" value={breakpoint} onChange={e => setBreakpoint(e.target.value as PageBreakpoint)}>{Object.entries(PAGE_LABELS).map(([bp, label]) => <option value={bp} key={bp}>{label} · {PAGE_WIDTHS[bp as PageBreakpoint]}px</option>)}</select>}
      </div>
      <div className="min-h-0 min-w-0 flex-1 overflow-auto">{layoutMode === 'page' ? <div className="mx-auto" style={{ width: PAGE_WIDTHS[breakpoint] }}><ResponsiveDashboardCanvas dashboard={draftDashboard} items={library.items} draft breakpoint={breakpoint} /></div> : <FittedDashboardCanvas dashboard={draftDashboard} items={library.items} draft />}</div></DialogContent></Dialog>
    <Dialog open={!!pack || followTheme} onOpenChange={() => { setPack(null); setFollowTheme(false); }}><DialogContent><DialogHeader><DialogTitle>{pack ? `套用主题包：${pack.name}` : '全部使用 UI 字模库'}</DialogTitle><DialogDescription>背景、卡片、全部图表与控件使用整套字模。{followTheme ? '跟随用户选择的系统主题。' : ''}仅修改草稿，保存后生效；数据绑定和布局不变。</DialogDescription></DialogHeader>
      {packPreview && <DashboardThumbnail dashboard={packPreview} items={library.items} />}
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={replaceStyles} onChange={e => setReplaceStyles(e.target.checked)} />替换旧的图表、控件和背景样式</label>
      <DialogFooter><Button variant="outline" onClick={() => { setPack(null); setFollowTheme(false); }}>取消</Button><Button disabled={saving} onClick={() => {
        charts.transaction(() => {
          if (packFilters) charts.updateMetadata({ filters: packFilters });
          if (replaceStyles) for (const chart of allCharts) charts.updateLocalChart(chart.id, { config: clearChartVisualOverrides(chart.config) });
        });
        setPack(null); setFollowTheme(false);
      }}>确认套用</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={!!layout} onOpenChange={() => setLayout(null)}><DialogContent><DialogHeader><DialogTitle>应用布局：{layout?.name}</DialogTitle><DialogDescription>仅修改当前草稿位置；控件保持原位，空间不足时向下扩展。</DialogDescription></DialogHeader>
      {layoutPreview && <DashboardThumbnail dashboard={layoutPreview} items={library.items} />}
      <DialogFooter><Button variant="outline" onClick={() => setLayout(null)}>取消</Button><Button onClick={() => {
        if (!layout || !layoutResult) return;
        const filters = applyScreenComponent(draftDashboard.filters, layout);
        charts.transaction(() => {
          for (const [id, position] of Object.entries(layoutResult.positions)) charts.updateLocalChart(Number(id), { position });
          charts.updateMetadata({ filters: { ...filters, design: { ...filters.design, canvas: { ...dimensions, ...filters.design?.canvas, height: layoutResult.height } } } });
        });
        setLayout(null);
      }}>确认应用布局</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={saveVis} onOpenChange={open => { if (!visSaving) setSaveVis(open); }}><DialogContent><DialogHeader><DialogTitle>存为字模</DialogTitle><DialogDescription>仅保存视觉白名单字段，不包含查询、数据源或缓存。</DialogDescription></DialogHeader>
      <Input aria-label="字模名称" placeholder="字模名称" value={visName} onChange={e => setVisName(e.target.value)} />
      <select aria-label="回存类别" className="rounded border bg-background p-2" value={visCategory} onChange={e => setVisCategory(e.target.value as VisCategory)}>
        {VIS_CATEGORIES.filter(c => (selectedChart || !['chart_style', 'kpi_card'].includes(c.id))).map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select><DialogFooter><Button disabled={visSaving || !visName.trim()} onClick={persistVis}>{visSaving ? '保存中…' : '保存字模'}</Button></DialogFooter>
    </DialogContent></Dialog>
    <Dialog open={!!confirmAction} onOpenChange={open => { if (!open) setConfirmAction(null); }}>
      <DialogContent><DialogHeader><DialogTitle>{confirmAction?.title}</DialogTitle><DialogDescription>{confirmAction?.description}</DialogDescription></DialogHeader>
        <DialogFooter><Button variant="outline" onClick={() => setConfirmAction(null)}>取消</Button>
          <Button onClick={() => { const action = confirmAction; setConfirmAction(null); action?.run(); }}>{confirmAction?.actionLabel}</Button></DialogFooter>
      </DialogContent></Dialog>
    {/* 变更清单：只一个 badge 无法知道改了什么，点开可逐项跳转选中 */}
    <Dialog open={changesOpen} onOpenChange={setChangesOpen}>
      <DialogContent className="max-h-[80vh] w-[min(560px,95vw)]"><DialogHeader><DialogTitle>未保存的更改 ({charts.pendingCount})</DialogTitle>
        <DialogDescription>来自主动编辑，保存后清空。点条目可在画布上选中它。</DialogDescription></DialogHeader>
        <div className="min-h-0 flex-1 space-y-3 overflow-auto text-sm">
          {charts.pendingNewCount > 0 && <ChangeGroup title={`新增 (${charts.pendingNewCount})`} charts={allCharts.filter(c => c.id < 0)} onPick={chart => { charts.handleSelectElement(chart); setChangesOpen(false); }} />}
          {charts.pendingChangeCount > 0 && <ChangeGroup title={`修改 (${charts.dirtyIds.length})`} charts={allCharts.filter(c => charts.dirtyIds.includes(c.id))} onPick={chart => { charts.handleSelectElement(chart); setChangesOpen(false); }} />}
          {charts.pendingDeleteCount > 0 && <p className="text-xs text-muted-foreground">待删除图表：{charts.pendingDeleteCount} 个（保存后不可恢复，只能从版本备份找回）</p>}
          {Object.keys(charts.draftMetadataKeys || {}).length > 0 && <p className="text-xs text-muted-foreground">看板级变更：{(charts.draftMetadataKeys || []).join('、')}</p>}
          {!charts.pendingCount && <p className="text-xs text-muted-foreground">当前没有未保存的更改。</p>}
        </div>
      </DialogContent></Dialog>
  </div>;
}
