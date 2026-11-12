// 会话历史批量管理（勾选 → 批量删除）的交互契约。
//
// 锁三件事：
// 1. 批量模式下点行是「勾选/取消」，不是切换会话（否则一边清理一边切上下文）；
// 2. 确认框与失败提示都点名到会话标题，不拿裸 id 面用户（ui-resource-display）；
// 3. 部分失败不静默：失败的保持勾选，用户能立刻重试（no-silent-degradation）。
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { toast } from 'sonner'
import Chat from '../Chat'
import { useChatStore } from '../../stores/chatStore'

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }))

vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}))
import client from '../../api/client'
const mockClient = vi.mocked(client)

vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

const CONVS = [
  { id: 11, title: '近7天案例趋势', datasource_id: 1, created_at: '', updated_at: '' },
  { id: 12, title: '站点口径确认', datasource_id: 1, created_at: '', updated_at: '' },
]

const enterBatch = () => {
  fireEvent.click(screen.getByRole('button', { name: /批量管理会话历史/ }))
}

describe('会话历史批量管理', () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn()
    URL.createObjectURL = vi.fn(() => 'blob:mock')
    URL.revokeObjectURL = vi.fn()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    useChatStore.setState({
      conversations: CONVS, currentConvId: 11, messages: [{ role: 'user', content: 'keep' }],
      loading: false,
    })
  })

  afterEach(() => cleanup())

  it('批量模式下点行只切换勾选，不切换会话', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    enterBatch()

    expect(screen.getByText('全选')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('选择会话 站点口径确认'))

    expect(screen.getByText('已选 1 / 2')).toBeInTheDocument()
    // 会话没被切走：当前仍是 11，消息也没被重拉
    expect(useChatStore.getState().currentConvId).toBe(11)
    expect(mockClient.get).not.toHaveBeenCalledWith('/chat/conversations/12')
  })

  it('确认框按标题点名待删会话，不显示裸 id', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    enterBatch()
    fireEvent.click(screen.getByLabelText('选择会话 近7天案例趋势'))
    fireEvent.click(screen.getByRole('button', { name: /删除 1 个/ }))

    expect(screen.getByText('批量删除会话')).toBeInTheDocument()
    expect(screen.getByText('· 近7天案例趋势')).toBeInTheDocument()
    expect(screen.queryByText(/11/)).not.toBeInTheDocument()
  })

  it('部分失败时失败的保持勾选并点名原因，成功的从清单移除', async () => {
    mockClient.post.mockResolvedValue({
      data: { deleted: [11], failed: [{ id: 12, title: '站点口径确认', reason: '会话仍在执行，请先停止后再删除' }] },
    })
    render(<MemoryRouter><Chat /></MemoryRouter>)
    enterBatch()
    fireEvent.click(screen.getByLabelText('全选会话'))
    fireEvent.click(screen.getByRole('button', { name: /删除 2 个/ }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))

    await waitFor(() => expect(mockClient.post).toHaveBeenCalledWith(
      '/chat/conversations/batch-delete', { conversation_ids: [11, 12] }))
    // @/lib/toast 包装器会给每条提示补上分档 duration，断言按两形参写（与全站其余用例同口径）
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining('会话仍在执行，请先停止后再删除'), expect.anything()))
    expect(toast.success).toHaveBeenCalledWith('已删除 1 个会话', expect.anything())
    // 仍在批量态，且勾选只剩失败项 → 用户可以直接重试
    expect(screen.getByText('已选 1 / 1')).toBeInTheDocument()
    expect(screen.getByLabelText('选择会话 站点口径确认')).toBeChecked()
  })

  it('未勾选时删除按钮不可用（不给出点了没反应的死开关）', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    enterBatch()
    expect(screen.getByRole('button', { name: /^删除$/ })).toBeDisabled()
  })

  it('退出批量管理后勾选清空，行恢复为切换会话', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>)
    enterBatch()
    fireEvent.click(screen.getByLabelText('选择会话 站点口径确认'))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(screen.queryByText('全选')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('选择会话 站点口径确认')).toBeNull()
  })
})
