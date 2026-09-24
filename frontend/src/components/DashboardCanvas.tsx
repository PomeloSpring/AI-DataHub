import { forwardRef, memo, useMemo, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import DashboardChart from './DashboardChart';
import type { Dashboard, DashboardChart as ChartModel } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';
import { useThemeStore } from '@/stores/themeStore';
import { chartData, chartRect, getCanvas, resolveChartDesign, resolveDashboardDesign, visualConfig, legacyCanvasFilters, type Rect } from '@/lib/dashboardDesign';
import { PAGE_COLUMNS, PAGE_ROW, pageBreakpoint, pageGap, resolvePageLayout, type PageBreakpoint, type PageItem } from '@/lib/dashboardPageLayout';

export const DashboardChartCard = memo(function DashboardChartCard({ chart, design, items, actions, draft = false }: {
  chart: ChartModel; design: ReturnType<typeof resolveDashboardDesign>; items: VisComponent[];
  actions?: ReactNode; draft?: boolean;
}) {
  const resolved = resolveChartDesign(chart.config, design, items, chart.chart_type);
  const cfg = visualConfig(resolved.config);
  const widget = chart.chart_type.startsWith('widget_');
  const ws = resolved.config.widgetStyle || {};
  const dark = resolved.visual.mode === 'dark';
  const base = resolved.visual.viewBg || (resolved.visual.mode ? (dark ? '#111827' : '#ffffff') : 'hsl(var(--card))');
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
      borderBottom: cfg.showUnderline ? `1px solid ${cfg.underlineColor || '#64748b'}` : undefined,
    }}><span className="truncate font-medium">{chart.name}</span>{actions}</div>}
    {widget && actions && <div className="absolute right-0 top-0 z-20">{actions}</div>}
    {resolved.missing.length > 0 && <span className="px-2 text-xs text-amber-600" title={resolved.missing.join(', ')}>字模缺失，保留原配置</span>}
    <div {...(draft && widget ? { inert: '' } as any : {})} className="relative min-h-0 flex-1 overflow-hidden" style={{ padding: widget ? ws.padding : 4, color: ws.textColor, fontSize: ws.fontSize }}>
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
  renderChart?: (chart: ChartModel, card: ReactNode) => ReactNode;
}
export const DashboardCanvas = forwardRef<HTMLDivElement, CanvasProps>(function DashboardCanvas({
  dashboard, items = [], style, children, showGrid, draft, renderChart,
}, ref) {
  const theme = useThemeStore(s => s.theme);
  const canvas = getCanvas(dashboard.filters, dashboard.charts);
  const design = useMemo(() => resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items, { theme }), [dashboard.filters, items, theme]);
  const bg = design.background;
  return <div ref={ref} className="adh-canvas relative shrink-0 overflow-hidden" style={{
    width: canvas.width, height: canvas.height,
    backgroundColor: bg.backgroundColor || design.visual.viewBg || 'hsl(var(--muted))',
    backgroundImage: bg.backgroundImage, backgroundSize: bg.backgroundSize,
    boxShadow: '0 0 0 1px hsl(var(--border)), 0 10px 34px rgba(0,0,0,0.16)', ...style,
  }}>
    {bg.overlay && <div className="pointer-events-none absolute inset-0" style={{ backgroundImage: bg.overlay }} />}
    {showGrid && <div className="pointer-events-none absolute inset-0" style={{
      backgroundImage: 'linear-gradient(#64748b55 1px, transparent 1px), linear-gradient(90deg, #64748b55 1px, transparent 1px), linear-gradient(#94a3b830 1px, transparent 1px), linear-gradient(90deg, #94a3b830 1px, transparent 1px)',
      backgroundSize: `${canvas.gridSize * 5}px ${canvas.gridSize * 5}px, ${canvas.gridSize * 5}px ${canvas.gridSize * 5}px, ${canvas.gridSize}px ${canvas.gridSize}px, ${canvas.gridSize}px ${canvas.gridSize}px`,
      backgroundPosition: '-0.5px -0.5px',
    }} />}
    {design.missing.length > 0 && <div className="absolute right-2 top-1 z-10 text-xs text-amber-600">部分字模缺失，已保留原配置</div>}
    {dashboard.charts.map(chart => {
      const card = <DashboardChartCard chart={chart} items={items} design={design} draft={draft} />;
      if (renderChart) return renderChart(chart, card);
      const p = chartRect(chart);
      return <div key={chart.id} className="absolute min-h-0" style={{ left: p.x, top: p.y, width: p.w, height: p.h }}>{card}</div>;
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
export function ResponsiveDashboardCanvas({ dashboard, items = [], draft, breakpoint, renderItem, layout, onWidth }: CanvasProps & {
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
  return <div ref={ref} className="adh-page-canvas min-w-0 w-full" data-breakpoint={bp} style={{
    backgroundColor: design.background.backgroundColor || design.visual.viewBg,
    backgroundImage: design.background.backgroundImage, backgroundSize: design.background.backgroundSize,
  }}>
    {result.error ? <p role="alert" className="p-6 text-destructive">{result.error}</p> : !dashboard.charts.length ? <p className="p-12 text-center text-muted-foreground">此看板暂无图表</p> :
      <div className="grid min-w-0" style={{ gridTemplateColumns: `repeat(${PAGE_COLUMNS[bp]}, minmax(0, 1fr))`, gridAutoRows: PAGE_ROW, gap: pageGap(bp) }}>
        {result.items.map(item => {
          const { chart, rect } = item;
          const style = { gridColumn: `${rect.x + 1} / span ${rect.w}`, gridRow: `${rect.y + 1} / span ${rect.h}` };
          const card = <DashboardChartCard chart={chart} design={design} items={items} draft={draft} />;
          return renderItem ? renderItem(item, card, style) : <div key={chart.id} data-chart-id={chart.id} className="min-h-0 min-w-0" style={style}>{card}</div>;
        })}
      </div>}
  </div>;
}

export function CanvasRulers({ canvas, scale, pan, active }: {
  canvas: { width: number; height: number }; scale: number; pan: { x: number; y: number }; active?: Rect;
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
export function DashboardThumbnail({ dashboard, items = [] }: { dashboard: Pick<Dashboard, 'filters' | 'charts'>; items?: VisComponent[] }) {
  const theme = useThemeStore(s => s.theme);
  const canvas = getCanvas(dashboard.filters, dashboard.charts);
  const design = resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items, { theme });
  return <div className="relative w-full overflow-hidden rounded-md border" style={{ aspectRatio: `${canvas.width} / ${canvas.height}`, backgroundColor: design.background.backgroundColor || design.visual.viewBg || '#f1f5f9', backgroundImage: design.background.backgroundImage, backgroundSize: design.background.backgroundSize }}>
    {dashboard.charts.map((c, i) => {
      const p = chartRect(c);
      const resolved = resolveChartDesign(c.config, design, items, c.chart_type);
      return <div key={c.id} className="absolute rounded-sm border border-black/10 p-1" style={{ left: `${p.x / canvas.width * 100}%`, top: `${p.y / canvas.height * 100}%`,
        width: `${p.w / canvas.width * 100}%`, height: `${p.h / canvas.height * 100}%`, background: visualConfig(resolved.config).cardBg || resolved.visual.viewBg || (resolved.visual.mode === 'dark' ? '#111827' : '#ffffff') }}>
        <div className="h-1 w-1/2 rounded opacity-40" style={{ background: resolved.visual.palette?.[i % (resolved.visual.palette.length || 1)] || '#475569' }} />
        {!c.chart_type.startsWith('widget_') && <div className="flex h-3/4 items-end gap-1 pt-1">{[40, 75, 55, 90].map((h, j) =>
          <span key={j} className="flex-1 rounded-sm opacity-60" style={{ height: `${h}%`, background: resolved.visual.palette?.[j % (resolved.visual.palette.length || 1)] || '#475569' }} />)}</div>}
      </div>;
    })}
    {!dashboard.charts.length && <span className="absolute inset-0 flex items-center justify-center text-xs text-slate-400">空白画布</span>}
  </div>;
}
