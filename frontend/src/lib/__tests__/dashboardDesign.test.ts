import { describe, it, expect } from 'vitest';
import {
  applyChartComponent, applyScreenComponent, applyLayout, chartDataKey, clampPosition,
  compatible, getCanvas, resolveChartDesign, resolveComponent, resolveDashboardDesign,
  sanitizeStyle, sanitizePack, screenToCanvas, snapshotComponent, themePackFor, visualConfig,
  legacyCanvasFilters, chartRect, snapCanvasRect, clearChartVisualOverrides, libraryThemeFilters,
} from '../dashboardDesign';
import { validateVisResponse } from '../../api/visLibrary';
import { exportChart } from '../../components/DashboardExportImport';
import type { VisComponent } from '../../api/visLibrary';

const comp = (over: Partial<VisComponent>): VisComponent => ({
  id: 1, code: 'c1', name: 'n', category: 'chart_style', chart_type: 'bar',
  style_config: {}, source: 'system', is_builtin: 1, is_active: 1, sort_order: 0, ...over,
});

describe('visLibrary 响应契约', () => {
  it('HTML/非数组视为错误而非空库', () => {
    expect(() => validateVisResponse('<html></html>')).toThrow(/有效 JSON 数组/);
    expect(() => validateVisResponse({})).toThrow(/有效 JSON 数组/);
  });
  it('真实空数组是合法空库', () => expect(validateVisResponse([])).toEqual([]));
});

describe('字模快照与缺失回退', () => {
  it('快照优先于库解析', () => {
    const snap = snapshotComponent(comp({ style_config: { config: { colorScheme: ['#abc'] } } }));
    const lib = [comp({ id: 99, code: 'other', style_config: { config: { colorScheme: ['#def'] } } })];
    expect(resolveComponent('c1', snap, lib)).toEqual({ config: { colorScheme: ['#abc'] } });
  });
  it('库中缺失字模返回 undefined，不使看板消失', () => {
    expect(resolveComponent('gone', undefined, [])).toBeUndefined();
  });
  it('sanitizeStyle 丢弃 url()/script 等不安全值', () => {
    const out = sanitizeStyle({ backgroundColor: 'red', backgroundImage: 'url(evil)', palette: ['#fff'] });
    expect(out.backgroundColor).toBe('red');
    expect(out.backgroundImage).toBeUndefined();
    expect(out.palette).toEqual(['#fff']);
  });
});

describe('字模应用不覆盖数据绑定', () => {
  it('applyChartComponent 保留查询字段映射并写入快照', () => {
    const config = { xCol: 'd', yCol: 'v', sqlQuerySafe: 'x', colorScheme: ['#old'] };
    const next = applyChartComponent(config, comp({ code: 'cs', style_config: { config: { colorScheme: ['#new'], gradient: true } } }));
    expect(next.xCol).toBe('d');
    expect(next.yCol).toBe('v');
    expect(next.vis.snapshots.chartStyle.code).toBe('cs');
  });
  it('替换字模时清掉旧字模遗留且未被用户改写的视觉字段', () => {
    const c1 = comp({ code: 'a', style_config: { config: { borderRadius: 6 } } });
    const withA = applyChartComponent({}, c1);
    // 用户未改动 borderRadius(等于旧字模值) → 换成 b 时旧值被移除
    const swapped = applyChartComponent(withA, comp({ code: 'b', style_config: { config: { borderRadius: 2 } } }));
    expect(swapped.borderRadius).toBeUndefined();
  });
  it('用户显式改写过的字段在切换字模时保留', () => {
    const c1 = comp({ code: 'a', style_config: { config: { borderRadius: 6 } } });
    const edited = { ...applyChartComponent({}, c1), borderRadius: 99 };
    const swapped = applyChartComponent(edited, comp({ code: 'b', style_config: { config: {} } }));
    expect(swapped.borderRadius).toBe(99);
  });
});

