import { useState, useCallback, useRef, useEffect } from 'react';
import type { DashboardChart } from '../stores/dashboardStore';
import { screenToCanvas, clampPosition, isEditingTarget } from '@/lib/dashboardDesign';

// Canvas constants
export const CANVAS_WIDTH = 1920;
export const CANVAS_HEIGHT = 1080;
export const GRID_SIZE = 20;
export const MIN_CHART_WIDTH = 200;
export const MIN_CHART_HEIGHT = 150;
export const MIN_WIDGET_WIDTH = 100;
export const MIN_WIDGET_HEIGHT = 36;
export const DEFAULT_CHART_SIZE = { w: 400, h: 300 };
export const DEFAULT_WIDGET_SIZE = { w: 300, h: 60 };

interface DragState {
  chartId: number | null;
  offset: { x: number; y: number };
  position: { x: number; y: number };
}

interface ResizeState {
  chartId: number | null;
  start: { x: number; y: number; w: number; h: number };
  position: { x: number; y: number; w: number; h: number };
}

interface PanState {
  active: boolean;
  start: { x: number; y: number };
}

export function useCanvasInteraction(opts: {
  allCharts: DashboardChart[];
  onDragEnd: (chartId: number, position: { x: number; y: number }) => void;
  onResizeEnd: (chartId: number, position: { x: number; y: number; w: number; h: number }) => void;
  onSelectElement: (chart: DashboardChart | null) => void;
}) {
  const { allCharts, onDragEnd, onResizeEnd, onSelectElement } = opts;

  // Canvas state
  const canvasRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [canvasSize, setCanvasSize] = useState({ width: CANVAS_WIDTH, height: CANVAS_HEIGHT });
  const [canvasBgColor, setCanvasBgColor] = useState('');
  const [gridSize, setGridSize] = useState(GRID_SIZE);
  const [scale, setScale] = useState(0.6);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });

  // Mutable refs for interaction state (avoid re-renders during drag)
  const dragRef = useRef<DragState>({ chartId: null, offset: { x: 0, y: 0 }, position: { x: 0, y: 0 } });
  const resizeRef = useRef<ResizeState>({ chartId: null, start: { x: 0, y: 0, w: 0, h: 0 }, position: { x: 0, y: 0, w: 0, h: 0 } });
  const panRef = useRef<PanState>({ active: false, start: { x: 0, y: 0 } });

  // Visual state (triggers re-renders for visual feedback)
  const [draggingChart, setDraggingChart] = useState<number | null>(null);
  const [dragPosition, setDragPosition] = useState({ x: 0, y: 0 });
  const [resizingChart, setResizingChart] = useState<number | null>(null);
  const [resizePosition, setResizePosition] = useState({ x: 0, y: 0, w: 0, h: 0 });
  const [isPanning, setIsPanning] = useState(false);

  const snapToGrid = useCallback((value: number) => Math.round(value / gridSize) * gridSize, [gridSize]);

  // ========== Drag existing chart ==========
  const handleDragStart = useCallback((e: React.MouseEvent, chartId: number) => {
    if (e.button !== 0 || isEditingTarget(e.target)) return;
    e.stopPropagation();
    e.preventDefault();
    const chart = allCharts.find(c => c.id === chartId);
    if (!chart) return;
    const cellEl = (e.currentTarget as HTMLElement).closest('.dashboard-chart-cell') as HTMLElement;
    if (!cellEl) return;
    const rect = cellEl.getBoundingClientRect();
    const offset = { x: (e.clientX - rect.left) / scale, y: (e.clientY - rect.top) / scale };
    const position = { x: chart.position?.x ?? 0, y: chart.position?.y ?? 0 };
    dragRef.current = { chartId, offset, position };
    setDraggingChart(chartId);
    setDragPosition(position);
  }, [allCharts, scale]);

  // ========== Resize ==========
  const handleResizeStart = useCallback((e: React.MouseEvent, chartId: number) => {
    if (e.button !== 0 || isEditingTarget(e.target)) return;
    e.stopPropagation();
    e.preventDefault();
    const chart = allCharts.find(c => c.id === chartId);
    if (!chart) return;
    const start = {
      x: e.clientX, y: e.clientY,
      w: chart.position?.w ?? DEFAULT_CHART_SIZE.w,
      h: chart.position?.h ?? DEFAULT_CHART_SIZE.h,
    };
    const position = {
      x: chart.position?.x ?? 0, y: chart.position?.y ?? 0,
      w: chart.position?.w ?? DEFAULT_CHART_SIZE.w,
      h: chart.position?.h ?? DEFAULT_CHART_SIZE.h,
    };
    resizeRef.current = { chartId, start, position };
    setResizingChart(chartId);
    setResizePosition(position);
  }, [allCharts]);

  // ========== Pan ==========
  const handlePanStart = useCallback((e: React.MouseEvent) => {
    if ((e.button === 0 || e.button === 1) && (e.target === canvasRef.current || e.target === surfaceRef.current)) {
      e.preventDefault();
      panRef.current = { active: true, start: { x: e.clientX - panOffset.x, y: e.clientY - panOffset.y } };
      setIsPanning(true);
      onSelectElement(null);
    }
  }, [panOffset, onSelectElement]);

  // ========== Zoom ==========
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.min(2, Math.max(0.2, scale * delta));
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    const scaleFactor = newScale / scale;
    const newPanX = panOffset.x * scaleFactor + (mouseX - centerX) * (1 - scaleFactor);
    const newPanY = panOffset.y * scaleFactor + (mouseY - centerY) * (1 - scaleFactor);
    setScale(newScale);
    setPanOffset({ x: newPanX, y: newPanY });
  }, [scale, panOffset]);

  const zoomIn = useCallback(() => setScale(prev => Math.min(2, prev * 1.2)), []);
  const zoomOut = useCallback(() => setScale(prev => Math.max(0.2, prev * 0.8)), []);

  const resetZoom = useCallback(() => {
    const container = canvasRef.current;
    if (!container) return;
    setScale(Math.max(0.05, Math.min((container.clientWidth - 64) / canvasSize.width, (container.clientHeight - 64) / canvasSize.height, 1)));
    setPanOffset({ x: 0, y: 0 });
  }, [canvasSize]);

  // ========== Global event listeners (registered once, use refs) ==========
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (dragRef.current.chartId !== null && surfaceRef.current) {
          const point = screenToCanvas({ x: e.clientX, y: e.clientY }, surfaceRef.current.getBoundingClientRect(), scale);
          const chart = allCharts.find(c => c.id === dragRef.current.chartId);
          const newPos = clampPosition({ x: point.x - dragRef.current.offset.x, y: point.y - dragRef.current.offset.y },
            chart?.position || DEFAULT_CHART_SIZE, canvasSize, gridSize);
          dragRef.current.position = newPos;
          setDragPosition(newPos);
        } else if (resizeRef.current.chartId) {
          const chart = allCharts.find(c => c.id === resizeRef.current.chartId);
          const isWidget = chart?.chart_type?.startsWith('widget_');
          const minW = isWidget ? MIN_WIDGET_WIDTH : MIN_CHART_WIDTH;
          const minH = isWidget ? MIN_WIDGET_HEIGHT : MIN_CHART_HEIGHT;
          const deltaX = (e.clientX - resizeRef.current.start.x) / scale;
          const deltaY = (e.clientY - resizeRef.current.start.y) / scale;
          const newW = snapToGrid(Math.max(minW, resizeRef.current.start.w + deltaX));
          const newH = snapToGrid(Math.max(minH, resizeRef.current.start.h + deltaY));
          const newPos = {
            ...resizeRef.current.position,
            w: Math.min(newW, canvasSize.width - resizeRef.current.position.x),
            h: Math.min(newH, canvasSize.height - resizeRef.current.position.y),
          };
          resizeRef.current.position = newPos;
          setResizePosition(newPos);
        } else if (panRef.current.active) {
          const newPos = { x: e.clientX - panRef.current.start.x, y: e.clientY - panRef.current.start.y };
          setPanOffset(newPos);
        }
    };

    const handleMouseUp = (e: MouseEvent) => {
      handleMouseMove(e);
      if (dragRef.current.chartId) {
        onDragEnd(dragRef.current.chartId, dragRef.current.position);
        dragRef.current = { chartId: null, offset: { x: 0, y: 0 }, position: { x: 0, y: 0 } };
        setDraggingChart(null);
      } else if (resizeRef.current.chartId) {
        onResizeEnd(resizeRef.current.chartId, resizeRef.current.position);
        resizeRef.current = { chartId: null, start: { x: 0, y: 0, w: 0, h: 0 }, position: { x: 0, y: 0, w: 0, h: 0 } };
        setResizingChart(null);
      } else if (panRef.current.active) {
        panRef.current = { active: false, start: { x: 0, y: 0 } };
        setIsPanning(false);
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (isEditingTarget(e.target)) return;
      if (e.key === 'Escape') {
        if (dragRef.current.chartId) {
          dragRef.current = { chartId: null, offset: { x: 0, y: 0 }, position: { x: 0, y: 0 } };
          setDraggingChart(null);
        }
        if (resizeRef.current.chartId) {
          resizeRef.current = { chartId: null, start: { x: 0, y: 0, w: 0, h: 0 }, position: { x: 0, y: 0, w: 0, h: 0 } };
          setResizingChart(null);
        }
        if (panRef.current.active) {
          panRef.current = { active: false, start: { x: 0, y: 0 } };
          setIsPanning(false);
        }
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [scale, canvasSize, allCharts, gridSize, snapToGrid, onDragEnd, onResizeEnd]);

  return {
    // State
    canvasRef,
    surfaceRef,
    canvasSize,
    setCanvasSize,
    canvasBgColor,
    setCanvasBgColor,
    gridSize,
    setGridSize,
    scale,
    setScale,
    panOffset,
    setPanOffset,
    draggingChart,
    dragPosition,
    resizingChart,
    resizePosition,
    isPanning,
    // Handlers
    handleDragStart,
    handleResizeStart,
    handlePanStart,
    handleWheel,
    zoomIn,
    zoomOut,
    resetZoom,
  };
}
