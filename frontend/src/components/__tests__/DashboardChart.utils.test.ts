import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createElement, useEffect } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import * as G2 from '@antv/g2'
import postcss from 'postcss'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { normalizeRows } from '../../lib/chartBlocks'
import { applyTheme, IS_DARK, useThemeStore, type ThemeId } from '../../stores/themeStore'
import { useDashboardStore } from '../../stores/dashboardStore'
import DashboardChart, {
  detectGroupColumn,
  isTimeColumn,
  parseTime,
  aggregateTimeData,
  groupDataByColumn,
  buildG2Spec,
  type ChartPaging,
} from '../DashboardChart'

// ============================================================
// detectGroupColumn
// ============================================================
describe('detectGroupColumn', () => {
  it('returns explicit config.groupCol', () => {
    const cols = ['date', 'value', 'region']
    const rows = [{ date: '2024-01-01', value: 10, region: 'East' }]
    expect(detectGroupColumn(cols, rows, { groupCol: 'region' })).toBe('region')
  })

  it('auto-detects group column with multiple unique string values', () => {
    const cols = ['date', 'value', 'region']
    const rows = [
      { date: '2024-01-01', value: 10, region: 'East' },
      { date: '2024-01-01', value: 20, region: 'West' },
      { date: '2024-01-02', value: 15, region: 'East' },
      { date: '2024-01-02', value: 25, region: 'West' },
    ]
    expect(detectGroupColumn(cols, rows, {})).toBe('region')
  })

  it('returns null when no suitable group column exists', () => {
    const cols = ['date', 'value']
    const rows = [{ date: '2024-01-01', value: 10 }]
    expect(detectGroupColumn(cols, rows, {})).toBeNull()
  })

  it('returns null when candidate has only one unique value', () => {
    const cols = ['date', 'value', 'type']
    const rows = [
      { date: '2024-01-01', value: 10, type: 'A' },
      { date: '2024-01-02', value: 20, type: 'A' },
    ]
    expect(detectGroupColumn(cols, rows, {})).toBeNull()
  })

  it('skips xCol and yCol from config', () => {
    const cols = ['date', 'value', 'category']
    const rows = [
      { date: '2024-01-01', value: 10, category: 'A' },
      { date: '2024-01-02', value: 20, category: 'B' },
      { date: '2024-01-03', value: 30, category: 'A' },
    ]
    // xCol='date', yCol='value' are excluded, 'category' is the candidate
    expect(detectGroupColumn(cols, rows, { xCol: 'date', yCol: 'value' })).toBe('category')
  })

  it('returns null for empty rows', () => {
    expect(detectGroupColumn(['a', 'b'], [], {})).toBeNull()
  })
})

// ============================================================
// isTimeColumn
// ============================================================
describe('isTimeColumn', () => {
  it('detects ISO date strings', () => {
    expect(isTimeColumn(['2024-01-01', '2024-01-02', '2024-01-03'])).toBe(true)
  })

  it('detects datetime strings', () => {
    expect(isTimeColumn(['2024-01-01 10:00:00', '2024-01-02 12:00:00'])).toBe(true)
  })

  it('detects slash-separated dates', () => {
    expect(isTimeColumn(['2024/01/01', '2024/01/02'])).toBe(true)
  })

  it('detects unix timestamps (10-digit)', () => {
    expect(isTimeColumn(['1704067200', '1704153600'])).toBe(true)
  })

  it('detects unix timestamps (13-digit)', () => {
    expect(isTimeColumn(['1704067200000', '1704153600000'])).toBe(true)
  })

  it('returns false for non-time strings', () => {
    expect(isTimeColumn(['apple', 'banana', 'cherry'])).toBe(false)
  })

  it('returns false for non-time strings', () => {
    expect(isTimeColumn(['apple', 'banana', 'cherry'])).toBe(false)
  })

  it('returns false for empty array', () => {
    expect(isTimeColumn([])).toBe(false)
  })

  it('returns true for time-only strings', () => {
    expect(isTimeColumn(['10:00:00', '12:30:00'])).toBe(true)
  })
})

// ============================================================
// parseTime
// ============================================================
describe('parseTime', () => {
  it('returns null for null/undefined', () => {
    expect(parseTime(null)).toBeNull()
    expect(parseTime(undefined)).toBeNull()
    expect(parseTime('')).toBeNull()
  })

  it('parses 10-digit unix timestamp', () => {
    const result = parseTime('1704067200')
    expect(result).toBeInstanceOf(Date)
    expect(result!.getFullYear()).toBe(2024)
  })

  it('parses 13-digit unix timestamp', () => {
    const result = parseTime('1704067200000')
    expect(result).toBeInstanceOf(Date)
    expect(result!.getFullYear()).toBe(2024)
  })

  it('parses ISO date string', () => {
    const result = parseTime('2024-01-15')
    expect(result).toBeInstanceOf(Date)
    expect(result!.getMonth()).toBe(0) // January
    expect(result!.getDate()).toBe(15)
  })

  it('parses ISO datetime string', () => {
    const result = parseTime('2024-06-15T10:30:00Z')
    expect(result).toBeInstanceOf(Date)
    expect(result!.getFullYear()).toBe(2024)
    expect(result!.getMonth()).toBe(5) // June
  })

  it('returns null for invalid string', () => {
    expect(parseTime('not-a-date')).toBeNull()
  })

  it('parses number as timestamp', () => {
    const result = parseTime(1704067200000)
    expect(result).toBeInstanceOf(Date)
    expect(result!.getFullYear()).toBe(2024)
  })
})

