import { create } from 'zustand';
import { toast } from 'sonner';
import client from '../api/client';

export interface DashboardChart {
  id: number;
  dashboard_id: number;
  name: string;
  chart_type: string;
  sql_query: string;
  semantic_query?: Record<string, any> | null;
  query_source?: 'raw_sql' | 'semantic';
  config: Record<string, any>;
  position: { x: number; y: number; w: number; h: number };
  source_type: string;
  source_id: number | null;
  data_cache: string | null;
  created_at: string;
  updated_at: string;
}

export interface DashboardParam {
  name: string;
  label: string;
  type: 'select' | 'text' | 'number' | 'date';
  options?: string[];
  default?: string;
  placeholder?: string;
}

export interface PageParam {
  name: string;
  type: 'string' | 'number' | 'date' | 'date_range';
  default: string;
  label: string;
}

export type DashboardStatus = 'designing' | 'enabled' | 'closed';

export interface Dashboard {
  id: number;
  name: string;
  description: string;
  layout: any[];
  filters: Record<string, any>;
  params: DashboardParam[];
  page_params: PageParam[];
  status: DashboardStatus;
  owner_id: number;
  is_public: boolean;
  is_default: boolean;
  carousel_interval: number;
  sort_order: number;
  charts: DashboardChart[];
  created_at: string;
  updated_at: string;
}

export const DASHBOARD_STATUS_MAP: Record<DashboardStatus, { label: string; color: string }> = {
  designing: { label: '设计中', color: 'text-blue-500 bg-blue-500/10 border-blue-500/20' },
  enabled: { label: '已启用', color: 'text-green-500 bg-green-500/10 border-green-500/20' },
  closed: { label: '已关闭', color: 'text-gray-500 bg-gray-500/10 border-gray-500/20' },
};

interface DashboardState {
  dashboards: Dashboard[];
  currentId: number | null;
  currentWorkspaceId: number;
  loading: boolean;
  error: string | null;
  globalFilters: Record<string, any>;
  crossFilters: any[];
  favorites: number[];
  paramValues: Record<string, any>;
  pageParams: PageParam[];
  pageParamValues: Record<string, any>;
  refreshing: boolean;
  refreshingChartIds: Set<number>;

  loadDashboards: (workspaceId?: number) => Promise<void>;
  setCurrent: (id: number | null) => void;
  createDashboard: (name: string, workspaceId?: number) => Promise<number>;
  updateDashboard: (id: number, data: Partial<Dashboard>, deferReload?: boolean) => Promise<void>;
  deleteDashboard: (id: number) => Promise<void>;
  copyDashboard: (id: number) => Promise<void>;
  setDefault: (id: number) => Promise<void>;
  addChart: (dashboardId: number, chart: Partial<DashboardChart>, deferReload?: boolean) => Promise<number>;
  updateChart: (dashboardId: number, chartId: number, data: Partial<DashboardChart>, deferReload?: boolean) => Promise<void>;
  deleteChart: (dashboardId: number, chartId: number, deferReload?: boolean) => Promise<void>;
  saveLayout: (dashboardId: number, layouts: { chart_id: number; position: any }[]) => Promise<void>;
  reorderDashboards: (orders: { id: number; sort_order: number }[]) => Promise<void>;
  setGlobalFilters: (filters: Record<string, any>) => void;
  setCrossFilters: (filters: any[]) => void;
  toggleFavorite: (dashboardId: number) => void;
  createFromTemplate: (template: any) => Promise<number>;
  setParamValue: (name: string, value: any) => void;
  setPageParams: (params: PageParam[]) => void;
  setPageParamValue: (name: string, value: any) => void;
  initPageParamValues: () => void;
  refreshCharts: () => Promise<void>;
  refreshSingleChart: (chartId: number, extra?: { page_limit?: number; page_offset?: number; count_sql?: string }) => Promise<void>;
}

let loadVersion = 0;
const refreshVersions = new Map<number, number>();

