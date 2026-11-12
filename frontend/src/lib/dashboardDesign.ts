import { isVisUsable, type VisCategory, type VisComponent } from '@/api/visLibrary';
import { sanitizeMotion } from './motion';
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
// barFade/barCap/axisLabelSize = AS 风格柱图与字模旋钮(素材 design-service 预设的「上深下浅同色系渐隐 +
// 4px 柱顶帽」与 chartTypography.axisLabelSize/legendSize=12px),
// 由 DashboardChart 的 bar/timeseries_bar/waterfall/默认柱图分支经 barBodyStyle/withBarCap/axisLabelStyle 真实消费。
// areaFade = 面积图竖向渐隐开关（未配=默认开、数值=顶部 alpha、false=平涂），
// 不登记进白名单会被入库即剥（"存了不生效"那类缺陷）
const VISUAL_KEYS = ['colorScheme', 'gradient', 'barFade', 'barCap', 'axisLabelSize', 'borderRadius', 'showLabel', 'smooth', 'lineWidth',
  'areaFill', 'areaFade', 'areaOpacity', 'innerRadius', 'legend', 'thresholds', 'gridColor', 'valueColor', 'labelColor',
  'cardBg', 'cardBorder', 'glow', 'cornerAccent', 'showCornerBrackets', 'borderStyle',
  'titleBarBg', 'titleColor', 'showUnderline', 'underlineColor', 'titleFontSize', 'titleFontWeight', 'cardRadius',
  // 分析画布字模旋钮(DataProfileCard/GraphCard/AnimatedTimeChart/DataGrid 消费,纯展示参数)
  'topN', 'bins', 'speedMs', 'rowHeight', 'headerBg', 'zebraBg',
  // 表值图（HTML 表格）字模旋钮：以前表头色/行高/表格字号在渲染里写死，挂字模不生效（§2“存了不生效”）
  'headerColor', 'tableFontSize',
  'nodeBg', 'nodeBorder', 'edgeColor', 'direction', 'nodeWidth', 'nodeHeight', 'ranksep', 'nodesep'];
const TOP_KEYS = ['backgroundColor', 'backgroundImage', 'backgroundSize', 'overlay',
  'mode', 'palette', 'viewBg', 'popoverBg', 'textColor', 'subTextColor', 'gridColor'];
const WIDGET_KEYS = ['backgroundColor', 'textColor', 'borderColor', 'borderWidth', 'borderStyle',
  'borderRadius', 'padding', 'fontSize', 'fontWeight', 'opacity', 'boxShadow', 'verticalAlign', 'horizontalAlign',
  // 紧凑排版旋钮: 控件内层 input 与 label 真实消费(见 DashboardChart 控件分支), 漏登记会被入库即剥
  'lineHeight', 'height', 'gap', 'labelWidth'];
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
  // 控件样式字模的旋钮走 WIDGET_KEYS 白名单（backgroundColor/height/gap/labelWidth…）；
  // 不登记就会被入库即剥，表现为“选了控件字模没反应”。
  if (style?.widgetStyle) result.widgetStyle = pick(style.widgetStyle, WIDGET_KEYS);
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
  // 动效字模的 style_config 是四段结构（enter/chart/transition/reveal），要走自己的净化器：
  // 交给 sanitizeStyle 会把四段当未知键剥干净，下架后的快照兜底直接失效（“存了不生效”同类）
  style_config: c.category === 'theme_pack' ? sanitizePack(c.style_config)
    : c.category === 'motion_preset' ? sanitizeMotion(c.style_config).spec
    : sanitizeStyle(c.style_config),
});
export const COMPONENT_SLOT: Partial<Record<VisCategory, string>> = {
  screen_background: 'background', color_theme: 'colorTheme', kpi_card: 'cardStyle',
  decoration_frame: 'decoration', layout_template: 'layout', chart_style: 'chartStyle',
  // 控件样式字模：写 style_config.widgetStyle 段，汇入 cfg.widgetStyle（与图表 config 分命名空间）
  widget_style: 'widgetStyle',
  theme_pack: 'themePack',
  // 动效预设：板级挂载（入场/切板/图表入场由 lib/motion 统一解析），不属单图旋钮
  motion_preset: 'motion',
};
// 主题包结构: { mode, palette, background, card, decoration, charts{default,<type>}, widgets{default,<type>} }
// 卡片作用域样式: 把 borderRadius 归一为 cardRadius, 避免与图表标记的圆角(borderRadius)冲突。
function toCardStyle(v: any): Record<string, any> {
  const out = visualConfig(v);
  if (out.cardRadius === undefined && out.borderRadius !== undefined) out.cardRadius = out.borderRadius;
  delete out.borderRadius;
  return out;
}
export function sanitizePack(style: any): Record<string, any> {
  const s = style || {};
  const map = (src: any, fn: (v: any) => any) => {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(src || {})) if (v && typeof v === 'object' && !Array.isArray(v)) out[k] = fn(v);
    return out;
  };
  return {
    ...pick(s, ['viewBg', 'popoverBg', 'textColor', 'subTextColor', 'gridColor']),
    mode: s.mode === 'dark' || s.mode === 'light' ? s.mode : undefined,
    palette: Array.isArray(s.palette) ? s.palette.filter((v: unknown) => typeof v === 'string' && safeString(v)).slice(0, 64) : undefined,
    background: pick(s.background, ['backgroundColor', 'backgroundImage', 'backgroundSize', 'overlay']),
    card: toCardStyle(s.card || {}),
    decoration: toCardStyle(s.decoration || {}),
    charts: map(s.charts, visualConfig),
    widgets: map(s.widgets, (v) => pick(v, WIDGET_KEYS)),
  };
}
/** 主题 id 的**精确**主题包（不含 datafoundry 兜底）。 */
export const themePackExact = (theme: string | undefined, items: VisComponent[]): VisComponent | undefined =>
  !!theme && items.find(c => c.category === 'theme_pack' && c.code === `tp_${theme}`) || undefined;

