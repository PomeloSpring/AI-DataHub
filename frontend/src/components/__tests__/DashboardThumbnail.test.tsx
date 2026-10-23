import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { DashboardThumbnail } from '../DashboardCanvas';
import { pagePixelRect, resolvePageLayout } from '@/lib/dashboardPageLayout';

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
    const width = pagePixelRect({ x: 0, y: 0, w: 12, h: 10 }, 'desktop').w;
    expect(left(tiles[0])).toBe(ratio(expected[0], width));
    expect(left(tiles[1])).toBe(ratio(expected[1], width));
    // 并且第二块不等于旧像素坐标 500 换算出的值
    expect(left(tiles[1])).not.toBe(`${(500 / width) * 100}%`);
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
