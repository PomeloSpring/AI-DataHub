import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useDashboardStore, type Dashboard, type DashboardChart } from '../dashboardStore'

// Mock the axios client
vi.mock('../../api/client', () => {
  return {
    default: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
  }
})

import client from '../../api/client'

// Helper to set up mock return values
function mockGet(data: any) {
  vi.mocked(client.get).mockResolvedValue({ data } as any)
}
function mockPost(data: any) {
  vi.mocked(client.post).mockResolvedValue({ data } as any)
}
function mockPut(data?: any) {
  vi.mocked(client.put).mockResolvedValue({ data } as any)
}
function mockDelete() {
  vi.mocked(client.delete).mockResolvedValue({} as any)
}

function makeChart(overrides: Partial<DashboardChart> = {}): DashboardChart {
  return {
    id: 1,
    dashboard_id: 1,
    name: 'Test Chart',
    chart_type: 'bar',
    sql_query: 'SELECT 1',
    config: {},
    position: { x: 0, y: 0, w: 400, h: 300 },
    source_type: 'query',
    source_id: null,
    data_cache: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

function makeDashboard(overrides: Partial<Dashboard> = {}): Dashboard {
  return {
    id: 1,
    name: 'Test Dashboard',
    description: '',
    layout: [],
    filters: {},
    params: [],
    page_params: [],
    status: 'designing',
    owner_id: 1,
    is_public: false,
    is_default: false,
    carousel_interval: 10,
    sort_order: 0,
    charts: [],
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('dashboardStore', () => {
  beforeEach(() => {
    useDashboardStore.setState({
      dashboards: [],
      currentId: null,
      loading: false,
      globalFilters: {},
      crossFilters: [],
      favorites: [],
      paramValues: {},
      pageParams: [],
      pageParamValues: {},
      refreshing: false,
      refreshingChartIds: new Set(),
    })
    vi.mocked(client.get).mockReset()
    vi.mocked(client.post).mockReset()
    vi.mocked(client.put).mockReset()
    vi.mocked(client.delete).mockReset()
    window.localStorage.clear()
  })

  describe('loadDashboards', () => {
    it('loads dashboards and sets default', async () => {
      const dashboards = [
        makeDashboard({ id: 1, name: 'A' }),
        makeDashboard({ id: 2, name: 'B', is_default: true }),
      ]
      mockGet(dashboards)

      await useDashboardStore.getState().loadDashboards()
      const state = useDashboardStore.getState()
      expect(state.dashboards).toEqual(dashboards)
      expect(state.currentId).toBe(2)
      expect(state.loading).toBe(false)
    })

    it('sets first dashboard as current when no default', async () => {
      const dashboards = [
        makeDashboard({ id: 1, name: 'A' }),
        makeDashboard({ id: 2, name: 'B' }),
      ]
      mockGet(dashboards)
      useDashboardStore.setState({ currentId: null })

      await useDashboardStore.getState().loadDashboards()
      expect(useDashboardStore.getState().currentId).toBe(1)
    })

    it('preserves existing currentId', async () => {
      const dashboards = [makeDashboard({ id: 1 }), makeDashboard({ id: 2 })]
      mockGet(dashboards)
      useDashboardStore.setState({ currentId: 2 })

      await useDashboardStore.getState().loadDashboards()
      expect(useDashboardStore.getState().currentId).toBe(2)
    })

    it('handles API error gracefully', async () => {
      vi.mocked(client.get).mockRejectedValue(new Error('Network error'))

      await useDashboardStore.getState().loadDashboards()
      expect(useDashboardStore.getState().dashboards).toEqual([])
      expect(useDashboardStore.getState().loading).toBe(false)
    })
  })

  describe('setCurrent', () => {
    it('sets currentId and initializes page params', () => {
      const dashboards = [
        makeDashboard({ id: 1, page_params: [{ name: 'p1', type: 'string', default: 'v1', label: 'P1' }] }),
        makeDashboard({ id: 2 }),
      ]
      useDashboardStore.setState({ dashboards })

      useDashboardStore.getState().setCurrent(1)
      const state = useDashboardStore.getState()
      expect(state.currentId).toBe(1)
      expect(state.pageParams).toEqual([{ name: 'p1', type: 'string', default: 'v1', label: 'P1' }])
      expect(state.pageParamValues).toEqual({ p1: 'v1' })
    })

    it('initializes page param values from defaults', () => {
      const dashboards = [
        makeDashboard({
          id: 1,
          page_params: [
            { name: 'start', type: 'date', default: '2024-01-01', label: 'Start' },
            { name: 'end', type: 'date', default: '2024-12-31', label: 'End' },
          ],
        }),
      ]
      useDashboardStore.setState({ dashboards })

      useDashboardStore.getState().setCurrent(1)
      expect(useDashboardStore.getState().pageParamValues).toEqual({
        start: '2024-01-01',
        end: '2024-12-31',
      })
    })
  })

  describe('toggleFavorite', () => {
    it('adds dashboard to favorites', () => {
      useDashboardStore.setState({ favorites: [] })
      useDashboardStore.getState().toggleFavorite(1)
      expect(useDashboardStore.getState().favorites).toEqual([1])
    })

    it('removes dashboard from favorites', () => {
      useDashboardStore.setState({ favorites: [1, 2, 3] })
      useDashboardStore.getState().toggleFavorite(2)
      expect(useDashboardStore.getState().favorites).toEqual([1, 3])
    })

    it('persists favorites to localStorage', () => {
      useDashboardStore.getState().toggleFavorite(5)
      const saved = JSON.parse(window.localStorage.getItem('dashboard_favorites') || '[]')
      expect(saved).toContain(5)
    })
  })

  describe('setParamValue', () => {
    it('updates a param value', () => {
      useDashboardStore.setState({ paramValues: { site: 'old' } })
      useDashboardStore.getState().setParamValue('site', 'new')
      expect(useDashboardStore.getState().paramValues.site).toBe('new')
    })

    it('adds a new param', () => {
      useDashboardStore.setState({ paramValues: {} })
      useDashboardStore.getState().setParamValue('date', '2024-01-01')
      expect(useDashboardStore.getState().paramValues).toEqual({ date: '2024-01-01' })
    })

    it('preserves other params', () => {
      useDashboardStore.setState({ paramValues: { a: 1, b: 2 } })
      useDashboardStore.getState().setParamValue('b', 99)
      expect(useDashboardStore.getState().paramValues).toEqual({ a: 1, b: 99 })
    })
  })

  describe('setPageParamValue', () => {
    it('updates page param value', () => {
      useDashboardStore.setState({ pageParamValues: { start: '2024-01-01' } })
      useDashboardStore.getState().setPageParamValue('start', '2024-06-01')
      expect(useDashboardStore.getState().pageParamValues.start).toBe('2024-06-01')
    })
  })

  describe('setGlobalFilters', () => {
    it('replaces all global filters', () => {
      useDashboardStore.setState({ globalFilters: { old: 'value' } })
      useDashboardStore.getState().setGlobalFilters({ new: 'filter' })
      expect(useDashboardStore.getState().globalFilters).toEqual({ new: 'filter' })
    })
  })

  describe('CRUD operations', () => {
    it('createDashboard creates and reloads', async () => {
      mockPost({ id: 99 })
      mockGet([makeDashboard({ id: 99, name: 'New' })])

      const id = await useDashboardStore.getState().createDashboard('New')
      expect(id).toBe(99)
      expect(vi.mocked(client.post)).toHaveBeenCalledWith('/dashboard/', { name: 'New' })
    })

    it('deleteDashboard removes and updates currentId', async () => {
      mockDelete()
      mockGet([makeDashboard({ id: 2 })])
      useDashboardStore.setState({
        dashboards: [makeDashboard({ id: 1 }), makeDashboard({ id: 2 })],
        currentId: 1,
      })

      await useDashboardStore.getState().deleteDashboard(1)
      expect(vi.mocked(client.delete)).toHaveBeenCalledWith('/dashboard/1')
    })

    it('updateDashboard calls API and reloads', async () => {
      mockPut()
      mockGet([])

      await useDashboardStore.getState().updateDashboard(1, { name: 'Updated' })
      expect(vi.mocked(client.put)).toHaveBeenCalledWith('/dashboard/1', { name: 'Updated' })
    })
  })

  describe('refreshSingleChart', () => {
    it('refreshes chart and updates data_cache', async () => {
      const chart = makeChart({ id: 1, sql_query: 'SELECT 1' })
      const dashboard = makeDashboard({ id: 10, charts: [chart] })
      useDashboardStore.setState({ dashboards: [dashboard], currentId: 10 })

      mockPost({ columns: ['a', 'b'], rows: [{ a: 1, b: 2 }] })

      await useDashboardStore.getState().refreshSingleChart(1)
      expect(vi.mocked(client.post)).toHaveBeenCalledWith('/dashboard/10/charts/1/refresh', expect.objectContaining({ params: {} }))

      const state = useDashboardStore.getState()
      const updatedChart = state.dashboards[0].charts[0]
      const cache = JSON.parse(updatedChart.data_cache!)
      expect(cache.columns).toEqual(['a', 'b'])
      expect(cache.rows).toEqual([{ a: 1, b: 2 }])
    })

    it('tracks refreshingChartIds', async () => {
      const chart = makeChart({ id: 1 })
      const dashboard = makeDashboard({ id: 10, charts: [chart] })
      useDashboardStore.setState({ dashboards: [dashboard], currentId: 10 })

      let resolvePromise: (v: any) => void
      const pendingPromise = new Promise(r => { resolvePromise = r })
      vi.mocked(client.post).mockReturnValue(pendingPromise as any)

      const refreshPromise = useDashboardStore.getState().refreshSingleChart(1)
      expect(useDashboardStore.getState().refreshingChartIds.has(1)).toBe(true)

      resolvePromise!({ data: { columns: [], rows: [] } })
      await refreshPromise

      expect(useDashboardStore.getState().refreshingChartIds.has(1)).toBe(false)
    })

    it('handles refresh error', async () => {
      const chart = makeChart({ id: 1 })
      const dashboard = makeDashboard({ id: 10, charts: [chart] })
      useDashboardStore.setState({ dashboards: [dashboard], currentId: 10 })

      vi.mocked(client.post).mockRejectedValue(new Error('SQL error'))

      await useDashboardStore.getState().refreshSingleChart(1)
      expect(useDashboardStore.getState().dashboards[0].charts[0].data_cache).toBeNull()
    })
  })

  describe('setPageParams', () => {
    it('sets page params and initializes values', () => {
      useDashboardStore.setState({ pageParamValues: {} })
      useDashboardStore.getState().setPageParams([
        { name: 'a', type: 'string', default: '1', label: 'A' },
        { name: 'b', type: 'number', default: '2', label: 'B' },
      ])
      const state = useDashboardStore.getState()
      expect(state.pageParams).toHaveLength(2)
      expect(state.pageParamValues).toEqual({ a: '1', b: '2' })
    })

    it('preserves existing values', () => {
      useDashboardStore.setState({ pageParamValues: { a: 'existing' } })
      useDashboardStore.getState().setPageParams([
        { name: 'a', type: 'string', default: 'default', label: 'A' },
        { name: 'b', type: 'string', default: 'new', label: 'B' },
      ])
      expect(useDashboardStore.getState().pageParamValues).toEqual({ a: 'existing', b: 'new' })
    })
  })

  describe('saveLayout', () => {
    it('updates chart positions in store', async () => {
      const charts = [
        makeChart({ id: 1, position: { x: 0, y: 0, w: 400, h: 300 } }),
        makeChart({ id: 2, position: { x: 0, y: 0, w: 400, h: 300 } }),
      ]
      const dashboard = makeDashboard({ id: 10, charts })
      useDashboardStore.setState({ dashboards: [dashboard] })

      mockPut()

      await useDashboardStore.getState().saveLayout(10, [
        { chart_id: 1, position: { x: 100, y: 200, w: 500, h: 400 } },
        { chart_id: 2, position: { x: 0, y: 600, w: 300, h: 200 } },
      ])

      const updatedCharts = useDashboardStore.getState().dashboards[0].charts
      expect(updatedCharts[0].position).toEqual({ x: 100, y: 200, w: 500, h: 400 })
      expect(updatedCharts[1].position).toEqual({ x: 0, y: 600, w: 300, h: 200 })
    })
  })
})
