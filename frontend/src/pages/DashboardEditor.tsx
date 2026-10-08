import { useState, useEffect, useContext, useRef, useMemo } from 'react';
import { useParams, useNavigate, useLocation, UNSAFE_NavigationContext } from 'react-router-dom';
import { GripHorizontal, Settings, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { useDashboardStore } from '@/stores/dashboardStore';
import { CHART_TYPES, type ChartTypeItem } from '@/components/DashboardChart';
import ChartConfigPanel from '@/components/ChartConfigPanel';
import DashboardTemplates from '@/components/DashboardTemplates';
import ComponentLibrary from '@/components/editor/ComponentLibrary';
import PropertyPanel from '@/components/editor/PropertyPanel';
import EditorToolbar from '@/components/editor/EditorToolbar';
import PageLayoutEditor from '@/components/editor/PageLayoutEditor';
import { PAGE_WIDTHS, PAGE_LABELS, PAGE_COLUMNS, pageCanvasSize, pageMinHeight, pageRows, resolvePageLayout, movePageItem, withPageRect, type PageBreakpoint } from '@/lib/dashboardPageLayout';
import { DashboardCanvas, CanvasRulers, FittedDashboardCanvas, ResponsiveDashboardCanvas, DashboardThumbnail } from '@/components/DashboardCanvas';
import { useCanvasInteraction, DEFAULT_CHART_SIZE, DEFAULT_WIDGET_SIZE } from '@/hooks/useCanvasInteraction';
import { useEditorCharts } from '@/hooks/useEditorCharts';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { useThemeStore } from '@/stores/themeStore';
import { createVisComponent, VIS_CATEGORIES, type VisCategory, type VisComponent } from '@/api/visLibrary';
import { applyChartComponent, applyScreenComponent, applyLayout, clampPosition, compatible, getCanvas,
  resolveChartDesign, resolveDashboardDesign, sanitizeStyle, sanitizePack, screenToCanvas, visualConfig, legacyCanvasFilters,
  chartRect, clearChartVisualOverrides, libraryThemeFilters, themePackFor } from '@/lib/dashboardDesign';

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
  const dimensions = getCanvas(draftDashboard?.filters, allCharts);
  const theme = useThemeStore(s => s.theme);
  const design = useMemo(() => resolveDashboardDesign(legacyCanvasFilters(draftDashboard?.filters), library.items, { theme }), [draftDashboard?.filters, library.items, theme]);
  const [leftOpen, setLeftOpen] = useState(() => window.innerWidth >= 1100);
  const [rightOpen, setRightOpen] = useState(() => window.innerWidth >= 1300);
  const [showGrid, setShowGrid] = useState(true);
  const [panMode, setPanMode] = useState(false);
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
  const resetPage = () => {
    if (window.confirm(`恢复${PAGE_LABELS[breakpoint]}自动适配？仅清除当前断点的手动布局，不影响其他断点与大屏坐标。`)) charts.updatePageLayout(breakpoint);
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
  const [dropPreview, setDropPreview] = useState<ReturnType<typeof clampPosition> | null>(null);
  const dragItem = useRef<{ item: ChartTypeItem; component?: VisComponent } | null>(null);
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
      if (key === 'delete' && selectedChart) { e.preventDefault(); charts.handleDeleteSelected(selectedChart); }
      if (key === 'f') { e.preventDefault(); canvas.resetZoom(); }
      if (key === '+' || key === '=') { e.preventDefault(); canvas.zoomIn(); }
      if (key === '-') { e.preventDefault(); canvas.zoomOut(); }
      if (key === 'h') setPanMode(true);
      if (key === 'v') setPanMode(false);
      if (key === 'g') setShowGrid(value => !value);
      if (key === 'escape') charts.handleSelectElement(null);
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, [saving, selectedChart, charts.handleDeleteSelected, charts.undo, charts.redo, canvas.resetZoom, canvas.zoomIn, canvas.zoomOut, canvas.isInteracting]);
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
  const startDrag = (e: React.DragEvent, item: ChartTypeItem, component?: VisComponent) => {
    dragItem.current = { item, component }; e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('application/chart-type', item.value);
  };
  const dropPosition = (e: React.DragEvent) => {
    const rect = canvas.surfaceRef.current?.getBoundingClientRect();
    if (!rect || !dragItem.current) return null;
    const size = dragItem.current.item.category === 'widget' ? DEFAULT_WIDGET_SIZE : DEFAULT_CHART_SIZE;
    const p = screenToCanvas({ x: e.clientX, y: e.clientY }, rect, canvas.scale);
    return clampPosition({ x: p.x - size.w / 2, y: p.y - size.h / 2 }, size, dimensions, dimensions.gridSize);
  };
  const openConfig = (chart: any) => { charts.setSelectedChart(chart); charts.setConfigPanelOpen(true); };
  const returnTo = typeof location.state?.from === 'string' && /^\/(?!\/)/.test(location.state.from) && !location.state.from.includes('\\')
    ? location.state.from : '/system/dashboards';
  const layoutResult = layout ? applyLayout(allCharts, layout.style_config, dimensions) : null;
  const packFilters = draftDashboard && (pack || followTheme)
    ? replaceStyles || followTheme ? libraryThemeFilters(draftDashboard.filters, pack || undefined) : applyScreenComponent(draftDashboard.filters, pack!) : null;
  const packPreview = draftDashboard && packFilters ? { ...draftDashboard, filters: packFilters,
    charts: replaceStyles ? allCharts.map(c => ({ ...c, config: clearChartVisualOverrides(c.config) })) : allCharts } : null;
  const activeRect = selectedChart ? { ...chartRect(selectedChart),
    ...(canvas.draggingChart === selectedChart.id ? canvas.dragPosition : {}),
    ...(canvas.resizingChart === selectedChart.id ? canvas.resizePosition : {}) } : undefined;
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
  return <div className="flex h-screen flex-col overflow-hidden bg-background">
    <EditorToolbar dashboardName={draftDashboard.name} scale={canvas.scale} canvasSize={dimensions}
      layoutMode={layoutMode} onLayoutMode={setLayoutMode} breakpoint={breakpoint} onBreakpoint={setBreakpoint} onResetPage={resetPage} onCapturePage={capturePage}
      hasUnsavedChanges={hasUnsavedChanges} pendingCount={charts.pendingCount} pendingNewCount={allCharts.filter(c => c.id < 0).length}
      pendingChangeCount={charts.pendingChangeCount} pendingDeleteCount={charts.pendingDeleteCount}
      leftPanelOpen={leftOpen} rightPanelOpen={rightOpen} onZoomIn={canvas.zoomIn} onZoomOut={canvas.zoomOut} onResetZoom={canvas.resetZoom}
      onActualSize={() => canvas.zoomTo(1)} canUndo={charts.canUndo} canRedo={charts.canRedo} onUndo={charts.undo} onRedo={charts.redo} panMode={panMode} onPanMode={setPanMode}
      onOpenTemplates={() => charts.setTemplatesOpen(true)} onSave={() => void charts.saveAllChanges()} onExit={() => navigate(returnTo)}
      onOpenLeftPanel={() => { setLeftOpen(true); if (window.innerWidth < 1100) setRightOpen(false); }} onOpenRightPanel={() => { setRightOpen(true); if (window.innerWidth < 1100) setLeftOpen(false); }} saving={saving}
      showGrid={showGrid} onToggleGrid={() => setShowGrid(v => !v)} onPreview={() => setPreview(true)}
      snapEnabled={canvas.snapEnabled} onToggleSnap={() => canvas.setSnapEnabled(v => !v)}
      onUseLibrary={() => { if (!themePackFor(theme, library.items)) { toast.error('主题包尚未加载，请在字模库重试'); return; } setPack(null); setReplaceStyles(true); setFollowTheme(true); }}
      onSaveVis={() => { setVisName(''); setVisCategory(selectedChart ? 'chart_style' : 'screen_background'); setSaveVis(true); }} />
    <div className={`relative flex min-h-0 flex-1 ${saving ? 'pointer-events-none opacity-70' : ''}`}>
      <ComponentLibrary design={design} allCharts={allCharts} selectedChart={selectedChart} isOpen={leftOpen} onClose={() => setLeftOpen(false)}
        onDragStart={startDrag} onDragEnd={() => { dragItem.current = null; setDropPreview(null); }} onSelectChart={charts.handleSelectElement}
        onApplyVis={applyVis} onVisDragStart={(e, component) => {
          const item = CHART_TYPES.find(c => c.value === (component.category === 'kpi_card' ? 'big_number_trend' : component.chart_type));
          if (item) startDrag(e, item, component); else { e.preventDefault(); toast.info('该字模未指定可创建的图表类型'); }
        }} onAddFromDataset={(ds) => {
          // 从数据集引入: 放在现有图表下方, 预设随数据集 chart_preset 搬入
          charts.handleAddFromDataset(ds, { x: 0, y: allCharts.reduce((bottom, c) => Math.max(bottom, chartRect(c).y + chartRect(c).h), 0) + 16 });
        }} />
      {layoutMode === 'page' ? <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="px-4 py-2 text-xs text-muted-foreground border-b" role="status">
          {!draftDashboard.filters?.design?.page ? '页面布局尚未保存，由大屏布局生成。' : pageLayout.items.some(item => item.automatic) ? `${PAGE_LABELS[breakpoint]}包含自动适配布局。` : `${PAGE_LABELS[breakpoint]}独立布局。`}
          {' '}设计宽度 {PAGE_WIDTHS[breakpoint]}px · 编辑画布可等比例缩放与平移，草稿预览和阅读页仍按真实宽度展示。
        </div>
        <PageLayoutEditor key={`${dashboardId}:${breakpoint}`} dashboard={draftDashboard} items={library.items} breakpoint={breakpoint} selectedId={selectedChart?.id} canvas={canvas} showGrid={showGrid} panMode={panMode}
          onSelect={charts.handleSelectElement} onConfig={openConfig} onDelete={charts.handleDeleteSelected}
          onChange={items => charts.updatePageLayout(breakpoint, items)} onAdd={point => {
            const value = dragItem.current; dragItem.current = null;
            if (!value || pageLayout.error) return;
            const w = Math.min(PAGE_COLUMNS[breakpoint], value.item.category === 'widget' ? 3 : 6);
            const rect = { x: Math.min(point.x, PAGE_COLUMNS[breakpoint] - w), y: point.y, w, h: pageRows(pageMinHeight(value.item.value), breakpoint) };
            const config = value.component ? applyChartComponent({}, value.component) : {};
            charts.handleAddFromPanel(value.item, { x: 0, y: allCharts.reduce((bottom, c) => Math.max(bottom, chartRect(c).y + chartRect(c).h), 0) + 16 }, withPageRect(config, breakpoint, rect));
          }} />
      </div> : <div ref={canvas.canvasRef} className="adh-canvas-viewport relative min-h-0 min-w-0 flex-1 overflow-hidden cursor-grab active:cursor-grabbing"
        style={{ touchAction: 'none', userSelect: 'none', backgroundColor: 'hsl(var(--muted))', backgroundImage: 'radial-gradient(circle, hsl(var(--foreground) / 0.14) 1px, transparent 1px)', backgroundSize: '16px 16px' }}
        onPointerDownCapture={canvas.handlePanStart}
        onDragOver={e => { e.preventDefault(); setDropPreview(dropPosition(e)); }} onDragLeave={() => setDropPreview(null)}
        onDrop={e => { e.preventDefault(); const position = dropPosition(e); const value = dragItem.current;
          if (position && value) charts.handleAddFromPanel(value.item, position, value.component ? applyChartComponent({}, value.component) : {});
          dragItem.current = null; setDropPreview(null);
        }}>
        <DashboardCanvas ref={canvas.surfaceRef} dashboard={draftDashboard} items={library.items} showGrid={showGrid} draft
          style={{ position: 'absolute', left: '50%', top: '50%', marginLeft: -dimensions.width / 2, marginTop: -dimensions.height / 2,
            transform: `translate(${canvas.panOffset.x}px, ${canvas.panOffset.y}px) scale(${canvas.scale})`, transformOrigin: 'center',
            boxShadow: `0 0 0 ${1.5 / canvas.scale}px #64748b, 0 16px 48px #0003` }}
          renderChart={(chart, card) => {
            const dragging = canvas.draggingChart === chart.id, resizing = canvas.resizingChart === chart.id, selected = selectedChart?.id === chart.id;
            const original = chartRect(chart);
            const p = { ...original, ...(dragging ? canvas.dragPosition : {}), ...(resizing ? canvas.resizePosition : {}) };
            return <div key={chart.id} data-chart-id={chart.id} className={`adh-cell absolute left-0 top-0 ${panMode ? 'cursor-grab' : 'cursor-move'}`}
              style={{ transform: `translate3d(${p.x}px, ${p.y}px, 0)`, width: p.w, height: p.h, zIndex: selected ? 10 : 1,
                outline: selected ? `${2 / canvas.scale}px solid #3b82f6` : undefined, outlineOffset: -1 / canvas.scale,
                willChange: dragging ? 'transform' : undefined, touchAction: 'none' }}
              onPointerDown={e => canvas.handleDragStart(e, chart.id)} onDoubleClick={() => { if (!panMode) openConfig(chart); }}>
              <div className="pointer-events-none relative min-h-0" style={{ width: original.w, height: original.h,
                transformOrigin: 'top left', transform: resizing ? `scale(${p.w / original.w}, ${p.h / original.h})` : undefined }}>{card}</div>
              {selected && <>
                <div className="absolute right-0 top-0 z-20 flex h-7 items-center rounded-bl border bg-background shadow-sm"
                  style={{ transform: `scale(${1 / canvas.scale})`, transformOrigin: 'top right' }}>
                  <span title="拖动图表（也可直接拖动卡片）" className="cursor-move px-2"><GripHorizontal className="h-4 w-4" /></span>
                  <button title="配置" aria-label="配置图表" className="p-2" onPointerDown={e => e.stopPropagation()} onClick={() => openConfig(chart)}><Settings className="h-3 w-3" /></button>
                  <button title="删除" aria-label="删除图表" className="p-2" onPointerDown={e => e.stopPropagation()} onClick={() => charts.handleDeleteSelected(chart)}><Trash2 className="h-3 w-3" /></button>
                </div>
                <div title="调整大小" role="separator" aria-label="调整图表大小" className="absolute bottom-0 right-0 z-20 h-4 w-4 cursor-se-resize rounded-tl border-2 border-background bg-blue-500"
                  style={{ transform: `scale(${1 / canvas.scale})`, transformOrigin: 'bottom right' }} onPointerDown={e => canvas.handleResizeStart(e, chart.id)} />
              </>}
            </div>;
          }}>
          {dropPreview && <div className="pointer-events-none absolute border-2 border-dashed border-primary bg-primary/10" style={{ left: dropPreview.x, top: dropPreview.y, ...{ width: dragItem.current?.item.category === 'widget' ? 300 : 400, height: dragItem.current?.item.category === 'widget' ? 60 : 300 } }} />}
          {canvas.guides.map(g => <div key={`${g.axis}-${g.value}`} className="pointer-events-none absolute z-20 bg-fuchsia-500"
            style={g.axis === 'x' ? { left: g.value, top: 0, width: 1 / canvas.scale, height: dimensions.height } : { top: g.value, left: 0, height: 1 / canvas.scale, width: dimensions.width }} />)}
        </DashboardCanvas>
        <CanvasRulers canvas={dimensions} scale={canvas.scale} pan={canvas.panOffset} active={activeRect} />
        <div className="pointer-events-none absolute bottom-3 left-8 z-30 rounded border bg-background/95 px-3 py-1.5 text-xs shadow-sm">
          {activeRect ? `X ${Math.round(activeRect.x)} · Y ${Math.round(activeRect.y)} · W ${Math.round(activeRect.w)} · H ${Math.round(activeRect.h)}` : '滚轮缩放 · 空格拖动 / 中键平移 · 双击配置 · F 适应窗口'}
          <span className="ml-3 text-muted-foreground">{canvas.snapEnabled ? `自动吸附 · 网格 ${dimensions.gridSize}px · Alt 临时关闭` : '自由移动 · 吸附已关闭'}</span>
        </div>
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
        onDelete={charts.handleDeleteSelected} onChartConfig={openConfig} />
    </div>
    <ChartConfigPanel open={charts.configPanelOpen} chart={selectedChart} onClose={() => charts.setConfigPanelOpen(false)} onSave={charts.handleSaveChartConfig} />
    <DashboardTemplates open={charts.templatesOpen} onClose={() => charts.setTemplatesOpen(false)} onApply={async template => {
      if (hasUnsavedChanges && !window.confirm('创建模板看板将离开当前草稿，确定放弃未保存更改吗？')) return;
      const newId = await charts.createFromTemplate(template); approveNavigation(); navigate(`/dashboard/editor/${newId}`);
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
        {VIS_CATEGORIES.filter(c => c.id !== 'sql_template' && (selectedChart || !['chart_style', 'kpi_card'].includes(c.id))).map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select><DialogFooter><Button disabled={visSaving || !visName.trim()} onClick={persistVis}>{visSaving ? '保存中…' : '保存字模'}</Button></DialogFooter>
    </DialogContent></Dialog>
  </div>;
}