describe('整屏字模与兼容', () => {
  it('applyScreenComponent 保留 filters 未知字段并落快照', () => {
    const filters = { custom: 1, theme: {} };
    const out = applyScreenComponent(filters, comp({ category: 'screen_background', code: 'bg' }));
    expect(out.custom).toBe(1);
    expect(out.components.background).toBe('bg');
    expect(out.design.componentSnapshots.background.code).toBe('bg');
  });
  it('旧 theme.card 字段映射进新解析', () => {
    const design = resolveDashboardDesign({ theme: { card: { background: '#111', textColor: '#eee' } } }, []);
    expect(design.card.cardBg).toBe('#111');
    expect(design.card.labelColor).toBe('#eee');
  });
  it('缺失字模记入 missing 但保留配置', () => {
    const design = resolveDashboardDesign({ components: { background: 'gone' } }, []);
    expect(design.missing).toContain('gone');
  });
});

describe('局部主题不污染全局', () => {
  it('colorScheme 仅覆盖该图 visual.palette', () => {
    const screen = resolveDashboardDesign({}, []);
    const r = resolveChartDesign({ colorScheme: ['#123456'] }, screen, []);
    expect(r.visual.palette).toEqual(['#123456']);
    // 整屏 visual 未被修改
    expect(screen.visual.palette).not.toEqual(['#123456']);
  });
});

describe('坐标与缩放', () => {
  it('screenToCanvas 按真实画布边界与缩放换算', () => {
    const p = screenToCanvas({ x: 300, y: 200 }, { left: 100, top: 50 }, 2);
    expect(p).toEqual({ x: 100, y: 75 });
  });
  it('clampPosition 网格吸附并限制边界', () => {
    const p = clampPosition({ x: 33, y: 4990 }, { w: 400, h: 300 }, { width: 1920, height: 1080 }, 20);
    expect(p.x).toBe(40);
    expect(p.y).toBe(1080 - 300);
  });
});

describe('布局模板', () => {
  const charts = (n: number, widget = false) => Array.from({ length: n }, (_, i) => ({
    id: i, chart_type: widget ? 'widget_text' : 'bar', position: { x: 0, y: 0, w: 400, h: 300 },
  })) as any;
  it('kpiRow 顶部指标行 + 下方网格', () => {
    const { positions, height } = applyLayout(charts(3), { grid: { kpiRow: { height: 180, count: 4 }, rows: 1, cols: 3 } }, { width: 1920, height: 1080 });
    expect(Object.keys(positions)).toHaveLength(3);
    expect(height).toBeGreaterThanOrEqual(1080);
  });
  it('columns 左主右辅', () => {
    const { positions } = applyLayout(charts(3), { grid: { columns: [{ width: 1280 }, { width: 640 }] } }, { width: 1920, height: 1080 });
    expect(positions[0].w).toBeGreaterThan(positions[1].w);
  });
  it('均分网格且控件避让', () => {
    const mixed = [...charts(2), ...charts(1, true)];
    mixed[2].id = 100;
    const { positions } = applyLayout(mixed, { grid: { rows: 1, cols: 2 } }, { width: 1920, height: 1080 });
    expect(positions[100]).toBeUndefined();
  });
});

describe('数据刷新与兼容', () => {
  it('chartDataKey 同首行但后续行变化也能识别', () => {
    const a = { columns: ['x'], rows: [[1], [2]] };
    const b = { columns: ['x'], rows: [[1], [999]] };
    expect(chartDataKey(a)).not.toBe(chartDataKey(b));
  });
  it('getCanvas 高度至少包住已有图表且不改坐标', () => {
    const c = getCanvas({}, [{ position: { x: 0, y: 2000, w: 10, h: 100 } } as any]);
    expect(c.height).toBeGreaterThanOrEqual(2100);
  });
  it('compatible 区分图表与控件', () => {
    expect(compatible(comp({ category: 'chart_style', chart_type: 'bar' }), 'bar')).toBe(true);
    expect(compatible(comp({ category: 'chart_style', chart_type: 'bar' }), 'widget_text')).toBe(false);
    expect(compatible(comp({ category: 'kpi_card' }), 'big_number')).toBe(true);
  });
});

