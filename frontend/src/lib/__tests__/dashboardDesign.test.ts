import { describe, it, expect } from 'vitest';
import {
  applyChartComponent, applyScreenComponent, applyLayout, chartDataKey, clampPosition,
  compatible, getCanvas, resolveChartDesign, resolveComponent, resolveDashboardDesign,
  sanitizeStyle, screenToCanvas, snapshotComponent, visualConfig,
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

describe('导出不泄露语义 SQL', () => {
  it('语义图表导出保留声明式查询并剥离任何 sql 字段', () => {
    const out = exportChart({ name: 't', chart_type: 'bar', query_source: 'semantic', semantic_query: { object: 'x' },
      sql_query: 'SELECT secret', config: { countSql: 'SELECT c', nested: { sql: 'x' } }, position: {}, source_type: 'template' });
    expect(out.sql_query).toBeUndefined();
    expect(out.semantic_query).toEqual({ object: 'x' });
    expect(JSON.stringify(out.config)).not.toMatch(/secret|countSql|"sql"/i);
  });
  it('人工 raw_sql 图表保留 SQL', () => {
    const out = exportChart({ name: 't', chart_type: 'bar', query_source: 'raw_sql', sql_query: 'SELECT 1', config: {}, position: {}, source_type: 'query' });
    expect(out.sql_query).toBe('SELECT 1');
  });
  it('visualConfig 只提取白名单视觉键', () => {
    const v = visualConfig({ colorScheme: ['#1'], sqlQuerySafe: 'nope', xCol: 'd', axisStyle: { gridColor: '#000', other: 1 } });
    expect(v.colorScheme).toEqual(['#1']);
    expect(v.xCol).toBeUndefined();
    expect(v.axisStyle).toEqual({ gridColor: '#000' });
  });
});
