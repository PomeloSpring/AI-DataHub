import type { VisCategory, VisComponent } from '@/api/visLibrary';
import type { DashboardChart } from '@/stores/dashboardStore';

export interface LocalVisual {
  mode?: 'light' | 'dark'; palette?: string[]; viewBg?: string; popoverBg?: string;
  textColor?: string; subTextColor?: string; gridColor?: string;
}
export interface VisSnapshot {
  version: 1; category: VisCategory; code: string; revision?: string;
  style_config: Record<string, any>;
}
export type Rect = { x: number; y: number; w: number; h: number };
const VISUAL_KEYS = ['colorScheme', 'gradient', 'borderRadius', 'showLabel', 'smooth', 'lineWidth',
  'areaFill', 'areaOpacity', 'innerRadius', 'legend', 'thresholds', 'gridColor', 'valueColor', 'labelColor',
  'cardBg', 'cardBorder', 'glow', 'cornerAccent', 'showCornerBrackets', 'borderStyle',
  'titleBarBg', 'titleColor', 'showUnderline', 'underlineColor', 'titleFontSize', 'titleFontWeight'];
const TOP_KEYS = ['backgroundColor', 'backgroundImage', 'backgroundSize', 'overlay',
  'mode', 'palette', 'viewBg', 'popoverBg', 'textColor', 'subTextColor', 'gridColor'];
