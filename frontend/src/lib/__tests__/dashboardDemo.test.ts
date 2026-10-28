/**
 * 样式模板 → 新增图表入参（模板只展示样式，不携带任何取数口径）。
 *
 * 锁定：带 sample 的图卡 sql_query/semantic_query 一律为空、只落示例缓存 + demoData 标记；
 * config 不夹带 sql/demoSql（护栏 §7）；不带 sample 的导入体原样保留口径且不注入缓存。
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
    expect(payload.sql_query).toBe('');
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
    expect(payload.sql_query).toBe('');
  });

  it('config 里的 sql / demoSql 一律不回填（护栏 §7：配置不夹 SQL）', () => {
    const payload: any = templateChartPayload({ ...chart, config: { xCol: 'dt', yCol: 'sales', sql: 'SELECT stale', demoSql: 'SELECT demo' } });
    expect(payload.config.sql).toBeUndefined();
    expect(payload.config.demoSql).toBeUndefined();
    expect(payload.config.xCol).toBe('dt');
    expect(JSON.stringify(payload)).not.toMatch(/SELECT/);
  });

  it('没有示例数据的图表保留原口径，且不注入 data_cache', () => {
    const payload: any = templateChartPayload({ ...chart, sample: undefined, sql_query: 'SELECT real FROM t' });
    expect(payload.data_cache).toBeUndefined();
    expect(payload.config[TEMPLATE_DEMO_KEY]).toBeUndefined();
    expect(payload.sql_query).toBe('SELECT real FROM t');
    expect(payload.query_source).toBe('raw_sql');
  });

  it('语义查询图表（导入体）保持 semantic 口径', () => {
    const payload: any = templateChartPayload({ ...chart, sample: undefined, semantic_query: { object: 'orders' }, query_source: 'semantic' });
    expect(payload.query_source).toBe('semantic');
    expect(payload.semantic_query).toEqual({ object: 'orders' });
  });

  it('形状不合法的 sample 不当样式图处理（不静默当空图卡）', () => {
    const payload: any = templateChartPayload({ ...chart, sample: { columns: ['dt'] } as any, sql_query: 'SELECT 1' });
    expect(payload.config[TEMPLATE_DEMO_KEY]).toBeUndefined();
    expect(payload.data_cache).toBeUndefined();
    expect(payload.sql_query).toBe('SELECT 1');
  });

  it('isDemoChart 只认显式标记', () => {
    expect(isDemoChart({ config: { demoData: true } })).toBe(true);
    expect(isDemoChart({ config: { demoData: false } })).toBe(false);
    expect(isDemoChart({ config: {} })).toBe(false);
    expect(isDemoChart(null)).toBe(false);
  });
});
