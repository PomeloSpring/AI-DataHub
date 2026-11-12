import { forwardRef, memo, useMemo, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import DashboardChart from './DashboardChart';
import { useDashboardStore, type Dashboard, type DashboardChart as ChartModel } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';
import { useThemeStore } from '@/stores/themeStore';
import { chartData, chartRect, chartZIndex, getCanvas, isChartHidden, resolveChartDesign, resolveDashboardDesign, visualConfig, legacyCanvasFilters, type Rect } from '@/lib/dashboardDesign';
import { paramBusinessName, paramDiagnosticHint } from '@/lib/dashboardParamAssembly';
import { PAGE_COLUMNS, PAGE_ROW, pageBreakpoint, pageGap, pageGapsOf, pagePixelRect, resolvePageLayout, type PageBreakpoint, type PageItem } from '@/lib/dashboardPageLayout';
import { thumbnailGeometry } from '@/lib/dashboardThumbnail';
import { fitBackgroundSize, gridBackgroundStyle } from '@/lib/canvasTheme';
import { cn } from '@/lib/utils';
import { ChartGlyph } from './ChartGlyph';
import { useMotion, boardMotionRef } from '@/hooks/useMotion';
import { canvasEntrance, enterClass, enterStaggerStyle, g2Animate } from '@/lib/motion';
import { TEMPLATE_DEMO_KEY } from '@/lib/dashboardDemo';

/** 样式模板图（内置演示数值）的角标提示：降级必须显式，用户得知道自己看的是样式示例而非取数结果 */
export const DEMO_DATA_HINT = '图表样式示例数据（模板内置，不取数）。要让这张卡出真数，在「数据与字段配置」里写 SQL 或语义查询；保存后示例数据与标记会自动清除。';

/** 参数值算不算“给了值”：空串 / 空数组 / 区间对象两端皆空 = 没给（与后端 bound_args 同口径） */
function hasParamValue(value: unknown): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === 'string') return value.trim() !== '';
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.values(value as Record<string, unknown>).some(hasParamValue);
  return true;
}