const pack = (code: string, over: any = {}): VisComponent => comp({
  id: 50, code, name: code, category: 'theme_pack', chart_type: null,
  style_config: {
    mode: 'dark', palette: ['#22d3ee', '#3b82f6'],
    background: { backgroundColor: '#0a1220' },
    card: { cardBg: 'rgba(15,23,42,0.65)', cardBorder: '1px solid rgba(34,211,238,0.35)', borderRadius: 12 },
    charts: { default: { gradient: true, borderRadius: 6 }, bar: { gradient: false, borderRadius: 2 } },
    widgets: { default: { backgroundColor: '#0f172a', textColor: '#e2e8f0', borderRadius: 12 } },
    ...over,
  },
});

describe('主题包(theme_pack)', () => {
  it('sanitizePack 提取 mode/palette/background/card/charts/widgets 并白名单过滤', () => {
    const p = sanitizePack({ mode: 'dark', palette: ['#fff', 'url(x)'], background: { backgroundColor: '#000', evil: 1 },
      card: { cardBg: '#111', sql: 'x' }, charts: { bar: { gradient: true, xCol: 'nope' } }, widgets: { default: { textColor: '#eee', paramKey: 'no' } } });
    expect(p.mode).toBe('dark');
    expect(p.palette).toEqual(['#fff']);
    expect(p.background).toEqual({ backgroundColor: '#000' });
    expect(p.card.cardBg).toBe('#111');
    expect(p.card.sql).toBeUndefined();
    expect(p.charts.bar).toEqual({ gradient: true });
    expect(p.widgets.default).toEqual({ textColor: '#eee' });
  });

  it('themePackFor 按主题 id 映射并回退 datafoundry', () => {
    const items = [pack('tp_tech'), pack('tp_datafoundry')];
    expect(themePackFor('tech', items)?.code).toBe('tp_tech');
    expect(themePackFor('nosuch', items)?.code).toBe('tp_datafoundry');
    expect(themePackFor(undefined, items)).toBeUndefined();
  });

  it('无显式设计时跟随用户全局主题(内存回退, 不改 filters)', () => {
    const items = [pack('tp_tech')];
    const d = resolveDashboardDesign({}, items, { theme: 'tech' });
    expect(d.pack?.background?.backgroundColor).toBe('#0a1220');
    expect(d.visual.mode).toBe('dark');
    expect(d.background.backgroundColor).toBe('#0a1220');
    expect(d.card.cardBg).toBe('rgba(15,23,42,0.65)');
  });

  it('显式设计优先于主题回退', () => {
    const items = [pack('tp_tech'), pack('tp_light', { mode: 'light', background: { backgroundColor: '#fff' } })];
    const filters = { design: { version: 1, themePack: 'tp_light' } };
    const d = resolveDashboardDesign(filters, items, { theme: 'tech' });
    expect(d.background.backgroundColor).toBe('#fff');
  });

  it('图表继承主题包 charts[type] 优先于 default，控件继承 widgets', () => {
    const items = [pack('tp_tech')];
    const screen = resolveDashboardDesign({}, items, { theme: 'tech' });
    const bar = resolveChartDesign({}, screen, items, 'bar');
    expect(bar.config.gradient).toBe(false);   // bar 覆盖 default
    expect(bar.config.borderRadius).toBe(2);
    const line = resolveChartDesign({}, screen, items, 'line');
    expect(line.config.gradient).toBe(true);    // 回落 default
    expect(line.widget.textColor).toBe('#e2e8f0');
    // 图表自身显式覆盖仍最高
    const own = resolveChartDesign({ gradient: true, borderRadius: 9 }, screen, items, 'bar');
    expect(own.config.gradient).toBe(true);
    expect(own.config.borderRadius).toBe(9);
  });

  it('applyScreenComponent 对 theme_pack 写入 design.themePack 与快照', () => {
    const f = applyScreenComponent({}, pack('tp_tech'));
    expect(f.design.themePack).toBe('tp_tech');
    expect(f.design.componentSnapshots.themePack.code).toBe('tp_tech');
    expect(f.components?.themePack).toBeUndefined();
  });
});

