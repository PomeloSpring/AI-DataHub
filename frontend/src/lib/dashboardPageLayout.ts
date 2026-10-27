import type { Dashboard, DashboardChart } from '@/stores/dashboardStore';
import { chartRect, getCanvas, naturalSizeOf, type Rect } from './dashboardDesign';

export type PageBreakpoint = 'desktop' | 'tablet' | 'mobile';
export const PAGE_WIDTHS = { desktop: 1440, tablet: 768, mobile: 390 };
/**
 * 网格分辨率 v3：列 12/6/1 → 48/24/1（宽度步进 121px → 30px）。
 * 拖拽调整大小时“突然缩到一个大小、突然放大到一个大小”的根因就是宽度只能按整列跳。
 */
export const PAGE_COLUMNS = { desktop: 48, tablet: 24, mobile: 1 };
/** v2 及更早的列数：只用于存量布局的等像素换算，读路一律以 v3 为准 */
export const LEGACY_PAGE_COLUMNS = { desktop: 12, tablet: 6, mobile: 1 };
export const PAGE_LABELS = { desktop: '桌面', tablet: '平板', mobile: '手机' };
/**
 * 行单位 v3：**纯 4px 像素单行，CSS grid 的 rowGap 恒为 0**。
 *
 * v2 的行单位是 `PAGE_ROW + gap`（4+16=20px），意味着跨 14 行的卡片里有 104px 是内部行距、
 * 只有 56px 是“内容行” —— 高度调不细，而且改间距会把所有卡片高度拉扇。
 * v3 把间距从行单位里剥离：高度步进 20px → 4px，间距只作用于列间距与自动避让留白。
 */
export const PAGE_ROW = 4;
/** v1 的行内容高（v1→v2→v3 逐版换算时需要） */
export const LEGACY_PAGE_ROW = 8;
/** v2 的行内容高（当时行单位 = 本值 + 固定 gap） */
const PAGE_ROW_V2 = 4;
export const PAGE_LAYOUT_VERSION = 3;
/** v2 时代写死的间距：换算存量布局必须用它，不能被用户的新设置影响 */
export const LEGACY_PAGE_GAP = { desktop: 16, tablet: 16, mobile: 12 };
export const DEFAULT_PAGE_GAP = LEGACY_PAGE_GAP;
export const PAGE_GAP_MIN = 0;
export const PAGE_GAP_MAX = 64;
/** 间距设置（看板级，按断点分别可调；缺档取默认值） */
export type PageGaps = Partial<Record<PageBreakpoint, number>>;

export const pageBreakpoint = (width: number): PageBreakpoint => width >= 1024 ? 'desktop' : width >= 640 ? 'tablet' : 'mobile';
export const pageGap = (bp: PageBreakpoint, gaps?: PageGaps) => {
  const value = gaps?.[bp];
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.min(PAGE_GAP_MAX, Math.max(PAGE_GAP_MIN, Math.round(value))) : DEFAULT_PAGE_GAP[bp];
};
/** 一列宽多少像素（含列间距；与 CSS grid 的 `repeat(n,1fr) + columnGap` 同口径） */
export const pageColumnPx = (bp: PageBreakpoint, gaps?: PageGaps) => (PAGE_WIDTHS[bp] + pageGap(bp, gaps)) / PAGE_COLUMNS[bp];
/** 自动避让补的留白行数：让默认观感与 v2 一致（卡片之间仍有「间距」那么宽的空隙） */
export const gapRows = (gap: number) => Math.ceil(gap / PAGE_ROW);
// 默认高度（自然尺寸），不是下限：控件紧凑、图表按内容常规；由人自由调整
export const pageMinHeight = (type: string) => type.startsWith('widget_') ? naturalSizeOf(type).h
  : type.includes('table') ? 360 : ['big_number', 'big_number_trend', 'statistic'].includes(type) ? 128 : 280;