export const DashboardChartCard = memo(function DashboardChartCard({ chart, design, items, actions, draft = false, interactive, demoBadge, dashboardId, motionAnimate }: {
  chart: ChartModel; design: ReturnType<typeof resolveDashboardDesign>; items: VisComponent[];
  actions?: ReactNode; draft?: boolean; interactive?: boolean; demoBadge?: boolean;
  /** 所属看板：刷新/查询按钮按它发起重查询；缺省时回退图表自带的 dashboard_id */
  dashboardId?: number;
  /** 图表入场动画（板上动效预设的 chart 段）；缺省 = 不写 animate 键，与改造前逐字节相同 */
  motionAnimate?: Record<string, any> | false;
}) {
  const resolved = resolveChartDesign(chart.config, design, items, chart.chart_type);
  // 实时取数覆盖层：编辑器/看板浏览页/大屏/弹窗页四个渲染面共用一份刷新结果，
  // 否则非 store 宿主的页面点刷新只会看到旧缓存（历史缺陷）。
  // 覆盖层只在真实发起过查询时有条目，编辑态试运行同样能看到刷新后的数据。
  const liveCache = useDashboardStore(s => s.chartCache[chart.id]);
  const paramDiag = useDashboardStore(s => s.paramDiagnostics[chart.id]);
  // 本次查询真正提交过值的参数键（与下发口径同源 `{...paramValues, ...pageParamValues}`）：
  // 只有“给了值却没被这张图接住”才值得告警；首屏什么都没填 = 按数据集自身口径出全量，是预期态
  const paramValues = useDashboardStore(s => s.paramValues);
  const pageParamValues = useDashboardStore(s => s.pageParamValues);
  const dashboard = useDashboardStore(s => s.dashboards.find(d => d.id === (dashboardId ?? s.currentId)));
  const cfg = visualConfig(resolved.config);
  const widget = chart.chart_type.startsWith('widget_');
  const ws = resolved.config.widgetStyle || {};
  const dark = resolved.visual.mode === 'dark';
  const base = resolved.visual.viewBg || (resolved.visual.mode ? (dark ? '#111827' : '#ffffff') : 'hsl(var(--card))');
  // 编辑态控件默认 inert：表单元素吃掉 pointerdown 会让卡片既选不中也拖不动，
  // 「试运行」(interactive) 时放开，控件可真实联动。
  const inertWidget = draft && widget && interactive === false;
  // 数据集入参没给值时按数据集自身口径出数 —— 结果看着正常但"没被筛过"，必须在卡片上说出来
  // （但只说真正“已填未生效”的那类，文案用看板上的业务名，不用变量名）
  const paramHint = useMemo(() => {
    if (widget) return null;
    const merged: Record<string, unknown> = { ...paramValues, ...pageParamValues };
    const filledKeys = Object.keys(merged).filter(k => hasParamValue(merged[k]));
    const params = [...(dashboard?.params || []), ...(dashboard?.page_params || [])];
    return paramDiagnosticHint(paramDiag, {
      filledKeys,
      nameOf: u => paramBusinessName(u, { charts: dashboard?.charts, params }),
    });
  }, [widget, paramDiag, paramValues, pageParamValues, dashboard]);
  return <div className="adh-chart-card relative flex h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border" style={{
    height: '100%', minHeight: 0, boxSizing: 'border-box',
    background: (widget ? ws.backgroundColor : undefined) || cfg.cardBg || base,
    border: cfg.cardBorder || cfg.borderStyle,
    borderRadius: widget ? ws.borderRadius ?? cfg.cardRadius ?? cfg.borderRadius : cfg.cardRadius ?? cfg.borderRadius,
    boxShadow: widget && ws.boxShadow ? ws.boxShadow : typeof cfg.glow === 'string' ? cfg.glow : undefined,
    color: cfg.labelColor || resolved.visual.textColor || (dark ? '#e2e8f0' : '#111827'),
    ...(widget ? { borderColor: ws.borderColor, borderWidth: ws.borderWidth, opacity: ws.opacity } : {}),
  }}>
    {!widget && <div className="flex shrink-0 items-center justify-between gap-2 px-3 py-2 text-sm" style={{
      background: cfg.titleBarBg, color: cfg.titleColor || cfg.labelColor,
            fontSize: cfg.titleFontSize, fontWeight: cfg.titleFontWeight,
      borderBottom: cfg.showUnderline ? `1px solid ${cfg.underlineColor || 'hsl(var(--border))'}` : undefined,
    }}>
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="truncate font-medium">{chart.name}</span>
        {demoBadge && chart.config?.[TEMPLATE_DEMO_KEY] === true && <span
          title={DEMO_DATA_HINT}
          className="shrink-0 rounded border border-amber-500/40 bg-amber-500/10 px-1 text-[10px] font-normal leading-4 text-amber-600">示例数据</span>}
        {paramHint && <span
          title={paramHint.title}
          className="shrink-0 rounded border border-orange-500/40 bg-orange-500/10 px-1 text-[10px] font-normal leading-4 text-orange-700">{paramHint.text}</span>}
      </span>
      {actions}
    </div>}
    {widget && actions && <div className="absolute right-0 top-0 z-20">{actions}</div>}
    {resolved.missing.length > 0 && <span className="px-2 text-xs text-amber-600" title={resolved.missing.join(', ')}>字模缺失，保留原配置</span>}
    <div {...(inertWidget ? { inert: '' } as any : {})} className="relative min-h-0 flex-1 overflow-hidden" style={{ padding: widget ? ws.padding : 4, color: ws.textColor, fontSize: ws.fontSize }}>
      <DashboardChart chartType={chart.chart_type} data={chartData(liveCache ? { ...chart, data_cache: liveCache } : chart)} config={resolved.config}
        visual={{ ...resolved.visual, viewBg: cfg.cardBg || resolved.visual.viewBg }} chartId={draft ? undefined : chart.id}
        dashboardId={dashboardId ?? chart.dashboard_id} chartName={chart.name} draft={draft} motionAnimate={motionAnimate} />
    </div>
    {cfg.showCornerBrackets && ['left-0 top-0 border-l-2 border-t-2', 'right-0 top-0 border-r-2 border-t-2',
      'left-0 bottom-0 border-l-2 border-b-2', 'right-0 bottom-0 border-r-2 border-b-2'].map(c =>
      <i key={c} className={`pointer-events-none absolute h-3 w-3 ${c}`} style={{ borderColor: cfg.cornerAccent }} />)}
  </div>;
});
interface CanvasProps {
  /** id = 所属看板：刷新/查询按钮按它发起重查询（缩略图/纯预览面可缺省，卡片会回退图表自带的 dashboard_id） */
  dashboard: Pick<Dashboard, 'filters' | 'charts'> & { id?: number }; items?: VisComponent[];
  style?: CSSProperties; children?: ReactNode; showGrid?: boolean; draft?: boolean;
  interactive?: boolean;
  /** 示例数据角标：真实渲染面（编辑/大屏/页面）默认开启，缩略图/预览卡不走本组件所以不会贴标签 */
  demoBadge?: boolean;
  renderChart?: (chart: ChartModel, card: ReactNode, enter: string) => ReactNode;
  /**
   * 卡片入场（逐块错峰）。缺省 = 有人显式选过动效（板上挂载 / 个人预设）才开：
   * 看板卡片今天本就一次性落位，默认档不得开箱多一段动画（零变化硬约束）；
   * 编辑器里拖一张图也不该让整版重新闪一遍。显式传 true/false 覆盖该判定。
   */
  entrance?: boolean;
  /**
   * 属性面板「重新播放」的信号（递增 nonce）。编辑器主画布是 draft 态、平时不开入场，
   * 但这个 nonce 变化时会**临时**开一次入场让用户在真看板上看到节奏（只改 wrapper 的 class、
   * 不重挂图表）。与预览色块同源：都吃 enterClass(motion.enter) + 同一套 CSS 变量。
   */
  entranceReplay?: number;
}