export function themePackFor(theme: string | undefined, items: VisComponent[]): VisComponent | undefined {
  // 未指定主题（如嵌入 SDK 无全局主题）不套任何包，走组件默认渲染
  if (!theme) return undefined;
  // 消费方传入的 items 已是"在用"口径（useVisLibrary 默认剔除下架/禁用），这里不再二次判定
  return themePackExact(theme, items)
    || items.find(c => c.category === 'theme_pack' && c.code === 'tp_datafoundry');
}

/**
 * 主题 id ↔ UI 字模库主题包（`tp_<theme>`）的可用性裁决 —— 系统主题菜单的唯一判断口径。
 *
 * - `blocked`：包在库里但已禁用或已下架 → 该主题**不可选**。禁用是"停用但保留"，
 *   不得静默换成别套主题包（那会出现"外壳还是亮色、图表已是另一套配色"的半截状态）；
 * - `absent`：库里根本没有这个包（未 seed 的存量库、只存在于 CSS 的主题）→ 维持可选，
 *   主题外壳由 globals.css 调色板决定，与字模库无关；
 * - `usable`：包在用。
 *
 * 判定必须拿**全量**清单（`useVisLibrary(true, true)`），否则区分不出"被禁用"与"不存在"。
 */
export function themePackAvailability(theme: string, items: VisComponent[]):
  { state: 'usable' | 'blocked' | 'absent'; pack?: VisComponent; reason?: string } {
  const pack = items.find(c => c.category === 'theme_pack' && c.code === `tp_${theme}`);
  if (!pack) return { state: 'absent' };
  if (isVisUsable(pack)) return { state: 'usable', pack };
  const state = !pack.is_active ? '已下架' : '已在 UI 字模库禁用';
  return { state: 'blocked', pack, reason: `主题包「${pack.name}」${state}` };
}
export function resolveComponent(ref: unknown, snapshot: VisSnapshot | undefined, items: VisComponent[]) {
  // 字模库是事实源（“活字”）：命中在用字模就按最新定义渲染 —— 改字模应当即时反映到所有已挂载的看板，
  // 不需要重挂/重存（ui-vis-library-style.md §3）。快照**只**在字模下架/缺失时兜底，保证历史大屏不花屏。
  const component = items.find(c => String(c.id) === String(ref) || (!!ref && c.code === ref))
    || (snapshot?.code ? items.find(c => c.code === snapshot.code) : undefined);
  if (component) return sanitizeStyle(component.style_config);
  if (snapshot?.version === 1 && snapshot.style_config) return sanitizeStyle(snapshot.style_config);
  return undefined;
}
const finite = (v: unknown, fallback: number) => typeof v === 'number' && Number.isFinite(v) ? v : fallback;
// 非法尺寸（≤0 / NaN）回落到默认尺寸；合法的小尺寸原样保留。
const positive = (v: unknown, fallback: number) => typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : fallback;
const DEFAULT_CHART_SIZE = { w: 400, h: 300 };

