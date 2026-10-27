import { useState, useCallback, useMemo, useRef } from 'react';
import { toast } from '@/lib/toast';
import client from '../api/client';
import { useDashboardStore, type Dashboard, type DashboardChart } from '../stores/dashboardStore';
import type { ChartTypeItem } from '../components/DashboardChart';
import { DEFAULT_CHART_SIZE, DEFAULT_WIDGET_SIZE } from './useCanvasInteraction';
import { chartData, chartRect, getCanvas } from '@/lib/dashboardDesign';
import { withPageRect, PAGE_LAYOUT_VERSION, type PageBreakpoint, type PageItem } from '@/lib/dashboardPageLayout';

let tempIdCounter = -1;
type ElementType = 'chart' | 'control' | 'canvas';
export const INTERACT_MODE_STORAGE_KEY = 'adh-editor-interact-mode';
interface Draft {
  changes: Record<number, Partial<DashboardChart>>; added: DashboardChart[]; deleted: number[];
  metadata: Partial<Dashboard>; selectedIds: number[];
}
const emptyDraft = (): Draft => ({ changes: {}, added: [], deleted: [], metadata: {}, selectedIds: [] });
const OP_LABELS: Record<string, string> = { create: '创建', update: '更新', delete: '删除' };
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
  const selectedChart = allCharts.find(c => c.id === draft.selectedIds[0]) || null;
  const selectedCharts = draft.selectedIds.map(id => allCharts.find(c => c.id === id)).filter((c): c is DashboardChart => !!c);
  const selectedElementType: ElementType = selectedChart ? (selectedChart.chart_type.startsWith('widget_') ? 'control' : 'chart') : 'canvas';
  const setSelection = useCallback((ids: number[]) => mutate(d => ({ ...d, selectedIds: ids })), [mutate]);
  const toggleSelection = useCallback((id: number) => mutate(d => ({
    ...d, selectedIds: d.selectedIds.includes(id) ? d.selectedIds.filter(v => v !== id) : [...d.selectedIds, id],
  })), [mutate]);
  const setSelectedChart = useCallback((chart: DashboardChart | null) => mutate(d => ({ ...d, selectedIds: chart ? [chart.id] : [] })), [mutate]);
  const [configPanelOpen, setConfigPanelOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  // 编辑态交互模式：select=控件 inert（可正常选中/拖动），interact=试运行（控件真实联动）。
  // 默认 select：编辑器惯例是「素材优先」，避免点控件既不改参数也不选中的静默死区。
  const [interactionMode, setInteractionModeState] = useState<'select' | 'interact'>(
    () => window.localStorage.getItem(INTERACT_MODE_STORAGE_KEY) === 'interact' ? 'interact' : 'select');
  const setInteractionMode = useCallback((mode: 'select' | 'interact') => {
    window.localStorage.setItem(INTERACT_MODE_STORAGE_KEY, mode);
    setInteractionModeState(mode);
  }, []);
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
        metadata: { ...d.metadata, filters: { ...filters, design: { ...filters.design, version: 1, page: { ...filters.design?.page, version: PAGE_LAYOUT_VERSION } } } } };
    });
  };
  /** 看板级页面设置（当前只有间距）：写 filters.design.page.gap，与 updateCanvas 同一套草稿通道 */
  const updatePage = useCallback((updates: Record<string, any>) => {
    if (!current || savingLock.current) return;
    mutate(d => {
      const filters = d.metadata.filters || current.filters || {};
      return { ...d, metadata: { ...d.metadata, filters: { ...filters,
        design: { ...filters.design, version: 1, page: { ...filters.design?.page, version: PAGE_LAYOUT_VERSION, ...updates } } } } };
    });
  }, [current, mutate]);
  const handlePropertyChange = (chartId: number, field: string, value: any) => {
    // 图表卡与控件卡不得跨类改型：改型后 source_type / 数据配置与渲染口径会分叉
    if (field === 'chart_type') {
      const chart = allCharts.find(c => c.id === chartId);
      const wasWidget = !!chart?.chart_type?.startsWith('widget_');
      if (chart && String(value || '').startsWith('widget_') !== wasWidget) {
        toast.error(`「${chart.name}」只能在${wasWidget ? '控件' : '图表'}同类内换型；如需另一种类型请删除后从组件库拖入`);
        return;
      }
    }
    updateLocalChart(chartId, { [field]: value });
  };
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
      return { ...d, added: [...d.added, chart], selectedIds: [chart.id],
        metadata: config.pageLayout ? { ...d.metadata, filters: { ...filters, design: { ...filters.design, version: 1, page: { ...filters.design?.page, version: PAGE_LAYOUT_VERSION } } } } : d.metadata };
    });
    return chart;
  }, [id, mutate, current]);
  // 从数据集引入: 生成 source_type='dataset' 图表卡片(预设图表类型/字段映射搬入 config),
  // 不复制 SQL 到 adh_charts.sql_query, 取数统一走数据集治理入口(单口径)。
  const handleAddFromDataset = useCallback((dataset: {
    id: number; name: string; chart_type?: string; chart_preset?: Record<string, any>;
  }, position: { x: number; y: number }) => {
    if (savingLock.current) return;
    const preset = dataset.chart_preset || {};
    const config: Record<string, any> = {};
    if (preset.xCol) config.xCol = preset.xCol;
    if (preset.yCol) config.yCol = preset.yCol;
    if (preset.groupCol) config.groupCol = preset.groupCol;
    if (preset.limit) config.limit = preset.limit;
    const chart: DashboardChart = {
      id: tempIdCounter--, dashboard_id: id, name: dataset.name,
      chart_type: dataset.chart_type || 'bar',
      sql_query: '', source_type: 'dataset', source_id: dataset.id, data_cache: null,
      config, position: { ...position, ...DEFAULT_CHART_SIZE }, created_at: '', updated_at: '' };
    mutate(d => ({ ...d, added: [...d.added, chart], selectedIds: [chart.id] }));
    return chart;
  }, [id, mutate]);
  const handleDragEnd = (chartId: number, position: { x: number; y: number }) => {
    const chart = allCharts.find(c => c.id === chartId);
    if (chart) updateLocalChart(chartId, { position: { ...chartRect(chart), ...position } });
  };
  const handleResizeEnd = (chartId: number, position: DashboardChart['position']) => updateLocalChart(chartId, { position });
  /** 删除可撤销：接现有 undo 栈，避免“直接删了且不知道能不能回” */
  const notifyDeleted = useCallback((removed: DashboardChart[]) => {
    if (!removed.length) return;
    const label = removed.length === 1 ? `「${removed[0].name}」` : `${removed.length} 个组件`;
    toast(`已删除${label}`, { action: { label: '撤销', onClick: () => undo() } });
  }, [undo]);
  const handleDeleteSelected = useCallback((chart: DashboardChart | null) => {
    if (!chart || savingLock.current) return;
    mutate(d => {
      const changes = { ...d.changes }; delete changes[chart.id];
      return { ...d, changes, added: d.added.filter(c => c.id !== chart.id),
        deleted: chart.id < 0 ? d.deleted : Array.from(new Set([...d.deleted, chart.id])), selectedIds: [] };
    });
    notifyDeleted([chart]);
  }, [mutate, notifyDeleted]);
  /** 批量删除当前选中集（多选后一次 Delete） */
  const handleDeleteMany = useCallback((chartsToDelete: DashboardChart[]) => {
    if (!chartsToDelete.length || savingLock.current) return;
    const ids = new Set(chartsToDelete.map(c => c.id));
    mutate(d => {
      const changes = { ...d.changes };
      for (const id of ids) delete changes[id];
      const deleted = new Set(d.deleted);
      for (const c of chartsToDelete) if (c.id >= 0) deleted.add(c.id);
      return { ...d, changes, added: d.added.filter(c => !ids.has(c.id)), deleted: [...deleted], selectedIds: [] };
    });
    notifyDeleted(chartsToDelete);
  }, [mutate, notifyDeleted]);
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
    const nameOf = (chartId: number) => allCharts.find(c => c.id === chartId)?.name || `图表 ${chartId}`;
    try {
      const { data } = await client.put(`/dashboard/${id}/batch`, {
        metadata: snapshot.metadata,
        added: snapshot.added.map(chart => {
          const { id: tempId, created_at: _created, updated_at: _updated, data_cache: _cache, ...payload } = chart;
          return payload;
        }),
        changed: Object.fromEntries(Object.entries(snapshot.changes).filter(([chartId]) => !snapshot.deleted.includes(Number(chartId)))),
        deleted: snapshot.deleted,
      });
        // 逐条定位失败项，不再笼统报“保存未完成”让用户猜哪个图表没存上
      const failed: string[] = [...(data.errors || [])];
      // 看板级元数据未落库也算失败（不能只盯图表条目，否则改名/画布设置静默丢）
      if (!data.metadata_saved && Object.keys(snapshot.metadata).length) {
        failed.push('看板级设置（名称/描述/画布/参数）未写入，草稿已保留');
      }
      for (const r of data.results || []) {
        if (r.ok) continue;
        const label = r.op === 'create'
          ? `「${snapshot.added[r.index]?.name || '新图表'}」`
          : `「${nameOf(r.chart_id)}」`;
        failed.push(`${label} ${OP_LABELS[r.op] || r.op}失败：${r.error || '服务端未返回原因'}`);
      }
      if (failed.length) {
        failed.forEach(message => toast.error(message, { duration: 8000 }));
        toast.warning(`共 ${failed.length} 项未保存，草稿已保留，可重试`);
      }
      // 部分成功也要把已写入的那部分从草稿剔掉（否则重试会重复创建）并建立新基线
      const createdIndexes = new Set((data.results || []).filter((r: any) => r.op === 'create' && r.ok).map((r: any) => r.index));
      const doneIds = new Set((data.results || []).filter((r: any) => r.op !== 'create' && r.ok).map((r: any) => r.chart_id));
      const tempToReal = new Map<number, number>();
      (data.results || []).forEach((r: any) => {
        if (r.op === 'create' && r.ok) {
          const temp = snapshot.added[r.index]?.id;
          if (temp !== undefined) tempToReal.set(temp, r.chart_id);
        }
      });
      mutate(d => ({
        ...d,
        added: d.added.filter((_, index) => !createdIndexes.has(index)),
        changes: Object.fromEntries(Object.entries(d.changes).filter(([chartId]) => !doneIds.has(Number(chartId)))),
        deleted: d.deleted.filter(cid => !doneIds.has(cid)),
        metadata: data.metadata_saved || !Object.keys(snapshot.metadata).length ? {} : d.metadata,
        selectedIds: d.selectedIds.map(v => tempToReal.get(v) ?? v),
      }), id);
      if (createdIndexes.size || doneIds.size || data.metadata_saved) savedCheckpoint();
      if (failed.length) return false;
      toast.success('所有更改已保存');
      return true;
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '保存失败，草稿已保留，可重试', { duration: 8000 });
      return false;
    } finally {
      try { await store.loadDashboards(); }
      finally { savingLock.current = false; setSaving(false); }
    }
  }, [current, id, store, mutate, allCharts]);
  const handleSelectElement = (chart: DashboardChart | null, e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (chart && e && (e.shiftKey || e.metaKey || e.ctrlKey)) { toggleSelection(chart.id); return; }
    setSelectedChart(chart);
  };
  return { current, draftDashboard, allCharts, selectedChart, selectedCharts, selectedIds: draft.selectedIds,
    setSelectedChart, setSelection, toggleSelection, handleSelectElement, selectedElementType,
    configPanelOpen, setConfigPanelOpen, templatesOpen, setTemplatesOpen, saving, interactionMode, setInteractionMode,
    undo, redo, transaction, canUndo: !saving && !!histories.current[id]?.past.length, canRedo: !saving && !!histories.current[id]?.future.length,
    hasUnsavedChanges: pendingCount > 0, pendingCount, pendingChangeCount, pendingDeleteCount: draft.deleted.length,
    pendingNewCount: draft.added.length, draftMetadataKeys: Object.keys(draft.metadata),
    updateMetadata, updateCanvas, updatePage, updatePageLayout, getChartData: chartData, isNewChart: (chartId: number) => chartId < 0,
    dirtyIds: Object.keys(draft.changes).map(Number).filter(id => !draft.deleted.includes(id)),
    updateLocalChart, handlePropertyChange, handlePositionChange, handleWidgetConfigChange, handleAddFromPanel,
    handleAddFromDataset,
    handleDragEnd, handleResizeEnd, handleDeleteSelected, handleDeleteMany, handleSaveChartConfig, saveAllChanges,
    createFromTemplate: store.createFromTemplate };
}
