// 长会话提问节点导航在 Chat 页的接线契约：
// 节点必须**一条提问一个**（不是每条消息一个），文本取自用户气泡正文；
// 只带附件的提问不能是空白节点（点了不知道跳到哪一问）；单提问会话不给死开关。
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
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

// 只关心节点清单，桩掉重型渲染依赖（three.js/mermaid/图表/文件面板）
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

const nodes = () => Array.from(
  document.querySelectorAll('[data-turn-navigator] button[aria-label^="跳转到第"]'),
) as HTMLButtonElement[]

describe('Chat 提问节点导航', () => {
  afterEach(() => cleanup())

  it('两条提问两个节点，文本来自用户气泡正文', () => {
    useChatStore.setState({
      currentConvId: 7, loading: false, stopping: false,
      messages: [
        { role: 'user', content: '帮我查一下11月3号设备表的同步日志' },
        { role: 'assistant', content: '我先加载相关的分析 skill…' },
        { role: 'user', content: '这次用设备 id 带，不要整机' },
        { role: 'assistant', content: '结论：已按设备 id 过滤。' },
      ] as any,
    })
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const list = nodes()
    expect(list).toHaveLength(2)
    expect(list[0].getAttribute('aria-label')).toContain('帮我查一下11月3号设备表的同步日志')
    expect(list[1].getAttribute('aria-label')).toContain('这次用设备 id 带，不要整机')
  })

  it('只带附件的提问显示附件名，不留空白节点', () => {
    useChatStore.setState({
      currentConvId: 7, loading: false, stopping: false,
      messages: [
        { role: 'user', content: '同步日志如下', attachments: [{ filename: 'device.csv', category: 'table' }] },
        { role: 'assistant', content: '已读取。' },
        { role: 'user', content: '', attachments: [{ filename: '截图_01.png', category: 'image' }] },
      ] as any,
    })
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const list = nodes()
    expect(list).toHaveLength(2)
    expect(list[1].getAttribute('aria-label')).toContain('截图_01.png')
  })

  it('只有一条提问时不渲染导航（没有回找需求）', () => {
    useChatStore.setState({
      currentConvId: 7, loading: false, stopping: false,
      messages: [
        { role: 'user', content: '180 天保留窗口内有多少台设备' },
        { role: 'assistant', content: '结论：3412 台。' },
      ] as any,
    })
    render(<MemoryRouter><Chat /></MemoryRouter>)
    expect(screen.queryByLabelText('会话提问导航')).toBeNull()
  })
})
