import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { GripHorizontal, Settings, Trash2 } from 'lucide-react';
import { ResponsiveDashboardCanvas, CanvasRulers } from '@/components/DashboardCanvas';
import type { useCanvasInteraction } from '@/hooks/useCanvasInteraction';
import type { Dashboard, DashboardChart } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';
import { isEditingTarget, screenToCanvas, type Rect } from '@/lib/dashboardDesign';
import { PAGE_COLUMNS, PAGE_ROW, PAGE_WIDTHS, pageGap, pageMinHeight, pageRows, pagePixelRect, pageCanvasSize, resolvePageLayout, movePageItem, type PageBreakpoint, type PageItem } from '@/lib/dashboardPageLayout';

interface Props {
  dashboard: Dashboard; items: VisComponent[]; breakpoint: PageBreakpoint; selectedId?: number;
  onSelect: (chart: DashboardChart) => void; onConfig: (chart: DashboardChart) => void; onDelete: (chart: DashboardChart) => void;
  onChange: (items: PageItem[]) => void; onAdd: (rect: { x: number; y: number }) => void;
  canvas: ReturnType<typeof useCanvasInteraction>; showGrid: boolean; panMode: boolean;
}
type Gesture = {
  id: number; pointer: number; x: number; y: number; scale: number; rect: Rect; resize: boolean;
  layout: PageItem[]; target: HTMLElement; scroll: number; scrollX: number;
};
type Preview = { layout: PageItem[]; pixels: Rect };
export default function PageLayoutEditor({ dashboard, items, breakpoint, selectedId, onSelect, onConfig, onDelete, onChange, onAdd, canvas, showGrid, panMode }: Props) {
  const { canvasRef: viewport, surfaceRef: surface, scale, panOffset, beginElementInteraction, endElementInteraction } = canvas;
  const [preview, setPreview] = useState<Preview | null>(null);
  const [active, setActive] = useState<Gesture | null>(null);
  const activeRef = useRef<Gesture | null>(null);
  const result = useMemo(() => {
    try { return { layout: resolvePageLayout(dashboard, breakpoint), error: '' }; }
    catch (e) { return { layout: [], error: e instanceof Error ? e.message : '页面布局无效' }; }
  }, [dashboard, breakpoint]);
  const changeRef = useRef(onChange); changeRef.current = onChange;
  const size = pageCanvasSize(result.layout, breakpoint);
  const gap = pageGap(breakpoint), column = (PAGE_WIDTHS[breakpoint] + gap) / PAGE_COLUMNS[breakpoint], row = PAGE_ROW + gap;
  useEffect(() => { activeRef.current = null; setActive(null); setPreview(null); }, [breakpoint, dashboard.id]);
  useEffect(() => {
    if (!active) return;
    let frame: number | null = null, pending: PointerEvent | null = null;
    let previousRect = '', previousLayout = active.layout;
    const minHeight = pageRows(pageMinHeight(active.layout.find(item => item.chart.id === active.id)!.chart.chart_type), breakpoint) * row - pageGap(breakpoint);
    const delta = (e: PointerEvent) => ({
      x: (e.clientX - active.x + (viewport.current?.scrollLeft || 0) - active.scrollX) / active.scale,
      y: (e.clientY - active.y + (viewport.current?.scrollTop || 0) - active.scroll) / active.scale,
    });
    const calculate = (e: PointerEvent): Preview => {
      const d = delta(e), dx = Math.round(d.x / column), dy = Math.round(d.y / row);
      const rect = active.resize ? { ...active.rect, w: active.rect.w + dx, h: active.rect.h + dy } : { ...active.rect, x: active.rect.x + dx, y: active.rect.y + dy };
      const key = JSON.stringify(rect);
      if (key !== previousRect) { previousRect = key; previousLayout = movePageItem(active.layout, active.id, rect, breakpoint); }
      const original = pagePixelRect(active.rect, breakpoint);
      // 卡片连续跟随指针；网格只计算落点，避免每次移动重排和重建图表。
      const pixels = active.resize ? { ...original, w: Math.min(size.width - original.x, Math.max(column - pageGap(breakpoint), original.w + d.x)), h: Math.max(minHeight, original.h + d.y) }
        : { ...original, x: Math.max(0, Math.min(size.width - original.w, original.x + d.x)), y: Math.max(0, original.y + d.y) };
      return { layout: previousLayout, pixels };
    };
    const moved = (e: PointerEvent) => { const d = delta(e); return Math.hypot(d.x, d.y) * active.scale > 4; };
    const clearFrame = () => { if (frame !== null) cancelAnimationFrame(frame); frame = null; pending = null; };
    const release = () => { if (active.target.hasPointerCapture?.(active.pointer)) active.target.releasePointerCapture(active.pointer); };
    const cancel = () => { clearFrame(); activeRef.current = null; endElementInteraction(); release(); setActive(null); setPreview(null); };
    const move = (e: PointerEvent) => {
      if (!activeRef.current || e.pointerId !== active.pointer) return;
      e.preventDefault(); pending = e;
      if (frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        if (pending && moved(pending)) setPreview(calculate(pending));
        else setPreview(null);
        pending = null;
      });
    };
    const up = (e: PointerEvent) => {
      if (!activeRef.current || e.pointerId !== active.pointer) return;
      const next = moved(e) ? calculate(e).layout : null;
      cancel();
      if (next && next.some(item => {
        const before = active.layout.find(value => value.chart.id === item.chart.id)!;
        return JSON.stringify(before.rect) !== JSON.stringify(item.rect);
      })) changeRef.current(next);
    };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); cancel(); } };
    const pointerCancel = (e: PointerEvent) => { if (activeRef.current && e.pointerId === active.pointer) cancel(); };
    window.addEventListener('pointermove', move, { passive: false }); window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', pointerCancel); window.addEventListener('lostpointercapture', pointerCancel);
    window.addEventListener('blur', cancel); window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', pointerCancel); window.removeEventListener('lostpointercapture', pointerCancel);
      window.removeEventListener('blur', cancel); window.removeEventListener('keydown', key);
      clearFrame(); activeRef.current = null; endElementInteraction(); release();
    };
  }, [active, breakpoint, column, row, size.width, viewport, endElementInteraction]);
  const start = (e: ReactPointerEvent, item: PageItem, resize = false) => {
    if (e.defaultPrevented || activeRef.current || e.isPrimary === false || e.button !== 0 || isEditingTarget(e.target) || !beginElementInteraction()) return;
    e.preventDefault(); e.stopPropagation();
    const target = (e.currentTarget as HTMLElement).closest<HTMLElement>('[data-chart-id]')!;
    const gesture = { id: item.chart.id, pointer: e.pointerId, x: e.clientX, y: e.clientY, scale,
      scroll: viewport.current?.scrollTop || 0, scrollX: viewport.current?.scrollLeft || 0, rect: item.rect, resize, layout: result.layout, target };
    activeRef.current = gesture; target.setPointerCapture?.(e.pointerId); setActive(gesture); onSelect(item.chart);
  };
  const snapped = active && preview?.layout.find(item => item.chart.id === active.id);
  return <div ref={viewport} className="adh-canvas-viewport relative min-h-0 min-w-0 flex-1 overflow-hidden bg-muted cursor-grab active:cursor-grabbing"
    style={{ touchAction: 'none', userSelect: 'none' }} aria-label="页面布局编辑区" onPointerDownCapture={canvas.handlePanStart}
    onDragOver={e => e.preventDefault()} onDrop={e => {
      e.preventDefault();
      if (!surface.current || result.error) return;
      const point = screenToCanvas({ x: e.clientX, y: e.clientY }, surface.current.getBoundingClientRect(), scale);
      onAdd({ x: Math.min(PAGE_COLUMNS[breakpoint] - 1, Math.max(0, Math.floor(point.x / column))), y: Math.min(50000, Math.max(0, Math.floor(point.y / row))) });
    }}>
    <div ref={surface} className="absolute bg-background shadow-lg" data-testid="page-editor-surface" style={{ ...size, left: '50%', top: '50%', marginLeft: -size.width / 2, marginTop: -size.height / 2,
      transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${scale})`, transformOrigin: 'center' }}>
      {result.error ? <p role="alert" className="p-6 text-destructive">{result.error}。可通过“恢复自动适配”重建当前断点。</p> : <ResponsiveDashboardCanvas dashboard={dashboard} items={items} breakpoint={breakpoint} draft layout={result.layout}
        renderItem={(item, card, style) => {
          const original = pagePixelRect(item.rect, breakpoint), dragging = active?.id === item.chart.id;
          const target = preview?.layout.find(value => value.chart.id === item.chart.id);
          const pixels = dragging && preview ? preview.pixels : target ? pagePixelRect(target.rect, breakpoint) : original;
          return <div key={item.chart.id} data-chart-id={item.chart.id} className={`relative min-h-0 min-w-0 rounded-lg ${panMode ? 'cursor-grab' : 'cursor-move'}`}
            style={{ ...style, width: pixels.w, height: pixels.h, touchAction: 'none', zIndex: dragging ? 20 : 1,
              transform: `translate3d(${pixels.x - original.x}px, ${pixels.y - original.y}px, 0)`,
              transition: active && !dragging ? 'transform 120ms ease-out' : undefined, willChange: dragging ? 'transform' : undefined,
              outline: selectedId === item.chart.id ? `${2 / scale}px solid #3b82f6` : undefined }}
          onPointerDown={e => start(e, item)} onDoubleClick={() => { if (!panMode) onConfig(item.chart); }}>
          <div className="pointer-events-none" style={{ width: original.w, height: original.h, transformOrigin: 'top left',
            transform: dragging && active.resize ? `scale(${pixels.w / original.w}, ${pixels.h / original.h})` : undefined }}>{card}</div>
          {selectedId === item.chart.id && <>
            <div className="absolute right-0 top-0 z-10 flex items-center rounded-bl border bg-background shadow-sm" style={{ transform: `scale(${1 / scale})`, transformOrigin: 'top right' }}>
              <GripHorizontal className="mx-2 h-4 w-4" />
              <button className="p-2" aria-label="配置图表" onClick={() => onConfig(item.chart)}><Settings className="h-3 w-3" /></button>
              <button className="p-2" aria-label="删除图表" onClick={() => onDelete(item.chart)}><Trash2 className="h-3 w-3" /></button>
            </div>
            <div role="separator" aria-label="调整页面图表大小" className="absolute bottom-0 right-0 h-5 w-5 cursor-se-resize rounded-tl bg-primary" style={{ transform: `scale(${1 / scale})`, transformOrigin: 'bottom right' }} onPointerDown={e => start(e, item, true)} />
          </>}
        </div>;
        }} />}
      {showGrid && <div aria-label="页面网格线" className="pointer-events-none absolute inset-0" style={{
        backgroundImage: `linear-gradient(to right, #64748b66 ${1 / scale}px, transparent ${1 / scale}px), linear-gradient(to bottom, #64748b44 ${1 / scale}px, transparent ${1 / scale}px)`,
        backgroundSize: `${column}px ${row}px`,
      }} />}
      {snapped && <div aria-label="图表放置预览" className="pointer-events-none absolute border-2 border-dashed border-primary bg-primary/10" style={{ left: pagePixelRect(snapped.rect, breakpoint).x, top: pagePixelRect(snapped.rect, breakpoint).y,
        width: pagePixelRect(snapped.rect, breakpoint).w, height: pagePixelRect(snapped.rect, breakpoint).h }} />}
      <div className="pointer-events-none absolute bottom-0 w-full p-6 text-center text-sm text-muted-foreground">从组件库拖入图表或控件 · 拖动调整位置 · 右下角调整尺寸</div>
    </div>
    <CanvasRulers canvas={size} scale={scale} pan={panOffset} active={preview?.pixels} />
    <div className="pointer-events-none absolute bottom-3 left-8 z-30 rounded border bg-background/95 px-3 py-1.5 text-xs shadow-sm">滚轮缩放 · 空格拖动 / 中键平移 · F 适应窗口 · 网格吸附落点</div>
  </div>;
}
