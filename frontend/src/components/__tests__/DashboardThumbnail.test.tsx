import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { DashboardThumbnail } from '../DashboardCanvas';
import { pageCanvasSize, pageColumnPx, pagePixelRect, resolvePageLayout } from '@/lib/dashboardPageLayout';

const chart = (id: number, chart_type: string, position: any, config: any = {}) =>
  ({ id, dashboard_id: 1, name: `c${id}`, chart_type, sql_query: '', config, position } as any);

describe('DashboardThumbnail：列表效果图', () => {
  it('page 模式走 resolvePageLayout，与真实画布同一套布局源', () => {
    const charts = [
      chart(1, 'bar', { x: 0, y: 0, w: 300, h: 200 }, { pageRect: { desktop: { x: 0, y: 0, w: 6, h: 4 } } }),
      chart(2, 'line', { x: 500, y: 0, w: 300, h: 200 }, { pageRect: { desktop: { x: 6, y: 0, w: 6, h: 4 } } }),
    ];
    const dashboard = { filters: { design: { page: { version: 2 } } }, charts } as any;
    const { container } = render(<DashboardThumbnail dashboard={dashboard} />);

    // 布局源必须是 resolvePageLayout：历史缺陷是用 chartRect 像素另算，与画布对不上
    const items = resolvePageLayout(dashboard, 'desktop');
    const expected = items.map(i => pagePixelRect(i.rect, 'desktop'));
    const tiles = Array.from(container.querySelectorAll('.absolute.overflow-hidden'));
    expect(tiles).toHaveLength(2);
    // 第二块的 left% 应等于 page 布局换算出的 x，而不是 chart.position 的 500
    const left = (el: Element) => (el as HTMLElement).style.left;
    const ratio = (r: any, size: number) => `${(r.x / size) * 100}%`;
    // 分母是画布设计宽（v3 下 12 列已不再是整屏，必须用 pageCanvasSize 与组件同源）
    const width = pageCanvasSize(items, 'desktop').width;
    expect(left(tiles[0])).toBe(ratio(expected[0], width));
    expect(left(tiles[1])).toBe(ratio(expected[1], width));
    // 并且第二块不等于旧像素坐标 500 换算出的值
    expect(left(tiles[1])).not.toBe(`${(500 / width) * 100}%`);
  });

  it('只有每图表 config.pageLayout（无看板级 design.page）时仍按网格画', () => {
    // 脚本/接口生成的看板就是这种：design 为空，网格只写在图上。
    // 曾误判成大屏，按默认 1920×1080 算百分比，内容只占顶部一小块（“没有自适应填充”）
    const charts = [
      chart(1, 'bar', { x: 0, y: 0, w: 300, h: 200 }, { pageLayout: { version: 3, desktop: { x: 0, y: 0, w: 24, h: 70 } } }),
      chart(2, 'line', { x: 500, y: 0, w: 300, h: 200 }, { pageLayout: { version: 3, desktop: { x: 24, y: 0, w: 24, h: 70 } } }),
    ];
    const dashboard = { filters: {}, charts } as any;
    const { container } = render(<DashboardThumbnail dashboard={dashboard} />);
    const tiles = Array.from(container.querySelectorAll<HTMLElement>('.absolute.overflow-hidden'));
    expect(tiles).toHaveLength(2);
    // 第二块从 24/48 列处开始（列偏移含 gap，故略大于半屏），而不是大屏像素 500/1920
    expect(parseFloat(tiles[1].style.left)).toBeCloseTo(24 * pageColumnPx('desktop') / 1440 * 100, 1);
    expect(parseFloat(tiles[1].style.left)).toBeGreaterThan(50);
    expect(parseFloat(tiles[0].style.width)).toBeCloseTo((24 * pageColumnPx('desktop') - 16) / 1440 * 100, 0);
  });

  it('超长看板只取 2 倍高的预览框，按真实比例裁顶部', () => {
    const charts = [chart(1, 'bar', { x: 0, y: 0, w: 300, h: 200 },
      { pageLayout: { version: 3, desktop: { x: 0, y: 0, w: 48, h: 4000 } } })];
    const { container } = render(<DashboardThumbnail dashboard={{ filters: {}, charts } as any} />);
    const box = container.querySelector('[style*="aspect-ratio"]') as HTMLElement;
    expect(box.style.aspectRatio.replace(/\s/g, '')).toBe('1440/2880');
  });

  it('px 平铺背景按画布比例换算，缩略图不再密铺', () => {
    const charts = [chart(1, 'bar', { x: 0, y: 0, w: 300, h: 200 },
      { pageLayout: { version: 3, desktop: { x: 0, y: 0, w: 24, h: 70 } } })];
    const { container } = render(<DashboardThumbnail dashboard={{ filters: { design: { page: { version: 3 }, canvas: { backgroundImage: 'linear-gradient(#0000 1px, transparent 1px)', backgroundSize: '40px 40px' } } }, charts } as any} />);
    const box = container.querySelector('[style*="aspect-ratio"]') as HTMLElement;
    expect(box.style.backgroundSize).toContain('%');
    expect(box.style.backgroundSize).not.toContain('40px');
  });

  it('图表类型可辨识：折线画折线、表格画行，而不是清一色四根假柱子', () => {
    const charts = [
      chart(1, 'line_chart', { x: 0, y: 0, w: 300, h: 200 }),
      chart(2, 'table', { x: 0, y: 300, w: 300, h: 200 }),
    ];
    const { container } = render(<DashboardThumbnail dashboard={{ filters: {}, charts } as any} />);
    expect(container.querySelector('polyline')).not.toBeNull();   // 折线
    expect(container.querySelectorAll('polyline')).toHaveLength(1); // 表格没有折线
    expect(container.querySelector('svg')).not.toBeNull();
  });

  it('控件类图表画成药丸，不画柱阵', () => {
    const charts = [chart(1, 'widget_button', { x: 0, y: 0, w: 120, h: 40 })];
    const { container } = render(<DashboardThumbnail dashboard={{ filters: {}, charts } as any} />);
    expect(container.querySelector('.rounded-full')).not.toBeNull();
  });

  it('空画布给出可读占位，不留白', () => {
    const { getByText } = render(<DashboardThumbnail dashboard={{ filters: {}, charts: [] } as any} />);
    expect(getByText('空白画布')).toBeTruthy();
  });
});