/**
 * 尺寸下限：**只留防塌陷的 1×1**，其余交给人主动控制。
 *
 * 曾经按类型分级设下限（按钮 48×24、日期范围 220×32、图表 200×150…），但用户把它拖到“最小”
 * 之后最终渲染仍然偏大 —— 一个搜索按钮占 227×64。现在下限退到网格量子级，想多小就多小；
 * 唯一保留的是不能压成 0/负（否则卡片彻底不渲染，且读取时会跳回自然尺寸造成前后不一致）。
 */
export function minSizeOf(_chartType?: string): { w: number; h: number } {
  return { w: 1, h: 1 };
}

/**
 * 参数控件默认尺寸：基准口径 = 「设备数据流」看板的现网尺寸（人工调优过、观感对齐）。
 *
 * 两条口径同时成立：
 * ① **高度统一**（`WIDGET_DEFAULT_HEIGHT`，10 行 = 40px）—— 同一排控件上下对齐；
 *    旧默认按类型给 24/28/32/64px，落到页面网格就是 6/7/8/16 行混排，看着参差（本次反馈）。
 * ② **宽度按控件分别配置** —— 按钮窄、日期区间宽，因为内容形态不同。
 *
 * 数值是**桌面 48 列制**的列数换算结果（`px = 列数 × 列宽 − 间距`，列宽 = (1440+16)/48）：
 * 按钮 3 列、文本/下拉 5 列、日期选择器 9 列、日期区间 18 列。日期区间必须给到 18 列：
 * 原生日期框有内在最小宽、压不下去，宽度不够就换行，而卡片高度只有 40px → 下端被裁掉，
 * 用户看到的就是“只有开始日期、没有结束日期”（2026-11-10 反馈）。
 * 与 `defaultPageSpan` 的跳列数互为往返（有用例钉住，防两处漂移）。
 */
export const WIDGET_DEFAULT_HEIGHT = 40;
/** 内容不能被压成一条的控件的默认高（其余控件沿用 WIDGET_DEFAULT_HEIGHT）。
 * 决策建议卡要装命中路径 + 建议正文 + 依据表，沿用 40px 就是“只看到一行字”。 */
export const WIDGET_DEFAULT_HEIGHT_BY_TYPE: Record<string, number> = { widget_decision: 220 };
/** `widget_*` 里的**内容型**控件：不是一行表单控件，因此不参与「同一排控件上下对齐」
 * 的高度基线（见 dashboardParamAssembly 用例）。新增成员必须同时在
 * `WIDGET_DEFAULT_HEIGHT_BY_TYPE` 登记高度，否则它会悄悄漂回 40px。 */
export const CONTENT_WIDGET_TYPES = Object.keys(WIDGET_DEFAULT_HEIGHT_BY_TYPE);
export const WIDGET_DEFAULT_WIDTH: Record<string, number> = {
  widget_search: 75,        // 3 列
  widget_reset: 75,         // 3 列
  widget_export: 75,        // 3 列
  widget_label: 105,        // 4 列
  widget_text: 135,         // 5 列
  widget_number: 135,       // 5 列
  widget_select: 135,       // 5 列
  widget_multi_select: 135, // 5 列
  widget_decision: 375,     // 14 列（一条建议 + 依据表不至于挤成一堆）
  widget_date: 257,         // 9 列（单个日期 + 预设下拉）
  widget_daterange: 530,    // 18 列（起止两个原生日期框 + 预设下拉）
};

/**
 * 自然尺寸：生成默认布局时用的**紧凑起点**，不是下限。
 *
 * 自动布局（从未被手动放置过的控件）会用它；用户一旦手动放置，以显式位置为准。
 * 只用于让“没碰过”的控件一开始就不臃肿，不影响任何人把它拖大或拖小。
 */
