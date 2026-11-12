// 「用最新数据重新生成」的版式结局要在页面上看得见（用户反馈：数据是新的，但不是原本报告的样式）。
// 口径：服务端产出时判 layout_result（版式）与 evidence_verdict（证据），本页只回显：
// 版本下拉能看出哪一版没沿原版式，正文上方给原因与出路（切回上一版）；
// 证据成立就是 ready，不再一律挂“降级”（旧版全仓写死 degraded，那句提醒没人当真）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ReportsCenter from '../ReportsCenter'

const TITLE = '全国页面访问统计'
const MAP_BODY = '## 核心发现\n\n```chart\n{"chart_type":"china_map"}\n```\n'
const FACT_BODY = '# 全国页面访问统计\n\n## 分析项 1\n\n| 国家 | 页面浏览量 |\n'

const v1 = {
  id: 843, task_id: null, log_id: null, title: TITLE, format: 'markdown',
  access_mode: 'private', view_count: 3, created_at: '2026-11-10T16:49:19',
  generation_status: 'ready', source_type: 'conversation', workspace_id: 3,
  series_key: 'report:843',
  origin: { channel: 'chat', question: '查访问 ip 归属地分布' },
  refresh: { available: true, channel: 'report', hint: '' },
}
// 可切换的重跑结局：用例改这里，详情按调用时刻取值（mock 工厂在导入期求值，不能捕获常量）
const rerun = {
  layout: undefined as undefined | Record<string, unknown>,
  body: FACT_BODY,
  // 事实表那一版也是已鉴权取数的产物 → ready；截断则另说样本口径
  evidence: { validation_status: 'governed', query_count: 2,
              sources: ['run_semantic_query'], completeness: 'sample_only', reasons: [] } as Record<string, unknown>,
  status: 'ready',
}
const v2 = {
  ...v1, id: 844, source_type: 'analysis',
  view_count: 0, created_at: '2026-11-10T17:12:46',
}

vi.mock('@/api/report', () => ({
  listReports: vi.fn(() => Promise.resolve([{ ...v2, layout: rerun.layout }, v1])),
  getReportDetail: vi.fn((id: number) => Promise.resolve(
    id === v1.id ? { ...v1, content_url: '/c/843', content_size: 1 }
      : { ...v2, generation_status: rerun.status, layout: rerun.layout,
          evidence_summary: rerun.evidence, content_url: '/c/844', content_size: 1 })),
  fetchReportContent: vi.fn((url: string) =>
    Promise.resolve(url.endsWith('843') ? MAP_BODY : rerun.body)),
  updateReport: vi.fn(), deleteReport: vi.fn(),
  // 重跑改成同一条 SSE 连接里直播（见 api/report.ts#refreshReportStream）
  refreshReportStream: vi.fn(() => Promise.resolve()),
  // 报告模板的使用面（套用/沉淀）不在本用例关注范围，给空候选保证页面能渲染
  listReportTemplates: vi.fn(() => Promise.resolve([])),
  fetchReportTemplateSkeleton: vi.fn(), saveReportAsTemplate: vi.fn(),
  isEditableReport: (r: any) => ['manual', 'conversation'].includes(r.source_type),
  isDeletableReport: (r: any) => ['manual', 'conversation', 'legacy', 'analysis'].includes(r.source_type),
}))
vi.mock('@/api/scheduledTask', () => ({ triggerScheduledTask: vi.fn() }))
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/components/ReportContent', () => ({
  default: ({ content }: { content: string }) => <div data-testid="body">{content}</div>,
}))
vi.mock('@/components/ReportTaskDialog', () => ({ default: () => null }))

const picker = async () => (await waitFor(() => screen.getByTitle('版本管理'))) as HTMLSelectElement

describe('重跑版本的版式结局可见', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    rerun.layout = { has_layout: true, status: 'fact_only', format: 'markdown',
                     reasons: ['缺少图表：china_map（省份/页面浏览量）'] }
    rerun.body = FACT_BODY
    rerun.status = 'ready'
    rerun.evidence = { validation_status: 'governed', query_count: 2,
                       completeness: 'sample_only', reasons: [] }
  })
  afterEach(() => { cleanup() })

  it('没沿原版式：版本下拉标出来，正文上方给原因与出路', async () => {
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    const select = await picker()
    expect(select.options[0].textContent).toContain('未按原版式')
    expect(select.options[1].textContent).not.toContain('未按原版式')

    const note = await waitFor(() => screen.getByRole('note'))
    expect(note).toHaveTextContent('未能沿用上一版报告的版式')
    expect(note).toHaveTextContent('china_map')
    expect(note).toHaveTextContent('切回上一版')
    // 证据口径按服务端判定说：截断了就说样本，不得再印那句“尚未完成证据校验、不可对外发布”
    expect(screen.getByTestId('evidence-note')).toHaveTextContent('仅代表样本')
    expect(screen.getByTestId('evidence-note')).not.toHaveTextContent('不可对外发布')
  })

  it('沿用了原版式且证据未截断：不给警告也不占一行', async () => {
    rerun.layout = { has_layout: true, status: 'preserved', format: 'markdown', reasons: [] }
    rerun.body = MAP_BODY
    rerun.evidence = { validation_status: 'governed', query_count: 1,
                       completeness: 'as_queried', reasons: [] }
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    const select = await picker()
    expect(select.options[0].textContent).not.toContain('未按原版式')
    await waitFor(() => expect(screen.getByTestId('body')).toHaveTextContent('china_map'))
    expect(screen.queryByRole('note')).toBeNull()
    expect(screen.queryByTestId('evidence-note')).toBeNull()
    // 旧版那句“降级事实报告”徽章不得再出现在就绪的报告上
    expect(screen.queryByText('降级事实报告')).toBeNull()
  })

  it('证据不成立才 degraded：原因逐条上页面', async () => {
    rerun.layout = { has_layout: true, status: 'preserved', format: 'markdown', reasons: [] }
    rerun.body = MAP_BODY
    rerun.status = 'degraded'
    rerun.evidence = { validation_status: 'unverified', reasons: ['本轮没有已登记的受治理取数结果'] }
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('body')).toHaveTextContent('china_map'))
    expect(screen.getByText('证据不全')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-note')).toHaveTextContent('受治理取数结果')
  })

  it('切回原报告那一版：不再出现重跑版本的版式警告', async () => {
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    const select = await picker()
    fireEvent.change(select, { target: { value: String(v1.id) } })
    await waitFor(() => expect(screen.getByTestId('body')).toHaveTextContent('china_map'))
    expect(screen.queryByRole('note')).toBeNull()
  })
})