// ============================================================
// aggregateTimeData
// ============================================================
describe('aggregateTimeData', () => {
  const rows = [
    { date: '2024-01-01', value: '10', region: 'East' },
    { date: '2024-01-01', value: '20', region: 'West' },
    { date: '2024-01-02', value: '15', region: 'East' },
    { date: '2024-01-02', value: '25', region: 'West' },
    { date: '2024-01-03', value: '30', region: 'East' },
  ]

  it('aggregates single series with sum', () => {
    const result = aggregateTimeData(rows, 'date', 'value', null, 'sum', 'day')
    expect(result.xData).toEqual(['2024-01-01', '2024-01-02', '2024-01-03'])
    expect(result.yData).toEqual([30, 40, 30]) // 10+20, 15+25, 30
  })

  it('aggregates single series with avg', () => {
    const result = aggregateTimeData(rows, 'date', 'value', null, 'avg', 'day')
    expect(result.yData).toEqual([15, 20, 30])
  })

  it('aggregates single series with max', () => {
    const result = aggregateTimeData(rows, 'date', 'value', null, 'max', 'day')
    expect(result.yData).toEqual([20, 25, 30])
  })

  it('aggregates single series with min', () => {
    const result = aggregateTimeData(rows, 'date', 'value', null, 'min', 'day')
    expect(result.yData).toEqual([10, 15, 30])
  })

  it('aggregates multi-series with group column', () => {
    const result = aggregateTimeData(rows, 'date', 'value', 'region', 'sum', 'day')
    expect(result.xData).toEqual(['2024-01-01', '2024-01-02', '2024-01-03'])
    expect(result.seriesData).toBeDefined()
    expect(result.seriesData!['East']).toEqual([10, 15, 30])
    expect(result.seriesData!['West']).toEqual([20, 25, null])
  })

  it('handles empty rows', () => {
    const result = aggregateTimeData([], 'date', 'value', null, 'sum', 'day')
    expect(result.xData).toEqual([])
  })

  it('filters out rows with unparseable dates', () => {
    const badRows = [
      { date: '2024-01-01', value: '10' },
      { date: 'invalid', value: '20' },
      { date: '2024-01-02', value: '30' },
    ]
    const result = aggregateTimeData(badRows, 'date', 'value', null, 'sum', 'day')
    expect(result.xData).toEqual(['2024-01-01', '2024-01-02'])
    expect(result.yData).toEqual([10, 30])
  })

  it('auto-detects day granularity for 30-day range', () => {
    const dayRows = Array.from({ length: 30 }, (_, i) => ({
      date: `2024-01-${String(i + 1).padStart(2, '0')}`,
      value: String(i),
    }))
    const result = aggregateTimeData(dayRows, 'date', 'value', null, 'sum', 'auto')
    expect(result.xData.length).toBe(30)
  })

  it('auto-detects month granularity for multi-year range', () => {
    const monthRows = [
      { date: '2020-01-01', value: '100' },
      { date: '2022-06-15', value: '200' },
      { date: '2024-12-31', value: '300' },
    ]
    const result = aggregateTimeData(monthRows, 'date', 'value', null, 'sum', 'auto')
    // Should have month-level aggregation
    expect(result.xData.length).toBeGreaterThan(0)
  })
})

// ============================================================
// groupDataByColumn
// ============================================================
describe('groupDataByColumn', () => {
  it('groups data and sorts x-axis alphabetically', () => {
    const rows = [
      { category: 'B', value: 10, type: 'X' },
      { category: 'A', value: 20, type: 'Y' },
      { category: 'B', value: 30, type: 'Y' },
      { category: 'A', value: 40, type: 'X' },
    ]
    const result = groupDataByColumn(rows, 'category', 'value', 'type')
    expect(result.xData).toEqual(['A', 'B'])
    expect(result.seriesData['X']).toEqual([40, 10])
    expect(result.seriesData['Y']).toEqual([20, 30])
  })

  it('sorts time-based x-axis chronologically', () => {
    const rows = [
      { date: '2024-01-03', value: 10, type: 'A' },
      { date: '2024-01-01', value: 20, type: 'A' },
      { date: '2024-01-02', value: 30, type: 'A' },
    ]
    const result = groupDataByColumn(rows, 'date', 'value', 'type')
    expect(result.xData).toEqual(['2024-01-01', '2024-01-02', '2024-01-03'])
  })

  it('fills null for missing group values', () => {
    const rows = [
      { x: 'A', y: 10, g: 'X' },
      { x: 'B', y: 20, g: 'X' },
      { x: 'A', y: 30, g: 'Y' },
      // B/Y is missing
    ]
    const result = groupDataByColumn(rows, 'x', 'y', 'g')
    expect(result.seriesData['Y'][1]).toBeNull()
  })

  it('handles empty rows', () => {
    const result = groupDataByColumn([], 'x', 'y', 'g')
    expect(result.xData).toEqual([])
    expect(Object.keys(result.seriesData)).toEqual([])
  })
})

