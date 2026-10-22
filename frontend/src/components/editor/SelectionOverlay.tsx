import type { PointerEvent as ReactPointerEvent } from 'react';
import { Settings, Trash2 } from 'lucide-react';
import { CANVAS_COLORS, OVERLAY } from '@/lib/canvasTheme';
import type { Rect } from '@/lib/dashboardDesign';

interface Props {
  /** 选中卡片在视口内的屏幕矩形（由 canvasRectToScreen 算出，已含缩放与平移） */
  screen: Rect;
  /** 画布坐标系下的当前尺寸，供 aria-valuenow 与键盘缩放使用 */
  size?: { w: number; h: number };
  minimum?: { w: number; h: number };
  onConfig: () => void;
  onDelete: () => void;
  onResizeStart: (e: ReactPointerEvent) => void;
  /** 键盘缩放：按轴向增减尺寸（无鼠标用户也能改大小） */
  onNudge?: (dx: number, dy: number) => void;
  /** 工具条左侧的尺寸读数 */
  readout?: string;
}

/**
 * 选中态覆盖层：渲染在缩放层之外，尺寸恒定为屏幕像素。
 *
 * 历史缺陷是工具条挂在被缩放的 surface 内部、靠逐层反向缩放补偿，
 * 50% 缩放时放大 2 倍直接压住卡片标题；这里改为按屏幕矩形定位并优先放在卡片上方。
 */
export default function SelectionOverlay({ screen, size, minimum, onConfig, onDelete, onResizeStart, onNudge, readout }: Props) {
  const fitsAbove = screen.y - OVERLAY.toolbarHeight - OVERLAY.edge >= 0;
  const toolbarTop = fitsAbove ? screen.y - OVERLAY.toolbarHeight - OVERLAY.edge : screen.y + OVERLAY.edge;
  const toolbarLeft = Math.max(OVERLAY.edge, screen.x + screen.w - OVERLAY.toolbarHeight * 2 - (readout ? 104 : 0));
  return <div className="pointer-events-none absolute inset-0 z-30" data-selection-overlay data-testid="selection-overlay">
    <div className="pointer-events-auto absolute flex items-center rounded-md border bg-background/95 shadow-sm"
      style={{ left: toolbarLeft, top: toolbarTop, height: OVERLAY.toolbarHeight }}>
      {readout && <span className="px-2 font-mono text-[10px] text-muted-foreground">{readout}</span>}
      <button type="button" className="px-1.5 hover:text-foreground" aria-label="配置图表" title="配置" onClick={onConfig}>
        <Settings className="h-3.5 w-3.5" />
      </button>
      <button type="button" className="px-1.5 hover:text-destructive" aria-label="删除图表" title="删除" onClick={onDelete}>
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
    <div role="slider" tabIndex={onNudge ? 0 : -1} aria-label="调整图表大小"
      aria-valuenow={size ? Math.round(size.w) : undefined} aria-valuetext={size ? `宽 ${Math.round(size.w)}，高 ${Math.round(size.h)}` : undefined}
      aria-valuemin={minimum?.w} aria-valuemax={10000}
      onKeyDown={e => {
        if (!onNudge) return;
        const step = e.shiftKey ? 1 : 10;
        if (e.key === 'ArrowRight') { e.preventDefault(); onNudge(step, 0); }
        if (e.key === 'ArrowLeft') { e.preventDefault(); onNudge(-step, 0); }
        if (e.key === 'ArrowDown') { e.preventDefault(); onNudge(0, step); }
        if (e.key === 'ArrowUp') { e.preventDefault(); onNudge(0, -step); }
      }}
      className="pointer-events-auto absolute cursor-se-resize rounded-sm border-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      style={{ left: screen.x + screen.w - OVERLAY.handleSize / 2, top: screen.y + screen.h - OVERLAY.handleSize / 2,
        width: OVERLAY.handleSize, height: OVERLAY.handleSize,
        background: CANVAS_COLORS.handle, borderColor: CANVAS_COLORS.handleBorder }}
      onPointerDown={onResizeStart} />
  </div>;
}