// 拖入画布时的默认跳列数：与自然尺寸一致，避免刚拖进去就臃肿
// （控件用 ceil：列宽含 gap，round 会把自然宽 96px 的按钮算成 75px，内容装不下）
export const defaultPageSpan = (type: string, bp: PageBreakpoint, gaps?: PageGaps) => {
  const natural = naturalSizeOf(type);
  const column = pageColumnPx(bp, gaps);
  const span = type.startsWith('widget_') ? Math.max(1, Math.ceil(natural.w / column)) : Math.round(PAGE_COLUMNS[bp] / 2);
  return Math.min(PAGE_COLUMNS[bp], span);
};
/** 像素 → 行（v3：行单位与间距无关，恒为 PAGE_ROW） */
export const pageRows = (height: number) => Math.max(1, Math.ceil(height / PAGE_ROW));
/** 行 → 像素高 */
export const pageRowPx = (rows: number) => rows * PAGE_ROW;
export type PageItem = { chart: DashboardChart; rect: Rect; automatic: boolean };
export function pagePixelRect(rect: Rect, bp: PageBreakpoint, gaps?: PageGaps): Rect {
  const gap = pageGap(bp, gaps), column = pageColumnPx(bp, gaps);
  return { x: rect.x * column, y: rect.y * PAGE_ROW, w: rect.w * column - gap, h: rect.h * PAGE_ROW };
}
export function pageCanvasSize(items: PageItem[], bp: PageBreakpoint) {
  const bottom = items.reduce((value, item) => Math.max(value, item.rect.y + item.rect.h), 0);
  return { width: PAGE_WIDTHS[bp], height: Math.max(480, bottom * PAGE_ROW + 96) };
}
type PageDashboard = Pick<Dashboard, 'filters' | 'charts'>;
const intersects = (a: Rect, b: Rect) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
/** v1 行单位 → v2 行单位，保持像素高度基本不变（偏差 ≤ 半个行单位） */
const upgradeRows = (rows: number, gap: number) => Math.max(1, Math.round((rows * (LEGACY_PAGE_ROW + gap)) / (PAGE_ROW_V2 + gap)));
/**
 * v2 单位 → v3 单位，**像素完全等值**（不是估算）：
 * 列×4（列宽单位恰好四倍关系）、`y' = y*(4+16)/4 = 5y`、`h' = (h*20-16)/4 = 5h-4`（手机 gap=12 → 4y / 4h-3），全是整数。
 * 所以存量看板升级后一像素都不动，不需要逐看板迁移脚本。
 */
const upgradeToV3 = (rect: Rect, bp: PageBreakpoint): Rect => {
  const factor = PAGE_COLUMNS[bp] / LEGACY_PAGE_COLUMNS[bp];
  const gap = LEGACY_PAGE_GAP[bp];
  const unit = PAGE_ROW_V2 + gap;
  return { x: rect.x * factor, y: (rect.y * unit) / PAGE_ROW, w: rect.w * factor, h: (rect.h * unit - gap) / PAGE_ROW };
};

/** 任意历史版本的存储矩形 → v3 矩形（v1 先过 v2，再统一升到 v3） */
const normalizeRect = (rect: Rect, bp: PageBreakpoint, version: number): Rect => {
  if (version === PAGE_LAYOUT_VERSION) return rect;
  const legacyGap = LEGACY_PAGE_GAP[bp];
  const v2 = version === 1
    ? { x: rect.x, y: upgradeRows(rect.y, legacyGap), w: rect.w, h: upgradeRows(rect.h, legacyGap) }
    : { x: rect.x, y: rect.y, w: rect.w, h: rect.h };
  return upgradeToV3(v2, bp);
};

function storedRect(chart: DashboardChart, bp: PageBreakpoint): Rect | undefined {
  const layout = chart.config?.pageLayout;
  if (layout === undefined) return undefined;
  const version = layout?.version;
  if (version !== 1 && version !== 2 && version !== 3) throw new Error(`“${chart.name}”的页面布局版本无效，请在编辑器修复`);
  const rect = layout[bp];
  if (rect === undefined) return undefined;
  // 越界判定用**该版本自己的**列数（v1/v2 是 12/6/1，v3 是 48/24/1），否则升级前写的数据会被误报损坏。
  const cols = version === 3 ? PAGE_COLUMNS[bp] : LEGACY_PAGE_COLUMNS[bp];
  if (!rect || !['x', 'y', 'w', 'h'].every(k => Number.isSafeInteger(rect[k])) || rect.x < 0 || rect.y < 0 || rect.w < 1 || rect.h < 1 || rect.x + rect.w > cols || rect.y + rect.h > 100000) {
    throw new Error(`“${chart.name}”的${PAGE_LABELS[bp]}布局无效，请在编辑器修复`);
  }
  // 不再在读取时把已存高度抬到下限：下限由交互层（movePageItem）把关，否则小尺寸存不住。
  // 行单位以本图表自己的 pageLayout.version 为准（单一事实源，不看看板级标记）。
  return normalizeRect(rect, bp, version);
}