// ============================================================
// buildG2Spec
// ============================================================
describe('buildG2Spec', () => {
  const columns = ['category', 'value']
  const rows = [
    { category: 'A', value: 10 },
    { category: 'B', value: 20 },
    { category: 'C', value: 30 },
  ]

  it('builds bar chart spec', () => {
    const spec = buildG2Spec('bar', columns, rows, {}, false, 400, 300)
    expect(spec.type).toBe('interval')
    expect(spec.width).toBe(400)
    expect(spec.height).toBe(300)
    expect(spec.data).toEqual(rows)
    expect(spec.encode.x).toBe('category')
    expect(spec.encode.y).toBe('value')
  })

  it('builds line chart spec', () => {
    const spec = buildG2Spec('line', columns, rows, {}, false, 400, 300)
    expect(spec.type).toBe('line')
    expect(spec.encode.x).toBe('category')
  })

  it('builds pie chart spec', () => {
    const spec = buildG2Spec('pie', columns, rows, {}, false, 400, 300)
    expect(spec.type).toBe('interval')
    expect(spec.coordinate.type).toBe('theta')
    expect(spec.data).toEqual([
      { name: 'A', value: 10 },
      { name: 'B', value: 20 },
      { name: 'C', value: 30 },
    ])
  })

  it('builds scatter chart spec', () => {
    const scatterCols = ['x', 'y']
    const scatterRows = [{ x: 1, y: 2 }, { x: 3, y: 4 }]
    const spec = buildG2Spec('scatter', scatterCols, scatterRows, {}, false, 400, 300)
    expect(spec.type).toBe('point')
  })

  it('builds area chart spec', () => {
    const spec = buildG2Spec('area', columns, rows, {}, false, 400, 300)
    expect(spec.type).toBe('area')
  })

  it('builds funnel chart spec', () => {
    const spec = buildG2Spec('funnel', columns, rows, {}, false, 400, 300)
    expect(spec.type).toBe('interval')
    expect(spec.data[0]).toHaveProperty('action')
    expect(spec.data[0]).toHaveProperty('pv')
  })

  it('builds gauge chart spec', () => {
    const gaugeRows = [{ label: 'CPU', value: 75 }]
    const spec = buildG2Spec('gauge', ['label', 'value'], gaugeRows, {}, false, 400, 300)
    expect(spec.type).toBe('gauge')
    expect(spec.data).toEqual({ target: 75, total: 100, thresholds: [100] })
  })

  it('仪表盘按量程转换比例阈值并限制越界值', () => {
    const spec = buildG2Spec('gauge', ['label', 'value'], [{ label: 'CPU', value: 75 }], { maxValue: 100, thresholds: [0.85, 0.6, 0.6] }, true, 400, 300)
    expect(spec.data).toEqual({ target: 75, total: 100, thresholds: [60, 85, 100] })
    const ratio = buildG2Spec('gauge', ['label', 'value'], [{ label: 'CPU', value: 0.75 }], {}, false, 400, 300)
    expect(ratio.data).toEqual({ target: 0.75, total: 1, thresholds: [1] })
  })

  it.each(['heatmap', 'calendar_heatmap'])('%s 采用字模色板', chartType => {
    const colors = ['#22d3ee', '#3b82f6']
    const spec = buildG2Spec(chartType, columns, rows, {}, true, 400, 300, { mode: 'dark', palette: colors })
    expect(spec.scale.color.range).toEqual(colors)
  })

  it('builds waterfall chart spec', () => {
    const waterfallRows = [
      { category: 'Start', value: 100 },
      { category: 'Q1', value: 20 },
      { category: 'Q2', value: -10 },
    ]
    const spec = buildG2Spec('waterfall', ['category', 'value'], waterfallRows, {}, false, 400, 300)
    expect(spec.type).toBe('interval')
    expect(spec.data[0]).toHaveProperty('start')
    expect(spec.data[0]).toHaveProperty('end')
  })

  // 桑基图历史缺陷：节点写成 {name}，而 G2 的 sankey 默认 nodeId = d => d.key 索引节点，
  // d3-sankey 找不到节点直接抛 “missing: A”，错误只进 console → 整幅空白（用户看到“我的图呢”）。
  const sankeyCols = ['source', 'target', 'value'];
  const sankeyRows = [
    { source: 'A', target: 'B', value: 10 },
    { source: 'A', target: 'C', value: 20 },
  ];

  /** 把 spec 的 sankey 子 mark 喂给 G2 自己的布局函数（包 exports 把 ./lib/* 映射到 ./lib/*.js，
   *  所以 require 路径不能带 .js 后缀）。节点缺 key 时这里直接抛 “missing: xxx”。 */
  const layOutSankey = (spec: any) => {
    const req = createRequire(import.meta.url);
    const { initializeData } = req('@antv/g2/lib/mark/utils');
    const { Sankey } = req('@antv/g2/lib/data/sankey');
    const child = spec.children[0];
    return Sankey({ nodeId: (d: any) => d.key, nodeWidth: 0.02, nodePadding: 0.02 })(
      initializeData(child.data, child.encode),
    );
  };

  it('builds sankey chart spec', () => {
    const spec = buildG2Spec('sankey', sankeyCols, sankeyRows, {}, false, 400, 300)
    expect(spec.type).toBe('view')
    expect(spec.children[0].type).toBe('sankey')
    // data 必须是边数组：G2 5.4 的 sankey 拿 {nodes, links} 对象时整幅静默不画（无报错无告警），
    // 节点由它按边端点自己派生（浏览器实测：对象 0 彩色像素 / 数组 35 万）。
    expect(Array.isArray(spec.children[0].data)).toBe(true)
    expect(spec.children[0].data).toEqual([
      { source: 'A', target: 'B', value: 10 },
      { source: 'A', target: 'C', value: 20 },
    ])
    expect(spec.children[0].encode.value).toEqual('value')
    // 带子按源节点取色：不写 linkColor 时 G2 把所有边刷成 #aaa@50%（浅底上就是一片 212 灰），
    // 多条边糊在一起看不出谁到谁。必须传返回字符串的函数（传字段名会拿到节点对象而报错）。
    const linkColor = spec.children[0].encode.linkColor
    expect(typeof linkColor).toBe('function')
    expect(linkColor({ source: { key: 'A' }, target: { key: 'B' } })).toBe('A')
    expect(linkColor({ source: 'A', target: 'B' })).toBe('A')
  })

  it('桑基 spec 必须能被 G2 的 sankey 布局吃下（算得出节点与 ribbon 几何）', () => {
    const laid = layOutSankey(buildG2Spec('sankey', sankeyCols, sankeyRows, {}, false, 400, 300) as any)
    expect(laid.nodes).toHaveLength(3)
    expect(laid.links[0].x).toHaveLength(4)
    expect(Number.isFinite(laid.links[0].x[0])).toBe(true)
  })

  // 真实会话载荷（adh_conversation_messages 1783069459145）：```chart 块用「数组的数组」行，
  // 归一化后只剩一条边 —— 单条边也必须算得出几何（历史缺陷：节点缺 key 时抛 missing → 整幅空白）。
  it('```chart 块的数组行归一化后，单条边的桑基照样画得出', () => {
    const columns = ['源', '目标', '流量'];
    const rows = normalizeRows(columns, [['外部链接进入（/en/direct-ip?channel=EDMCA）', '/en/direct-ip', 20]]);
    const spec: any = buildG2Spec('sankey', columns, rows, { xCol: '源', yCol: '流量' }, false, 800, 400);
    expect(spec.children[0].data).toEqual([
      { source: '外部链接进入（/en/direct-ip?channel=EDMCA）', target: '/en/direct-ip', value: 20 },
    ]);
    const laid = layOutSankey(spec);
    expect(laid.nodes).toHaveLength(2);
    expect(laid.links).toHaveLength(1);
    expect(laid.links[0].x.every(Number.isFinite)).toBe(true);
  })

  it('桑基按 X=源 / 分组=目标 / Y=数值 取列（选择框只有通用 X/Y/分组）', () => {
    const rows = [
      { 源: '首页', 去向: '详情页', 流量: 5 },
      { 源: '首页', 去向: '活动页', 流量: 7 },
    ];
    const spec: any = buildG2Spec('sankey', ['源', '去向', '流量'], rows,
      { xCol: '源', yCol: '流量' }, false, 400, 300);
    expect(spec.children[0].data).toEqual([
      { source: '首页', target: '详情页', value: 5 },
      { source: '首页', target: '活动页', value: 7 },
    ]);
  })

  it('只有一个文本列（没有目标列）时显式报错，不画“节点名叫数字”的假图', () => {
    const rows = [{ 源: '首页', 流量: 5 }];
    expect(() => buildG2Spec('sankey', ['源', '流量'], rows,
      { xCol: '源', yCol: '流量' }, false, 400, 300)).toThrow(/目标/);
  })

  it('builds heatmap chart spec', () => {
    const heatCols = ['day', 'hour', 'count']
    const heatRows = [{ day: 'Mon', hour: '9am', count: 5 }]
    const spec = buildG2Spec('heatmap', heatCols, heatRows, {}, false, 400, 300)
    expect(spec.type).toBe('cell')
  })

  it('uses dark theme colors when isDark=true', () => {
    const specLight = buildG2Spec('bar', columns, rows, {}, false, 400, 300)
    const specDark = buildG2Spec('bar', columns, rows, {}, true, 400, 300)
    // Both should have color range, but different palettes
    expect(specLight.scale.color.range).not.toEqual(specDark.scale.color.range)
  })

  it('defaults to bar chart for unknown type', () => {
    const spec = buildG2Spec('unknown_type', columns, rows, {}, false, 400, 300)
    expect(spec.type).toBe('interval')
  })

  it('builds text_display spec', () => {
    const kpiRows = [{ metric: 'Revenue', value: 12345 }]
    const spec = buildG2Spec('text_display', ['metric', 'value'], kpiRows, {}, false, 400, 300)
    expect(spec.type).toBe('view')
    expect(spec.children.length).toBeGreaterThanOrEqual(1)
    expect(spec.children[0].style.text).toContain('12,345')
  })

  it('builds big_number_trend spec', () => {
    const trendRows = [
      { month: 'Jan', value: 100 },
      { month: 'Feb', value: 120 },
      { month: 'Mar', value: 110 },
    ]
    const spec = buildG2Spec('big_number_trend', ['month', 'value'], trendRows, {}, false, 400, 300)
    expect(spec.type).toBe('view')
    // 趋势线先画、文字后画（折线压在数值上会糊），数值 330 与涨幅各占一个 text 子节点
    expect(spec.children[0].type).toBe('line')
    const texts = spec.children.filter((c: any) => c.type === 'text').map((c: any) => c.style.text).join(' ')
    expect(texts).toContain('330')
  })

  it('builds rose chart spec', () => {
    const spec = buildG2Spec('rose', columns, rows, {}, false, 400, 300)
    expect(spec.type).toBe('interval')
    expect(spec.coordinate.type).toBe('polar')
  })

  it('builds tree chart spec', () => {
    const treeRows = [
      { name: 'Root', value: 100, parent: '' },
      { name: 'Child1', value: 50, parent: 'Root' },
      { name: 'Child2', value: 50, parent: 'Root' },
    ]
    const spec = buildG2Spec('tree', ['name', 'value', 'parent'], treeRows, {}, false, 400, 300)
    expect(spec.type).toBe('tree')
  })

  it('builds word_cloud spec', () => {
    const wordRows = [
      { word: 'hello', count: 10 },
      { word: 'world', count: 5 },
    ]
    const spec = buildG2Spec('word_cloud', ['word', 'count'], wordRows, {}, false, 400, 300)
    expect(spec.type).toBe('wordCloud')
  })

  it('respects config.xCol and config.yCol', () => {
    const multiCols = ['name', 'age', 'score']
    const multiRows = [{ name: 'Alice', age: 30, score: 95 }]
    const spec = buildG2Spec('bar', multiCols, multiRows, { xCol: 'name', yCol: 'score' }, false, 400, 300)
    expect(spec.encode.x).toBe('name')
    expect(spec.encode.y).toBe('score')
  })

  it('builds timeseries_line spec', () => {
    const tsRows = [
      { date: '2024-01-01', value: 10 },
      { date: '2024-01-02', value: 20 },
    ]
    const spec = buildG2Spec('timeseries_line', ['date', 'value'], tsRows, {}, false, 400, 300)
    expect(spec.type).toBe('line')
  })

  it('builds boxplot spec', () => {
    const boxRows = [
      { category: 'A', value: 10 },
      { category: 'A', value: 20 },
      { category: 'A', value: 30 },
      { category: 'B', value: 15 },
      { category: 'B', value: 25 },
    ]
    const spec = buildG2Spec('boxplot', ['category', 'value'], boxRows, {}, false, 400, 300)
    expect(spec.type).toBe('box')
  })

  it('builds radar spec', () => {
    const radarRows = [
      { metric: 'Speed', score: 80, category: 'A' },
      { metric: 'Power', score: 60, category: 'A' },
    ]
    const spec = buildG2Spec('radar', ['metric', 'score', 'category'], radarRows, {}, false, 400, 300)
    expect(spec.type).toBe('line')
    expect(spec.coordinate.type).toBe('polar')
  })
})

