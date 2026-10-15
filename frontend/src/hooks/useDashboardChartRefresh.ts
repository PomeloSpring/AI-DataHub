import { useCallback } from 'react';
import client from '@/api/client';
import { useDashboardStore } from '@/stores/dashboardStore';

/**
 * 看板"刷新数据"共享逻辑：逐个数据图表 POST /dashboard/{id}/charts/{cid}/refresh
 * 下 push 数据源重查询，把返回的 columns/rows 回写本地 detail.charts[i].data_cache。
 *
 * 为什么不能只 GET /dashboard/{id}：GET 只回 DB 缓存，不重查数据源，
 * 用户点"刷新数据"看到页面转了一圈但数据没变——是缺陷不是特性。
 * widget_* 组件没有 SQL 语义，不参与重查询。
 */
export function useDashboardChartRefresh(
  detail: any,
  setDetail: (updater: (prev: any) => any) => void,
) {
  return useCallback(async (): Promise<boolean> => {
    if (!detail?.id) return false;
    const charts: any[] = (detail.charts || []).filter((c: any) =>
      (c.semantic_query || c.sql_query) && !String(c.chart_type || '').startsWith('widget_'));
    if (!charts.length) return true;
    const { paramValues, pageParamValues } = useDashboardStore.getState();
    const params = { ...paramValues, ...pageParamValues };
    const dashboardId = detail.id;
    const updates: Record<number, string> = {};
    const results = await Promise.all(charts.map(async (c: any) => {
      const cfg = c.config || {};
      const body: any = { params };
      if (cfg.enableServerPagination) {
        body.page_limit = cfg.pageLimit || 20;
        body.page_offset = 0;
        if (cfg.countSql) body.count_sql = cfg.countSql;
      }
      try {
        const { data } = await client.post(`/dashboard/${dashboardId}/charts/${c.id}/refresh`, body);
        if (data && !data.error) {
          const cache: any = { columns: data.columns, rows: data.rows };
          if (data.total != null) cache.total = data.total;
          updates[c.id] = JSON.stringify(cache);
          return true;
        }
        return false;
      } catch { return false; }
    }));
    setDetail((prev: any) => (prev && prev.id === dashboardId)
      ? { ...prev, charts: (prev.charts || []).map((x: any) => (updates[x.id] ? { ...x, data_cache: updates[x.id] } : x)) }
      : prev);
    return results.every(Boolean);
  }, [detail, setDetail]);
}
