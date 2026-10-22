import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import type { Ref } from 'react';
import { Button } from '@/components/ui/button';
import { ResponsiveDashboardCanvas, CanvasRulers } from '@/components/DashboardCanvas';
import type { useCanvasInteraction } from '@/hooks/useCanvasInteraction';
import { useViewportSize } from '@/hooks/useOverlayPosition';
import type { Dashboard, DashboardChart } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';
import { isChartLocked, isEditingTarget, type Rect } from '@/lib/dashboardDesign';
import { CANVAS_COLORS, backdropStyle, canvasRectToScreen, cornerDotStyle } from '@/lib/canvasTheme';
import CanvasHint from './CanvasHint';
import SelectionOverlay from './SelectionOverlay';
import { PAGE_COLUMNS, PAGE_ROW, PAGE_WIDTHS, pageGap, pageMinHeight, pageRows, pagePixelRect, pageCanvasSize, previewInsert, resolvePageLayout, movePageItem, type PageBreakpoint, type PageItem } from '@/lib/dashboardPageLayout';
import type { DropGhost } from '@/lib/editorDrag';
import { useMergedRefs } from '@/lib/mergeRefs';

interface Props {
  dashboard: Dashboard; items: VisComponent[]; breakpoint: PageBreakpoint; selectedIds?: number[];
  onSelect: (chart: DashboardChart, e?: ReactPointerEvent) => void; onConfig: (chart: DashboardChart) => void; onDelete: (chart: DashboardChart) => void;
  onChange: (items: PageItem[]) => void;
  canvas: ReturnType<typeof useCanvasInteraction>; showGrid: boolean; panMode: boolean; interactive: boolean;
  showRulers: boolean;
  /** dnd-kit 落点区：与视口 ref 合挂到同一个节点上 */
  dropRef?: Ref<HTMLDivElement>;
  /** 从库拖入时的落点预览（网格行列） */
  dropGhost?: DropGhost | null;
  /** 空画布引导动作（缺省对不渲染按钮） */
  onInsertFromLibrary?: () => void; onOpenTemplates?: () => void;
}
type Gesture = {
  id: number; pointer: number; x: number; y: number; scale: number; rect: Rect; resize: boolean;
  layout: PageItem[]; target: HTMLElement; scroll: number; scrollX: number;
};
type Preview = { layout: PageItem[]; pixels: Rect };
/** 拖入预览用的哨兵 id：不与真实图表冲突，也不会被渲染成卡片 */
const PREVIEW_ID = -0x9c40;
export default function PageLayoutEditor({ dashboard, items, breakpoint, selectedIds = [], onSelect, onConfig, onDelete, onChange, canvas, showGrid, panMode, interactive, showRulers, dropRef, dropGhost, onInsertFromLibrary, onOpenTemplates }: Props) {
  const { canvasRef: viewport, surfaceRef: surface, scale, panOffset, beginElementInteraction, endElementInteraction } = canvas;
  // 稳定身份的合并 ref：内联 mergeRefs 会每帧新建，React 先用 null 调旧回调会注销 dnd-kit 落点节点
  const viewRef = useMergedRefs<HTMLDivElement>(viewport, dropRef);
  const viewportSize = useViewportSize(viewport);
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
    if (isChartLocked(item.chart)) return;
    if (e.defaultPrevented || activeRef.current || e.isPrimary === false || e.button !== 0 || isEditingTarget(e.target) || !beginElementInteraction()) return;
    e.preventDefault(); e.stopPropagation();
    // 选中工具条/手柄已移到缩放层之外，按 id 反查卡片节点作为指针捕获目标
    const target = viewport.current?.querySelector<HTMLElement>(`[data-chart-id="${item.chart.id}"]`) ?? (e.currentTarget as HTMLElement);
    const gesture = { id: item.chart.id, pointer: e.pointerId, x: e.clientX, y: e.clientY, scale,
      scroll: viewport.current?.scrollTop || 0, scrollX: viewport.current?.scrollLeft || 0, rect: item.rect, resize, layout: result.layout, target };
    activeRef.current = gesture; target.setPointerCapture?.(e.pointerId); setActive(gesture); onSelect(item.chart, e);
  };
  const snapped = active && preview?.layout.find(item => item.chart.id === active.id);
  // 从库拖入：插入预览。虚线框 = 将占的空间，其余卡片挪到预测位置 = 将被挤到的地方。
  // 松手才提交；拖回图标库即自然取消（不写草稿）。
  const insert = dropGhost && dropGhost.mode === 'page'
    ? previewInsert(result.layout, {
        // 用真实图表类型：movePageItem 会按它的最小尺寸夹取，预览与真实插入必须同构
        chart: { id: PREVIEW_ID, name: '__preview__', chart_type: dropGhost.chartType, config: {}, position: {} } as DashboardChart,
        rect: { x: dropGhost.x, y: dropGhost.y, w: dropGhost.w, h: dropGhost.h }, automatic: false,
      }, breakpoint)
    : null;
  const predicted = (chartId: number) => insert?.find(item => item.chart.id === chartId)?.rect;
  const ghostPixels = insert ? pagePixelRect(insert.find(item => item.chart.id === PREVIEW_ID)!.rect, breakpoint) : null;
  // 选中卡片的屏幕矩形：覆盖层不在缩放层里，尺寸恒定为屏幕像素（多选时取主选）
  const selected = result.layout.find(item => item.chart.id === selectedIds[0]);
  const selectedPixels = selected ? (active?.id === selected.chart.id && preview ? preview.pixels : pagePixelRect(selected.rect, breakpoint)) : null;
  const selectedScreen = selected && selectedPixels ? canvasRectToScreen(selectedPixels, size, viewportSize, scale, panOffset) : null;
  const empty = !result.error && !result.layout.length;
  return <div ref={viewRef} className="adh-canvas-viewport relative min-h-0 min-w-0 flex-1 overflow-hidden"
    data-panning={panMode || undefined} data-drag-over={ghostPixels ? true : undefined}
    style={{ touchAction: 'none', userSelect: 'none', ...backdropStyle() }}
    aria-label="页面布局编辑区" onPointerDownCapture={canvas.handlePanStart}>
    <div ref={surface} className="absolute bg-background" data-testid="page-editor-surface" style={{ ...size, left: '50%', top: '50%', marginLeft: -size.width / 2, marginTop: -size.height / 2,
      transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${scale})`, transformOrigin: 'center',
      boxShadow: `0 0 0 1px ${CANVAS_COLORS.surfaceBorder}, ${CANVAS_COLORS.surfaceShadow}` }}>
      {result.error ? <p role="alert" className="p-6 text-destructive">{result.error}。可通过“恢复自动适配”重建当前断点。</p> : <ResponsiveDashboardCanvas dashboard={dashboard} items={items} breakpoint={breakpoint} draft interactive={interactive} layout={result.layout}
        renderItem={(item, card, style) => {
          const original = pagePixelRect(item.rect, breakpoint), dragging = active?.id === item.chart.id;
          const target = preview?.layout.find(value => value.chart.id === item.chart.id);
          // 拖入时展示“会被挤到哪”的预测位置（带过渡），松手后才落真实布局
          const forecast = predicted(item.chart.id);
          const pixels = dragging && preview ? preview.pixels
            : target ? pagePixelRect(target.rect, breakpoint)
            : forecast ? pagePixelRect(forecast, breakpoint) : original;
          return <div key={item.chart.id} data-chart-id={item.chart.id} data-selected={selectedIds.includes(item.chart.id) || undefined}
            className={`adh-editable-card relative min-h-0 min-w-0 rounded-lg ${panMode ? '' : 'cursor-move'}`}
            style={{ ...style, width: pixels.w, height: pixels.h, touchAction: 'none', zIndex: dragging ? 20 : 1,
              transform: `translate3d(${pixels.x - original.x}px, ${pixels.y - original.y}px, 0)`,
              transition: active && !dragging ? 'transform 120ms ease-out' : insert ? 'transform 160ms ease-out' : undefined, willChange: dragging ? 'transform' : undefined }}
            onPointerDown={e => start(e, item)} onDoubleClick={() => { if (!panMode) onConfig(item.chart); }}>
            <div className="pointer-events-none" style={{ width: original.w, height: original.h, transformOrigin: 'top left',
              transform: dragging && active.resize ? `scale(${pixels.w / original.w}, ${pixels.h / original.h})` : undefined }}>{card}</div>
          </div>;
        }} />}
      {/* 方块转角点星，不画线；zIndex 0 保证参考标记永远在卡片下方 */}
      {showGrid && <div aria-label="页面参考点" className="pointer-events-none absolute inset-0" style={{ ...cornerDotStyle(column), zIndex: 0 }} />}
      {snapped && <div aria-label="图表放置预览" className="pointer-events-none absolute border-2 border-dashed bg-primary/10"
        style={{ left: pagePixelRect(snapped.rect, breakpoint).x, top: pagePixelRect(snapped.rect, breakpoint).y,
          width: pagePixelRect(snapped.rect, breakpoint).w, height: pagePixelRect(snapped.rect, breakpoint).h, borderColor: CANVAS_COLORS.dropGhostBorder }} />}
      {ghostPixels && <div aria-label="新增落点预览" className="pointer-events-none absolute border-2 border-dashed bg-primary/10"
        style={{ left: ghostPixels.x, top: ghostPixels.y, width: ghostPixels.w, height: ghostPixels.h, borderColor: CANVAS_COLORS.dropGhostBorder }}>
        {/* 把将占用的空间直接标在框内，用户不必自己算尺寸 */}
        <span className="absolute inset-0 flex items-center justify-center rounded-sm bg-background/70 px-1 text-[10px] font-medium text-primary"
          style={{ whiteSpace: 'nowrap', overflow: 'hidden' }}>
          {dropGhost!.w} 列 × {dropGhost!.h} 行
        </span>
      </div>}
    </div>
    {empty && <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
      <div className="pointer-events-auto max-w-sm rounded-lg border bg-background/95 p-4 text-center shadow-sm">
        <p className="text-sm font-medium">这个断点还是空的</p>
        <p className="mt-1 text-xs text-muted-foreground">从左侧组件库把图表或控件拖到画布；网格会自动避让，不需要手动对齐。</p>
        {(onInsertFromLibrary || onOpenTemplates) && <div className="mt-3 flex justify-center gap-2">
          {onInsertFromLibrary && <Button size="sm" variant="outline" onClick={onInsertFromLibrary}>从数据集引入</Button>}
          {onOpenTemplates && <Button size="sm" variant="outline" onClick={onOpenTemplates}>套用模板</Button>}
        </div>}
      </div>
    </div>}
    {selectedScreen && selected && <SelectionOverlay screen={selectedScreen} size={selectedPixels ?? undefined} minimum={{ w: 1, h: 1 }}
      onConfig={() => onConfig(selected.chart)} onDelete={() => onDelete(selected.chart)}
      onResizeStart={e => start(e, selected, true)}
      onNudge={(dx, dy) => onChange(movePageItem(result.layout, selected.chart.id, {
        ...selected.rect, w: selected.rect.w + Math.round(dx / column), h: selected.rect.h + Math.round(dy / row),
      }, breakpoint))} />}
    <CanvasRulers canvas={size} scale={scale} pan={panOffset} active={preview?.pixels} visible={showRulers} />
    <CanvasHint text="滚轮缩放 · 空格拖动 / 中键平移 · F 适应窗口 · 网格吸附落点" />
  </div>;
}
