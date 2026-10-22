import type { CSSProperties } from 'react';
import type { Rect } from './dashboardDesign';

/**
 * 画布与覆盖层的唯一配色源（对齐 ui-vis-library-style.md「唯一样式源」）。
 *
 * page（网格）与 screen（大屏）两种编辑模式必须共用这里，禁止再写裸 hex：
 * 历史缺陷是选中描边 #3b82f6、吸附线 fuchsia-500、手柄 #64748b / blue-500 各自为政，
 * 既不随主题换装，也让两种模式看起来像两个产品。
 */
export const CANVAS_COLORS = {
  /** 画布外区域（视口底） */
  backdrop: 'hsl(var(--muted))',
  /** 网格参考点（page 模式：方块转角的点星，不画线） */
  gridDot: 'hsl(var(--muted-foreground) / 0.32)',
  /** 大屏画布的方形网格：细线 + 每 majorEvery 格一条主线 */
  gridMinor: 'hsl(var(--border) / 0.4)',
  gridMajor: 'hsl(var(--border) / 0.75)',
  /** 画布表面描边与投影（分层投影，避免一个硬阴影块） */
  surfaceBorder: 'hsl(var(--border))',
  surfaceShadow: '0 1px 2px hsl(0 0% 0% / 0.05), 0 8px 24px hsl(0 0% 0% / 0.08), 0 24px 60px hsl(0 0% 0% / 0.10)',
  /** 选中 / 悬停分级：selected 实描边，hover 半透明细描边 */
  selection: 'hsl(var(--primary))',
  hoverOutline: 'hsl(var(--primary) / 0.4)',
  /** 吸附辅助线：主色系而非扎眼的品红 */
  guide: 'hsl(var(--primary) / 0.75)',
  /** 缩放手柄 */
  handle: 'hsl(var(--primary))',
  handleBorder: 'hsl(var(--background))',
  /** 拖入落点预览 */
  dropGhostBorder: 'hsl(var(--primary))',
  dropGhostFill: 'hsl(var(--primary) / 0.1)',
  /** 大屏模式点阵背景 */
  dot: 'hsl(var(--foreground) / 0.09)',
} as const;

/** 覆盖层尺寸固定为屏幕像素（不随画布缩放），避免小缩放下工具条放大数倍压住标题 */
export const OVERLAY = { toolbarHeight: 26, handleSize: 12, edge: 6 } as const;

/**
 * page（网格）模式的参考标记：在方块的**四个转角**打点星，不画任何连续线条。
 *
 * 曾经逐行/逐列画线，20px 行量子 + 121px 列宽直接把画布变成方格坐标纸（“像草纸”）。
 * 现在只在 blockPx × blockPx 方块的角上落一个点：既给出吸附参照，又不引入条状噪声。
 * blockPx 取列宽，于是点星自然把每列/每行的网格单元边界标出来。
 */
export function cornerDotStyle(blockPx: number, size = 3.5): CSSProperties {
  const half = size / 2;
  return {
    backgroundImage: `radial-gradient(circle at 0 0, ${CANVAS_COLORS.gridDot} ${half}px, transparent ${half + 0.5}px)`,
    backgroundSize: `${blockPx}px ${blockPx}px`,
  };
}

/** 大屏画布的方形网格：线宽固定 1 屏幕像素，每 majorEvery 格一条主线 */
export function gridBackgroundStyle(columnPx: number, rowPx: number, majorEvery = 5): CSSProperties {
  const majorX = `${columnPx * majorEvery}px`;
  const majorY = `${rowPx * majorEvery}px`;
  return {
    backgroundImage: [
      `linear-gradient(to right, ${CANVAS_COLORS.gridMajor} 1px, transparent 1px)`,
      `linear-gradient(to bottom, ${CANVAS_COLORS.gridMajor} 1px, transparent 1px)`,
      `linear-gradient(to right, ${CANVAS_COLORS.gridMinor} 1px, transparent 1px)`,
      `linear-gradient(to bottom, ${CANVAS_COLORS.gridMinor} 1px, transparent 1px)`,
    ].join(', '),
    backgroundSize: `${majorX} ${majorY}, ${majorX} ${majorY}, ${columnPx}px ${rowPx}px, ${columnPx}px ${rowPx}px`,
  };
}

/** 编辑视口（画布以外区域）：给一点纵向渐变，让画布平面“浮”在背景上而不是一片死平 */
export function backdropStyle(): CSSProperties {
  return {
    backgroundColor: CANVAS_COLORS.backdrop,
    backgroundImage: `radial-gradient(120% 90% at 50% 0%, hsl(var(--background) / 0.55) 0%, transparent 60%)`,
  };
}

/** 大屏模式点阵背景：步长跟随 gridSize，不再写死 16px */
export function dotBackgroundStyle(step: number): CSSProperties {
  return {
    backgroundImage: `radial-gradient(circle, ${CANVAS_COLORS.dot} 1px, transparent 1px)`,
    backgroundSize: `${step}px ${step}px`,
  };
}

/**
 * 画布坐标 → 视口屏幕坐标。
 *
 * surface 以 `left/top: 50%` + 负 margin 居中，再做 `translate(pan) scale(s)` 且
 * `transformOrigin: center`，故点 p 的屏幕位置为 s·p + pan + viewport/2 − s·surface/2。
 * 覆盖层（选中工具条、缩放手柄）用这个换算渲染在缩放层之外，尺寸就恒定为屏幕像素。
 */
export function canvasRectToScreen(rect: Rect, surface: { width: number; height: number },
  viewport: { width: number; height: number }, scale: number, pan: { x: number; y: number }): Rect {
  return {
    x: scale * rect.x + pan.x + viewport.width / 2 - scale * surface.width / 2,
    y: scale * rect.y + pan.y + viewport.height / 2 - scale * surface.height / 2,
    w: rect.w * scale, h: rect.h * scale,
  };
}
