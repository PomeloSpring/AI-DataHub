import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChatStore, deriveTitle, type ChatMessage } from '../chatStore'

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
      currentConvId: null,
      messages: [],
      loading: false,
      loadingStep: '',
      abortController: null,
      selectedDsId: 0,
      datasources: [],
      selectedModelId: null,
      llmModels: [],
      useLoopEngine: false,
      selectedWorkflowId: null,
      workflows: [],
    })
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  describe('loadConversations', () => {
    it('loads conversations from API', async () => {
      const convs = [
        { id: 1, title: 'Conv 1', datasource_id: 1, created_at: '2024-01-01', updated_at: '2024-01-01' },
        { id: 2, title: 'Conv 2', datasource_id: 1, created_at: '2024-01-02', updated_at: '2024-01-02' },
      ]
      mockClient.get.mockResolvedValue({ data: convs })

      await useChatStore.getState().loadConversations()
      expect(useChatStore.getState().conversations).toEqual(convs)
    })

    it('handles API error gracefully', async () => {
      mockClient.get.mockRejectedValue(new Error('Network error'))

      await useChatStore.getState().loadConversations()
      expect(useChatStore.getState().conversations).toEqual([])
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

  describe('setUseLoopEngine', () => {
    it('toggles loop engine', () => {
      useChatStore.getState().setUseLoopEngine(true)
      expect(useChatStore.getState().useLoopEngine).toBe(true)
      useChatStore.getState().setUseLoopEngine(false)
      expect(useChatStore.getState().useLoopEngine).toBe(false)
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
      expect(useChatStore.getState().currentConvId).toBe(999)
      expect(useChatStore.getState().messages).toEqual([])
    })
  })

  describe('deleteConversation', () => {
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
