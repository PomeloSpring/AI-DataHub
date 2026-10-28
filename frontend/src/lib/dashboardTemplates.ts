/**
 * 仪表盘模板定义（前端唯一事实源）—— 定位是**图表样式展示模板**。
 *
 * 设计纪律（改动前必读）：
 * 1. 模板不携带任何 SQL/取数口径：它只定义样式与排版（图型、栅格位置、字段映射），
 *    数据一律来自内置的 `sample`（演示数值）。“绑不接真数”由用户自己决定，
 *    模板不对任何环境里的表结构作假设（因此也不预置 datasource_id）。
 * 2. 样式也不写死：配色/卡片/渐变由字模库主题包在渲染时解析（ui-vis-library-style.md §1），
 *    这里出现色值就等于另创一套画风。
 * 3. 导入后图表无 SQL 且带示例缓存：「刷新数据」不会拿它去跳数据源（无查询可跑），
 *    也不会把示例数据抹空；用户在「数据与字段配置」里写了 SQL/语义查询后，
 *    服务端同批清掉示例标记与演示缓存，转为真实取数。
 * 4. 图表类型必须是 `DashboardChart.tsx: CHART_TYPES` 里的真实类型；
 *    位置必须落在画布宽度内 —— 两条都由 `validateTemplates()` 把关（静态护栏用例锁定）。
 */
import type { ComponentType } from 'react';
import {
  Activity, BarChart3, Coins, Database, FlaskConical, Gauge, Layout, Rocket, Sparkles,
  TrendingUp, Users,
} from 'lucide-react';
import { CHART_TYPES } from '@/components/DashboardChart';
import type { Rect } from './dashboardDesign';
import { TEMPLATE_DEMO_KEY, type Sample } from './dashboardDemo';

// 示例标记与 payload 组装的唯一口径在 dashboardDemo（避开组件依赖，防止 store 循环）
export { TEMPLATE_DEMO_KEY, templateChartPayload, type Sample } from './dashboardDemo';

// ── 画布与网格 ───────────────────────────────────────────────────────────────
/** 大屏画布 1920×1080（与新建看板默认口径一致），四周 20px 内边距、列间距 20px */
export const TEMPLATE_CANVAS = { width: 1920, height: 1080, pad: 20, gap: 20 };
export const TEMPLATE_COLUMNS = 12;
const COL_W = (TEMPLATE_CANVAS.width - TEMPLATE_CANVAS.pad * 2 - TEMPLATE_CANVAS.gap * (TEMPLATE_COLUMNS - 1)) / TEMPLATE_COLUMNS;

/** 行高档位：KPI 卡 160、常规图表 320、主图 360 */
const KPI_H = 160;
const ROW_H = 320;
const MAIN_H = 360;

/** 12 栏栅格 → 大屏像素坐标（栅格是唯一录入口径，不再手算像素倍数） */
export function gridRect(col: number, colSpan: number, rowTop: number, height: number): Rect {
  return {
    x: TEMPLATE_CANVAS.pad + col * (COL_W + TEMPLATE_CANVAS.gap),
    y: rowTop,
    w: colSpan * COL_W + (colSpan - 1) * TEMPLATE_CANVAS.gap,
    h: height,
  };
}

// ── 示例数据助手 ─────────────────────────────────────────────────────────────
/** 行式样本：cols 给列序，rows 给等长数组 */
const tab = (columns: string[], rows: any[][]): Sample => ({
  columns, rows: rows.map(r => Object.fromEntries(columns.map((c, i) => [c, r[i]]))),
});

/** 时间序列样本：labels 与 values 等长（示例日期是静态文案，绑真实数据源后由查询结果替换） */
const series = (labels: string[], values: number[], xName = 'dt', yName = 'value'): Sample =>
  tab([xName, yName], labels.map((l, i) => [l, values[i]]));

/** 单值样本（仪表/占比） */
const single = (value: number, name = 'value'): Sample => tab([name], [[value]]);

const DAYS7 = ['10-19', '10-20', '10-21', '10-22', '10-23', '10-24', '10-25'];
const MONTHS6 = ['2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10'];
const HOURS = ['00', '02', '04', '06', '08', '10', '12', '14', '16', '18', '20', '22'];

// ── 类型 ─────────────────────────────────────────────────────────────────────
export interface TemplateChartDef {
  name: string;
  chart_type: string;
  position: Rect;
  /** 字段映射（xCol/yCol/groupCol/nameCol…）——必须被 sample.columns 覆盖 */
  config?: Record<string, any>;
  /** 示例数据（演示数值）：导入即写入 data_cache，看板立刻可看样式效果 */
  sample?: Sample | string;
  /** 以下各项只在导入的 JSON（仪表盘导出体）里出现；模板本体不得携带 */
  sql_query?: string;
  semantic_query?: Record<string, any> | null;
  query_source?: string;
  source_type?: string;
  source_id?: number | null;
}

export interface DashboardTemplate {
  id: string;
  name: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
  tags: string[];
  scenario: string;
  canvas: { width: number; height: number };
  charts: TemplateChartDef[];
  /** 导入时一并写入的看板参数（模板自身不预置参数，保留通道给后续模板使用） */
  params?: Record<string, any>[];
  page_params?: Record<string, any>[];
}

