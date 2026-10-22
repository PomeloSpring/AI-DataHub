import { describe, expect, it } from 'vitest';
import { PAGE_COLUMNS, PAGE_ROW, PAGE_LAYOUT_VERSION, LEGACY_PAGE_ROW, defaultPageSpan, nextPageRow, pageBreakpoint, pageGap, pageMinHeight, pageCanvasSize, pagePixelRect, pageRows, previewInsert, resolvePageLayout, movePageItem, withPageRect, type PageBreakpoint } from '../dashboardPageLayout';
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
    const config = { xCol: 'name', pageLayout: { version: PAGE_LAYOUT_VERSION, desktop, mobile } };
    const next = withPageRect(config, 'tablet', { x: 0, y: 0, w: 6, h: 15 });
    expect(next.pageLayout.desktop).toEqual(desktop);
    expect(next.pageLayout.mobile).toEqual(mobile);
    expect(next.xCol).toBe('name');
    expect(withPageRect(next, 'tablet').pageLayout).toEqual(config.pageLayout);
  });
  it.each([null, { version: 3 }, { version: 1, desktop: { x: -1, y: 0, w: 6, h: 12 } }, { version: 1, desktop: { x: 0, y: 0, w: 13, h: 12 } }])('损坏的页面配置显式报错：%j', pageLayout => {
    expect(() => resolvePageLayout({ ...dashboard, charts: [{ ...chart(1), config: { pageLayout } }] }, 'desktop')).toThrow(/布局/);
  });
  it('页面版本损坏不回退历史自动布局', () => {
    expect(() => resolvePageLayout({ ...dashboard, filters: { design: { page: { version: 3 } } } }, 'desktop')).toThrow(/版本/);
  });
  it('套用主题、图表配置与导出保留页面布局', () => {
    const config = withPageRect({ xCol: 'name', cardBg: '#fff' }, 'desktop', { x: 0, y: 0, w: 6, h: 15 });
    const component: VisComponent = { id: 1, code: 'test', name: '测试样式', category: 'chart_style', chart_type: 'bar', style_config: { config: { cardBg: '#222' } }, source: 'custom', is_builtin: 0, is_active: 1, sort_order: 0 };
    expect(applyChartComponent(config, component).pageLayout).toEqual(config.pageLayout);
    expect(clearChartVisualOverrides(config).pageLayout).toEqual(config.pageLayout);
    expect(exportChart({ ...chart(1), config }).config.pageLayout).toEqual(config.pageLayout);
    expect(libraryThemeFilters({ design: { page: { version: 1 } } }).design.page).toEqual({ version: 1 });
  });
  it('控件不再一律 48px 地板，按自然尺寸取默认高', () => {
    expect(pageMinHeight('widget_search')).toBe(24);
    expect(pageMinHeight('widget_daterange')).toBe(32);
    expect(pageMinHeight('bar')).toBe(280);
    expect(pageRows(pageMinHeight('widget_search'), 'desktop')).toBe(2);
    expect(pagePixelRect({ x: 0, y: 0, w: 3, h: 2 }, 'desktop').h).toBe(24);
    // 已存的合法小行高不再被读取时抬回下限
    const small = { ...dashboard, charts: [{ ...chart(3, 'widget_search'), config: withPageRect({}, 'desktop', { x: 0, y: 0, w: 3, h: 2 }) }] };
    expect(resolvePageLayout(small, 'desktop').find(i => i.chart.id === 3)?.rect.h).toBe(2);
  });
  it('拖入默认跳列与自然尺寸一致，不再生成一条 3 列横带', () => {
    // 按钮自然宽 96 → 1 列；日期范围 320 → 3 列；下拉 180 → 2 列
    expect(defaultPageSpan('widget_search', 'desktop')).toBe(1);
    expect(defaultPageSpan('widget_daterange', 'desktop')).toBe(3);
    expect(defaultPageSpan('widget_select', 'desktop')).toBe(2);
    expect(defaultPageSpan('bar', 'desktop')).toBe(6);
    // 手机单列下收敛到列数上限
    expect(defaultPageSpan('bar', 'mobile')).toBe(1);
    expect(defaultPageSpan('widget_daterange', 'tablet')).toBe(3);
  });
  it('自动布局不把未放置的控件从 position 放大：按钮只占 1 列 2 行', () => {
    const button = { ...chart(4, 'widget_search'), position: { x: 0, y: 0, w: 300, h: 60 } } as DashboardChart;
    const items = resolvePageLayout({ filters: { design: { version: 1, canvas: { width: 1440, height: 1080 } } }, charts: [button] }, 'desktop');
    expect(items[0].rect.w).toBe(1);
    expect(items[0].rect.h).toBe(2);
  });
  it('resize 只受 1 行/1 列量子约束：可缩到很小，不被类型下限抬回', () => {
    const items = resolvePageLayout(dashboard, 'desktop');
    const moved = movePageItem(items, 10, { x: 0, y: 0, w: 1, h: 1 }, 'desktop');
    expect(moved.find(i => i.chart.id === 10)?.rect).toMatchObject({ w: 1, h: 1 });
  });
  it('行单位 v1→v2 按像素等值换算，偏差 ≤ 半个行单位', () => {
    const gap = pageGap('desktop');
    for (const h of [2, 3, 5, 8, 10, 15, 20]) {
      const legacyPx = h * (LEGACY_PAGE_ROW + gap) - gap;
      const config = { pageLayout: { version: 1, desktop: { x: 0, y: 0, w: 6, h } } };
      const rect = resolvePageLayout({ filters: {}, charts: [{ ...chart(1), config }] }, 'desktop')[0].rect;
      expect(Math.abs((rect.h * (PAGE_ROW + gap) - gap) - legacyPx)).toBeLessThanOrEqual((PAGE_ROW + gap) / 2);
    }
  });
  it('v2 直读不换算；v1 与 v2 共存时各自按自己单位解释', () => {
    const v2 = { pageLayout: { version: PAGE_LAYOUT_VERSION, desktop: { x: 1, y: 4, w: 3, h: 9 } } };
    const v1 = { pageLayout: { version: 1, desktop: { x: 7, y: 4, w: 3, h: 9 } } };
    const items = resolvePageLayout({ filters: {}, charts: [{ ...chart(1), config: v2 }, { ...chart(2), config: v1 }] }, 'desktop');
    expect(items.find(i => i.chart.id === 1)?.rect).toEqual({ x: 1, y: 4, w: 3, h: 9 });
    expect(items.find(i => i.chart.id === 2)?.rect).toEqual({ x: 7, y: 5, w: 3, h: 11 });
  });
  it('插入点是**行**单位而不是像素：连续插入不会把画布撑到几万像素', () => {
    const items = resolvePageLayout(dashboard, 'desktop');
    // 任意一个卡片的像素下沿（曾经被误当行用）与行下沿不是一个量级
    const pixelBottom = items.reduce((m, i) => Math.max(m, pagePixelRect(i.rect, 'desktop').y + pagePixelRect(i.rect, 'desktop').h), 0);
    expect(nextPageRow(items)).toBeLessThan(200);
    expect(nextPageRow(items)).toBeLessThan(pixelBottom / 10);
    // 连续 5 次“插到最下面”仍应落在几行内
    let rows = items.map(i => ({ chart: i.chart, rect: { ...i.rect }, automatic: false }));
    for (let i = 0; i < 5; i++) {
      rows = [...rows, { chart: { ...chart(900 + i), id: 900 + i },
        rect: { x: 0, y: nextPageRow(rows) + 1, w: 6, h: 3 }, automatic: false }];
    }
    expect(pageCanvasSize(rows, 'desktop').height).toBeLessThan(2000);
  });

  it('预览插入：虚线框占位并把重叠卡片挤到预测位置，不改原布局', () => {
    const items = resolvePageLayout(dashboard, 'desktop');
    const before = JSON.stringify(items);
    const insert = { chart: { ...chart(999, 'bar'), id: 999, name: '__预览__' },
      rect: { x: 0, y: 0, w: 6, h: 4 }, automatic: false };
    const forecast = previewInsert(items, insert, 'desktop');
    expect(JSON.stringify(items)).toBe(before);
    // 预览项落在请求的位置（预览与真实插入同构，含最小尺寸夹取）
    expect(forecast.find(i => i.chart.id === 999)?.rect).toMatchObject({ x: 0, y: 0, w: 6 });
    // 与插入项重叠的卡片被挤到插入项下方；原本不重叠的（x=600）保持不动
    const displaced = forecast.find(i => i.chart.id === 10)!;
    expect(displaced.rect.y).toBeGreaterThanOrEqual(4);
    const untouched = forecast.find(i => i.chart.id === 9)!;
    expect(untouched.rect).toMatchObject({ y: 0 });
    assertNoOverlap(forecast);
  });

  it('withPageRect 写入时落 v2，保存后旧换算不再触发', () => {
    const next = withPageRect({ pageLayout: { version: 1, desktop: { x: 0, y: 0, w: 6, h: 10 } } }, 'desktop', { x: 0, y: 0, w: 6, h: 12 });
    expect(next.pageLayout.version).toBe(PAGE_LAYOUT_VERSION);
    expect(next.pageLayout.desktop).toEqual({ x: 0, y: 0, w: 6, h: 12 });
  });
});
