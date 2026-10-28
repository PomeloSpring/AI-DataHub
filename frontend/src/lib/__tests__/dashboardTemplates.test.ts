/**
 * 仪表盘样式模板静态护栏（模板定位=图表样式展示，数据内置；残缺模板比空白看板更糟）。
 *
 * 锁定：
 *  - 模板清单非空且校验器无错（类型真实、位置不越界、无重叠）
 *  - 模板不携带任何 SQL/取数口径（只展示样式，不假设环境里的表结构）
 *  - 每个图卡自带示例数据，字段映射列/行形状与列一一对应
 *  - 趋势大数字至少两行；gauge 量程用 `maxValue`（写 `max` 是死配置）
 *  - 不预置 datasource_id / 不写死配色（样式唯一源是字模库主题包）
 */
import { describe, expect, it } from 'vitest';
import {
  DASHBOARD_TEMPLATES, TEMPLATE_CANVAS, TEMPLATE_CONFIG_KEYS, validateTemplates, type Sample, type TemplateChartDef,
} from '@/lib/dashboardTemplates';
import { CHART_TYPES, buildG2Spec } from '@/components/DashboardChart';

const MAPPED_KEYS = ['xCol', 'yCol', 'groupCol', 'nameCol', 'valCol', 'timeCol'];
/** 模板本体里的 sample 一定是对象形态（字符串只出现在导入体） */
const sampleOf = (chart: TemplateChartDef): Sample => chart.sample as Sample;
const numericTypes = (chart: TemplateChartDef) => ['line', 'bar', 'area', 'pie', 'funnel', 'gauge',
  'big_number_trend', 'timeseries_line', 'timeseries_area', 'timeseries_bar', 'rose', 'radial_bar'].includes(chart.chart_type);
/** 拿内置首个模板做单图变异，验证校验器确实拦得住 */
const errorsOf = (mutate: (chart: TemplateChartDef) => TemplateChartDef) => {
  const base = DASHBOARD_TEMPLATES[0];
  return validateTemplates([{ ...base, charts: [mutate(base.charts[0])] }]);
};

