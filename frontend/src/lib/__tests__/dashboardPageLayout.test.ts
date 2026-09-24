import { describe, expect, it } from 'vitest';
import { PAGE_COLUMNS, PAGE_ROW, pageBreakpoint, pageGap, pageMinHeight, resolvePageLayout, movePageItem, withPageRect, type PageBreakpoint } from '../dashboardPageLayout';
import { applyChartComponent, clearChartVisualOverrides, libraryThemeFilters } from '../dashboardDesign';
import { exportChart } from '@/components/DashboardExportImport';
import type { DashboardChart } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';

const chart = (id: number, type = 'bar', x = 0, y = 0): DashboardChart => ({ id, chart_type: type, name: `图表 ${id}`, config: {}, position: { x, y, w: 600, h: 120 } } as DashboardChart);
const dashboard = { filters: { design: { version: 1, canvas: { width: 1200, height: 1080 } } }, charts: [chart(10, 'widget_input'), chart(9, 'bar', 600), chart(8, 'table_value', 0, 200), chart(7, 'big_number', 600, 600)] };
const breakpoints: PageBreakpoint[] = ['desktop', 'tablet', 'mobile'];
function assertNoOverlap(items: ReturnType<typeof resolvePageLayout>) {
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const a = items[i].rect, b = items[j].rect;
    expect(a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y).toBe(false);
  }
}

describe('响应式页面布局', () => {
  it.each([[390, 'mobile'], [639, 'mobile'], [640, 'tablet'], [1023, 'tablet'], [1024, 'desktop'], [1920, 'desktop']])('容器宽度 %s 选择 %s', (width, bp) => expect(pageBreakpoint(Number(width))).toBe(bp));
  it.each(breakpoints)('%s 确定性兼容历史布局，不丢组件、不改大屏位置、无碰撞', bp => {
    const before = JSON.stringify(dashboard);
    const items = resolvePageLayout(dashboard, bp);
    expect(resolvePageLayout(dashboard, bp)).toEqual(items);
    expect(items.map(i => i.chart.id).sort()).toEqual(dashboard.charts.map(c => c.id).sort());
    expect(JSON.stringify(dashboard)).toBe(before);
    assertNoOverlap(items);
    for (const item of items) {
      expect(item.rect.x + item.rect.w).toBeLessThanOrEqual(PAGE_COLUMNS[bp]);
      expect(item.rect.h * (PAGE_ROW + pageGap(bp)) - pageGap(bp)).toBeGreaterThanOrEqual(pageMinHeight(item.chart.chart_type));
    }
  });
  it('手机保留桌面阅读顺序，不按 ID 颠倒同一行的控件与图表', () => {
    expect(resolvePageLayout(dashboard, 'mobile').map(i => i.chart.id)).toEqual(resolvePageLayout(dashboard, 'desktop').map(i => i.chart.id));
  });
  it('拖动与缩放自动避让，输入越界收敛，原对象不变', () => {
    const items = resolvePageLayout(dashboard, 'desktop');
    const before = JSON.stringify(items);
    const moved = movePageItem(items, 8, { x: -3, y: -3, w: 30, h: 1 }, 'desktop');
    assertNoOverlap(moved);
    expect(moved.find(i => i.chart.id === 8)?.rect).toMatchObject({ x: 0, y: 0, w: 12 });
    expect(JSON.stringify(items)).toBe(before);
    expect(movePageItem(items, 8, { x: NaN, y: 0, w: 1, h: 1 }, 'desktop')).toBe(items);
  });
  it('平板修改与恢复不覆盖桌面、手机、像素位置及绑定', () => {
    const desktop = { x: 0, y: 0, w: 6, h: 15 }, mobile = { x: 0, y: 30, w: 1, h: 15 };
    const config = { xCol: 'name', pageLayout: { version: 1, desktop, mobile } };
    const next = withPageRect(config, 'tablet', { x: 0, y: 0, w: 6, h: 15 });
    expect(next.pageLayout.desktop).toEqual(desktop);
    expect(next.pageLayout.mobile).toEqual(mobile);
    expect(next.xCol).toBe('name');
    expect(withPageRect(next, 'tablet').pageLayout).toEqual(config.pageLayout);
  });
  it.each([null, { version: 2 }, { version: 1, desktop: { x: -1, y: 0, w: 6, h: 12 } }, { version: 1, desktop: { x: 0, y: 0, w: 13, h: 12 } }])('损坏的页面配置显式报错：%j', pageLayout => {
    expect(() => resolvePageLayout({ ...dashboard, charts: [{ ...chart(1), config: { pageLayout } }] }, 'desktop')).toThrow(/布局/);
  });
  it('页面版本损坏不回退历史自动布局', () => {
    expect(() => resolvePageLayout({ ...dashboard, filters: { design: { page: { version: 2 } } } }, 'desktop')).toThrow(/版本/);
  });
  it('套用主题、图表配置与导出保留页面布局', () => {
    const config = withPageRect({ xCol: 'name', cardBg: '#fff' }, 'desktop', { x: 0, y: 0, w: 6, h: 15 });
    const component: VisComponent = { id: 1, code: 'test', name: '测试样式', category: 'chart_style', chart_type: 'bar', style_config: { config: { cardBg: '#222' } }, source: 'custom', is_builtin: 0, is_active: 1, sort_order: 0 };
    expect(applyChartComponent(config, component).pageLayout).toEqual(config.pageLayout);
    expect(clearChartVisualOverrides(config).pageLayout).toEqual(config.pageLayout);
    expect(exportChart({ ...chart(1), config }).config.pageLayout).toEqual(config.pageLayout);
    expect(libraryThemeFilters({ design: { page: { version: 1 } } }).design.page).toEqual({ version: 1 });
  });
});