// 使用实际主题令牌，覆盖无分组图表、分组色盘及父级应用主题的时序。
describe('图表主题适配', () => {
  const columns = ['日期', '病例数量']
  const rows = [
    { 日期: '2026-09-14', 病例数量: 8 },
    { 日期: '2026-09-15', 病例数量: 20 },
    { 日期: '2026-09-16', 病例数量: 36 },
  ]
  let stylesheet: HTMLStyleElement
  let originalClass: string
  let themeCSS: string
  const originalTheme = useThemeStore.getState().theme

  beforeEach(() => {
    originalClass = document.documentElement.className
    stylesheet = document.createElement('style')
    const rules: string[] = []
    themeCSS = readFileSync('src/styles/globals.css', 'utf8')
    postcss.parse(themeCSS).walkRules(rule => {
      if (rule.selector === ':root' || /^\.(light|tech|finance|bento|glass|ainative|medical|datafoundry)$/.test(rule.selector)) {
        rules.push(rule.toString())
      }
    })
    stylesheet.textContent = rules.join('\n')
    document.head.append(stylesheet)
    applyTheme('datafoundry')
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    stylesheet.remove()
    document.documentElement.className = originalClass
    useThemeStore.setState({ theme: originalTheme, isDark: IS_DARK[originalTheme] })
  })

  it.each(['bar', 'line', 'area', 'timeseries_bar', 'timeseries_line', 'timeseries_area'])(
    '%s 无分组时使用近黑主色，不回退 G2 默认蓝色', chartType => {
      const spec = buildG2Spec(chartType, columns, rows, {}, false, 400, 300)
      expect(spec.encode.color).toBeUndefined()
      expect(spec.theme.color).toBe('hsl(0, 0%, 5%)')
      // 验证 G2 实际主题解析器接收到了默认色，而不只是 scale.color.range。
      const actualTheme = G2.stdlib()['theme.classic'](spec.theme)
      expect(actualTheme.color).toBe('hsl(0, 0%, 5%)')
      expect((actualTheme as any).view.viewFill).toBe('hsl(0, 0%, 100%)')
    },
  )

  it.each(['bar', 'line', 'timeseries_bar', 'timeseries_line', 'timeseries_area'])(
    '%s 分组时保留不同颜色，不被单系列默认色覆盖', chartType => {
      const groupedRows = rows.flatMap(row => [
        { ...row, 地区: '东区' }, { ...row, 地区: '西区' },
      ])
      const spec = buildG2Spec(chartType, [...columns, '地区'], groupedRows, { groupCol: '地区' }, false, 400, 300)
      expect(spec.encode.color).toBe('地区')
      expect(spec.scale.color.range.slice(0, 2)).toEqual(['hsl(0, 0%, 5%)', 'hsl(0, 0%, 40%)'])
      expect(new Set(spec.scale.color.range).size).toBe(10)
      expect(spec.theme.category10).toEqual(spec.scale.color.range)
    },
  )

  it('背景、坐标轴、图例与浮层使用主题令牌', () => {
    const spec = buildG2Spec('timeseries_bar', columns, rows, {}, false, 400, 300)
    expect(spec.theme.view.viewFill).toBe('hsl(0, 0%, 100%)')
    expect(spec.theme.axis.labelOpacity).toBe(1)
    expect(spec.theme.axis.gridStroke).toBe('hsl(0, 0%, 93%)')
    expect(spec.theme.legendCategory.itemLabelFill).toBe('hsl(0, 0%, 40%)')
    expect(spec.theme.tooltip.css['.g2-tooltip']['background-color']).toBe('hsl(0, 0%, 100%)')
    expect(spec.theme.tooltip.css['.g2-tooltip'].color).toBe('hsl(0, 0%, 5%)')
    // 数值与名称显式取 popover-foreground，避免深色主题下与背景同色而看不清。
    expect(spec.theme.tooltip.css['.g2-tooltip-list-item-value'].color).toBe('hsl(0, 0%, 5%)')
    expect(spec.theme.tooltip.css['.g2-tooltip-list-item-name-label'].color).toBe('hsl(0, 0%, 5%)')
  })

  it('全局 UI tooltip 规则排除 G2 图表 tooltip，不再污染其子元素', () => {
    for (const theme of ['datafoundry', 'ainative', 'medical']) {
      const rule = new RegExp(`\\.${theme} \\[class\\*="tooltip"\\][^{]*\\{`)
      const match = themeCSS.match(rule)
      expect(match, `${theme} 应有 tooltip 主题规则`).toBeTruthy()
      expect(match![0], `${theme} 的 tooltip 规则应排除 g2-tooltip`).toContain(':not([class*="g2-tooltip"])')
    }
  })

  it('其他主题仍使用各自色盘', () => {
    // light/dark 已就地换皮为 AS 风格（design-service 预设）：
    // 亮态主色 #0037CB、暗态主色 #809BE5（旧 Kibana 蓝 217 91% 60% 不再作为明暗态主色）。
    const expectedColors = {
      datafoundry: 'hsl(0, 0%, 5%)',
      light: 'hsl(224, 100%, 40%)',
      dark: 'hsl(224, 66%, 70%)',
      tech: 'hsl(190, 100%, 50%)',
    }
    for (const theme of Object.keys(expectedColors) as (keyof typeof expectedColors)[]) {
      applyTheme(theme)
      const spec = buildG2Spec('timeseries_bar', columns, rows, {}, IS_DARK[theme], 400, 300)
      expect(spec.theme.color).toBe(expectedColors[theme])
      expect(spec.theme.color).toBe(spec.scale.color.range[0])
      expect(spec.theme.type).toBe(IS_DARK[theme] ? 'classicDark' : 'classic')
    }
  })

  it('等父级应用主题后再取色，同明暗切换及卸载时取消旧帧', () => {
    const frames = new Map<number, FrameRequestCallback>()
    let nextFrame = 0
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      frames.set(++nextFrame, callback)
      return nextFrame
    })
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id))
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(400)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(300)
    const specs: any[] = []
    const destroy = vi.fn()
    vi.spyOn(G2, 'Chart').mockImplementation(function (this: any) {
      this.options = (spec: any) => specs.push(spec)
      this.render = vi.fn()
      this.destroy = destroy
    } as any)

    function ThemeLayout() {
      const theme = useThemeStore(state => state.theme)
      useEffect(() => { applyTheme(theme) }, [theme])
      return createElement(DashboardChart, { chartType: 'timeseries_bar', data: { columns, rows } })
    }
    const flushFrame = () => act(() => {
      const pending = [...frames.values()]
      frames.clear()
      pending.forEach(callback => callback(0))
    })

    useThemeStore.setState({ theme: 'datafoundry', isDark: false })
    const mounted = render(createElement(ThemeLayout))
    expect(specs).toHaveLength(0)
    flushFrame()
    expect(specs[specs.length - 1].theme.color).toBe('hsl(0, 0%, 5%)')

    for (const theme of ['dark', 'tech'] as ThemeId[]) {
      act(() => useThemeStore.getState().setTheme(theme))
      flushFrame()
      const expected = buildG2Spec('timeseries_bar', columns, rows, {}, true, 400, 300)
      expect(specs[specs.length - 1].theme.color).toBe(expected.theme.color)
    }
    expect(specs[specs.length - 2].theme.color).not.toBe(specs[specs.length - 1].theme.color)
    expect(destroy).toHaveBeenCalled()

    act(() => useThemeStore.getState().setTheme('datafoundry'))
    mounted.unmount()
    expect(frames.size).toBe(0)
  })
})