describe('主题包完整应用回归', () => {
  it('套用、序列化、库下架后仍保留整套图表与控件视觉', () => {
    const applied = applyScreenComponent({}, pack('tp_tech'));
    const screen = resolveDashboardDesign(JSON.parse(JSON.stringify(applied)), [], { theme: 'light' });
    expect(screen.background.backgroundColor).toBe('#0a1220');
    expect(screen.card.cardRadius).toBe(12);
    expect(resolveChartDesign({}, screen, [], 'bar').config.borderRadius).toBe(2);
    expect(resolveChartDesign({}, screen, [], 'widget_text').config.widgetStyle.textColor).toBe('#e2e8f0');
  });
  it('通过真实旧偏好转换链路以及仅有画布尺寸时都跟随全局主题', () => {
    localStorage.removeItem('dashboard_canvas_bg'); localStorage.removeItem('screen_settings'); localStorage.removeItem('carousel_settings');
    const items = [pack('tp_tech'), pack('tp_light', { mode: 'light' })];
    for (const filters of [{}, { theme: {}, components: {}, design: { canvas: { width: 1600 } } }]) {
      expect(resolveDashboardDesign(legacyCanvasFilters(filters), items, { theme: 'tech' }).visual.mode).toBe('dark');
      expect(resolveDashboardDesign(legacyCanvasFilters(filters), items, { theme: 'light' }).visual.mode).toBe('light');
    }
  });
  it('修复旧残缺快照并拒绝错误版本或类别的快照', () => {
    const items = [pack('tp_tech')];
    const filters = { design: { themePack: 'tp_tech', componentSnapshots: { themePack: { version: 1, category: 'theme_pack', style_config: { mode: 'dark' } } } } };
    expect(resolveDashboardDesign(filters, items).background.backgroundColor).toBe('#0a1220');
    filters.design.componentSnapshots.themePack.version = 9;
    expect(resolveDashboardDesign(filters, items).background.backgroundColor).toBe('#0a1220');
  });
  it('按字段合并 default 与具体类型，内部控件收到相同结果', () => {
    const p = pack('tp_tech', { charts: { default: { smooth: true, lineWidth: 3 }, line: { lineWidth: 5 } },
      widgets: { default: { fontSize: 18, textColor: '#fff' }, widget_text: { borderRadius: 4 } } });
    const screen = resolveDashboardDesign({}, [p], { theme: 'tech' });
    expect(resolveChartDesign({}, screen, [], 'line').config).toMatchObject({ smooth: true, lineWidth: 5 });
    expect(resolveChartDesign({ widgetStyle: { fontSize: 20 }, paramKey: 'region' }, screen, [], 'widget_text').config)
      .toMatchObject({ paramKey: 'region', widgetStyle: { fontSize: 20, textColor: '#fff', borderRadius: 4 } });
  });
  it('整屏切换不修改查询、参数、布局与原对象', () => {
    const config = { xCol: 'date', yCol: 'value', countSql: 'SELECT 1', paramKey: 'region', colorScheme: ['#old'],
      widgetStyle: { textColor: '#old' }, axisStyle: { gridColor: '#old', title: 'value' }, vis: { refs: { chartStyle: 'old' } } };
    const filters = { pageParams: [{ name: 'region' }], theme: { background: '#old' }, design: { canvas: { width: 1920, backgroundColor: '#old' } } };
    const clean = clearChartVisualOverrides(config);
    expect(clean).toMatchObject({ xCol: 'date', yCol: 'value', countSql: 'SELECT 1', paramKey: 'region', axisStyle: { title: 'value' } });
    expect(clean.colorScheme).toBeUndefined(); expect(clean.vis).toBeUndefined();
    expect(config.widgetStyle.textColor).toBe('#old');
    const next = libraryThemeFilters(filters);
    expect(next.pageParams).toEqual(filters.pageParams);
    expect(next.design.canvas).toEqual({ width: 1920 });
    expect(filters.design.canvas.backgroundColor).toBe('#old');
  });
  it('空或非法色板不覆盖类型安全约束', () => {
    expect(sanitizePack({ palette: 'red' }).palette).toBeUndefined();
    expect(sanitizePack({ palette: [123, '#fff', 'url(x)'] }).palette).toEqual(['#fff']);
  });
});