describe('仪表盘模板清单', () => {
  it('校验器对内置模板零报错', () => {
    expect(validateTemplates()).toEqual([]);
  });

  it('模板数量充足且含空白看板', () => {
    expect(DASHBOARD_TEMPLATES.length >= 9).toBe(true);
    expect(DASHBOARD_TEMPLATES.some(t => t.id === 'empty' && !t.charts.length)).toBe(true);
  });

  it('模板 id 唯一、名称/描述/标签/场景齐备', () => {
    const ids = DASHBOARD_TEMPLATES.map(t => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const t of DASHBOARD_TEMPLATES) {
      expect(t.name.trim().length > 1).toBe(true);
      expect(t.description.length > 8).toBe(true);
      expect(t.tags.length > 0).toBe(true);
      expect(t.scenario.length > 0).toBe(true);
    }
  });

  it('图表类型必须是 CHART_TYPES 真实项（悬空类型导入后渲染成默认柱图）', () => {
    const real = new Set(CHART_TYPES.map(c => c.value));
    for (const t of DASHBOARD_TEMPLATES) {
      for (const chart of t.charts) expect(real.has(chart.chart_type)).toBe(true);
    }
  });

  it('每个图卡自带示例数据，映射列与行形状都对得上', () => {
    for (const t of DASHBOARD_TEMPLATES) {
      for (const chart of t.charts.filter(c => !c.chart_type.startsWith('widget_'))) {
        const sample = sampleOf(chart);
        expect(!!sample).toBe(true);
        expect(sample.columns.length > 0).toBe(true);
        expect(sample.rows.length > 0).toBe(true);
        for (const row of sample.rows) {
          expect(Object.keys(row).sort().join('|')).toBe([...sample.columns].sort().join('|'));
        }
        for (const key of MAPPED_KEYS) {
          const col = chart.config?.[key];
          if (col) expect(sample.columns.includes(col)).toBe(true);
        }
      }
    }
  });

  it('数值型图表的度量列是真数字；trend 大数字至少两行；gauge 用 maxValue 量程', () => {
    for (const t of DASHBOARD_TEMPLATES) {
      for (const chart of t.charts) {
        // config 键必须全部被渲染层消费（写错键名就是“存了不生效”的隐形缺陷）
        for (const key of Object.keys(chart.config || {})) {
          expect(TEMPLATE_CONFIG_KEYS.has(key), `${t.id}/${chart.name} 的 ${key}`).toBe(true);
        }
        const yCol = chart.config?.yCol || chart.config?.valCol;
        if (yCol && numericTypes(chart)) {
          for (const row of sampleOf(chart).rows) expect(typeof row[yCol]).toBe('number');
        }
        if (chart.chart_type === 'big_number_trend') expect(sampleOf(chart).rows.length >= 2).toBe(true);
        if (chart.chart_type === 'gauge') {
          expect(typeof chart.config?.maxValue).toBe('number');
          expect(chart.config?.max).toBeUndefined(); // 历史死配置键，不得回到模板里
        }
      }
    }
  });

  it('位置落在画布宽度内且互不重叠，画布是大屏默认 1920 宽', () => {
    for (const t of DASHBOARD_TEMPLATES) {
      expect(t.canvas.width).toBe(TEMPLATE_CANVAS.width);
      const rects = t.charts.map(c => c.position);
      for (const r of rects) {
        expect(r.x >= 0).toBe(true);
        expect(r.x + r.w <= t.canvas.width).toBe(true);
      }
      for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) {
          const a = rects[i], b = rects[j];
          const overlap = a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
          expect(overlap).toBe(false);
        }
      }
    }
  });

  it('大屏模板的 KPI 独占一整行（6 张窄卡横向铺开）', () => {
    const screen = DASHBOARD_TEMPLATES.find(t => t.id === 'command_center')!;
    const kpis = screen.charts.filter(c => ['big_number_trend', 'gauge'].includes(c.chart_type));
    expect(kpis.length).toBe(6);
    expect(new Set(kpis.map(c => c.position.x)).size).toBe(6);
  });

  it('样式模板不携带任何 SQL（只展示样式，不假设环境里的表结构）', () => {
    const raw = JSON.stringify(DASHBOARD_TEMPLATES.map(t => t.charts));
    expect(raw).not.toMatch(/SELECT\s|FROM\s+\w+|demoSql/i);
    for (const t of DASHBOARD_TEMPLATES) {
      for (const chart of t.charts) expect(chart.sql_query).toBeUndefined();
    }
    expect(errorsOf(c => ({ ...c, sql_query: 'SELECT dt FROM dwd_demo' })).some(e => /不得携带 SQL/.test(e))).toBe(true);
  });

  it('模板不写死配色/渐变、不预置 datasource_id（样式唯一源是字模库主题包）', () => {
    const raw = JSON.stringify(DASHBOARD_TEMPLATES.map(t =>
      t.charts.map(c => ({ chart_type: c.chart_type, position: c.position, config: c.config }))));
    expect(raw).not.toMatch(/colorScheme|cardBg|gradient/);
    expect(raw).not.toMatch(/#[0-9a-f]{3,8}/i);
    expect(raw).not.toMatch(/datasource_id/);
  });

  it('每份示例数据都能被真实渲染器消成可用 spec（导入即见效果的最后一道）', () => {
    for (const t of DASHBOARD_TEMPLATES) {
      for (const chart of t.charts) {
        // table_value 走 HTML 表格分支，不在 G2 spec 里
        if (chart.chart_type === 'table_value') continue;
        const sample = sampleOf(chart);
        const spec: any = buildG2Spec(chart.chart_type, sample.columns, sample.rows, chart.config || {}, false, 640, 320);
        const dumped = JSON.stringify(spec);
        expect(dumped, `${t.id}/${chart.name} spec 含 NaN/undefined`).not.toMatch(/NaN|undefined|Infinity/);
        expect(spec.type).toBeTruthy();
        // 编码列必须存在于“与它同层的数据”里（列名错一位就是空图）；
        // pie/funnel 等分支会先把行重塑成 {name,value}，因此逐层对照而非直接对示例数据
        const problems: string[] = [];
        const collect = (node: any) => {
          if (!node || typeof node !== 'object') return;
          if (Array.isArray(node)) { node.forEach(collect); return; }
          const encode = node.encode;
          const rows = Array.isArray(node.data) && typeof node.data[0] === 'object' ? node.data : null;
          if (encode && rows) {
            for (const axis of ['x', 'y', 'color']) {
              const value = encode[axis];
              const cols = [value].flat().filter((c: any) => typeof c === 'string') as string[];
              for (const col of cols) {
                if (!(col in rows[0]) && !/^(name|value|pv|x|y|series|action|text)$/.test(col)) problems.push(`${axis}=${col}`);
              }
            }
          }
          Object.values(node).forEach(collect);
        };
        collect(spec);
        expect(problems, `${t.id}/${chart.name}`).toEqual([]);
      }
    }
  });
});

describe('校验器真的会拦（不是摆设）', () => {
  const base = DASHBOARD_TEMPLATES[0];

  it('悬空图表类型 / 越界位置 / 缺示例数据 / 映射列不存在 / 预置 datasource_id 都被点名', () => {
    expect(errorsOf(c => ({ ...c, chart_type: 'pie_chart' })).some(e => /CHART_TYPES/.test(e))).toBe(true);
    expect(errorsOf(c => ({ ...c, position: { x: 0, y: 0, w: 3000, h: 200 } })).some(e => /超出画布宽度/.test(e))).toBe(true);
    expect(errorsOf(c => ({ ...c, sample: undefined })).some(e => /缺少示例数据/.test(e))).toBe(true);
    expect(errorsOf(c => ({ ...c, config: { yCol: 'nope' } })).some(e => /字段映射 nope/.test(e))).toBe(true);
    expect(errorsOf(c => ({ ...c, config: { ...c.config, datasource_id: 7 } })).some(e => /datasource_id/.test(e))).toBe(true);
    expect(errorsOf(c => ({ ...c, config: { ...c.config, demoData: false } })).some(e => /示例标记/.test(e))).toBe(true);
    expect(errorsOf(c => ({ ...c, config: { ...c.config, valueCol: 'sales' } })).some(e => /不被渲染层消费/.test(e))).toBe(true);
    expect(errorsOf(c => ({ ...c, chart_type: 'china_map' })).some(e => /CDN/.test(e))).toBe(true);
    expect(validateTemplates([{ ...base, charts: [base.charts[0], base.charts[0]] }]).some(e => /位置重叠/.test(e))).toBe(true);
  });
});
