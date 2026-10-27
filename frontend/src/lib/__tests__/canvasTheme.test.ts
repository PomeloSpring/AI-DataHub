import { describe, expect, it } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { CANVAS_COLORS, backdropStyle, canvasRectToScreen, cornerDotStyle, dotBackgroundStyle, fitBackgroundSize, gridBackgroundStyle } from '../canvasTheme';

const srcRoot = path.resolve(__dirname, '../..');
const read = (rel: string) => fs.readFileSync(path.join(srcRoot, rel), 'utf8');

describe('画布坐标 → 屏幕坐标', () => {
  const surface = { width: 1000, height: 500 };
  const viewport = { width: 800, height: 600 };

  it('surface 中心恒定居中，原点的换算与 CanvasRulers 同式', () => {
    const center = canvasRectToScreen({ x: 500, y: 250, w: 0, h: 0 }, surface, viewport, 0.5, { x: 0, y: 0 });
    expect(center.x).toBeCloseTo(400);
    expect(center.y).toBeCloseTo(300);
    // 画布坐标 0 的屏幕位置 = (viewport - surface*scale)/2 + pan，与标尺实现一致
    const origin = canvasRectToScreen({ x: 0, y: 0, w: 0, h: 0 }, surface, viewport, 0.5, { x: 0, y: 0 });
    expect(origin.x).toBeCloseTo((viewport.width - surface.width * 0.5) / 2);
    expect(origin.y).toBeCloseTo((viewport.height - surface.height * 0.5) / 2);
  });

  it('缩放与平移同时作用于位置与尺寸', () => {
    const rect = canvasRectToScreen({ x: 0, y: 0, w: 200, h: 100 }, surface, viewport, 0.5, { x: 20, y: -10 });
    expect(rect).toEqual({ x: 170, y: 165, w: 100, h: 50 });
  });

  it('100% 且无平移时为恒等映射', () => {
    const rect = canvasRectToScreen({ x: 120, y: 60, w: 30, h: 24 }, { width: 800, height: 600 }, { width: 800, height: 600 }, 1, { x: 0, y: 0 });
    expect(rect).toEqual({ x: 120, y: 60, w: 30, h: 24 });
  });
});

describe('画布背景样式', () => {
  it('page 参考标记是方块转角的点星，绝不画任何连续线条', () => {
    const dots = cornerDotStyle(121.33);
    expect(dots.backgroundImage).toContain('radial-gradient(circle at 0 0');
    expect(dots.backgroundImage).not.toMatch(/#[0-9a-f]/i);
    // 不得出现条状（linear-gradient）——把画布变回方格纸的根源
    expect(dots.backgroundImage).not.toContain('linear-gradient');
    // 一个 tile 一个点，点落在方块转角
    expect(dots.backgroundSize).toBe('121.33px 121.33px');
    expect(CANVAS_COLORS.gridDot).toContain('hsl(var(--muted-foreground)');
  });

  it('大屏方形网格仍保留横竖线与主线，但比点星更淡', () => {
    const grid = gridBackgroundStyle(20, 20);
    expect(grid.backgroundImage).toContain('to bottom');
    expect(grid.backgroundSize).toContain('100px 100px');
    expect(CANVAS_COLORS.gridMinor).toBe('hsl(var(--border) / 0.4)');
  });

  it('点阵与背景渐变都走主题变量', () => {
    const dots = dotBackgroundStyle(20);
    expect(dots.backgroundSize).toBe('20px 20px');
    expect(dots.backgroundImage).toContain('hsl(var(--foreground)');
    const backdrop = backdropStyle();
    expect(backdrop.backgroundColor).toBe('hsl(var(--muted))');
    expect(backdrop.backgroundImage).toContain('hsl(var(--background)');
  });
});

describe('静态护栏：画布覆盖层不得另写配色', () => {
  // 历史缺陷：选中描边 #3b82f6、吸附线 fuchsia-500、手柄 blue-500/#64748b 各自为政，
  // 既不随主题换装，也让 page / screen 两种模式看起来像两个产品。
  const files = [
    'components/editor/PageLayoutEditor.tsx',
    'components/editor/SelectionOverlay.tsx',
    'components/editor/CanvasHint.tsx',
    'components/DashboardCanvas.tsx',
    'pages/DashboardEditor.tsx',
  ];
  it.each(files)('%s 不含裸 hex 强调色与品红辅助线', file => {
    const code = read(file);
    expect(code).not.toMatch(/#3b82f6|#64748b|#94a3b8|fuchsia|blue-500/i);
  });
  it('覆盖层配色全部来自 canvasTheme', () => {
    expect(CANVAS_COLORS.selection).toBe('hsl(var(--primary))');
    expect(CANVAS_COLORS.guide).toContain('hsl(var(--primary)');
  });
  it.each(files)('%s 不再用 scale(1/scale) 反向补偿覆盖层', file => {
    expect(read(file)).not.toMatch(/scale\(\s*1\s*\/\s*scale\s*\)/);
  });
});

// 缩略图上的 px 平铺图案是按设计宽画的，原样落到 400px 宽的卡片上会密成一片（“背景重复”）
describe('fitBackgroundSize：px 平铺尺寸换算为画布占比', () => {
  const canvas = { width: 1440, height: 900 };
  it('px 两值换算成 W/H 百分比，随缩略图等比缩小', () => {
    const [w, h] = fitBackgroundSize('40px 40px', canvas)!.split(' ');
    expect(parseFloat(w)).toBeCloseTo(40 / 1440 * 100);
    expect(parseFloat(h)).toBeCloseTo(40 / 900 * 100);
  });
  it.each(['22px 22px', '40px 40px'])('%s 不再以 px 单位输出', size => {
    expect(fitBackgroundSize(size, canvas)).not.toMatch(/px/);
  });
  it('百分比/cover/contain/auto/空值原样透传（不猜）', () => {
    expect(fitBackgroundSize('50% 50%', canvas)).toBe('50% 50%');
    expect(fitBackgroundSize('cover', canvas)).toBe('cover');
    expect(fitBackgroundSize('auto', canvas)).toBe('auto');
    expect(fitBackgroundSize(undefined, canvas)).toBeUndefined();
    expect(fitBackgroundSize('none', canvas)).toBe('none');
  });
});