/** 入场顺序按画布位置排（左上→右下），DOM 顺序保持原样：
 *  grid 用显式行列定位，改 DOM 顺序会连带影响 renderChart/renderItem 的既有断言。 */
function entranceRanks(entries: { id: number | string; rect: Rect }[]): Map<number | string, number> {
  const sorted = entries.slice().sort((a, b) => (a.rect.y - b.rect.y) || (a.rect.x - b.rect.x));
  return new Map(sorted.map((e, i) => [e.id, i]));
}

/**
 * 卡片出场的时机：等内容真的出现那一刻才播。
 *
 * 画布先挂载、取数后到是常态（首屏并行重查、自动刷新）：外壳在空卡上就把入场播完，
 * 用户看到的就只剩“图表在自己动”，卡片出场等于没有（用户反馈的真相）。
 * class 由空变非空只发生一次 → 只播一次，后续刷新不重播；控件无取数过程，挂载即有内容。
 */
function useCardEnter(enter: string, force = false) {
  // 只订阅“哪些图有结果”这一标量：直接选 chartCache 对象会让画布在每次刷新时重渲染整棵
  const readyKeys = useDashboardStore(s => Object.keys(s.chartCache).join(','));
  const ready = useMemo(() => new Set(readyKeys.split(',').filter(Boolean)), [readyKeys]);
  return (chart: ChartModel) => (!enter || force || chart.chart_type.startsWith('widget_') || !!chart.data_cache || ready.has(String(chart.id))
    ? enter : '');
}

/**
 * 「重新播放」窗口：nonce 变化时短暂返回 true（先置 false 再双帧置 true，保证连续两次点击也能重播），
 * 总时长 = 入场时长 + 错峰上限 + 缓冲。窗口内画布强制开入场、绕过 draft 关闭与内容就绪门（这是显式预览动作）。
 */
