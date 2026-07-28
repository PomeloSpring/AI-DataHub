import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Plus, Settings, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { useDashboardStore } from '../stores/dashboardStore';
import DashboardChart, { type ChartTypeItem } from '../components/DashboardChart';
import ChartConfigPanel from '../components/ChartConfigPanel';
import DashboardTemplates from '../components/DashboardTemplates';
import ComponentLibrary from '../components/editor/ComponentLibrary';
import PropertyPanel from '../components/editor/PropertyPanel';
import EditorToolbar from '../components/editor/EditorToolbar';
import { useCanvasInteraction, DEFAULT_CHART_SIZE } from '../hooks/useCanvasInteraction';
import { useEditorCharts } from '../hooks/useEditorCharts';

export default function DashboardEditor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { loading, loadDashboards, setCurrent } = useDashboardStore();

  const dashboardId = Number(id);

  // Charts hook
  const charts = useEditorCharts(dashboardId);
  const {
    current, allCharts, selectedChart, selectedElementType,
    configPanelOpen, setConfigPanelOpen, templatesOpen, setTemplatesOpen,
    hasUnsavedChanges, pendingCount,
    getChartData, isNewChart,
    handlePropertyChange, handlePositionChange, handleWidgetConfigChange,
    handleAddFromPanel, handleDragEnd, handleResizeEnd,
    handleDeleteSelected, handleSaveChartConfig, saveAllChanges, handleSelectElement,
    createFromTemplate,
  } = charts;

  // Canvas interaction hook
  const canvas = useCanvasInteraction({
    allCharts,
    onDragEnd: handleDragEnd,
    onResizeEnd: handleResizeEnd,
    onSelectElement: (chart) => handleSelectElement(chart),
  });
  const {
    canvasRef, canvasSize, setCanvasSize, canvasBgColor, setCanvasBgColor,
    gridSize, setGridSize, scale, setScale, panOffset,
    draggingChart, dragPosition, resizingChart, resizePosition, isPanning,
    handleDragStart, handleResizeStart, handlePanStart, handleWheel,
    zoomIn, zoomOut, resetZoom,
  } = canvas;

  // Panel state
  const [leftPanelOpen, setLeftPanelOpen] = useState(true);
  const [rightPanelOpen, setRightPanelOpen] = useState(true);

  // Drag from panel state
  const [draggingFromPanel, setDraggingFromPanel] = useState<ChartTypeItem | null>(null);
  const [dropPreview, setDropPreview] = useState<{ x: number; y: number; w: number; h: number } | null>(null);

  // Init
  useEffect(() => { loadDashboards(); }, []);
  useEffect(() => { if (dashboardId) setCurrent(dashboardId); }, [dashboardId, setCurrent]);

  // Warn before leaving with unsaved changes
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) { e.preventDefault(); e.returnValue = ''; }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [hasUnsavedChanges]);

  // Auto-fit on initial load
  useEffect(() => {
    if (current && current.charts.length > 0) {
      const timer = setTimeout(() => resetZoom(), 100);
      return () => clearTimeout(timer);
    }
  }, [current?.id]);

  // Keyboard: Delete selected
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Delete' && selectedChart && selectedElementType !== 'canvas') {
        handleDeleteSelected(selectedChart);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedChart, selectedElementType, handleDeleteSelected]);

  // Panel drag-drop handlers
  const snapToGrid = useCallback((value: number) => Math.round(value / gridSize) * gridSize, [gridSize]);

  const handlePanelDragStart = useCallback((e: React.DragEvent, item: ChartTypeItem) => {
    e.dataTransfer.setData('application/chart-type', JSON.stringify(item));
    e.dataTransfer.effectAllowed = 'copy';
    setDraggingFromPanel(item);
  }, []);

  const handleCanvasDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    if (!draggingFromPanel || !canvasRef.current) return;
    const canvasRect = canvasRef.current.getBoundingClientRect();
    const isWidget = draggingFromPanel.category === 'widget';
    const size = isWidget ? { w: 300, h: 60 } : DEFAULT_CHART_SIZE;
    const x = snapToGrid(Math.max(0, (e.clientX - canvasRect.left - panOffset.x) / scale - size.w / 2));
    const y = snapToGrid(Math.max(0, (e.clientY - canvasRect.top - panOffset.y) / scale - size.h / 2));
    setDropPreview({ x, y, w: size.w, h: size.h });
  }, [draggingFromPanel, scale, panOffset, snapToGrid, canvasRef]);

  const handleCanvasDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const data = e.dataTransfer.getData('application/chart-type');
    if (!data || !canvasRef.current) return;
    try {
      const item: ChartTypeItem = JSON.parse(data);
      const canvasRect = canvasRef.current.getBoundingClientRect();
      const isWidget = item.category === 'widget';
      const size = isWidget ? { w: 300, h: 60 } : DEFAULT_CHART_SIZE;
      const x = snapToGrid(Math.max(0, (e.clientX - canvasRect.left - panOffset.x) / scale - size.w / 2));
      const y = snapToGrid(Math.max(0, (e.clientY - canvasRect.top - panOffset.y) / scale - size.h / 2));
      handleAddFromPanel(item, { x, y });
    } catch { /* ignore */ }
    setDraggingFromPanel(null);
    setDropPreview(null);
  }, [scale, panOffset, snapToGrid, canvasRef, handleAddFromPanel]);

  const handleCanvasDragLeave = useCallback(() => setDropPreview(null), []);

  const handleChartConfig = useCallback((chart: any) => {
    charts.setSelectedChart(chart);
    setConfigPanelOpen(true);
  }, [charts, setConfigPanelOpen]);

  const handleApplyTemplate = useCallback(async (template: any) => {
    await createFromTemplate(template);
  }, [createFromTemplate]);

  // Loading state
  if (loading && !current) {
    return <div className="flex items-center justify-center h-screen bg-background"><Spinner size={48} /></div>;
  }
  if (!current) {
    return (
      <div className="flex items-center justify-center h-screen bg-background">
        <div className="text-center">
          <p className="text-muted-foreground mb-4">仪表盘不存在</p>
          <Button onClick={() => navigate('/dashboard')}>返回仪表盘</Button>
        </div>
      </div>
    );
  }

  // Pending counts for toolbar
  const pendingNewCount = allCharts.filter(c => c.id < 0).length;

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Left Panel */}
      <ComponentLibrary
        allCharts={allCharts}
        selectedChart={selectedChart}
        isOpen={leftPanelOpen}
        onClose={() => setLeftPanelOpen(false)}
        onDragStart={handlePanelDragStart}
        onDragEnd={() => { setDraggingFromPanel(null); setDropPreview(null); }}
        onSelectChart={(chart) => handleSelectElement(chart)}
      />

      {/* Center - Canvas */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <EditorToolbar
          dashboardName={current.name}
          scale={scale}
          canvasSize={canvasSize}
          hasUnsavedChanges={hasUnsavedChanges}
          pendingCount={pendingCount}
          pendingNewCount={pendingNewCount}
          pendingChangeCount={0}
          pendingDeleteCount={0}
          leftPanelOpen={leftPanelOpen}
          rightPanelOpen={rightPanelOpen}
          onZoomIn={zoomIn}
          onZoomOut={zoomOut}
          onResetZoom={resetZoom}
          onOpenTemplates={() => setTemplatesOpen(true)}
          onSave={saveAllChanges}
          onExit={() => navigate('/dashboard')}
          onOpenLeftPanel={() => setLeftPanelOpen(true)}
          onOpenRightPanel={() => setRightPanelOpen(true)}
        />

        {/* Canvas */}
        <div
          ref={canvasRef}
          className="flex-1 overflow-hidden relative"
          style={{ cursor: isPanning ? 'grabbing' : 'grab', background: canvasBgColor || undefined }}
          onMouseDown={handlePanStart}
          onWheel={handleWheel}
          onDragOver={handleCanvasDragOver}
          onDrop={handleCanvasDrop}
          onDragLeave={handleCanvasDragLeave}
        >
          <div
            className="dashboard-canvas absolute"
            style={{
              width: canvasSize.width,
              height: canvasSize.height,
              transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${scale})`,
              transformOrigin: 'center center',
              left: '50%',
              top: '50%',
              marginLeft: -canvasSize.width / 2,
              marginTop: -canvasSize.height / 2,
            }}
          >
            {/* Drop preview */}
            {dropPreview && (
              <div
                className="absolute border-2 border-dashed border-primary/60 bg-primary/10 rounded-lg pointer-events-none z-50"
                style={{ left: dropPreview.x, top: dropPreview.y, width: dropPreview.w, height: dropPreview.h }}
              >
                <div className="flex items-center justify-center h-full text-primary/60 text-sm">
                  <Plus className="h-5 w-5 mr-1" /> 放置到这里
                </div>
              </div>
            )}

            {/* Rendered charts */}
            {allCharts.map((chart) => {
              const chartData = getChartData(chart);
              const isDragging = draggingChart === chart.id;
              const isResizing = resizingChart === chart.id;
              const isSelected = selectedChart?.id === chart.id;
              const posX = isDragging ? dragPosition.x : (chart.position?.x ?? 0);
              const posY = isDragging ? dragPosition.y : (chart.position?.y ?? 0);
              const posW = isResizing ? resizePosition.w : (chart.position?.w ?? DEFAULT_CHART_SIZE.w);
              const posH = isResizing ? resizePosition.h : (chart.position?.h ?? DEFAULT_CHART_SIZE.h);
              const isWidget = chart.chart_type?.startsWith('widget_');
              const ws = chart.config?.widgetStyle || {};

              return (
                <div
                  key={chart.id}
                  className={`dashboard-chart-cell ${isDragging ? 'dragging' : ''} ${isResizing ? 'resizing' : ''} ${isSelected ? 'selected-element' : ''}`}
                  style={{
                    position: 'absolute', left: posX, top: posY, width: posW, height: posH,
                    zIndex: isDragging || isResizing ? 100 : (isSelected ? 10 : 1),
                    ...(isWidget ? {
                      background: ws.backgroundColor || undefined,
                      borderRadius: ws.borderRadius != null ? ws.borderRadius : undefined,
                      borderLeft: `3px solid ${ws.borderColor || 'hsl(var(--primary))'}`,
                      borderWidth: ws.borderWidth != null ? ws.borderWidth : undefined,
                      borderColor: ws.borderColor || undefined,
                      borderStyle: ws.borderStyle || undefined,
                      boxShadow: ws.boxShadow || undefined,
                      opacity: ws.opacity != null ? ws.opacity : undefined,
                    } : {}),
                  }}
                  onMouseDown={(e) => handleDragStart(e, chart.id)}
                  onClick={(e) => handleSelectElement(chart, e)}
                >
                  {!isWidget && (
                    <div className="dashboard-chart-header flex items-center justify-between px-3 py-2 flex-shrink-0">
                      <span className="text-sm font-medium truncate flex-1">{chart.name}</span>
                      <div className="flex gap-1 ml-2 flex-shrink-0">
                        <Button size="sm" variant="ghost" className="h-6 w-6 p-0"
                          onClick={(e) => { e.stopPropagation(); handleChartConfig(chart); }}>
                          <Settings className="h-3 w-3 text-muted-foreground" />
                        </Button>
                        <Button size="sm" variant="ghost" className="h-6 w-6 p-0"
                          onClick={(e) => { e.stopPropagation(); handleDeleteSelected(chart); }}>
                          <Trash2 className="h-3 w-3 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  )}
                  <div
                    className="p-2 flex-1 min-h-0 overflow-hidden"
                    style={{
                      color: ws.textColor || undefined,
                      fontSize: ws.fontSize != null ? ws.fontSize : undefined,
                      fontWeight: ws.fontWeight || undefined,
                      padding: ws.padding != null ? ws.padding : undefined,
                      margin: ws.margin != null ? ws.margin : undefined,
                      display: 'flex',
                      alignItems: ws.verticalAlign || 'center',
                      justifyContent: ws.horizontalAlign || 'center',
                    }}
                  >
                    <DashboardChart
                      chartType={chart.chart_type}
                      data={chartData || { columns: [], rows: [] }}
                      config={chart.config || {}}
                    />
                  </div>
                  <div className="resize-handle" onMouseDown={(e) => handleResizeStart(e, chart.id)} />
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Right Panel */}
      <PropertyPanel
        isOpen={rightPanelOpen}
        onClose={() => setRightPanelOpen(false)}
        selectedChart={selectedChart}
        selectedElementType={selectedElementType}
        canvasSize={canvasSize}
        setCanvasSize={setCanvasSize}
        canvasBgColor={canvasBgColor}
        setCanvasBgColor={setCanvasBgColor}
        gridSize={gridSize}
        setGridSize={setGridSize}
        scale={scale}
        setScale={setScale}
        allCharts={allCharts}
        isNewChart={isNewChart}
        onPropertyChange={handlePropertyChange}
        onPositionChange={handlePositionChange}
        onWidgetConfigChange={handleWidgetConfigChange}
        onDelete={handleDeleteSelected}
        onChartConfig={handleChartConfig}
      />

      {/* Modals */}
      <ChartConfigPanel open={configPanelOpen} chart={selectedChart}
        onClose={() => setConfigPanelOpen(false)}
        onSave={handleSaveChartConfig} />
      <DashboardTemplates open={templatesOpen} onClose={() => setTemplatesOpen(false)} onApply={handleApplyTemplate} />
    </div>
  );
}
