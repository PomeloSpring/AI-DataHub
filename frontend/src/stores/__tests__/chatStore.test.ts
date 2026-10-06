import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChatStore, deriveTitle, saveMessages, type ChatMessage } from '../chatStore'
import { useAsBotStore } from '../asBotStore'
import { toast } from 'sonner'

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

// Mock the axios client
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import client from '../../api/client'
const mockClient = vi.mocked(client)

describe('deriveTitle', () => {
  it('returns first 30 chars of first user message', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: '这是一段很长的用户消息内容超过三十个字符应该被截断显示' },
      { role: 'assistant', content: '回复' },
    ]
    const title = deriveTitle(messages)
    expect(title).toBeDefined()
    expect(title!.length).toBeLessThanOrEqual(33) // 30 + '...'
    expect(title).toContain('这是一段很长的用户消息内容')
  })

  it('returns undefined when no user messages', () => {
    const messages: ChatMessage[] = [
      { role: 'assistant', content: 'Hello' },
    ]
    expect(deriveTitle(messages)).toBeUndefined()
  })

  it('returns undefined for empty messages', () => {
    expect(deriveTitle([])).toBeUndefined()
  })

  it('does not truncate short messages', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: '短消息' },
    ]
    const title = deriveTitle(messages)
    expect(title).toBe('短消息')
  })

  it('truncates at 30 characters with ellipsis', () => {
    const longContent = 'A'.repeat(50)
    const messages: ChatMessage[] = [
      { role: 'user', content: longContent },
    ]
    const title = deriveTitle(messages)
    expect(title).toBe('A'.repeat(30) + '...')
  })
})

