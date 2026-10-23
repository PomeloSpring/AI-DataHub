import type { ChartTypeItem } from '@/components/DashboardChart';
import type { VisComponent } from '@/api/visLibrary';

/** 从左侧组件库拖入画布时携带的载荷（图表类型 / 字模创建的图表） */
export interface LibraryDragData { item: ChartTypeItem; component?: VisComponent }

/** 画布是唯一落点区；两种布局模式共用同一 id，避免各写一套 drop 判定 */
export const LIBRARY_DROP_ZONE = 'canvas';

export const libraryDragId = (kind: 'chart' | 'vis', key: string | number) => `${kind}:${key}`;

/** 落点矩形：page 模式是网格行列，screen 模式是画布像素 */
export type DropGhost =
  | { mode: 'page'; x: number; y: number; w: number; h: number; /** 真实图表类型：预览要按它的最小尺寸夹取，才能与真实插入同构 */ chartType: string }
  | { mode: 'screen'; x: number; y: number; w: number; h: number };

/**
 * 拖拽过程中的**实时指针**客户端坐标。
 *
 * dnd-kit 的 `activatorEvent` 是 pointerdown（拖拽起点）那一刻的事件，
 * 直接拿它的 clientX/Y 当落点会永远停在字模库里 —— 历史缺陷正是如此：
 * `pointerInViewport(起点)` 恒 false，图表怎么拖都放不进画布、松手就缩回去。
 * 必须叠加 `delta`（相对起点的位移）才是当前指针。
 */
export function dragPointer(
  activatorEvent: unknown,
  delta: { x?: number; y?: number } | undefined | null,
): { x: number; y: number } | null {
  const ev = activatorEvent as { clientX?: unknown; clientY?: unknown } | undefined;
  // 用 isFinite 而非 typeof：NaN/Infinity 也能过 typeof 判断，会把落点算成 NaN
  const startX = ev?.clientX, startY = ev?.clientY;
  if (!Number.isFinite(startX)) return null;
  const dx = Number.isFinite(delta?.x) ? (delta!.x as number) : 0;
  const dy = Number.isFinite(delta?.y) ? (delta!.y as number) : 0;
  return {
    x: (startX as number) + dx,
    y: (Number.isFinite(startY) ? startY as number : 0) + dy,
  };
}