function useReplayWindow(nonce: number | undefined, totalMs: number): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!nonce) return;
    setOn(false);
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => { raf2 = requestAnimationFrame(() => setOn(true)); });
    const stop = setTimeout(() => setOn(false), totalMs + 200);
    return () => { cancelAnimationFrame(raf1); cancelAnimationFrame(raf2); clearTimeout(stop); };
  }, [nonce, totalMs]);
  return on;
}
export const DashboardCanvas = forwardRef<HTMLDivElement, CanvasProps>(function DashboardCanvas({
  dashboard, items = [], style, children, showGrid, draft, interactive, demoBadge = true, renderChart, entrance, entranceReplay,
}, ref) {
  const theme = useThemeStore(s => s.theme);
  // 板上挂载的动效预设优先于个人偏好（播放场景是"给别人看"，观感由这块板决定）
  const { motion, cssVars, origin } = useMotion({ board: boardMotionRef(dashboard.filters) });
  const replaying = useReplayWindow(entranceReplay, motion.enter.durationMs + (motion.enter.maxStaggerMs || 0));
  const enter = (canvasEntrance({ origin, enter: motion.enter, draft, entrance }) || replaying) ? enterClass(motion.enter) : '';
  const enterFor = useCardEnter(enter, replaying);
  const chartAnimate = g2Animate(motion.chart);
  const canvas = getCanvas(dashboard.filters, dashboard.charts);
  const design = useMemo(() => resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items, { theme }), [dashboard.filters, items, theme]);
  const bg = design.background;
  // 图层：阅读态跳过被隐藏的卡片；编辑态仍渲染（否则无法再取消隐藏）
  const chartRefs = (draft ? dashboard.charts : dashboard.charts.filter(c => !isChartHidden(c)))
    .slice().sort((a, b) => chartZIndex(a) - chartZIndex(b) || a.id - b.id);
  const ranks = useMemo(() => entranceRanks(chartRefs.map(c => ({ id: c.id, rect: chartRect(c) }))), [chartRefs]);
  return <div ref={ref} className="adh-canvas relative shrink-0 overflow-hidden" style={{
    width: canvas.width, height: canvas.height,
    backgroundColor: bg.backgroundColor || design.visual.viewBg || 'hsl(var(--muted))',
    backgroundImage: bg.backgroundImage, backgroundSize: bg.backgroundSize,
    boxShadow: '0 0 0 1px hsl(var(--border)), 0 10px 34px rgba(0,0,0,0.16)', ...cssVars, ...style,
  }}>
    {bg.overlay && <div className="pointer-events-none absolute inset-0" style={{ backgroundImage: bg.overlay }} />}
    {showGrid && <div className="pointer-events-none absolute inset-0" style={gridBackgroundStyle(canvas.gridSize, canvas.gridSize)} />}
    {design.missing.length > 0 && <div className="absolute right-2 top-1 z-10 text-xs text-amber-600">部分字模缺失，已保留原配置</div>}
    {chartRefs.map(chart => {
      const card = <DashboardChartCard chart={chart} items={items} design={design} draft={draft} interactive={interactive} demoBadge={demoBadge} dashboardId={dashboard.id} motionAnimate={chartAnimate} />;
      // 编辑器用 renderChart 自己套定位 wrapper（transform 定位），入场 class 传出让它挂到内层内容 div。
      // 不能挂到定位外层：入场关键帧动 transform，会覆盖外层 translate3d(x,y) 把卡片抽到左上角。
      if (renderChart) return renderChart(chart, card, enterFor(chart));
      const p = chartRect(chart);
      return <div key={chart.id} className={cn('absolute min-h-0', enterFor(chart))} style={{
        left: p.x, top: p.y, width: p.w, height: p.h, zIndex: chartZIndex(chart) || undefined,
        ...enterStaggerStyle(ranks.get(chart.id) ?? 0, motion.enter),
      }}>{card}</div>;
    })}
    {children}
  </div>;
});
export function FittedDashboardCanvas(props: CanvasProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.5);
  const canvas = getCanvas(props.dashboard.filters, props.dashboard.charts);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setScale(Math.max(0.01, Math.min(el.clientWidth / canvas.width, el.clientHeight / canvas.height)));
    const observer = new ResizeObserver(measure); observer.observe(el); measure();
    return () => observer.disconnect();
  }, [canvas.width, canvas.height]);
  return <div ref={ref} className="relative h-full min-h-0 w-full overflow-hidden">
    <DashboardCanvas {...props} style={{ position: 'absolute', left: '50%', top: '50%',
      transform: `translate(-50%, -50%) scale(${scale})`, transformOrigin: 'center', ...props.style }} />
  </div>;
}
export function ResponsiveDashboardCanvas({ dashboard, items = [], draft, interactive, demoBadge = true, breakpoint, renderItem, layout, onWidth, entrance, entranceReplay }: CanvasProps & {
  breakpoint?: PageBreakpoint; layout?: PageItem[]; onWidth?: (width: number) => void;
  renderItem?: (item: PageItem, card: ReactNode, style: CSSProperties, enter: string) => ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const theme = useThemeStore(s => s.theme);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => { setWidth(el.clientWidth); onWidth?.(el.clientWidth); };
    const observer = new ResizeObserver(measure); observer.observe(el); measure();
    return () => observer.disconnect();
  }, [onWidth]);
  const { motion, cssVars, origin } = useMotion({ board: boardMotionRef(dashboard.filters) });
  const replaying = useReplayWindow(entranceReplay, motion.enter.durationMs + (motion.enter.maxStaggerMs || 0));
  const enter = (canvasEntrance({ origin, enter: motion.enter, draft, entrance }) || replaying) ? enterClass(motion.enter) : '';
  const enterFor = useCardEnter(enter, replaying);
  const chartAnimate = g2Animate(motion.chart);
  const bp = breakpoint || pageBreakpoint(width);
  const design = useMemo(() => resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items, { theme }), [dashboard.filters, items, theme]);
  const result = useMemo(() => {
    try { return { items: layout || resolvePageLayout(dashboard, bp), error: '' }; }
    catch (error) { return { items: [], error: error instanceof Error ? error.message : '页面布局读取失败' }; }
  }, [dashboard, bp, layout]);
  const shown = draft ? result.items : result.items.filter(item => !isChartHidden(item.chart));
  // v3 行单位是纯 4px 像素，所以这里 rowGap 必须为 0（间距只当列间距用），
  // 否则 CSS 行间距会叠加到 gridAutoRows 上，一改间距就把所有卡片高度拉扇。
  const gap = pageGap(bp, pageGapsOf(dashboard.filters));
  const ranks = useMemo(() => entranceRanks(result.items.map(i => ({ id: i.chart.id, rect: i.rect }))), [result.items]);
  return <div ref={ref} className="adh-page-canvas min-w-0 w-full" data-breakpoint={bp} style={{
    backgroundColor: design.background.backgroundColor || design.visual.viewBg,
    backgroundImage: design.background.backgroundImage, backgroundSize: design.background.backgroundSize,
    ...cssVars,
  }}>
    {result.error ? <p role="alert" className="p-6 text-destructive">{result.error}</p> : !shown.length ? <p className="p-12 text-center text-muted-foreground">此看板暂无图表</p> :
      <div className="grid min-w-0" style={{
        gridTemplateColumns: `repeat(${PAGE_COLUMNS[bp]}, minmax(0, 1fr))`, gridAutoRows: PAGE_ROW,
        columnGap: gap, rowGap: 0,
      }}>
        {shown.map(item => {
          const { chart, rect } = item;
          const style = { gridColumn: `${rect.x + 1} / span ${rect.w}`, gridRow: `${rect.y + 1} / span ${rect.h}` };
          const card = <DashboardChartCard chart={chart} design={design} items={items} draft={draft} interactive={interactive} demoBadge={demoBadge} dashboardId={dashboard.id} motionAnimate={chartAnimate} />;
          const motionStyle = { ...style, ...enterStaggerStyle(ranks.get(chart.id) ?? 0, motion.enter) };
          // renderItem 自己套 wrapper（拖拽/缩放需要），入场 class 作为第 4 参传出，不丢给内置 wrapper
          return renderItem ? renderItem(item, card, motionStyle, enterFor(chart))
            : <div key={chart.id} data-chart-id={chart.id} data-enter={enterFor(chart) || undefined} className={cn('min-h-0 min-w-0', enterFor(chart))} style={motionStyle}>{card}</div>;
        })}
      </div>}
  </div>;
}

