import type { Dashboard, DashboardChart } from '@/stores/dashboardStore';
import { chartRect, getCanvas, naturalSizeOf, type Rect } from './dashboardDesign';

export type PageBreakpoint = 'desktop' | 'tablet' | 'mobile';
export const PAGE_WIDTHS = { desktop: 1440, tablet: 768, mobile: 390 };
export const PAGE_COLUMNS = { desktop: 12, tablet: 6, mobile: 1 };
export const PAGE_LABELS = { desktop: '桌面', tablet: '平板', mobile: '手机' };
/**
 * 行高粒度 v2：8px → 4px，高度步进从 24px 降到 20px，控件才能做出 20/40/60 的紧凑档位。
 * 旧数据（page.version === 1）在读取时按像素等值换算，不需要逐看板迁移脚本。
 */
export const PAGE_ROW = 4;
export const LEGACY_PAGE_ROW = 8;
export const PAGE_LAYOUT_VERSION = 2;
export const pageBreakpoint = (width: number): PageBreakpoint => width >= 1024 ? 'desktop' : width >= 640 ? 'tablet' : 'mobile';
export const pageGap = (bp: PageBreakpoint) => bp === 'mobile' ? 12 : 16;
// 默认高度（自然尺寸），不是下限：控件紧凑、图表按内容常规；由人自由调整
export const pageMinHeight = (type: string) => type.startsWith('widget_') ? naturalSizeOf(type).h
  : type.includes('table') ? 360 : ['big_number', 'big_number_trend', 'statistic'].includes(type) ? 128 : 280;
// 拖入画布时的默认跳列数：与自然尺寸一致，避免刚拖进去就臃肿
export const defaultPageSpan = (type: string, bp: PageBreakpoint) => {
  const natural = naturalSizeOf(type);
  const span = type.startsWith('widget_') ? Math.max(1, Math.round(natural.w / 120)) : 6;
  return Math.min(PAGE_COLUMNS[bp], span);
};
export const pageRows = (height: number, bp: PageBreakpoint) => Math.ceil((height + pageGap(bp)) / (PAGE_ROW + pageGap(bp)));
export type PageItem = { chart: DashboardChart; rect: Rect; automatic: boolean };
export function pagePixelRect(rect: Rect, bp: PageBreakpoint): Rect {
  const gap = pageGap(bp), column = (PAGE_WIDTHS[bp] + gap) / PAGE_COLUMNS[bp], row = PAGE_ROW + gap;
  return { x: rect.x * column, y: rect.y * row, w: rect.w * column - gap, h: rect.h * row - gap };
}
export function pageCanvasSize(items: PageItem[], bp: PageBreakpoint) {
  const bottom = items.reduce((value, item) => Math.max(value, item.rect.y + item.rect.h), 0);
  return { width: PAGE_WIDTHS[bp], height: Math.max(480, bottom * (PAGE_ROW + pageGap(bp)) - pageGap(bp) + 96) };
}
type PageDashboard = Pick<Dashboard, 'filters' | 'charts'>;
const intersects = (a: Rect, b: Rect) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
/** v1 行单位 → v2 行单位，保持像素高度基本不变（偏差 ≤ 半个行单位） */
const upgradeRows = (rows: number, gap: number) => Math.max(1, Math.round((rows * (LEGACY_PAGE_ROW + gap)) / (PAGE_ROW + gap)));

function storedRect(chart: DashboardChart, bp: PageBreakpoint): Rect | undefined {
  const layout = chart.config?.pageLayout;
  if (layout === undefined) return undefined;
  if (!layout || (layout.version !== 1 && layout.version !== 2)) throw new Error(`“${chart.name}”的页面布局版本无效，请在编辑器修复`);
  const rect = layout[bp];
  if (rect === undefined) return undefined;
  if (!rect || !['x', 'y', 'w', 'h'].every(k => Number.isSafeInteger(rect[k])) || rect.x < 0 || rect.y < 0 || rect.w < 1 || rect.h < 1 || rect.x + rect.w > PAGE_COLUMNS[bp] || rect.y + rect.h > 100000) {
    throw new Error(`“${chart.name}”的${PAGE_LABELS[bp]}布局无效，请在编辑器修复`);
  }
  // 不再在读取时把已存高度抬到下限：下限由交互层（movePageItem）把关，否则小尺寸存不住。
  // 行单位以本图表自己的 pageLayout.version 为准（单一事实源，不看看板级标记）。
  if (layout.version !== 1) return { x: rect.x, y: rect.y, w: rect.w, h: rect.h };
  const gap = pageGap(bp);
  return { x: rect.x, y: upgradeRows(rect.y, gap), w: rect.w, h: upgradeRows(rect.h, gap) };
}

