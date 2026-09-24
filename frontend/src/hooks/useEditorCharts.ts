import { useState, useCallback, useMemo, useRef } from 'react';
import { toast } from 'sonner';
import { useDashboardStore, type Dashboard, type DashboardChart } from '../stores/dashboardStore';
import type { ChartTypeItem } from '../components/DashboardChart';
import { DEFAULT_CHART_SIZE, DEFAULT_WIDGET_SIZE } from './useCanvasInteraction';
import { chartData, chartRect, getCanvas } from '@/lib/dashboardDesign';
import { withPageRect, type PageBreakpoint, type PageItem } from '@/lib/dashboardPageLayout';

let tempIdCounter = -1;
type ElementType = 'chart' | 'control' | 'canvas';
interface Draft {
  changes: Record<number, Partial<DashboardChart>>; added: DashboardChart[]; deleted: number[];
  metadata: Partial<Dashboard>; selectedId: number | null;
}
const emptyDraft = (): Draft => ({ changes: {}, added: [], deleted: [], metadata: {}, selectedId: null });
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const sameContent = (a: Draft, b: Draft) =>
  (a.changes === b.changes && a.added === b.added && a.deleted === b.deleted && a.metadata === b.metadata) ||
  equal([a.changes, a.added, a.deleted, a.metadata], [b.changes, b.added, b.deleted, b.metadata]);
