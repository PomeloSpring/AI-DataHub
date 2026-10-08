import { describe, it, expect, beforeEach, beforeAll, afterEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { toast } from 'sonner'
import Chat from '../Chat'
import { useChatStore } from '../../stores/chatStore'

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}))

// 粘贴链路不经过 3D 查看器/文件面板/图表渲染,桩掉重型依赖(three.js/mermaid 等)保持用例轻量
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

// jsdom 无 DataTransfer 构造器,用只读字段的替身传入 paste 事件
const fakeClipboard = (opts: { items?: any[]; files?: File[] }): DataTransfer =>
  ({ items: opts.items ?? [], files: opts.files ?? [] }) as unknown as DataTransfer

const fileItem = (file: File) => ({ kind: 'file', type: file.type, getAsFile: () => file })

describe('聊天输入框 Ctrl+V 粘贴图片', () => {
  beforeAll(() => {
    // jsdom 无 createObjectURL/scrollIntoView:附件预览 URL 与贴底滚动用桩(URL 每次唯一,贴近真实浏览器)
    let blobSeq = 0
    URL.createObjectURL = vi.fn(() => `blob:mock-${++blobSeq}`)
    URL.revokeObjectURL = vi.fn()
    Element.prototype.scrollIntoView = vi.fn()
  })

  beforeEach(() => {
    useChatStore.setState({
      conversations: [],
      conversationError: null,
      currentConvId: null,
      messages: [],
      loading: false,
    })
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const renderChat = () => render(<MemoryRouter><Chat /></MemoryRouter>)
  const paste = (clipboardData: DataTransfer) =>
    fireEvent.paste(screen.getByPlaceholderText('输入你的数据查询问题...'), { clipboardData })

  it('粘贴截图 → 图片进入待发附件,发送时随消息一起发给模型', async () => {
    const sendSpy = vi.spyOn(useChatStore.getState(), 'sendMessage').mockResolvedValue(undefined)
    renderChat()

    const shot = new File(['img'], 'paste-test.png', { type: 'image/png' })
    const notCanceled = paste(fakeClipboard({ items: [fileItem(shot)], files: [shot] }))

    // 阻断默认粘贴,截图入暂存区并可见
    expect(notCanceled).toBe(false)
    expect(await screen.findByText('paste-test.png')).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('已放入 1 个附件'))

    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(sendSpy).toHaveBeenCalledWith(
      '请分析我上传的附件',
      undefined,
      [expect.objectContaining({ filename: 'paste-test.png', category: 'image' })],
    )
  })

  it('无名截图自动生成「截图_时间戳.png」文件名', async () => {
    vi.spyOn(useChatStore.getState(), 'sendMessage').mockResolvedValue(undefined)
    renderChat()

    const shot = new File(['img'], '', { type: 'image/png' })
    paste(fakeClipboard({ items: [fileItem(shot)], files: [shot] }))

    expect(await screen.findByText(/^截图_\d{14}\.png$/)).toBeInTheDocument()
  })

  it('纯文本粘贴走默认行为,不产生附件', () => {
    renderChat()

    const notCanceled = paste(fakeClipboard({
      items: [{ kind: 'string', getAsFile: () => null }],
    }))

    expect(notCanceled).toBe(true)
    expect(screen.queryByText(/paste-test\.png|^截图_/)).toBeNull()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('粘贴不支持的文件类型 → 显式报错,不静默丢弃', () => {
    renderChat()

    const bad = new File(['x'], 'evil.exe', { type: 'application/x-msdownload' })
    paste(fakeClipboard({ items: [fileItem(bad)], files: [bad] }))

    expect(toast.error).toHaveBeenCalledWith('不支持的文件类型: .exe')
    expect(screen.queryByText('evil.exe')).toBeNull()
  })

  it('粘贴多个图片全部入暂存区', async () => {
    vi.spyOn(useChatStore.getState(), 'sendMessage').mockResolvedValue(undefined)
    renderChat()

    const a = new File(['img'], 'a.png', { type: 'image/png' })
    const b = new File(['img'], 'b.jpg', { type: 'image/jpeg' })
    paste(fakeClipboard({ items: [fileItem(a), fileItem(b)], files: [a, b] }))

    expect(await screen.findByText('a.png')).toBeInTheDocument()
    expect(await screen.findByText('b.jpg')).toBeInTheDocument()
  })
})
