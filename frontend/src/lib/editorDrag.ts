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