export function CanvasRulers({ canvas, scale, pan, active, visible = true }: {
  canvas: { width: number; height: number }; scale: number; pan: { x: number; y: number }; active?: Rect; visible?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ width: el.clientWidth, height: el.clientHeight });
    const observer = new ResizeObserver(measure); observer.observe(el); measure();
    return () => observer.disconnect();
  }, []);
  const step = [20, 50, 100, 200, 500, 1000, 2000].find(v => v * scale >= 64) || 2000;
  const ticks = (length: number, origin: number, extent: number, vertical: boolean) => {
    const minor = step / 5;
    const start = Math.max(0, Math.floor(-origin / scale / minor));
    const end = Math.min(Math.ceil(extent / minor), Math.ceil((length - origin) / scale / minor));
    return Array.from({ length: Math.max(0, end - start + 1) }, (_, i) => {
      const index = start + i, value = index * minor, pixel = origin + value * scale;
      const major = index % 5 === 0;
      return <g key={index}>
        <line x1={vertical ? (major ? 12 : 18) : pixel} y1={vertical ? pixel : (major ? 12 : 18)} x2={vertical ? 24 : pixel} y2={vertical ? pixel : 24} stroke="currentColor" opacity={major ? 0.7 : 0.4} />
        {major && <text x={vertical ? 10 : pixel + 3} y={vertical ? pixel - 3 : 10} transform={vertical ? `rotate(-90 10 ${pixel - 3})` : undefined} fontSize="10" fill="currentColor">{value}</text>}
      </g>;
    });
  };
  const x = (size.width - canvas.width * scale) / 2 + pan.x;
  const y = (size.height - canvas.height * scale) / 2 + pan.y;
  // 标尺可关：小缩放下刻度信息量低但占两行空间
  if (!visible) return null;
  return <div ref={ref} aria-label="画布坐标标尺" className="pointer-events-none absolute inset-0 z-30 text-muted-foreground">
    <svg className="absolute left-0 top-0 h-6 w-full border-b bg-background/95">
      {active && <rect x={x + active.x * scale} width={active.w * scale} height={24} fill="hsl(var(--primary) / 0.15)" />}
      {ticks(size.width, x, canvas.width, false)}
    </svg>
    <svg className="absolute left-0 top-0 h-full w-6 border-r bg-background/95">
      {active && <rect y={y + active.y * scale} height={active.h * scale} width={24} fill="hsl(var(--primary) / 0.15)" />}
      {ticks(size.height, y, canvas.height, true)}
    </svg>
    <span className="absolute left-0 top-0 flex h-6 w-6 items-center justify-center border-b border-r bg-background text-[9px]">px</span>
  </div>;
}
// 列表缩略图只绘制配置，绝不挂载图表或执行查询。
/**
 * 缩影图形已下沉到 `components/ChartGlyph.tsx`（看板列表与模板预览共用，
 * 避免模板弹窗 import 本模块形成环）。
 */

