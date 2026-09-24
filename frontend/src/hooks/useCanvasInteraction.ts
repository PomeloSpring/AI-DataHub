import { useState, useCallback, useRef, useEffect, useLayoutEffect } from 'react';
import type { DashboardChart } from '../stores/dashboardStore';
import { chartRect, snapCanvasRect, isEditingTarget, type Rect, type SnapGuide } from '@/lib/dashboardDesign';

export const CANVAS_WIDTH = 1920;
export const CANVAS_HEIGHT = 1080;
export const GRID_SIZE = 20;
export const MIN_CHART_WIDTH = 200;
export const MIN_CHART_HEIGHT = 150;
export const MIN_WIDGET_WIDTH = 100;
export const MIN_WIDGET_HEIGHT = 36;
export const DEFAULT_CHART_SIZE = { w: 400, h: 300 };
export const DEFAULT_WIDGET_SIZE = { w: 300, h: 60 };

type Point = { x: number; y: number };
type Gesture = {
  kind: 'drag' | 'resize' | 'pan'; pointerId: number; target: HTMLElement;
  start: Point; original: Rect; current: Rect; scale: number;
  chartId?: number; others: Rect[]; moved: boolean; minimum: { w: number; h: number };
};

export function useCanvasInteraction(opts: {
  allCharts: DashboardChart[];
  onDragEnd: (chartId: number, position: Point) => void;
  onResizeEnd: (chartId: number, position: Rect) => void;
  onSelectElement: (chart: DashboardChart | null) => void;
  autoFit?: boolean;
  viewportKey?: string;
  viewportSize?: { width: number; height: number };
  panMode?: boolean;
  interactionLocked?: boolean;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [canvasSize, setCanvasSize] = useState({ width: CANVAS_WIDTH, height: CANVAS_HEIGHT });
  const [gridSize, setGridSize] = useState(GRID_SIZE);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [scale, setScale] = useState(0.6);
  const [panOffset, setPanOffset] = useState<Point>({ x: 0, y: 0 });
  const [draggingChart, setDraggingChart] = useState<number | null>(null);
  const [dragPosition, setDragPosition] = useState<Point>({ x: 0, y: 0 });
  const [resizingChart, setResizingChart] = useState<number | null>(null);
  const [resizePosition, setResizePosition] = useState<Rect>({ x: 0, y: 0, w: 400, h: 300 });
  const [isPanning, setIsPanning] = useState(false);
  const [guides, setGuides] = useState<SnapGuide[]>([]);
  const gesture = useRef<Gesture | null>(null);
  const frame = useRef<number | null>(null);
  const pending = useRef<PointerEvent | null>(null);
  const spacePressed = useRef(false);
  const fitting = useRef(true);
  const elementInteraction = useRef(false);
  const cancelRef = useRef<() => void>(() => {});
  const size = opts.viewportSize || canvasSize;
  const previousSize = useRef({ ...size, key: opts.viewportKey });
  useLayoutEffect(() => {
    const previous = previousSize.current;
    if (previous.key === opts.viewportKey && !fitting.current && (previous.width !== size.width || previous.height !== size.height)) {
      // 页面增高或缩短时保持左上角不跳动，避免松手后卡片偏离指针落点。
      setPanOffset(p => ({ x: p.x + (size.width - previous.width) * scale / 2, y: p.y + (size.height - previous.height) * scale / 2 }));
    }
    previousSize.current = { ...size, key: opts.viewportKey };
  }, [size.width, size.height, opts.viewportKey, scale]);
  const latest = useRef({ ...opts, canvasSize: size, gridSize, snapEnabled });
  latest.current = { ...opts, canvasSize: size, gridSize, snapEnabled };
  const isInteracting = useCallback(() => !!gesture.current || elementInteraction.current, []);
  const beginElementInteraction = useCallback(() => {
    if (isInteracting() || latest.current.interactionLocked) return false;
    elementInteraction.current = true;
    fitting.current = false;
    return true;
  }, [isInteracting]);
  const endElementInteraction = useCallback(() => { elementInteraction.current = false; }, []);

  const start = useCallback((e: React.PointerEvent, kind: Gesture['kind'], chartId?: number) => {
    if (e.defaultPrevented || isInteracting() || latest.current.interactionLocked || e.isPrimary === false || (e.button !== 0 && !(kind === 'pan' && e.button === 1)) || isEditingTarget(e.target)) return;
    const chart = latest.current.allCharts.find(c => c.id === chartId);
    if (kind !== 'pan' && !chart) return;
    e.preventDefault(); e.stopPropagation();
    const target = (kind !== 'pan' && (e.currentTarget as HTMLElement).closest<HTMLElement>('.adh-cell')) || canvasRef.current || e.currentTarget as HTMLElement;
    const original = chart ? chartRect(chart) : { ...panOffset, w: 0, h: 0 };
    const widget = chart?.chart_type.startsWith('widget_');
    gesture.current = { kind, chartId, pointerId: e.pointerId, target, start: { x: e.clientX, y: e.clientY },
      original, current: original, scale, moved: false,
      others: latest.current.allCharts.filter(c => c.id !== chartId).map(chartRect),
      minimum: { w: widget ? MIN_WIDGET_WIDTH : MIN_CHART_WIDTH, h: widget ? MIN_WIDGET_HEIGHT : MIN_CHART_HEIGHT } };
    // 捕获在稳定的视口节点上，选中图表产生的新工具条不会中断捕获。
    target.setPointerCapture?.(e.pointerId);
    if (kind !== 'pan' || (!spacePressed.current && !latest.current.panMode && e.button === 0)) latest.current.onSelectElement(chart || null);
  }, [scale, panOffset, isInteracting]);
  const handleDragStart = useCallback((e: React.PointerEvent, id: number) => {
    if (spacePressed.current || latest.current.panMode || e.button === 1) start(e, 'pan');
    else start(e, 'drag', id);
  }, [start]);
  const handleResizeStart = useCallback((e: React.PointerEvent, id: number) => start(e, 'resize', id), [start]);
  const handlePanStart = useCallback((e: React.PointerEvent) => {
    const target = e.target as HTMLElement;
    if (e.button === 1 || spacePressed.current || latest.current.panMode || !target.closest?.('[data-chart-id],.adh-cell,button,input,select,textarea,[role="separator"]')) start(e, 'pan');
  }, [start]);

  useEffect(() => {
    const apply = (e: PointerEvent) => {
      const g = gesture.current;
      if (!g || e.pointerId !== g.pointerId) return;
      const dx = e.clientX - g.start.x, dy = e.clientY - g.start.y;
      if (!g.moved && Math.hypot(dx, dy) < 3) return;
      g.moved = true;
      fitting.current = false;
      if (g.kind === 'pan') {
        setIsPanning(true);
        g.current = { ...g.original, x: g.original.x + dx, y: g.original.y + dy };
        setPanOffset({ x: g.current.x, y: g.current.y });
        return;
      }
      const raw = g.kind === 'drag'
        ? { ...g.original, x: g.original.x + dx / g.scale, y: g.original.y + dy / g.scale }
        : { ...g.original, w: g.original.w + dx / g.scale, h: g.original.h + dy / g.scale };
      const { canvasSize, gridSize, snapEnabled } = latest.current;
      const next = snapCanvasRect(raw, canvasSize, g.others, gridSize, g.scale, g.kind === 'resize', snapEnabled && !e.altKey, g.minimum);
      g.current = next.rect;
      setGuides(next.guides);
      if (g.kind === 'drag') { setDraggingChart(g.chartId!); setDragPosition(next.rect); }
      else { setResizingChart(g.chartId!); setResizePosition(next.rect); }
    };
    const clearFrame = () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null; pending.current = null;
    };
    const clear = () => {
      clearFrame();
      const g = gesture.current;
      gesture.current = null;
      if (g?.target.hasPointerCapture?.(g.pointerId)) g.target.releasePointerCapture(g.pointerId);
      setDraggingChart(null); setResizingChart(null); setIsPanning(false); setGuides([]);
    };
    const move = (e: PointerEvent) => {
      if (!gesture.current || gesture.current.pointerId !== e.pointerId) return;
      pending.current = e;
      if (frame.current !== null) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = null;
        if (pending.current) apply(pending.current);
        pending.current = null;
      });
    };
    const up = (e: PointerEvent) => {
      const g = gesture.current;
      if (!g || e.pointerId !== g.pointerId) return;
      clearFrame(); apply(e);
      if (g.moved) {
        if (g.kind === 'drag') latest.current.onDragEnd(g.chartId!, { x: g.current.x, y: g.current.y });
        if (g.kind === 'resize') latest.current.onResizeEnd(g.chartId!, g.current);
      }
      clear();
    };
    const cancel = () => {
      if (gesture.current?.kind === 'pan') setPanOffset({ x: gesture.current.original.x, y: gesture.current.original.y });
      clear();
    };
    cancelRef.current = cancel;
    const cancelledPointer = (e: PointerEvent) => { if (gesture.current?.pointerId === e.pointerId) cancel(); };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && gesture.current) { e.preventDefault(); cancel(); }
      if (e.code === 'Space' && canvasRef.current && !isEditingTarget(e.target) && !document.querySelector('[role="dialog"],[role="menu"]')) { e.preventDefault(); spacePressed.current = true; }
    };
    const keyup = (e: KeyboardEvent) => { if (e.code === 'Space') spacePressed.current = false; };
    const blur = () => { spacePressed.current = false; cancel(); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancelledPointer);
    window.addEventListener('lostpointercapture', cancelledPointer);
    window.addEventListener('keydown', key);
    window.addEventListener('keyup', keyup);
    window.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancelledPointer); window.removeEventListener('lostpointercapture', cancelledPointer);
      window.removeEventListener('keydown', key); window.removeEventListener('keyup', keyup); window.removeEventListener('blur', blur);
      clearFrame();
      const g = gesture.current; gesture.current = null;
      if (g?.target.hasPointerCapture?.(g.pointerId)) g.target.releasePointerCapture(g.pointerId);
    };
  }, []);

  useEffect(() => { cancelRef.current(); spacePressed.current = false; }, [opts.viewportKey, opts.interactionLocked]);

  // 非被动监听确保滚轮缩放不同时滚动页面，缩放锚点固定在鼠标位置。
  useEffect(() => {
    const wheel = (e: WheelEvent) => {
      const el = canvasRef.current;
      if (!el || !(e.target instanceof Node) || !el.contains(e.target)) return;
      e.preventDefault();
      if (isInteracting() || latest.current.interactionLocked) return;
      fitting.current = false;
      const bounds = el.getBoundingClientRect();
      const next = Math.min(2, Math.max(0.01, scale * Math.exp(-e.deltaY * 0.002)));
      const factor = next / scale;
      setPanOffset(p => ({ x: p.x * factor + (e.clientX - bounds.left - bounds.width / 2) * (1 - factor),
        y: p.y * factor + (e.clientY - bounds.top - bounds.height / 2) * (1 - factor) }));
      setScale(next);
    };
    window.addEventListener('wheel', wheel, { passive: false });
    return () => window.removeEventListener('wheel', wheel);
  }, [scale, isInteracting]);
  const zoomIn = useCallback(() => { if (!isInteracting() && !latest.current.interactionLocked) { fitting.current = false; setScale(v => Math.min(2, v * 1.2)); } }, [isInteracting]);
  const zoomOut = useCallback(() => { if (!isInteracting() && !latest.current.interactionLocked) { fitting.current = false; setScale(v => Math.max(0.01, v / 1.2)); } }, [isInteracting]);
  const zoomTo = useCallback((value: number) => {
    if (isInteracting() || latest.current.interactionLocked || !Number.isFinite(value)) return;
    fitting.current = false; setScale(Math.max(0.01, Math.min(2, value))); setPanOffset({ x: 0, y: 0 });
  }, [isInteracting]);
  const resetZoom = useCallback(() => {
    const el = canvasRef.current;
    if (!el || !el.clientWidth || !el.clientHeight || isInteracting() || latest.current.interactionLocked) return;
    fitting.current = true;
    setScale(Math.max(0.01, Math.min((el.clientWidth - 64) / size.width, (el.clientHeight - 64) / size.height, 1)));
    setPanOffset({ x: 0, y: 0 });
  }, [size.width, size.height, isInteracting]);
  useEffect(() => { fitting.current = true; }, [opts.viewportKey]);
  useEffect(() => {
    const el = canvasRef.current;
    if (!opts.autoFit || !el) return;
    const measure = () => { if (fitting.current) resetZoom(); };
    const observer = new ResizeObserver(measure);
    observer.observe(el); measure();
    return () => observer.disconnect();
  }, [opts.autoFit, opts.viewportKey, resetZoom]);

  return { canvasRef, surfaceRef, canvasSize: size, setCanvasSize, gridSize, setGridSize, snapEnabled, setSnapEnabled,
    scale, setScale, panOffset, setPanOffset, draggingChart, dragPosition, resizingChart, resizePosition, isPanning, guides,
    handleDragStart, handleResizeStart, handlePanStart, zoomIn, zoomOut, zoomTo, resetZoom,
    isInteracting, beginElementInteraction, endElementInteraction };
}
