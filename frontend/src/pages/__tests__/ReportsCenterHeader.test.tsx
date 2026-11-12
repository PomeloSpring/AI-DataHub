// 报告详情头栏的信息密度：标题只出现一次，会话来源不再抄第二遍「标题」。
//
// 历史现象（用户反馈）：头栏在报告标题下方又渲染了会话标题（origin.conversation_title），
// 而会话标题本身就是那句提问的复述，与报告标题讲的是同一件事 → 看起来像标题写了两遍。
// 现在来源仍要可辨识（「会话保存」徽章），具体会话号收进徽章 title 供排查，不占一行。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ReportsCenter from '../ReportsCenter'

const CONVERSATION_TITLE = '查看最近7天的案例数量'
const REPORT_TITLE = '最近7天的案例数据分布'

const summary = {
  id: 843, task_id: null, log_id: null, title: REPORT_TITLE, format: 'markdown',
  access_mode: 'private', view_count: 18, created_at: '2026-10-16T10:10:15',
  generation_status: 'ready', source_type: 'conversation', workspace_id: 3,
  origin: { channel: 'chat', conversation_id: 146, conversation_title: CONVERSATION_TITLE,
            question: CONVERSATION_TITLE },
  refresh: { available: true, channel: 'report', hint: '' },
}
// 详情只回元信息 + 正文直链；正文本身由 fetchReportContent 走直链取回
const BODY_TEXT = '核心发现：总案例数 61,534 个'
const detail = { ...summary, content_url: 'https://oss.test/reports/843', content_size: BODY_TEXT.length }

vi.mock('@/api/report', () => ({
  listReports: vi.fn(() => Promise.resolve([summary])),
  getReportDetail: vi.fn(() => Promise.resolve(detail)),
  fetchReportContent: vi.fn(() => Promise.resolve(BODY_TEXT)),
  updateReport: vi.fn(), deleteReport: vi.fn(),
  refreshReportStream: vi.fn(() => Promise.resolve()), createTaskFromReport: vi.fn(),
  // 模板使用面不在本用例关注范围：给空候选，页面渲染不依赖真接口
  listReportTemplates: vi.fn(() => Promise.resolve([])),
  fetchReportTemplateSkeleton: vi.fn(), saveReportAsTemplate: vi.fn(),
  isEditableReport: () => true, isDeletableReport: () => true,
}))
vi.mock('@/api/scheduledTask', () => ({ triggerScheduledTask: vi.fn() }))
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
// 正文渲染另有组件负责（含图表），这里只留文本，避免把 markdown/echarts 拖进本用例
vi.mock('@/components/ReportContent', () => ({
  default: ({ content }: { content: string }) => <div data-testid="body">{content}</div>,
}))
vi.mock('@/components/ReportTaskDialog', () => ({ default: () => null }))

describe('报告中心详情头栏', () => {
  beforeEach(() => { vi.clearAllMocks() })
  afterEach(() => { cleanup() })

  it('会话标题不再与报告标题并排重复显示', async () => {
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    // 头栏只有一份报告标题（左侧清单条目是导航，不计入重复）
    await waitFor(() => expect(screen.getByRole('heading', { name: REPORT_TITLE })).toBeInTheDocument())
    expect(screen.queryByText(CONVERSATION_TITLE)).toBeNull()
    expect(screen.queryByText(/来自会话/)).toBeNull()
  })

  it('来源仍可辨识：会话保存徽章 + 会话号收在 title 里', async () => {
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    const badges = await waitFor(() => {
      const found = screen.getAllByText('会话保存')
      expect(found.some(el => (el.getAttribute('title') || '').includes('#146'))).toBe(true)
      return found
    })
    expect(badges.length).toBeGreaterThan(0)
  })

  it('正文按直链取回的内容原样渲染（不再含提问行）', async () => {
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    const body = await waitFor(() => screen.getByTestId('body'))
    expect(body.textContent).toBe(BODY_TEXT)
    expect(body.textContent).not.toContain('提问')
  })
})
