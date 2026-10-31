import { create } from 'zustand';
import { toast } from '@/lib/toast';
import client from '../api/client';
import { invalidateCatalogCache } from '@/lib/dashboardCatalog';
import { chartQueryPool } from '@/lib/chartQueryPool';
import { templateChartPayload, isDemoChart } from '@/lib/dashboardDemo';

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
  /** 服务端裁决: 仅看板属主/管理员可写; 角色授予的可见权不含编辑权(缺省=不可写) */
  editable?: boolean;
}

export const DASHBOARD_STATUS_MAP: Record<DashboardStatus, { label: string; color: string }> = {
  designing: { label: '设计中', color: 'text-blue-500 bg-blue-500/10 border-blue-500/20' },
  enabled: { label: '已启用', color: 'text-green-500 bg-green-500/10 border-green-500/20' },
  closed: { label: '已关闭', color: 'text-gray-500 bg-gray-500/10 border-gray-500/20' },
};

/**
 * 可重查询的数据图表（刷新/查询按钮的作用范围）。
 * - 控件类 widget_* 无取数语义，不参与；
 * - 挂数据集（source_type='dataset'）与 AS-BOT 设计图（config.as_bot_design）同样要参与：
 *   它们在库里不存 sql_query（SQL 由服务端按数据集/设计生成），历史缺陷是过滤只认
 *   sql_query/semantic_query，于是这两类图点刷新永远没反应；
 * - 模板样式示例图（未绑源）由服务端原样回读演示缓存，前端不发起无效查询。
 */
export function isRefreshableDataChart(chart: {
  chart_type?: string; sql_query?: string | null; semantic_query?: Record<string, any> | null;
  source_type?: string; source_id?: number | null; config?: Record<string, any>;
}): boolean {
  if (!chart || String(chart.chart_type || '').startsWith('widget_')) return false;
  if (chart.source_type === 'dataset' && chart.source_id) return true;
  if (chart.config?.as_bot_design) return true;
  if (isDemoChart(chart) && !chart.source_id) return false;
  return !!(chart.sql_query || chart.semantic_query);
}

/** 单图重查询参数：dashboardId 必须显式（宿主可同时挂着多个看板，见 refreshSingleChart） */
export interface ChartRefreshExtra {
  dashboardId?: number;
  /** 诊断文案用的业务名：宿主未登记看板时也能报出图名而非裸 id */
  chartName?: string;
  page_limit?: number;
  page_offset?: number;
  count_sql?: string;
}

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
  /**
   * 实时取数覆盖层（chartId -> data_cache JSON）。
   * 图表数据有四个渲染面（编辑器 / 看板浏览页 / 大屏播放 / 弹窗页），后三者把 GET
   * 到的看板详情存在各自组件里；刷新结果只写 dashboards 时它们读不到，按钮点了
   * 像没反应（只回缓存）。覆盖层按全局唯一 chartId 存放，卡片订阅它即可全场景一致。
   */
  chartCache: Record<number, string>;

  reset: () => void;
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
  /** 宿主页面（看板浏览页 / 大屏）登记当前看板：图表重查询、参数栏、查询与导出控件都以 store 为唯一通道 */
  adoptDashboard: (dashboard: Dashboard) => void;
  setGlobalFilters: (filters: Record<string, any>) => void;
  setCrossFilters: (filters: any[]) => void;
  toggleFavorite: (dashboardId: number) => void;
  createFromTemplate: (template: any) => Promise<number>;
  setParamValue: (name: string, value: any) => void;
  setPageParams: (params: PageParam[]) => void;
  setPageParamValue: (name: string, value: any) => void;
  initPageParamValues: () => void;
  refreshCharts: (dashboardId?: number, charts?: DashboardChart[]) => Promise<boolean>;
  refreshSingleChart: (chartId: number, extra?: ChartRefreshExtra) => Promise<boolean>;
  /** 退出页面 / 切换看板时调用：未执行的图表查询直接丢弃，执行中的直接中止 */
  cancelChartRefresh: () => void;
}

