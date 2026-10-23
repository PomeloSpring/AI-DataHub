import { forwardRef, memo, useMemo, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import DashboardChart from './DashboardChart';
import type { Dashboard, DashboardChart as ChartModel } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';
import { useThemeStore } from '@/stores/themeStore';
import { chartData, chartRect, chartZIndex, getCanvas, isChartHidden, resolveChartDesign, resolveDashboardDesign, visualConfig, legacyCanvasFilters, type Rect } from '@/lib/dashboardDesign';
import { PAGE_COLUMNS, PAGE_ROW, pageBreakpoint, pageCanvasSize, pageGap, pagePixelRect, resolvePageLayout, type PageBreakpoint, type PageItem } from '@/lib/dashboardPageLayout';
import { gridBackgroundStyle } from '@/lib/canvasTheme';

export const DashboardChartCard = memo(function DashboardChartCard({ chart, design, items, actions, draft = false, interactive }: {
  chart: ChartModel; design: ReturnType<typeof resolveDashboardDesign>; items: VisComponent[];
  actions?: ReactNode; draft?: boolean; interactive?: boolean;
}) {
  const resolved = resolveChartDesign(chart.config, design, items, chart.chart_type);
  const cfg = visualConfig(resolved.config);
  const widget = chart.chart_type.startsWith('widget_');
  const ws = resolved.config.widgetStyle || {};
  const dark = resolved.visual.mode === 'dark';
  const base = resolved.visual.viewBg || (resolved.visual.mode ? (dark ? '#111827' : '#ffffff') : 'hsl(var(--card))');
  // 编辑态控件默认 inert：表单元素吃掉 pointerdown 会让卡片既选不中也拖不动，
  // 「试运行」(interactive) 时放开，控件可真实联动。
  const inertWidget = draft && widget && interactive === false;
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
    }}><span className="truncate font-medium">{chart.name}</span>{actions}</div>}
    {widget && actions && <div className="absolute right-0 top-0 z-20">{actions}</div>}
    {resolved.missing.length > 0 && <span className="px-2 text-xs text-amber-600" title={resolved.missing.join(', ')}>字模缺失，保留原配置</span>}
    <div {...(inertWidget ? { inert: '' } as any : {})} className="relative min-h-0 flex-1 overflow-hidden" style={{ padding: widget ? ws.padding : 4, color: ws.textColor, fontSize: ws.fontSize }}>
      <DashboardChart chartType={chart.chart_type} data={chartData(chart)} config={resolved.config}
        visual={{ ...resolved.visual, viewBg: cfg.cardBg || resolved.visual.viewBg }} chartId={draft ? undefined : chart.id} draft={draft} />
    </div>
    {cfg.showCornerBrackets && ['left-0 top-0 border-l-2 border-t-2', 'right-0 top-0 border-r-2 border-t-2',
      'left-0 bottom-0 border-l-2 border-b-2', 'right-0 bottom-0 border-r-2 border-b-2'].map(c =>
      <i key={c} className={`pointer-events-none absolute h-3 w-3 ${c}`} style={{ borderColor: cfg.cornerAccent }} />)}
  </div>;
});
interface CanvasProps {
  dashboard: Pick<Dashboard, 'filters' | 'charts'>; items?: VisComponent[];
  style?: CSSProperties; children?: ReactNode; showGrid?: boolean; draft?: boolean;
  interactive?: boolean;
  renderChart?: (chart: ChartModel, card: ReactNode) => ReactNode;
}
export const DashboardCanvas = forwardRef<HTMLDivElement, CanvasProps>(function DashboardCanvas({
  dashboard, items = [], style, children, showGrid, draft, interactive, renderChart,
}, ref) {
  const theme = useThemeStore(s => s.theme);
  const canvas = getCanvas(dashboard.filters, dashboard.charts);
  const design = useMemo(() => resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items, { theme }), [dashboard.filters, items, theme]);
  const bg = design.background;
  // 图层：阅读态跳过被隐藏的卡片；编辑态仍渲染（否则无法再取消隐藏）
  const chartRefs = (draft ? dashboard.charts : dashboard.charts.filter(c => !isChartHidden(c)))
    .slice().sort((a, b) => chartZIndex(a) - chartZIndex(b) || a.id - b.id);
  return <div ref={ref} className="adh-canvas relative shrink-0 overflow-hidden" style={{
    width: canvas.width, height: canvas.height,
    backgroundColor: bg.backgroundColor || design.visual.viewBg || 'hsl(var(--muted))',
    backgroundImage: bg.backgroundImage, backgroundSize: bg.backgroundSize,
    boxShadow: '0 0 0 1px hsl(var(--border)), 0 10px 34px rgba(0,0,0,0.16)', ...style,
  }}>
    {bg.overlay && <div className="pointer-events-none absolute inset-0" style={{ backgroundImage: bg.overlay }} />}
    {showGrid && <div className="pointer-events-none absolute inset-0" style={gridBackgroundStyle(canvas.gridSize, canvas.gridSize)} />}
    {design.missing.length > 0 && <div className="absolute right-2 top-1 z-10 text-xs text-amber-600">部分字模缺失，已保留原配置</div>}
    {chartRefs.map(chart => {
      const card = <DashboardChartCard chart={chart} items={items} design={design} draft={draft} interactive={interactive} />;
      if (renderChart) return renderChart(chart, card);
      const p = chartRect(chart);
      return <div key={chart.id} className="absolute min-h-0" style={{ left: p.x, top: p.y, width: p.w, height: p.h, zIndex: chartZIndex(chart) || undefined }}>{card}</div>;
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
export function ResponsiveDashboardCanvas({ dashboard, items = [], draft, interactive, breakpoint, renderItem, layout, onWidth }: CanvasProps & {
  breakpoint?: PageBreakpoint; layout?: PageItem[]; onWidth?: (width: number) => void;
  renderItem?: (item: PageItem, card: ReactNode, style: CSSProperties) => ReactNode;
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
  const bp = breakpoint || pageBreakpoint(width);
  const design = useMemo(() => resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items, { theme }), [dashboard.filters, items, theme]);
  const result = useMemo(() => {
    try { return { items: layout || resolvePageLayout(dashboard, bp), error: '' }; }
    catch (error) { return { items: [], error: error instanceof Error ? error.message : '页面布局读取失败' }; }
  }, [dashboard, bp, layout]);
  const shown = draft ? result.items : result.items.filter(item => !isChartHidden(item.chart));
  return <div ref={ref} className="adh-page-canvas min-w-0 w-full" data-breakpoint={bp} style={{
    backgroundColor: design.background.backgroundColor || design.visual.viewBg,
    backgroundImage: design.background.backgroundImage, backgroundSize: design.background.backgroundSize,
  }}>
    {result.error ? <p role="alert" className="p-6 text-destructive">{result.error}</p> : !shown.length ? <p className="p-12 text-center text-muted-foreground">此看板暂无图表</p> :
      <div className="grid min-w-0" style={{ gridTemplateColumns: `repeat(${PAGE_COLUMNS[bp]}, minmax(0, 1fr))`, gridAutoRows: PAGE_ROW, gap: pageGap(bp) }}>
        {shown.map(item => {
          const { chart, rect } = item;
          const style = { gridColumn: `${rect.x + 1} / span ${rect.w}`, gridRow: `${rect.y + 1} / span ${rect.h}` };
          const card = <DashboardChartCard chart={chart} design={design} items={items} draft={draft} interactive={interactive} />;
          return renderItem ? renderItem(item, card, style) : <div key={chart.id} data-chart-id={chart.id} className="min-h-0 min-w-0" style={style}>{card}</div>;
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
 * 缩影图形：列表里的效果图要能一眼认出图表类型，而不是清一色四根假柱子。
 * 颜色取自字模库主题包（visual.palette），与真实渲染同源。
 * **导出供工作空间看板页复用**，两个列表不得各画一套（样式唯一源）。
 */
export function ChartGlyph({ chartType, palette }: { chartType: string; palette: string[] }) {
  const color = (i: number) => palette[i % (palette.length || 1)] || '#475569';
  const type = chartType.toLowerCase();
  if (type.startsWith('widget_')) {
    return <div className="flex h-full items-center"><div className="h-1/3 w-2/3 rounded-full" style={{ background: color(0), opacity: 0.55 }} /></div>;
  }
  if (/big_number|statistic|kpi|indicator/.test(type)) {
    return <div className="flex h-full flex-col justify-center gap-0.5"><div className="h-2 w-2/3 rounded-sm" style={{ background: color(0), opacity: 0.75 }} /><div className="h-1 w-1/3 rounded-sm" style={{ background: color(1), opacity: 0.4 }} /></div>;
  }
  if (/table|list/.test(type)) {
    return <div className="flex h-full flex-col justify-center gap-0.5">{[0, 1, 2].map(r => <div key={r} className="h-1 rounded-sm" style={{ background: color(r), opacity: 0.4, width: `${90 - r * 18}%` }} />)}</div>;
  }
  if (/pie|donut|ring|rose/.test(type)) {
    return <div className="flex h-full items-center justify-center"><div className="aspect-square h-3/4 rounded-full" style={{ background: `conic-gradient(${color(0)} 0 40%, ${color(1)} 40% 70%, ${color(2)} 70% 100%)`, opacity: 0.75 }} /></div>;
  }
  if (/line|area|trend|time|stock|scatter/.test(type)) {
    return <div className="flex h-full items-end"><svg viewBox="0 0 40 20" preserveAspectRatio="none" className="h-full w-full"><polyline points="2,15 12,8 22,11 38,3" fill="none" stroke={color(0)} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" opacity="0.8" /></svg></div>;
  }
  // 柱状/条形/其余默认：竖向柱阵
  return <div className="flex h-full items-end gap-1">{[40, 75, 55, 90].map((h, j) => <span key={j} className="flex-1 rounded-sm" style={{ height: `${h}%`, background: color(j), opacity: 0.6 }} />)}</div>;
}

export function DashboardThumbnail({ dashboard, items = [] }: { dashboard: Pick<Dashboard, 'filters' | 'charts'>; items?: VisComponent[] }) {
  const theme = useThemeStore(s => s.theme);
  const design = resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items, { theme });
  // page 模式必须走 resolvePageLayout：这是真实画布的唯一布局源，
  // 用 chartRect 像素另算一套会与画布对不上（曾出现缩略图 4 列溢出、大片空白）
  const isPage = dashboard.filters?.design?.page !== undefined;
  const pageItems = useMemo(() => {
    if (!isPage) return [];
    try { return resolvePageLayout(dashboard, 'desktop'); } catch { return []; }
  }, [dashboard, isPage]);
  const canvas = isPage ? pageCanvasSize(pageItems, 'desktop') : getCanvas(dashboard.filters, dashboard.charts);
  const tiles = (isPage ? pageItems.map(i => ({ chart: i.chart, rect: pagePixelRect(i.rect, 'desktop') }))
    : dashboard.charts.map(c => ({ chart: c, rect: chartRect(c) })));
  return <div className="relative w-full overflow-hidden rounded-md border" style={{ aspectRatio: `${canvas.width} / ${canvas.height}`, backgroundColor: design.background.backgroundColor || design.visual.viewBg || '#f1f5f9', backgroundImage: design.background.backgroundImage, backgroundSize: design.background.backgroundSize }}>
    {tiles.map(({ chart: c, rect: p }) => {
      const resolved = resolveChartDesign(c.config, design, items, c.chart_type);
      const palette = resolved.visual.palette?.length ? resolved.visual.palette : design.visual.palette;
      return <div key={c.id} className="absolute overflow-hidden rounded-sm border border-black/10 p-1" style={{ left: `${p.x / canvas.width * 100}%`, top: `${p.y / canvas.height * 100}%`,
        width: `${p.w / canvas.width * 100}%`, height: `${p.h / canvas.height * 100}%`, background: visualConfig(resolved.config).cardBg || resolved.visual.viewBg || (resolved.visual.mode === 'dark' ? '#111827' : '#ffffff') }}>
        <div className="mb-0.5 h-1 w-1/2 rounded opacity-40" style={{ background: palette?.[0] || '#475569' }} />
        <div className="flex h-[calc(100%-4px)] items-stretch"><ChartGlyph chartType={c.chart_type} palette={palette || []} /></div>
      </div>;
    })}
    {!tiles.length && <span className="absolute inset-0 flex items-center justify-center text-xs text-slate-400">空白画布</span>}
  </div>;
}
