import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {
  DEFAULT_PAGE_GAP, LEGACY_PAGE_COLUMNS, LEGACY_PAGE_GAP, LEGACY_PAGE_ROW, PAGE_COLUMNS, PAGE_GAP_MAX,
  PAGE_LAYOUT_VERSION, PAGE_ROW, computeAlignHints, defaultPageSpan, gapRows, nextPageRow, pageBreakpoint, pageCanvasSize,
  pageColumnPx, pageGap, pageGapsOf, pageMinHeight, pagePixelRect, pageRowPx, pageRows, previewInsert,
  resolvePageLayout, movePageItem, withPageRect, type PageBreakpoint,
} from '../dashboardPageLayout';
import { applyChartComponent, clearChartVisualOverrides, libraryThemeFilters } from '../dashboardDesign';
import { exportChart } from '@/components/DashboardExportImport';
import type { DashboardChart } from '@/stores/dashboardStore';
import type { VisComponent } from '@/api/visLibrary';

const chart = (id: number, type = 'bar', x = 0, y = 0): DashboardChart => ({ id, chart_type: type, name: `图表 ${id}`, config: {}, position: { x, y, w: 600, h: 120 } } as DashboardChart);
const dashboard = { filters: { design: { version: 1, canvas: { width: 1200, height: 1080 } } }, charts: [chart(10, 'widget_input'), chart(9, 'bar', 600), chart(8, 'table_value', 0, 200), chart(7, 'big_number', 600, 600)] };
const breakpoints: PageBreakpoint[] = ['desktop', 'tablet', 'mobile'];
/** v2（含 v1）时代的像素算法：行单位 = 4 + 固定 gap，列单位 = (设计宽 + gap) / 旧列数 */
const legacyPx = (rect: { x: number; y: number; w: number; h: number }, bp: PageBreakpoint) => {
  const gap = LEGACY_PAGE_GAP[bp];
  const column = ({ desktop: 1440, tablet: 768, mobile: 390 }[bp] + gap) / LEGACY_PAGE_COLUMNS[bp];
  return { x: rect.x * column, y: rect.y * (PAGE_ROW + gap), w: rect.w * column - gap, h: rect.h * (PAGE_ROW + gap) - gap };
};
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
      expect(pageRowPx(item.rect.h)).toBeGreaterThanOrEqual(pageMinHeight(item.chart.chart_type));
    }
  });
  it('手机保留桌面阅读顺序，不按 ID 颠倒同一行的控件与图表', () => {
    expect(resolvePageLayout(dashboard, 'mobile').map(i => i.chart.id)).toEqual(resolvePageLayout(dashboard, 'desktop').map(i => i.chart.id));
  });
  it('拖动与缩放自动避让，输入越界收敛，原对象不变', () => {
    const items = resolvePageLayout(dashboard, 'desktop');
    const before = JSON.stringify(items);
    const moved = movePageItem(items, 8, { x: -3, y: -3, w: PAGE_COLUMNS.desktop + 12, h: 1 }, 'desktop');
    assertNoOverlap(moved);
    expect(moved.find(i => i.chart.id === 8)?.rect).toMatchObject({ x: 0, y: 0, w: PAGE_COLUMNS.desktop });
    expect(JSON.stringify(items)).toBe(before);
    expect(movePageItem(items, 8, { x: NaN, y: 0, w: 1, h: 1 }, 'desktop')).toBe(items);
  });
  it('平板修改与恢复不覆盖桌面、手机、像素位置及绑定', () => {
    const desktop = { x: 0, y: 0, w: 24, h: 70 }, mobile = { x: 0, y: 150, w: 1, h: 70 };
    const config = { xCol: 'name', pageLayout: { version: PAGE_LAYOUT_VERSION, desktop, mobile } };
    const next = withPageRect(config, 'tablet', { x: 0, y: 0, w: 12, h: 70 });
    expect(next.pageLayout.desktop).toEqual(desktop);
    expect(next.pageLayout.mobile).toEqual(mobile);
    expect(next.xCol).toBe('name');
    expect(withPageRect(next, 'tablet').pageLayout).toEqual(config.pageLayout);
  });
  it.each([null, { version: 4 }, { version: 1, desktop: { x: -1, y: 0, w: 6, h: 12 } }, { version: 1, desktop: { x: 0, y: 0, w: 13, h: 12 } }, { version: 3, desktop: { x: 0, y: 0, w: 49, h: 12 } }])('损坏的页面配置显式报错：%j', pageLayout => {
    expect(() => resolvePageLayout({ ...dashboard, charts: [{ ...chart(1), config: { pageLayout } }] }, 'desktop')).toThrow(/布局/);
  });
  it('页面版本损坏不回退历史自动布局', () => {
    expect(() => resolvePageLayout({ ...dashboard, filters: { design: { page: { version: 4 } } } }, 'desktop')).toThrow(/版本/);
  });
  it('套用主题、图表配置与导出保留页面布局', () => {
    const config = withPageRect({ xCol: 'name', cardBg: '#fff' }, 'desktop', { x: 0, y: 0, w: 24, h: 70 });
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
    // v3：1 行 = 4px，所以 24px 高的按钮是 6 行
    expect(pageRows(pageMinHeight('widget_search'))).toBe(6);
    expect(pagePixelRect({ x: 0, y: 0, w: 3, h: 2 }, 'desktop').h).toBe(8);
    // 已存的合法小行高不再被读取时抬回下限
    const small = { ...dashboard, charts: [{ ...chart(3, 'widget_search'), config: withPageRect({}, 'desktop', { x: 0, y: 0, w: 3, h: 2 }) }] };
    expect(resolvePageLayout(small, 'desktop').find(i => i.chart.id === 3)?.rect.h).toBe(2);
  });
  it('拖入默认跳列与自然尺寸一致，不再生成一条横带', () => {
    // 列宽 ≈ 30px：按钮自然宽 96 → 4 列(105px)、日期范围 320 → 11 列、下拉 180 → 6 列；图表半屏
    expect(defaultPageSpan('widget_search', 'desktop')).toBe(4);
    expect(defaultPageSpan('widget_daterange', 'desktop')).toBe(11);
    expect(defaultPageSpan('widget_select', 'desktop')).toBe(6);
    expect(defaultPageSpan('bar', 'desktop')).toBe(24);
    // 手机单列下收敛到列数上限
    expect(defaultPageSpan('bar', 'mobile')).toBe(1);
    expect(defaultPageSpan('widget_daterange', 'tablet')).toBe(10);
    // 默认跳列必须装得下自然宽（ceil 而非 round）
    for (const type of ['widget_search', 'widget_daterange', 'widget_select']) {
      const span = defaultPageSpan(type, 'desktop');
      expect(pagePixelRect({ x: 0, y: 0, w: span, h: 1 }, 'desktop').w).toBeGreaterThanOrEqual(
        // 允许半列以内的取整误差
        ({ widget_search: 96, widget_daterange: 320, widget_select: 180 } as Record<string, number>)[type] - pageColumnPx('desktop') / 2);
    }
  });
  it('自动布局不把未放置的控件从 position 放大：按钮只按自然宽取列', () => {
    const button = { ...chart(4, 'widget_search'), position: { x: 0, y: 0, w: 300, h: 60 } } as DashboardChart;
    const items = resolvePageLayout({ filters: { design: { version: 1, canvas: { width: 1440, height: 1080 } } }, charts: [button] }, 'desktop');
    expect(items[0].rect.w).toBe(4);
    expect(items[0].rect.h).toBe(6);
  });
  it('resize 只受 1 行/1 列量子约束：可缩到很小，不被类型下限抬回', () => {
    const items = resolvePageLayout(dashboard, 'desktop');
    const moved = movePageItem(items, 10, { x: 0, y: 0, w: 1, h: 1 }, 'desktop');
    expect(moved.find(i => i.chart.id === 10)?.rect).toMatchObject({ w: 1, h: 1 });
    // v3 的 1 行 = 4px（v2 是 20px），这才是“能小幅微调”的步进
    expect(pageRowPx(1)).toBe(PAGE_ROW);
  });
});

