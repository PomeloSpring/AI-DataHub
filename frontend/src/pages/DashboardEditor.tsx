import { useState, useEffect, useContext, useRef } from 'react';
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
import { DashboardCanvas, DashboardChartCard, FittedDashboardCanvas, DashboardThumbnail } from '@/components/DashboardCanvas';
import { useCanvasInteraction, DEFAULT_CHART_SIZE, DEFAULT_WIDGET_SIZE } from '@/hooks/useCanvasInteraction';
import { useEditorCharts } from '@/hooks/useEditorCharts';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { createVisComponent, VIS_CATEGORIES, type VisCategory, type VisComponent } from '@/api/visLibrary';
import { applyChartComponent, applyScreenComponent, applyLayout, clampPosition, compatible, getCanvas,
  isEditingTarget, resolveChartDesign, resolveDashboardDesign, sanitizeStyle, screenToCanvas, visualConfig, legacyCanvasFilters } from '@/lib/dashboardDesign';

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
  const canvas = useCanvasInteraction({ allCharts, onDragEnd: charts.handleDragEnd, onResizeEnd: charts.handleResizeEnd,
    onSelectElement: chart => charts.handleSelectElement(chart) });
  const dimensions = getCanvas(draftDashboard?.filters, allCharts);
  const design = resolveDashboardDesign(legacyCanvasFilters(draftDashboard?.filters), library.items);
  const [leftOpen, setLeftOpen] = useState(() => window.innerWidth >= 1100);
  const [rightOpen, setRightOpen] = useState(() => window.innerWidth >= 1300);
  const [showGrid, setShowGrid] = useState(false);
  const [preview, setPreview] = useState(false);
  const [layout, setLayout] = useState<VisComponent | null>(null);
  const [saveVis, setSaveVis] = useState(false);
  const [visName, setVisName] = useState('');
  const [visCategory, setVisCategory] = useState<VisCategory>('chart_style');
  const [visSaving, setVisSaving] = useState(false);
  const [dropPreview, setDropPreview] = useState<ReturnType<typeof clampPosition> | null>(null);
  const dragItem = useRef<{ item: ChartTypeItem; component?: VisComponent } | null>(null);
  const approveNavigation = useUnsavedGuard(hasUnsavedChanges, saving);
  useEffect(() => { void store.loadDashboards(); }, []);
  useEffect(() => { if (current) store.setCurrent(dashboardId); }, [current?.id, dashboardId]);
  useEffect(() => {
    canvas.setCanvasSize({ width: dimensions.width, height: dimensions.height });
    canvas.setGridSize(dimensions.gridSize);
  }, [dimensions.width, dimensions.height, dimensions.gridSize]);
  useEffect(() => { const timer = setTimeout(canvas.resetZoom, 100); return () => clearTimeout(timer); }, [current?.id, dimensions.width, dimensions.height, leftOpen, rightOpen]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (saving || isEditingTarget(e.target) || document.querySelector('[role="dialog"]')) return;
      if (e.key === 'Delete' && selectedChart) { e.preventDefault(); charts.handleDeleteSelected(selectedChart); }
      if (e.key.toLowerCase() === 'f' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); canvas.resetZoom(); }
      if (e.key === 'Escape') charts.handleSelectElement(null);
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, [saving, selectedChart, charts.handleDeleteSelected, canvas.resetZoom]);
  const applyVis = (component: VisComponent) => {
    if (!draftDashboard || saving) return;
    if (component.category === 'layout_template') { setLayout(component); return; }
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
  const layoutPreview = draftDashboard && layoutResult ? { ...draftDashboard, filters: { ...draftDashboard.filters,
    design: { ...draftDashboard.filters.design, canvas: { ...dimensions, ...draftDashboard.filters.design?.canvas, height: layoutResult.height } } },
    charts: allCharts.map(c => ({ ...c, position: layoutResult.positions[c.id] || c.position })) } : null;
  const persistVis = async () => {
    if (!visName.trim() || !draftDashboard || visSaving) return;
    if (visCategory === 'chart_style' && (!selectedChart || selectedChart.chart_type.startsWith('widget_'))) { toast.error('请选择图表'); return; }
    if (visCategory === 'kpi_card' && (!selectedChart || !compatible({ category: 'kpi_card' } as VisComponent, selectedChart.chart_type))) { toast.error('请选择指标卡'); return; }
    setVisSaving(true);
    try {
      const resolved = selectedChart ? resolveChartDesign(selectedChart.config, design, library.items) : null;
      const style = visCategory === 'screen_background' ? { backgroundColor: '#f8fafc', ...design.background } : visCategory === 'color_theme' ? { palette: ['#111827', '#374151', '#6b7280', '#9ca3af'], ...(resolved?.visual || design.visual) }
        : visCategory === 'layout_template' ? { grid: { canvasW: dimensions.width, canvasH: dimensions.height,
          positions: allCharts.filter(c => !c.chart_type.startsWith('widget_')).map(c => c.position) } }
        : { config: visualConfig(resolved?.config || design.card) };
      await createVisComponent({ name: visName.trim(), category: visCategory,
        chart_type: visCategory === 'chart_style' ? selectedChart?.chart_type : undefined, style_config: sanitizeStyle(style) });
      await library.refresh(); setSaveVis(false); toast.success('已保存为自定义字模');
    } catch { toast.error('字模保存失败，请重试'); } finally { setVisSaving(false); }
  };
  if (!current || !draftDashboard) return <div className="p-8">{store.loading ? '正在加载…' : '看板不存在'}<Button variant="link" onClick={() => navigate('/system/dashboards')}>返回列表</Button></div>;
  return <div className="flex h-screen flex-col overflow-hidden bg-background">
    <EditorToolbar dashboardName={draftDashboard.name} scale={canvas.scale} canvasSize={dimensions}
      hasUnsavedChanges={hasUnsavedChanges} pendingCount={charts.pendingCount} pendingNewCount={allCharts.filter(c => c.id < 0).length}
      pendingChangeCount={charts.pendingChangeCount} pendingDeleteCount={charts.pendingDeleteCount}
      leftPanelOpen={leftOpen} rightPanelOpen={rightOpen} onZoomIn={canvas.zoomIn} onZoomOut={canvas.zoomOut} onResetZoom={canvas.resetZoom}
      onOpenTemplates={() => charts.setTemplatesOpen(true)} onSave={() => void charts.saveAllChanges()} onExit={() => navigate(returnTo)}
      onOpenLeftPanel={() => { setLeftOpen(true); if (window.innerWidth < 1100) setRightOpen(false); }} onOpenRightPanel={() => { setRightOpen(true); if (window.innerWidth < 1100) setLeftOpen(false); }} saving={saving}
      showGrid={showGrid} onToggleGrid={() => setShowGrid(v => !v)} onPreview={() => setPreview(true)}
      onSaveVis={() => { setVisName(''); setVisCategory(selectedChart ? 'chart_style' : 'screen_background'); setSaveVis(true); }} />
    <div className={`relative flex min-h-0 flex-1 ${saving ? 'pointer-events-none opacity-70' : ''}`}>
      <ComponentLibrary allCharts={allCharts} selectedChart={selectedChart} isOpen={leftOpen} onClose={() => setLeftOpen(false)}
        onDragStart={startDrag} onDragEnd={() => { dragItem.current = null; setDropPreview(null); }} onSelectChart={charts.handleSelectElement}
        onApplyVis={applyVis} onVisDragStart={(e, component) => {
          const item = CHART_TYPES.find(c => c.value === (component.category === 'kpi_card' ? 'big_number_trend' : component.chart_type));
          if (item) startDrag(e, item, component); else { e.preventDefault(); toast.info('该字模未指定可创建的图表类型'); }
        }} />
      <div ref={canvas.canvasRef} className="relative min-w-0 flex-1 overflow-hidden bg-muted/30" onMouseDown={canvas.handlePanStart} onWheel={canvas.handleWheel}
        onDragOver={e => { e.preventDefault(); setDropPreview(dropPosition(e)); }} onDragLeave={() => setDropPreview(null)}
        onDrop={e => { e.preventDefault(); const position = dropPosition(e); const value = dragItem.current;
          if (position && value) charts.handleAddFromPanel(value.item, position, value.component ? applyChartComponent({}, value.component) : {});
          dragItem.current = null; setDropPreview(null);
        }}>
        <DashboardCanvas ref={canvas.surfaceRef} dashboard={draftDashboard} items={library.items} showGrid={showGrid} draft
          style={{ position: 'absolute', left: '50%', top: '50%', marginLeft: -dimensions.width / 2, marginTop: -dimensions.height / 2,
            transform: `translate(${canvas.panOffset.x}px, ${canvas.panOffset.y}px) scale(${canvas.scale})`, transformOrigin: 'center' }}
          renderChart={chart => {
            const dragging = canvas.draggingChart === chart.id, resizing = canvas.resizingChart === chart.id, selected = selectedChart?.id === chart.id;
            const p = { ...chart.position, ...(dragging ? canvas.dragPosition : {}), ...(resizing ? canvas.resizePosition : {}) };
            return <div key={chart.id} className={`dashboard-chart-cell absolute ${selected ? 'ring-2 ring-primary' : ''}`}
              style={{ left: p.x, top: p.y, width: p.w, height: p.h, zIndex: selected ? 10 : 1 }} onClick={e => charts.handleSelectElement(chart, e)}>
              <DashboardChartCard chart={chart} design={design} items={library.items} draft />
              {selected && <>
                <div className="absolute -top-7 left-0 z-20 flex h-7 items-center rounded-t border bg-background px-2" onMouseDown={e => canvas.handleDragStart(e, chart.id)}>
                  <span title="拖动图表" className="cursor-move px-2"><GripHorizontal className="h-4 w-4" /></span>
                  <button title="配置" onClick={() => openConfig(chart)}><Settings className="mx-2 h-3 w-3" /></button>
                  <button title="删除" onClick={() => charts.handleDeleteSelected(chart)}><Trash2 className="mx-2 h-3 w-3" /></button>
                </div>
                <div title="调整大小" className="absolute bottom-0 right-0 z-20 h-4 w-4 cursor-se-resize bg-primary" onMouseDown={e => canvas.handleResizeStart(e, chart.id)} />
              </>}
            </div>;
          }}>
          {dropPreview && <div className="pointer-events-none absolute border-2 border-dashed border-primary bg-primary/10" style={{ left: dropPreview.x, top: dropPreview.y, ...{ width: dragItem.current?.item.category === 'widget' ? 300 : 400, height: dragItem.current?.item.category === 'widget' ? 60 : 300 } }} />}
        </DashboardCanvas>
      </div>
      <PropertyPanel isOpen={rightOpen} onClose={() => setRightOpen(false)} selectedChart={selectedChart} selectedElementType={selectedElementType}
        canvasSize={dimensions} setCanvasSize={size => charts.updateCanvas(size)} canvasBgColor={design.background.backgroundColor || ''}
        setCanvasBgColor={color => charts.updateCanvas({ backgroundColor: color })} gridSize={dimensions.gridSize} setGridSize={gridSize => charts.updateCanvas({ gridSize })}
        scale={canvas.scale} setScale={canvas.setScale} allCharts={allCharts} isNewChart={charts.isNewChart}
        onPropertyChange={charts.handlePropertyChange} onPositionChange={charts.handlePositionChange} onWidgetConfigChange={charts.handleWidgetConfigChange}
        onDelete={charts.handleDeleteSelected} onChartConfig={openConfig} />
    </div>
    <ChartConfigPanel open={charts.configPanelOpen} chart={selectedChart} onClose={() => charts.setConfigPanelOpen(false)} onSave={charts.handleSaveChartConfig} />
    <DashboardTemplates open={charts.templatesOpen} onClose={() => charts.setTemplatesOpen(false)} onApply={async template => {
      if (hasUnsavedChanges && !window.confirm('创建模板看板将离开当前草稿，确定放弃未保存更改吗？')) return;
      const newId = await charts.createFromTemplate(template); approveNavigation(); navigate(`/dashboard/editor/${newId}`);
    }} />
    <Dialog open={preview} onOpenChange={setPreview}><DialogContent className="max-w-[95vw] h-[90vh] flex flex-col"><DialogHeader><DialogTitle>草稿预览</DialogTitle><DialogDescription>仅使用当前草稿与已有数据，不写入配置。</DialogDescription></DialogHeader>
      <div className="min-h-0 flex-1"><FittedDashboardCanvas dashboard={draftDashboard} items={library.items} draft /></div></DialogContent></Dialog>
    <Dialog open={!!layout} onOpenChange={() => setLayout(null)}><DialogContent><DialogHeader><DialogTitle>应用布局：{layout?.name}</DialogTitle><DialogDescription>仅修改当前草稿位置；控件保持原位，空间不足时向下扩展。</DialogDescription></DialogHeader>
      {layoutPreview && <DashboardThumbnail dashboard={layoutPreview} items={library.items} />}
      <DialogFooter><Button variant="outline" onClick={() => setLayout(null)}>取消</Button><Button onClick={() => {
        if (!layout || !layoutResult) return;
        for (const [id, position] of Object.entries(layoutResult.positions)) charts.updateLocalChart(Number(id), { position });
        const filters = applyScreenComponent(draftDashboard.filters, layout);
        charts.updateMetadata({ filters: { ...filters, design: { ...filters.design, canvas: { ...dimensions, ...filters.design?.canvas, height: layoutResult.height } } } }); setLayout(null);
      }}>确认应用布局</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={saveVis} onOpenChange={open => { if (!visSaving) setSaveVis(open); }}><DialogContent><DialogHeader><DialogTitle>存为字模</DialogTitle><DialogDescription>仅保存视觉白名单字段，不包含查询、数据源或缓存。</DialogDescription></DialogHeader>
      <Input aria-label="字模名称" placeholder="字模名称" value={visName} onChange={e => setVisName(e.target.value)} />
      <select aria-label="回存类别" className="rounded border bg-background p-2" value={visCategory} onChange={e => setVisCategory(e.target.value as VisCategory)}>
        {VIS_CATEGORIES.filter(c => c.id !== 'sql_template' && (selectedChart || !['chart_style', 'kpi_card'].includes(c.id))).map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select><DialogFooter><Button disabled={visSaving || !visName.trim()} onClick={persistVis}>{visSaving ? '保存中…' : '保存字模'}</Button></DialogFooter>
    </DialogContent></Dialog>
  </div>;
}