// 表值图走 HTML 渲染，不走 G2 spec —— 字模键必须在 DOM 上真实生效（§2「存了不生效」防线）。
describe('表值图渲染消费字模', () => {
  const data = { columns: ['name', 'value'], rows: [{ name: 'a', value: 1 }, { name: 'b', value: 2 }] }

  it('headerBg/headerColor/zebraBg/rowHeight/tableFontSize 落到表格内联样式', () => {
    const { container } = render(createElement(DashboardChart, {
      chartType: 'table_value', data,
      config: { headerBg: '#F7F8FA', headerColor: '#262626', zebraBg: '#FAFBFC', rowHeight: 32, tableFontSize: 13 },
    }))
    const table = container.querySelector('table')
    expect(table?.style.fontSize).toBe('13px')
    const head = container.querySelector('thead tr') as HTMLElement
    expect(head.style.background).toContain('rgb(247, 248, 250)')
    expect(head.style.height).toBe('32px')
    expect((container.querySelector('th') as HTMLElement).style.color).toContain('rgb(38, 38, 38)')
    const body = container.querySelectorAll<HTMLElement>('tbody tr')
    expect(body[0].style.background).toBe('transparent')
    expect(body[1].style.background).toContain('rgb(250, 251, 252)')   // 偶数行斑马纹取字模色
    expect(body[1].style.height).toBe('32px')
  })

  it('未挂字模时保持原默认观感（存量表格不被改动）', () => {
    const { container } = render(createElement(DashboardChart, { chartType: 'table_value', data, config: {} }))
    expect((container.querySelector('table') as HTMLElement).style.fontSize).toBe('12px')
    expect((container.querySelector('thead tr') as HTMLElement).style.height).toBe('')
    const body = container.querySelectorAll<HTMLElement>('tbody tr')
    expect(body[1].style.background).not.toContain('rgb(250, 251, 252)')
  })
})