// ── 模板清单 ─────────────────────────────────────────────────────────────────
export const DASHBOARD_TEMPLATES: DashboardTemplate[] = [
  {
    id: 'sales_overview',
    name: '销售经营概览',
    description: '销售额、订单量、客户数与转化率四项核心指标，配合趋势、品类构成、区域与单品排行，适合业务例会复盘。',
    icon: BarChart3,
    tags: ['业务分析', 'KPI', '销售'],
    scenario: '销售 / 经营例会复盘版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: '近 30 天销售额', chart_type: 'big_number_trend', position: gridRect(0, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'sales' }, sample: series(DAYS7, [182, 168, 205, 224, 246, 232, 268], 'dt', 'sales')
      },
      { name: '近 30 天订单量', chart_type: 'big_number_trend', position: gridRect(3, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'orders' }, sample: series(DAYS7, [1240, 1180, 1325, 1410, 1502, 1460, 1588], 'dt', 'orders')
      },
      { name: '成交客户数', chart_type: 'big_number_trend', position: gridRect(6, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'buyers' }, sample: series(DAYS7, [720, 698, 812, 860, 902, 878, 951], 'dt', 'buyers')
      },
      { name: '支付转化率', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'rate', maxValue: 100 }, sample: single(38.6, 'rate')
      },
      { name: '销售趋势（线上/门店）', chart_type: 'line', position: gridRect(0, 8, 200, MAIN_H),
        config: { xCol: 'dt', yCol: 'sales', groupCol: 'channel' },
        sample: tab(['dt', 'channel', 'sales'], [
          ['10-19', '线上', 118], ['10-20', '线上', 106], ['10-21', '线上', 132], ['10-22', '线上', 141],
          ['10-23', '线上', 158], ['10-24', '线上', 149], ['10-25', '线上', 172],
          ['10-19', '门店', 64], ['10-20', '门店', 62], ['10-21', '门店', 73], ['10-22', '门店', 83],
          ['10-23', '门店', 88], ['10-24', '门店', 83], ['10-25', '门店', 96],
        ])
      },
      { name: '品类销售构成', chart_type: 'pie', position: gridRect(8, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['家用电器', 862], ['服饰鞋包', 731], ['食品生鲜', 604], ['美妆个护', 452], ['运动户外', 288], ['其他', 205]])
      },
      { name: '区域销售额 TOP', chart_type: 'bar', position: gridRect(0, 6, 560, ROW_H),
        config: { xCol: 'province', yCol: 'sales' },
        sample: tab(['province', 'sales'], [['上海市', 386], ['广东省', 342], ['浙江省', 298], ['江苏省', 264], ['北京市', 232], ['四川省', 186], ['山东省', 154], ['湖北省', 122]])
      },
      { name: '商品销量排行 TOP', chart_type: 'bar', position: gridRect(6, 6, 560, ROW_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['智能音箱', 1862], ['无线耳机', 1640], ['扫地机器人', 1325], ['保温杯', 1188], ['运动水壶', 962], ['空气净化器', 875], ['电动牙刷', 764], ['智能手表', 690]])
      },
    ],
  },
  {
    id: 'ai_usage',
    name: 'AI 应用用量分析',
    description: 'Token 消耗、调用量、活跃用户与响应耗时，按模型和应用下钻，适合平台/中台的 AI 用量运营周会。',
    icon: Sparkles,
    tags: ['AI 用量', 'Token', '平台运营'],
    scenario: 'AI 应用量与模型消耗运营版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: 'Token 消耗（亿）', chart_type: 'big_number_trend', position: gridRect(0, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'tokens' }, sample: series(DAYS7, [3.6, 3.9, 4.2, 4.1, 4.8, 4.5, 5.2], 'dt', 'tokens')
      },
      { name: '请求次数', chart_type: 'big_number_trend', position: gridRect(3, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'requests' }, sample: series(DAYS7, [18200, 19400, 21600, 20800, 23500, 22100, 25800], 'dt', 'requests')
      },
      { name: '活跃用户', chart_type: 'big_number_trend', position: gridRect(6, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'users' }, sample: series(DAYS7, [986, 1042, 1128, 1096, 1264, 1188, 1352], 'dt', 'users')
      },
      { name: '平均响应耗时（秒）', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'latency', maxValue: 10 }, sample: single(2.4, 'latency')
      },
      { name: '分模型 Token 趋势', chart_type: 'line', position: gridRect(0, 8, 200, MAIN_H),
        config: { xCol: 'dt', yCol: 'tokens', groupCol: 'model' },
        sample: tab(['dt', 'model', 'tokens'], [
          ['10-19', '旗舰模型', 1.8], ['10-20', '旗舰模型', 1.9], ['10-21', '旗舰模型', 2.1], ['10-22', '旗舰模型', 2.0],
          ['10-23', '旗舰模型', 2.4], ['10-24', '旗舰模型', 2.2], ['10-25', '旗舰模型', 2.6],
          ['10-19', '轻量模型', 1.8], ['10-20', '轻量模型', 2.0], ['10-21', '轻量模型', 2.1], ['10-22', '轻量模型', 2.1],
          ['10-23', '轻量模型', 2.4], ['10-24', '轻量模型', 2.3], ['10-25', '轻量模型', 2.6],
        ])
      },
      { name: '模型调用占比', chart_type: 'pie', position: gridRect(8, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['旗舰模型', 11240], ['轻量模型', 8620], ['向量模型', 3280], ['语音模型', 1560], ['其他', 820]])
      },
      { name: '应用 Token 排行', chart_type: 'bar', position: gridRect(0, 6, 560, ROW_H),
        config: { xCol: 'app', yCol: 'tokens' },
        sample: tab(['app', 'tokens'], [['智能问数', 12.6], ['报告生成', 9.8], ['本体建模', 7.4], ['看板设计', 5.2], ['资产盘点', 3.6], ['数据质量', 2.8], ['客服助手', 2.1], ['其他', 1.5]])
      },
      { name: '应用用量明细', chart_type: 'table_value', position: gridRect(6, 6, 560, ROW_H),
        config: { pageSize: 5 },
        sample: tab(['app', 'requests', 'tokens', 'cost'], [
          ['智能问数', 8642, 12.6, 318.4], ['报告生成', 6120, 9.8, 246.1], ['本体建模', 4380, 7.4, 186.2],
          ['看板设计', 3260, 5.2, 132.5], ['资产盘点', 2140, 3.6, 92.4], ['数据质量', 1680, 2.8, 74.2],
        ])
      },
    ],
  },
  {
    id: 'cost_billing',
    name: '成本与计费分析',
    description: '月度成本构成、环比变化、费用 TOP 应用与预算执行进度，适合出账与成本治理场景。',
    icon: Coins,
    tags: ['成本', '计费', '预算'],
    scenario: '出账与成本治理版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: '本月成本（万元）', chart_type: 'big_number_trend', position: gridRect(0, 3, 20, KPI_H),
        config: { xCol: 'month', yCol: 'cost' }, sample: series(MONTHS6, [186, 198, 212, 205, 228, 246], 'month', 'cost')
      },
      { name: '累计成本（万元）', chart_type: 'big_number_trend', position: gridRect(3, 3, 20, KPI_H),
        config: { xCol: 'month', yCol: 'cumulative' }, sample: series(MONTHS6, [186, 384, 596, 801, 1029, 1275], 'month', 'cumulative')
      },
      { name: '成本环比（%）', chart_type: 'big_number_trend', position: gridRect(6, 3, 20, KPI_H),
        config: { xCol: 'month', yCol: 'mom' }, sample: series(MONTHS6, [4.2, 6.5, 7.1, -3.3, 11.2, 7.9], 'month', 'mom')
      },
      { name: '预算执行进度', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'rate', maxValue: 100 }, sample: single(72.5, 'rate')
      },
      { name: '成本构成趋势', chart_type: 'area', position: gridRect(0, 8, 200, MAIN_H),
        config: { xCol: 'month', yCol: 'cost', groupCol: 'type' },
        sample: tab(['month', 'type', 'cost'], [
          ['2026-05', '模型推理', 108], ['2026-06', '模型推理', 116], ['2026-07', '模型推理', 128],
          ['2026-08', '模型推理', 124], ['2026-09', '模型推理', 142], ['2026-10', '模型推理', 156],
          ['2026-05', '数据存储', 52], ['2026-06', '数据存储', 56], ['2026-07', '数据存储', 58],
          ['2026-08', '数据存储', 55], ['2026-09', '数据存储', 59], ['2026-10', '数据存储', 62],
        ])
      },
      { name: '成本类型占比', chart_type: 'pie', position: gridRect(8, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['模型推理', 156], ['数据存储', 62], ['计算资源', 38], ['网络传输', 18], ['其他', 12]])
      },
      { name: '应用成本 TOP', chart_type: 'bar', position: gridRect(0, 6, 560, ROW_H),
        config: { xCol: 'app', yCol: 'cost' },
        sample: tab(['app', 'cost'], [['智能问数', 86.4], ['报告生成', 62.1], ['本体建模', 45.8], ['看板设计', 32.5], ['资产盘点', 21.4], ['数据质量', 16.2], ['客服助手', 12.8], ['其他', 9.4]])
      },
      { name: '月度成本明细', chart_type: 'table_value', position: gridRect(6, 6, 560, ROW_H),
        config: { pageSize: 5 },
        sample: tab(['month', 'cost', 'budget', 'rate'], [
          ['2026-10', 246, 340, 72.4], ['2026-09', 228, 320, 71.3], ['2026-08', 205, 300, 68.3],
          ['2026-07', 212, 300, 70.7], ['2026-06', 198, 280, 70.7], ['2026-05', 186, 280, 66.4],
        ])
      },
    ],
  },
  {
    id: 'user_growth',
    name: '用户增长分析',
    description: '新增、活跃、留存与转化路径，配渠道构成、活跃排行与高价值用户清单。',
    icon: Users,
    tags: ['用户', '增长', '留存'],
    scenario: '用户增长与留存分析版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: '累计用户', chart_type: 'big_number_trend', position: gridRect(0, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'total' }, sample: series(DAYS7, [48620, 49180, 49860, 50420, 51280, 51940, 52780], 'dt', 'total')
      },
      { name: '今日新增', chart_type: 'big_number_trend', position: gridRect(3, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'new_users' }, sample: series(DAYS7, [286, 320, 356, 318, 420, 386, 452], 'dt', 'new_users')
      },
      { name: '日活跃用户', chart_type: 'big_number_trend', position: gridRect(6, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'dau' }, sample: series(DAYS7, [5820, 6140, 6480, 6320, 7020, 6840, 7360], 'dt', 'dau')
      },
      { name: '7 日留存率', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'rate', maxValue: 100 }, sample: single(42.8, 'rate')
      },
      { name: '新增用户趋势', chart_type: 'area', position: gridRect(0, 7, 200, MAIN_H),
        config: { xCol: 'dt', yCol: 'new_users' }, sample: series(DAYS7, [286, 320, 356, 318, 420, 386, 452], 'dt', 'new_users')
      },
      { name: '注册渠道构成', chart_type: 'pie', position: gridRect(7, 5, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['应用商店', 1260], ['社交分享', 986], ['搜索引擎', 742], ['活动投放', 528], ['线下地推', 264]])
      },
      { name: '转化路径', chart_type: 'funnel', position: gridRect(0, 5, 560, ROW_H),
        config: { xCol: 'action', yCol: 'users' },
        sample: tab(['action', 'users'], [['访问', 42600], ['注册', 12840], ['首次下单', 6420], ['复购', 2860], ['推荐他人', 960]])
      },
      { name: '活跃时段分布', chart_type: 'bar', position: gridRect(5, 4, 560, ROW_H),
        config: { xCol: 'hour', yCol: 'users' },
        sample: tab(['hour', 'users'], HOURS.map((h, i) => [`${h}:00`, [820, 560, 380, 420, 980, 1680, 2140, 1980, 1760, 2260, 2580, 1420][i]]))
      },
      { name: '高价值用户清单', chart_type: 'table_value', position: gridRect(9, 3, 560, ROW_H),
        config: { pageSize: 5 },
        sample: tab(['user', 'orders', 'amount', 'last_active'], [
          ['U100238', 42, 18620, '2026-10-25'], ['U100571', 36, 15240, '2026-10-25'], ['U100082', 31, 13860, '2026-10-24'],
          ['U100914', 28, 11240, '2026-10-25'], ['U100455', 24, 9860, '2026-10-23'], ['U100720', 21, 8640, '2026-10-24'],
        ])
      },
    ],
  },
  {
    id: 'ops_monitor',
    name: '服务运维监控',
    description: '请求量、成功率、响应分位与错误分布，附最近告警明细，适合值班与容量巡检。',
    icon: Gauge,
    tags: ['运维', '监控', '告警'],
    scenario: '值班巡检与容量监控版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: 'QPS', chart_type: 'big_number_trend', position: gridRect(0, 3, 20, KPI_H),
        config: { xCol: 'hour', yCol: 'qps' }, sample: series(HOURS, [42, 31, 22, 26, 58, 96, 128, 116, 102, 134, 152, 88], 'hour', 'qps')
      },
      { name: '平均响应耗时', chart_type: 'big_number_trend', position: gridRect(3, 3, 20, KPI_H),
        config: { xCol: 'hour', yCol: 'cost_ms' }, sample: series(HOURS, [182, 164, 152, 158, 214, 268, 312, 286, 262, 298, 326, 244], 'hour', 'cost_ms')
      },
      { name: '5xx 次数', chart_type: 'big_number_trend', position: gridRect(6, 3, 20, KPI_H),
        config: { xCol: 'hour', yCol: 'errors' }, sample: series(HOURS, [12, 8, 4, 6, 18, 32, 46, 38, 26, 34, 52, 22], 'hour', 'errors')
      },
      { name: '服务成功率', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'rate', maxValue: 100 }, sample: single(99.62, 'rate')
      },
      { name: '请求量趋势', chart_type: 'timeseries_line', position: gridRect(0, 8, 200, MAIN_H),
        config: { timeCol: 'hour', valCol: 'requests' },
        sample: series(HOURS, [15120, 11160, 7920, 9360, 20880, 34560, 46080, 41760, 36720, 48240, 54720, 31680], 'hour', 'requests')
      },
      { name: '错误类型分布', chart_type: 'pie', position: gridRect(8, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['500 内部错误', 46], ['502 网关错误', 28], ['504 超时', 22], ['429 限流', 18], ['404 未找到', 12]])
      },
      { name: '接口响应分位', chart_type: 'bar', position: gridRect(0, 5, 560, ROW_H),
        config: { xCol: 'api', yCol: 'cost_ms' },
        sample: tab(['api', 'cost_ms'], [['/semantic/query', 1240], ['/execute_sql', 986], ['/ontology/graph', 742], ['/dashboard/refresh', 588], ['/datasets', 312], ['/assets/search', 186]])
      },
      { name: '最近告警明细', chart_type: 'table_value', position: gridRect(5, 7, 560, ROW_H),
        config: { pageSize: 6 },
        sample: tab(['time', 'level', 'target', 'message'], [
          ['10-25 14:22', '严重', 'semantic-query', '语义层查询超时 > 30s'], ['10-25 11:08', '警告', 'datasource-doris', '连接池使用率 92%'],
          ['10-24 22:41', '严重', 'ontology-build', '图谱重建任务失败'], ['10-24 16:35', '提示', 'kb-sync', '知识库同步延迟 6 分钟'],
          ['10-24 09:12', '警告', 'access-gateway', '5xx 比例 0.8%'], ['10-23 20:54', '提示', 'celery-beat', '定时任务错过调度 1 次'],
        ])
      },
    ],
  },
  {
    id: 'asset_operations',
    name: '数据资产运营',
    description: '纳管表数、数据源、同步任务与新鲜度，配合域分布、大表排行和数据源清单，适合数据团队盘点。',
    icon: Database,
    tags: ['数据资产', '元数据', '同步'],
    scenario: '数据资产盘点版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: '纳管表数', chart_type: 'big_number_trend', position: gridRect(0, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'tables' }, sample: series(DAYS7, [1268, 1275, 1284, 1290, 1302, 1310, 1324], 'dt', 'tables')
      },
      { name: '字段总数', chart_type: 'big_number_trend', position: gridRect(3, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'columns' }, sample: series(DAYS7, [28640, 28910, 29240, 29480, 30120, 30460, 30980], 'dt', 'columns')
      },
      { name: '接入数据源', chart_type: 'big_number_trend', position: gridRect(6, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'sources' }, sample: series(DAYS7, [12, 12, 13, 13, 14, 14, 15], 'dt', 'sources')
      },
      { name: '同步成功率', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'rate', maxValue: 100 }, sample: single(96.4, 'rate')
      },
      { name: '元数据同步状态', chart_type: 'pie', position: gridRect(0, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['成功', 128], ['进行中', 6], ['失败', 4], ['已停用', 3]])
      },
      { name: '数据源类型分布', chart_type: 'pie', position: gridRect(4, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['MySQL', 6], ['Doris', 3], ['SLS', 2], ['MaxCompute', 2], ['PostgreSQL', 2]])
      },
      { name: '本体覆盖对象数', chart_type: 'bar', position: gridRect(8, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['订单域', 28], ['用户域', 22], ['资产域', 18], ['成本域', 14], ['用量域', 12], ['质量域', 8]])
      },
      { name: '大表存储排行', chart_type: 'bar', position: gridRect(0, 6, 560, ROW_H),
        config: { xCol: 'table', yCol: 'size_gb' },
        sample: tab(['table', 'size_gb'], [['dwd_order_detail', 862], ['ods_access_log', 640], ['dwd_ai_usage', 428], ['dwd_visit', 316], ['dim_product', 128], ['dwd_cost', 96]])
      },
      { name: '数据源清单', chart_type: 'table_value', position: gridRect(6, 6, 560, ROW_H),
        config: { pageSize: 5 },
        sample: tab(['name', 'type', 'site', 'tables'], [
          ['生产 Doris', 'Doris', '华东', 268], ['业务 MySQL', 'MySQL', '华东', 186], ['日志服务 SLS', 'SLS', '华北', 42],
          ['数仓 MaxCompute', 'MaxCompute', '华东', 312], ['报表库 PostgreSQL', 'PostgreSQL', '华南', 74], ['备份库 MySQL', 'MySQL', '华南', 58],
        ])
      },
    ],
  },
  {
    id: 'data_quality',
    name: '数据质量看板',
    description: '质量总分与完整性/及时性/准确性分项，配域分布、规则命中趋势和问题清单。',
    icon: FlaskConical,
    tags: ['数据质量', '规则', '治理'],
    scenario: '数据质量治理版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: '质量总分', chart_type: 'gauge', position: gridRect(0, 3, 20, KPI_H),
        config: { yCol: 'score', maxValue: 100 }, sample: single(91.6, 'score')
      },
      { name: '完整性', chart_type: 'gauge', position: gridRect(3, 3, 20, KPI_H),
        config: { yCol: 'score', maxValue: 100 }, sample: single(94.2, 'score')
      },
      { name: '及时性', chart_type: 'gauge', position: gridRect(6, 3, 20, KPI_H),
        config: { yCol: 'score', maxValue: 100 }, sample: single(88.5, 'score')
      },
      { name: '准确性', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'score', maxValue: 100 }, sample: single(92.8, 'score')
      },
      { name: '规则命中趋势', chart_type: 'timeseries_area', position: gridRect(0, 8, 200, MAIN_H),
        config: { timeCol: 'dt', valCol: 'issues' },
        sample: series(DAYS7, [42, 38, 51, 46, 33, 28, 24], 'dt', 'issues')
      },
      { name: '问题类型分布', chart_type: 'pie', position: gridRect(8, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['空值超限', 36], ['主键重复', 22], ['口径不一致', 18], ['延迟超标', 14], ['枚举越界', 9]])
      },
      { name: '各域质量得分', chart_type: 'bar', position: gridRect(0, 6, 560, ROW_H),
        config: { xCol: 'domain', yCol: 'score' },
        sample: tab(['domain', 'score'], [['订单域', 93], ['用户域', 90], ['资产域', 88], ['成本域', 95], ['用量域', 91], ['质量域', 86]])
      },
      { name: '质量问题清单', chart_type: 'table_value', position: gridRect(6, 6, 560, ROW_H),
        config: { pageSize: 5 },
        sample: tab(['table', 'rule', 'severity', 'found'], [
          ['dwd_order', 'pay_amount 空值率 > 1%', '严重', '2026-10-25'], ['dwd_user', '手机号格式非法', '警告', '2026-10-25'],
          ['ods_access_log', '主键重复', '严重', '2026-10-24'], ['dwd_cost', '金额与计量不一致', '警告', '2026-10-24'],
          ['dim_product', '枚举值越界', '提示', '2026-10-23'], ['dwd_visit', '延迟 3 小时未到', '警告', '2026-10-23'],
        ])
      },
    ],
  },
  {
    id: 'command_center',
    name: '实时业务监控大屏',
    description: '深色指挥中心风格：全站 KPI、实时请求趋势、站点与渠道构成、热门接口、地域分布与实时事件流。',
    icon: Rocket,
    tags: ['大屏', '实时监控', '指挥中心'],
    scenario: '实时指挥大屏版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: '今日请求量', chart_type: 'big_number_trend', position: gridRect(0, 2, 20, 160),
        config: { xCol: 'hour', yCol: 'requests' },
        sample: series(HOURS, [15120, 11160, 7920, 9360, 20880, 34560, 46080, 41760, 36720, 48240, 54720, 31680], 'hour', 'requests')
      },
      { name: '在线会话', chart_type: 'big_number_trend', position: gridRect(2, 2, 20, 160),
        config: { xCol: 'hour', yCol: 'sessions' }, sample: series(HOURS, [820, 560, 380, 420, 980, 1680, 2140, 1980, 1760, 2260, 2580, 1420], 'hour', 'sessions')
      },
      { name: '今日成交', chart_type: 'big_number_trend', position: gridRect(4, 2, 20, 160),
        config: { xCol: 'hour', yCol: 'amount' }, sample: series(HOURS, [36, 18, 12, 16, 42, 86, 128, 112, 96, 138, 164, 88], 'hour', 'amount')
      },
      { name: '成功率', chart_type: 'gauge', position: gridRect(6, 2, 20, 160),
        config: { yCol: 'rate', maxValue: 100 }, sample: single(99.7, 'rate')
      },
      { name: 'P95 耗时（ms）', chart_type: 'big_number_trend', position: gridRect(8, 2, 20, 160),
        config: { xCol: 'hour', yCol: 'p95' }, sample: series(HOURS, [286, 244, 218, 232, 318, 402, 486, 452, 418, 496, 528, 384], 'hour', 'p95')
      },
      { name: '异常告警', chart_type: 'big_number_trend', position: gridRect(10, 2, 20, 160),
        config: { xCol: 'hour', yCol: 'alerts' }, sample: series(HOURS, [3, 1, 0, 1, 4, 8, 12, 9, 6, 11, 14, 5], 'hour', 'alerts')
      },
      { name: '实时请求趋势', chart_type: 'timeseries_line', position: gridRect(0, 7, 200, 320),
        config: { timeCol: 'hour', valCol: 'requests' },
        sample: series(HOURS, [15120, 11160, 7920, 9360, 20880, 34560, 46080, 41760, 36720, 48240, 54720, 31680], 'hour', 'requests')
      },
      { name: '站点请求量', chart_type: 'bar', position: gridRect(7, 3, 200, 320),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['华东', 186200], ['华北', 142800], ['华南', 98600], ['西南', 42600], ['海外', 18400]])
      },
      { name: '渠道构成', chart_type: 'rose', position: gridRect(10, 2, 200, 320),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['Web', 46], ['App', 38], ['小程序', 22], ['OpenAPI', 12], ['合作方', 6]])
      },
      { name: '热门接口排行', chart_type: 'bar', position: gridRect(0, 5, 560, ROW_H),
        config: { xCol: 'api', yCol: 'requests' },
        sample: tab(['api', 'requests'], [['/semantic/query', 42600], ['/execute_sql', 36400], ['/assets/search', 28600], ['/dashboard/refresh', 22400], ['/ontology/graph', 18600], ['/datasets', 12800]])
      },
      { name: '区域体量分布', chart_type: 'treemap', position: gridRect(5, 4, 560, ROW_H),
        config: { nameCol: 'province', valCol: 'requests' },
        sample: tab(['province', 'requests'], [['广东省', 42600], ['江苏省', 31800], ['浙江省', 28600], ['上海市', 26400], ['北京市', 22800], ['四川省', 16200], ['山东省', 12400], ['湖北省', 9800]])
      },
      { name: '实时事件流', chart_type: 'table_value', position: gridRect(9, 3, 560, ROW_H),
        config: { pageSize: 5 },
        sample: tab(['time', 'level', 'event'], [
          ['14:32:08', '严重', '语义查询超时'], ['14:28:41', '提示', '新数据集发布'], ['14:21:06', '警告', 'Doris 连接池 92%'],
          ['14:12:55', '提示', '本体草案已发布'], ['14:02:18', '严重', '同步任务失败重试'], ['13:56:40', '提示', '看板轮播切换'],
        ])
      },
    ],
  },
  {
    id: 'traffic_analysis',
    name: '网站流量分析',
    description: 'PV/UV、停留时长与跳出率，配合访问趋势、来源与设备构成、热门页面清单。',
    icon: Activity,
    tags: ['流量', '网站', '行为'],
    scenario: '站点流量分析版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: '今日 PV', chart_type: 'big_number_trend', position: gridRect(0, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'pv' }, sample: series(DAYS7, [86400, 92800, 88200, 101600, 112400, 108600, 124800], 'dt', 'pv')
      },
      { name: '今日 UV', chart_type: 'big_number_trend', position: gridRect(3, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'uv' }, sample: series(DAYS7, [18600, 19800, 19200, 21400, 23600, 22800, 25600], 'dt', 'uv')
      },
      { name: '平均停留（秒）', chart_type: 'big_number_trend', position: gridRect(6, 3, 20, KPI_H),
        config: { xCol: 'dt', yCol: 'duration' }, sample: series(DAYS7, [186, 192, 178, 204, 218, 212, 226], 'dt', 'duration')
      },
      { name: '跳出率', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'rate', maxValue: 100 }, sample: single(38.2, 'rate')
      },
      { name: 'PV / UV 趋势', chart_type: 'line', position: gridRect(0, 8, 200, MAIN_H),
        config: { xCol: 'dt', yCol: 'value', groupCol: 'metric' },
        sample: tab(['dt', 'metric', 'value'], [
          ...DAYS7.map((d, i) => [d, 'PV', [86400, 92800, 88200, 101600, 112400, 108600, 124800][i]]),
          ...DAYS7.map((d, i) => [d, 'UV', [18600, 19800, 19200, 21400, 23600, 22800, 25600][i]]),
        ])
      },
      { name: '流量来源', chart_type: 'pie', position: gridRect(8, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['搜索引擎', 42600], ['直接访问', 31200], ['社交媒体', 18600], ['外部链接', 12400], ['邮件营销', 6200]])
      },
      { name: '设备类型分布', chart_type: 'bar', position: gridRect(0, 6, 560, ROW_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['移动端', 68200], ['PC 端', 42600], ['平板', 12800], ['TV 端', 3600]])
      },
      { name: '热门页面 TOP', chart_type: 'table_value', position: gridRect(6, 6, 560, ROW_H),
        config: { pageSize: 6 },
        sample: tab(['page', 'pv', 'uv', 'duration'], [
          ['/home', 38600, 12400, 186], ['/product/detail', 22400, 9860, 242], ['/search', 18200, 8620, 96],
          ['/cart', 12600, 6480, 168], ['/activity', 9860, 5240, 124], ['/help', 6420, 3860, 208],
        ])
      },
    ],
  },
  {
    id: 'financial_report',
    name: '财务收支分析',
    description: '收入、支出、利润与预算执行率，配合月度趋势、支出构成、利润对比与月度明细。',
    icon: TrendingUp,
    tags: ['财务', '收支', '预算'],
    scenario: '财务收支与预算版式',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [
      { name: '本月收入（万元）', chart_type: 'big_number_trend', position: gridRect(0, 3, 20, KPI_H),
        config: { xCol: 'month', yCol: 'income' }, sample: series(MONTHS6, [486, 512, 548, 526, 602, 648], 'month', 'income')
      },
      { name: '本月支出（万元）', chart_type: 'big_number_trend', position: gridRect(3, 3, 20, KPI_H),
        config: { xCol: 'month', yCol: 'expense' }, sample: series(MONTHS6, [328, 342, 366, 358, 386, 402], 'month', 'expense')
      },
      { name: '净利润（万元）', chart_type: 'big_number_trend', position: gridRect(6, 3, 20, KPI_H),
        config: { xCol: 'month', yCol: 'profit' }, sample: series(MONTHS6, [158, 170, 182, 168, 216, 246], 'month', 'profit')
      },
      { name: '预算执行率', chart_type: 'gauge', position: gridRect(9, 3, 20, KPI_H),
        config: { yCol: 'rate', maxValue: 100 }, sample: single(84.6, 'rate')
      },
      { name: '收支趋势', chart_type: 'line', position: gridRect(0, 8, 200, MAIN_H),
        config: { xCol: 'month', yCol: 'amount', groupCol: 'type' },
        sample: tab(['month', 'type', 'amount'], [
          ...MONTHS6.map((m, i) => [m, '收入', [486, 512, 548, 526, 602, 648][i]]),
          ...MONTHS6.map((m, i) => [m, '支出', [328, 342, 366, 358, 386, 402][i]]),
        ])
      },
      { name: '支出构成', chart_type: 'pie', position: gridRect(8, 4, 200, MAIN_H),
        config: { xCol: 'name', yCol: 'value' },
        sample: tab(['name', 'value'], [['人力成本', 168], ['技术与云资源', 96], ['市场投放', 68], ['办公与行政', 36], ['其他', 34]])
      },
      { name: '月度利润对比', chart_type: 'bar', position: gridRect(0, 6, 560, ROW_H),
        config: { xCol: 'month', yCol: 'profit' }, sample: series(MONTHS6, [158, 170, 182, 168, 216, 246], 'month', 'profit')
      },
      { name: '月度收支明细', chart_type: 'table_value', position: gridRect(6, 6, 560, ROW_H),
        config: { pageSize: 6 },
        sample: tab(['month', 'income', 'expense', 'profit'], MONTHS6.map((m, i) =>
          [m, [486, 512, 548, 526, 602, 648][i], [328, 342, 366, 358, 386, 402][i], [158, 170, 182, 168, 216, 246][i]]))
      },
    ],
  },
  {
    id: 'empty',
    name: '空白看板',
    description: '不带任何图表，从空白画布开始自行设计（可稍后套用字模与布局模板）。',
    icon: Layout,
    tags: ['自定义', '空白'],
    scenario: '不限',
    canvas: { width: TEMPLATE_CANVAS.width, height: TEMPLATE_CANVAS.height },
    charts: [],
  },
];