/** 看板级间距设置（`filters.design.page.gap`，按断点分别可调） */
export const pageGapsOf = (filters: any): PageGaps | undefined => filters?.design?.page?.gap;

// 仅生成展示视图，不改变历史大屏坐标，也不在读取时持久化。
export function resolvePageLayout(dashboard: PageDashboard, bp: PageBreakpoint): PageItem[] {
  const page = dashboard.filters?.design?.page;
  // 看板级标记只验版本（gap 可以单独存在），单位换算一律看每图表自己的 pageLayout.version。
  if (page?.version !== undefined && page.version !== 1 && page.version !== 2 && page.version !== 3) throw new Error('页面布局版本无效，请在编辑器修复');
  const gaps = pageGapsOf(dashboard.filters);
  const gap = pageGap(bp, gaps);
  const width = getCanvas(dashboard.filters, dashboard.charts).width;
  const desktop = bp === 'desktop' ? null : resolvePageLayout(dashboard, 'desktop');
  const columns = PAGE_COLUMNS[bp];
  const items = dashboard.charts.map(chart => {
    const explicit = storedRect(chart, bp);
    const origin = chartRect(chart);
    const parent = desktop?.find(item => item.chart.id === chart.id)?.rect;
    const widget = chart.chart_type.startsWith('widget_');
    const natural = naturalSizeOf(chart.chart_type);
    const ratio = parent ? columns / PAGE_COLUMNS.desktop : columns / width;
    // 未手动放置的控件按自然尺寸生成（ceil 保证装得下内容），不再从大屏 position(默认 300×60)按比例放大；
    // 图表保留按 position 等比映射（大屏布局是有意义的起点，round 即等比例）
    const sourceW = parent?.w ?? (widget ? natural.w : origin.w);
    // 图表默认跳“四分之一屏”（v2 时代是 3/12 列），不随列数膨胀
    const w = bp === 'mobile' ? 1 : Math.min(columns, Math.max(widget ? 1 : Math.round(columns / 4),
      widget ? Math.max(1, Math.ceil(sourceW * ratio)) : Math.round(sourceW * ratio)));
    const height = parent ? pageRowPx(parent.h)
      : widget ? natural.h : Math.min(600, origin.h);
    return { chart, automatic: !explicit, rect: explicit || {
      x: Math.max(0, Math.min(columns - w, Math.round((parent?.x ?? origin.x) * ratio))),
      y: parent?.y ?? Math.round(origin.y / PAGE_ROW), w,
      h: pageRows(Math.max(pageMinHeight(chart.chart_type), height)),
    } };
  }).sort((a, b) => bp === 'mobile' && a.automatic && b.automatic
    ? desktop!.findIndex(item => item.chart.id === a.chart.id) - desktop!.findIndex(item => item.chart.id === b.chart.id)
    : a.rect.y - b.rect.y || a.rect.x - b.rect.x || a.chart.id - b.chart.id);
  const slack = gapRows(gap);
  const occupied: Rect[] = [];
  for (const item of items) {
    if (item.automatic) {
      const above = bp === 'mobile' ? occupied : occupied.filter(r => item.rect.x < r.x + r.w && item.rect.x + item.rect.w > r.x);
      item.rect.y = above.reduce((y, r) => Math.max(y, r.y + r.h + slack), 0);
    }
    let collision = occupied.find(r => intersects(item.rect, r));
    while (collision) { item.rect.y = collision.y + collision.h + slack; collision = occupied.find(r => intersects(item.rect, r)); }
    occupied.push(item.rect);
  }
  return items;
}

