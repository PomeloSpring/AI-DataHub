import { describe, it, expect } from 'vitest'
import {
  detectGroupColumn,
  isTimeColumn,
  parseTime,
  aggregateTimeData,
  groupDataByColumn,
  buildG2Spec,
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
    expect(spec.data.value).toBe(75)
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

  it('builds sankey chart spec', () => {
    const sankeyRows = [
      { source: 'A', target: 'B', value: 10 },
      { source: 'A', target: 'C', value: 20 },
    ]
    const spec = buildG2Spec('sankey', ['source', 'target', 'value'], sankeyRows, {}, false, 400, 300)
    expect(spec.type).toBe('view')
    expect(spec.children[0].type).toBe('sankey')
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
    // Should have total (330) and trend
    expect(spec.children[0].style.text).toContain('330')
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
