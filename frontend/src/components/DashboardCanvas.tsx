import { forwardRef, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import DashboardChart from './DashboardChart';
import type { Dashboard, DashboardChart as ChartModel } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';
import { chartData, getCanvas, resolveChartDesign, resolveDashboardDesign, visualConfig, legacyCanvasFilters } from '@/lib/dashboardDesign';

export function DashboardChartCard({ chart, design, items, actions, draft = false }: {
  chart: ChartModel; design: ReturnType<typeof resolveDashboardDesign>; items: VisComponent[];
  actions?: ReactNode; draft?: boolean;
}) {
  const resolved = resolveChartDesign(chart.config, design, items);
  const cfg = visualConfig(resolved.config);
  const widget = chart.chart_type.startsWith('widget_');
  const ws = chart.config?.widgetStyle || {};
  const dark = resolved.visual.mode === 'dark';
  const base = resolved.visual.viewBg || (resolved.visual.mode ? (dark ? '#111827' : '#ffffff') : 'hsl(var(--card))');
  return <div className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-lg border" style={{
    background: (widget ? ws.backgroundColor : undefined) || cfg.cardBg || base,
    border: cfg.cardBorder || cfg.borderStyle,
    borderRadius: widget ? ws.borderRadius ?? cfg.borderRadius : cfg.borderRadius,
    boxShadow: widget && ws.boxShadow ? ws.boxShadow : typeof cfg.glow === 'string' ? cfg.glow : undefined,
    color: cfg.labelColor || resolved.visual.textColor || (dark ? '#e2e8f0' : undefined),
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
}
interface CanvasProps {
  dashboard: Pick<Dashboard, 'filters' | 'charts'>; items?: VisComponent[];
  style?: CSSProperties; children?: ReactNode; showGrid?: boolean; draft?: boolean;
  renderChart?: (chart: ChartModel, card: ReactNode) => ReactNode;
}
export const DashboardCanvas = forwardRef<HTMLDivElement, CanvasProps>(function DashboardCanvas({
  dashboard, items = [], style, children, showGrid, draft, renderChart,
}, ref) {
  const canvas = getCanvas(dashboard.filters, dashboard.charts);
  const design = resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items);
  const bg = design.background;
  return <div ref={ref} className="relative shrink-0 overflow-hidden" style={{
    width: canvas.width, height: canvas.height,
    backgroundColor: bg.backgroundColor || design.visual.viewBg || 'hsl(var(--muted))',
    backgroundImage: bg.backgroundImage, backgroundSize: bg.backgroundSize, ...style,
  }}>
    {bg.overlay && <div className="pointer-events-none absolute inset-0" style={{ backgroundImage: bg.overlay }} />}
    {showGrid && <div className="pointer-events-none absolute inset-0" style={{
      backgroundImage: 'linear-gradient(#94a3b830 1px, transparent 1px), linear-gradient(90deg, #94a3b830 1px, transparent 1px)',
      backgroundSize: `${canvas.gridSize}px ${canvas.gridSize}px`,
    }} />}
    {design.missing.length > 0 && <div className="absolute right-2 top-1 z-10 text-xs text-amber-600">部分字模缺失，已保留原配置</div>}
    {dashboard.charts.map(chart => {
      const card = <DashboardChartCard chart={chart} items={items} design={design} draft={draft} />;
      if (renderChart) return renderChart(chart, card);
      const p = chart.position || { x: 0, y: 0, w: 400, h: 300 };
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
// 列表缩略图只绘制配置，绝不挂载图表或执行查询。
export function DashboardThumbnail({ dashboard, items = [] }: { dashboard: Pick<Dashboard, 'filters' | 'charts'>; items?: VisComponent[] }) {
  const canvas = getCanvas(dashboard.filters, dashboard.charts);
  const design = resolveDashboardDesign(legacyCanvasFilters(dashboard.filters), items);
  return <div className="relative w-full overflow-hidden rounded-md border" style={{ aspectRatio: `${canvas.width} / ${canvas.height}`, backgroundColor: design.background.backgroundColor || design.visual.viewBg || '#f1f5f9', backgroundImage: design.background.backgroundImage, backgroundSize: design.background.backgroundSize }}>
    {dashboard.charts.map((c, i) => {
      const p = c.position || { x: 0, y: 0, w: 400, h: 300 };
      const resolved = resolveChartDesign(c.config, design, items);
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
