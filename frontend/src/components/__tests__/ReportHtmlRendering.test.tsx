// 会话 HTML 报告产物 → 报告中心的两处渲染面：
// 1) 报告正文：整份自包含文档必须隔离 iframe 渲染（白名单净化会把 <style>/内联 SVG
//    图表剥光 = "存了却不长那样"；把 <style> 注进主文档又会污染全局样式）；
// 2) 报告草稿：html 草稿不给 markdown 文本框（当纯文本改一处就能把整份报告改坏），
//    只让改标题 + 看预览。
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import ReportContent from '../ReportContent'
import ReportDraftEditor from '../chat/ReportDraftEditor'
import type { ChatReportDraft } from '@/stores/chatStore'

vi.mock('../MarkdownWithCharts', () => ({
  default: ({ text }: { text: string }) => <div data-testid="md">{text}</div>,
}))

const DOC = '<!DOCTYPE html><html lang="zh-CN"><head><style>h1{color:#1d4ed8}</style></head>'
  + '<body><h1>direct-ip 页面访问 IP 归属地分布报告</h1><svg><rect/></svg></body></html>'

describe('报告正文的 HTML 渲染', () => {
  afterEach(() => { cleanup() })

  it('整份 HTML 文档 → 隔离 iframe，样式与内联图表原样保留', () => {
    render(<ReportContent content={DOC} format="html" />)
    const frame = screen.getByTitle('报告正文') as HTMLIFrameElement
    expect(frame.tagName).toBe('IFRAME')
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame.getAttribute('srcDoc') || frame.getAttribute('srcdoc'))
      .toContain('<style>h1{color:#1d4ed8}</style>')
  })

  it('HTML 片段仍走白名单净化（不引 iframe，也不放样式进主文档）', () => {
    const { container } = render(
      <ReportContent content="<h1>标题</h1><style>body{color:red}</style><p>正文</p>" format="html" />)
    expect(screen.queryByTitle('报告正文')).toBeNull()
    expect(screen.getByText('标题')).toBeInTheDocument()
    expect(container.innerHTML).not.toContain('color:red')
  })

  it('markdown 报告不碰 iframe 分支', () => {
    render(<ReportContent content="# 标题" format="markdown" />)
    expect(screen.getByTestId('md')).toHaveTextContent('# 标题')
    expect(screen.queryByTitle('报告正文')).toBeNull()
  })
})

describe('报告草稿的 HTML 模式', () => {
  afterEach(() => { cleanup() })

  const draft: ChatReportDraft = {
    title: 'direct-ip 页面访问 IP 归属地分布报告', markdown: DOC,
    format: 'html', artifact: { filename: 'direct-ip访问IP归属地分布报告.html', type: 'html' },
    created_at: '2026-11-10T17:00:00', updated_at: '2026-11-10T17:00:00', revisions: 0,
    archived: null,
  }

  it('html 草稿不给 markdown 文本框，说明正文来源并给隔离预览', () => {
    render(<ReportDraftEditor draft={draft} onSave={async () => true} onArchive={() => {}} onClose={() => {}} />)
    // 标题仍是 input，但正文不给 textarea：当纯文本改会把整份报告改坏
    // （Dialog 走 portal，要查 document 而不是 container）
    expect(document.querySelector('textarea')).toBeNull()
    expect(screen.getByText(/正文取自本轮交付的/)).toHaveTextContent('direct-ip访问IP归属地分布报告.html')
    expect(screen.getByTitle('报告预览')).toBeInTheDocument()
    // 标题仍可改（报告中心条目名要可读）
    const title = screen.getByPlaceholderText(/这份报告讲什么/) as HTMLInputElement
    expect(title.value).toBe(draft.title)
  })

  it('markdown 草稿照旧可逐字编辑', () => {
    render(
      <ReportDraftEditor
        draft={{ ...draft, format: 'markdown', artifact: null, markdown: '# 初稿' }}
        onSave={async () => true} onArchive={() => {}} onClose={() => {}}
      />,
    )
    const box = document.querySelector('textarea') as HTMLTextAreaElement
    expect(box.value).toBe('# 初稿')
  })

  it('html 草稿点归档：先落库再归档（正文仍取会话里那份产物原文）', async () => {
    const onSave = vi.fn(async (_patch: ChatReportDraft) => true)
    const onArchive = vi.fn()
    render(<ReportDraftEditor draft={draft} onSave={onSave} onArchive={onArchive} onClose={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/这份报告讲什么/), { target: { value: '改过的标题' } })
    fireEvent.click(screen.getByRole('button', { name: /归档进报告中心/ }))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    expect(onSave.mock.calls[0][0]).toMatchObject({ title: '改过的标题', format: 'html', markdown: DOC })
    await waitFor(() => expect(onArchive).toHaveBeenCalledTimes(1))
  })
})
