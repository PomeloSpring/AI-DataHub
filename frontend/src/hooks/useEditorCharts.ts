import { useState, useCallback, useMemo, useRef } from 'react';
import { toast } from 'sonner';
import { useDashboardStore, type Dashboard, type DashboardChart } from '../stores/dashboardStore';
import type { ChartTypeItem } from '../components/DashboardChart';
import { DEFAULT_CHART_SIZE, DEFAULT_WIDGET_SIZE } from './useCanvasInteraction';
import { chartData, getCanvas } from '@/lib/dashboardDesign';

let tempIdCounter = -1;
type ElementType = 'chart' | 'control' | 'canvas';
interface Draft {
  changes: Record<number, Partial<DashboardChart>>; added: DashboardChart[]; deleted: number[];
  metadata: Partial<Dashboard>; selectedId: number | null;
}
const emptyDraft = (): Draft => ({ changes: {}, added: [], deleted: [], metadata: {}, selectedId: null });
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
export function useEditorCharts(dashboardId: number | undefined) {
  const store = useDashboardStore();
  const id = dashboardId || 0;
  const drafts = useRef<Record<number, Draft>>({});
  const [, render] = useState(0);
  const savingLock = useRef(false);
  const [saving, setSaving] = useState(false);
  const draft = drafts.current[id] || emptyDraft();
  const mutate = useCallback((fn: (d: Draft) => Draft, target = id) => {
    drafts.current[target] = fn(drafts.current[target] || emptyDraft());
    render(n => n + 1);
  }, [id]);
  const current = store.dashboards.find(d => d.id === id) || null;
  const allCharts = useMemo(() => current ? [
    ...current.charts.filter(c => !draft.deleted.includes(c.id)).map(c => ({ ...c, ...draft.changes[c.id] })),
    ...draft.added,
  ] : [], [current, draft]);
  const selectedChart = allCharts.find(c => c.id === draft.selectedId) || null;
  const selectedElementType: ElementType = selectedChart ? (selectedChart.chart_type.startsWith('widget_') ? 'control' : 'chart') : 'canvas';
  const setSelectedChart = useCallback((chart: any) => mutate(d => ({ ...d, selectedId: chart?.id ?? null })), [mutate]);
  const [configPanelOpen, setConfigPanelOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const pendingChangeCount = Object.keys(draft.changes).filter(key => !draft.deleted.includes(Number(key))).length + Object.keys(draft.metadata).length;
  const pendingCount = pendingChangeCount + draft.added.length + draft.deleted.length;
  const draftDashboard = current ? { ...current, ...draft.metadata, charts: allCharts } : null;
  const updateMetadata = useCallback((updates: Partial<Dashboard>) => {
    if (!savingLock.current) mutate(d => {
      const metadata = { ...d.metadata, ...updates };
      for (const key of Object.keys(metadata) as (keyof Dashboard)[]) if (equal(metadata[key], current?.[key])) delete metadata[key];
      return { ...d, metadata };
    });
  }, [mutate, current]);
  const updateCanvas = useCallback((updates: Record<string, any>) => {
    if (!current || savingLock.current) return;
    mutate(d => {
      const filters = d.metadata.filters || current.filters || {};
      return { ...d, metadata: { ...d.metadata, filters: { ...filters,
        design: { ...filters.design, version: 1, canvas: { ...getCanvas(filters, allCharts), ...filters.design?.canvas, ...updates } } } } };
    });
  }, [current, allCharts, mutate]);
  const updateLocalChart = useCallback((chartId: number, changes: Partial<DashboardChart>) => {
    if (savingLock.current) return;
    mutate(d => {
      if (chartId < 0) return { ...d, added: d.added.map(c => c.id === chartId ? { ...c, ...changes } : c) };
      const original = current?.charts.find(c => c.id === chartId);
      const pending = { ...d.changes[chartId], ...changes };
      for (const key of Object.keys(pending) as (keyof DashboardChart)[]) if (equal(pending[key], original?.[key])) delete pending[key];
      const next = { ...d.changes };
      if (Object.keys(pending).length) next[chartId] = pending; else delete next[chartId];
      return { ...d, changes: next };
    });
  }, [mutate, current]);
  const handlePropertyChange = (chartId: number, field: string, value: any) => updateLocalChart(chartId, { [field]: value });
  const handlePositionChange = (chartId: number, axis: 'x' | 'y' | 'w' | 'h', value: number) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (chart) updateLocalChart(chartId, { position: { ...chart.position, [axis]: Math.max(axis === 'w' || axis === 'h' ? 20 : 0, value) } });
  };
  const handleWidgetConfigChange = (chartId: number, key: string, value: any) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (chart) updateLocalChart(chartId, { config: { ...chart.config, [key]: value } });
  };
  const handleAddFromPanel = useCallback((item: ChartTypeItem, position: { x: number; y: number }, config = {}) => {
    if (savingLock.current) return;
    const widget = item.category === 'widget';
    const size = widget ? DEFAULT_WIDGET_SIZE : DEFAULT_CHART_SIZE;
    const chart: DashboardChart = { id: tempIdCounter--, dashboard_id: id, name: item.label, chart_type: item.value,
      sql_query: '', source_type: widget ? 'widget' : 'empty', source_id: null, data_cache: null,
      config: widget ? { paramKey: '', label: item.label, labelPosition: 'left', ...config } : config,
      position: { ...position, ...size }, created_at: '', updated_at: '' };
    mutate(d => ({ ...d, added: [...d.added, chart], selectedId: chart.id }));
    return chart;
  }, [id, mutate]);
  const handleDragEnd = (chartId: number, position: { x: number; y: number }) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (chart) updateLocalChart(chartId, { position: { ...chart.position, ...position } });
  };
  const handleResizeEnd = (chartId: number, position: DashboardChart['position']) => updateLocalChart(chartId, { position });
  const handleDeleteSelected = useCallback((chart: DashboardChart | null) => {
    if (!chart || savingLock.current) return;
    mutate(d => {
      const changes = { ...d.changes }; delete changes[chart.id];
      return { ...d, changes, added: d.added.filter(c => c.id !== chart.id),
        deleted: chart.id < 0 ? d.deleted : Array.from(new Set([...d.deleted, chart.id])), selectedId: null };
    });
  }, [mutate]);
  const handleSaveChartConfig = (value: any) => {
    if (!selectedChart) return;
    const { _sql_query, _previewData, _datasource_id, _chart_type, ...config } = value;
    const updates: Partial<DashboardChart> = { config };
    if (_chart_type) updates.chart_type = _chart_type;
    if (selectedChart.query_source !== 'semantic' && !selectedChart.semantic_query) {
      if (_sql_query !== undefined && _sql_query !== selectedChart.sql_query) updates.sql_query = _sql_query;
      if (_datasource_id && _datasource_id !== selectedChart.source_id) {
        updates.source_id = _datasource_id; updates.config = { ...config, datasource_id: _datasource_id };
      }
    }
    updateLocalChart(selectedChart.id, updates);
  };
  const saveAllChanges = useCallback(async () => {
    if (!current || savingLock.current) return false;
    savingLock.current = true; setSaving(true);
    const snapshot = drafts.current[id] || emptyDraft();
    try {
      for (const chart of snapshot.added) {
        const { id: tempId, created_at: _created, updated_at: _updated, data_cache: _cache, ...payload } = chart;
        const realId = await store.addChart(id, payload, true);
        mutate(d => ({ ...d, added: d.added.filter(c => c.id !== tempId), selectedId: d.selectedId === tempId ? realId : d.selectedId }), id);
      }
      for (const [chartId, changes] of Object.entries(snapshot.changes)) {
        if (snapshot.deleted.includes(Number(chartId))) continue;
        await store.updateChart(id, Number(chartId), changes, true);
        mutate(d => { const remaining = { ...d.changes }; delete remaining[Number(chartId)]; return { ...d, changes: remaining }; }, id);
      }
      for (const chartId of snapshot.deleted) {
        await store.deleteChart(id, chartId, true);
        mutate(d => ({ ...d, deleted: d.deleted.filter(v => v !== chartId) }), id);
      }
      if (Object.keys(snapshot.metadata).length) {
        await store.updateDashboard(id, snapshot.metadata, true);
        mutate(d => ({ ...d, metadata: {} }), id);
      }
      toast.success('所有更改已保存');
      return true;
    } catch {
      toast.error('保存未完成，未成功的更改已保留，可重试');
      return false;
    } finally {
      try { await store.loadDashboards(); }
      finally { savingLock.current = false; setSaving(false); }
    }
  }, [current, id, store, mutate]);
  const handleSelectElement = (chart: DashboardChart | null, e?: React.MouseEvent) => { e?.stopPropagation(); setSelectedChart(chart); };
  return { current, draftDashboard, allCharts, selectedChart, setSelectedChart, selectedElementType,
    configPanelOpen, setConfigPanelOpen, templatesOpen, setTemplatesOpen, saving,
    hasUnsavedChanges: pendingCount > 0, pendingCount, pendingChangeCount, pendingDeleteCount: draft.deleted.length,
    updateMetadata, updateCanvas, getChartData: chartData, isNewChart: (chartId: number) => chartId < 0,
    updateLocalChart, handlePropertyChange, handlePositionChange, handleWidgetConfigChange, handleAddFromPanel,
    handleDragEnd, handleResizeEnd, handleDeleteSelected, handleSaveChartConfig, saveAllChanges, handleSelectElement,
    createFromTemplate: store.createFromTemplate };
}
