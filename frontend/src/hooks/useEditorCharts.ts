import { useState, useCallback, useMemo } from 'react';
import { toast } from 'sonner';
import { useDashboardStore } from '../stores/dashboardStore';
import type { ChartTypeItem } from '../components/DashboardChart';
import { DEFAULT_CHART_SIZE, DEFAULT_WIDGET_SIZE } from './useCanvasInteraction';

// Temp ID counter for locally staged new items
let tempIdCounter = -1;

type ElementType = 'chart' | 'control' | 'canvas';

export function useEditorCharts(dashboardId: number | undefined) {
  const {
    dashboards, addChart, updateChart, deleteChart, createFromTemplate,
  } = useDashboardStore();

  const current = useMemo(() => dashboards.find(d => d.id === dashboardId) || null, [dashboards, dashboardId]);

  // Selection state
  const [selectedChart, setSelectedChart] = useState<any>(null);
  const [selectedElementType, setSelectedElementType] = useState<ElementType>('canvas');

  // Modal state
  const [configPanelOpen, setConfigPanelOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);

  // Local staging state
  const [pendingChanges, setPendingChanges] = useState<Record<number, any>>({});
  const [pendingNewCharts, setPendingNewCharts] = useState<any[]>([]);
  const [pendingDeletedIds, setPendingDeletedIds] = useState<Set<number>>(new Set());
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

  // Merged chart list: server + pending changes + new - deleted
  const allCharts = useMemo(() => {
    if (!current) return [];
    const existing = current.charts
      .filter(c => !pendingDeletedIds.has(c.id))
      .map(c => {
        const changes = pendingChanges[c.id];
        return changes ? { ...c, ...changes } : c;
      });
    return [...existing, ...pendingNewCharts];
  }, [current, pendingChanges, pendingNewCharts, pendingDeletedIds]);

  const isNewChart = useCallback((chartId: number) => chartId < 0, []);

  const getChartData = useCallback((chart: any) => {
    if (chart.data_cache) {
      try {
        const parsed = JSON.parse(chart.data_cache);
        if (parsed.columns && parsed.rows) return parsed;
      } catch { /* ignore */ }
    }
    if (chart.config?.data) return chart.config.data;
    return null;
  }, []);

  // Generic property update (local staging)
  const updateLocalChart = useCallback((chartId: number, changes: any) => {
    if (isNewChart(chartId)) {
      setPendingNewCharts(prev => prev.map(c => c.id === chartId ? { ...c, ...changes } : c));
      setSelectedChart((prev: any) => prev?.id === chartId ? { ...prev, ...changes } : prev);
    } else {
      setPendingChanges(prev => ({
        ...prev,
        [chartId]: { ...(prev[chartId] || {}), ...changes },
      }));
      setSelectedChart((prev: any) => prev?.id === chartId ? { ...prev, ...changes } : prev);
    }
    setHasUnsavedChanges(true);
  }, [isNewChart]);

  const handlePropertyChange = useCallback((chartId: number, field: string, value: any) => {
    updateLocalChart(chartId, { [field]: value });
  }, [updateLocalChart]);

  const handlePositionChange = useCallback((chartId: number, axis: 'x' | 'y' | 'w' | 'h', value: number) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (!chart) return;
    const newPos = { ...(chart.position || {}), [axis]: value };
    updateLocalChart(chartId, { position: newPos });
  }, [allCharts, updateLocalChart]);

  const handleWidgetConfigChange = useCallback((chartId: number, key: string, value: any) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (!chart) return;
    const newConfig = { ...(chart.config || {}), [key]: value };
    updateLocalChart(chartId, { config: newConfig });
  }, [allCharts, updateLocalChart]);

  // Add chart from panel drag-drop
  const handleAddFromPanel = useCallback((item: ChartTypeItem, position: { x: number; y: number }) => {
    const isWidget = item.category === 'widget';
    const size = isWidget ? DEFAULT_WIDGET_SIZE : DEFAULT_CHART_SIZE;

    const widgetConfig: any = {};
    if (isWidget) {
      widgetConfig.paramKey = '';
      widgetConfig.label = item.label;
      widgetConfig.placeholder = '';
      widgetConfig.labelPosition = 'left';
    }

    const newChart = {
      id: tempIdCounter--,
      dashboard_id: current?.id ?? 0,
      name: item.label,
      chart_type: item.value,
      sql_query: '',
      config: widgetConfig,
      position: { x: position.x, y: position.y, w: size.w, h: size.h },
      source_type: isWidget ? 'widget' : 'empty',
      source_id: 0,
      data_cache: '',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setPendingNewCharts(prev => [...prev, newChart]);
    setHasUnsavedChanges(true);
    setSelectedChart(newChart);
    setSelectedElementType(isWidget ? 'control' : 'chart');
    toast.success(`${item.label} 已添加到画布`);
  }, [current]);

  // Drag end callback
  const handleDragEnd = useCallback((chartId: number, position: { x: number; y: number }) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (!chart) return;
    const newPos = { ...chart.position, x: position.x, y: position.y };
    if (isNewChart(chartId)) {
      setPendingNewCharts(prev => prev.map(c => c.id === chartId ? { ...c, position: newPos } : c));
    } else {
      setPendingChanges(prev => ({
        ...prev,
        [chartId]: { ...(prev[chartId] || {}), position: newPos },
      }));
    }
    setHasUnsavedChanges(true);
    setSelectedChart((prev: any) => prev?.id === chartId ? { ...prev, position: newPos } : prev);
  }, [allCharts, isNewChart]);

  // Resize end callback
  const handleResizeEnd = useCallback((chartId: number, position: { x: number; y: number; w: number; h: number }) => {
    if (isNewChart(chartId)) {
      setPendingNewCharts(prev => prev.map(c => c.id === chartId ? { ...c, position } : c));
    } else {
      setPendingChanges(prev => ({
        ...prev,
        [chartId]: { ...(prev[chartId] || {}), position },
      }));
    }
    setHasUnsavedChanges(true);
    setSelectedChart((prev: any) => prev?.id === chartId ? { ...prev, position } : prev);
  }, [isNewChart]);

  // Delete selected
  const handleDeleteSelected = useCallback((chart: any) => {
    if (!chart) return;
    if (isNewChart(chart.id)) {
      setPendingNewCharts(prev => prev.filter(c => c.id !== chart.id));
    } else {
      setPendingDeletedIds(prev => new Set([...prev, chart.id]));
    }
    setSelectedChart(null);
    setSelectedElementType('canvas');
    setHasUnsavedChanges(true);
    toast.success('已删除（点击保存生效）');
  }, [isNewChart]);

  // Save chart config from config panel
  const handleSaveChartConfig = useCallback((configWithMeta: any) => {
    if (!selectedChart) return;
    const { _sql_query, _previewData, _datasource_id, _chart_type, ...config } = configWithMeta;
    const updates: any = { config };
    if (_chart_type && _chart_type !== selectedChart.chart_type) {
      updates.chart_type = _chart_type;
    }
    if (_sql_query !== undefined) {
      updates.sql_query = _sql_query;
      if (_previewData) {
        updates.data_cache = JSON.stringify({ columns: _previewData.columns, rows: _previewData.rows.slice(0, 200) });
        updates.source_type = 'query';
      }
    }
    if (_datasource_id) {
      updates.source_id = _datasource_id;
      updates.config = { ...config, datasource_id: _datasource_id };
    }
    if (isNewChart(selectedChart.id)) {
      setPendingNewCharts(prev => prev.map(c => c.id === selectedChart.id ? { ...c, ...updates } : c));
    } else {
      setPendingChanges(prev => ({
        ...prev,
        [selectedChart.id]: { ...(prev[selectedChart.id] || {}), ...updates },
      }));
    }
    setHasUnsavedChanges(true);
    setSelectedChart((prev: any) => prev ? { ...prev, ...updates } : prev);
    toast.success('配置已更新');
  }, [selectedChart, isNewChart]);

  // Save all pending changes to server
  const saveAllChanges = useCallback(async () => {
    if (!current) return;
    let savedCount = 0;

    for (const newChart of pendingNewCharts) {
      const extraChanges = pendingChanges[newChart.id] || {};
      const { id: _tempId, created_at, updated_at, ...basePayload } = newChart;
      const payload = { ...basePayload, ...extraChanges };
      await addChart(current.id, payload);
      savedCount++;
    }

    for (const [chartId, changes] of Object.entries(pendingChanges)) {
      const realId = Number(chartId);
      if (realId < 0) continue;
      const original = current.charts.find(c => c.id === realId);
      if (original) {
        await updateChart(current.id, realId, { ...original, ...changes });
        savedCount++;
      }
    }

    for (const deletedId of pendingDeletedIds) {
      if (deletedId < 0) continue;
      await deleteChart(current.id, deletedId);
      savedCount++;
    }

    setPendingChanges({});
    setPendingNewCharts([]);
    setPendingDeletedIds(new Set());
    setHasUnsavedChanges(false);
    setSelectedChart(null);
    setSelectedElementType('canvas');
    toast.success(`已保存 ${savedCount} 项更改`);
  }, [current, pendingChanges, pendingNewCharts, pendingDeletedIds, addChart, updateChart, deleteChart]);

  // Select element
  const handleSelectElement = useCallback((chart: any, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setSelectedChart(chart);
    setSelectedElementType(chart ? (chart.chart_type?.startsWith('widget_') ? 'control' : 'chart') : 'canvas');
  }, []);

  const pendingCount = useMemo(() => {
    return Object.keys(pendingChanges).length + pendingNewCharts.length + pendingDeletedIds.size;
  }, [pendingChanges, pendingNewCharts, pendingDeletedIds]);

  return {
    // State
    current,
    allCharts,
    selectedChart,
    setSelectedChart,
    selectedElementType,
    setSelectedElementType,
    configPanelOpen,
    setConfigPanelOpen,
    templatesOpen,
    setTemplatesOpen,
    hasUnsavedChanges,
    pendingCount,
    // Handlers
    getChartData,
    isNewChart,
    updateLocalChart,
    handlePropertyChange,
    handlePositionChange,
    handleWidgetConfigChange,
    handleAddFromPanel,
    handleDragEnd,
    handleResizeEnd,
    handleDeleteSelected,
    handleSaveChartConfig,
    saveAllChanges,
    handleSelectElement,
    createFromTemplate,
  };
}