export const useDashboardStore = create<DashboardState>((set, get) => ({
  dashboards: [],
  currentId: null,
  currentWorkspaceId: 0,
  loading: false,
  error: null,
  globalFilters: {},
  crossFilters: [],
  favorites: (() => {
    try {
      const saved = localStorage.getItem('dashboard_favorites');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  })(),
  paramValues: {},
  pageParams: [],
  pageParamValues: {},
  refreshing: false,
  refreshingChartIds: new Set(),

  loadDashboards: async (workspaceId?: number) => {
    // Remember workspace context so subsequent operations reload the correct scope
    const wsId = workspaceId ?? get().currentWorkspaceId ?? 0;
    const version = ++loadVersion;
    if (wsId !== get().currentWorkspaceId) { set({ dashboards: [] }); get().setCurrent(null); }
    set({ loading: true, error: null, currentWorkspaceId: wsId });
    try {
      const params = wsId ? `?workspace_id=${wsId}` : '';
      const { data } = await client.get(`/dashboard/${params}`);
      if (version !== loadVersion) return;
      if (!Array.isArray(data)) throw new Error('仪表盘接口格式错误');
      const dashboards = data.map((d: Dashboard) => ({ ...d, filters: d.filters || {}, charts: d.charts || [], page_params: d.page_params || d.filters?.pageParams || [] }));
      set({ dashboards });
      const state = get();
      if (!dashboards.some(d => d.id === state.currentId)) {
        const defaultDb = dashboards.find(d => d.is_default);
        get().setCurrent(defaultDb?.id || dashboards[0]?.id || null);
      }
    } catch {
      if (version === loadVersion) set({ error: '看板加载失败，已保留本地已确认的数据，请重试' });
    } finally {
      if (version === loadVersion) set({ loading: false });
    }
  },

  setCurrent: (id) => {
    if (get().currentId === id) return;
    const dashboard = get().dashboards.find(d => d.id === id);
    const pageParams = dashboard?.page_params || [];
    const pageParamValues: Record<string, any> = {};
    for (const p of pageParams) {
      pageParamValues[p.name] = p.default ?? '';
    }
    set({
      currentId: id,
      paramValues: Object.fromEntries((dashboard?.params || []).map(p => [p.name, p.default ?? ''])),
      globalFilters: {},
      crossFilters: [],
      pageParams,
      pageParamValues,
      refreshing: false,
    });
  },

  createDashboard: async (name, workspaceId) => {
    const wsId = workspaceId || get().currentWorkspaceId || 0;
    const { data } = await client.post('/dashboard/', { name, workspace_id: wsId });
    await get().loadDashboards(wsId);
    get().setCurrent(data.id);
    return data.id;
  },

  updateDashboard: async (id, updates, deferReload = false) => {
    const { page_params, ...payload } = updates;
    if (page_params !== undefined) payload.filters = { ...(updates.filters || get().dashboards.find(d => d.id === id)?.filters || {}), pageParams: page_params };
    await client.put(`/dashboard/${id}`, payload);
    set(state => ({ dashboards: state.dashboards.map(d => d.id === id ? { ...d, ...updates, ...payload } : d) }));
    if (get().currentId === id && page_params !== undefined) get().setPageParams(page_params);
    if (!deferReload) await get().loadDashboards(get().currentWorkspaceId);
  },

  deleteDashboard: async (id) => {
    await client.delete(`/dashboard/${id}`);
    const state = get();
    const remaining = state.dashboards.filter(d => d.id !== id);
    set({ dashboards: remaining });
    if (state.currentId === id) get().setCurrent(remaining[0]?.id ?? null);
    await get().loadDashboards(get().currentWorkspaceId);
  },

  copyDashboard: async (id) => {
    const { data } = await client.post(`/dashboard/${id}/copy`);
    await get().loadDashboards(get().currentWorkspaceId);
    get().setCurrent(data.id);
    toast.success('仪表盘已拷贝');
  },

  setDefault: async (id) => {
    await client.put(`/dashboard/${id}`, { is_default: true });
    await get().loadDashboards(get().currentWorkspaceId);
  },

  addChart: async (dashboardId, chart, deferReload = false) => {
    const { data } = await client.post(`/dashboard/${dashboardId}/charts`, chart);
    const id = Number(data.id);
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error('新增图表未返回有效 ID');
    set(state => ({ dashboards: state.dashboards.map(d => d.id === dashboardId
      ? { ...d, charts: [...d.charts, { ...chart, id, dashboard_id: dashboardId } as DashboardChart] } : d) }));
    if (!deferReload) await get().loadDashboards(get().currentWorkspaceId);
    return id;
  },

  updateChart: async (dashboardId, chartId, data, deferReload = false) => {
    await client.put(`/dashboard/${dashboardId}/charts/${chartId}`, data);
    set(state => ({ dashboards: state.dashboards.map(d => d.id === dashboardId
      ? { ...d, charts: d.charts.map(c => c.id === chartId ? { ...c, ...data } : c) } : d) }));
    if (!deferReload) await get().loadDashboards(get().currentWorkspaceId);
  },

  deleteChart: async (dashboardId, chartId, deferReload = false) => {
    await client.delete(`/dashboard/${dashboardId}/charts/${chartId}`);
    set(state => ({ dashboards: state.dashboards.map(d => d.id === dashboardId
      ? { ...d, charts: d.charts.filter(c => c.id !== chartId) } : d) }));
    if (!deferReload) await get().loadDashboards(get().currentWorkspaceId);
  },

  saveLayout: async (dashboardId, layouts) => {
    await client.put(`/dashboard/${dashboardId}/layout`, { layouts });
    set(state => ({
      dashboards: state.dashboards.map(d => {
        if (d.id !== dashboardId) return d;
        return {
          ...d,
          charts: d.charts.map(c => {
            const layout = layouts.find(l => l.chart_id === c.id);
            return layout ? { ...c, position: layout.position } : c;
          }),
        };
      }),
    }));
  },

  reorderDashboards: async (orders) => {
    await client.post('/dashboard/reorder', { orders });
    await get().loadDashboards(get().currentWorkspaceId);
  },

  setGlobalFilters: (filters) => set({ globalFilters: filters }),

  setCrossFilters: (filters) => set({ crossFilters: filters }),

  toggleFavorite: (dashboardId) => {
    const { favorites } = get();
    const newFavorites = favorites.includes(dashboardId)
      ? favorites.filter(id => id !== dashboardId)
      : [...favorites, dashboardId];
    localStorage.setItem('dashboard_favorites', JSON.stringify(newFavorites));
    set({ favorites: newFavorites });
  },

  setParamValue: (name, value) => {
    set(state => ({ paramValues: { ...state.paramValues, [name]: value } }));
  },

  setPageParams: (params) => {
    set({ pageParams: params });
    const currentValues = get().pageParamValues;
    const newValues: Record<string, any> = { ...currentValues };
    for (const p of params) {
      if (newValues[p.name] === undefined || newValues[p.name] === null) {
        newValues[p.name] = p.default ?? '';
      }
    }
    set({ pageParamValues: newValues });
  },

  setPageParamValue: (name, value) => {
    set(state => ({ pageParamValues: { ...state.pageParamValues, [name]: value } }));
  },

  initPageParamValues: () => {
    const { pageParams, pageParamValues } = get();
    const newValues: Record<string, any> = { ...pageParamValues };
    for (const p of pageParams) {
      if (newValues[p.name] === undefined || newValues[p.name] === null) {
        newValues[p.name] = p.default ?? '';
      }
    }
    set({ pageParamValues: newValues });
  },

  refreshCharts: async () => {
    const { currentId, dashboards } = get();
    if (!currentId) return;
    const dashboard = dashboards.find(d => d.id === currentId);
    if (!dashboard) return;
    const charts = dashboard.charts.filter(c => (c.semantic_query || c.sql_query) && !c.chart_type.startsWith('widget_'));
    await Promise.all(charts.map(c => {
      const cfg = c.config || {};
      if (cfg.enableServerPagination) {
        return get().refreshSingleChart(c.id, { page_limit: cfg.pageLimit || 20, page_offset: 0, count_sql: cfg.countSql });
      }
      return get().refreshSingleChart(c.id);
    }));
  },

  refreshSingleChart: async (chartId, extra?) => {
    const { currentId, paramValues, pageParamValues } = get();
    if (!currentId || !get().dashboards.find(d => d.id === currentId)?.charts.some(c => c.id === chartId)) return;
    const version = (refreshVersions.get(chartId) || 0) + 1;
    refreshVersions.set(chartId, version);
    set(state => {
      const ids = new Set(state.refreshingChartIds);
      ids.add(chartId);
      return { refreshingChartIds: ids, refreshing: true };
    });
    try {
      const body: any = { params: { ...paramValues, ...pageParamValues } };
      if (extra?.page_limit != null) body.page_limit = extra.page_limit;
      if (extra?.page_offset != null) body.page_offset = extra.page_offset;
      if (extra?.count_sql) body.count_sql = extra.count_sql;

      const { data } = await client.post(`/dashboard/${currentId}/charts/${chartId}/refresh`, body);
      if (refreshVersions.get(chartId) !== version) return;
      if (data && !data.error) {
        set(state => ({
          dashboards: state.dashboards.map(d => {
            if (d.id !== currentId) return d;
            return {
              ...d,
              charts: d.charts.map(c => {
                if (c.id !== chartId) return c;
                const cache: any = { columns: data.columns, rows: data.rows };
                if (data.total != null) cache.total = data.total;
                return { ...c, data_cache: JSON.stringify(cache) };
              }),
            };
          }),
        }));
      } else if (data?.error) {
        toast.error(`图表刷新失败: ${data.error}`);
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '图表刷新失败');
    } finally {
      if (refreshVersions.get(chartId) === version) set(state => {
        const ids = new Set(state.refreshingChartIds);
        ids.delete(chartId);
        const currentCharts = state.dashboards.find(d => d.id === state.currentId)?.charts || [];
        return { refreshingChartIds: ids, refreshing: currentCharts.some(c => ids.has(c.id)) };
      });
    }
  },

  createFromTemplate: async (template) => {
    const { createDashboard, addChart, updateDashboard } = get();
    const dashboardId = await createDashboard(template.name);
    try {
      await updateDashboard(dashboardId, {
        description: template.description || '', filters: template.filters || {},
        params: template.params || [], page_params: template.page_params || template.filters?.pageParams || [], carousel_interval: template.carousel_interval || 0,
      }, true);
      for (const chart of template.charts || []) {
        const { sql, ...config } = chart.config || {};
        const semantic = chart.query_source === 'semantic' || !!chart.semantic_query;
        await addChart(dashboardId, {
          name: chart.name, chart_type: chart.chart_type,
          sql_query: semantic ? '' : (chart.sql_query ?? sql ?? ''),
          semantic_query: chart.semantic_query,
          query_source: semantic ? 'semantic' : 'raw_sql',
          position: chart.position, config,
          source_type: chart.source_type || 'template', source_id: chart.source_id,
        }, true);
      }
    } finally {
      await get().loadDashboards(get().currentWorkspaceId);
    }
    return dashboardId;
  },
}));