// 仅生成展示视图，不改变历史大屏坐标，也不在读取时持久化。
export function resolvePageLayout(dashboard: PageDashboard, bp: PageBreakpoint): PageItem[] {
  const page = dashboard.filters?.design?.page;
  if (page !== undefined && page?.version !== 1 && page?.version !== 2) throw new Error('页面布局版本无效，请在编辑器修复');
  const width = getCanvas(dashboard.filters, dashboard.charts).width;
  const desktop = bp === 'desktop' ? null : resolvePageLayout(dashboard, 'desktop');
  const columns = PAGE_COLUMNS[bp];
  const items = dashboard.charts.map(chart => {
    const explicit = storedRect(chart, bp);
    const origin = chartRect(chart);
    const parent = desktop?.find(item => item.chart.id === chart.id)?.rect;
    const widget = chart.chart_type.startsWith('widget_');
    const natural = naturalSizeOf(chart.chart_type);
    const ratio = parent ? columns / 12 : columns / width;
    // 未手动放置的控件按自然尺寸生成，不再从大屏 position(默认 300×60)按比例放大；
    // 图表保留按 position 等比映射（大屏布局是有意义的起点）
    const sourceW = parent?.w ?? (widget ? natural.w : origin.w);
    const w = bp === 'mobile' ? 1 : Math.min(columns, Math.max(widget ? 1 : 3, Math.round(sourceW * ratio)));
    const height = parent ? parent.h * (PAGE_ROW + pageGap('desktop')) - pageGap('desktop')
      : widget ? natural.h : Math.min(600, origin.h);
    return { chart, automatic: !explicit, rect: explicit || {
      x: Math.max(0, Math.min(columns - w, Math.round((parent?.x ?? origin.x) * ratio))),
      y: parent?.y ?? Math.round(origin.y / (PAGE_ROW + pageGap(bp))), w,
      h: pageRows(Math.max(pageMinHeight(chart.chart_type), height), bp),
    } };
  }).sort((a, b) => bp === 'mobile' && a.automatic && b.automatic
    ? desktop!.findIndex(item => item.chart.id === a.chart.id) - desktop!.findIndex(item => item.chart.id === b.chart.id)
    : a.rect.y - b.rect.y || a.rect.x - b.rect.x || a.chart.id - b.chart.id);
  const occupied: Rect[] = [];
  for (const item of items) {
    if (item.automatic) {
      if (bp === 'mobile') item.rect.y = occupied.reduce((y, r) => Math.max(y, r.y + r.h), 0);
      else item.rect.y = occupied.filter(r => item.rect.x < r.x + r.w && item.rect.x + item.rect.w > r.x).reduce((y, r) => Math.max(y, r.y + r.h), 0);
    }
    let collision = occupied.find(r => intersects(item.rect, r));
    while (collision) { item.rect.y = collision.y + collision.h; collision = occupied.find(r => intersects(item.rect, r)); }
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
export function previewInsert(items: PageItem[], insert: PageItem, bp: PageBreakpoint): PageItem[] {
  return movePageItem([insert, ...items], insert.chart.id, insert.rect, bp);
}

export function movePageItem(items: PageItem[], id: number, rect: Rect, bp: PageBreakpoint): PageItem[] {
  const selected = items.find(item => item.chart.id === id);
  if (!selected || !Object.values(rect).every(Number.isFinite)) return items;
  const cols = PAGE_COLUMNS[bp];
  const w = Math.min(cols, Math.max(1, Math.round(rect.w)));
  // 不再按类型抬高到最小行数：下限只保留网格的 1 行量子，大小由人主动控制
  const target = { ...selected, rect: { x: Math.min(cols - w, Math.max(0, Math.round(rect.x))), y: Math.min(50000, Math.max(0, Math.round(rect.y))), w,
    h: Math.min(10000, Math.max(1, Math.round(rect.h))) }, automatic: false };
  const result = [target];
  for (const item of items.filter(item => item.chart.id !== id)) {
    const next = { ...item, rect: { ...item.rect } };
    let collision = result.find(other => intersects(next.rect, other.rect));
    while (collision) { next.rect.y = collision.rect.y + collision.rect.h; collision = result.find(other => intersects(next.rect, other.rect)); }
    result.push(next);
  }
  return result.sort((a, b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x || a.chart.id - b.chart.id);
}

export function withPageRect<T extends Record<string, any>>(config: T, bp: PageBreakpoint, rect?: Rect) {
  const pageLayout = { ...config.pageLayout, version: PAGE_LAYOUT_VERSION };
  if (rect) pageLayout[bp] = rect; else delete pageLayout[bp];
  return { ...config, pageLayout };
}