const safeString = (v: string) => !/url\s*\(|javascript:|expression\s*\(|<|>|[;{}]/i.test(v);
function safeValue(v: unknown): any {
  if (typeof v === 'string') return safeString(v) && v.length < 2048 ? v : undefined;
  if (typeof v === 'number') return Number.isFinite(v) ? v : undefined;
  if (typeof v === 'boolean') return v;
  if (Array.isArray(v)) return v.slice(0, 64).map(safeValue).filter(x => x !== undefined);
  return undefined;
}
function pick(source: any, keys: string[]): Record<string, any> {
  const result: Record<string, any> = {};
  for (const key of keys) {
    const value = safeValue(source?.[key]);
    if (value !== undefined) result[key] = value;
  }
  return result;
}
export function visualConfig(config: any) {
  const result = pick(config, VISUAL_KEYS);
  if (config?.axisStyle) result.axisStyle = pick(config.axisStyle, ['gridColor']);
  return result;
}
export function sanitizeStyle(style: any): Record<string, any> {
  const result = pick(style, TOP_KEYS);
  if (style?.config) result.config = visualConfig(style.config);
  if (style?.grid) {
    const g = style.grid;
    result.grid = pick(g, ['canvasW', 'canvasH', 'cols']);
    if (typeof g.rows === 'number') result.grid.rows = g.rows;
    if (Array.isArray(g.rows)) result.grid.rows = g.rows.slice(0, 50).map((r: any) => pick(r, ['cols', 'ratio']));
    if (g.kpiRow) result.grid.kpiRow = pick(g.kpiRow, ['height', 'count']);
    if (Array.isArray(g.columns)) result.grid.columns = g.columns.slice(0, 20).map((c: any) => pick(c, ['width', 'role']));
    if (Array.isArray(g.positions)) result.grid.positions = g.positions.slice(0, 500).map((p: any) => pick(p, ['x', 'y', 'w', 'h']));
  }
  return result;
}
export const snapshotComponent = (c: VisComponent): VisSnapshot => ({
  version: 1, category: c.category, code: c.code, revision: c.updated_at,
  style_config: sanitizeStyle(c.style_config),
});
export const COMPONENT_SLOT: Partial<Record<VisCategory, string>> = {
  screen_background: 'background', color_theme: 'colorTheme', kpi_card: 'cardStyle',
  decoration_frame: 'decoration', layout_template: 'layout', chart_style: 'chartStyle',
};
export function resolveComponent(ref: unknown, snapshot: VisSnapshot | undefined, items: VisComponent[]) {
  if (snapshot?.version === 1 && snapshot.style_config) return sanitizeStyle(snapshot.style_config);
  const component = items.find(c => String(c.id) === String(ref) || c.code === ref);
  return component ? sanitizeStyle(component.style_config) : undefined;
}
export function getCanvas(filters: any = {}, charts: Pick<DashboardChart, 'position'>[] = []) {
  const c = filters?.design?.canvas || {};
  const positive = (v: any, fallback: number) => Number.isFinite(v) && v > 0 ? v : fallback;
  return {
    width: Math.max(positive(c.width, 1920), ...charts.map(chart => (chart.position?.x || 0) + (chart.position?.w || 0))),
    height: Math.max(positive(c.height, 1080), ...charts.map(chart => (chart.position?.y || 0) + (chart.position?.h || 0))),
    gridSize: positive(c.gridSize, 20),
  };
}
export function applyScreenComponent(filters: any = {}, c: VisComponent) {
  const slot = COMPONENT_SLOT[c.category];
  if (!slot || c.category === 'sql_template') return filters;
  return { ...filters, components: { ...filters.components, [slot]: c.code },
    design: { ...filters.design, version: 1, canvas: { ...getCanvas(filters), ...filters.design?.canvas },
      componentSnapshots: { ...filters.design?.componentSnapshots, [slot]: snapshotComponent(c) } } };
}
export function compatible(c: VisComponent, chartType: string) {
  if (c.category === 'kpi_card') return ['big_number', 'big_number_trend', 'statistic', 'text_display'].includes(chartType);
  if (c.category === 'chart_style') return !chartType.startsWith('widget_') && (!c.chart_type || c.chart_type === chartType);
  return ['decoration_frame', 'color_theme'].includes(c.category);
}
export function applyChartComponent(config: any = {}, c: VisComponent, items: VisComponent[] = []) {
  const slot = COMPONENT_SLOT[c.category];
  if (!slot || c.category === 'sql_template') return config;
  const next = { ...config };
  const old = resolveComponent(config.vis?.refs?.[slot], config.vis?.snapshots?.[slot], items)?.config || {};
  // 清掉旧字模物化且未被用户改写的字段，查询、映射及显式覆盖原样保留。
  for (const [key, value] of Object.entries(visualConfig(old))) {
    if (JSON.stringify(next[key]) === JSON.stringify(value)) delete next[key];
  }
  next.vis = { ...config.vis, version: 1,
    refs: { ...config.vis?.refs, [slot]: c.code },
    snapshots: { ...config.vis?.snapshots, [slot]: snapshotComponent(c) } };
  return next;
}
export function resolveDashboardDesign(filters: any = {}, items: VisComponent[] = []) {
  const refs = filters.components || {};
  const snapshots = filters.design?.componentSnapshots || {};
  const styles: Record<string, any> = {};
  const missing: string[] = [];
  for (const slot of new Set([...Object.keys(refs), ...Object.keys(snapshots)])) {
    styles[slot] = resolveComponent(refs[slot], snapshots[slot], items);
    if (!styles[slot] && refs[slot]) missing.push(String(refs[slot]));
  }
  const theme = { ...styles.colorTheme, ...sanitizeStyle(filters.theme || {}) };
  const legacy = filters.theme || {};
  const lc = legacy.card || {};
  const legacyCard = visualConfig({ ...lc, cardBg: lc.cardBg || lc.background,
    cardBorder: lc.cardBorder || (lc.borderColor ? `1px solid ${lc.borderColor}` : undefined),
    glow: lc.glow || lc.boxShadow, labelColor: lc.labelColor || lc.textColor,
    showUnderline: lc.showUnderline ?? lc.titleDivider, underlineColor: lc.underlineColor || lc.titleDividerColor,
    titleBarBg: legacy.header?.gradient, titleColor: legacy.title?.color || legacy.header?.color });
  const card = { ...styles.cardStyle?.config, ...styles.decoration?.config, ...legacyCard };
  const background = { ...styles.background, ...pick({ ...legacy, backgroundColor: legacy.backgroundColor || legacy.background }, ['backgroundColor', 'backgroundImage', 'backgroundSize', 'overlay']),
    ...pick(filters.design?.canvas, ['backgroundColor', 'backgroundImage', 'backgroundSize', 'overlay']) };
  return { visual: theme as LocalVisual, card, background, missing };
}
export function resolveChartDesign(config: any = {}, screen: ReturnType<typeof resolveDashboardDesign>, items: VisComponent[]) {
  let inherited: Record<string, any> = { ...screen.card };
  let visual = { ...screen.visual };
  const refs = config.vis?.refs || {};
  const snapshots = config.vis?.snapshots || {};
  const missing: string[] = [];
  for (const slot of new Set([...Object.keys(refs), ...Object.keys(snapshots)])) {
    const style = resolveComponent(refs[slot], snapshots[slot], items);
    if (!style && refs[slot]) missing.push(String(refs[slot]));
    inherited = { ...inherited, ...style?.config };
    if (slot === 'colorTheme' && style) visual = { ...visual, ...style };
  }
  const overrides = { ...config };
  for (const key of VISUAL_KEYS) if (overrides[key] === '' || (Array.isArray(overrides[key]) && !overrides[key].length)) delete overrides[key];
  const merged = { ...inherited, ...overrides, axisStyle: { ...inherited.axisStyle, ...overrides.axisStyle } };
  if (merged.colorScheme?.length) visual.palette = merged.colorScheme;
  if (merged.axisStyle?.gridColor || merged.gridColor) visual.gridColor = merged.axisStyle?.gridColor || merged.gridColor;
  return { config: merged, visual, missing };
}
// 仅在完全没有持久外观时读取旧本机偏好；返回值只用于渲染，不参与保存。
export function legacyCanvasFilters(filters: any = {}) {
  if (filters.design || filters.theme || filters.components) return filters;
  try {
    const editor = localStorage.getItem('dashboard_canvas_bg');
    const screen = JSON.parse(localStorage.getItem('screen_settings') || '{}');
    const carousel = JSON.parse(localStorage.getItem('carousel_settings') || '{}');
    const backgroundColor = editor || screen.bgColor || carousel.backgroundColor;
    const backgroundImage = screen.bgImage || carousel.backgroundImage;
    return { ...filters, theme: pick({ backgroundColor, backgroundImage }, ['backgroundColor', 'backgroundImage']) };
  } catch { return filters; }
}
export function screenToCanvas(point: { x: number; y: number }, rect: { left: number; top: number }, scale: number) {
  return { x: (point.x - rect.left) / scale, y: (point.y - rect.top) / scale };
}
export function clampPosition(point: { x: number; y: number }, size: { w: number; h: number }, canvas: { width: number; height: number }, grid = 20) {
  return {
    x: Math.max(0, Math.min(Math.round(point.x / grid) * grid, canvas.width - size.w)),
    y: Math.max(0, Math.min(Math.round(point.y / grid) * grid, canvas.height - size.h)),
  };
}
export function isEditingTarget(target: EventTarget | null) {
  return target instanceof Element && !!target.closest('input,textarea,select,button,[contenteditable="true"],.cm-editor,[role="dialog"],[role="alertdialog"]');
}
export function chartData(chart: any) {
  try {
    const data = typeof chart.data_cache === 'string' ? JSON.parse(chart.data_cache) : chart.data_cache;
    if (Array.isArray(data?.columns) && Array.isArray(data?.rows)) return data;
  } catch { /* 旧缓存损坏时保留卡片 */ }
  return chart.config?.data || { columns: [], rows: [] };
}
export const chartDataKey = (data: any) => JSON.stringify(data || {});
const intersects = (a: Rect, b: Rect) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
export function applyLayout(charts: DashboardChart[], style: any, canvas: { width: number; height: number }) {
  const grid = sanitizeStyle(style).grid || {};
  const gap = 20, width = canvas.width;
  const positive = (v: any, fallback: number) => typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : fallback;
  const colsOf = (v: any) => Math.min(20, Math.max(1, Math.floor(positive(v, 3))));
  const widgets = charts.filter(c => c.chart_type.startsWith('widget_'));
  const normal = charts.filter(c => !c.chart_type.startsWith('widget_'));
  const positions: Record<number, Rect> = {};
  const occupied: Rect[] = widgets.map(c => c.position || { x: 0, y: 0, w: 300, h: 60 });
  const place = (chart: DashboardChart, rect: Rect) => {
    rect = { x: Math.max(0, Number.isFinite(rect.x) ? rect.x : 0), y: Math.max(0, Number.isFinite(rect.y) ? rect.y : 0), w: Math.min(width, Math.max(100, positive(rect.w, 400))), h: Math.max(80, Math.min(10000, positive(rect.h, 300))) };
    rect.x = Math.min(rect.x, width - rect.w);
    let collision = occupied.find(o => intersects(rect, o));
    while (collision) { rect.y = collision.y + collision.h + gap; collision = occupied.find(o => intersects(rect, o)); }
    occupied.push(rect); positions[chart.id] = rect;
  };
  if (Array.isArray(grid.positions) && grid.positions.length) {
    normal.forEach((c, i) => place(c, grid.positions[i] || { x: gap, y: canvas.height + i * 320, w: width - 2 * gap, h: 300 }));
  } else if (Array.isArray(grid.columns) && grid.columns.length >= 2) {
    const total = grid.columns.slice(0, 2).reduce((n: number, c: any) => n + positive(c.width, 1), 0);
    const primaryW = (width - 3 * gap) * (positive(grid.columns[0].width, 1) / total);
    if (normal[0]) place(normal[0], { x: gap, y: gap, w: primaryW, h: canvas.height - 2 * gap });
    normal.slice(1).forEach((c, i) => place(c, { x: primaryW + 2 * gap, y: gap + i * 320, w: width - primaryW - 3 * gap, h: 300 }));
  } else {
    const kpis = grid.kpiRow ? normal.filter(c => compatible({ category: 'kpi_card' } as VisComponent, c.chart_type)) : [];
    const rest = normal.filter(c => !kpis.includes(c));
    const kpiCount = colsOf(grid.kpiRow?.count || 4);
    const kpiHeight = positive(grid.kpiRow?.height, 180);
    kpis.forEach((c, i) => place(c, { x: gap + i % kpiCount * ((width - gap) / kpiCount),
      y: gap + Math.floor(i / kpiCount) * (kpiHeight + gap),
      w: (width - gap) / kpiCount - gap, h: kpiHeight }));
    let y = kpis.length ? Math.max(...occupied.map(p => p.y + p.h)) + gap : gap;
    let index = 0, row = 0;
    const rowSpecs = Array.isArray(grid.rows) && grid.rows.length ? grid.rows : null;
    const rows = rowSpecs?.length || colsOf(grid.rows || 3);
    const ratios = rowSpecs?.map((r: any) => positive(r.ratio, 1)) || Array(rows).fill(1);
    const totalRatio = ratios.reduce((n: number, r: number) => n + r, 0);
    const availableHeight = Math.max(180 * rows, canvas.height - y - gap * rows);
    while (index < rest.length) {
      const rowSpec = rowSpecs?.[row % rows];
      const cols = colsOf(rowSpec?.cols || grid.cols);
      const height = Math.max(180, availableHeight * ratios[row % rows] / totalRatio);
      for (let col = 0; col < cols && index < rest.length; col++, index++) {
        place(rest[index], { x: gap + col * ((width - gap) / cols), y, w: (width - gap) / cols - gap, h: height });
      }
      y = Math.max(y + height, ...occupied.map(p => p.y + p.h)) + gap; row++;
    }
  }
  return { positions, height: Math.max(canvas.height, ...occupied.map(p => p.y + p.h + gap)) };
}
