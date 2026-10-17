import { describe, it, expect } from 'vitest'
import { resolveChartType } from '../ChartPicker'
import { CHART_TYPES } from '../DashboardChart'

describe('Chat 图表类型解析(与看板 CHART_TYPES 对齐)', () => {
  it('看板全部可渲染图表类型在 Chat 中直接生效(参数控件除外)', () => {
    const pickable = CHART_TYPES.filter(t => t.category !== 'widget')
    for (const t of pickable) {
      expect(resolveChartType(t.value)).toBe(t.value)
    }
  })

  it('雷达图/词云/地图等此前 Chat 缺失的类型已支持', () => {
    expect(resolveChartType('radar')).toBe('radar')
    expect(resolveChartType('word_cloud')).toBe('word_cloud')
    expect(resolveChartType('china_map')).toBe('china_map')
    expect(resolveChartType('gauge')).toBe('gauge')
    expect(resolveChartType('rose')).toBe('rose')
  })

  it('未知类型与历史别名安全退回柱状图', () => {
    expect(resolveChartType('chord')).toBe('bar')       // 看板从未渲染过 chord
    expect(resolveChartType('bogus')).toBe('bar')
    expect(resolveChartType(undefined)).toBe('bar')
    expect(resolveChartType('table')).toBe('bar')      // 历史契约 table → 柱状图(原行为)
    expect(resolveChartType('column')).toBe('bar')     // 旧别名
  })

  it('地图热力(map_heat)及 LLM 常用别名解析到地图热力', () => {
    expect(resolveChartType('map_heat')).toBe('map_heat')
    expect(resolveChartType('map')).toBe('map_heat')
    expect(resolveChartType('heatmap_map')).toBe('map_heat')
    expect(resolveChartType('geo_heatmap')).toBe('map_heat')
    expect(resolveChartType('map_heatmap')).toBe('map_heat')
    // 矩阵热力图(heatmap)是另一类型,不得被吸进地图热力
    expect(resolveChartType('heatmap')).toBe('heatmap')
  })
})