// 历史缺陷（2026-11-11 收口）：表值图把全量拉进前端再 slice，
// “共 100 行”挂在 446 万行的明细表上 —— 那是伪分页。
// 现口径：分页事实（page_size/page_num/total/can_jump）由服务端随取数结果回传，
// 前端只渲染服务端给的这一页，翻页就是一次带页参的取数。
describe('表值图服务端真分页', () => {
  const paged = (from: number, n: number) => ({
    columns: ['case_id', 'site'],
    rows: Array.from({ length: n }, (_, i) => ({ case_id: `C${from + i}`, site: 'SH' })),
  })
  const at = (over: Partial<{ total: number; paging: ChartPaging; truncated: boolean }>) => ({ ...paged(1, 20), ...over })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('回显 paging 时总数用服务端 total，不拿本页行数冒充全量', () => {
    const { container } = render(createElement(DashboardChart, {
      chartType: 'table_value', config: { pageSize: 20 }, chartId: 1,
      data: at({
        total: 4460000,
        paging: { mode: 'offset', page_size: 20, page_num: 1, total: 4460000, can_jump: true },
      }),
    }))
    // 服务端只给了 20 行，前端就只渲染 20 行（旧实现会先拉满再 slice）
    expect(container.querySelectorAll('tbody tr')).toHaveLength(20)
    expect(screen.getByText(`共 4460000 行 · 第 1/${Math.ceil(4460000 / 20)} 页`)).toBeInTheDocument()
  })

  it('页大小以服务端回显为准：配置残留旧的大页不会把行错位', () => {
    const { container } = render(createElement(DashboardChart, {
      chartType: 'table_value', config: { pageSize: 50 }, chartId: 1,
      data: at({
        total: 1000,
        // 服务端把请求的 50 行页收到 20（存量配置/越界请求），回显才是真相
        paging: { mode: 'offset', page_size: 20, page_num: 3, total: 1000, can_jump: true },
      }),
    }))
    expect(container.querySelectorAll('tbody tr')).toHaveLength(20)
    expect(screen.getByText('共 1000 行 · 第 3/50 页')).toBeInTheDocument()
  })

  it('翻页 = 带 page_size/page_num 重新取数（前端不递 offset）', () => {
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshSingleChart').mockResolvedValue(true)
    render(createElement(DashboardChart, {
      chartType: 'table_value', chartName: 'CASE 明细', dashboardId: 3, chartId: 7, config: { pageSize: 20 },
      data: at({
        total: 60,
        paging: { mode: 'offset', page_size: 20, page_num: 1, total: 60, can_jump: true },
      }),
    }))
    fireEvent.click(screen.getByTitle('下一页'))
    expect(refresh).toHaveBeenCalledWith(7, { dashboardId: 3, chartName: 'CASE 明细', page_size: 20, page_num: 2 })
    const sent = refresh.mock.calls[0][1] as Record<string, unknown>
    expect(Object.keys(sent).sort()).toEqual(['chartName', 'dashboardId', 'page_num', 'page_size'])
  })

  it('SQL 通道（can_jump）给页码按钮，点第 3 页直接跳', () => {
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshSingleChart').mockResolvedValue(true)
    render(createElement(DashboardChart, {
      chartType: 'table_value', dashboardId: 3, chartId: 7, config: { pageSize: 20 },
      data: at({
        total: 60,
        paging: { mode: 'offset', page_size: 20, page_num: 1, total: 60, can_jump: true },
      }),
    }))
    fireEvent.click(screen.getByRole('button', { name: '3' }))
    expect(refresh).toHaveBeenCalledWith(7, expect.objectContaining({ page_num: 3 }))
  })

  it('ES 游标通道（can_jump=false）不给页码，只给逐页往后翻', () => {
    render(createElement(DashboardChart, {
      chartType: 'table_value', dashboardId: 3, chartId: 7, config: { pageSize: 20 },
      data: at({
        total: 4460000,
        paging: { mode: 'cursor', page_size: 20, page_num: 2, total: 4460000, can_jump: false },
      }),
    }))
    // 跳页会被服务端拒（无 PIT，游标不连续）—— 给个点了必报错的按钮是空承诺
    expect(screen.queryByRole('button', { name: '3' })).toBeNull()
    expect(screen.getByTitle('该通道按游标顺序翻页，需逐页往后翻（不可直接跳页）')).toBeInTheDocument()
    expect(screen.getByText(`共 4460000 行 · 第 2/${Math.ceil(4460000 / 20)} 页`)).toBeInTheDocument()
  })

  it('服务端真分页时分页条不得被 enablePagination=false 关掉', () => {
    render(createElement(DashboardChart, {
      chartType: 'table_value', chartId: 7, config: { pageSize: 20, enablePagination: false },
      data: at({
        total: 4460000,
        paging: { mode: 'offset', page_size: 20, page_num: 1, total: 4460000, can_jump: true },
      }),
    }))
    // 关掉分页条 = 用户只看得首页且不知道后面还有多少页
    expect(screen.getByText(`共 4460000 行 · 第 1/${Math.ceil(4460000 / 20)} 页`)).toBeInTheDocument()
    expect(screen.getByTitle('下一页')).toBeInTheDocument()
  })

  it('行序不确定（正文无 ORDER BY）：显式说“翻页可能重行/漏行”', () => {
    render(createElement(DashboardChart, {
      chartType: 'table_value', chartId: 7, config: { pageSize: 20 },
      data: at({
        total: 5000,
        paging: { mode: 'offset', page_size: 20, page_num: 2, total: 5000, can_jump: true,
          stable_order: false },
      }),
    }))
    // 分页参数没错，错在集合本身没有可翻的顺序：不给提醒用户只会拿到两张一样的页
    expect(screen.getByRole('alert')).toHaveTextContent('翻页可能重复或漏行')
  })

  it('行序确定时不弹排序提醒（已写 ORDER BY 的数据集不该被打扰）', () => {
    render(createElement(DashboardChart, {
      chartType: 'table_value', chartId: 7, config: { pageSize: 20 },
      data: at({
        total: 5000,
        paging: { mode: 'cursor', page_size: 20, page_num: 2, total: 5000, can_jump: false,
          stable_order: true },
      }),
    }))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('未开分页且行数撞上限：显式说“已达上限”，不拿“共 N 行”冒充整张表', () => {
    const big = { columns: ['case_id'], rows: Array.from({ length: 1000 }, (_, i) => ({ case_id: i })), truncated: true }
    render(createElement(DashboardChart, { chartType: 'table_value', chartId: 7, config: {}, data: big }))
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('已达单次返回行数上限')
    expect(alert).toHaveTextContent('支持分页')
  })

  it('旧缓存没带分页事实：挂载即补一次首页取数（自愈）', () => {
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshSingleChart').mockResolvedValue(true)
    render(createElement(DashboardChart, {
      chartType: 'table_value', chartId: 9, dashboardId: 2, chartName: 'x', config: {}, data: paged(1, 20),
    }))
    expect(refresh).toHaveBeenCalledWith(9, expect.objectContaining({ page_size: 20, page_num: 1 }))
  })

  it('已带分页事实时挂载不再补查（不是每张图进看板都重查）', () => {
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshSingleChart').mockResolvedValue(true)
    render(createElement(DashboardChart, {
      chartType: 'table_value', chartId: 9, dashboardId: 2, config: {},
      data: at({ total: 20, paging: { mode: 'offset', page_size: 20, page_num: 1, total: 20, can_jump: true } }),
    }))
    expect(refresh).not.toHaveBeenCalled()
  })

  it('服务端声明 unpaged（本次即全量）时挂载不补查', () => {
    // 存量统计型表值图转圈的回归点：缓存里没有 paging 不等于“状态未知”，
    // 服务端已经说过这就是全部行，再补一次重查纯属浪费（且一慢就一直转）
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshSingleChart').mockResolvedValue(true)
    render(createElement(DashboardChart, {
      chartType: 'table_value', chartId: 9, dashboardId: 2, config: {},
      data: { columns: ['d', 'cnt'], rows: [{ d: '1.0.44', cnt: 1 }], unpaged: true },
    }))
    expect(refresh).not.toHaveBeenCalled()
  })

  it('撞过行限（truncated）也不补查：结果状态已知，缺的是分页配置不是重取', () => {
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshSingleChart').mockResolvedValue(true)
    render(createElement(DashboardChart, {
      chartType: 'table_value', chartId: 9, dashboardId: 2, config: {},
      data: { columns: ['d'], rows: [{ d: 1 }], truncated: true },
    }))
    expect(refresh).not.toHaveBeenCalled()
  })

  it('统计型图表（无 paging）保持整页渲染，不被套页限', () => {
    const { container } = render(createElement(DashboardChart, {
      chartType: 'table_value', chartId: 7, config: {},
      data: { columns: ['d', 'cnt'], rows: Array.from({ length: 5 }, (_, i) => ({ d: `2026-10-0${i + 1}`, cnt: i })), unpaged: true },
    }))
    expect(container.querySelectorAll('tbody tr')).toHaveLength(5)
    expect(screen.queryByText(/共 .* 页/)).toBeNull()
  })
})

// 字段组的视觉一半在 globals.css：组件只负责挂 class，样式若没落地就是又一个“存了不生效”
// （还会退回原生的三个白框 + 原生下拉箭头）。这里把 CSS 侧也锁住。
describe('日期区间控件样式落地', () => {
  // 与上方主题守卫同口径（vitest 的 cwd 就是 frontend，import.meta.url 不是 file: 协议）
  const css = readFileSync('src/styles/globals.css', 'utf8')

  it('字段组承接描边与聚焦环，内层不再各自成箱', () => {
    expect(css).toMatch(/\.dh-daterange-group\s*\{[^}]*border-color:\s*var\(--dh-dr-border/)
    expect(css).toMatch(/\.dh-daterange-group:focus-within\s*\{[\s\S]*?box-shadow/)
  })

  it('空值时分段透明 + 业务占位，聚焦时让位给编辑', () => {
    expect(css).toMatch(/\.dh-daterange-field\.is-empty > input:not\(:focus\)\s*\{\s*color:\s*transparent/)
    expect(css).toMatch(/\.dh-daterange-hint\s*\{[\s\S]*?pointer-events:\s*none/)
    expect(css).toMatch(/\.dh-daterange-field\.is-empty:focus-within \.dh-daterange-hint\s*\{\s*opacity:\s*0/)
  })

  it('新增规则能被 postcss 解析（自绘箭头的 data URI 写错会静默丢掉整条声明）', () => {
    const rules: Record<string, string> = {}
    postcss.parse(css).walkRules(rule => {
      if (rule.selector.includes('.dh-daterange')) rules[rule.selector] = rule.toString()
    })
    for (const key of ['.dh-daterange-group', '.dh-daterange-group:focus-within', '.dh-daterange-hint',
      '.dh-daterange-preset', '.dark .dh-daterange-preset',
      '.dh-daterange-field.is-empty > input:not(:focus)', '.dh-daterange-field.is-empty:focus-within .dh-daterange-hint'])
      expect(rules[key], `日期区间样式缺少规则：${key}`).toBeTruthy()
    expect(rules['.dh-daterange-preset']).toMatch(/background-image: url\("data:image\/svg\+xml/)
    expect(rules['.dark .dh-daterange-preset']).toMatch(/background-image: url\("data:image\/svg\+xml/)
    // 浅色主题的 --muted-foreground 偏深，占位必须带透明度才不会跟已填值一样重
    expect(rules['.dh-daterange-hint']).toMatch(/color: hsl\(var\(--muted-foreground\) \/ 0\.72\)/)
  })

  it('快捷下拉去掉原生外框与箭头，改自绘（深浅两套）', () => {
    expect(css).toMatch(/\.dh-daterange-preset\s*\{[\s\S]*?appearance:\s*none/)
    expect(css).toMatch(/\.dh-daterange-preset\s*\{[\s\S]*?background-image:\s*url\(/)
    expect(css).toMatch(/\.dark \.dh-daterange-preset\s*\{[\s\S]*?background-image/)
  })
})

// 历史缺陷：G2 报错只进 console，画布留一片空白，用户只能问“我的图呢”。
// 渲染失败必须在图上说出来（与 no-silent-degradation 对齐）。
describe('渲染失败必须可见', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('spec 构建报错（桑基缺目标列）时，画布上说出原因而不是静默空白', () => {
    // jsdom 不跑真实 rAF，与上方主题用例同口径：接住帧回调手动刷
    const frames = new Map<number, FrameRequestCallback>()
    let nextFrame = 0
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frames.set(++nextFrame, cb); return nextFrame })
    vi.stubGlobal('cancelAnimationFrame', (id: number) => { frames.delete(id) })
    // 尺寸量不到时不进入渲染帧（与上方主题用例同口径抹平）
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(400)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(300)

    render(createElement(DashboardChart, {
      chartType: 'sankey',
      data: { columns: ['源', '流量'], rows: [{ 源: '首页', 流量: 5 }] },
      config: { xCol: '源', yCol: '流量' },
      chartName: '页面访问流向',
    }))
    act(() => { const pending = [...frames.values()]; frames.clear(); pending.forEach(cb => cb(0)) })

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('页面访问流向')
    expect(alert).toHaveTextContent('渲染失败')
    expect(alert).toHaveTextContent('目标')
  })
})
