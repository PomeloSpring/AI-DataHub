import type { Dashboard, DashboardChart } from '@/stores/dashboardStore';
import { chartRect, getCanvas, type Rect } from './dashboardDesign';

export type PageBreakpoint = 'desktop' | 'tablet' | 'mobile';
export const PAGE_WIDTHS = { desktop: 1440, tablet: 768, mobile: 390 };
export const PAGE_COLUMNS = { desktop: 12, tablet: 6, mobile: 1 };
export const PAGE_LABELS = { desktop: '桌面', tablet: '平板', mobile: '手机' };
export const PAGE_ROW = 8;
export const pageBreakpoint = (width: number): PageBreakpoint => width >= 1024 ? 'desktop' : width >= 640 ? 'tablet' : 'mobile';
export const pageGap = (bp: PageBreakpoint) => bp === 'mobile' ? 12 : 16;
export const pageMinHeight = (type: string) => type.startsWith('widget_') ? 48 : type.includes('table') ? 360 : ['big_number', 'big_number_trend', 'statistic'].includes(type) ? 128 : 280;
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

function storedRect(chart: DashboardChart, bp: PageBreakpoint): Rect | undefined {
  const layout = chart.config?.pageLayout;
  if (layout === undefined) return undefined;
  if (!layout || layout.version !== 1) throw new Error(`“${chart.name}”的页面布局版本无效，请在编辑器修复`);
  const rect = layout[bp];
  if (rect === undefined) return undefined;
  if (!rect || !['x', 'y', 'w', 'h'].every(k => Number.isSafeInteger(rect[k])) || rect.x < 0 || rect.y < 0 || rect.w < 1 || rect.h < 1 || rect.x + rect.w > PAGE_COLUMNS[bp] || rect.y + rect.h > 100000) {
    throw new Error(`“${chart.name}”的${PAGE_LABELS[bp]}布局无效，请在编辑器修复`);
  }
  return { x: rect.x, y: rect.y, w: rect.w, h: Math.max(rect.h, pageRows(pageMinHeight(chart.chart_type), bp)) };
}

// 仅生成展示视图，不改变历史大屏坐标，也不在读取时持久化。
export function resolvePageLayout(dashboard: PageDashboard, bp: PageBreakpoint): PageItem[] {
  if (dashboard.filters?.design?.page !== undefined && dashboard.filters.design.page?.version !== 1) throw new Error('页面布局版本无效，请在编辑器修复');
  const width = getCanvas(dashboard.filters, dashboard.charts).width;
  const desktop = bp === 'desktop' ? null : resolvePageLayout(dashboard, 'desktop');
  const columns = PAGE_COLUMNS[bp];
  const items = dashboard.charts.map(chart => {
    const explicit = storedRect(chart, bp);
    const origin = chartRect(chart);
    const parent = desktop?.find(item => item.chart.id === chart.id)?.rect;
    const ratio = parent ? columns / 12 : columns / width;
    const w = bp === 'mobile' ? 1 : Math.min(columns, Math.max(chart.chart_type.startsWith('widget_') ? 2 : 3, Math.round((parent?.w ?? origin.w) * ratio)));
    const height = parent ? parent.h * (PAGE_ROW + 16) - 16 : Math.min(600, origin.h);
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

export function movePageItem(items: PageItem[], id: number, rect: Rect, bp: PageBreakpoint): PageItem[] {
  const selected = items.find(item => item.chart.id === id);
  if (!selected || !Object.values(rect).every(Number.isFinite)) return items;
  const cols = PAGE_COLUMNS[bp];
  const w = Math.min(cols, Math.max(1, Math.round(rect.w)));
  const target = { ...selected, rect: { x: Math.min(cols - w, Math.max(0, Math.round(rect.x))), y: Math.min(50000, Math.max(0, Math.round(rect.y))), w,
    h: Math.min(10000, Math.max(pageRows(pageMinHeight(selected.chart.chart_type), bp), Math.round(rect.h))) }, automatic: false };
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
  const pageLayout = { ...config.pageLayout, version: 1 };
  if (rect) pageLayout[bp] = rect; else delete pageLayout[bp];
  return { ...config, pageLayout };
}