type History = { past: Draft[]; future: Draft[] };
export function useEditorCharts(dashboardId: number | undefined) {
  const store = useDashboardStore();
  const id = dashboardId || 0;
  const drafts = useRef<Record<number, Draft>>({});
  const histories = useRef<Record<number, History>>({});
  const transactionDepth = useRef(0);
  const [, render] = useState(0);
  const savingLock = useRef(false);
  const [saving, setSaving] = useState(false);
  const draft = drafts.current[id] || (drafts.current[id] = emptyDraft());
  const remember = useCallback((before: Draft, after: Draft, target: number) => {
    if (sameContent(before, after)) return;
    const history = histories.current[target] || (histories.current[target] = { past: [], future: [] });
    history.past = [...history.past.slice(-99), before];
    history.future = [];
  }, []);
  const mutate = useCallback((fn: (d: Draft) => Draft, target = id) => {
    const before = drafts.current[target] || emptyDraft();
    const after = fn(before);
    if (!savingLock.current && !transactionDepth.current) remember(before, after, target);
    drafts.current[target] = after;
    if (!transactionDepth.current) render(n => n + 1);
  }, [id, remember]);
  const transaction = useCallback((action: () => void) => {
    if (savingLock.current) return;
    const before = drafts.current[id] || emptyDraft();
    transactionDepth.current++;
    try { action(); } finally {
      transactionDepth.current--;
      if (!transactionDepth.current) {
        remember(before, drafts.current[id] || emptyDraft(), id);
        render(n => n + 1);
      }
    }
  }, [id, remember]);
  const travel = useCallback((direction: 'past' | 'future') => {
    if (savingLock.current || transactionDepth.current) return;
    const history = histories.current[id];
    const previous = history?.[direction].pop();
    if (!previous) return;
    history[direction === 'past' ? 'future' : 'past'].push(drafts.current[id] || emptyDraft());
    drafts.current[id] = previous;
    render(n => n + 1);
  }, [id]);
  const undo = useCallback(() => travel('past'), [travel]);
  const redo = useCallback(() => travel('future'), [travel]);
  // 已写入服务端的变更成为新基线，不能恢复旧临时 ID 或重复创建图表。
  const savedCheckpoint = () => { histories.current[id] = { past: [], future: [] }; };
  const current = store.dashboards.find(d => d.id === id) || null;
  const allCharts = useMemo(() => current ? [
    ...current.charts.filter(c => !draft.deleted.includes(c.id)).map(c => draft.changes[c.id] ? { ...c, ...draft.changes[c.id] } : c),
    ...draft.added,
  ] : [], [current, draft.changes, draft.deleted, draft.added]);
  const selectedChart = allCharts.find(c => c.id === draft.selectedId) || null;
  const selectedElementType: ElementType = selectedChart ? (selectedChart.chart_type.startsWith('widget_') ? 'control' : 'chart') : 'canvas';
  const setSelectedChart = useCallback((chart: any) => mutate(d => ({ ...d, selectedId: chart?.id ?? null })), [mutate]);
  const [configPanelOpen, setConfigPanelOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const pendingChangeCount = Object.keys(draft.changes).filter(key => !draft.deleted.includes(Number(key))).length + Object.keys(draft.metadata).length;
  const pendingCount = pendingChangeCount + draft.added.length + draft.deleted.length;
  const draftDashboard = useMemo(() => current ? { ...current, ...draft.metadata, charts: allCharts } : null, [current, draft.metadata, allCharts]);
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
  const updatePageLayout = (breakpoint: PageBreakpoint, items?: PageItem[]) => {
    if (!current || savingLock.current) return;
    mutate(d => {
      const changes = { ...d.changes };
      const configs = new Map(allCharts.map(chart => [chart.id, withPageRect(chart.config || {}, breakpoint, items?.find(item => item.chart.id === chart.id)?.rect)]));
      for (const chart of allCharts.filter(c => c.id >= 0)) {
        changes[chart.id] = { ...changes[chart.id], config: configs.get(chart.id)! };
      }
      const filters = d.metadata.filters || current.filters || {};
      return { ...d, changes, added: d.added.map(c => ({ ...c, config: configs.get(c.id) || c.config })),
        metadata: { ...d.metadata, filters: { ...filters, design: { ...filters.design, version: 1, page: { ...filters.design?.page, version: 1 } } } } };
    });
  };
  const handlePropertyChange = (chartId: number, field: string, value: any) => updateLocalChart(chartId, { [field]: value });
  const handlePositionChange = (chartId: number, axis: 'x' | 'y' | 'w' | 'h', value: number) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (chart && Number.isFinite(value)) updateLocalChart(chartId, { position: chartRect({ ...chart, position: { ...chartRect(chart), [axis]: value } }) });
  };
  const handleWidgetConfigChange = (chartId: number, key: string, value: any) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (chart) updateLocalChart(chartId, { config: { ...chart.config, [key]: value } });
  };
  const handleAddFromPanel = useCallback((item: ChartTypeItem, position: { x: number; y: number }, config: Record<string, any> = {}) => {
    if (savingLock.current) return;
    const widget = item.category === 'widget';
    const size = widget ? DEFAULT_WIDGET_SIZE : DEFAULT_CHART_SIZE;
    const chart: DashboardChart = { id: tempIdCounter--, dashboard_id: id, name: item.label, chart_type: item.value,
      sql_query: '', source_type: widget ? 'widget' : 'empty', source_id: null, data_cache: null,
      config: widget ? { paramKey: '', label: item.label, labelPosition: 'left', ...config } : config,
      position: { ...position, ...size }, created_at: '', updated_at: '' };
    mutate(d => {
      const filters = d.metadata.filters || current?.filters || {};
      return { ...d, added: [...d.added, chart], selectedId: chart.id,
        metadata: config.pageLayout ? { ...d.metadata, filters: { ...filters, design: { ...filters.design, version: 1, page: { ...filters.design?.page, version: 1 } } } } : d.metadata };
    });
    return chart;
  }, [id, mutate, current]);
  const handleDragEnd = (chartId: number, position: { x: number; y: number }) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (chart) updateLocalChart(chartId, { position: { ...chartRect(chart), ...position } });
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
    const preservedConfig = { ...config, ...(selectedChart.config?.pageLayout !== undefined ? { pageLayout: selectedChart.config.pageLayout } : {}) };
    const updates: Partial<DashboardChart> = { config: preservedConfig };
    if (_chart_type) updates.chart_type = _chart_type;
    if (selectedChart.query_source !== 'semantic' && !selectedChart.semantic_query) {
      if (_sql_query !== undefined && _sql_query !== selectedChart.sql_query) updates.sql_query = _sql_query;
      if (_datasource_id && _datasource_id !== selectedChart.source_id) {
        updates.source_id = _datasource_id; updates.config = { ...preservedConfig, datasource_id: _datasource_id };
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
        savedCheckpoint();
        mutate(d => ({ ...d, added: d.added.filter(c => c.id !== tempId), selectedId: d.selectedId === tempId ? realId : d.selectedId }), id);
      }
      for (const [chartId, changes] of Object.entries(snapshot.changes)) {
        if (snapshot.deleted.includes(Number(chartId))) continue;
        await store.updateChart(id, Number(chartId), changes, true);
        savedCheckpoint();
        mutate(d => { const remaining = { ...d.changes }; delete remaining[Number(chartId)]; return { ...d, changes: remaining }; }, id);
      }
      for (const chartId of snapshot.deleted) {
        await store.deleteChart(id, chartId, true);
        savedCheckpoint();
        mutate(d => ({ ...d, deleted: d.deleted.filter(v => v !== chartId) }), id);
      }
      if (Object.keys(snapshot.metadata).length) {
        await store.updateDashboard(id, snapshot.metadata, true);
        savedCheckpoint();
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
    undo, redo, transaction, canUndo: !saving && !!histories.current[id]?.past.length, canRedo: !saving && !!histories.current[id]?.future.length,
    hasUnsavedChanges: pendingCount > 0, pendingCount, pendingChangeCount, pendingDeleteCount: draft.deleted.length,
    updateMetadata, updateCanvas, updatePageLayout, getChartData: chartData, isNewChart: (chartId: number) => chartId < 0,
    updateLocalChart, handlePropertyChange, handlePositionChange, handleWidgetConfigChange, handleAddFromPanel,
    handleDragEnd, handleResizeEnd, handleDeleteSelected, handleSaveChartConfig, saveAllChanges, handleSelectElement,
    createFromTemplate: store.createFromTemplate };
}