let loadVersion = 0;
let sessionVersion = 0;
const refreshVersions = new Map<number, number>();

/** 接口返回的看板归一为 store 形状（历史字段 pageParams 藏在 filters 里，两处同一口径） */
const normalizeDashboard = (d: Dashboard): Dashboard => ({
  ...d, filters: d.filters || {}, charts: d.charts || [],
  page_params: d.page_params || d.filters?.pageParams || [],
});

/** 诊断文案前缀：优先业务名，不退化成裸 id（ui-resource-display.md） */
const chartLabel = (chartId: number, name?: string) => (name ? `「${name}」` : `图表 ${chartId}`);

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
  chartCache: {},

  reset: () => {
    // 登出 / 切换工作空间：先终止未完成的图表查询，再推进会话版本
    chartQueryPool.cancelAll();
    ++loadVersion; ++sessionVersion; refreshVersions.clear();
    set({ dashboards: [], currentId: null, currentWorkspaceId: 0, loading: false, error: null,
      globalFilters: {}, crossFilters: [], paramValues: {}, pageParams: [], pageParamValues: {}, refreshing: false,
      refreshingChartIds: new Set(), chartCache: {} });
  },

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
      const dashboards = data.map((d: Dashboard) => normalizeDashboard(d));
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
    // 切换看板：上一块板的排队/在跑查询直接丢弃与中止（接口注释既定语义），
    // 否则轮播或切板后旧查询会继续占用图表查询池的槽位。
    get().cancelChartRefresh();
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
    invalidateCatalogCache();
    await get().loadDashboards(wsId);
    get().setCurrent(data.id);
    return data.id;
  },

  updateDashboard: async (id, updates, deferReload = false) => {
    const { page_params, ...payload } = updates;
    if (page_params !== undefined) payload.filters = { ...(updates.filters || get().dashboards.find(d => d.id === id)?.filters || {}), pageParams: page_params };
    await client.put(`/dashboard/${id}`, payload);
    invalidateCatalogCache();
    set(state => ({ dashboards: state.dashboards.map(d => d.id === id ? { ...d, ...updates, ...payload } : d) }));
    if (get().currentId === id && page_params !== undefined) get().setPageParams(page_params);
    if (!deferReload) await get().loadDashboards(get().currentWorkspaceId);
  },

  deleteDashboard: async (id) => {
    await client.delete(`/dashboard/${id}`);
    invalidateCatalogCache();
    const state = get();
    const remaining = state.dashboards.filter(d => d.id !== id);
    set({ dashboards: remaining });
    if (state.currentId === id) get().setCurrent(remaining[0]?.id ?? null);
    await get().loadDashboards(get().currentWorkspaceId);
  },

  copyDashboard: async (id) => {
    const { data } = await client.post(`/dashboard/${id}/copy`);
    invalidateCatalogCache();
    await get().loadDashboards(get().currentWorkspaceId);
    get().setCurrent(data.id);
    toast.success('仪表盘已拷贝');
  },

  setDefault: async (id) => {
    await client.put(`/dashboard/${id}`, { is_default: true });
    invalidateCatalogCache();
    await get().loadDashboards(get().currentWorkspaceId);
  },

  addChart: async (dashboardId, chart, deferReload = false) => {
    const { data } = await client.post(`/dashboard/${dashboardId}/charts`, chart);
    invalidateCatalogCache();
    const id = Number(data.id);
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error('新增图表未返回有效 ID');
    set(state => ({ dashboards: state.dashboards.map(d => d.id === dashboardId
      ? { ...d, charts: [...d.charts, { ...chart, id, dashboard_id: dashboardId } as DashboardChart] } : d) }));
    if (!deferReload) await get().loadDashboards(get().currentWorkspaceId);
    return id;
  },

  updateChart: async (dashboardId, chartId, data, deferReload = false) => {
    await client.put(`/dashboard/${dashboardId}/charts/${chartId}`, data);
    invalidateCatalogCache();
    set(state => ({ dashboards: state.dashboards.map(d => d.id === dashboardId
      ? { ...d, charts: d.charts.map(c => c.id === chartId ? { ...c, ...data } : c) } : d) }));
    if (!deferReload) await get().loadDashboards(get().currentWorkspaceId);
  },

  deleteChart: async (dashboardId, chartId, deferReload = false) => {
    await client.delete(`/dashboard/${dashboardId}/charts/${chartId}`);
    invalidateCatalogCache();
    set(state => ({ dashboards: state.dashboards.map(d => d.id === dashboardId
      ? { ...d, charts: d.charts.filter(c => c.id !== chartId) } : d) }));
    if (!deferReload) await get().loadDashboards(get().currentWorkspaceId);
  },

  saveLayout: async (dashboardId, layouts) => {
    await client.put(`/dashboard/${dashboardId}/layout`, { layouts });
    invalidateCatalogCache();
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
    invalidateCatalogCache();
    await get().loadDashboards(get().currentWorkspaceId);
  },

  adoptDashboard: (dashboard) => {
    if (!dashboard?.id) return;
    const normalized = normalizeDashboard(dashboard);
    set(state => ({
      dashboards: state.dashboards.some(d => d.id === normalized.id)
        ? state.dashboards.map(d => (d.id === normalized.id ? normalized : d))
        : [...state.dashboards, normalized],
    }));
    // 登记为当前看板：参数栏(DashboardRuntimeParams)、查询按钮、导出控件都以 currentId 为上下文。
    // 浏览页/大屏此前不登记，导致参数栏整块不渲染、控件按钮点了没反应。
    get().setCurrent(normalized.id);
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

  refreshCharts: async (dashboardId, charts) => {
    // 看板 id 显式解析：浏览页/大屏/弹窗页可能同时呈现多个看板的图表，只读全局 currentId
    // 会让"非当前看板"的刷新静默 no-op（用户看到的就是"点了没反应、只回缓存"）。
    const dashId = Number(dashboardId) || get().currentId || 0;
    if (!dashId) {
      // 上下文缺失时不得静默返回，必须提醒用户这次没有发起查询（no-silent-degradation）
      toast.error('刷新未发起：看板数据尚未加载完成，请稍后重试');
      return false;
    }
    const list = charts ?? get().dashboards.find(d => d.id === dashId)?.charts ?? [];
    const targets = list.filter(isRefreshableDataChart);
    // 纯控件看板没有可重查询的图表：不算失败，但也不谎称重新取了数
    if (!targets.length) return true;
    // Promise.all 仍是“全部完成才算成功”，但实际并发由 chartQueryPool 收敛到 4，
    // 不会再把浏览器每源连接打满而卡住其他功能的请求
    const results = await Promise.all(targets.map(c => {
      const cfg = c.config || {};
      if (cfg.enableServerPagination) {
        return get().refreshSingleChart(c.id, {
          dashboardId: dashId, chartName: c.name,
          page_limit: cfg.pageLimit || 20, page_offset: 0, count_sql: cfg.countSql,
        });
      }
      return get().refreshSingleChart(c.id, { dashboardId: dashId, chartName: c.name });
    }));
    return results.every(Boolean);
  },

  cancelChartRefresh: () => {
    // 未执行的排队查询直接丢弃、执行中的直接中止；同时推进会话版本，
    // 避免取消后仍有迟到响应写回已离开的看板
    chartQueryPool.cancelAll();
    ++sessionVersion;
    refreshVersions.clear();
    set({ refreshing: false, refreshingChartIds: new Set() });
  },

  refreshSingleChart: async (chartId, extra?) => {
    const dashId = Number(extra?.dashboardId) || get().currentId || 0;
    if (!dashId) {
      toast.error(`${chartLabel(chartId, extra?.chartName)}未发起查询：看板数据尚未加载完成，请稍后重试`);
      return false;
    }
    const session = sessionVersion;
    const version = (refreshVersions.get(chartId) || 0) + 1;
    refreshVersions.set(chartId, version);
    set(state => {
      const ids = new Set(state.refreshingChartIds);
      ids.add(chartId);
      return { refreshingChartIds: ids, refreshing: true };
    });
    try {
      const { paramValues, pageParamValues } = get();
      const body: any = { params: { ...paramValues, ...pageParamValues } };
      if (extra?.page_limit != null) body.page_limit = extra.page_limit;
      if (extra?.page_offset != null) body.page_offset = extra.page_offset;
      if (extra?.count_sql) body.count_sql = extra.count_sql;

      // 走图表查询专用池：渐进式（30 个图表也只同时跑 4 个，其余排队）、有超时、可随页面退出中止
      const result = await chartQueryPool.run(signal =>
        client.post(`/dashboard/${dashId}/charts/${chartId}/refresh`, body, { signal }).then(r => r.data));
      if (!result.ok) {
        if (result.reason === 'cancelled') return false; // 退出页面是预期行为，不弹错
        if (session === sessionVersion) {
          const label = chartLabel(chartId, extra?.chartName);
          toast.error(result.reason === 'timeout'
            ? `${label}查询超过 ${result.seconds} 秒未返回，已取消`
            : `${label}刷新失败`);
        }
        return false;
      }
      const data = result.value;
      if (session !== sessionVersion || refreshVersions.get(chartId) !== version) return false;
      if (data && !data.error) {
        const cache: any = { columns: data.columns, rows: data.rows };
        if (data.total != null) cache.total = data.total;
        const json = JSON.stringify(cache);
        // 写两处：覆盖层让任何渲染面（浏览页/大屏/弹窗页）即时看到新数据，
        // dashboards 条目维持编辑器口径（保存/重载仍以 store 为源）。
        set(state => ({
          chartCache: { ...state.chartCache, [chartId]: json },
          dashboards: state.dashboards.map(d => d.id !== dashId ? d : ({
            ...d,
            charts: d.charts.map(c => c.id === chartId ? { ...c, data_cache: json } : c),
          })),
        }));
        return true;
      } else if (data?.error) {
        toast.error(`${chartLabel(chartId, extra?.chartName)}刷新失败: ${data.error}`);
      } else toast.error(`${chartLabel(chartId, extra?.chartName)}未返回有效结果`);
      return false;
    } catch (e: any) {
      if (session === sessionVersion) toast.error(e.response?.data?.detail || `${chartLabel(chartId, extra?.chartName)}刷新失败`);
      return false;
    } finally {
      if (session === sessionVersion && refreshVersions.get(chartId) === version) set(state => {
        const ids = new Set(state.refreshingChartIds);
        ids.delete(chartId);
        // refreshing = 是否还有图表在取数：宿主可能是浏览页/弹窗页，不能只数 currentId 名下的图
        return { refreshingChartIds: ids, refreshing: ids.size > 0 };
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
        // 样式模板只认显式的 `sample` 字段：内置演示数值经 config.demoData 许可写入 data_cache，
        // 导入后立刻看到样式效果。存量图表的缓存由服务端取数写入，不由客户端注入
        // （否则一张真实图会被误标成"演示数值"，护栏 §7 + no-silent-degradation）。
        const sample = typeof chart.sample === 'string'
          ? (() => { try { return JSON.parse(chart.sample); } catch { return undefined; } })()
          : chart.sample;
        const { sql: legacySql, ...cleanConfig } = chart.config || {};
        const payload = sample ? templateChartPayload({ ...chart, sample, config: cleanConfig }) : {
          name: chart.name, chart_type: chart.chart_type, position: chart.position,
          sql_query: chart.sql_query ?? legacySql ?? '',
          semantic_query: chart.semantic_query,
          query_source: (chart.query_source === 'semantic' || chart.semantic_query ? 'semantic' : 'raw_sql') as 'semantic' | 'raw_sql',
          config: cleanConfig,
          source_type: chart.source_type || 'template', source_id: chart.source_id,
        };
        await addChart(dashboardId, payload as Partial<DashboardChart>, true);
      }
    } finally {
      await get().loadDashboards(get().currentWorkspaceId);
    }
    return dashboardId;
  },
}));