export function naturalSizeOf(chartType?: string): { w: number; h: number } {
  if (!chartType || !chartType.startsWith('widget_')) return DEFAULT_CHART_SIZE;
  // 未知控件类型不编新尺寸：按最宽的可输入控件给，宁可宽一点也不把内容裁掉
  return { w: WIDGET_DEFAULT_WIDTH[chartType] ?? WIDGET_DEFAULT_WIDTH.widget_daterange,
    h: WIDGET_DEFAULT_HEIGHT_BY_TYPE[chartType] ?? WIDGET_DEFAULT_HEIGHT };
}

/**
 * 内容不可压缩控件的**可读下限**（低于此宽就会“只展示一半”，不是拖小一点的代价）。
 * 不当硬下限用（下限依旧交给人控制，见 `minSizeOf`），只用于编辑态显式提醒 + 一键补宽。
 */
export const MIN_READABLE_WIDTH: Record<string, number> = { widget_daterange: 420, widget_date: 200 };
export const minReadableWidth = (chartType?: string): number => MIN_READABLE_WIDTH[chartType || ''] || 0;

export function chartRect(chart: { position?: Partial<Rect>; chart_type?: string }): Rect {
  const widget = chart.chart_type?.startsWith('widget_');
  const p = chart.position || {};
  const natural = widget ? naturalSizeOf(chart.chart_type) : DEFAULT_CHART_SIZE;
  // 只做「非法值回落」，不再把已存的合法小尺寸抬到下限：历史/导入/API 写入的小尺寸控件必须保持原样，
  // 尺寸下限统一由交互层（minSizeOf）把关，否则任何途径都改不小。
  return { x: Math.max(0, finite(p.x, 0)), y: Math.max(0, finite(p.y, 0)),
    w: positive(p.w, natural.w), h: positive(p.h, natural.h) };
}
export function getCanvas(filters: any = {}, charts: Pick<DashboardChart, 'position'>[] = []) {
  const c = filters?.design?.canvas || {};
  const positions = charts.map(chartRect);
  return {
    width: Math.max(320, finite(c.width, 1920), ...positions.map(p => p.x + p.w)),
    height: Math.max(180, finite(c.height, 1080), ...positions.map(p => p.y + p.h)),
    gridSize: Math.max(1, finite(c.gridSize, 20)),
  };
}
export function applyScreenComponent(filters: any = {}, c: VisComponent) {
  const slot = COMPONENT_SLOT[c.category];
  if (!slot) return filters;
  const snapshots = { ...filters.design?.componentSnapshots, [slot]: snapshotComponent(c) };
  const design = { ...filters.design, version: 1, canvas: { ...getCanvas(filters), ...filters.design?.canvas }, componentSnapshots: snapshots };
  if (c.category === 'theme_pack') design.themePack = c.code;
  return { ...filters, components: c.category === 'theme_pack' ? filters.components : { ...filters.components, [slot]: c.code }, design };
}
/**
 * 摘除板上挂载的某类字模（清引用 + 清快照 + 清板级差量）。
 *
 * 只删 `components[slot]` 而留着快照，解析口会按快照继续生效 —— 那就是「摘了但没摘掉」。
 * 动效预设的「不挂载」入口用它，其他板级字模取消挂载同样适用。
 */
export function removeScreenComponent(filters: any = {}, category: VisCategory) {
  const slot = COMPONENT_SLOT[category];
  if (!slot) return filters;
  const components = { ...filters.components };
  delete components[slot];
  let design: Record<string, any> | undefined = filters.design;
  if (design) {
    const snapshots = { ...design.componentSnapshots };
    delete snapshots[slot];
    design = { ...design, componentSnapshots: snapshots };
    delete design.motionOverride;
  }
  return { ...filters, components, ...(design ? { design } : {}) };
}
/**
 * 字模适用类型：`chart_type` 为空 = 不限；支持逗号分隔多类型（如按钮族 widget_search,widget_reset,widget_export）。
 * 历史缺陷：seed 登记了 `widget_input` / `widget_button` 两个**不存在的类型名**，
 * compatible 永远为 false，字模在管理页看得到、在编辑器永远挂不上（空承诺）。
 */
