// 消息列与底部输入卡片的对齐契约：三者（用户气泡 / AI 回复卡 / 输入卡片）必须在同一根内容列里。
//
// 历史缺陷：输入卡片走的是自己一套 `mx-auto max-w-3xl`，消息行则铺满整个内容区 ——
// 结果 AI 回复靠左、用户提问顶到视口最右、输入框居中缩在中间，三条边一条都对不上；
// 而且新会话空态用 max-w-4xl、有消息后变 max-w-3xl，发出第一条消息卡片会突然变窄。
//
// jsdom 没有布局，所以这里断言的是**结构事实**：同一份列类名挂在消息列与输入卡片上，
// 用户行确实被包在这根列里（而不是散在容器右边缘）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
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

// 本用例只关心列宽与归属，桩掉重型渲染依赖（three.js/mermaid/图表）保持轻量
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

const columns = () => Array.from(document.querySelectorAll('[data-chat-column]')) as HTMLElement[]

describe('消息列与输入卡片共用同一根内容列', () => {
  beforeEach(() => {
    useChatStore.setState({
      currentConvId: 7, loading: false, stopping: false,
      messages: [
        { role: 'user', content: 'asconnect是什么软件' },
        { role: 'assistant', content: '结论：知识库里没有登记。' },
      ] as any,
    })
  })

  afterEach(() => cleanup())

  it('输入卡片与消息列同宽同锚（靠左），右边缘对齐', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const cols = columns()
    expect(cols.length).toBe(2)
    // 两套宽度就等于没对齐：类名必须一字不差地相同
    expect(new Set(cols.map(el => el.className)).size).toBe(1)
    // 宽度必须是百分比份额：像素上限在窄窗口（用户常见 1280~1440 CSS px）等于没约束，
    // 右侧留白就根本不存在（真实回归：max-w-[1200px] 在 1280 窗口下“改了等于没改”）
    expect(cols[0].className).toMatch(/w-\[\d+%/)
    expect(cols[0].className).toMatch(/max-w-/)
    // 必须靠左：居中会把右侧留白劈成两半，那块空白是要留给后续功能的
    expect(cols[0].className).toContain('mr-auto')
    expect(cols[0].className).toContain('ml-0')
    expect(cols[0].className).not.toContain('mx-auto')
  })

  it('用户提问与 AI 回复都落在这根列里，不再各自贴视口边缘', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const user = screen.getByText('asconnect是什么软件')
    // 文档序上消息列在前、底部输入卡片在后
    const list = columns()[0]
    expect(user.closest('[data-chat-column]')).toBe(list)
    // AI 回复正文走 MarkdownWithCharts（本用例桩成 null），用机器人头像认这一行
    expect(list.querySelector('svg.lucide-bot')).toBeTruthy()
  })

  it('新会话空态居中（不需要右侧留白），但宽度口径与会话中一致', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    // 文档序最后一个就是底部输入卡片列
    const all = columns()
    const running = all[all.length - 1].className
    cleanup()
    useChatStore.setState({ messages: [], currentConvId: null })
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const empty = columns()[0].className
    // 空态还没有会话产物，跟着欢迎语走居中版式，不给右侧留白
    expect(empty).toContain('mx-auto')
    expect(empty).not.toContain('mr-auto')
    // 宽度口径必须与会话中一致（发第一条消息时只能锁位变，不能突然变宽/变窄）
    for (const w of ['w-[75%]', 'max-w-[1400px]']) {
      expect(empty).toContain(w)
      expect(running).toContain(w)
    }
    // 列内三块（欢迎语 / 输入卡片 / 示例问题）必须有纵向间隔：外层 gap 对列内子元素无效，
    // 没 gap 时推荐问题会贴着卡片下沿（用户报障“挤在一起”）
    expect(empty).toMatch(/flex-col/)
    expect(empty).toMatch(/gap-\d+/)
  })
})

// 全屏（专注）模式的两个口径：
// 1) 左侧历史会话列表不得跟着隐藏 —— 切会话是高频动作，全屏要的是不被外壳干扰；
// 2) 会话中（有消息）全屏时**照样靠左**：右侧那 25% 是要留给会话产物等信息的，
//    居中会把留白劈成两半（用户明确要过，不得回退）；focusMode 不参与锚位判定。
describe('全屏模式：会话列表保留 + 会话中仍靠左留白', () => {
  beforeEach(() => {
    useChatStore.setState({
      currentConvId: 7, loading: false, stopping: false,
      messages: [
        { role: 'user', content: 'asconnect是什么软件' },
        { role: 'assistant', content: '结论：知识库里没有登记。' },
      ] as any,
    })
  })

  afterEach(() => cleanup())

  const sidebar = () => document.querySelector('[aria-label="会话列表"]') as HTMLElement

  it('进全屏后左侧会话列表还在，消息列与输入卡片仍靠左（右侧留白不被劈半）', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    expect(sidebar()).toBeTruthy()
    fireEvent.click(screen.getByTitle('全屏（保留左侧会话列表）'))

    const list = sidebar()
    // 旧写法进全屏直接 className='hidden'（列表凭空消失）；现在必须是带宽度的正常列
    expect(list.className).not.toBe('hidden')
    expect(list.className).toContain('w-[240px]')

    const cols = columns()
    expect(cols).toHaveLength(2)
    expect(new Set(cols.map(el => el.className)).size).toBe(1)
    for (const el of cols) {
      expect(el.className).toContain('mr-auto')
      expect(el.className).toContain('ml-0')
      expect(el.className).not.toContain('mx-auto')
      // 宽度口径不变：百分比份额 + 像素上限
      expect(el.className).toMatch(/w-\[\d+%/)
      expect(el.className).toContain('max-w-[1400px]')
    }
  })

  it('进出全屏不得改版式（锚位与宽度口径全程一致）', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    const before = columns()[0].className
    fireEvent.click(screen.getByTitle('全屏（保留左侧会话列表）'))
    expect(columns()[0].className).toBe(before)
    fireEvent.click(screen.getByTitle('退出全屏 (Esc)'))
    expect(columns()[0].className).toBe(before)
    expect(sidebar().className).toContain('w-[240px]')
  })
})
