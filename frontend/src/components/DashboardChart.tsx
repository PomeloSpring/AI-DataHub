import { memo, useRef, useEffect, useMemo, useState } from 'react';
import { Chart } from '@antv/g2';
import { toast } from '@/lib/toast';
import * as topojson from 'topojson-client';
import {
  BarChart3, TrendingUp, PieChart, Activity, Circle, Webhook, Filter, ArrowDownUp,
  Type, Table, Hash, Gauge, LineChart, BarChart, AreaChart, Calendar,
  Grid3x3, Box, Aperture, ArrowRightLeft, GitBranch, LayoutGrid,
  Flower2, Target, Cloud, Map, List, ListChecks, Sliders,
  Search, RotateCcw, Download, Trophy, Play, Pause,
} from 'lucide-react';
import { useThemeStore, IS_DARK } from '../stores/themeStore';
import { Label } from '@/components/ui/label';
import { useDashboardStore } from '../stores/dashboardStore';
import ModalPage from './ModalPage';
import AnimatedTimeChart from './AnimatedTimeChart';
import DecisionWidget from './decision/DecisionWidget';
import { chartDataKey, pageSizeOf, type LocalVisual } from '@/lib/dashboardDesign';
import { applyG2Animate } from '@/lib/motion';
import { applyFieldFormats } from '@/lib/fieldFormat';
import { placeholderOfShape, shapeOfControl } from '@/lib/dashboardParamAssembly';
import { readChartPalette, readToken } from '@/lib/chartTheme';
import {
  CUSTOM_PRESET, enabledPresets, effectiveGranularity, formatTime,
  fromInputValue, maxEndFor, resolvePreset, toInputValue, type TimeGranularity,
} from '@/lib/timePresets';

// Lucide icon map for chart types
const CHART_ICON_MAP: Record<string, React.ComponentType<any>> = {
  BarChart3, TrendingUp, PieChart, Activity, Circle, Webhook, Filter, ArrowDownUp,
  Type, Table, Hash, Gauge, LineChart, BarChart, AreaChart, Calendar,
  Grid3x3, Box, Aperture, ArrowRightLeft, GitBranch, LayoutGrid,
  Flower2, Target, Cloud, Map, List, ListChecks, Sliders,
  Search, RotateCcw, Download, Trophy, Play, Pause,
};

export function ChartIcon({ name, className }: { name: string; className?: string }) {
  const Icon = CHART_ICON_MAP[name];
  if (!Icon) return <Circle className={className} />;
  return <Icon className={className} />;
}

/**
 * 服务端随取数结果回传的分页事实（`dataset_service` 生成，前端不得自行反推）。
 *
 * 为什么由服务端而不是前端判定：能不能翻页、翻到第几页、能不能跳页，全部由
 * 数据集是否声明「支持分页」与实际取数通道决定；前端拿行数反推会把首页当成全量
 * （旧伪分页缺陷的根因）。
 * - mode='offset'：SQL 通道 LIMIT/OFFSET，页号可随机定位（can_jump=true）；
 * - mode='cursor'：ES 通道 search_after 游标（集群无 PIT，只能顺序往后翻，can_jump=false）。
 */
export interface ChartPaging {
  mode: 'offset' | 'cursor';
  page_size: number;
  page_num: number;
  total: number | null;
  can_jump: boolean;
  /**
   * 行序是否确定（服务端正文最外层有 ORDER BY）。
   * SQL 通道缺它时 `LIMIT n OFFSET m` 的行序由引擎自定，相邻页会重行/漏行；
   * ES 游标通道恒为 true（`_doc` 全序 tiebreaker）。缺省按 true 处理（旧结果无此键）。
   */
  stable_order?: boolean;
}

interface Props {
  chartType: string;
  data: {
    columns: string[];
    rows: any[];
    /** 服务端行总数（仅分页取数时回） */
    total?: number;
    /** 行数撞到服务端上限：结果不是全量，必须显式提醒 */
    truncated?: boolean;
    /**
     * 服务端声明「本次没按页取数，返回的就是全部行」（统计型数据集走这条）。
     * 有它才知道缓存里的行是完整结果，不必再补一次重查；缺它就只能当成状态未知。
     */
    unpaged?: boolean;
    paging?: ChartPaging;
    /**
     * 列名 → 字段显示格式令牌（数据集 `field_config.display_format` 声明，服务端随取数
     * 结果下发）。格式跟数据走、不复制进图表 config：后者会让每张图各持一份第二事实源
     * （改一次要重挂每张图，看板「从数据集引入」时还会被静默丢掉）。
     */
    field_formats?: Record<string, string>;
  };
  config?: Record<string, any>;
  style?: React.CSSProperties;
  loading?: boolean;
  chartId?: number;
  /** 该图所属看板：刷新/分页/查询按钮都要按它发起 POST（宿主可同时呈现多个看板的图表） */
  dashboardId?: number;
  /** 业务名：仅用于失败提示文案，store 未登记该看板时也能报出图名而非裸 id */
  chartName?: string;
  visual?: LocalVisual;
  draft?: boolean;
  /** 图上下钻: 点击图形元素时回传该行原始数据(不传则不挂载点击事件) */
  onDrill?: (datum: Record<string, any>) => void;
  /**
   * 图表入场动画（由 lib/motion 的 g2Animate 算出，调用方不得自己拼）：
   * - `undefined` = **不往 spec 写 animate 键**，与改造前逐字节相同（默认预设 mp_default 的口径，
   *   用例 components/__tests__/DashboardChartSpec.test.ts 钉住）；
   * - `false` = 关掉 G2 动画；
   * - 对象 = `{ enter: { type, duration, easing } }`（type 取 @antv/g2 注册表名）。
   */
  motionAnimate?: Record<string, any> | false;
}

// Map data URLs
const MAP_URLS = {
  china: 'https://assets.antv.antgroup.com/g2/china-topo.json',
  world: 'https://assets.antv.antgroup.com/g2/world-topo.json',
};

// Color palettes
// 主题令牌读取与系列色板已抽取至 lib/chartTheme(与 AnimatedTimeChart 共用单一真值源)


// Helper: check if data has group column for multi-series
export function detectGroupColumn(columns: string[], rows: any[], config: Record<string, any>): string | null {
  if (config?.groupCol) return config.groupCol;

  const xCol = config?.xCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
  const yCol = config?.yCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];

  const candidateCols = columns.filter(c => {
    if (c === xCol || c === yCol) return false;
    if (typeof rows[0]?.[c] !== 'string') return false;
    const uniqueVals = new Set(rows.map(r => r[c]));
    return uniqueVals.size > 1 && uniqueVals.size < rows.length;
  });

  return candidateCols[0] || null;
}

// Helper: detect if a column contains time/date values
export function isTimeColumn(values: any[]): boolean {
  if (!values.length) return false;
  const sample = values.slice(0, 10);
  const timePatterns = [
    /^\d{4}-\d{2}-\d{2}/,
    /^\d{4}\/\d{2}\/\d{2}/,
    /^\d{4}\d{2}\d{2}/,
    /^\d{2}:\d{2}:\d{2}/,
    /^\d{10,13}$/,
  ];
  return sample.every(v => {
    const str = String(v);
    return timePatterns.some(p => p.test(str)) || !isNaN(Date.parse(str));
  });
}

// Helper: parse time value to Date object
export function parseTime(value: any): Date | null {
  if (!value) return null;
  const str = String(value);
  if (/^\d{10}$/.test(str)) return new Date(Number(str) * 1000);
  if (/^\d{13}$/.test(str)) return new Date(Number(str));
  const date = new Date(str);
  return isNaN(date.getTime()) ? null : date;
}