export function appliesToType(c: Pick<VisComponent, 'chart_type'>, chartType: string) {
  const types = (c.chart_type || '').split(',').map(s => s.trim()).filter(Boolean);
  return !types.length || types.includes(chartType);
}
export function compatible(c: VisComponent, chartType: string) {
  // 动效预设只给板级挂：单图挂它会出现「板上 + 单图」两套动效口径，而 chart.appear 本应
  // 由 useMotion 统一注入 spec，不是逐图旋钮（见 lib/motion.ts 头部说明）
  if (c.category === 'motion_preset') return false;
  if (c.category === 'kpi_card') return ['big_number', 'big_number_trend', 'statistic', 'text_display'].includes(chartType);
  // 控件类只能挂 widget_style 字模，不能挂 chart_style（两套旋钮命名空间不同，混挂会互相覆盖）
  if (c.category === 'chart_style') return !chartType.startsWith('widget_') && appliesToType(c, chartType);
  if (c.category === 'widget_style') return chartType.startsWith('widget_') && appliesToType(c, chartType);
  return ['decoration_frame', 'color_theme'].includes(c.category);
}
export function applyChartComponent(config: any = {}, c: VisComponent, items: VisComponent[] = []) {
  const slot = COMPONENT_SLOT[c.category];
  if (!slot) return config;
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
export function resolveDashboardDesign(filters: any = {}, items: VisComponent[] = [], opts?: { theme?: string }) {
  const refs = filters.components || {};
  const snapshots = filters.design?.componentSnapshots || {};
  const styles: Record<string, any> = {};
  const missing: string[] = [];
  for (const slot of new Set([...Object.keys(refs), ...Object.keys(snapshots)])) {
    styles[slot] = resolveComponent(refs[slot], snapshots[slot], items);
    if (!styles[slot] && refs[slot]) missing.push(String(refs[slot]));
  }
  // 主题包: 显式引用/快照优先; 无显式设计时跟随用户全局主题(内存回退, 不落库)。
  const packRef = filters.design?.themePack || refs.themePack;
  const snap = snapshots.themePack;
  const validSnapshot = snap?.version === 1 && snap.category === 'theme_pack' && snap.style_config;
  const component = items.find(c => c.category === 'theme_pack' && (c.code === packRef || String(c.id) === String(packRef)));
  let pack: Record<string, any> | undefined;
  if (packRef || validSnapshot) {
    // 与 resolveComponent 同口径：库优先，只有字模下架/缺失才回退快照（早期残缺色板快照不再遮蔽完整定义）
    const raw = component?.style_config || (validSnapshot ? snap.style_config : undefined);
    pack = raw ? sanitizePack(raw) : undefined;
    if (!pack && packRef) missing.push(String(packRef));
  } else {
    const comp = themePackFor(opts?.theme, items);
    pack = comp ? sanitizePack(comp.style_config) : undefined;
  }
  const theme = { ...pick(pack, ['mode', 'palette', 'viewBg', 'popoverBg', 'textColor', 'subTextColor', 'gridColor']),
    ...styles.colorTheme, ...sanitizeStyle(filters.theme || {}) };
  const legacy = filters.theme || {};
  const lc = legacy.card || {};
  const legacyCard = toCardStyle({ ...lc, cardBg: lc.cardBg || lc.background,
    cardBorder: lc.cardBorder || (lc.borderColor ? `1px solid ${lc.borderColor}` : undefined),
    glow: lc.glow || lc.boxShadow, labelColor: lc.labelColor || lc.textColor,
    showUnderline: lc.showUnderline ?? lc.titleDivider, underlineColor: lc.underlineColor || lc.titleDividerColor,
    titleBarBg: legacy.header?.gradient, titleColor: legacy.title?.color || legacy.header?.color });
  const card = { ...pack?.card, ...pack?.decoration, ...toCardStyle(styles.cardStyle?.config), ...toCardStyle(styles.decoration?.config), ...legacyCard };
  const background = { ...pack?.background, ...styles.background, ...pick({ ...legacy, backgroundColor: legacy.backgroundColor || legacy.background }, ['backgroundColor', 'backgroundImage', 'backgroundSize', 'overlay']),
    ...pick(filters.design?.canvas, ['backgroundColor', 'backgroundImage', 'backgroundSize', 'overlay']) };
  return { visual: theme as LocalVisual, card, background, missing, pack };
}
export function resolveChartDesign(config: any = {}, screen: ReturnType<typeof resolveDashboardDesign>, items: VisComponent[], chartType = '') {
  const packChart = { ...screen.pack?.charts?.default, ...screen.pack?.charts?.[chartType] };
  const packWidget = { ...screen.pack?.widgets?.default, ...screen.pack?.widgets?.[chartType] };
  let inherited: Record<string, any> = { ...packChart, ...screen.card };
  // 控件字模（widget_style）单独累积：优先级 主题包 widgets < 控件字模 < 图表显式 widgetStyle
  let componentWidget: Record<string, any> = {};
  let visual = { ...screen.visual };
  const refs = config.vis?.refs || {};
  const snapshots = config.vis?.snapshots || {};
  const missing: string[] = [];
  for (const slot of new Set([...Object.keys(refs), ...Object.keys(snapshots)])) {
    const style = resolveComponent(refs[slot], snapshots[slot], items);
    if (!style && refs[slot]) missing.push(String(refs[slot]));
    if (slot === 'widgetStyle') { componentWidget = { ...componentWidget, ...(style?.widgetStyle || {}) }; continue; }
    inherited = { ...inherited, ...(['cardStyle', 'decoration'].includes(slot) ? toCardStyle(style?.config) : style?.config) };
    if (slot === 'colorTheme' && style) visual = { ...visual, ...style };
  }
  const overrides = { ...config };
  for (const key of VISUAL_KEYS) if (overrides[key] === '' || (Array.isArray(overrides[key]) && !overrides[key].length)) delete overrides[key];
  const merged = { ...inherited, ...overrides, axisStyle: { ...inherited.axisStyle, ...overrides.axisStyle },
    ...(chartType.startsWith('widget_') ? { widgetStyle: { ...packWidget, ...componentWidget, ...overrides.widgetStyle } } : {}) };
  if (merged.colorScheme?.length) visual.palette = merged.colorScheme;
  if (merged.axisStyle?.gridColor || merged.gridColor) visual.gridColor = merged.axisStyle?.gridColor || merged.gridColor;
  return { config: merged, visual, missing, widget: { ...packWidget, ...componentWidget } };
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
    const theme = pick({ backgroundColor, backgroundImage }, ['backgroundColor', 'backgroundImage']);
    return Object.keys(theme).length ? { ...filters, theme } : filters;
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
export type SnapGuide = { axis: 'x' | 'y'; value: number };
// 吸附阈值以屏幕像素计算；邻居边缘/中心优先，远离吸附点时连续移动。
export function snapCanvasRect(raw: Rect, canvas: { width: number; height: number }, others: Rect[], grid: number,
  scale: number, resize = false, enabled = true, minimum = { w: 200, h: 150 }) {
  const rect = { ...raw };
  const guides: SnapGuide[] = [];
  const tolerance = 6 / Math.max(0.05, scale);
  for (const axis of ['x', 'y'] as const) {
    const size = axis === 'x' ? 'w' : 'h';
    const extent = axis === 'x' ? canvas.width : canvas.height;
    const max = Math.max(0, extent - rect[size]);
    if (!resize) rect[axis] = Math.max(0, Math.min(rect[axis], max));
    else rect[size] = Math.max(minimum[size], Math.min(rect[size], extent - rect[axis]));
    if (!enabled) continue;
    const anchors = resize ? [rect[axis] + rect[size]] : [rect[axis], rect[axis] + rect[size] / 2, rect[axis] + rect[size]];
    const targets = [0, extent / 2, extent, ...others.flatMap(p => [p[axis], p[axis] + p[size] / 2, p[axis] + p[size]])];
    let best: { delta: number; target: number } | undefined;
    for (const anchor of anchors) for (const target of targets) {
      const delta = target - anchor;
      const next = resize ? rect[size] + delta : rect[axis] + delta;
      if (resize ? next < minimum[size] || rect[axis] + next > extent : next < 0 || next > max) continue;
      if (Math.abs(delta) <= tolerance && (!best || Math.abs(delta) < Math.abs(best.delta))) best = { delta, target };
    }
    if (best) {
      rect[resize ? size : axis] += best.delta;
      guides.push({ axis, value: best.target });
    } else if (grid > 0) {
      const value = resize ? rect[axis] + rect[size] : rect[axis];
      const delta = Math.round(value / grid) * grid - value;
      const next = rect[resize ? size : axis] + delta;
      if (Math.abs(delta) <= tolerance && (resize ? next >= minimum[size] && rect[axis] + next <= extent : next >= 0 && next <= max)) {
        rect[resize ? size : axis] = next;
      }
    }
  }
  return { rect, guides };
}
// 整屏切换只移除视觉覆盖；保留查询、字段映射、交互参数及位置。
export function clearChartVisualOverrides(config: any = {}) {
  const next = { ...config };
  for (const key of VISUAL_KEYS) delete next[key];
  if (next.axisStyle) { next.axisStyle = { ...next.axisStyle }; delete next.axisStyle.gridColor; }
  if (next.widgetStyle) {
    next.widgetStyle = { ...next.widgetStyle };
    for (const key of WIDGET_KEYS) delete next.widgetStyle[key];
  }
  delete next.vis;
  return next;
}
export function libraryThemeFilters(filters: any = {}, component?: VisComponent) {
  const next = { ...filters, design: { ...filters.design }, components: { ...filters.components } };
  delete next.theme;
  const snapshots = { ...next.design.componentSnapshots };
  for (const slot of ['background', 'cardStyle', 'decoration', 'colorTheme', 'themePack']) {
    delete next.components[slot]; delete snapshots[slot];
  }
  delete next.design.themePack;
  next.design.componentSnapshots = snapshots;
  next.design.canvas = { ...next.design.canvas };
  for (const key of ['backgroundColor', 'backgroundImage', 'backgroundSize', 'overlay']) delete next.design.canvas[key];
  return component ? applyScreenComponent(next, component) : next;
}
export function isEditingTarget(target: EventTarget | null) {
  return target instanceof Element && !!target.closest('input,textarea,select,button,[contenteditable="true"],.cm-editor,[role="dialog"],[role="alertdialog"]');
}
/** 图层属性（存 config，免建表免迁移）：叠放顺序 / 隐藏 / 锁定 */
export const chartZIndex = (chart?: { config?: Record<string, any> }) =>
  Number.isFinite(chart?.config?.zIndex) ? Number(chart?.config?.zIndex) : 0;
export const isChartHidden = (chart?: { config?: Record<string, any> }) => chart?.config?.hidden === true;
export const isChartLocked = (chart?: { config?: Record<string, any> }) => chart?.config?.locked === true;

export function chartData(chart: any) {
  try {
    const data = typeof chart.data_cache === 'string' ? JSON.parse(chart.data_cache) : chart.data_cache;
    if (Array.isArray(data?.columns) && Array.isArray(data?.rows)) return data;
  } catch { /* 旧缓存损坏时保留卡片 */ }
  return chart.config?.data || { columns: [], rows: [] };
}

/**
 * 表值图每页行数：只有这三档，上限与服务端 `es_pagination.PAGE_SIZE_MAX` 同口径。
 * 放 lib 而不是组件里：发起重查的 store 与渲染分页条的组件必须共用一份算法，
 * 两处各推一套就会出现“按 20 取、按 50 显”的页错位。
 */
export const TABLE_VALUE_PAGE_SIZES = [20, 50, 100];
export const TABLE_VALUE_PAGE_SIZE_MAX = 100;
export const TABLE_VALUE_PAGE_SIZE_DEFAULT = 20;

/** 图表配置 → 生效页大小（档位之外收敛到 100，不放开扫行规模） */
export function pageSizeOf(cfg: Record<string, any> | undefined): number {
  const raw = Math.floor(Number(cfg?.pageSize));
  const n = Number.isFinite(raw) && raw > 0 ? raw : TABLE_VALUE_PAGE_SIZE_DEFAULT;
  return Math.min(n, TABLE_VALUE_PAGE_SIZE_MAX);
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
