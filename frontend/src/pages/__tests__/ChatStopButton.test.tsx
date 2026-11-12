// 运行中的「停止」交互：发送位就地变成方块停止按钮，点一下即停。
//
// 旧口径是在发送按钮旁另挂一个「✕ 停止」文字按钮（两个按钮并排、占位、还和发送态
// 的转圈重复表达同一件事）。现在收敛成一个位置：空闲=发送箭头，运行中=停止方块。
// 配色口径（用户拍板）：不抄参考图的黑底，必须走主题变量（与发送钮同为 bg-primary），
// 否则切主题时这颗按钮会脱离全平台配色。
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
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

// 本用例只关心输入区右下角那颗按钮，桩掉重型渲染依赖保持轻量
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

const stopSpy = vi.fn()

describe('运行中的停止按钮（发送位就地切换）', () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn()
  })

  beforeEach(() => {
    stopSpy.mockClear()
    useChatStore.setState({
      messages: [], currentConvId: 1,
      loading: false, stopping: false, cancelMessage: stopSpy,
    } as any)
  })

  afterEach(() => cleanup())

  it('空闲时是发送按钮，没有停止按钮', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument()
  })

  it('运行中：发送位变成可点的停止按钮，点击即停止本轮', () => {
    useChatStore.setState({ loading: true })
    render(<MemoryRouter><Chat /></MemoryRouter>)

    const btn = screen.getByRole('button', { name: '停止' })
    expect(btn).toBeEnabled()
    // 方块按钮（不是并排的第二个按钮）：同一位置只应有一个动作按钮
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    fireEvent.click(btn)
    expect(stopSpy).toHaveBeenCalledTimes(1)
  })

  it('已点停止、后端未释放执行权：按钮转圈禁用，不再重复点', () => {
    useChatStore.setState({ loading: false, stopping: true })
    render(<MemoryRouter><Chat /></MemoryRouter>)

    const btn = screen.getByRole('button', { name: '停止' })
    expect(btn).toBeDisabled()
    fireEvent.click(btn)
    expect(stopSpy).not.toHaveBeenCalled()
    expect(btn.getAttribute('title')).toContain('正在停止')
  })

  it('停止钮用主题主色（与发送钮同一颗），不得写死黑底/裸色值', () => {
    useChatStore.setState({ loading: true })
    render(<MemoryRouter><Chat /></MemoryRouter>)

    const cls = screen.getByRole('button', { name: '停止' }).className
    expect(cls).toContain('bg-primary')
    expect(cls).toContain('text-primary-foreground')
    // 参考图那套黑底是抄来的写法：不随主题变化，不得回潮
    expect(cls).not.toMatch(/bg-foreground|text-background/)
  })

  it('停止不再靠并排的文字按钮（旧「✕ 停止」不得回潮）', () => {
    const src = readFileSync(resolve(__dirname, '../Chat.tsx'), 'utf-8')
    expect(src).not.toMatch(/\?\s*'正在停止…'\s*:\s*'停止'/)
    expect(src).toMatch(/aria-label="停止"/)
    // 停止钮的 className 不得出现裸 hex（主题变量口径）
    const stopLine = src.split('\n').filter(l => l.includes('aria-label="停止"') || l.includes('rounded-lg ring-1 ring-primary'))
    expect(stopLine.join('\n')).not.toMatch(/#[0-9a-fA-F]{3,8}/)
  })
})