// ── 静态护栏：模板结构校验 ────────────────────────────────────────────────────
const REAL_CHART_TYPES = new Set(CHART_TYPES.map(c => c.value));
/** 模板允许出现的 config 键：全部是 DashboardChart 真实消费的口径/分页/量程键。
 * 样式类键（颜色/渐变/圆角/字号）一律不登记 —— 那是字模库主题包的职责（ui-vis-library-style.md §1）；
 * 写错键名（如 valueCol 当 valCol）就是"存了不生效"的隐形缺陷，因此白名单外的键直接报错。*/
export const TEMPLATE_CONFIG_KEYS = new Set(['xCol', 'yCol', 'groupCol', 'nameCol', 'valCol', 'timeCol',
  'maxValue', 'pageSize', 'maxRows']);
/** 地图类底图靠外部 CDN 拉取（assets.antv.antgroup.com），内网实例导入后只会显式报错，
 *  与"导入即见效果"相违 —— 模板不预置地图（需地图的用户自行换型或配本地瓦片）。*/
const REMOTE_ASSET_TYPES = new Set(['china_map', 'world_map']);

/**
 * 模板自检（返回可读错误清单，空数组 = 通过）：
 * 类型必须是 CHART_TYPES 真实项、位置不得超出画布宽度、示例数据必须覆盖字段映射列、
 * 同板图表 id 不重复。导入前的错比导入后空白看板便宜得多。
 */
