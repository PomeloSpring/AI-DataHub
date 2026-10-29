import { describe, it, expect, beforeEach, vi } from 'vitest'

// graphStore 直接 import axios（非封装 client），故按 axios mock 捕获请求 URL。
vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import axios from 'axios'
import { useGraphStore } from '../graphStore'

// 取最近一次 axios.get 的请求 URL
function lastGetUrl(): string {
  const calls = vi.mocked(axios.get).mock.calls
  expect(calls.length).toBeGreaterThan(0)
  return calls[calls.length - 1][0] as string
}

describe('graphStore.fetchGraphData — model_id 透传', () => {
  beforeEach(() => {
    vi.mocked(axios.get).mockReset()
    vi.mocked(axios.get).mockResolvedValue({
      data: { nodes: [], edges: [], stats: { node_count: 0, relationship_count: 0, labels: [], connected: true } },
    } as any)
    useGraphStore.setState({ graphData: null, isLoading: false, error: null })
  })

  it('传 modelId 时 query 带 model_id', async () => {
    await useGraphStore.getState().fetchGraphData({ graphType: 'unified', datasourceId: 0, modelId: 123 })
    const url = lastGetUrl()
    expect(url).toContain('model_id=123')
  })

  it('不传 modelId 时 query 不含 model_id（回退全图）', async () => {
    await useGraphStore.getState().fetchGraphData({ graphType: 'unified', datasourceId: 0 })
    const url = lastGetUrl()
    expect(url).not.toContain('model_id')
  })

  it('modelId 与其它过滤参数并存', async () => {
    await useGraphStore
      .getState()
      .fetchGraphData({ graphType: 'unified', datasourceId: 7, modelId: 456, search: '订单' })
    const url = lastGetUrl()
    expect(url).toContain('model_id=456')
    expect(url).toContain('graph_type=unified')
    expect(url).toContain('datasource_id=7')
    expect(url).toContain('search=')
  })
})
