// 报告中心：工作空间筛选器已移除 + 正文走对象存储直链 + 导出入口。
//
// 三条各自锁住一个容易悄悄回退的点：
// 1. 创建工作空间暂未开放，而报告跟随属主跨空间 —— 空间筛选器给不出任何收窄效果，
//    留着就是"配了也没用的开关"（与菜单权限规范里的死开关同一判据）。
// 2. 详情只回 content_url，正文由浏览器直连对象存储取；取不到必须显式暴露原因，
//    不能渲染成空白页（那会被当成"报告没内容"，而真因可能是直链过期或桶没开跨域）。
// 3. 导出取的是当前屏幕上这一份正文；正文没加载完就不给可用的导出。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ReportsCenter from '../ReportsCenter'
import { listReports, fetchReportContent } from '@/api/report'

const summary = {
  id: 900, task_id: null, log_id: null, title: '新加坡近30天新增案例', format: 'markdown',
  access_mode: 'private', view_count: 3, created_at: '2026-11-02T09:00:00',
  generation_status: 'ready', source_type: 'conversation', workspace_id: 3,
  origin: { channel: 'chat', conversation_id: 146, question: '新加坡近30天新增案例' },
  refresh: { available: true, channel: 'report', hint: '' },
}
const detail = {
  ...summary,
  content_url: 'https://bucket.oss-test.example.com/ai-datahub/7/reports/900?X-Amz-Signature=x',
  content_size: 30,
}
const BODY = '# 新加坡近30天\n\n共 128 个新增案例。'

vi.mock('@/api/report', () => ({
  listReports: vi.fn(() => Promise.resolve([summary])),
  getReportDetail: vi.fn(() => Promise.resolve({
    ...summary,
    content_url: 'https://bucket.oss-test.example.com/ai-datahub/7/reports/900?X-Amz-Signature=x',
    content_size: 30,
  })),
  fetchReportContent: vi.fn(() => Promise.resolve('# 新加坡近30天\n\n共 128 个新增案例。')),
  updateReport: vi.fn(), deleteReport: vi.fn(),
  refreshReportStream: vi.fn(() => Promise.resolve()), createTaskFromReport: vi.fn(),
  // 模板使用面不在本用例关注范围：给空候选，页面渲染不依赖真接口
  listReportTemplates: vi.fn(() => Promise.resolve([])),
  fetchReportTemplateSkeleton: vi.fn(), saveReportAsTemplate: vi.fn(),
  isEditableReport: () => true, isDeletableReport: () => true,
  EDITABLE_SOURCES: ['manual', 'conversation'], DELETABLE_SOURCES: ['manual', 'conversation', 'legacy', 'analysis'],
}))
vi.mock('@/api/scheduledTask', () => ({ triggerScheduledTask: vi.fn() }))
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/components/ReportTaskDialog', () => ({ default: () => null }))

const renderPage = () => render(
  <MemoryRouter initialEntries={['/ws/3/reports']}><ReportsCenter /></MemoryRouter>)

beforeEach(() => { vi.clearAllMocks(); vi.mocked(listReports).mockResolvedValue([summary] as never) })
afterEach(() => { cleanup() })

describe('工作空间筛选器已移除', () => {
  it('筛选行不再出现工作空间范围选项', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('新加坡近30天新增案例')).toBeInTheDocument())
    expect(screen.queryByText('全部工作空间')).toBeNull()
    expect(screen.queryByText('本工作空间')).toBeNull()
    // 来源筛选还在（它是真实可用的收窄维度）
    expect(screen.getByText('全部来源')).toBeInTheDocument()
  })

  it('清单一律按跨空间全量拉（workspace_id=0）', async () => {
    renderPage()
    await waitFor(() => expect(listReports).toHaveBeenCalled())
    expect(listReports).toHaveBeenCalledWith(0)
  })
})

describe('正文走对象存储直链', () => {
  it('按详情给的 content_url 取正文并渲染', async () => {
    renderPage()
    await waitFor(() => expect(fetchReportContent).toHaveBeenCalled())
    expect(fetchReportContent).toHaveBeenCalledWith(detail.content_url)
    await waitFor(() => expect(screen.getByText(/共 128 个新增案例/)).toBeInTheDocument())
  })

  it('直链取失败时显式暴露原因并给重试入口，不渲染空白', async () => {
    vi.mocked(fetchReportContent).mockRejectedValueOnce(
      new Error('读取报告正文失败：对象存储签名已过期或无效，请刷新报告'))
    renderPage()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent).toContain('签名已过期')
    fireEvent.click(screen.getByRole('button', { name: /重新获取正文/ }))
    await waitFor(() => expect(fetchReportContent).toHaveBeenCalledTimes(2))
    // 重试成功 → 报警区消失，正文照常渲染
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(screen.getByText(/共 128 个新增案例/)).toBeInTheDocument()
  })

  it('详情没回直链时按服务端给的迁移提示展示，不回退读库正文', async () => {
    vi.mocked(fetchReportContent).mockClear()
    const { getReportDetail } = await import('@/api/report')
    vi.mocked(getReportDetail).mockResolvedValueOnce({
      ...summary, content_url: '',
      access_warning: '报告正文尚未迁移到对象存储，请运行 backend/scripts/migrate_report_content_to_oss.py',
    } as never)
    renderPage()
    await waitFor(() => expect(
      screen.getByText(/migrate_report_content_to_oss/)).toBeInTheDocument())
    expect(fetchReportContent).not.toHaveBeenCalled()
  })
})

describe('导出入口', () => {
  it('正文加载完成后导出可用，能出 Markdown 文件', async () => {
    const clicks: string[] = []
    Object.defineProperty(window, 'URL', {
      configurable: true,
      value: { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() },
    })
    const realCreate = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = realCreate(tag) as HTMLElement
      if (tag === 'a') el.click = () => { clicks.push(String((el as HTMLAnchorElement).download)) }
      return el
    })
    renderPage()
    fireEvent.click(await waitFor(() => screen.getByRole('button', { name: /导出/ })))
    fireEvent.click(await screen.findByRole('menuitem', { name: /Markdown/ }))
    await waitFor(() => expect(clicks).toHaveLength(1))
    expect(clicks[0]).toMatch(/^新加坡近30天新增案例-\d{4}-\d{2}-\d{2}\.md$/)
    vi.spyOn(document, 'createElement').mockRestore()
  })

  it('正文还没取到时导出禁用并说明原因（不导出空文件）', async () => {
    let release: (v: string) => void = () => {}
    vi.mocked(fetchReportContent).mockReturnValue(
      new Promise<string>(res => { release = res }) as never)
    renderPage()
    const btn = await waitFor(() => screen.getByRole('button', { name: /导出/ }))
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    expect(btn.getAttribute('title')).toContain('尚未加载完成')
    release(BODY)
  })
})