export function validateTemplates(templates: DashboardTemplate[] = DASHBOARD_TEMPLATES): string[] {
  const errors: string[] = [];
  const seenIds = new Set<string>();
  for (const tpl of templates) {
    const tag = `模板「${tpl.name}」`;
    if (seenIds.has(tpl.id)) errors.push(`${tag} id 重复：${tpl.id}`);
    seenIds.add(tpl.id);
    if (!tpl.charts.length && tpl.id !== 'empty') errors.push(`${tag} 没有任何图表`);
    if (tpl.canvas.width > TEMPLATE_CANVAS.width) errors.push(`${tag} 画布宽度 ${tpl.canvas.width} 超过大屏默认 ${TEMPLATE_CANVAS.width}`);
    const placed: Array<{ name: string; rect: Rect }> = [];
    for (const chart of tpl.charts) {
      const label = `${tag} ·「${chart.name}」`;
      if (!REAL_CHART_TYPES.has(chart.chart_type)) errors.push(`${label} 图表类型 ${chart.chart_type} 不在 CHART_TYPES 清单内`);
      if (REMOTE_ASSET_TYPES.has(chart.chart_type)) errors.push(`${label} 地图类底图需要外部 CDN 资源，模板不得预置（内网导入会直接报错）`);
      const { x, y, w, h } = chart.position;
      if (x < 0 || y < 0 || w <= 0 || h <= 0) errors.push(`${label} 位置非法：${JSON.stringify(chart.position)}`);
      if (x + w > tpl.canvas.width) errors.push(`${label} 超出画布宽度（右缘 ${Math.round(x + w)} > ${tpl.canvas.width}）`);
      for (const [key, value] of Object.entries(chart.config || {})) {
        if (!TEMPLATE_CONFIG_KEYS.has(key)) errors.push(`${label} config 键 ${key} 不被渲染层消费（白名单：${[...TEMPLATE_CONFIG_KEYS].join(', ')}）`);
        if (value === undefined || value === null || value === '') errors.push(`${label} config 键 ${key} 为空值`);
      }
      if (typeof chart.sample === 'string' || !chart.sample?.columns?.length) errors.push(`${label} 缺少示例数据，导入后看不到效果`);
      if ('datasource_id' in (chart.config || {})) errors.push(`${label} 不得预置 datasource_id（内部 id 不由模板元数据携带，绑源由用户在配置面板选择）`);
      if (chart.config?.[TEMPLATE_DEMO_KEY] === false) errors.push(`${label} 示例标记只能由 templateChartPayload 写入 true（false 代表已转真实取数，不属于模板定义）`);
      // 样式模板不携带 SQL：模板本体写了 sql 就是“拿假想表当口径”的旧做法，直接拦
      if (chart.sql_query || (chart.config && 'sql' in chart.config)) errors.push(`${label} 样式模板不得携带 SQL（数据由内置示例提供，接真数请在配置面板改写）`);
      const mapped = ['xCol', 'yCol', 'groupCol', 'nameCol', 'valCol', 'timeCol']
        .map(k => chart.config?.[k]).filter(Boolean) as string[];
      for (const col of mapped) {
        if (chart.sample && typeof chart.sample !== 'string' && !chart.sample.columns.includes(col)) errors.push(`${label} 字段映射 ${col}（${mapped.join('/')} 之一）在示例数据里不存在`);
      }
      if (chart.sample && typeof chart.sample !== 'string' && !chart.sample.rows.length) errors.push(`${label} 示例数据没有数据行`);
      const hit = placed.find(other => chart.position.x < other.rect.x + other.rect.w
        && chart.position.x + chart.position.w > other.rect.x
        && chart.position.y < other.rect.y + other.rect.h
        && chart.position.y + chart.position.h > other.rect.y);
      if (hit) errors.push(`${label} 与「${hit.name}」位置重叠`);
      placed.push({ name: chart.name, rect: chart.position });
    }
  }
  return errors;
}
