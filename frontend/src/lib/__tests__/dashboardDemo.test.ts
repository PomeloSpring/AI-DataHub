/**
 * 样式模板 / 导入体 → 新增图表入参（图表不再携带任何取数口径）。
 *
 * 锁定：
 *  - 带 sample 的图卡 = 样式展示：只落示例缓存 + demoData 标记，不携带 SQL/语义查询；
 *  - 入参一律不再出现 `sql_query`（服务端写入口 2026-11-06 起直接拒收，见
 *    tests/test_dashboard_chart_dataset_only.py）；存量图旧版 'query'/'snapshot'
 *    通道降为占位图，由调用侧显式提醒改挂数据集（不静默当已收编）；
 *  - 数据集图保持 dataset + source_id（单口径：不复制 SQL）；
 *  - config 不夹带 sql/demoSql/countSql（护栏 §7 + 统计语句不得由前端递交）。
 */
import { describe, expect, it } from 'vitest';
import { isDemoChart, TEMPLATE_DEMO_KEY, templateChartPayload } from '@/lib/dashboardDemo';

const chart = {
  name: '销售趋势', chart_type: 'line', position: { x: 20, y: 20, w: 600, h: 320 },
  config: { xCol: 'dt', yCol: 'sales' },
  sample: { columns: ['dt', 'sales'], rows: [{ dt: '10-19', sales: 182 }] },
};

describe('templateChartPayload', () => {
  it('样式图卡不携带 SQL，只落示例数据与标记', () => {
    const payload: any = templateChartPayload(chart);
    expect(payload.data_cache).toEqual({ columns: ['dt', 'sales'], rows: [{ dt: '10-19', sales: 182 }] });
    expect(payload.config[TEMPLATE_DEMO_KEY]).toBe(true);
    expect(payload.sql_query).toBeUndefined();
    expect(payload.semantic_query).toBeNull();
    expect(payload.query_source).toBe('raw_sql');
    expect(payload.source_type).toBe('template');
    expect(payload.source_id).toBeNull();
  });

  it('字符串形态的示例数据（导入体）归一成对象后同样按样式图卡处理', () => {
    const payload: any = templateChartPayload({ ...chart, sql_query: 'SELECT legacy FROM dwd_demo', sample: JSON.stringify(chart.sample) });
    expect(payload.data_cache).toEqual(chart.sample);
    expect(payload.config[TEMPLATE_DEMO_KEY]).toBe(true);
    // 旧版模板带进来的占位口径不保留：模板定位是样式展示，不是假想表取数
    expect(payload.sql_query).toBeUndefined();
  });

  it('config 里的 sql / demoSql / countSql 一律不回填（护栏 §7：配置不夹 SQL）', () => {
    const payload: any = templateChartPayload({
      ...chart,
      config: { xCol: 'dt', yCol: 'sales', sql: 'SELECT stale', demoSql: 'SELECT demo', countSql: 'SELECT COUNT(*)' },
    });
    expect(payload.config.sql).toBeUndefined();
    expect(payload.config.demoSql).toBeUndefined();
    expect(payload.config.countSql).toBeUndefined();
    expect(payload.config.xCol).toBe('dt');
    expect(JSON.stringify(payload)).not.toMatch(/SELECT/);
  });

  it('存量图（旧直连通道 + 带口径）导入降为占位图：不再携带 SQL，也不注入缓存', () => {
    const payload: any = templateChartPayload({
      ...chart, sample: undefined, source_type: 'query', source_id: 12, sql_query: 'SELECT real FROM t',
    });
    expect(payload.data_cache).toBeUndefined();
    expect(payload.config[TEMPLATE_DEMO_KEY]).toBeUndefined();
    expect(payload.sql_query).toBeUndefined();
    expect(payload.source_type).toBe('empty');   // 'query' 通道已下线，服务端会拒
    expect(payload.source_id).toBeNull();
    expect(payload.query_source).toBe('raw_sql');
  });

  it('数据集图保持 dataset + source_id（单口径，不复制 SQL）', () => {
    const payload: any = templateChartPayload({
      ...chart, sample: undefined, source_type: 'dataset', source_id: 7, sql_query: 'SELECT stale FROM t',
    });
    expect(payload.source_type).toBe('dataset');
    expect(payload.source_id).toBe(7);
    expect(payload.sql_query).toBeUndefined();
  });

  it('样式图卡自带遗留占位口径时仍是样式图（有 sample 可用，不当待收编存量图）', () => {
    const payload: any = templateChartPayload({ ...chart, source_type: 'query', sql_query: 'SELECT demo FROM t' });
    expect(payload.source_type).toBe('template');
    expect(payload.config[TEMPLATE_DEMO_KEY]).toBe(true);
    expect(payload.sql_query).toBeUndefined();
  });

  it('config.datasource_id 不回填（旧直连通道残留键，通道只由 source_type/source_id 表达）', () => {
    const payload: any = templateChartPayload({
      ...chart, sample: undefined, source_type: 'dataset', source_id: 7,
      config: { xCol: 'dt', yCol: 'sales', datasource_id: 5 },
    });
    expect(payload.config.datasource_id).toBeUndefined();
    expect(payload.config.xCol).toBe('dt');
    expect(JSON.stringify(payload)).not.toMatch(/datasource_id/);
  });

  it('语义查询图表（导入体）保持 semantic 口径', () => {
    const payload: any = templateChartPayload({ ...chart, sample: undefined, semantic_query: { object: 'orders' }, query_source: 'semantic' });
    expect(payload.query_source).toBe('semantic');
    expect(payload.semantic_query).toEqual({ object: 'orders' });
    expect(payload.sql_query).toBeUndefined();
  });

  it('形状不合法的 sample 不当样式图处理（不静默当空图卡）', () => {
    const payload: any = templateChartPayload({ ...chart, sample: { columns: ['dt'] } as any, sql_query: 'SELECT 1' });
    expect(payload.config[TEMPLATE_DEMO_KEY]).toBeUndefined();
    expect(payload.data_cache).toBeUndefined();
    expect(payload.sql_query).toBeUndefined();
  });

  it('isDemoChart 只认显式标记', () => {
    expect(isDemoChart({ config: { demoData: true } })).toBe(true);
    expect(isDemoChart({ config: { demoData: false } })).toBe(false);
    expect(isDemoChart({ config: {} })).toBe(false);
    expect(isDemoChart(null)).toBe(false);
  });
});