describe('chatStore', () => {
  beforeEach(() => {
    useChatStore.setState({
      conversations: [],
      conversationError: null,
      selectedWorkspaceId: 0,
      currentConvId: null,
      messages: [],
      loading: false,
      loadingStep: '',
      abortController: null,
      selectedDsId: 0,
      datasources: [],
      selectedModelId: null,
      llmModels: [],
      selectedAsBotKey: null,
      asBots: [],
    })
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  describe('loadConversations', () => {
    it('loads conversations from API', async () => {
      useChatStore.getState().setSelectedWorkspaceId(7)
      const convs = [
        { id: 1, title: 'Conv 1', datasource_id: 1, created_at: '2024-01-01', updated_at: '2024-01-01' },
        { id: 2, title: 'Conv 2', datasource_id: 1, created_at: '2024-01-02', updated_at: '2024-01-02' },
      ]
      mockClient.get.mockResolvedValue({ data: convs })

      await useChatStore.getState().loadConversations()
      expect(useChatStore.getState().conversations).toEqual(convs)
      expect(mockClient.get).toHaveBeenCalledWith('/chat/conversations?workspace_id=7')
    })

    it('handles API error gracefully', async () => {
      useChatStore.getState().setSelectedWorkspaceId(7)
      mockClient.get.mockRejectedValue(new Error('Network error'))

      await useChatStore.getState().loadConversations()
      expect(useChatStore.getState().conversations).toEqual([])
      expect(useChatStore.getState().conversationError).toBe('会话列表加载失败，请重试')
    })

    it('未选择工作空间时不请求全量会话', async () => {
      await useChatStore.getState().loadConversations()
      expect(mockClient.get).not.toHaveBeenCalled()
    })

    it('切换工作空间后忽略旧列表响应并清理旧消息', async () => {
      useChatStore.getState().setSelectedWorkspaceId(7)
      useChatStore.setState({ messages: [{ role: 'user', content: '旧消息' }] })
      let finish!: (value: any) => void
      mockClient.get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      const old = useChatStore.getState().loadConversations()
      useChatStore.getState().setSelectedWorkspaceId(8)
      mockClient.get.mockResolvedValueOnce({ data: [{ id: 8, title: '新空间会话' }] })
      await useChatStore.getState().loadConversations()
      finish({ data: [{ id: 7, title: '旧空间会话' }] })
      await old
      expect(useChatStore.getState().conversations).toEqual([{ id: 8, title: '新空间会话' }])
      expect(useChatStore.getState().messages).toEqual([])
    })
  })

  describe('loadDatasources', () => {
    it('loads datasources and auto-selects default', async () => {
      const datasources = [
        { id: 1, name: 'DS1', db_type: 'doris' },
        { id: 2, name: 'DS2', db_type: 'mysql', is_default: true },
      ]
      mockClient.get.mockResolvedValue({ data: datasources })

      await useChatStore.getState().loadDatasources()
      expect(useChatStore.getState().datasources).toEqual(datasources)
      expect(useChatStore.getState().selectedDsId).toBe(2)
    })

    it('auto-selects first datasource when no default', async () => {
      const datasources = [
        { id: 10, name: 'DS1', db_type: 'doris' },
      ]
      mockClient.get.mockResolvedValue({ data: datasources })
      useChatStore.setState({ selectedDsId: 0 })

      await useChatStore.getState().loadDatasources()
      expect(useChatStore.getState().selectedDsId).toBe(10)
    })

    it('preserves existing selection', async () => {
      const datasources = [
        { id: 1, name: 'DS1', db_type: 'doris' },
        { id: 2, name: 'DS2', db_type: 'mysql' },
      ]
      mockClient.get.mockResolvedValue({ data: datasources })
      useChatStore.setState({ selectedDsId: 1 })

      await useChatStore.getState().loadDatasources()
      expect(useChatStore.getState().selectedDsId).toBe(1)
    })
  })

  describe('loadLLMModels', () => {
    it('loads models and auto-selects default', async () => {
      const models = [
        { id: 1, name: 'GPT-4', provider: 'openai', model_name: 'gpt-4', is_default: 0 },
        { id: 2, name: 'Claude', provider: 'anthropic', model_name: 'claude-3', is_default: 1 },
      ]
      mockClient.get.mockResolvedValue({ data: models })

      await useChatStore.getState().loadLLMModels()
      expect(useChatStore.getState().llmModels).toEqual(models)
      expect(useChatStore.getState().selectedModelId).toBe(2)
    })
  })

  describe('setSelectedDsId', () => {
    it('updates selected datasource', () => {
      useChatStore.getState().setSelectedDsId(42)
      expect(useChatStore.getState().selectedDsId).toBe(42)
    })
  })

  describe('setSelectedModelId', () => {
    it('updates selected model', () => {
      useChatStore.getState().setSelectedModelId(5)
      expect(useChatStore.getState().selectedModelId).toBe(5)
    })

    it('allows null for default model', () => {
      useChatStore.setState({ selectedModelId: 5 })
      useChatStore.getState().setSelectedModelId(null)
      expect(useChatStore.getState().selectedModelId).toBeNull()
    })
  })

  describe('createConversation', () => {
    it('creates conversation and sets as current', async () => {
      mockClient.post.mockResolvedValue({
        data: { id: 100, title: '新对话', datasource_id: 1, created_at: '2024-01-01' },
      })
      useChatStore.setState({ selectedDsId: 1 })

      const id = await useChatStore.getState().createConversation()
      expect(id).toBe(100)
      expect(useChatStore.getState().currentConvId).toBe(100)
      expect(useChatStore.getState().messages).toEqual([])
      expect(useChatStore.getState().conversations[0].id).toBe(100)
    })
  })

  describe('switchConversation', () => {
    it('loads conversation messages', async () => {
      const messages: ChatMessage[] = [
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Hi there' },
      ]
      mockClient.get.mockResolvedValue({
        data: { messages, datasource_id: 1 },
      })

      await useChatStore.getState().switchConversation(1)
      expect(useChatStore.getState().currentConvId).toBe(1)
      expect(useChatStore.getState().messages).toEqual(messages)
      expect(useChatStore.getState().selectedDsId).toBe(1)
    })

    it('handles API error gracefully', async () => {
      mockClient.get.mockRejectedValue(new Error('Not found'))

      await useChatStore.getState().switchConversation(999)
      expect(useChatStore.getState().currentConvId).toBeNull()
      expect(useChatStore.getState().messages).toEqual([])
    })
  })

  describe('deleteConversation', () => {
    it('清理失败时保留会话和消息，并显示可重试提示', async () => {
      const detail = '会话工作区清理未完成，记录仍保留；请重试删除'
      mockClient.delete.mockRejectedValueOnce({ response: { data: { detail } } })
      useChatStore.setState({
        conversations: [{ id: 1, title: 'A', datasource_id: 1, created_at: '', updated_at: '' }],
        currentConvId: 1, messages: [{ role: 'user', content: '保留' }],
      })
      await useChatStore.getState().deleteConversation(1)
      expect(useChatStore.getState().currentConvId).toBe(1)
      expect(useChatStore.getState().conversations).toHaveLength(1)
      expect(useChatStore.getState().messages[0].content).toBe('保留')
      expect(toast.error).toHaveBeenCalledWith(detail)
    })

    it('运行中会话删除被拒绝时提示先停止', async () => {
      const detail = '会话仍在执行或尚未确认停止，请停止后再删除'
      mockClient.delete.mockRejectedValueOnce({ response: { status: 409, data: { detail } } })
      useChatStore.setState({ currentConvId: 1, loading: true })
      await useChatStore.getState().deleteConversation(1)
      expect(useChatStore.getState().currentConvId).toBe(1)
      expect(useChatStore.getState().loading).toBe(true)
      expect(toast.error).toHaveBeenCalledWith(detail)
    })

    it('系统助手也显示清理失败原因，不提前移除历史', async () => {
      const detail = '当前实例无法访问该会话的存储节点，请在对应节点重试删除'
      mockClient.delete.mockRejectedValueOnce({ response: { data: { detail } } })
      useAsBotStore.setState({
        conversationId: 1, messages: [{ role: 'user', content: '保留' }],
        conversations: [{ id: 1, title: 'A', workspace_id: 0, datasource_id: 0, created_at: '', updated_at: '' }],
      })
      await useAsBotStore.getState().deleteConversation(1)
      expect(useAsBotStore.getState().conversationId).toBe(1)
      expect(useAsBotStore.getState().conversations).toHaveLength(1)
      expect(toast.error).toHaveBeenCalledWith(detail)
    })

    it('removes conversation from list', async () => {
      mockClient.delete.mockResolvedValue({})
      useChatStore.setState({
        conversations: [
          { id: 1, title: 'A', datasource_id: 1, created_at: '', updated_at: '' },
          { id: 2, title: 'B', datasource_id: 1, created_at: '', updated_at: '' },
        ],
        currentConvId: 1,
      })

      await useChatStore.getState().deleteConversation(1)
      expect(useChatStore.getState().conversations).toHaveLength(1)
      expect(useChatStore.getState().conversations[0].id).toBe(2)
      expect(useChatStore.getState().currentConvId).toBeNull()
      expect(useChatStore.getState().messages).toEqual([])
    })

    it('preserves current when deleting non-current', async () => {
      mockClient.delete.mockResolvedValue({})
      useChatStore.setState({
        conversations: [
          { id: 1, title: 'A', datasource_id: 1, created_at: '', updated_at: '' },
          { id: 2, title: 'B', datasource_id: 1, created_at: '', updated_at: '' },
        ],
        currentConvId: 2,
        messages: [{ role: 'user', content: 'keep' }],
      })

      await useChatStore.getState().deleteConversation(1)
      expect(useChatStore.getState().currentConvId).toBe(2)
      expect(useChatStore.getState().messages).toHaveLength(1)
    })
  })

  describe('renameConversation', () => {
    it('updates conversation title', async () => {
      mockClient.put.mockResolvedValue({})
      useChatStore.setState({
        conversations: [
          { id: 1, title: 'Old', datasource_id: 1, created_at: '', updated_at: '' },
        ],
      })

      await useChatStore.getState().renameConversation(1, 'New Title')
      expect(useChatStore.getState().conversations[0].title).toBe('New Title')
    })
  })

  describe('setViewMode', () => {
    it('changes view mode for a message', () => {
      useChatStore.setState({
        messages: [
          { role: 'user', content: 'q' },
          { role: 'assistant', content: 'a', viewMode: 'chart' },
        ],
      })

      useChatStore.getState().setViewMode(1, 'table')
      expect(useChatStore.getState().messages[1].viewMode).toBe('table')
    })

    it('handles out-of-bounds index gracefully', () => {
      useChatStore.setState({ messages: [] })
      // Should not throw
      useChatStore.getState().setViewMode(99, 'sql')
    })
  })

  describe('updateMessageFeedback', () => {
    it('updates feedback on a message', () => {
      useChatStore.setState({
        messages: [
          { role: 'assistant', content: 'result' },
        ],
        currentConvId: 1,
      })
      mockClient.put.mockResolvedValue({})

      useChatStore.getState().updateMessageFeedback(0, 'up')
      expect(useChatStore.getState().messages[0].feedback).toBe('up')
    })

    it('includes expected_table for negative feedback', () => {
      useChatStore.setState({
        messages: [{ role: 'assistant', content: 'result' }],
        currentConvId: 1,
      })
      mockClient.put.mockResolvedValue({})

      useChatStore.getState().updateMessageFeedback(0, 'down', 't_user')
      expect(useChatStore.getState().messages[0].feedback).toBe('down')
      expect(useChatStore.getState().messages[0].expected_table).toBe('t_user')
    })
  })

  describe('clear', () => {
    it('resets messages and current conversation', () => {
      useChatStore.setState({
        messages: [{ role: 'user', content: 'test' }],
        currentConvId: 1,
        loading: true,
        loadingStep: 'processing',
      })

      useChatStore.getState().clear()
      expect(useChatStore.getState().messages).toEqual([])
      expect(useChatStore.getState().currentConvId).toBeNull()
      expect(useChatStore.getState().loading).toBe(false)
      expect(useChatStore.getState().loadingStep).toBe('')
    })
  })

  describe('AS-BOT 会话隔离', () => {
    it('切换 AS-BOT 时清空当前会话并取消执行', () => {
      const abort = vi.fn()
      useChatStore.setState({ currentConvId: 12, selectedAsBotKey: 'chatbi', messages: [{ role: 'user', content: '私有上下文' }],
        abortController: { abort } as any, capabilities: { tools: [], version: 'old', empty: true } })
      useChatStore.getState().setSelectedAsBotKey('other')
      expect(abort).toHaveBeenCalledOnce()
      expect(useChatStore.getState().currentConvId).toBeNull()
      expect(useChatStore.getState().messages).toEqual([])
      expect(useChatStore.getState().capabilities).toBeNull()
    })

    it('打开未绑定 AS-BOT 的历史会话后选择 AS-BOT 仅绑定, 不清空消息', () => {
      useChatStore.setState({ currentConvId: 12, selectedAsBotKey: null,
        messages: [{ role: 'user', content: '历史消息' }, { role: 'assistant', content: '回答' }] })
      useChatStore.getState().setSelectedAsBotKey('chatbi')
      const s = useChatStore.getState()
      expect(s.selectedAsBotKey).toBe('chatbi')
      expect(s.currentConvId).toBe(12)          // 仍停留在该历史会话
      expect(s.messages).toHaveLength(2)         // 消息未被清空
    })

    it('选择与当前相同的 AS-BOT 不触发任何重置', () => {
      useChatStore.setState({ currentConvId: 12, selectedAsBotKey: 'chatbi',
        messages: [{ role: 'user', content: '历史消息' }] })
      useChatStore.getState().setSelectedAsBotKey('chatbi')
      const s = useChatStore.getState()
      expect(s.currentConvId).toBe(12)
      expect(s.messages).toHaveLength(1)
    })

    it('切换工作空间不会继承旧会话', () => {
      useChatStore.setState({ currentConvId: 12, selectedWorkspaceId: 1 })
      useChatStore.getState().setSelectedWorkspaceId(2)
      expect(useChatStore.getState().currentConvId).toBeNull()
      expect(useChatStore.getState().selectedWorkspaceId).toBe(2)
    })

    it('恢复会话以服务端工作空间和 AS-BOT 为准，不接管 SDK 标识', async () => {
      mockClient.get.mockResolvedValue({ data: { messages: [], workspace_id: 7, as_bot_key: 'bound', executor_session_id: 'untrusted' } })
      await useChatStore.getState().switchConversation(20)
      expect(useChatStore.getState().selectedWorkspaceId).toBe(7)
      expect(useChatStore.getState().selectedAsBotKey).toBe('bound')
      expect(useChatStore.getState().executorSessionId).toBeNull()
    })

    it('打开未绑定 AS-BOT 的历史会话时默认选中首个可用 AS-BOT（不留空）', async () => {
      useChatStore.setState({
        selectedWorkspaceId: 3,
        asBots: [
          { as_bot_key: 'chatbi', name: 'chatbi', available: true } as any,
          { as_bot_key: 'nl2sql', name: 'nl2sql', available: true } as any,
        ],
      })
      mockClient.get.mockResolvedValue({ data: { messages: [{ role: 'user', content: '历史' }], workspace_id: 3, as_bot_key: '' } })
      await useChatStore.getState().switchConversation(31)
      const s = useChatStore.getState()
      expect(s.selectedAsBotKey).toBe('chatbi')   // 默认首个可用, 而非空"选择 AS-BOT"
      expect(s.messages).toHaveLength(1)            // 历史消息正常加载
    })

    it('历史会话 AS-BOT 为空时优先默认 is_default 项', async () => {
      useChatStore.setState({
        selectedWorkspaceId: 3,
        asBots: [
          { as_bot_key: 'chatbi', name: 'chatbi', available: true } as any,
          { as_bot_key: 'nl2sql', name: 'nl2sql', available: true, is_default: true } as any,
        ],
      })
      mockClient.get.mockResolvedValue({ data: { messages: [], workspace_id: 3, as_bot_key: null } })
      await useChatStore.getState().switchConversation(32)
      expect(useChatStore.getState().selectedAsBotKey).toBe('nl2sql')
    })

    it('前端保存记录不写 SDK 会话标识', async () => {
      mockClient.put.mockResolvedValue({})
      await saveMessages(20, [{ role: 'user', content: 'hello' }])
      expect(mockClient.put.mock.calls[0][1]).not.toHaveProperty('executor_session_id')
    })
  })

  describe('异步上下文隔离', () => {
    it('旧工作空间的权限查询失败不会清除当前 AS-BOT', async () => {
      let reject!: (reason: unknown) => void
      mockClient.get.mockReturnValueOnce(new Promise((_, r) => { reject = r }))
      const loading = useChatStore.getState().loadAsBots(1)
      useChatStore.setState({ selectedWorkspaceId: 2, selectedAsBotKey: 'keep' })
      reject(new Error('旧请求失败'))
      await loading
      expect(useChatStore.getState().selectedAsBotKey).toBe('keep')
    })

    it('创建会话期间切换 AS-BOT 不会接管迟到会话', async () => {
      let resolve!: (result: any) => void
      mockClient.post.mockReturnValueOnce(new Promise(r => { resolve = r }))
      const creating = useChatStore.getState().createConversation()
      useChatStore.getState().setSelectedAsBotKey('new')
      resolve({ data: { id: 45 } })
      await expect(creating).rejects.toThrow('上下文已改变')
      expect(useChatStore.getState().currentConvId).toBeNull()
    })

    it('旧请求异常不会关闭新会话的加载状态', async () => {
      let reject!: (reason: unknown) => void
      vi.stubGlobal('fetch', vi.fn(() => new Promise((_, r) => { reject = r })))
      try {
        useChatStore.setState({ currentConvId: 1 })
        const sending = useChatStore.getState().sendMessage('hi')
        const next = new AbortController()
        useChatStore.setState({ currentConvId: 2, loading: true, abortController: next,
          messages: [{ role: 'user', content: 'new' }] })
        reject(new DOMException('取消', 'AbortError'))
        await sending
        expect(useChatStore.getState().abortController).toBe(next)
        expect(useChatStore.getState().loading).toBe(true)
        expect(useChatStore.getState().messages[0].content).toBe('new')
      } finally { vi.unstubAllGlobals() }
    })
  })

  describe('AS-BOT 会话隔离', () => {
    beforeEach(() => {
      useAsBotStore.setState({ conversationId: null, loading: false, abortController: null, messages: [], conversations: [] })
    })

    it('新建会话会取消旧请求', () => {
      const abort = new AbortController()
      useAsBotStore.setState({ conversationId: 4, abortController: abort, loading: true })
      useAsBotStore.getState().newConversation()
      expect(abort.signal.aborted).toBe(true)
      expect(useAsBotStore.getState().conversationId).toBeNull()
    })

    it('建会话失败不进入无持久化执行', async () => {
      mockClient.post.mockRejectedValueOnce(new Error('失败'))
      const fetch = vi.fn()
      vi.stubGlobal('fetch', fetch)
      try {
        await useAsBotStore.getState().sendMessage('hi')
        expect(fetch).not.toHaveBeenCalled()
        const messages = useAsBotStore.getState().messages
        expect(messages[messages.length - 1]?.error).toContain('消息未发送')
      } finally { vi.unstubAllGlobals() }
    })

    it('建会话期间清空不接管迟到响应', async () => {
      let resolve!: (result: any) => void
      mockClient.post.mockReturnValueOnce(new Promise(r => { resolve = r }))
      const fetch = vi.fn()
      vi.stubGlobal('fetch', fetch)
      try {
        const sending = useAsBotStore.getState().sendMessage('hi')
        useAsBotStore.getState().newConversation()
        resolve({ data: { id: 45 } })
        await sending
        expect(useAsBotStore.getState().conversationId).toBeNull()
        expect(useAsBotStore.getState().messages).toEqual([])
        expect(fetch).not.toHaveBeenCalled()
      } finally { vi.unstubAllGlobals() }
    })
  })

  describe('cancelMessage', () => {
    it('aborts the current request', () => {
      const abortFn = vi.fn()
      const controller = { abort: abortFn } as any
      useChatStore.setState({ abortController: controller })

      useChatStore.getState().cancelMessage()
      expect(abortFn).toHaveBeenCalled()
    })

    it('does nothing when no abort controller', () => {
      useChatStore.setState({ abortController: null })
      // Should not throw
      useChatStore.getState().cancelMessage()
    })
  })
})
