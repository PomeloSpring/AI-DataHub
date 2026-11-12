// Esc 必须真的退出「/」选择（而不是"按了没反应"）。
//
// 历史缺陷：Esc 只把面板关掉，正在检索的 `/xxx` 片段还留在输入框里，于是用户接着
// 敲第一个字符时 slashQueryOf 又命中那个 `/`，面板立刻顶回来——从用户视角看就是
// "Esc 不管用，弹出来的东西关不掉"。这里锁两件事：
// * Esc 后面板消失 **且** 输入框里不再有那个 `/` 片段（正文前缀原样保留）；
// * 退出后继续打字不会因为残留斜杠重新弹出面板。
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Chat from '../Chat'
import { useChatStore } from '../../stores/chatStore'

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

// `/` 候选走服务端同源清单：给两条真候选，面板才有可断言的类别标签
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn(async (url: string) => ({
      data: url.includes('/chat/slash-options')
        ? {
            skills: [{ name: 'data-analysis', display_name: '数据分析师', description: '语义层取数剧本' }],
            tasks: [],
            commands: [{ key: 'create_scheduled_task', label: '创建定时任务', description: '把本轮问题创建为周期执行的任务' }],
            modes: [],
          }
        : [],
    })),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}))

// 本用例只关心输入区，桩掉重型渲染依赖（three.js/mermaid/图表）保持轻量
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

const inputEl = () => screen.getByPlaceholderText('输入你的数据查询问题…') as HTMLInputElement

describe('输入框「/」选择的 Esc 退出', () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn()
  })

  beforeEach(() => {
    localStorage.clear()
    useChatStore.setState({ messages: [], currentConvId: null, loading: false })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('Esc 关面板并摘掉正在检索的 / 片段，接着打字不再弹回', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const input = inputEl()

    fireEvent.change(input, { target: { value: '/' } })
    await waitFor(() => expect(screen.getByRole('tab', { name: '技能' })).toBeInTheDocument())

    fireEvent.keyDown(input, { key: 'Escape' })
    // 残留的 `/` 是复发的根源：必须一起摘掉
    expect(input.value).toBe('')
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()

    fireEvent.change(input, { target: { value: '帮我看看昨天的量' } })
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('只摘 / 开始的检索片段，不动它前面的正文', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const input = inputEl()

    fireEvent.change(input, { target: { value: '帮我 /技能 数' } })
    await waitFor(() => expect(screen.getByRole('tab', { name: '技能' })).toBeInTheDocument())

    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input.value).toBe('帮我 ')
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  // 中文输入法的中文标点模式下按 `/` 键出的是顿号「、」：同一个面板、同一个 Esc 语义。
  // 若只认半角 `/`，用户要么每次开面板前先切输入法，要么被残留的顿号把面板反复顶回来。
  it('中文输入法下用「、」起的检索，Esc 同样摘干净且不再弹回', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const input = inputEl()

    fireEvent.change(input, { target: { value: '、技能' } })
    await waitFor(() => expect(screen.getByRole('tab', { name: '技能' })).toBeInTheDocument())

    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input.value).toBe('')
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()

    // 正文里的顿号列举（紧贴前文）不得把面板顶出来
    fireEvent.change(input, { target: { value: '统计苹果、香蕉的销量' } })
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('面板没开时 Esc 不吃事件（留给专注模式等其它退出语义）', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const input = inputEl()
    fireEvent.change(input, { target: { value: '普通问题' } })
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
    // 正文里不该被改动
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input.value).toBe('普通问题')
  })
})