describe('v3 网格：分辨率、间距与存量换算', () => {
  const one = (config: Record<string, any>, bp: PageBreakpoint = 'desktop') =>
    resolvePageLayout({ filters: {}, charts: [{ ...chart(1), config }] }, bp)[0].rect;

  it('列 12→48 后宽度步进降到 1/4（拖拽不再整列跳）', () => {
    expect(PAGE_COLUMNS.desktop / LEGACY_PAGE_COLUMNS.desktop).toBe(4);
    expect(Math.round(pageColumnPx('desktop'))).toBe(30);
  });

  it.each(['desktop', 'tablet', 'mobile'] as PageBreakpoint[])('%s：v2 存量布局按像素完全等值换算到 v3', bp => {
    for (const rect of [{ x: 0, y: 0, w: 1, h: 1 }, { x: 1, y: 4, w: 3, h: 9 }, { x: 0, y: 12, w: 6, h: 14 }, { x: 2, y: 0, w: 3, h: 30 }]) {
      if (rect.x + rect.w > LEGACY_PAGE_COLUMNS[bp]) continue;
      const v3 = one({ pageLayout: { version: 2, [bp]: rect } }, bp);
      const before = legacyPx(rect, bp), after = pagePixelRect(v3, bp);
      // 一像素都不差：升级不能改变任何存量看板的观感
      expect([Math.round(after.x), Math.round(after.y), Math.round(after.w), Math.round(after.h)])
        .toEqual([Math.round(before.x), Math.round(before.y), Math.round(before.w), Math.round(before.h)]);
    }
  });

  it('v1 与 v2 共存时各自按自己单位解释，且都落到 v3 网格', () => {
    const v2 = one({ pageLayout: { version: 2, desktop: { x: 1, y: 4, w: 3, h: 9 } } });
    expect(v2).toEqual({ x: 4, y: 20, w: 12, h: 41 });
    const v1 = one({ pageLayout: { version: 1, desktop: { x: 7, y: 4, w: 3, h: 9 } } });
    // v1 行单位是 8+gap=24px，先换算到 v2 再升到 v3（允许半个 v2 行单位以内的取整偏差）
    expect(Math.abs(pageRowPx(v1.h) - (9 * (LEGACY_PAGE_ROW + LEGACY_PAGE_GAP.desktop) - LEGACY_PAGE_GAP.desktop)))
      .toBeLessThanOrEqual(PAGE_ROW + LEGACY_PAGE_GAP.desktop);
    expect(v1.x).toBe(28);
  });

  it('间距可调：只改列宽与卡片宽度，不改任何卡片高度', () => {
    const rect = { x: 1, y: 0, w: 12, h: 70 };
    const tight = pagePixelRect(rect, 'desktop', { desktop: 8 }), loose = pagePixelRect(rect, 'desktop', { desktop: 24 });
    expect(tight.h).toBe(loose.h);
    expect(loose.w).toBeLessThan(tight.w);
    expect(tight.x).not.toBe(loose.x);
  });

  it('间距越界被夹到合法区间；缺档回落默认值', () => {
    expect(pageGap('desktop', { desktop: 200 })).toBe(PAGE_GAP_MAX);
    expect(pageGap('desktop', { desktop: -5 })).toBe(0);
    expect(pageGap('desktop', {})).toBe(DEFAULT_PAGE_GAP.desktop);
    expect(pageGap('mobile', { mobile: 4 })).toBe(4);
  });

  it('间距从看板级 filters.design.page.gap 读取（单一事实源）', () => {
    expect(pageGapsOf({ design: { page: { gap: { desktop: 8 } } } })).toEqual({ desktop: 8 });
    expect(pageGapsOf({ design: {} })).toBeUndefined();
    const source = { filters: { design: { page: { gap: { desktop: 8 } } } }, charts: [{ ...chart(2), config: withPageRect({}, 'desktop', { x: 0, y: 0, w: 12, h: 10 }) }] };
    const gaps = pageGapsOf(source.filters);
    const rect = resolvePageLayout(source, 'desktop')[0].rect;
    // 同一个布局：读出的像素高与 gap 无关（10 行 × 4px），宽度才随 gap 变
    expect(pagePixelRect(rect, 'desktop', gaps).h).toBe(40);
    expect(pagePixelRect(rect, 'desktop', gaps).w).toBe(12 * pageColumnPx('desktop', gaps) - 8);
    expect(pagePixelRect(rect, 'desktop', { desktop: 24 }).w).not.toBe(pagePixelRect(rect, 'desktop', gaps).w);
  });

  it('避让留白跟随间距：gap=0 时允许贴齐，默认 16 时多留 4 行空隙', () => {
    expect(gapRows(16)).toBe(4);
    expect(gapRows(0)).toBe(0);
    const base = resolvePageLayout({ filters: {}, charts: [
      { ...chart(1), config: withPageRect({}, 'desktop', { x: 0, y: 0, w: 12, h: 10 }) },
      { ...chart(2), config: withPageRect({}, 'desktop', { x: 0, y: 5, w: 12, h: 10 }) },
    ] }, 'desktop');
    // 被重叠的那张往后退，退多少 = 间距对应的行数
    expect(base.find(i => i.chart.id === 2)!.rect.y).toBe(14);   // 10 + gapRows(16)
    const flush = movePageItem(base, 2, { x: 0, y: 5, w: 12, h: 10 }, 'desktop', { desktop: 0 });
    expect(flush.find(i => i.chart.id === 1)!.rect.y).toBe(15);  // 间距 0 → 贴齐
    const spaced = movePageItem(base, 2, { x: 0, y: 5, w: 12, h: 10 }, 'desktop');
    expect(spaced.find(i => i.chart.id === 1)!.rect.y).toBe(19); // 默认间距 → 多留 4 行
  });

  it('插入点是**行**单位而不是像素：连续插入不会把画布撑到几万像素', () => {
    const items = resolvePageLayout(dashboard, 'desktop');
    const pixelBottom = items.reduce((m, i) => {
      const px = pagePixelRect(i.rect, 'desktop');
      return Math.max(m, px.y + px.h);
    }, 0);
    expect(nextPageRow(items)).toBeLessThan(400);
    expect(nextPageRow(items)).toBeLessThan(pixelBottom / 2);
    let rows = items.map(i => ({ chart: i.chart, rect: { ...i.rect }, automatic: false }));
    for (let i = 0; i < 5; i++) {
      rows = [...rows, { chart: { ...chart(900 + i), id: 900 + i },
        rect: { x: 0, y: nextPageRow(rows) + 1, w: 12, h: 15 }, automatic: false }];
    }
    expect(pageCanvasSize(rows, 'desktop').height).toBeLessThan(3000);
  });

  it('预览插入：虚线框占位并把重叠卡片挤到预测位置，不改原布局', () => {
    const items = resolvePageLayout(dashboard, 'desktop');
    const before = JSON.stringify(items);
    const insert = { chart: { ...chart(999, 'bar'), id: 999, name: '__预览__' },
      rect: { x: 0, y: 0, w: 24, h: 20 }, automatic: false };
    const forecast = previewInsert(items, insert, 'desktop');
    expect(JSON.stringify(items)).toBe(before);
    expect(forecast.find(i => i.chart.id === 999)?.rect).toMatchObject({ x: 0, y: 0, w: 24 });
    const displaced = forecast.find(i => i.chart.id === 10)!;
    expect(displaced.rect.y).toBeGreaterThanOrEqual(20);
    const untouched = forecast.find(i => i.chart.id === 9)!;
    expect(untouched.rect).toMatchObject({ y: 0 });
    assertNoOverlap(forecast);
  });

  it('编辑单个断点时，其余断点的旧单位矩形同步升版（不留旧单位陷阱）', () => {
    const config = { pageLayout: { version: 2, desktop: { x: 1, y: 4, w: 3, h: 9 }, mobile: { x: 0, y: 3, w: 1, h: 8 } } };
    const next = withPageRect(config, 'tablet', { x: 0, y: 0, w: 12, h: 70 });
    expect(next.pageLayout.version).toBe(PAGE_LAYOUT_VERSION);
    // 桌面/手机没被动过，但单位必须已经换成 v3（像素不变）
    expect(next.pageLayout.desktop).toEqual({ x: 4, y: 20, w: 12, h: 41 });
    expect(next.pageLayout.mobile).toEqual({ x: 0, y: 12, w: 1, h: 29 });
    expect(pagePixelRect(next.pageLayout.desktop, 'desktop').h).toBe(9 * (PAGE_ROW + LEGACY_PAGE_GAP.desktop) - LEGACY_PAGE_GAP.desktop);
    // 已是 v3 的配置再写不会被二次换算
    expect(withPageRect(next, 'tablet', { x: 0, y: 0, w: 12, h: 70 }).pageLayout.desktop).toEqual(next.pageLayout.desktop);
  });

  it('withPageRect 写入时落当前版本，保存后旧换算不再触发', () => {
    const next = withPageRect({ pageLayout: { version: 2, desktop: { x: 0, y: 0, w: 6, h: 10 } } }, 'desktop', { x: 0, y: 0, w: 24, h: 60 });
    expect(next.pageLayout.version).toBe(PAGE_LAYOUT_VERSION);
    expect(next.pageLayout.desktop).toEqual({ x: 0, y: 0, w: 24, h: 60 });
  });
});

