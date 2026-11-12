// 报告历史版本归集与失败原因（用户反馈缺陷）：
// 「用最新数据重新生成」的产物必须落在**原报告卡片**的历史版本里，而不是另开一张同名卡片；
// 失败的那一版要说清“本次生成未完成”，切换回有正文的版本要真的渲染正文，
// 不得把四种读不到的原因（排队/失败/超时/权限快照变化）压成一句“权限已变化”。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ReportsCenter from '../ReportsCenter'

const TITLE = '全国页面访问统计'
const BODY_V1 = '各站分别统计：中国站 1,234 次访问'

/** v1 = 会话保存的原报告（有正文）；v2 = 重跑产物（生成失败、无正文），同一 series_key */
const v1 = {
  id: 843, task_id: null, log_id: null, title: TITLE, format: 'markdown',
  access_mode: 'private', view_count: 3, created_at: '2026-11-10T16:49:19',
  generation_status: 'ready', source_type: 'conversation', workspace_id: 3,
  series_key: 'report:843',
  origin: { channel: 'chat', conversation_id: 146, question: '查访问 ip 归属地分布' },
  refresh: { available: true, channel: 'report', hint: '' },
}
const v2 = {
  ...v1, id: 844, source_type: 'analysis', created_at: '2026-11-10T16:50:10',
  generation_status: 'failed', series_key: 'report:843', view_count: 0,
  refresh: { available: true, channel: 'report', hint: '' },
}
const FAILED_HINT = '本次生成未完成：分析模型（Harness）未完成本轮运行，未拿到分析终稿。'
  + '可点「用最新数据重新生成」重试；切换历史版本仍可查看之前生成的正文'

const detailFor = (r: typeof v1) => r.generation_status === 'ready'
  ? { ...r, content_url: `https://oss.test/reports/${r.id}`, content_size: BODY_V1.length }
  : { ...r, content_url: '', content_size: 0, access_warning: FAILED_HINT }

vi.mock('@/api/report', () => {
  const EDITABLE = ['manual', 'conversation']
  return {
    listReports: vi.fn(() => Promise.resolve([v2, v1])),       // 清单按时间倒序
    getReportDetail: vi.fn((id: number) =>
      Promise.resolve(detailFor(id === v1.id ? v1 : v2))),
    fetchReportContent: vi.fn(() => Promise.resolve(BODY_V1)),
    updateReport: vi.fn(), deleteReport: vi.fn(),
    // 重跑 = 直播：桩按服务端事件形状发「已建好新版本」+「这一版没跑成」
    refreshReportStream: vi.fn(async (_id: number, handlers: any) => {
      handlers.onEvent({ type: 'stage', step: 'prepared', report_id: v2.id,
                         message: '已在本报告下建好新版本' });
      handlers.onEvent({ type: 'done', generation_status: 'failed',
                         stage_error_code: 'LLM_UNAVAILABLE', reason: FAILED_HINT });
    }),
    createTaskFromReport: vi.fn(),
    // 模板使用面不在本用例关注范围：给空候选，页面渲染不依赖真接口
    listReportTemplates: vi.fn(() => Promise.resolve([])),
    fetchReportTemplateSkeleton: vi.fn(), saveReportAsTemplate: vi.fn(),
    isEditableReport: (r: any) => EDITABLE.includes(r.source_type),
    isDeletableReport: (r: any) => ['manual', 'conversation', 'legacy', 'analysis'].includes(r.source_type),
  }
})
vi.mock('@/api/scheduledTask', () => ({ triggerScheduledTask: vi.fn() }))
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/components/ReportContent', () => ({
  default: ({ content }: { content: string }) => <div data-testid="body">{content}</div>,
}))
vi.mock('@/components/ReportTaskDialog', () => ({ default: () => null }))

describe('报告历史版本归集与失败原因', () => {
  beforeEach(() => { vi.clearAllMocks() })
  afterEach(() => { cleanup() })

  it('重跑产物与原报告是同一张卡片，卡片身份取原始报告（会话保存、可删）', async () => {
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    const cards = await waitFor(() => screen.findAllByText(TITLE))
    // 左栏卡片只有一张（另有详情头栏标题），且标出两个版本
    expect(cards.length).toBeLessThanOrEqual(2)
    expect(screen.getByText('2 个版本')).toBeInTheDocument()
    const list = document.querySelector('.w-72')
    // 卡片身份取最早一版（会话保存）：最新一版是分析产物也不能把整张卡改标为「分析生成」
    expect(list?.textContent).toContain('会话保存')
    expect(list?.textContent).not.toContain('分析生成')
    // 会话保存可编辑 + 可删除 → 名称/编辑/删除三个按钮（分析生成不可删的旧行为已修）
    expect(list?.querySelectorAll('button').length).toBe(3)
    expect(list?.querySelector('[title="删除报告"]')).toBeInTheDocument()
  })

  it('失败版本在版本下拉里可见，切回 v1 渲染 v1 正文', async () => {
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    const picker = await waitFor(() => screen.getByTitle('版本管理')) as HTMLSelectElement
    expect(picker.options).toHaveLength(2)
    // 版本名带上生成状态：只看时间分不清哪一版有正文
    expect(picker.options[0].textContent).toContain('生成失败')
    expect(picker.options[1].textContent).not.toContain('生成失败')

    // 默认选中最新一版：显式暴露失败原因（不是“权限已变化”那种误导文案）
    fireEvent.change(picker, { target: { value: String(v2.id) } })
    const loud = await waitFor(() => screen.getByText(/本次生成未完成/))
    expect(loud).toBeInTheDocument()
    expect(screen.queryByText(/权限已变化/)).toBeNull()

    // 切到 v1：必须渲染 v1 正文，错误文案随版本切换清掉
    fireEvent.change(picker, { target: { value: String(v1.id) } })
    await waitFor(() => expect(screen.getByTestId('body')).toHaveTextContent(BODY_V1))
    expect(screen.queryByText(/本次生成未完成/)).toBeNull()
  })

  it('重新生成后停留在原报告卡片，只把选中版本切到最新一版', async () => {
    const { refreshReportStream } = await import('@/api/report')
    // 先落在 v1（有正文），再点「用最新数据重新生成」
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>)
    const picker = await waitFor(() => screen.getByTitle('版本管理')) as HTMLSelectElement
    fireEvent.change(picker, { target: { value: String(v1.id) } })
    await waitFor(() => expect(screen.getByTestId('body')).toHaveTextContent(BODY_V1))

    fireEvent.click(screen.getByRole('button', { name: /用最新数据重新生成/ }))
    // 直播入口只带报告号与中止柄：问题正文/数据源/身份都不由前端传（护栏 §2）
    await waitFor(() => expect(refreshReportStream).toHaveBeenCalledWith(
      v1.id, expect.objectContaining({ signal: expect.any(AbortSignal) })))
    // 卡片键仍是原报告（不再跳到按标题拼出来的另一张卡）
    await waitFor(() => expect(
      (screen.getByTitle('版本管理') as HTMLSelectElement).value).toBe(String(v2.id)))
    expect(document.querySelectorAll('.w-72 button')).toHaveLength(3)
  })
})