// Helper: aggregate time series data
export function aggregateTimeData(
  rows: any[], xCol: string, yCol: string, groupCol: string | null,
  aggMethod: string = 'auto', timeGranularity: string = 'auto'
) {
  const parsedRows = rows.map(r => ({
    ...r,
    _time: parseTime(r[xCol]),
    _value: Number(r[yCol]) || 0,
  })).filter(r => r._time !== null);

  parsedRows.sort((a, b) => a._time!.getTime() - b._time!.getTime());

  let granularity = timeGranularity;
  if (granularity === 'auto' && parsedRows.length >= 2) {
    const first = parsedRows[0]._time!;
    const last = parsedRows[parsedRows.length - 1]._time!;
    const diffMs = last.getTime() - first.getTime();
    const diffDays = diffMs / (1000 * 60 * 60 * 24);

    if (diffDays <= 2) granularity = 'hour';
    else if (diffDays <= 90) granularity = 'day';
    else if (diffDays <= 730) granularity = 'week';
    else if (diffDays <= 1825) granularity = 'month';
    else granularity = 'year';
  }

  const formatTime = (date: Date): string => {
    switch (granularity) {
      case 'hour':
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:00`;
      case 'day':
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
      case 'week': {
        const startOfWeek = new Date(date);
        startOfWeek.setDate(date.getDate() - date.getDay());
        return `${startOfWeek.getFullYear()}-W${String(Math.ceil((startOfWeek.getDate()) / 7)).padStart(2, '0')}`;
      }
      case 'month':
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
      case 'year':
        return `${date.getFullYear()}`;
      default:
        return date.toISOString().slice(0, 10);
    }
  };

  const aggFunc = (values: number[], method: string): number => {
    switch (method) {
      case 'sum': return values.reduce((a, b) => a + b, 0);
      case 'avg': return values.reduce((a, b) => a + b, 0) / values.length;
      case 'max': return Math.max(...values);
      case 'min': return Math.min(...values);
      case 'count': return values.length;
      default: return values.reduce((a, b) => a + b, 0);
    }
  };

  if (groupCol) {
    const groups: Record<string, Record<string, number[]>> = {};
    parsedRows.forEach(r => {
      const timeKey = formatTime(r._time!);
      const groupVal = String(r[groupCol!]);
      if (!groups[groupVal]) groups[groupVal] = {};
      if (!groups[groupVal][timeKey]) groups[groupVal][timeKey] = [];
      groups[groupVal][timeKey].push(r._value);
    });

    const allTimeKeysSet: Record<string, boolean> = {};
    parsedRows.forEach(r => { allTimeKeysSet[formatTime(r._time!)] = true; });
    const allTimeKeys = Object.keys(allTimeKeysSet).sort();
    const seriesData: Record<string, (number | null)[]> = {};
    Object.entries(groups).forEach(([name, timeMap]) => {
      seriesData[name] = allTimeKeys.map(key => {
        const vals = timeMap[key];
        return vals ? aggFunc(vals, aggMethod) : null;
      });
    });

    return { xData: allTimeKeys, seriesData };
  } else {
    const timeMap: Record<string, number[]> = {};
    parsedRows.forEach(r => {
      const timeKey = formatTime(r._time!);
      if (!timeMap[timeKey]) timeMap[timeKey] = [];
      timeMap[timeKey].push(r._value);
    });

    const xData = Object.keys(timeMap).sort();
    const yData = xData.map(key => aggFunc(timeMap[key], aggMethod));
    return { xData, yData };
  }
}

// Helper: group data by a column
export function groupDataByColumn(rows: any[], xCol: string, yCol: string, groupCol: string) {
  const xSet: Record<string, boolean> = {};
  const groups: Record<string, Record<string, number>> = {};

  rows.forEach(r => {
    const xVal = String(r[xCol]);
    const groupVal = String(r[groupCol]);
    const yVal = Number(r[yCol]) || 0;

    xSet[xVal] = true;
    if (!groups[groupVal]) groups[groupVal] = {};
    groups[groupVal][xVal] = yVal;
  });

  let xData = Object.keys(xSet);
  const isTime = isTimeColumn(xData);

  if (isTime) {
    xData.sort((a, b) => {
      const da = parseTime(a);
      const db = parseTime(b);
      if (da && db) return da.getTime() - db.getTime();
      return a.localeCompare(b);
    });
  } else {
    xData.sort();
  }

  const seriesData: Record<string, (number | null)[]> = {};
  Object.entries(groups).forEach(([name, valMap]) => {
    seriesData[name] = xData.map(x => x in valMap ? valMap[x] : null);
  });

  return { xData, seriesData };
}

/**
 * 稀疏序列判定：线/面至少需要两个端点才画得出形状。
 * 只有一个数据点（或所有点 x 相同）时，整张图在 hover 之前什么都不显示 —— 用户看不到值。
 */
function isSparseSeries(data: any[], xCol: string): boolean {
  if (!Array.isArray(data) || data.length === 0) return false;
  return new Set(data.map(d => String(d?.[xCol] ?? ''))).size < 2;
}

/** 单点可见化用的实心圆点 mark：颜色随色盘，自身不出 tooltip（值仍由线/面 mark 的 shared tooltip 给） */
function visiblePointMark(encode: Record<string, any>) {
  return {
    type: 'point',
    encode: { x: encode.x, y: encode.y, color: encode.color, shape: 'circle' },
    style: { r: 4, fillOpacity: 1 },
    tooltip: false,
  };
}

/**
 * 线/面单 mark 在稀疏序列下包成 view 并叠加圆点，保证「不悬停也看得见这个点」。
 * 注：G2 v5 的 line mark 不认 v4 遗留写法 `point: { shapeSize }`（静默不渲染），必须走 mark 组合。
 */
function ensureVisiblePoints(spec: any, data: any[], xCol: string): any {
  if (!isSparseSeries(data, xCol)) return spec;
  const { type, encode, style, labels, ...view } = spec;
  return {
    ...view,
    type: 'view',
    children: [{ type, encode, style, labels }, visiblePointMark(encode)],
  };
}

/**
 * 给颜色附加 alpha（hex / rgb(a) / hsl(a) 皆可，其余原样返回）。
 * 输出不带空格：渐变串 `l(270) 0:<c1> 1:<c2>` 的色标正则虽能容忍空格，压成紧凑形式更稳。
 */
function withAlpha(color: string, alpha: number): string {
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 1000) / 1000;
  const c = (color || '').trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c);
  if (hex) {
    let h = hex[1];
    if (h.length === 3) h = h.split('').map(x => x + x).join('');
    const n = parseInt(h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  const fn = /^(rgba?|hsla?)\(([^)]*)\)$/i.exec(c);
  if (fn) {
    const parts = fn[2].split(/[,/\s]+/).filter(Boolean).slice(0, 3);
    if (parts.length === 3) return `${fn[1].toLowerCase()[0] === 'r' ? 'rgba' : 'hsla'}(${parts.join(',')},${a})`;
  }
  return c;
}

/**
 * 柱体竖向「同色系渐隐」（AS 风格 design-service 预设：顶部 rgba(brand,.6) → 底部 rgba(brand,.1)）。
 * `barFade=true` 取该口径；传数值则作为顶部 alpha（底部按其 1/6 收敛，保持同一形状）。
 * 与 `gradient`（跨色渐变）同时出现时 barFade 优先 —— 它描述的是同一系列的明暗纵深，不是第二系列。
 *
 * 角度取 `l(90)` 而非旧口径的 `l(270)`：@antv/g-lite 把 legacy 角 +90 换算为 CSS 角，
 * `l(270)` → 360deg = to top（色标 0 在底部），实测会把渐隐画成下深上浅、与素材相反；
 * `l(90)` → 180deg = to bottom，色标 0（深）才落在柱顶。gradient 保持 `l(270)` 不动：
 * 那是存量主题（科技风等）已验收的方向，改它等于顺带换别人的衣。
 */
/**
 * 系列色映射：G2 的分类色标按**数据里首次出现顺序**建域，这里必须同口径。
 *
 * 历史缺陷：柱体 `style.fill` 是个常量通道（一律取 colors[0]），会把色标按系列上色盖掉
 * → 图例是蓝/绿、柱子全是蓝，多系列根本分不出来（2026-11-10 反馈）。
 */
function seriesColorMap(data: any[], seriesField: string, colors: string[]): (value: unknown) => string {
  const domain: string[] = [];
  for (const row of data) {
    const key = String(row?.[seriesField] ?? '');
    if (!domain.includes(key)) domain.push(key);
  }
  // 本模块导入了 lucide 的 `Map` 图标，这里不能写 new Map()；null 原型避开 'constructor' 这类键碰撞
  const byValue: Record<string, string> = Object.create(null);
  domain.forEach((v, i) => { byValue[v] = colors[i % Math.max(1, colors.length)]; });
  return (value: unknown) => byValue[String(value ?? '')] || colors[0] || '#1677ff';
}

function barFadeFill(config: any, colors: string[]): string | undefined {
  const raw = config?.barFade;
  if (raw === undefined || raw === false || raw === '' || raw === 0) return undefined;
  const top = typeof raw === 'number' && Number.isFinite(raw) ? Math.min(1, Math.max(0.05, raw)) : 0.6;
  return `l(90) 0:${withAlpha(colors[0], top)} 1:${withAlpha(colors[0], Math.max(0.05, top / 6))}`;
}

/**
 * 面积填充口径（图表渲染与字模预览共用这一份数学）：竖向同色系渐隐 ——
 * 顶部按 `areaOpacity` 出实色，往下收敛到近透明（面积图的通行画法：填色是"纵深"，
 * 不是"色块"；平涂 0.5 的蓝会把整片糊成一块，线形反而读不出来）。
 *
 * `areaFade` 三态：未配 = 默认开；数值 = 开且作为顶部 alpha；false = 回到平涂。
 * 顶部沿用 `areaOpacity`，所以存量字模的这个旋钮继续生效（只是底端变透），没有废掉它。
 */
export function areaFadeStops(config: any): { top: number; bottom: number } | null {
  const raw = config?.areaFade;
  if (raw === false || raw === '') return null;
  const declared = Number(config?.areaOpacity);
  const fallback = Number.isFinite(declared) && config?.areaOpacity != null ? declared : 0.55;
  const top = typeof raw === 'number' && Number.isFinite(raw) ? raw : fallback;
  const clamped = Math.min(1, Math.max(0.05, top));
  // 底端按 1/6 收敛（与柱体渐隐同一形状），取两位小数免得色标串里拖一长串浮点尾数
  return { top: clamped, bottom: Math.round(Math.max(0.04, clamped / 6) * 100) / 100 };
}

/**
 * 面积 mark 的 style：默认渐隐，`areaFade=false` 时平涂。
 *
 * 多系列必须按**该系列自己的颜色**做纵深 —— 写死 colors[0] 会把系列洗成同色（与柱图同一历史缺陷）。
 * 走渐隐时不再写 fillOpacity：色标已带 alpha，再乘一道会把整片洗淡（同一件事只留一处口径）。
 */
function areaFillStyle(config: any, colors: string[], data?: any[], seriesField?: string | null,
  fallbackOpacity = 0.3): Record<string, any> {
  const fade = areaFadeStops(config);
  if (!fade) return { fillOpacity: Number(config?.areaOpacity ?? fallbackOpacity) };
  const fillOf = (c: string) => `l(90) 0:${withAlpha(c, fade.top)} 1:${withAlpha(c, fade.bottom)}`;
  if (seriesField && data?.length) {
    const colorOf = seriesColorMap(data, seriesField, colors);
    return { fill: (d: any) => fillOf(colorOf(d?.[seriesField])) };
  }
  return { fill: fillOf(colors[0]) };
}

/**
 * 柱体样式单一口径：圆角 + 填充（渐隐 > 渐变 > 色板主色）。bar/时序柱/瀑布/默认柱图共用。
 *
 * 传了 `seriesField` 时**填充必须按系列取色**（否则多系列柱子同色，图例白给）：
 * 渐隐/渐变都在“该系列自己的颜色”上做纵深，既保住 AS 观感，又让系列之间一眼分得开；
 * 没配渐隐/渐变时不写 fill，交回 G2 色标上色。
 */
function barBodyStyle(config: any, colors: string[], data?: any[], seriesField?: string | null): Record<string, any> {
  const radius = Number.parseFloat(config?.borderRadius ?? 4) || 0;
  const style: Record<string, any> = { radiusTopLeft: radius, radiusTopRight: radius };
  if (!seriesField || !data?.length) {
    const fill = barFadeFill(config, colors)
      || (config?.gradient ? `l(270) 0:${colors[0]} 1:${colors[1] || colors[0]}` : undefined);
    if (fill) style.fill = fill;
    return style;
  }
  const colorOf = seriesColorMap(data, seriesField, colors);
  const raw = config?.barFade;
  const fade = raw === undefined || raw === false || raw === '' || raw === 0
    ? undefined
    : (typeof raw === 'number' && Number.isFinite(raw) ? Math.min(1, Math.max(0.05, raw)) : 0.6);
  if (fade !== undefined) {
    style.fill = (d: any) => {
      const c = colorOf(d?.[seriesField]);
      return `l(90) 0:${withAlpha(c, fade)} 1:${withAlpha(c, Math.max(0.05, fade / 6))}`;
    };
  } else if (config?.gradient) {
    // 多系列的“渐变”是同色系纵深，不跟下一个系列借色（借了就把系列色洗掉了）
    style.fill = (d: any) => {
      const c = colorOf(d?.[seriesField]);
      return `l(270) 0:${c} 1:${withAlpha(c, 0.55)}`;
    };
  }
  return style;
}

/**
 * AS 风格「柱顶帽」(素材 bar.cap：有值柱顶 4px 压边、零值柱 1px 细线)。
 * G2 没有「只描顶边」的 interval 形状，改用同 x 带的浮动区间柱复刻：
 * 主柱与帽子共用 band 刻度 ⇒ 宽度与 x 位置天然对齐，无需换算柱宽。
 * 帽厚按绘图高反推为数据单位（轴域按数据极值估，G2 nice-tick 只会让帽子略细，不会消失）。
 * 分组柱图不画帽：主柱走 dodge 位移，第二个 mark 拿不到同一位移会错位（此时宁缺勿错）。
 */
function withBarCap(spec: any, config: any, data: any[], xCol: string, yCol: string, plotHeight: number): any {
  const capColor = typeof config?.barCap === 'string' && config.barCap.trim() ? config.barCap.trim() : undefined;
  if (!capColor || spec.encode?.color || data.length === 0) return spec;
  const values = data.map(d => Number(d[yCol]) || 0);
  const span = Math.max(...values) - Math.min(0, ...values) || 1;
  const half = (px: number) => (px / Math.max(40, plotHeight)) * span / 2;
  const capData = data.map(d => {
    const v = Number(d[yCol]) || 0;
    // 有值：帽跨柱顶居中；零值：底线以上 1px 细线（与素材 renderItem 的口径一致）
    return { ...d, __capLo: v === 0 ? 0 : v - half(4), __capHi: v === 0 ? half(1) * 2 : v + half(4) };
  });
  const { type, encode, style, labels, scale, interaction, ...view } = spec;
  return {
    ...view,
    type: 'view',
    children: [
      { type, encode, style, labels, scale, interaction },
      { type: 'interval', data: capData, encode: { x: xCol, y: ['__capLo', '__capHi'] }, style: { fill: capColor }, tooltip: false },
    ],
  };
}


/**
 * 无 canvas 度量时的文本占宽估算：全角字形（中日韩文字/全角标点）按 1em，其余按 0.6em。
 * 只用于「会不会挤在一起」的判定，宁可高估（多转一档/多截几个字）也不低估（糊成一坨）。
 */
const FULLWIDTH_CHAR = /[\u2E80-\u303F\u3040-\u33FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\uFFE0-\uFFE6]/;

export function estimateTextWidth(text: string, fontSize: number): number {
  let w = 0;
  for (const ch of String(text)) w += FULLWIDTH_CHAR.test(ch) ? fontSize : fontSize * 0.6;
  return w;
}

/** 按估算宽度截断（尾部省略号）。截断而非抽稀：一个类目都不丢，完整值仍可 hover 看 tooltip。 */
export function truncateToWidth(text: string, maxWidth: number, fontSize: number): string {
  if (!Number.isFinite(maxWidth) || maxWidth <= 0) return text;
  if (estimateTextWidth(text, fontSize) <= maxWidth) return text;
  const budget = Math.max(0, maxWidth - fontSize * 0.6);
  let w = 0;
  let out = '';
  for (const ch of String(text)) {
    const cw = FULLWIDTH_CHAR.test(ch) ? fontSize : fontSize * 0.6;
    if (w + cw > budget) break;
    w += cw;
    out += ch;
  }
  return `${out}…`;
}

const XAXIS_ROTATE_STEPS = [0, 30, 45, 60, 90];
const XAXIS_SIDE_PADDING = 24;         // baseSpec 的左右留白（与 padding[1]/[3] 同源）
const XAXIS_BASE_PADDING = 40;          // 未旋转时的底部留白（维持历史版式）
const XAXIS_MAX_PADDING_RATIO = 0.28;   // 轴下方最多吃掉图高 28%，再多就挤压绘图区
const XAXIS_MAX_PADDING = 96;

/**
 * X 轴类目标签排布裁决：数据多 / 标签长时自动选旋转角、撑高轴带、按需截断。
 *
 * 为什么不能交给 G2 自动旋转（历史缺陷根因）：`labelAutoRotate` 的碰撞消解会把标签转进
 * 「轴带」，而轴带高度 = 组件 size —— 显式 padding 下 `computeAxisSize` 根本不被调用
 * （runtime/layout.js 的 `autoSizeOf` 只在 `padding === 'auto'` 分支里算尺寸），size 恒为
 * 默认 45px。200px 的长标签转 45° 需要 ~150px 竖向空间，contain 检测在任何角度都过不了，
 * autoRotate 的 `recoverWhenFailed` 于是把角度复位成 0 —— 标签原地水平挤成一坨（Chat 结果图）。
 * 结论：角度自己定，并把 padding[2] 与轴 `size` 一起撑开，让「旋转后的投影」真有地方放。
 *
 * 档位择优：旋转是用「竖向空间」换「横向可容纳宽度」，只有换得更多才值得转 ——
 * 类目很少但单个标签很长时（band 很宽），水平截断比旋转保字更多，就不转。
 * 两个方向都得夹住：竖向 = 斜投影装得进轴带；横向 = 最外侧那条标签不能溢出画布
 * （θ>0 时 G2 按 start 对齐从 tick 单向伸出，θ=0 时居中向两侧各占一半）。
 */
export function resolveXAxisLabelLayout(
  labels: string[],
  opts: { width: number; height: number; fontSize: number; explicitRotate?: number },
): { rotate: number; bottomPadding: number; maxLabelWidth: number; truncate: boolean; hideWhenOverlap: boolean } {
  const { width, height, fontSize, explicitRotate = 0 } = opts;
  const sin = (deg: number) => Math.sin((deg * Math.PI) / 180);
  const lineH = fontSize * 1.25 + 8;                     // 标签行高 + tick/labelSpacing
  const plotWidth = Math.max(60, width - XAXIS_SIDE_PADDING * 2);  // 扣左右 padding
  const maxRoom = Math.max(XAXIS_BASE_PADDING, Math.min(XAXIS_MAX_PADDING, Math.round(height * XAXIS_MAX_PADDING_RATIO)));
  const room = Math.max(fontSize * 2, maxRoom - lineH);  // 可用于标签斜向投影的高度
  const base = { rotate: explicitRotate, bottomPadding: XAXIS_BASE_PADDING, maxLabelWidth: 0, truncate: false, hideWhenOverlap: false };
  if (labels.length === 0) return base;
  const band = plotWidth / labels.length;
  const wMax = labels.reduce((m, t) => Math.max(m, estimateTextWidth(t, fontSize)), 0);
  const fits = labels.length < 2 || wMax + 6 <= band;
  const keep = (deg: number) => {
    const cos = Math.cos((deg * Math.PI) / 180);
    // 最外侧 tick 到画布边缘还有 sidePad + band/2，超出就会被裁（G2 不做沿轴方向的兼容）
    const lateral = deg === 0 ? (XAXIS_SIDE_PADDING + band / 2) * 2 : (XAXIS_SIDE_PADDING + band / 2) / cos;
    return deg === 0
      ? Math.min(wMax, band - 6, lateral)
      : Math.min(wMax, room / sin(deg), lateral);
  };
  // 相邻斜标签的垂直间距 = band·sinθ，必须放得下一行文字，否则该档位仍然糊
  const allowed = (deg: number) => deg === 0 || band * sin(deg) >= lineH * 1.05;
  const rotate = explicitRotate || (fits ? 0
    : XAXIS_ROTATE_STEPS.filter(allowed).reduce((best, deg) => (keep(deg) > keep(best) ? deg : best), 0));
  const maxLabelWidth = Math.max(fontSize * 3, rotate === 0 ? (fits ? wMax : keep(0)) : keep(rotate));
  return {
    rotate,
    bottomPadding: Math.max(XAXIS_BASE_PADDING, Math.min(maxRoom, Math.ceil(maxLabelWidth * sin(rotate) + lineH))),
    maxLabelWidth,
    truncate: maxLabelWidth < wMax - 1,
    // 极端拥挤（连竖排都放不下）才允许 G2 抽稀，其余情况一律全量呈现 + 截断
    hideWhenOverlap: band < lineH,
  };
}

/** 不参与 X 轴类目标签自动排布：无极坐标轴（饼/玫瑰/仪表/漏斗/地图/词云/HTML 渲染），
 *  或 x 轴沿圆周排布（radar/radial_bar，统一旋转角无意义）。 */
const NO_X_LABEL_LAYOUT_TYPES = new Set([
  'pie', 'rose', 'gauge', 'funnel', 'word_cloud', 'tree', 'treemap', 'sankey',
  'text_display', 'big_number_trend', 'table_value', 'china_map', 'world_map',
  'radar', 'radial_bar', 'widget_decision',
]);

/** 饼图外围标签单条最大宽度：超出截断（完整名仍在 tooltip/图例里）。
 *  spider 列贴在画布两侧，列位 = 外半径 + 引导线长，不限制宽长名字会把列推出画布。 */
const PIE_LABEL_MAX_WIDTH = 80;

// ── KPI 大数字版式（text_display / big_number_trend 共用）─────────────────────
const KPI_PADDING_X = 16;
const KPI_MIN_VALUE_FONT = 12;
const KPI_MIN_DELTA_FONT = 9;

/** KPI 上下留白：这两类图不渲染坐标轴，沿用 baseSpec 的 40px 轴留白等于凭空吃掉 80px 绘图区，
 *  小尺寸卡片连「数值 + 涨幅」两行都装不下 —— 按图高收敛到 6~16px。 */
export const kpiPaddingY = (height: number) => Math.max(6, Math.min(16, Math.round(height * 0.1)));

/**
 * 大数字 + 涨幅的排版裁决：以**绘图区**为坐标系，字号与行位随图表实际尺寸收敛。
 *
 * 为什么必须自己算（2026-11-12 反馈「大数字下面的涨幅看不到了」根因）：G2 把 mark 层
 * `translate(paddingLeft, paddingTop)` 到了绘图区原点（runtime/plot.js updateBBox），所以
 * `style.x/y` 是**绘图区**坐标。旧写法 `height / 2 ± 20` 拿整图高当基准又用固定偏移，涨幅行的
 * 画布 y = paddingTop + height/2 + 20，图高 ≤130 时越过画布下沿被裁掉半行；x 同理整体右偏 paddingLeft。
 *
 * 两条约束同时成立（缺一不算「适配」）：
 * ① 纵向 —— 数值行 + 行距 + 涨幅行整体在绘图区垂直居中，装不下先缩数值字号（涨幅字号有下限，
 *    它已是最小可读单位，再缩等于没有）；
 * ② 横向 —— 两行各自按 estimateTextWidth 夹进绘图区宽（长数字、带「同比 | 环比」前缀同样可见）。
 *
 * 返回的 padding 与坐标是同一口径，调用方把 padding 原样写进 spec 即可（不得两处各算一遍留白）。
 */
export function kpiNumberLayout(opts: {
  width: number; height: number;
  valueText: string; deltaText?: string;
  /** 数值行基准字号（用户配置优先）：作为上限，仍会被可用高/宽夹小 */
  baseValueFontSize?: number;
}): {
  padding: [number, number, number, number];
  cx: number;
  value: { y: number; fontSize: number };
  delta: { y: number; fontSize: number } | null;
} {
  const { width, height, valueText, deltaText = '' } = opts;
  const base = Number(opts.baseValueFontSize);
  const padY = kpiPaddingY(height);
  const innerWidth = Math.max(24, width - KPI_PADDING_X * 2);
  const innerHeight = Math.max(24, height - padY * 2);
  const hasDelta = !!deltaText;
  // 涨幅行：占绘图区高 16%，夹在 9~14px。不跟数值等比缩是因为它字号本就小，
  // 等比下去会跌破可读下限（缩没 = 静默丢信息，等同把涨幅藏回画布外）。
  const deltaFontSize = hasDelta ? Math.max(KPI_MIN_DELTA_FONT, Math.min(14, Math.round(innerHeight * 0.16))) : 0;
  const gap = hasDelta ? Math.max(3, Math.min(8, Math.round(innerHeight * 0.06))) : 0;
  // 按宽回缩：给定字号下估宽超了，就按比例换算出「刚好塞进 innerWidth」的字号（不低于下限）
  const fitByWidth = (text: string, size: number, minSize: number) => {
    const w = estimateTextWidth(text, size);
    return w <= innerWidth ? size : Math.max(minSize, Math.floor(innerWidth * size / w));
  };
  const valueRoom = hasDelta ? innerHeight - deltaFontSize - gap : innerHeight;
  // 取整后才参与排版：否则「渲染字号（整数）」与「占位高度（小数）」不一致，行数会整块偏移半像素
  const valueFontSize = Math.round(fitByWidth(String(valueText),
    Math.max(KPI_MIN_VALUE_FONT, Math.min(Number.isFinite(base) && base > 0 ? base : 36, valueRoom)),
    KPI_MIN_VALUE_FONT));
  const deltaSize = hasDelta ? fitByWidth(deltaText, deltaFontSize, KPI_MIN_DELTA_FONT) : 0;
  const total = valueFontSize + (hasDelta ? gap + deltaSize : 0);
  const top = (innerHeight - total) / 2;
  return {
    padding: [padY, KPI_PADDING_X, padY, KPI_PADDING_X],
    cx: innerWidth / 2,
    value: { y: top + valueFontSize / 2, fontSize: valueFontSize },
    delta: hasDelta ? { y: top + valueFontSize + gap + deltaSize / 2, fontSize: deltaSize } : null,
  };
}

// Build G2 spec based on chart type
export function buildG2Spec(chartType: string, columns: string[], rows: any[], config: Record<string, any>, isDark: boolean, width: number, height: number, visual?: LocalVisual) {
  isDark = visual?.mode ? visual.mode === 'dark' : isDark;
  // 图表配色运行时读取主题令牌(--chart-*/--foreground/--card),随 9 套主题联动;
  // isDark 仅用于 G2 内置 classic/classicDark 网格底纹与字重微调
  const colors = config?.colorScheme?.length ? config.colorScheme : visual?.palette?.length ? visual.palette : readChartPalette(isDark);
  const local = !!visual?.mode;
  const textColor = visual?.textColor || (local ? (isDark ? '#e2e8f0' : '#111827') : readToken('--foreground', isDark ? '#f0f0f0' : '#333'));
  const subTextColor = visual?.subTextColor || (local ? (isDark ? '#94a3b8' : '#6b7280') : readToken('--muted-foreground', isDark ? '#aaa' : '#555'));
  const bgColor = visual?.viewBg || (local ? (isDark ? '#0f172a' : '#ffffff') : readToken('--card', isDark ? '#1f1f3a' : '#ffffff'));
  const borderColor = config?.axisStyle?.gridColor || config?.gridColor || visual?.gridColor || (local ? (isDark ? '#334155' : '#e5e7eb') : readToken('--border', isDark ? '#444' : '#e5e7eb'));
  const tooltipBg = visual?.popoverBg || (local ? bgColor : readToken('--popover', bgColor));
  const tooltipText = local ? textColor : readToken('--popover-foreground', textColor);
  // Axis label styling — brighter, larger, bolder on dark backgrounds
  // 字模轴字号: 素材 chartTypography.axisLabelSize / legendSize 均 12px, 主题包给了就按它走(未给维持原明暗自适应 11/12)
  const axisFontSize = Number.parseFloat(config?.axisLabelSize ?? '') || undefined;
  const axisLabelStyle = isDark
    ? { labelFill: textColor, labelFontSize: axisFontSize ?? 12, labelFontWeight: '600' as const }
    : { labelFill: subTextColor, labelFontSize: axisFontSize ?? 11, labelFontWeight: '400' as const };
  const legendLabelStyle = isDark
    ? { fill: textColor, fontSize: axisFontSize ?? 12, fontWeight: '600' as const }
    : { fill: subTextColor, fontSize: axisFontSize ?? 11 };

  // 坐标轴显式配置（图表级覆盖，在「坐标轴」页可配）：这一组曾经只写 config、无人消费，
  // 面板拉满也没反应（ui-vis-library-style.md §2：“存了不生效”就是缺陷，不是待办）。
  // 0 = 自动（由 resolveXAxisLabelLayout 按标签宽/类目数裁决），>0 = 用户指定的固定角度。
  const rotate = Math.max(0, Math.min(90, Number(config?.xAxisLabelRotate) || 0));
  const axisGrid = config?.showGrid === false ? { grid: false } : {};
  // y 轴不渲染标题：G2 的 y 轴标题默认竖排（theme.axisLeft.titleDirection='vertical' + rotate(-90)），
  // 小字号旋转后贴左边缘糊成一排，用户读不出（反馈“左侧字看不清”），而原文又是物理列名；
  // 但用户显式写了「Y轴名称」时照他说的渲染（显式覆盖优先于默认）。
  const yAxis: any = config?.showYAxis === false ? false : {
    title: typeof config?.yAxisName === 'string' && config.yAxisName.trim() ? config.yAxisName.trim() : false,
    ...axisGrid,
    style: axisLabelStyle,
  };

  const xCol = config?.xCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
  const yCol = config?.yCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];

  // Detect group column
  const groupCol = detectGroupColumn(columns, rows, config);
  const hasGroups = groupCol && ['line', 'bar', 'area'].includes(chartType);

  // Check if x-axis is time data
  const xValues = rows.map(r => r[xCol]);
  const isTimeData = isTimeColumn(xValues);

  // Time aggregation settings
  const enableTimeAgg = config?.enableTimeAgg === true;
  const timeGranularity = config?.timeGranularity || 'auto';
  const aggMethod = config?.aggMethod || 'sum';

  // Prepare data
  let processedData: any[] = [];
  let seriesField: string | null = null;

  if (enableTimeAgg && isTimeData) {
    const aggResult = aggregateTimeData(rows, xCol, yCol, groupCol, aggMethod, timeGranularity);
    if (groupCol && aggResult.seriesData) {
      // Multi-series with time aggregation
      seriesField = groupCol;
      Object.entries(aggResult.seriesData).forEach(([name, values]) => {
        aggResult.xData.forEach((x, i) => {
          if (values[i] !== null) {
            processedData.push({ [xCol]: x, [yCol]: values[i], [groupCol]: name });
          }
        });
      });
    } else {
      // Single series with time aggregation
      processedData = aggResult.xData.map((x, i) => ({
        [xCol]: x,
        [yCol]: aggResult.yData![i],
      }));
    }
  } else if (hasGroups) {
    // Multi-series without time aggregation
    seriesField = groupCol;
    const grouped = groupDataByColumn(rows, xCol, yCol, groupCol!);
    Object.entries(grouped.seriesData).forEach(([name, values]) => {
      grouped.xData.forEach((x, i) => {
        if (values[i] !== null) {
          processedData.push({ [xCol]: x, [yCol]: values[i], [groupCol!]: name });
        }
      });
    });
  } else {
    // Single series, sort by time if needed
    if (isTimeData) {
      const sortedRows = [...rows].sort((a, b) => {
        const da = parseTime(a[xCol]);
        const db = parseTime(b[xCol]);
        if (da && db) return da.getTime() - db.getTime();
        return 0;
      });
      processedData = sortedRows.map(r => ({ ...r }));
    } else {
      processedData = rows.map(r => ({ ...r }));
    }
  }

  // X 轴类目标签排布（自动旋转角 + 轴带撑高 + 按需截断）：依赖标签集，故在数据准备后裁决。
  // x 通道实际编码的列在分支间有差异（时序族走 timeCol、瀑布走 categoryCol、日历热力走 dateCol…），
  // 拿错列会按错的标签集判角；标签集也要取真正渲染的那批行（时间聚合后类目数会变）。
  const xLabelCol = chartType.startsWith('timeseries') ? (config?.timeCol || xCol)
    : chartType === 'waterfall' ? (config?.categoryCol || xCol)
    : chartType === 'calendar_heatmap' ? (config?.dateCol || xCol)
    : chartType === 'bubble' ? (config?.xCol || columns.find(c => typeof rows[0]?.[c] === 'number') || xCol)
    : xCol;
  const xLabelRows = chartType.startsWith('timeseries') ? rows : processedData;
  const xLabelFontSize = Number(axisLabelStyle.labelFontSize) || 11;
  const xLabels = NO_X_LABEL_LAYOUT_TYPES.has(chartType)
    ? []
    : [...new Set(xLabelRows.map(r => String(r[xLabelCol] ?? '')))];
  const xLayout = resolveXAxisLabelLayout(xLabels, {
    width, height, fontSize: xLabelFontSize, explicitRotate: rotate,
  });
  // 柱顶帽/瀑布等按绘图区高反推数据单位的分支共用：扣掉上下 padding 后的真实高度
  const plotHeightHint = Math.max(60, height - 40 - xLayout.bottomPadding);
  const xAxis: any = config?.showXAxis === false ? false : {
    // x 轴不渲染标题：G2 默认把编码字段名（如 app_code）当轴标题，而底部 padding 只为旋转标签留高、
    // 不含标题行 → 标题溢出卡片下沿（用户反馈）。与 y 轴同口径：不编码物理列名当标题，
    // 需要轴名时由业务名配置，绝不把裸列名甩给用户（ui-resource-display / 物理名泄露）。
    title: false,
    // 旋转/截断一律由 resolveXAxisLabelLayout 裁决，不再交给 G2 自决：
    // 显式 padding 下它的 labelAutoRotate 永远算不过碰撞检测（见该函数注释），等于没开。
    labelAutoRotate: false,
    labelAutoHide: xLayout.hideWhenOverlap,
    // size 即轴带高度：与 padding[2] 同值，G2 的碰撞消解才知道标签真有多少地方可摆
    size: xLayout.bottomPadding,
    ...(xLayout.rotate ? { labelTransform: `rotate(${xLayout.rotate})` } : {}),
    // 超宽标签截断（完整值仍在 tooltip 里），避免长 URL/长名称溢出画布
    ...(xLayout.truncate
      ? { labelFormatter: (v: any) => truncateToWidth(String(v), xLayout.maxLabelWidth, xLabelFontSize) }
      : {}),
    ...axisGrid,
    style: axisLabelStyle,
  };

  // Build spec based on chart type
  const baseSpec: any = {
    width,
    height,
    autoFit: false,
    theme: {
      type: isDark ? 'classicDark' : 'classic',
      // 未编码 color 的单系列走 theme.color，分组系列才走色盘。
      color: colors[0],
      category10: colors,
      category20: [...colors, ...colors],
      // 放在主题内，避免被各图表的 mark style 覆盖。
      view: { viewFill: bgColor },
      axis: {
        labelFill: subTextColor, labelOpacity: 1,
        titleFill: textColor, titleOpacity: 1,
        gridStroke: borderColor, gridStrokeOpacity: 1,
        lineStroke: borderColor, lineStrokeOpacity: 1,
        tickStroke: borderColor, tickOpacity: 1,
      },
      legendCategory: {
        itemLabelFill: subTextColor, itemLabelFillOpacity: 1,
        itemValueFill: subTextColor, itemValueFillOpacity: 1,
        titleFill: textColor, titleFillOpacity: 1,
      },
      legendContinuous: {
        labelFill: subTextColor, labelFillOpacity: 1,
        titleFill: textColor, titleFillOpacity: 1,
      },
      tooltip: {
        css: {
          '.g2-tooltip': {
            'background-color': tooltipBg,
            color: tooltipText,
            border: `1px solid ${borderColor}`,
            'border-radius': '8px',
            'font-family': 'inherit',
            'box-shadow': '0 6px 16px rgb(0 0 0 / 0.12)',
          },
          '.g2-tooltip-title': { color: subTextColor },
          // 数值/名称显式取 popover-foreground，G2 默认黑色在深色 popover 上会看不见。
          '.g2-tooltip-list-item-name-label': { color: tooltipText },
          '.g2-tooltip-list-item-value': { color: tooltipText, 'font-weight': '600' },
        },
      },
    },
    padding: [40, 24, xLayout.bottomPadding, 24],
    style: {
      viewFill: bgColor,
    },
  };

  switch (chartType) {
    case 'bar': {
      const barSpec: any = {
        ...baseSpec,
        type: 'interval',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined },
        // 多系列默认**分组**（每系列一根柱、各自取色）：G2 的 interval 带 color 时默认堆叠，
        // 会把几个系列挤进同一根柱、读不出谁是谁（2026-11-10 反馈，参考图为分组柱）。
        // 真要累计口径（部分与整体）在图表配置里切「堆叠」，不静默替人决定。
        ...(seriesField && config?.seriesLayout !== 'stack'
          ? { transform: [{ type: 'dodgeX' }] } : {}),
        axis: {
          x: xAxis,
          y: yAxis,
        },
        scale: { color: { range: colors } },
        style: barBodyStyle(config, colors, processedData, seriesField),
        labels: config?.showLabel ? [{ text: yCol, style: { fill: textColor } }] : [],
        interaction: { tooltip: { shared: true } },
      };
      return withBarCap(barSpec, config, processedData, xCol, yCol, plotHeightHint);
    }

    case 'line': {
      const lineEncode = { x: xCol, y: yCol, color: seriesField || undefined, shape: config?.smooth === false ? undefined : 'smooth' };
      if (config?.areaFill) return {
        ...baseSpec, type: 'view', data: processedData,
        scale: { color: { range: colors } },
        axis: { x: xAxis, y: yAxis },
        children: [
          { type: 'area', encode: { x: xCol, y: yCol, color: seriesField || undefined, shape: config.smooth === false ? 'area' : 'smooth' }, style: areaFillStyle(config, colors, processedData, seriesField || null, 0.15) },
          { type: 'line', encode: { x: xCol, y: yCol, color: seriesField || undefined, shape: config.smooth === false ? 'line' : 'smooth' }, style: { lineWidth: config.lineWidth ?? 2, ...(config.glow ? { shadowColor: colors[0], shadowBlur: 8 } : {}) } },
          // 单点时 area/line 都画不出形状，叠加圆点兜住可见性
          ...(isSparseSeries(processedData, xCol) ? [visiblePointMark(lineEncode)] : []),
        ],
      };
      return ensureVisiblePoints({
        ...baseSpec,
        type: 'line',
        data: processedData,
        encode: lineEncode,
        axis: {
          x: xAxis,
          y: yAxis,
        },
        scale: { color: { range: colors } },
        style: { lineWidth: config?.lineWidth ?? 2 },
        interaction: { tooltip: { shared: true } },
      }, processedData, xCol);
    }

    case 'area':
      return ensureVisiblePoints({
        ...baseSpec,
        type: 'area',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined, shape: config?.smooth === false ? undefined : 'smooth' },
        axis: {
          x: xAxis,
          y: yAxis,
        },
        scale: { color: { range: colors } },
        style: areaFillStyle(config, colors, processedData, seriesField || null),
        interaction: { tooltip: { shared: true } },
      }, processedData, xCol);

    case 'pie': {
      const pieData = rows.map(r => ({
        name: String(r[xCol]),
        value: Number(r[yCol]) || 0,
      }));
      // theta 坐标是 Transpose+Polar：x 通道落在「半径」维度、y 通道落在「角度」维度。
      // 曾经这里编码了 x='name'，后果是（历史缺陷，截图表现为“环细如发丝 + 标签糊成一团”）：
      //  ① 每个切片只占 1/类目数 的一段同心细环（8 个类目时环粗只剩几像素），而不是整段半径；
      //  ② stackY 默认按 x 分组，各自独立堆叠 → 角度按「值/最大值」而不是「值/总和」，
      //     各切片角度之和 > 360° 互相叠压，小切片全挤在同一角度区间，外围标签必然重叠。
      // 正确写法只编码 y + color，x 由 G2 的 MaybeZeroX 补常量 0 → 切片占满 [inner, outer]、角度累加。
      // innerRadius 按「占外半径的比例」理解（上界 0.75：环粗不得退化到发丝；0 = 实心饼），
      // 并等比跟随下面的 outerRadius —— 主题包写 0.58 就仍是“环粗 = 外半径的 42%”，
      // 不会因为给标签让位而把环越改越细（字模/主题包实际取值 0.4~0.6）。
      const rawInner = Number(config?.innerRadius);
      const innerRatio = Number.isFinite(rawInner) ? Math.max(0, Math.min(0.75, rawInner)) : 0.4;
      // 外半径给标签留环带：写满 1 时 spider 标签列贴画布左右边缘，长名字被裁成残字。
      const showPieLabel = config?.showLabel !== false;
      const outerRadius = showPieLabel ? 0.72 : 0.9;
      const innerRadius = innerRatio * outerRadius;
      const pieLabelSize = Number.parseFloat(config?.axisLabelSize ?? '') || 11;
      return {
        ...baseSpec,
        type: 'interval',
        data: pieData,
        encode: { y: 'value', color: 'name' },
        transform: [{ type: 'stackY' }],
        coordinate: { type: 'theta', innerRadius, outerRadius },
        scale: { color: { range: colors } },
        style: { stroke: bgColor, lineWidth: 2 },
        // spider 布局：标签按左右两列竖向避让（dodgeY）并保留引导线锚在弧上，
        // 放不下时才隐藏（值仍在 tooltip/图例里）；单纯 overlapDodgeY 会把引导线整条拖离切片。
        labels: showPieLabel ? [{
          text: (d: any) => truncateToWidth(String(d.name), PIE_LABEL_MAX_WIDTH, pieLabelSize),
          position: 'spider',
          style: { fontSize: pieLabelSize, fill: textColor },
          connector: true,
          connectorStroke: borderColor,
          connectorLineWidth: 1,
          connectorLength: 8,
          connectorLength2: 8,
          connectorDistance: 4,
          labelHeight: Math.round(pieLabelSize * 1.6),
        }] : [],
        legend: config?.legend === false ? false : { color: { position: config?.legend || 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'scatter':
      return {
        ...baseSpec,
        type: 'point',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined },
        scale: { color: { range: colors } },
        style: { r: 4, opacity: 0.7 },
        axis: {
          x: xAxis,
          y: yAxis,
        },
        interaction: { tooltip: {} },
      };

    case 'radar': {
      const radarCols = columns.filter(c => typeof rows[0]?.[c] === 'number').slice(0, 8);
      const radarData: any[] = [];
      rows.slice(0, 5).forEach(r => {
        radarCols.forEach(col => {
          radarData.push({ name: col, value: Number(r[col]) || 0, category: String(r[xCol]) });
        });
      });
      return {
        ...baseSpec,
        type: config?.areaOpacity != null ? 'area' : 'line',
        data: radarData,
        encode: { x: 'name', y: 'value', color: 'category' },
        // 极坐标：竖向渐隐没有方向可言（"上深下浅"会随角度绕圈），这里刻意不套 areaFillStyle
        coordinate: { type: 'polar' },
        scale: { color: { range: colors } },
        style: { lineWidth: config?.lineWidth ?? 2, fillOpacity: config?.areaOpacity ?? 0 },
        axis: { x: xAxis, y: yAxis },
        interaction: { tooltip: {} },
      };
    }

    case 'gauge': {
      const input = Number(rows[0]?.[yCol]);
      const val = Number.isFinite(input) ? input : 0;
      const maximum = Number(config?.maxValue);
      // G2 使用 target/total；未配置量程时兼容比例值与百分数。
      const total = Number.isFinite(maximum) && maximum > 0 ? maximum : (val >= 0 && val <= 1 ? 1 : Math.max(100, val));
      const thresholds = Array.isArray(config?.thresholds)
        ? [...new Set<number>(config.thresholds.filter((t: any) => typeof t === 'number' && t > 0 && t < 1))].sort((a, b) => a - b).map(t => t * total).concat(total)
        : [total];
      return {
        ...baseSpec,
        type: 'gauge',
        data: { target: Math.max(0, Math.min(val, total)), total, thresholds },
        style: {
          pointerShape: 'pointer',
          pinShape: 'circle',
          textFill: config?.valueColor || textColor,
          textFontSize: 24,
          textFontWeight: '600',
        },
        scale: { color: { range: colors } },
      };
    }

    case 'funnel': {
      const funnelData = rows.map(r => ({
        action: String(r[xCol]),
        pv: Number(r[yCol]) || 0,
      })).sort((a, b) => b.pv - a.pv);

      return {
        ...baseSpec,
        type: 'interval',
        data: funnelData,
        encode: { x: 'action', y: 'pv', color: 'action', shape: 'funnel' },
        transform: [{ type: 'symmetryY' }],
        scale: { x: { padding: 0 }, color: { range: colors } },
        coordinate: { transform: [{ type: 'transpose' }] },
        axis: false,
        style: { stroke: bgColor, lineWidth: 1, ...(config?.gradient ? { fill: (d: any) => {
          const i = Math.max(0, funnelData.findIndex(row => row.action === d.action));
          return `l(0) 0:${colors[i % colors.length]} 1:${colors[(i + 1) % colors.length]}`;
        } } : {}) },
        labels: [{
          text: (d: any) => `${d.action}\n${d.pv}`,
          position: 'inside',
          transform: [{ type: 'contrastReverse' }],
          style: { fill: '#fff', fontSize: 11 }
        }],
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'heatmap': {
      const valCols = columns.filter(c => typeof rows[0]?.[c] === 'number').slice(0, 10);
      const heatData: any[] = [];
      rows.forEach(r => {
        valCols.forEach(vc => {
          heatData.push({ x: String(r[xCol]), y: vc, value: Number(r[vc]) || 0 });
        });
      });
      return {
        ...baseSpec,
        type: 'cell',
        data: heatData,
        encode: { x: 'x', y: 'y', color: 'value' },
        scale: { color: { range: colors } },
        style: { inset: 1 },
        labels: [{ text: 'value', style: { fill: textColor, fontSize: 10 } }],
        axis: {
          x: xAxis,
          y: yAxis,
        },
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'text_display': {
      // Text display for count/KPI metrics with optional YoY/MoM comparison
      const value = rows.length > 0 ? Number(rows[0][yCol]) || 0 : 0;

      // Format value
      const formatValue = (val: number, fmt?: string) => {
        switch (fmt) {
          case 'percent': return `${val.toFixed(1)}%`;
          case 'currency': return `¥${val.toLocaleString()}`;
          case 'raw': return String(val);
          default: return val.toLocaleString();
        }
      };

      const prefix = config?.valuePrefix || '';
      const suffix = config?.valueSuffix || '';
      const valueFontSize = config?.valueFontSize || 48;
      const formattedValue = `${prefix}${formatValue(value, config?.valueFormat)}${suffix}`;

      // YoY (同比) comparison
      const yoyColumn = config?.yoyColumn;
      const yoyValue = yoyColumn && rows.length > 0 ? Number(rows[0][yoyColumn]) : null;
      const yoyChange = yoyValue && yoyValue !== 0 ? ((value - yoyValue) / yoyValue * 100) : null;

      // MoM (环比) comparison
      const momColumn = config?.momColumn;
      const momValue = momColumn && rows.length > 0 ? Number(rows[0][momColumn]) : null;
      const momChange = momValue && momValue !== 0 ? ((value - momValue) / momValue * 100) : null;

      const showComparison = config?.showComparison !== false;
      const deltaText = showComparison
        ? [
            yoyChange !== null ? `同比 ${yoyChange >= 0 ? '↑' : '↓'} ${Math.abs(yoyChange).toFixed(1)}%` : '',
            momChange !== null ? `环比 ${momChange >= 0 ? '↑' : '↓'} ${Math.abs(momChange).toFixed(1)}%` : '',
          ].filter(Boolean).join('  |  ')
        : '';
      const layout = kpiNumberLayout({ width, height, valueText: formattedValue, deltaText, baseValueFontSize: valueFontSize });
      const deltaColor = (yoyChange !== null && yoyChange >= 0) || (momChange !== null && momChange >= 0)
        ? '#52c41a'
        : (yoyChange !== null && yoyChange < 0) || (momChange !== null && momChange < 0)
          ? '#ff4d4f'
          : textColor;

      return {
        ...baseSpec,
        padding: layout.padding,
        type: 'view',
        children: [
          // Main value
          {
            type: 'text',
            data: { value: null },
            style: {
              text: formattedValue,
              x: layout.cx,
              y: layout.value.y,
              fontSize: layout.value.fontSize,
              fontWeight: 'bold',
              fill: config?.valueColor || textColor,
              textAlign: 'center',
            },
          },
          // Comparison labels
          ...(layout.delta ? [
            {
              type: 'text',
              data: { value: null },
              style: {
                text: deltaText,
                x: layout.cx,
                y: layout.delta.y,
                fontSize: layout.delta.fontSize,
                fill: deltaColor,
                textAlign: 'center',
              },
            },
          ] : []),
        ],
      };
    }

    case 'big_number_trend': {
      const total = rows.reduce((sum, r) => sum + (Number(r[yCol]) || 0), 0);
      const lastVal = Number(rows[rows.length - 1]?.[yCol]) || 0;
      const prevVal = Number(rows[rows.length - 2]?.[yCol]) || lastVal;
      const changePercent = prevVal ? ((lastVal - prevVal) / prevVal * 100).toFixed(1) : '0';
      const isUp = lastVal >= prevVal;
      const valueText = total.toLocaleString();
      const deltaText = `${isUp ? '↑' : '↓'} ${changePercent}%`;
      const layout = kpiNumberLayout({ width, height, valueText, deltaText });
      const delta = layout.delta!;

      return {
        ...baseSpec,
        padding: layout.padding,
        type: 'view',
        children: [
          // 趋势线先画、文字后画：旧顺序把 line 放在最后，折线会压在数值与涨幅上；
          // 收紧上下留白后折线铺满整卡，遮挡更明显 —— 数值才是这张图的主读信息。
          {
            type: 'line',
            data: processedData,
            encode: { x: xCol, y: yCol },
            style: { stroke: colors[0], lineWidth: 2 },
            axis: false,
          },
          {
            type: 'text',
            data: { value: null },
            style: {
              text: valueText,
              x: layout.cx,
              y: layout.value.y,
              fontSize: layout.value.fontSize,
              fontWeight: 'bold',
              fill: config?.valueColor || textColor,
              textAlign: 'center',
            },
          },
          {
            type: 'text',
            data: { value: null },
            style: {
              text: deltaText,
              x: layout.cx,
              y: delta.y,
              fontSize: delta.fontSize,
              fill: isUp ? '#52c41a' : '#ff4d4f',
              textAlign: 'center',
            },
          },
        ],
      };
    }

    case 'timeseries_line':
    case 'timeseries_bar':
    case 'timeseries_area': {
      const timeCol = config?.timeCol || xCol;
      const valCol = config?.valCol || yCol;
      const tsGroupCol = detectGroupColumn(columns, rows, config);
      const tsHasGroups = tsGroupCol && ['timeseries_line', 'timeseries_bar', 'timeseries_area'].includes(chartType);

      let tsData = rows;
      if (isTimeColumn(rows.map(r => r[timeCol]))) {
        tsData = [...rows].sort((a, b) => {
          const da = parseTime(a[timeCol]);
          const db = parseTime(b[timeCol]);
          if (da && db) return da.getTime() - db.getTime();
          return 0;
        });
      }

      const isBar = chartType === 'timeseries_bar';
      const isArea = chartType === 'timeseries_area';

      const chartTypeG2 = isBar ? 'interval' : isArea ? 'area' : 'line';
      // 字模样式对时序族同样生效(与 bar/line/area 分支同口径, 统一走 barBodyStyle):
      // 圆角/渐隐/渐变/标签/线宽/平滑/面积透明度全部消费 config(resolveChartDesign 从主题包注入),
      // 曾经写死 radius 4/lineWidth 2 不看 gradient —— 科技风柱状图没有渐变就是这里丢的

      const tsSpec: any = {
        ...baseSpec,
        type: chartTypeG2,
        data: tsData,
        encode: {
          x: timeCol, y: valCol, color: tsHasGroups ? tsGroupCol! : undefined,
          shape: isBar ? undefined : (config?.smooth === false ? undefined : 'smooth'),
        },
        axis: {
          x: xAxis,
          y: yAxis,
        },
        scale: { color: { range: colors } },
        // 时序柱与 bar 同口径：多系列默认分组，填充按系列取色（写死 colors[0] 会把系列洗成同色）
        ...(isBar && tsHasGroups && config?.seriesLayout !== 'stack'
          ? { transform: [{ type: 'dodgeX' }] } : {}),
        style: isBar
          ? barBodyStyle(config, colors, tsData, tsHasGroups ? tsGroupCol : null)
          : { lineWidth: config?.lineWidth ?? 2,
              // 面积族与 area 分支同一口径（默认竖向渐隐，系列各自取色）；纯线不填底
              ...(isArea ? areaFillStyle(config, colors, tsData, tsHasGroups ? tsGroupCol : null) : { fillOpacity: 0 }),
              ...(config?.glow ? { shadowColor: colors[0], shadowBlur: 8 } : {}) },
        labels: isBar && config?.showLabel ? [{ text: valCol, style: { fill: textColor } }] : [],
        interaction: { tooltip: { shared: true } },
      };
      // 柱状单点本身可见；线/面单点画不出形状，叠加圆点
      return isBar
        ? withBarCap(tsSpec, config, tsData, timeCol, valCol, plotHeightHint)
        : ensureVisiblePoints(tsSpec, tsData, timeCol);
    }

    case 'tree':
    case 'treemap': {
      const nameCol = config?.nameCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
      const valCol = config?.valCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];
      const parentCol = config?.parentCol || columns.find(c => typeof rows[0]?.[c] === 'string' && c !== nameCol);

      let treeData: any[];
      if (parentCol) {
        const nodeMap: Record<string, any> = {};
        rows.forEach(r => {
          const name = String(r[nameCol]);
          nodeMap[name] = { name, value: Number(r[valCol]) || 0, children: [] };
        });
        rows.forEach(r => {
          const parent = String(r[parentCol]);
          const name = String(r[nameCol]);
          if (nodeMap[parent] && parent !== name) {
            nodeMap[parent].children.push(nodeMap[name]);
          }
        });
        const roots = rows.filter(r => !nodeMap[String(r[parentCol])] || String(r[parentCol]) === String(r[nameCol]));
        treeData = roots.length > 0 ? [nodeMap[String(roots[0][nameCol])]] : [{ name: 'root', children: Object.values(nodeMap) }];
      } else {
        treeData = rows.map(r => ({ name: String(r[nameCol]), value: Number(r[valCol]) || 0 }));
      }

      return {
        ...baseSpec,
        type: chartType === 'tree' ? 'tree' : 'treemap',
        data: { value: { children: treeData } },
        encode: { value: 'value' },
        scale: { color: { range: colors } },
        style: { labelFill: textColor, labelFontSize: 12, labelFontWeight: '600' },
        legend: { color: { itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'waterfall': {
      const categoryCol = config?.categoryCol || xCol;
      const valCol = config?.valCol || yCol;
      let cumulative = 0;
      const waterfallData = rows.map((r, i) => {
        const val = Number(r[valCol]) || 0;
        if (i === 0) {
          cumulative = val;
          return { [categoryCol]: String(r[categoryCol]), start: 0, end: val, value: val };
        }
        const start = cumulative;
        cumulative += val;
        return { [categoryCol]: String(r[categoryCol]), start, end: cumulative, value: val };
      });

      return {
        ...baseSpec,
        type: 'interval',
        data: waterfallData,
        encode: { x: categoryCol, y: ['start', 'end'], color: (d: any) => d.value >= 0 ? 'increase' : 'decrease' },
        scale: { color: { domain: ['increase', 'decrease'], range: ['#52c41a', '#ff4d4f'] } },
        // 瀑布柱的填充色由涨跌语义色决定, 不吃 barFade/barCap(主题包主色会抹掉增减区分), 只共用圆角+跨色渐变口径
        style: { radiusTopLeft: Number.parseFloat(config?.borderRadius ?? 4) || 0, radiusTopRight: Number.parseFloat(config?.borderRadius ?? 4) || 0,
          ...(config?.gradient ? { fill: `l(270) 0:${colors[0]} 1:${colors[1] || colors[0]}` } : {}) },
        labels: config?.showLabel ? [{ text: yCol, style: { fill: textColor } }] : [],
        axis: {
          x: xAxis,
          y: yAxis,
        },
        legend: { color: { itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'sankey': {
      // 桑基要的是「源 / 目标 / 数值」三件，而选择框只给通用 X/Y/分组：按 X=源、分组=目标、Y=数值对齐。
      // 凑不齐三件就不画（旧写法把 targetCol 兜到 columns[1]，那往往是数值列 →
      // 生成一个“节点名叫数字”的假图，或整幅空白）。
      const stringCols = columns.filter(c => typeof rows[0]?.[c] === 'string');
      const numberCols = columns.filter(c => typeof rows[0]?.[c] === 'number');
      const sourceCol = config?.sourceCol || config?.xCol || stringCols[0];
      const targetCol = config?.targetCol || config?.groupCol
        || stringCols.find(c => c !== sourceCol) || (typeof rows[0]?.[config?.yCol] === 'string' ? config?.yCol : undefined);
      const valueCol = config?.valueCol
        || (numberCols.includes(config?.yCol) ? config?.yCol : undefined) || numberCols[0];
      if (!sourceCol || !targetCol || !valueCol || targetCol === sourceCol) {
        throw new Error('桑基图需要「源」与「目标」两列文本加一列数值，当前数据凑不齐（可改用其它图型或补上目标列）');
      }

      const links = rows.map(r => ({
        source: String(r[sourceCol]),
        target: String(r[targetCol]),
        value: Number(r[valueCol]) || 0,
      }));

      return {
        ...baseSpec,
        type: 'view',
        children: [{
          type: 'sankey',
          // data 只传边数组，不传 {nodes, links} 对象：G2 5.4 的 sankey 拿到对象形态时整幅静默不画
          // （无报错无告警、彩色像素为 0，2026-11-10 浏览器实测定位）。节点由 G2 按边端点自己派生成
          // {key}，正好和它默认的 nodeId = d => d.key 对齐；手写 nodes 反而要自己保证字段叫 key
          // （旧写法造 {name} 时 d3-sankey 直接抛 “missing: xxx”，也是整幅空白）。
          data: links,
          // encode 只被 initializeData 读 source/target/value（默认取 d.source/d.target），
          // x/y 字段对布局不起作用，留着只会让人以为它们在控位。
          // linkColor：给带子按「源节点」取色。不写时 G2 把所有边刷成 #aaa@50%（在浅底上正好是
          // 一片 212,212,212），多条边完全糊在一起看不出谁到谁（浏览器实测对比：不写=全灰带，
          // 写=与源竖条同色系彩带）。注意这里必须传返回字符串的函数：传字段名 'source' 会拿到
          // 布局后的节点对象，G2 内部报 colorStr.indexOf is not a function。
          encode: { value: 'value', linkColor: (d: any) => String(d.source?.key ?? d.source ?? '') },
          style: { labelFill: textColor, labelFontSize: 12, labelFontWeight: '600' },
          interaction: { tooltip: {} },
        }],
      };
    }

    case 'boxplot': {
      const numCols = columns.filter(c => typeof rows[0]?.[c] === 'number');
      const dataCol = config?.dataCol || numCols[0] || columns[0];
      const categoryCol = config?.categoryCol || columns.find(c => typeof rows[0]?.[c] === 'string') || '';

      const calculateBoxStats = (values: number[]) => {
        const sorted = [...values].sort((a, b) => a - b);
        const n = sorted.length;
        return {
          min: sorted[0],
          q1: sorted[Math.floor(n * 0.25)],
          median: sorted[Math.floor(n * 0.5)],
          q3: sorted[Math.floor(n * 0.75)],
          max: sorted[n - 1],
        };
      };

      let boxData: any[];
      if (categoryCol) {
        const groups: Record<string, number[]> = {};
        rows.forEach(r => {
          const cat = String(r[categoryCol]);
          if (!groups[cat]) groups[cat] = [];
          groups[cat].push(Number(r[dataCol]) || 0);
        });
        boxData = Object.entries(groups).map(([cat, values]) => ({
          category: cat,
          ...calculateBoxStats(values),
        }));
      } else {
        const values = rows.map(r => Number(r[dataCol]) || 0);
        boxData = [{ category: dataCol, ...calculateBoxStats(values) }];
      }

      return {
        ...baseSpec,
        type: 'box',
        data: boxData,
        encode: { x: 'category', y: ['min', 'q1', 'median', 'q3', 'max'] },
        style: { stroke: colors[0], fill: colors[0], fillOpacity: 0.3 },
        axis: {
          x: xAxis,
          y: yAxis,
        },
        interaction: { tooltip: {} },
      };
    }

    case 'bubble': {
      const numCols = columns.filter(c => typeof rows[0]?.[c] === 'number');
      const bx = config?.xCol || numCols[0] || columns[0];
      const by = config?.yCol || numCols[1] || columns[1];
      const bSize = config?.sizeCol || numCols[2] || columns[2];
      const catCol = config?.categoryCol || columns.find(c => typeof rows[0]?.[c] === 'string');

      return {
        ...baseSpec,
        type: 'point',
        data: rows,
        encode: {
          x: bx,
          y: by,
          size: bSize,
          color: catCol || undefined,
        },
        scale: { color: { range: colors }, size: { range: [4, 20] } },
        style: { opacity: 0.7 },
        axis: {
          x: xAxis,
          y: yAxis,
        },
        legend: { color: { itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'calendar_heatmap': {
      const dateCol = config?.dateCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
      const valCol = config?.valCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];

      return {
        ...baseSpec,
        type: 'cell',
        data: rows.map(r => ({ date: String(r[dateCol]), value: Number(r[valCol]) || 0 })),
        encode: { x: 'date', y: 'value', color: 'value' },
        scale: { color: { range: colors } },
        style: { inset: 1 },
        labels: [{ text: 'value', style: { fill: textColor, fontSize: 10 } }],
        axis: {
          x: xAxis,
          y: yAxis,
        },
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'rose': {
      const roseData = rows.map(r => ({
        name: String(r[xCol]),
        value: Number(r[yCol]) || 0,
      }));
      return {
        ...baseSpec,
        type: 'interval',
        data: roseData,
        encode: { x: 'name', y: 'value', color: 'name' },
        coordinate: { type: 'polar' },
        scale: { color: { range: colors } },
        style: { stroke: bgColor, lineWidth: 1 },
        labels: [{ text: 'name', style: { fill: textColor, fontSize: 11 } }],
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'radial_bar': {
      return {
        ...baseSpec,
        type: 'interval',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined },
        coordinate: { type: 'polar', innerRadius: 0.5 },
        scale: { color: { range: colors } },
        style: { stroke: bgColor, lineWidth: 1 },
        axis: {
          x: xAxis,
          y: yAxis,
        },
        legend: { color: { itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'word_cloud': {
      const wordData = rows.map(r => ({
        text: String(r[xCol]),
        value: Number(r[yCol]) || 0,
      }));
      return {
        ...baseSpec,
        type: 'wordCloud',
        data: wordData,
        encode: { text: 'text', color: 'text' },
        scale: { color: { range: colors } },
        style: { fontSize: [12, 48] },
        layout: { spiral: 'rectangular' },
        interaction: { tooltip: {} },
      };
    }

    case 'china_map':
    case 'world_map': {
      // Map charts require special handling - return a placeholder spec
      // The actual rendering will be done asynchronously in the component
      const nameCol = config?.nameCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
      const valueCol = config?.valueCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];

      // Prepare data as a map for quick lookup
      const dataMap: Record<string, number> = {};
      rows.forEach(r => {
        const name = String(r[nameCol]);
        const value = Number(r[valueCol]) || 0;
        dataMap[name] = value;
      });

      return {
        ...baseSpec,
        type: 'geoView',
        _dataMap: dataMap,
        _nameCol: nameCol,
        _valueCol: valueCol,
        _textColor: textColor,
        _bgColor: bgColor,
        _colors: colors,
      };
    }

    case 'widget_decision': {
      // 看板「决策建议」控件：DOM 渲染、没有 G2 mark，但 `widget_style` 旋钮必须
      // 在这里被真实解析一次（字模/主题包/本图覆盖已在上游汇入 config.widgetStyle）。
      // 渲染面只消费这份结果、不再各自读 ws.*：两处读法必然漂移，“存了不生效”
      // 就从这里诞生（ui-vis-library-style.md §2）。
      const ws = config?.widgetStyle || {};
      return {
        type: 'html',
        _widget: 'decision',
        _chrome: {
          background: ws.backgroundColor || bgColor,
          color: ws.textColor || textColor,
          borderColor: ws.borderColor || borderColor,
          borderWidth: typeof ws.borderWidth === 'number' ? ws.borderWidth : 1,
          borderStyle: ws.borderStyle || 'solid',
          borderRadius: typeof ws.borderRadius === 'number' ? ws.borderRadius : 6,
          padding: ws.padding ?? '10px 12px',
          fontSize: typeof ws.fontSize === 'number' ? ws.fontSize : 13,
          height: typeof ws.height === 'number' && ws.height > 0 ? ws.height : undefined,
          opacity: ws.opacity,
          boxShadow: ws.boxShadow,
        },
      };
    }

    case 'table_value':
      // Rendered as HTML table in DashboardChartInner, not a G2 chart
      return { ...baseSpec, type: 'view', children: [] };

    default: {
      // Default to bar chart
      const spec: any = {
        ...baseSpec,
        type: 'interval',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined },
        // 兜底柱图与 bar 分支同口径（多系列分组 + 按系列取色），否则同一数据换图型就变堆叠同色
        ...(seriesField && config?.seriesLayout !== 'stack'
          ? { transform: [{ type: 'dodgeX' }] } : {}),
        axis: {
          x: xAxis,
          y: yAxis,
        },
        scale: { color: { range: colors } },
        style: barBodyStyle(config, colors, processedData, seriesField),
        labels: config?.showLabel ? [{ text: yCol, style: { fill: textColor } }] : [],
        interaction: { tooltip: { shared: true } },
      };
      return withBarCap(spec, config, processedData, xCol, yCol, plotHeightHint);
    }
  }
}

/** 控件按 paramKey 反查看板参数定义里的业务名（ui-resource-display.md：显示用 name，不拿内部键当主信息） */
function declaredParamLabel(dashboardId: number | undefined, key: string): string {
  if (!key) return '';
  const store = useDashboardStore.getState();
  const dash = store.dashboards?.find(d => d.id === (dashboardId ?? store.currentId));
  const hit = [...(dash?.params || []), ...(dash?.page_params || [])].find(p => p.name === key);
  return String(hit?.label || '').trim();
}

/**
 * 控件占位提示：没配 placeholder 时按“动作 + 业务名”给默认文案，不留一个无名的空框（2026-11-10 用户反馈）。
 * 文案与看板顶部参数带同源（`placeholderOfShape`）；日期类原生输入框不渲染 placeholder，调用方靠 label / title 兼容。
 */
function widgetPlaceholder(chartType: string, label: string, bound: boolean, optionCount = 1): string {
  if (!bound) return '未绑定看板参数，填了不会生效';
  if (chartType === 'widget_multi_select') return label ? `可多选 ${label}` : '可多选';
  // 没候选值的下拉 = 点开是空的，得说清去哪里补，不能只写“请选择”
  if (chartType === 'widget_select' && optionCount === 0) return '候选值未配置，请在属性面板的「候选值」里填写';
  return placeholderOfShape(shapeOfControl(chartType), label);
}

/**
 * 渲染失败的可读原因：优先 Error.message（图型分支抛的都是中文原因），兜底 String(e)。
 * 拿不到原因也要说一句事实，不能让人对着空白猜。
 */
function reasonOf(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e ?? '');
  return msg.trim() || '图表渲染失败（未给出原因）';
}

function DashboardChartInner({ chartType, data, config, style, loading, chartId, dashboardId, chartName, visual, draft = false, onDrill, motionAnimate }: Props) {
  // 依赖 theme(主题 id)而非 isDark: 同明暗主题切换(dark→tech)也需重建图表以读取新令牌色
  const theme = useThemeStore(s => s.theme);
  const isDark = visual?.mode ? visual.mode === 'dark' : IS_DARK[theme];
  const refreshing = useDashboardStore(s => chartId != null && s.refreshingChartIds.has(chartId));
  const isLoading = loading ?? refreshing;
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [mapData, setMapData] = useState<any>(null);
  const [mapError, setMapError] = useState('');
  // 渲染失败必须可见：过去 G2 报错只进 console，画布就留一片空白（桑基图整幅没图就是这么藏的）
  const [renderError, setRenderError] = useState('');
  const [modalState, setModalState] = useState<{ pageId: number; params: Record<string, any> } | null>(null);
  const [page, setPage] = useState(1);

  const cfg = config || {};
  const widgetParams = useDashboardStore(s => s.paramValues);
  // 决策建议控件取参数走**下发时的同一份合并口径**（`{ ...paramValues, ...pageParamValues }`，
  // 与 store 里刷新取数处同源），不在控件里另起一套取值逻辑。
  const pageParamValues = useDashboardStore(s => s.pageParamValues);
  const dashWorkspaceId = useDashboardStore(s => s.currentWorkspaceId);

  // Serialize data/config to detect actual content changes (not just reference changes)
  const dataKey = chartDataKey(data);
  // 字段显示格式在进渲染面**之前**归一一次：轴标签、tooltip、图例、表值图单元格、下钻条件
  // 读的都是同一批行值，逐图型分支挂 labelFormatter 必然漏掉某一面（与"字模挂了没反应"
  // 同一类缺陷）。只按 dataKey 重算，内容没变不重复扫行。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const view = useMemo(() => applyFieldFormats(data), [dataKey]);

  // Initialize date/date range default values before chart refresh
  useEffect(() => {
    if (draft || !cfg.paramKey) return;
    const store = useDashboardStore.getState();
    const current = store.paramValues[cfg.paramKey];

    // 时间区间：只有显式配了「默认选项」才预填。rangeMaxDays 是手选跨度上限，不是默认值
    // （历史缺陷：没配默认选项也按 rangeMaxDays/365 回填成“近一年”，用户以为数据只覆盖这段时间，
    //   而实际取数窗口由 SQL 自身口径决定 —— 控件显示与真实范围两回事，宁留空让人自己选）
    if (chartType === 'widget_daterange' && cfg.defaultPreset) {
      if (!current || (!current.start && !current.end)) {
        const window = resolvePreset(cfg.defaultPreset, new Date());
        if (!window) return; // 未知预设不回填，留空由用户自选
        // 本地时区格式化：原先用 toISOString() 取的是 UTC，在 UTC+8 晚间会算成前一天
        const granularity = effectiveGranularity(cfg.granularity, cfg.defaultPreset);
        store.setParamValue(cfg.paramKey, {
          start: formatTime(window.start, granularity), end: formatTime(window.end, granularity),
          preset: cfg.defaultPreset,
        });
      }
    } else if (chartType === 'widget_date' && cfg.defaultValue) {
      if (!current) {
        store.setParamValue(cfg.paramKey, cfg.defaultValue);
      }
    }
  }, [draft, chartType, cfg.paramKey, cfg.defaultPreset, cfg.granularity, cfg.defaultValue]); // eslint-disable-line react-hooks/exhaustive-deps

  // 真分页事实必须随首屏数据一起到。旧缓存（分页上线前写的 data_cache）没带 paging/total，
  // 拿它会“共 20 行”当成全量挂在几百万行的明细表上 —— 补一次首页取数。
  // 只在缓存确实陈旧时发（拿到 paging 后永不再发），不是每张图进看板都重查。
  useEffect(() => {
    if (!chartId || draft || chartType !== 'table_value') return;
    if (loading) return;
    // 三种"已知状态"都不必重查：按页取过（paging）、撞过线（truncated）、
    // 服务端声明本次即全量（unpaged，统计型数据集恒定走这条）。
    const bound = data.paging != null || data.truncated === true
      || data.unpaged === true || data.rows.length === 0;
    if (bound) return;
    useDashboardStore.getState().refreshSingleChart(chartId, {
      dashboardId, chartName, page_size: pageSizeOf(cfg), page_num: 1,
    });
  }, [chartId, dashboardId, draft]); // eslint-disable-line react-hooks/exhaustive-deps

  const configKey = JSON.stringify({ config: cfg, visual });
  // 预设切换（包括从默认档换成有动画的档）要让图表重建一次才能吃到新 animate；
  // 序列化后比较，避免引用变化把没动的图表全重建一遍
  const motionAnimateKey = JSON.stringify(motionAnimate ?? null);

  // Measure container dimensions
  useEffect(() => {
    const measure = () => {
      if (containerRef.current) {
        const { clientWidth, clientHeight } = containerRef.current;
        if (clientWidth > 0 && clientHeight > 0) {
          setDimensions(previous => previous.width === clientWidth && previous.height === clientHeight ? previous : { width: clientWidth, height: clientHeight });
        }
      }
    };

    measure();
    const timer = setTimeout(measure, 50);

    let observer: ResizeObserver | null = null;
    if (containerRef.current && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => measure());
      observer.observe(containerRef.current);
    }

    window.addEventListener('resize', measure);

    return () => {
      clearTimeout(timer);
      window.removeEventListener('resize', measure);
      if (observer) observer.disconnect();
    };
  }, [chartType, !!view.columns?.length, !!view.rows?.length, !!mapData]);

  // Fetch map data for map charts
  useEffect(() => {
    if (chartType !== 'china_map' && chartType !== 'world_map') {
      setMapData(null);
      setMapError('');
      return;
    }

    const mapType = chartType === 'china_map' ? 'china' : 'world';
    const url = MAP_URLS[mapType];

    setMapError('');
    fetch(url)
      .then(res => {
        if (!res.ok) throw new Error(`地图底图数据请求失败(HTTP ${res.status})`);
        return res.json();
      })
      .then(topoData => {
        const features = topojson.feature(topoData, topoData.objects.default || Object.values(topoData.objects)[0]);
        setMapData(features);
      })
      .catch(err => {
        // fail-loud:底图数据拉不到必须显式报错,不得静默卡在“加载中”
        console.error('Failed to load map data:', err);
        setMapError(String(err?.message || err));
      });
  }, [chartType]);

  // Build and render chart
  useEffect(() => {
    if (!containerRef.current || dimensions.width === 0 || dimensions.height === 0) return;
    if (!view.columns?.length || !view.rows?.length) return;

    // For map charts, wait for map data
    if ((chartType === 'china_map' || chartType === 'world_map') && !mapData) return;

    // 布局组件在父级 useEffect 中应用主题类；下一帧再取 CSS 变量，避免读到旧主题。
    const frame = requestAnimationFrame(() => {
      if (!containerRef.current) return;
      // Destroy previous chart
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }

      try {
        // 动效只在显式给了预设时写进 spec（undefined 时原样返回），保证默认档零变化
        const spec = applyG2Animate(buildG2Spec(
          chartType, view.columns, view.rows, cfg, isDark,
          dimensions.width, dimensions.height, visual
        ), motionAnimate);
        setRenderError('');

        // Handle map charts
        if (chartType === 'china_map' || chartType === 'world_map') {
          const nameCol = cfg?.nameCol || view.columns.find(c => typeof view.rows[0]?.[c] === 'string') || view.columns[0];
          const valueCol = cfg?.valueCol || view.columns.find(c => typeof view.rows[0]?.[c] === 'number') || view.columns[1];

          // Prepare data as a map for quick lookup
          const dataMap: Record<string, number> = {};
          view.rows.forEach(r => {
            const name = String(r[nameCol]);
            const value = Number(r[valueCol]) || 0;
            dataMap[name] = value;
          });

          // Merge data into map features
          const featuresWithValues = mapData.features.map((f: any) => ({
            ...f,
            properties: {
              ...f.properties,
              value: dataMap[f.properties.name] || 0,
            },
          }));

          const chart = new Chart({
            container: containerRef.current,
            width: dimensions.width,
            height: dimensions.height,
            autoFit: false,
          });

          chart.options({
            type: 'geoView',
            theme: spec.theme,
            children: [
              {
                type: 'geoPath',
                data: { type: 'FeatureCollection', features: featuresWithValues },
                encode: {
                  color: 'value',
                },
                scale: {
                  color: {
                    range: cfg.colorScheme?.length ? cfg.colorScheme : visual?.palette?.length ? visual.palette : readChartPalette(isDark),
                    unknown: visual?.viewBg || readToken('--muted', '#f0f0f0'),
                  },
                },
                style: {
                  stroke: visual?.viewBg || readToken('--card', '#fff'),
                  lineWidth: 0.5,
                },
                tooltip: {
                  title: 'name',
                  items: [{ field: 'value', name: '数值' }],
                },
                // geoView 的 animate 是 mark 级的（落在 geoPath 子节点上），根层写了不吃；
                // 不给它另开一套「地图不带动效」的例外，否则预设在这两类图上就是静默失效
                ...(motionAnimate !== undefined ? { animate: motionAnimate } : {}),
              },
            ],
          });

          const renderedMap = chart.render() as any;
          if (renderedMap && typeof renderedMap.catch === 'function') renderedMap.catch((e: any) => setRenderError(reasonOf(e)));
          chartRef.current = chart;
        } else {
          const chart = new Chart({
            container: containerRef.current,
            width: dimensions.width,
            height: dimensions.height,
            autoFit: false,
          });

          chart.options(spec);
          // G2 v5 的 render 是异步的：布局/转换报错走不到下面的 catch，不接住就是静默空白。
          // 拿不到 thenable（旧版本/测试替身返回 undefined）时不能假设有 .catch
          const rendered = chart.render() as any;
          if (rendered && typeof rendered.catch === 'function') rendered.catch((e: any) => setRenderError(reasonOf(e)));
          // 图上下钻:点击图形元素回传该行数据(G2 v5 element:click,datum 在 event.data.data)
          if (onDrill) {
            chart.on('element:click', (e: any) => {
              const datum = e?.data?.data;
              if (datum && typeof datum === 'object') onDrill(datum);
            });
          }
          chartRef.current = chart;
        }
      } catch (e) {
        console.error('Chart render error:', e);
        setRenderError(reasonOf(e));
      }
    });

    return () => {
      cancelAnimationFrame(frame);
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }
    };
  }, [chartType, dataKey, configKey, dimensions.width, dimensions.height, mapData, theme, motionAnimateKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // 时间动画图表(竞速条形图/时序播放) — 独立播放器组件,G2 实例常驻 + changeData 过渡动画
  const ANIMATED_TYPES = ['bar_race', 'timeline_play'];
  if (ANIMATED_TYPES.includes(chartType)) {
    return <AnimatedTimeChart chartType={chartType as 'bar_race' | 'timeline_play'} data={data} config={cfg} style={style} />;
  }

  // 决策建议控件（widget_decision）：不是表单控件也不画 G2，只展示服务端引擎算好的
  // 结论（计划 §四出口 4）。样式由 buildG2Spec 的同名分支解析一次，这里只消费结果；
  // 看板筛选只把参数原值 + 绑定清单交给服务端（翻译唯一在 semantics.param_plan）。
  if (chartType === 'widget_decision') {
    const spec: any = buildG2Spec(chartType, [], [], cfg, isDark,
      dimensions.width || 360, dimensions.height || 220, visual);
    return (
      <DecisionWidget
        chrome={spec?._chrome || {}}
        treeKey={String(cfg.treeKey || '')}
        paramBindings={Array.isArray(cfg.paramBindings) ? cfg.paramBindings : []}
        params={{ ...widgetParams, ...pageParamValues }}
        workspaceId={Number(cfg.workspaceId ?? dashWorkspaceId ?? 0)}
        draft={draft}
      />
    );
  }

  // Widget rendering — compact form controls, not G2 charts (buttons handled separately below)
  const WIDGET_TYPES = ['widget_label', 'widget_text', 'widget_number', 'widget_date', 'widget_daterange', 'widget_select', 'widget_multi_select'];
  if (WIDGET_TYPES.includes(chartType)) {
    const paramKey = cfg?.paramKey || '';
    const bound = !!paramKey;
    /*
     * 业务名优先级：本图显式 label > 看板参数定义里的 label > 参数键本身。
     * 没绑参数的控件不假装能用（否则是个“填了什么都不发生”的空框）：
     * 能用 placeholder 说清的（文本/数字/单选下拉）不改动布局；
     * 原生日期框与多选框不渲染 placeholder，才补一个可见 label（静态文本标签无绑定概念，不参与）。
     */
    const isInputWidget = chartType !== 'widget_label';
    const needsUnboundLabel = !bound && isInputWidget
      && ['widget_date', 'widget_daterange', 'widget_multi_select'].includes(chartType);
    const label = (cfg?.label || '').trim() || declaredParamLabel(dashboardId, paramKey) || paramKey
      || (needsUnboundLabel ? '未绑定参数' : '');
    const placeholder = cfg?.placeholder || (isInputWidget
      ? widgetPlaceholder(chartType, label, bound, ((cfg?.options || []) as unknown[]).length) : '');
    const defaultValue = cfg?.defaultValue ?? '';
    const labelPosition = cfg?.labelPosition || 'left';
    const showLabel = cfg?.showLabel !== false;
    const options: { label: string; value: string }[] = cfg?.options || [];
    const ws = cfg?.widgetStyle || {};

    const paramValues = widgetParams;
    const setParamValue = useDashboardStore.getState().setParamValue;
    const currentValue = paramValues[paramKey] ?? defaultValue;

    const handleChange = (val: any) => {
      // 编辑态的拦截由卡片层 inert 承担（见 DashboardChartCard），此处不再把用户输入静默丢弃
      if (paramKey) setParamValue(paramKey, val);
      // Bind filter value to page param(s) if bind_param is configured
      const bindParam = cfg?.bind_param;
      if (bindParam && typeof bindParam === 'string' && bindParam.trim()) {
        const setPageParamValue = useDashboardStore.getState().setPageParamValue;
        if (chartType === 'widget_daterange' && val && typeof val === 'object') {
          // dateRange: bind_param is comma-separated, e.g. "date_start,date_end"
          const parts = bindParam.split(',').map((s: string) => s.trim()).filter(Boolean);
          if (parts.length >= 1) setPageParamValue(parts[0], val.start || '');
          if (parts.length >= 2) setPageParamValue(parts[1], val.end || '');
        } else {
          // select, multi_select, text, number: single param name
          setPageParamValue(bindParam.trim(), val ?? '');
        }
      }
    };

    const isHorizontal = labelPosition === 'left' && showLabel;
    const hasLabel = showLabel && label && chartType !== 'widget_label';
    // 光标按控件语义分派：下拉开 pointer、标签无操作，不再一律 text
    const cursor: React.CSSProperties['cursor'] = chartType === 'widget_label' ? 'default'
      : chartType === 'widget_select' || chartType === 'widget_multi_select' ? 'pointer' : 'text';

    // 「控件高」旋钮必须真实消费（否则字模/面板存了不生效）：
    // 无标签时控件就是卡片本身，inset:0 + 固定高 + margin:auto 0 居中；有标签时高落在内层表单元素上。
    const controlHeight = typeof ws.height === 'number' && ws.height > 0 ? ws.height : undefined;

    // Merged style: container + input, applied directly to the element (no wrapper div)
    const directStyle: React.CSSProperties = {
      display: 'flex',
      flexDirection: isHorizontal ? 'row' : 'column',
      alignItems: isHorizontal ? 'center' : 'stretch',
      justifyContent: 'center',
      // 允许换行 + 可收缩：居中布局下内容超宽会向两侧溢出（卡片两端文字被同时切掉），
      // 所以必须先让它收缩、再换行，而不是靠外层裁切。
      flexWrap: 'wrap', minWidth: 0,
      gap: ws.gap ?? (hasLabel ? 6 : 4),
      position: 'absolute', inset: 0,
      padding: ws.padding ?? (hasLabel ? '4px 8px' : '4px'),
      fontSize: ws.fontSize ?? 14,
      lineHeight: ws.lineHeight,
      fontWeight: ws.fontWeight,
      color: ws.textColor || 'inherit',
      boxSizing: 'border-box',
      borderRadius: ws.borderRadius ?? 6,
      border: `${ws.borderWidth ?? 1}px ${ws.borderStyle || 'solid'} ${ws.borderColor || (isDark ? '#475569' : '#cbd5e1')}`,
      background: ws.backgroundColor || visual?.viewBg || (isDark ? '#0f172a' : '#ffffff'),
      colorScheme: isDark ? 'dark' : 'light',
      outline: 'none',
      cursor,
      ...(controlHeight && !hasLabel ? { height: controlHeight, margin: 'auto 0' } : {}),
    };
    // 内层表单元素与外层共用一份样式源：圆角/内边距/行高/控件高必须跟随 ws.*，否则字模与覆盖「存了不生效」
    const innerFieldStyle: React.CSSProperties = {
      flex: '1 1 auto', minHeight: 0, minWidth: 0, width: '100%',
      fontSize: 'inherit', lineHeight: 'inherit', fontWeight: 'inherit',
      fontFamily: 'inherit',
      borderRadius: ws.borderRadius ?? 6,
      padding: ws.padding ?? '4px 8px',
      border: 'none', background: 'transparent', outline: 'none', color: 'inherit',
      cursor: 'inherit', boxSizing: 'border-box',
      ...(controlHeight ? { height: controlHeight, maxHeight: '100%' } : {}),
    };

    // 需要自己画外框的控件（日期/多选）：有标签时主题色必须落在内层元素上，
    // 否则 label 模式下背景/文字色回到 transparent，字模与主题包挂了没效果
    const fieldChrome: React.CSSProperties = {
      borderRadius: ws.borderRadius ?? 6,
      border: `1px solid ${isDark ? 'rgba(255,255,255,0.15)' : 'hsl(var(--input))'}`,
      background: ws.backgroundColor || visual?.viewBg || (isDark ? '#1e293b' : '#ffffff'),
      padding: ws.padding ?? '4px 8px',
      color: ws.textColor || visual?.textColor || (isDark ? '#e2e8f0' : '#111827'),
      colorScheme: isDark ? 'dark' : 'light',
    };

    const labelEl = hasLabel ? (
      <Label style={{
        fontSize: 'inherit', fontWeight: 500, color: ws.textColor || visual?.subTextColor || (isDark ? '#cbd5e1' : '#475569'),
        whiteSpace: 'nowrap', flexShrink: 0, maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis',
        ...(ws.labelWidth ? { width: ws.labelWidth, flexBasis: ws.labelWidth, textAlign: ws.horizontalAlign === 'flex-start' ? 'left' : ws.horizontalAlign === 'flex-end' ? 'right' : 'left' } : {}),
      }}>
        {label}
      </Label>
    ) : null;

    // widget_label — static text, direct <span>
    if (chartType === 'widget_label') {
      return (
        <div style={{
          display: 'flex', alignItems: 'center',
          justifyContent: ws.horizontalAlign || 'center',
          position: 'absolute', inset: 0,
          overflow: 'hidden',
          padding: ws.padding ?? '4px 8px',
          fontSize: ws.fontSize ?? 14,
          fontWeight: ws.fontWeight || 500,
          color: ws.textColor || visual?.textColor || (isDark ? '#e2e8f0' : '#111827'),
          lineHeight: ws.lineHeight ?? 1.4, wordBreak: 'break-word',
          boxSizing: 'border-box',
        }}>
          {cfg?.content || cfg?.label || label || '文本标签'}
        </div>
      );
    }

    // widget_daterange — two inputs + separator, needs minimal wrapper
    if (chartType === 'widget_daterange') {
      const rangeMaxDays = cfg?.rangeMaxDays || 365; // default 1 year
      const rangeHint = placeholder || undefined;
      const startDate = typeof currentValue?.start === 'string' ? currentValue.start : '';
      const endDate = typeof currentValue?.end === 'string' ? currentValue.end : '';
      const presets = enabledPresets(cfg?.presets);
      // 当前预设从值里读（刷新后仍在）；没带 preset 字段的历史值归为自由选择
      const selectedPreset: string = currentValue?.preset || CUSTOM_PRESET;
      const granularity: TimeGranularity = effectiveGranularity(cfg?.granularity, selectedPreset);
      // 自算的 min/max 用本地时区字符串，不再走 toISOString()（UTC 会偏一天）
      const endMax = startDate ? maxEndFor(startDate, rangeMaxDays) : '';
      const inputValue = (v: string) => toInputValue(v, granularity);
      // 边界值必须转成输入框自己的格式（datetime-local 用 T 分隔），否则浏览器比较会失真
      const startBoundary = inputValue(startDate);
      const endBoundary = inputValue(endDate);
      const endMaxBoundary = endMax ? inputValue(endMax) : '';
      const commit = (patch: Record<string, unknown>) => handleChange({ ...currentValue, ...patch });

      // 一个控件，而不是三个盒子：卡片只做排版（透明、无描边、无内边距），
      // 描边/底色/圆角/内边距/控件高全部落在字段组上 —— 字模 ws_daterange_* 的旋钮由此作用在
      // “看得见的那一层”，而不是三个小框各描一遍边（ui-vis-library-style §2）。
      const shellStyle: React.CSSProperties = {
        ...directStyle, border: 'none', background: 'transparent', padding: 0,
        gap: ws.gap ?? (hasLabel ? 6 : 0),
      };
      const groupStyle = {
        display: 'flex', alignItems: 'center', alignContent: 'center', flexWrap: 'wrap',
        flex: '1 1 12rem', minWidth: 0, maxWidth: '100%', boxSizing: 'border-box',
        // 不给 input 写高度：换行后每行占满容器会垂直溢出（见交互回归用例）
        minHeight: controlHeight ?? 30,
        ...(controlHeight ? { height: controlHeight } : {}),
        borderRadius: ws.borderRadius ?? 6,
        borderWidth: ws.borderWidth ?? 1, borderStyle: 'solid',
        // 边框色走 CSS 变量：focus 环要能覆盖它（inline 颜色 class 压不住）
        '--dh-dr-border': ws.borderColor || (isDark ? 'rgba(255,255,255,0.15)' : 'hsl(var(--input))'),
        background: ws.backgroundColor || visual?.viewBg || (isDark ? '#1e293b' : '#ffffff'),
        color: ws.textColor || visual?.textColor || (isDark ? '#e2e8f0' : '#111827'),
        padding: ws.padding ?? '0 8px',
        gap: ws.gap ?? 4, rowGap: 2,
        fontSize: 'inherit', fontFamily: 'inherit', lineHeight: 'inherit', fontWeight: 'inherit',
      } as React.CSSProperties;
      // 字段自身不再描边（组给）；文字色靠继承，input 上**不写 inline color** ——
      // 否则 globals.css 的「空值时分段透明」压不过 inline，那行「年/月/日」噪音还在
      // 字段是 flex 项（min-width:0 + 裁剪）：原生日期框的内在最小宽压不下去时，
      // 裁掉的是日期文字，而不是把快捷项挤到第二行把控件撑成两截
      const fieldStyle: React.CSSProperties = {
        position: 'relative', display: 'flex', alignItems: 'center',
        flex: '1 1 6.5rem', minWidth: 0, maxWidth: '100%', overflow: 'hidden',
      };
      const inputStyle: React.CSSProperties = {
        width: '100%', minWidth: 0,
        borderWidth: 0, borderStyle: 'none', background: 'transparent', outline: 'none', padding: 0,
        fontSize: 'inherit', fontFamily: 'inherit', lineHeight: 'inherit', fontWeight: 'inherit',
        fontVariantNumeric: 'tabular-nums',
      };
      const divider = isDark ? 'rgba(255,255,255,0.14)' : 'hsl(var(--border))';
      // 快捷项排在两个端点之后，只留一条分隔线（不再是第三个独立白框）；
      // 宽度不够时换行被挤掉的是快捷项，而不是结束日期
      const presetStyle: React.CSSProperties = {
        flex: '0 1 auto', minWidth: 0, maxWidth: '9.5rem',
        borderWidth: 0, borderLeftStyle: 'solid', borderLeftWidth: 1, borderLeftColor: divider,
        borderRadius: 0,
        background: 'transparent', outline: 'none', color: 'inherit', cursor: 'pointer',
        fontSize: 'inherit', fontFamily: 'inherit', lineHeight: 'inherit', fontWeight: 'inherit',
        paddingLeft: 8, paddingRight: 18,
        whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden',
      };
      const hints = granularity === 'datetime' ? ['开始时间', '结束时间'] : ['开始日期', '结束日期'];

      return (
        <div onClick={e => e.stopPropagation()} style={shellStyle}>
          {labelEl}
          <div className="dh-daterange-group" style={groupStyle}>
            <span className={`dh-daterange-field${startBoundary ? '' : ' is-empty'}`} style={fieldStyle}>
              <input type={granularity === 'datetime' ? 'datetime-local' : 'date'}
                aria-label={`${label || '时间区间'}开始`} title={rangeHint}
                value={startBoundary} max={endBoundary || undefined}
                onChange={e => commit({ preset: CUSTOM_PRESET, start: fromInputValue(e.target.value, granularity) })}
                style={inputStyle} />
              {!startBoundary && <span className="dh-daterange-hint">{hints[0]}</span>}
            </span>
            <span className="dh-daterange-sep" style={{
              flexShrink: 0, fontSize: '0.85em',
              color: isDark ? 'rgba(226,232,240,0.5)' : 'hsl(var(--muted-foreground))',
            }}>~</span>
            <span className={`dh-daterange-field${endBoundary ? '' : ' is-empty'}`} style={fieldStyle}>
              <input type={granularity === 'datetime' ? 'datetime-local' : 'date'}
                aria-label={`${label || '时间区间'}结束`}
                value={endBoundary} min={startBoundary || undefined} max={endMaxBoundary || undefined}
                title={endMaxBoundary ? `可选区间最长 ${rangeMaxDays} 天` : undefined}
                onChange={e => commit({ preset: CUSTOM_PRESET, end: fromInputValue(e.target.value, granularity) })}
                style={inputStyle} />
              {!endBoundary && <span className="dh-daterange-hint">{hints[1]}</span>}
            </span>
            {presets.length > 0 && <select className="dh-daterange-preset"
              aria-label={`${label || '时间区间'}快捷选项`}
              value={presets.some(p => p.key === selectedPreset) ? selectedPreset : CUSTOM_PRESET}
              onChange={e => {
                const key = e.target.value;
                if (key === CUSTOM_PRESET) { commit({ preset: CUSTOM_PRESET }); return; }
                const window = resolvePreset(key);
                if (!window) { commit({ preset: CUSTOM_PRESET }); return; }
                const next: TimeGranularity = effectiveGranularity(cfg?.granularity, key);
                commit({ preset: key, start: formatTime(window.start, next), end: formatTime(window.end, next) });
              }}
              style={presetStyle}>
              {presets.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
              <option value={CUSTOM_PRESET}>自由选择</option>
            </select>}
          </div>
        </div>
      );
    }

    // Single-element widgets — no wrapper div, label is inline
    const commonProps = {
      onClick: (e: React.MouseEvent) => e.stopPropagation(),
      onMouseDown: (e: React.MouseEvent) => e.stopPropagation(),
    };

    if (chartType === 'widget_text') {
      if (!hasLabel) return <input type="text" {...commonProps} value={currentValue || ''} onChange={e => handleChange(e.target.value)} placeholder={placeholder} style={directStyle} />;
      return <div {...commonProps} style={directStyle}>{labelEl}<input type="text" value={currentValue || ''} onChange={e => handleChange(e.target.value)} placeholder={placeholder} style={innerFieldStyle} /></div>;
    }

    if (chartType === 'widget_number') {
      if (!hasLabel) return <input type="number" {...commonProps} value={currentValue ?? ''} onChange={e => handleChange(e.target.value)} placeholder={placeholder} min={cfg?.min} max={cfg?.max} step={cfg?.step || 1} style={directStyle} />;
      return <div {...commonProps} style={directStyle}>{labelEl}<input type="number" value={currentValue ?? ''} onChange={e => handleChange(e.target.value)} placeholder={placeholder} min={cfg?.min} max={cfg?.max} step={cfg?.step || 1} style={innerFieldStyle} /></div>;
    }

    if (chartType === 'widget_date') {
      const dateProps = { onClick: (e: React.MouseEvent) => e.stopPropagation(), title: placeholder || undefined };
      const dateInnerStyle: React.CSSProperties = { ...innerFieldStyle, ...fieldChrome };
      if (!hasLabel) return <input type="date" {...dateProps} value={currentValue || ''} onChange={e => handleChange(e.target.value)} style={directStyle} />;
      return <div {...dateProps} style={directStyle}>{labelEl}<input type="date" value={currentValue || ''} onChange={e => handleChange(e.target.value)} style={dateInnerStyle} /></div>;
    }

    if (chartType === 'widget_select') {
      if (!hasLabel) return <select {...commonProps} value={currentValue || ''} onChange={e => handleChange(e.target.value || undefined)} style={{ ...directStyle, cursor: 'pointer' }}>
        <option value="">{placeholder || '请选择'}</option>
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select>;
      return <div {...commonProps} style={directStyle}>{labelEl}<select value={currentValue || ''} onChange={e => handleChange(e.target.value || undefined)} style={{ ...innerFieldStyle, cursor: 'pointer' }}>
        <option value="">{placeholder || '请选择'}</option>
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select></div>;
    }

    // 多选框高度不再写死 60：由「控件高」旋钮接管，使小尺寸控件可以真正缩小
    if (chartType === 'widget_multi_select') {
      if (!hasLabel) return <select multiple {...commonProps} value={Array.isArray(currentValue) ? currentValue : []} onChange={e => handleChange(Array.from(e.target.selectedOptions, o => o.value))} title={placeholder || undefined} style={{ ...directStyle, cursor: 'pointer', minHeight: ws.height ?? 60 }}>
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select>;
      return <div {...commonProps} style={directStyle}>{labelEl}<select multiple value={Array.isArray(currentValue) ? currentValue : []} onChange={e => handleChange(Array.from(e.target.selectedOptions, o => o.value))} title={placeholder || undefined} style={{ ...innerFieldStyle, ...fieldChrome, cursor: 'pointer', minHeight: ws.height ?? 60 }}>
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select></div>;
    }
  }

  // Button widgets — search, reset, export
  const BUTTON_WIDGET_TYPES = ['widget_search', 'widget_reset', 'widget_export'];
  if (BUTTON_WIDGET_TYPES.includes(chartType)) {
    const label = cfg?.label || (chartType === 'widget_search' ? '搜索' : chartType === 'widget_reset' ? '重置' : '导出');
    // 导出必须配显式目标图表：不再静默回退“第一个非控件图表”（导出错数据比不导出危害更大）
    const targetChartId = typeof cfg?.targetChartId === 'number' && cfg.targetChartId > 0 ? cfg.targetChartId : 0;
    const exportNoTarget = chartType === 'widget_export' && !targetChartId;

    const handleClick = (e: React.MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      // 编辑态的拦截由卡片层 inert 承担（见 DashboardChartCard），试运行时必须真实响应
      if (chartType === 'widget_search') {
        // 查询按钮必须真实重查询：按所属看板 id 逐图 POST（缺上下文时 store 会显式报错）
        useDashboardStore.getState().refreshCharts(dashboardId);
      } else if (chartType === 'widget_reset') {
        // 与顶栏「重置」共用 store 的一份口径：两个命名空间一起恢复默认值后重查。
        // 旧实现只清 paramValues，而下发时 `{...paramValues, ...pageParamValues}` 会把
        // 控件 bind_param 写的同名键覆盖回来 → 按了“重置”取数仍按旧区间（空承诺）。
        void useDashboardStore.getState().resetParams(dashboardId);
      } else if (chartType === 'widget_export') {
        if (exportNoTarget) { toast.error('请先在属性面板选择要导出的图表'); return; }
        const store = useDashboardStore.getState();
        const dashboard = store.dashboards.find(d => d.id === store.currentId);
        const targetChart = dashboard?.charts.find(c => c.id === targetChartId);
        if (!targetChart) { toast.error(`要导出的图表（id ${targetChartId}）已不在当前看板，请重新选择`); return; }
        if (!targetChart.data_cache) { toast.error(`「${targetChart.name}」暂无数据，请先刷新后重试`); return; }
        try {
          const { columns, rows } = JSON.parse(targetChart.data_cache);
          const csv = [columns.join(','), ...rows.map((r: any) => columns.map((c: string) => String(r[c] ?? '')).join(','))].join('\n');
          const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url; a.download = `${targetChart.name || 'export'}.csv`; a.click();
          URL.revokeObjectURL(url);
        } catch (error) {
          toast.error(`导出失败：${error instanceof Error ? error.message : '数据格式无法解析'}`);
        }
      }
    };

    const isPrimary = chartType === 'widget_search';
    const ws = cfg?.widgetStyle || {};
    const btnTextColor = ws.textColor || (isPrimary ? 'hsl(var(--primary-foreground))' : 'inherit');
    const btnBgColor = ws.backgroundColor || (isPrimary ? 'hsl(var(--primary))' : 'hsl(var(--background))');
    const btnBorderColor = ws.borderColor || 'hsl(var(--input))';

    return (
      <button
        type="button"
        onClick={handleClick}
        disabled={exportNoTarget}
        title={exportNoTarget ? '请先在属性面板选择要导出的图表' : undefined}
        onMouseDown={e => { e.stopPropagation(); if (!exportNoTarget) (e.currentTarget as HTMLElement).style.transform = 'scale(0.95)'; }}
        onMouseUp={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)'; }}
        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)'; }}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          position: 'absolute', inset: 0,
          fontSize: 'inherit',
          lineHeight: ws.lineHeight,
          borderRadius: ws.borderRadius ?? 6,
          fontWeight: ws.fontWeight,
          border: `${ws.borderWidth ?? 1}px ${ws.borderStyle || 'solid'} ${btnBorderColor}`,
          background: btnBgColor,
          color: btnTextColor,
          padding: ws.padding ?? '4px 8px',
          gap: ws.gap ?? 6,
          cursor: 'pointer',
          transition: 'transform 0.15s ease',
          boxSizing: 'border-box',
          // 按钮同样吃「控件高」旋钮：高给定时在卡片内居中，不拉满整张卡
          ...(ws.height > 0 ? { height: ws.height, margin: 'auto 0' } : {}),
          ...(exportNoTarget ? { opacity: 0.5, cursor: 'not-allowed' } : {}),
        }}
      >
        {chartType === 'widget_search' && <Search style={{ width: '1.2em', height: '1.2em' }} />}
        {chartType === 'widget_reset' && <RotateCcw style={{ width: '1.2em', height: '1.2em' }} />}
        {chartType === 'widget_export' && <Download style={{ width: '1.2em', height: '1.2em' }} />}
        {label}
      </button>
    );
  }

  if (!view.columns?.length || !view.rows?.length) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        position: 'absolute', inset: 0, color: '#999',
      }}>
        暂无数据
      </div>
    );
  }

  // table_value: HTML 表格 + 真分页（服务端按 page_size/page_num 取数，前端不再切片全量）
  if (chartType === 'table_value') {
    const showIndex = cfg?.showIndex !== false;
    const striped = cfg?.striped !== false;
    const paging = data.paging;
    // 页大小以服务端回显为准（它可能把越界请求收到 100），前端只看配置算会错位
    const pageSize = paging?.page_size || pageSizeOf(cfg);
    const serverPaged = !!paging;
    const allRows = view.rows;

    // Drill-through link config
    const links: Array<{
      column: string;
      target_page_id: number;
      open_mode: 'new_page' | 'modal' | 'same_page';
      param_mapping: Record<string, string>;
    }> = cfg?.links || [];

    const handleCellClick = (col: string, row: any) => {
      const linkDef = links.find(l => l.column === col);
      if (!linkDef || draft) return;

      // Build params from param_mapping: targetParamName -> sourceColumnValue
      const mappedParams: Record<string, any> = {};
      for (const [targetParam, sourceCol] of Object.entries(linkDef.param_mapping || {})) {
        mappedParams[targetParam] = row[sourceCol];
      }

      if (linkDef.open_mode === 'new_page') {
        const qs = new URLSearchParams(mappedParams).toString();
        window.open(`/page/${linkDef.target_page_id}${qs ? '?' + qs : ''}`, '_blank');
      } else if (linkDef.open_mode === 'modal') {
        setModalState({ pageId: linkDef.target_page_id, params: mappedParams });
      } else if (linkDef.open_mode === 'same_page') {
        const setPageParamValue = useDashboardStore.getState().setPageParamValue;
        for (const [targetParam, value] of Object.entries(mappedParams)) {
          setPageParamValue(targetParam, value);
        }
      }
    };

    // 分页口径两分：
    // · 服务端分页（数据集声明了支持分页）—— rows 就是本次那一页，总数来自服务端；
    // · 无分页事实（统计展示型数据集整页返回 / 设计预览 / 对话结果卡）—— 本地切片，
    //   此时“共 N 行”说的是本次查询返回的全部行，不是“拉了多少算多少”。
    const totalRows = serverPaged ? (paging.total ?? allRows.length) : allRows.length;
    // 服务端真分页时分页条恒显示：拿 enablePagination 关掉它，用户就只看得到首页
    // 且不知道后面还有多少页 —— 与“共 20 行”冒充全量是同一类空承诺
    const enablePagination = serverPaged
      || (cfg?.enablePagination !== false && allRows.length > pageSize);
    const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
    // 当前页以服务端回显为准：改参数后整板重查会回到首页，只跟本地 state 会显错页
    const currentPage = Math.min(Math.max(1, serverPaged ? (paging.page_num || 1) : page), totalPages);
    const rows = serverPaged
      ? allRows
      : (enablePagination ? allRows.slice((currentPage - 1) * pageSize, currentPage * pageSize) : allRows);
    const startIndex = enablePagination ? (currentPage - 1) * pageSize : 0;
    // ES 游标式分页（集群无 PIT）只能从上一页往后走：随机跳页会被服务端拒，按钮就不给
    const canJump = serverPaged ? paging.can_jump !== false : true;

    const goPage = (next: number) => {
      const target = Math.min(Math.max(1, next), totalPages);
      if (target === currentPage) return;
      setPage(target);
      if (serverPaged && chartId) {
        useDashboardStore.getState().refreshSingleChart(chartId, {
          dashboardId, chartName, page_size: pageSize, page_num: target,
        });
      }
    };

    // Generate page numbers to display（仅可跳页时给页码；游标式分页只给顺序按钮）
    const pageNumbers: (number | '...')[] = [];
    if (canJump) {
      if (totalPages <= 7) {
        for (let i = 1; i <= totalPages; i++) pageNumbers.push(i);
      } else {
        pageNumbers.push(1);
        if (currentPage > 3) pageNumbers.push('...');
        for (let i = Math.max(2, currentPage - 1); i <= Math.min(totalPages - 1, currentPage + 1); i++) {
          pageNumbers.push(i);
        }
        if (currentPage < totalPages - 2) pageNumbers.push('...');
        pageNumbers.push(totalPages);
      }
    }

    const btnStyle = (active = false, disabled = false): React.CSSProperties => ({
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      minWidth: 28, height: 26, fontSize: 11, borderRadius: 4,
      border: '1px solid hsl(var(--border))',
      background: active ? 'hsl(var(--primary))' : 'hsl(var(--background))',
      color: active ? 'hsl(var(--primary-foreground))' : disabled ? 'hsl(var(--muted-foreground) / 0.5)' : 'inherit',
      cursor: disabled ? 'default' : 'pointer',
      padding: '0 6px',
      opacity: disabled ? 0.5 : 1,
    });

    const showRefreshBtn = chartId != null;
    const tableText = visual?.textColor || (isDark ? '#e2e8f0' : '#111827');
    const tableMuted = visual?.subTextColor || (isDark ? '#94a3b8' : '#475569');
    const tableBorder = visual?.gridColor || (isDark ? '#334155' : '#e2e8f0');
    // 表值图字模旋钮必须在这里真实消费：以前表头色/行高/字号全写死，
    // 挂了 chart_style 字模也没反应（ui-vis-library-style.md §2“存了不生效”即缺陷）。
    const tableHeaderBg = cfg?.headerBg || cfg?.titleBarBg;
    const tableHeaderColor = cfg?.headerColor || tableMuted;
    const tableZebraBg = cfg?.zebraBg || (isDark ? '#ffffff0a' : '#00000005');
    const tableFontSize = Number(cfg?.tableFontSize) || 12;
    const tableRowHeight = Number(cfg?.rowHeight) || 0;

    return (
      <div className="chart-cell" style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', position: 'relative', color: tableText, ...style }}>
        {isLoading && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 10,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: isDark ? 'rgba(0,0,0,0.4)' : 'rgba(255,255,255,0.6)',
            borderRadius: 'inherit', backdropFilter: 'blur(2px)',
          }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" style={{ animation: 'spin 1s linear infinite' }}>
              <circle cx="12" cy="12" r="10" stroke={isDark ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.1)'} strokeWidth="3" />
              <path d="M12 2a10 10 0 0 1 10 10" stroke="hsl(var(--primary))" strokeWidth="3" strokeLinecap="round" />
            </svg>
          </div>
        )}
        {showRefreshBtn && (
          <button
            className="chart-refresh-btn"
            onClick={(e) => {
              e.stopPropagation();
              useDashboardStore.getState().refreshSingleChart(chartId!, { dashboardId, chartName });
            }}
            title="刷新此图表"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={isLoading ? { animation: 'spin 1s linear infinite' } : undefined}>
              <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
              <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
              <path d="M16 16h5v5" />
            </svg>
          </button>
        )}
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          <table style={{
            width: '100%', borderCollapse: 'collapse',
            fontSize: tableFontSize, lineHeight: 1.5,
          }}>
            <thead>
              <tr style={{ borderBottom: `2px solid ${tableBorder}`, position: 'sticky', top: 0, background: tableHeaderBg || visual?.viewBg || (isDark ? '#0f172a' : '#ffffff'), zIndex: 1, height: tableRowHeight || undefined }}>
                {showIndex && <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 500, color: tableHeaderColor, width: 40 }}>#</th>}
                {view.columns.map(col => (
                  <th key={col} style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 500, color: tableHeaderColor, whiteSpace: 'nowrap' }}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} style={{
                  borderBottom: `1px solid ${tableBorder}`,
                  height: tableRowHeight || undefined,
                  background: striped && i % 2 === 1 ? tableZebraBg : 'transparent',
                }}>
                  {showIndex && <td style={{ padding: '4px 8px', color: 'hsl(var(--muted-foreground))', fontSize: 11 }}>{startIndex + i + 1}</td>}
                  {view.columns.map(col => {
                    const val = row[col];
                    const isNum = typeof val === 'number';
                    const linkDef = links.find(l => l.column === col);
                    const isLink = !!linkDef;
                    return (
                      <td key={col} style={{
                        padding: '4px 8px',
                        textAlign: isNum ? 'right' : 'left',
                        fontFamily: isNum ? 'var(--font-mono, monospace)' : undefined,
                        whiteSpace: 'nowrap',
                      }}>
                        {isLink ? (
                          <span
                            onClick={(e) => { e.stopPropagation(); handleCellClick(col, row); }}
                            style={{
                              color: 'hsl(210, 100%, 56%)',
                              cursor: 'pointer',
                              textDecoration: 'underline',
                              textUnderlineOffset: 2,
                            }}
                            onMouseOver={(e) => { (e.currentTarget as HTMLElement).style.color = 'hsl(210, 100%, 46%)'; }}
                            onMouseOut={(e) => { (e.currentTarget as HTMLElement).style.color = 'hsl(210, 100%, 56%)'; }}
                          >
                            {val != null ? String(val) : '-'}
                          </span>
                        ) : (
                          val != null ? String(val) : '-'
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 行序不确定（正文没顶层 ORDER BY）：翻页会重行/漏行——分页参数没错，
            错在集合本身没有可翻的顺序，得说出来而不是让用户拿到两张一样的页 */}
        {serverPaged && paging.stable_order === false && (
          <div role="alert" style={{
            padding: '3px 8px', borderTop: '1px solid hsl(var(--border))',
            flexShrink: 0, fontSize: 11, color: '#b45309',
            background: 'rgba(245, 158, 11, 0.08)',
          }}>
            该数据集正文没有排序（ORDER BY），翻页可能重复或漏行。请在数据集正文补上稳定排序。
          </div>
        )}

        {/* 行数撞到服务端上限：结果不是全量，必须说出来 —— 不得拿“共 N 行”冒充整张表 */}
        {!serverPaged && data.truncated && (
          <div role="alert" style={{
            padding: '3px 8px', borderTop: '1px solid hsl(var(--border))',
            flexShrink: 0, fontSize: 11, color: '#b45309',
            background: 'rgba(245, 158, 11, 0.08)',
          }}>
            已达单次返回行数上限（{allRows.length} 行），实际结果更多。如需翻页请在数据集开启「支持分页」。
          </div>
        )}

        {/* 分页条：总数与页号一律用服务端回显的事实，不再拿本页行数冒充全量 */}
        {enablePagination && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '4px 8px', borderTop: '1px solid hsl(var(--border))',
            flexShrink: 0, gap: 4, fontSize: 11,
          }}>
            <span style={{ color: 'hsl(var(--muted-foreground))' }}>
              {`共 ${totalRows} 行 · 第 ${currentPage}/${totalPages} 页`}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <button
                style={btnStyle(false, currentPage <= 1)}
                onClick={() => goPage(currentPage - 1)}
                disabled={currentPage <= 1}
                title="上一页"
              >‹</button>
              {pageNumbers.map((p, idx) =>
                p === '...'
                  ? <span key={`e${idx}`} style={{ padding: '0 4px', color: 'hsl(var(--muted-foreground))' }}>…</span>
                  : <button
                      key={p}
                      style={btnStyle(p === currentPage)}
                      onClick={() => goPage(p as number)}
                    >{p}</button>
              )}
              <button
                style={btnStyle(false, currentPage >= totalPages)}
                onClick={() => goPage(currentPage + 1)}
                disabled={currentPage >= totalPages}
                title={canJump ? '下一页' : '该通道按游标顺序翻页，需逐页往后翻（不可直接跳页）'}
              >›</button>
            </div>
          </div>
        )}

        {/* Drill-through modal */}
        {modalState && (
          <ModalPage
            pageId={modalState.pageId}
            params={modalState.params}
            open={!!modalState}
            onClose={() => setModalState(null)}
          />
        )}
      </div>
    );
  }

  // Show loading for map charts(加载失败显式报错,不静默卡“加载中”)
  if ((chartType === 'china_map' || chartType === 'world_map') && !mapData) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 8,
        position: 'absolute', inset: 0, padding: 16, textAlign: 'center',
        color: mapError ? '#b45309' : '#999', fontSize: 12,
      }}>
        {mapError ? `地图底图数据加载失败：${mapError}` : '加载地图数据中...'}
        {mapError && (
          <span style={{ color: '#999' }}>
            该图型依赖外部 topo 底图资源；对话页可改用「地图热力」（复用 UI 字模库地图瓦片）展示地理分布。
          </span>
        )}
      </div>
    );
  }

  const isWidget = chartType.startsWith('widget_');

  return (
    <div
      ref={containerRef}
      className="chart-cell"
      style={{
        position: 'absolute', inset: 0,
        overflow: 'hidden', ...style,
      }}
    >
      {chartId != null && !isWidget && (
        <button
          className="chart-refresh-btn"
          onClick={(e) => {
            e.stopPropagation();
            useDashboardStore.getState().refreshSingleChart(chartId, { dashboardId, chartName });
          }}
          title="刷新此图表"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={isLoading ? { animation: 'spin 1s linear infinite' } : undefined}>
            <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
            <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
            <path d="M16 16h5v5" />
          </svg>
        </button>
      )}
      {isLoading && (
        <div style={{
          position: 'absolute', inset: 0, zIndex: 10,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: isDark ? 'rgba(0,0,0,0.4)' : 'rgba(255,255,255,0.6)',
          borderRadius: 'inherit',
          backdropFilter: 'blur(2px)',
        }}>
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" style={{ animation: 'spin 1s linear infinite' }}>
            <circle cx="12" cy="12" r="10" stroke={isDark ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.1)'} strokeWidth="3" />
            <path d="M12 2a10 10 0 0 1 10 10" stroke="hsl(var(--primary))" strokeWidth="3" strokeLinecap="round" />
          </svg>
        </div>
      )}
      {/* 渲染失败：说清是哪张图、为什么不成，并给可行动下一步（不静默留一片空白） */}
      {!!renderError && (
        <div role="alert" style={{
          position: 'absolute', inset: 0, zIndex: 11,
          display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column',
          gap: 6, padding: 16, textAlign: 'center', background: isDark ? 'rgba(0,0,0,0.35)' : 'rgba(255,255,255,0.75)',
          color: '#b45309', fontSize: 12, lineHeight: 1.6,
        }}>
          <span>{chartName ? `「${chartName}」` : '该图表'}渲染失败：{renderError}</span>
          <span style={{ color: '#999' }}>可换个图型重试，或在「明细」页看原始数据。</span>
        </div>
      )}
    </div>
  );
}

export default memo(DashboardChartInner);

export interface ChartTypeItem {
  value: string;
  label: string;
  icon: string; // Lucide icon name
  category: 'basic' | 'stat' | 'timeseries' | 'advanced' | 'geo' | 'widget';
}

export const CHART_TYPES: ChartTypeItem[] = [
  // 基础图表
  { value: 'bar', label: '柱状图', icon: 'BarChart3', category: 'basic' },
  { value: 'line', label: '折线图', icon: 'TrendingUp', category: 'basic' },
  { value: 'pie', label: '饼图', icon: 'PieChart', category: 'basic' },
  { value: 'area', label: '面积图', icon: 'Activity', category: 'basic' },
  { value: 'scatter', label: '散点图', icon: 'Circle', category: 'basic' },
  { value: 'radar', label: '雷达图', icon: 'Webhook', category: 'basic' },
  { value: 'funnel', label: '漏斗图', icon: 'Filter', category: 'basic' },
  { value: 'waterfall', label: '瀑布图', icon: 'ArrowDownUp', category: 'basic' },
  // 数据展示
  { value: 'text_display', label: '文本展示', icon: 'Type', category: 'stat' },
  { value: 'table_value', label: '表值图', icon: 'Table', category: 'stat' },
  { value: 'big_number_trend', label: '趋势大数字', icon: 'Hash', category: 'stat' },
  { value: 'gauge', label: '仪表盘', icon: 'Gauge', category: 'stat' },
  // 时间序列
  { value: 'timeseries_line', label: '时序折线图', icon: 'LineChart', category: 'timeseries' },
  { value: 'timeseries_bar', label: '时序柱状图', icon: 'BarChart', category: 'timeseries' },
  { value: 'timeseries_area', label: '时序面积图', icon: 'AreaChart', category: 'timeseries' },
  { value: 'calendar_heatmap', label: '日历热力图', icon: 'Calendar', category: 'timeseries' },
  { value: 'bar_race', label: '竞速条形图', icon: 'Trophy', category: 'timeseries' },
  { value: 'timeline_play', label: '时序播放', icon: 'Play', category: 'timeseries' },
  // 高级图表
  { value: 'heatmap', label: '热力图', icon: 'Grid3x3', category: 'advanced' },
  { value: 'boxplot', label: '箱线图', icon: 'Box', category: 'advanced' },
  { value: 'bubble', label: '气泡图', icon: 'Aperture', category: 'advanced' },
  { value: 'sankey', label: '桑基图', icon: 'ArrowRightLeft', category: 'advanced' },
  { value: 'tree', label: '树图', icon: 'GitBranch', category: 'advanced' },
  { value: 'treemap', label: '矩形树图', icon: 'LayoutGrid', category: 'advanced' },
  { value: 'rose', label: '玫瑰图', icon: 'Flower2', category: 'advanced' },
  { value: 'radial_bar', label: '径向柱状图', icon: 'Target', category: 'advanced' },
  { value: 'word_cloud', label: '词云图', icon: 'Cloud', category: 'advanced' },
  // 地理图表
  { value: 'china_map', label: '中国地图', icon: 'Map', category: 'geo' },
  { value: 'world_map', label: '世界地图', icon: 'Globe', category: 'geo' },
  // 参数控件
  { value: 'widget_label', label: '文本展示框', icon: 'Type', category: 'widget' },
  { value: 'widget_text', label: '文本输入框', icon: 'Type', category: 'widget' },
  { value: 'widget_number', label: '数字输入框', icon: 'Hash', category: 'widget' },
  { value: 'widget_date', label: '日期选择器', icon: 'Calendar', category: 'widget' },
  { value: 'widget_daterange', label: '日期范围', icon: 'Calendar', category: 'widget' },
  { value: 'widget_select', label: '下拉选择框', icon: 'List', category: 'widget' },
  { value: 'widget_multi_select', label: '多选下拉框', icon: 'ListChecks', category: 'widget' },
  { value: 'widget_search', label: '搜索按钮', icon: 'Search', category: 'widget' },
  { value: 'widget_reset', label: '重置按钮', icon: 'RotateCcw', category: 'widget' },
  { value: 'widget_export', label: '导出按钮', icon: 'Download', category: 'widget' },
  // 展示型控件：不写参数，只把服务端算好的结论放在看板上
  { value: 'widget_decision', label: '决策建议卡', icon: 'ListChecks', category: 'widget' },
];

export const CHART_TYPE_CATEGORIES = [
  { key: 'basic', label: '基础图表' },
  { key: 'stat', label: '数据展示' },
  { key: 'timeseries', label: '时间序列' },
  { key: 'advanced', label: '高级图表' },
  { key: 'geo', label: '地理图表' },
  { key: 'widget', label: '参数控件' },
] as const;
