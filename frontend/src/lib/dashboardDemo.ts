/**
 * 看板模板示例数据（demo chart）的服务端口径 helper —— 纯数据，无组件依赖。
 *
 * 单独成文件（不放 dashboardTemplates.ts）：模板清单要引用 CHART_TYPES 做静态校验，
 * 而 DashboardChart 又 import dashboardStore（图表内刷新/导出），store 需要这里的
 * payload 组装 —— 放在 dashboardTemplates 上会把这条链绕成环（模块初始化时
 * CHART_TYPES 还是 undefined）。环只在这一处切断，模板清单仍是唯一事实源。
 */
import type { Rect } from './dashboardDesign';

/** 示例标记唯一键名：config.demoData=true 表示"卡片展示的是演示数值" */
export const TEMPLATE_DEMO_KEY = 'demoData';

export type Sample = { columns: string[]; rows: Record<string, any>[] };

/** 模板图表形状（与 lib/dashboardTemplates.ts 的 TemplateChartDef 一致，此处只取组装所需字段） */
export interface DemoChartInput {
  name: string;
  chart_type: string;
  position: Rect;
  config?: Record<string, any>;
  sample?: Sample | string;
  sql_query?: string;
  semantic_query?: Record<string, any> | null;
  query_source?: string;
  source_type?: string;
  source_id?: number | null;
}

/**
 * 样式模板 / 导入体 → 新增图表入参。
 *
 * 带 `sample` 的图表 = **样式展示图**：不写任何 SQL（模板不对环境里的表结构做假设），
 * 只落示例缓存 + `demoData` 标记：它不进「刷新数据」队列（无查询可跑），
 * 也不会被刷新抹空。导入体（看板导出 JSON）里的存量图表则原样保留口径，
 * 示例标记不存在时不得携带 data_cache（缓存由服务端取数写入，不由客户端注入）。
 */
export function templateChartPayload(chart: DemoChartInput): Record<string, any> {
  const sample = typeof chart.sample === 'string'
    ? (() => { try { return JSON.parse(chart.sample as string); } catch { return null; } })()
    : chart.sample;
  const demo = !!sample && Array.isArray((sample as Sample).columns) && Array.isArray((sample as Sample).rows);
  // 遗留 config.sql / config.demoSql 不回填（护栏 §7：配置不夹 SQL）
  const config: Record<string, any> = Object.fromEntries(
    Object.entries(chart.config || {}).filter(([k]) => k !== 'sql' && k !== 'demoSql'));
  if (demo) config[TEMPLATE_DEMO_KEY] = true;
  return {
    name: chart.name,
    chart_type: chart.chart_type,
    position: chart.position,
    sql_query: demo ? '' : (chart.sql_query || ''),
    semantic_query: demo ? null : chart.semantic_query,
    query_source: !demo && (chart.query_source === 'semantic' || chart.semantic_query) ? 'semantic' : 'raw_sql',
    source_type: chart.source_type || 'template',
    source_id: chart.source_id ?? null,
    config,
    data_cache: demo ? sample : undefined,
  };
}

/** 示例数据判定（渲染面共用）：图表是否仍在展示模板演示数值 */
export const isDemoChart = (chart?: { config?: Record<string, any> } | null) =>
  chart?.config?.[TEMPLATE_DEMO_KEY] === true;