// 网格只能保证“落在格子上”，保证不了“两张卡左边缘齐平”与“左右留空相等”——
// 对齐/间距提示就是把后者实时告知给用户（输入一律是像素矩形）。
describe('computeAlignHints：对齐线与间距读数', () => {
  const canvas = { width: 1440, height: 900 };
  it('左边缘与邻居齐平 → 竖参考线，跨度取两卡并集', () => {
    const { lines } = computeAlignHints({ x: 100, y: 300, w: 200, h: 100 }, [{ x: 100, y: 100, w: 300, h: 120 }], canvas);
    const v = lines.find(l => l.axis === 'x');
    expect(v).toMatchObject({ at: 100, from: 100, to: 400 });
  });
  it('只有中心线对齐也算（边缘不相等时仍给参考线）', () => {
    const { lines } = computeAlignHints({ x: 200, y: 0, w: 200, h: 100 }, [{ x: 220, y: 200, w: 160, h: 100 }], canvas);
    expect(lines.some(l => l.axis === 'x' && l.at === 300)).toBe(true);
  });
  it('两侧留空相等 → 等距提示；不相等时只报数值', () => {
    const left = { x: 0, y: 0, w: 280, h: 100 }, right = { x: 500, y: 0, w: 280, h: 100 };
    const { gaps } = computeAlignHints({ x: 300, y: 10, w: 180, h: 60 }, [left, right], canvas);
    expect(gaps).toHaveLength(2);
    expect(gaps.every(g => g.equal)).toBe(true);
    expect(gaps.map(g => Math.round(g.value))).toEqual([20, 20]);
    const uneven = computeAlignHints({ x: 320, y: 10, w: 180, h: 60 }, [left, right], canvas);
    expect(uneven.gaps.every(g => g.equal)).toBe(false);
  });
  it('另一轴不重叠的邻居不参与间距读数', () => {
    const { gaps } = computeAlignHints({ x: 200, y: 0, w: 100, h: 100 }, [{ x: 0, y: 500, w: 100, h: 100 }], canvas);
    expect(gaps.filter(g => g.axis === 'x')).toHaveLength(0);
  });
  it('贴到画布边缘/中线时也给参考线；同一位置只画一条', () => {
    expect(computeAlignHints({ x: 2, y: 0, w: 100, h: 100 }, [], canvas).lines.some(l => l.axis === 'x' && l.at === 0)).toBe(true);
    const dup = computeAlignHints({ x: 101, y: 100, w: 50, h: 50 },
      [{ x: 100, y: 0, w: 50, h: 50 }, { x: 100, y: 200, w: 50, h: 50 }], canvas);
    expect(dup.lines.filter(l => l.axis === 'x' && Math.abs(l.at - 100) < 1)).toHaveLength(1);
  });
  it('超出容差时不给参考线（不乱提示）', () => {
    const { lines } = computeAlignHints({ x: 120, y: 0, w: 100, h: 100 }, [{ x: 100, y: 0, w: 100, h: 100 }], canvas);
    expect(lines.filter(l => l.axis === 'x')).toHaveLength(0);
  });
});

// 静态护栏：两种布局（page 网格 / 大屏像素）的间距数学必须共用 computeAlignHints，
// 不得在页面里再手算一套“差多少 px”（同一口径只能有一个实现）。
describe('静态护栏：对齐/间距提示单一实现', () => {
  const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, '../..', rel), 'utf8');
  it.each(['components/editor/PageLayoutEditor.tsx', 'pages/DashboardEditor.tsx'])('%s 复用 computeAlignHints', file => {
    const code = read(file);
    expect(code).toMatch(/computeAlignHints/);
    // 不得自己算邻居间距（`- target.x` 这类手算差值容易与提示/吸附口径分叉）
    expect(code).not.toMatch(/gap\w*\s*=\s*\w+\.x\s*-/);
  });
});