/**
 * 插入点的下一行 —— 注意这是**行**单位。
 *
 * 曾经把 chartRect 的像素值当行用（20px 一行），每加一个控件就跑到几万行外，
 * 画布高度狂飙到 2 万像素、看起来像“拖不进画布”。像素与行在这里必须分开。
 */
export function nextPageRow(items: PageItem[]): number {
  return items.reduce((max, i) => Math.max(max, i.rect.y + i.rect.h), 0);
}

/**
 * 预览「插入后会被挤到哪」：把一个尚不存在的项塞进布局并跑一遍避让，
 * 返回完整布局（含插入项）。不改变传入的 items。
 *
 * 用于拖入时实时展示：虚线框 = 将占的空间，其余卡片按预测位置移动 = 将被挤到的地方。
 * 松手才真正提交，拖回图标库即自然取消（不写草稿）。
 */
export function previewInsert(items: PageItem[], insert: PageItem, bp: PageBreakpoint, gaps?: PageGaps): PageItem[] {
  return movePageItem([insert, ...items], insert.chart.id, insert.rect, bp, gaps);
}

export function movePageItem(items: PageItem[], id: number, rect: Rect, bp: PageBreakpoint, gaps?: PageGaps): PageItem[] {
  const selected = items.find(item => item.chart.id === id);
  if (!selected || !Object.values(rect).every(Number.isFinite)) return items;
  const cols = PAGE_COLUMNS[bp];
  const slack = gapRows(pageGap(bp, gaps));
  const w = Math.min(cols, Math.max(1, Math.round(rect.w)));
  // 不再按类型抬高到最小行数：下限只保留网格的 1 行量子（v3 = 4px），大小由人主动控制
  const target = { ...selected, rect: { x: Math.min(cols - w, Math.max(0, Math.round(rect.x))), y: Math.min(50000, Math.max(0, Math.round(rect.y))), w,
    h: Math.min(10000, Math.max(1, Math.round(rect.h))) }, automatic: false };
  const result = [target];
  for (const item of items.filter(item => item.chart.id !== id)) {
    const next = { ...item, rect: { ...item.rect } };
    let collision = result.find(other => intersects(next.rect, other.rect));
    // 避让时补上间距那么宽的留白（v2 靠 CSS rowGap 天然隔开，v3 rowGap=0 必须自己留）
    while (collision) { next.rect.y = collision.rect.y + collision.rect.h + slack; collision = result.find(other => intersects(next.rect, other.rect)); }
    result.push(next);
  }
  return result.sort((a, b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x || a.chart.id - b.chart.id);
}

/** 对齐参考线：axis 为线的法轴（'x' = 竖线，位置在 at），from/to 是线在另一轴上的跨度 */
export type AlignLine = { axis: 'x' | 'y'; at: number; from: number; to: number };
/** 间距读数：与最近邻居（或画布边缘）之间的空白，at 是卡片在另一轴上的中心 */
export type GapHint = { axis: 'x' | 'y'; from: number; to: number; at: number; value: number; equal: boolean };
export interface AlignHints { lines: AlignLine[]; gaps: GapHint[] }

const axisStart = (r: Rect, axis: 'x' | 'y') => axis === 'x' ? r.x : r.y;
const axisSize = (r: Rect, axis: 'x' | 'y') => axis === 'x' ? r.w : r.h;
/** 一个矩形在某轴上的三个参考锚点：起始边 / 中心 / 结束边 */
const axisAnchors = (r: Rect, axis: 'x' | 'y') => {
  const start = axisStart(r, axis), size = axisSize(r, axis);
  return [start, start + size / 2, start + size];
};

/**
 * 拖拽/缩放时的对齐与间距辅助提示（纯函数，输入一律是已换算完的像素矩形）。
 *
 * 网格只能保证“落子到格子上”，但保证不了“两张卡左边缘齐平”与“左右留空相等”——
 * 那需要实时告知“现在差多少 px”：
 *  - lines：与邻居边缘/中心或画布边缘/中线对齐的参考线（同一位置只画一条，跨度取并集）；
 *  - gaps：与左右（或上下）最近邻居之间的间距读数，两侧相等时 equal=true（等距提示）。
 */
export function computeAlignHints(
  target: Rect, others: Rect[], canvas: { width: number; height: number }, tolerance = 4,
): AlignHints {
  const lines: AlignLine[] = [];
  for (const axis of ['x', 'y'] as const) {
    const cross = axis === 'x' ? 'y' : 'x';
    const extent = axis === 'x' ? canvas.width : canvas.height;
    const spans: { at: number; from: number; to: number }[] = [];
    const push = (at: number, other: Rect) => {
      const from = Math.min(axisStart(target, cross), axisStart(other, cross));
      const to = Math.max(axisStart(target, cross) + axisSize(target, cross), axisStart(other, cross) + axisSize(other, cross));
      const hit = spans.find(s => Math.abs(s.at - at) <= 0.5);
      if (hit) { hit.from = Math.min(hit.from, from); hit.to = Math.max(hit.to, to); }
      else spans.push({ at, from, to });
    };
    for (const other of others)
      for (const t of axisAnchors(target, axis)) for (const o of axisAnchors(other, axis))
        if (Math.abs(t - o) <= tolerance) push(o, other);
    // 画布边缘与中线：跨度就是目标自己（没有可并的邻居）
    for (const o of [0, extent / 2, extent])
      for (const t of axisAnchors(target, axis))
        if (Math.abs(t - o) <= tolerance) push(o, target);
    for (const s of spans) lines.push({ axis, at: s.at, from: s.from, to: s.to });
  }
  const gaps: GapHint[] = [];
  for (const axis of ['x', 'y'] as const) {
    const cross = axis === 'x' ? 'y' : 'x';
    const start = axisStart(target, axis), end = start + axisSize(target, axis);
    const overlaps = (o: Rect) => axisStart(o, cross) < axisStart(target, cross) + axisSize(target, cross)
      && axisStart(o, cross) + axisSize(o, cross) > axisStart(target, cross);
    const before = others.filter(o => overlaps(o) && axisStart(o, axis) + axisSize(o, axis) <= start + 0.5)
      .sort((a, b) => (axisStart(b, axis) + axisSize(b, axis)) - (axisStart(a, axis) + axisSize(a, axis)))[0];
    const after = others.filter(o => overlaps(o) && axisStart(o, axis) >= end - 0.5)
      .sort((a, b) => axisStart(a, axis) - axisStart(b, axis))[0];
    const at = axisStart(target, cross) + axisSize(target, cross) / 2;
    const found: GapHint[] = [];
    if (before) {
      const edge = axisStart(before, axis) + axisSize(before, axis);
      if (start - edge >= 0) found.push({ axis, from: edge, to: start, at, value: start - edge, equal: false });
    }
    if (after && after !== before) {
      if (axisStart(after, axis) - end >= 0) found.push({ axis, from: end, to: axisStart(after, axis), at, value: axisStart(after, axis) - end, equal: false });
    }
    // 两侧留空相等 → 等距（Figma 风格的强提示）
    if (found.length === 2 && Math.abs(found[0].value - found[1].value) <= 1) found.forEach(g => { g.equal = true; });
    gaps.push(...found);
  }
  return { lines, gaps };
}

export function withPageRect<T extends Record<string, any>>(config: T, bp: PageBreakpoint, rect?: Rect) {
  const stored = config.pageLayout;
  const storedVersion = stored?.version;
  // 升版时必须把**所有断点**的存量矩形一起换算：只改 version 不换单位，
  // 未编辑的桌面/手机矩形会被当 v3 读（宽 4 倍、高 5 倍），整板布局直接崩掉。
  const migrated: Record<string, any> = { ...stored, version: PAGE_LAYOUT_VERSION };
  if (stored && storedVersion !== PAGE_LAYOUT_VERSION && storedVersion !== undefined) {
    for (const key of ['desktop', 'tablet', 'mobile'] as PageBreakpoint[]) {
      if (stored[key]) migrated[key] = normalizeRect(stored[key], key, storedVersion);
    }
  }
  const pageLayout: Record<string, any> = migrated;
  if (rect) pageLayout[bp] = rect; else delete pageLayout[bp];
  return { ...config, pageLayout };
}