describe('画布吸附与防塌陷', () => {
  const canvas = { width: 1920, height: 1080 };
  it('异常尺寸有下限且不回写旧位置', () => {
    const chart = { chart_type: 'bar', position: { x: NaN, y: 20, w: 0, h: -1 } };
    expect(chartRect(chart)).toEqual({ x: 0, y: 20, w: 200, h: 150 });
    expect(chart.position.h).toBe(-1);
    expect(getCanvas({ design: { canvas: { width: 1, height: NaN, gridSize: 0 } } }, [chart])).toEqual({ width: 320, height: 1080, gridSize: 1 });
  });
  it('邻居边缘优先于网格，反馈实际对齐线', () => {
    const result = snapCanvasRect({ x: 198, y: 213, w: 400, h: 300 }, canvas, [{ x: 203, y: 711, w: 410, h: 200 }], 20, 1);
    expect(result.rect.x).toBe(203);
    expect(result.rect.y).toBe(213);
    expect(result.guides).toContainEqual({ axis: 'x', value: 203 });
  });
  it('支持居中与画布边缘吸附', () => {
    const result = snapCanvasRect({ x: 758, y: 778, w: 400, h: 300 }, canvas, [], 20, 1);
    expect(result.rect).toMatchObject({ x: 760, y: 780 });
    expect(result.guides).toEqual([{ axis: 'x', value: 960 }, { axis: 'y', value: 1080 }]);
  });
  it('缩放下使用屏幕阈值，Alt 禁用时仍约束边界', () => {
    const raw = { x: 190, y: 213, w: 400, h: 300 };
    const others = [{ x: 200, y: 710, w: 410, h: 200 }];
    expect(snapCanvasRect(raw, canvas, others, 20, 0.5).rect.x).toBe(200);
    expect(snapCanvasRect(raw, canvas, others, 20, 2).rect.x).toBe(190);
    expect(snapCanvasRect(raw, canvas, others, 20, 0.5, false, false).rect.x).toBe(190);
    expect(snapCanvasRect({ ...raw, x: -100, y: 2000 }, canvas, others, 20, 1, false, false).rect).toMatchObject({ x: 0, y: 780 });
  });
  it('缩放吸附右下边缘并遵守最小尺寸与画布范围', () => {
    expect(snapCanvasRect({ x: 100, y: 100, w: 397, h: 297 }, canvas, [], 20, 1, true).rect).toMatchObject({ w: 400, h: 300 });
    expect(snapCanvasRect({ x: 100, y: 100, w: -20, h: 10 }, canvas, [], 20, 1, true).rect).toMatchObject({ w: 200, h: 150 });
    expect(snapCanvasRect({ x: 100, y: 100, w: 9999, h: 9999 }, canvas, [], 20, 1, true).rect).toMatchObject({ w: 1820, h: 980 });
    expect(snapCanvasRect({ x: 100, y: 100, w: 2, h: 2 }, canvas, [], 20, 1, true, true, { w: 100, h: 36 }).rect.h).toBeGreaterThanOrEqual(36);
  });
});

describe('导出不泄露语义 SQL', () => {
  it('语义图表导出保留声明式查询并剥离任何 sql 字段', () => {
    const out: any = exportChart({ name: 't', chart_type: 'bar', query_source: 'semantic', semantic_query: { object: 'x' },
      sql_query: 'SELECT secret', config: { countSql: 'SELECT c', nested: { sql: 'x' } }, position: {}, source_type: 'template' });
    expect(out.sql_query).toBeUndefined();
    expect(out.semantic_query).toEqual({ object: 'x' });
    expect(JSON.stringify(out.config)).not.toMatch(/secret|countSql|"sql"/i);
  });
  it('人工 raw_sql 图表保留 SQL', () => {
    const out: any = exportChart({ name: 't', chart_type: 'bar', query_source: 'raw_sql', sql_query: 'SELECT 1', config: {}, position: {}, source_type: 'query' });
    expect(out.sql_query).toBe('SELECT 1');
  });
  it('visualConfig 只提取白名单视觉键', () => {
    const v = visualConfig({ colorScheme: ['#1'], sqlQuerySafe: 'nope', xCol: 'd', axisStyle: { gridColor: '#000', other: 1 } });
    expect(v.colorScheme).toEqual(['#1']);
    expect(v.xCol).toBeUndefined();
    expect(v.axisStyle).toEqual({ gridColor: '#000' });
  });
});