/**
 * 缩略图内容渲染器（列表效果图）。固定外框见 `DashboardThumbnailFrame`，
 * 两者共用 `lib/dashboardThumbnail.ts: thumbnailGeometry` 的几何算点。
 */
export function DashboardThumbnail({ dashboard, items = [] }: { dashboard: Pick<Dashboard, 'filters' | 'charts'>; items?: VisComponent[] }) {
  const theme = useThemeStore(s => s.theme);
  const design = resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items, { theme });
  const { isPage, pageItems, canvas, box } = useMemo(() => thumbnailGeometry(dashboard), [dashboard]);
  // 预览也是只读视图：被隐藏的图表不得混进缩略图（与阅读页同口径）
  const shownCharts = dashboard.charts.filter(c => !isChartHidden(c));
  const tiles = (isPage ? pageItems.filter(i => !isChartHidden(i.chart)).map(i => ({ chart: i.chart, rect: pagePixelRect(i.rect, 'desktop', pageGapsOf(dashboard.filters)) }))
    : shownCharts.map(c => ({ chart: c, rect: chartRect(c) })));
  return <div className="relative w-full overflow-hidden rounded-md border" style={{ aspectRatio: `${box.width} / ${box.height}`, backgroundColor: design.background.backgroundColor || design.visual.viewBg || '#f1f5f9', backgroundImage: design.background.backgroundImage, backgroundSize: fitBackgroundSize(design.background.backgroundSize, canvas) }}>
    {tiles.map(({ chart: c, rect: p }) => {
      const resolved = resolveChartDesign(c.config, design, items, c.chart_type);
      const palette = resolved.visual.palette?.length ? resolved.visual.palette : design.visual.palette;
      return <div key={c.id} className="absolute overflow-hidden rounded-sm border border-black/10 p-1" style={{ left: `${p.x / box.width * 100}%`, top: `${p.y / box.height * 100}%`,
        width: `${p.w / box.width * 100}%`, height: `${p.h / box.height * 100}%`, background: visualConfig(resolved.config).cardBg || resolved.visual.viewBg || (resolved.visual.mode === 'dark' ? '#111827' : '#ffffff') }}>
        <div className="mb-0.5 h-1 w-1/2 rounded opacity-40" style={{ background: palette?.[0] || '#475569' }} />
        <div className="flex h-[calc(100%-4px)] items-stretch"><ChartGlyph chartType={c.chart_type} palette={palette || []} /></div>
      </div>;
    })}
    {!tiles.length && <span className="absolute inset-0 flex items-center justify-center text-xs text-slate-400">空白画布</span>}
  </div>;
}
