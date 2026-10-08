import { create } from 'zustand';
import client from '../api/client';
import { toast } from 'sonner';

// AS-BOT 面板与智能问数同一载体、不同入口：会话历史合并共用，
// 设计会话绑定只校验归属（user_id），不再需要入口哨兵。

// ── Types ──────────────────────────────────────────────────────────

export interface AsBotMessage {
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  // 工具调用
  tool_calls?: Array<{
    step: number;
    tool_call_id?: string;
    tool: string;
    arguments?: Record<string, any>;
    result?: string;
    error?: string;
  }>;
  // 进度
  progressStages?: Array<{
    stage: string;
    message: string;
    timestamp: number;
  }>;
  error?: string;
}

export interface AsBotConversation {
  id: number;
  title: string;
  workspace_id: number;
  datasource_id: number;
  as_bot_key?: string;
  created_at: string;
  updated_at: string;
}

// AS-BOT 继承当前角色的 AS-BOT（与问数页面一致）

// 从会话首条用户消息派生标题(与主聊天一致)
function deriveTitle(messages: AsBotMessage[]): string {
  const firstUser = messages.find(m => m.role === 'user');
  if (firstUser) {
    const t = firstUser.content.slice(0, 30);
    return t.length < firstUser.content.length ? t + '...' : t;
  }
  return '新对话';
}

// 持久化前裁剪:工具结果/进度按长度截断, 避免 messages blob 膨胀(转录正文保留)
function slimForStore(messages: AsBotMessage[]): AsBotMessage[] {
  return messages.map(m => ({
    role: m.role,
    content: m.content,
    thinking: m.thinking ? m.thinking.slice(0, 8000) : undefined,
    error: m.error,
    tool_calls: (m.tool_calls || []).map(tc => ({
      ...tc,
      result: tc.result ? tc.result.slice(0, 4000) : tc.result,
      error: tc.error ? tc.error.slice(0, 1000) : tc.error,
    })),
    progressStages: (m.progressStages || []).slice(-40),
  }));
}

interface AsBotState {
  // Panel state
  panelOpen: boolean;
  // Messages
  messages: AsBotMessage[];
  loading: boolean;
  loadingStep: string;
  abortController: AbortController | null;
  // Conversation history (persisted to adh_conversations; 两入口合并共用)
  conversationId: number | null;
  conversations: AsBotConversation[];
  executorSessionId: string | null;

  // Actions
  togglePanel: () => void;
  openPanel: () => void;
  closePanel: () => void;
  sendMessage: (text: string) => Promise<void>;
  cancelMessage: () => void;
  loadConversations: () => Promise<void>;
  newConversation: () => void;
  switchConversation: (convId: number) => Promise<void>;
  deleteConversation: (convId: number) => Promise<void>;
  clearMessages: () => void;
}

export const useAsBotStore = create<AsBotState>((set, get) => ({
  panelOpen: false,
  messages: [],
  loading: false,
  loadingStep: '',
  abortController: null,
  conversationId: null,
  conversations: [],
  executorSessionId: null,

  togglePanel: () => set(state => ({ panelOpen: !state.panelOpen })),
  openPanel: () => { set({ panelOpen: true }); void get().loadConversations(); },
  closePanel: () => set({ panelOpen: false }),

  clearMessages: () => get().newConversation(),

  newConversation: () => {
    get().abortController?.abort();
    set({ conversationId: null, messages: [], executorSessionId: null, loading: false, loadingStep: '', abortController: null });
  },

  loadConversations: async () => {
    try {
      const { data } = await client.get('/chat/conversations', { params: { workspace_id: 0 } });
      set({ conversations: Array.isArray(data) ? data : [] });
    } catch { toast.error('会话列表加载失败'); }
  },

  switchConversation: async (convId) => {
    get().abortController?.abort();
    const switching = new AbortController();
    set({ abortController: switching, loading: false });
    try {
      const { data } = await client.get(`/chat/conversations/${convId}`);
      if (get().abortController !== switching) return;
      const msgs = Array.isArray(data.messages) ? data.messages : [];
      set({ conversationId: convId, messages: msgs, executorSessionId: null, abortController: null });
    } catch {
      if (get().abortController !== switching) return;
      set({ abortController: null });
      toast.error('切换会话失败，当前会话未改变');
    }
  },

  deleteConversation: async (convId) => {
    try {
      await client.delete(`/chat/conversations/${convId}`);
      if (get().conversationId === convId) get().newConversation();
      set(state => {
        const conversations = state.conversations.filter(c => c.id !== convId);
        return state.conversationId === convId
          ? { conversations, conversationId: null, messages: [], executorSessionId: null }
          : { conversations };
      });
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : '删除会话失败，记录未移除');
    }
  },

  sendMessage: async (text: string) => {
    const state = get();
    if (state.loading || !text.trim()) return;

    const userMsg: AsBotMessage = { role: 'user', content: text };
    const currentMessages = state.messages;

    const abortController = new AbortController();
    const ownsRequest = () => get().abortController === abortController;
    set({
      loading: true,
      loadingStep: '正在分析...',
      messages: [...currentMessages, userMsg],
      abortController,
    });

    // 首次发送惰性建会话(空会话不落库); 建失败不阻断对话, 仅本轮不持久化
    let convId = get().conversationId;
    if (!convId) {
      try {
        const { data } = await client.post('/chat/conversations', {
          workspace_id: 0, datasource_id: 0,  // workspace_id=0 未指定 → 服务端解析为用户默认工作空间
        });
        if (!ownsRequest() || abortController.signal.aborted) return;
        convId = data.id;
        set(s => ({
          conversationId: data.id,
          conversations: [{ id: data.id, title: data.title, workspace_id: data.workspace_id || 0, datasource_id: 0,
            as_bot_key: data.as_bot_key || '', created_at: data.created_at, updated_at: data.created_at },
            ...s.conversations],
        }));
      } catch {
        if (ownsRequest()) {
          set(s => ({ loading: false, abortController: null, loadingStep: '',
            messages: [...s.messages, { role: 'assistant', content: '', error: '创建会话失败，消息未发送，请重试' }] }));
        }
        return;
      }
    }

    // 持久化当前转录到会话(标题取首条用户消息); 失败静默不阻断对话
    const persist = async () => {
      if (!convId || get().conversationId !== convId || abortController.signal.aborted) return;
      const msgs = get().messages;
      try {
        await client.put(`/chat/conversations/${convId}`, {
          messages: slimForStore(msgs),
          title: deriveTitle(msgs),
        });
        set(s => ({ conversations: s.conversations.map(c => c.id === convId ? { ...c, updated_at: new Date().toISOString() } : c) }));
      } catch { toast.error('会话记录保存失败，请重试'); }
    };

    const history = currentMessages.map(m => ({
      role: m.role,
      content: m.content,
    }));

    const token = localStorage.getItem('token');

    const requestBody = {
      question: text,
      history,
      pipeline_mode: 'agent',
      workspace_id: 0,
      conversation_id: convId || 0,
    };

    try {
      const response = await fetch('/api/pipeline/send/stream', {
        method: 'POST',
        signal: abortController.signal,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(requestBody),
      });

      if (!ownsRequest()) { await response.body?.cancel(); return; }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let streamingText = '';
      let streamingThinking = '';

      // Insert empty assistant message for streaming
      const streamingMsg: AsBotMessage = { role: 'assistant', content: '' };
      set(state => ({ messages: [...state.messages, streamingMsg] }));

      let currentEvent = '';
      let dataBuffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (!ownsRequest()) { await reader.cancel(); return; }
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            dataBuffer = line.slice(6);
          } else if (line === '' && dataBuffer) {
            try {
              const data = JSON.parse(dataBuffer);

              if (currentEvent === 'progress') {
                set(state => {
                  const msgs = [...state.messages];
                  const lastIdx = msgs.length - 1;
                  if (msgs[lastIdx]?.role === 'assistant') {
                    const prev = msgs[lastIdx].progressStages || [];
                    msgs[lastIdx] = {
                      ...msgs[lastIdx],
                      progressStages: [...prev, {
                        stage: data.stage || '',
                        message: data.message || '',
                        timestamp: Date.now(),
                      }],
                    };
                  }
                  return { messages: msgs, loadingStep: data.message || '' };
                });
              } else if (currentEvent === 'thinking') {
                streamingThinking += data.text;
                set(state => {
                  const msgs = [...state.messages];
                  const lastIdx = msgs.length - 1;
                  if (msgs[lastIdx]?.role === 'assistant') {
                    msgs[lastIdx] = { ...msgs[lastIdx], thinking: streamingThinking };
                  }
                  return { messages: msgs };
                });
              } else if (currentEvent === 'token') {
                streamingText += data.text;
                set(state => {
                  const msgs = [...state.messages];
                  const lastIdx = msgs.length - 1;
                  if (msgs[lastIdx]?.role === 'assistant') {
                    msgs[lastIdx] = { ...msgs[lastIdx], content: streamingText };
                  }
                  return { messages: msgs };
                });
              } else if (currentEvent === 'tool_start') {
                set(state => {
                  const msgs = [...state.messages];
                  const lastIdx = msgs.length - 1;
                  if (msgs[lastIdx]?.role === 'assistant') {
                    const prev = msgs[lastIdx].tool_calls || [];
                    msgs[lastIdx] = {
                      ...msgs[lastIdx],
                      tool_calls: [...prev, {
                        step: prev.length + 1,
                        tool_call_id: data.tool_call_id,
                        tool: data.tool,
                        arguments: data.arguments,
                      }],
                    };
                  }
                  return { messages: msgs };
                });
              } else if (currentEvent === 'tool_result') {
                set(state => {
                  const msgs = [...state.messages];
                  const lastIdx = msgs.length - 1;
                  if (msgs[lastIdx]?.role === 'assistant') {
                    const updated = (msgs[lastIdx].tool_calls || []).map(tc =>
                      tc.tool_call_id && tc.tool_call_id === data.tool_call_id
                        ? { ...tc, result: data.output, error: data.error }
                        : tc
                    );
                    msgs[lastIdx] = { ...msgs[lastIdx], tool_calls: updated };
                  }
                  return { messages: msgs };
                });
              } else if (currentEvent === 'done') {
                set(state => {
                  const msgs = [...state.messages];
                  const lastIdx = msgs.length - 1;
                  if (msgs[lastIdx]?.role === 'assistant') {
                    msgs[lastIdx] = {
                      ...msgs[lastIdx],
                      content: data.error ? '' : (data.reply || streamingText),
                      thinking: data.thinking || streamingThinking,
                      error: data.error,
                      tool_calls: data.tool_calls || msgs[lastIdx].tool_calls,
                    };
                  }
                  return { messages: msgs, loading: false, loadingStep: '' };
                });

                set({ executorSessionId: null });
                void persist();
              } else if (currentEvent === 'error') {
                throw new Error(data.message);
              }
            } catch (e: any) {
              if (currentEvent === 'error' || currentEvent === 'done' || !currentEvent) {
                // Only throw for actual errors
                if (currentEvent === 'error') {
                  // 解析错误消息：优先从 JSON 中提取 message 字段
                  let errorMsg = e.message || '请求失败';
                  try {
                    const parsed = JSON.parse(dataBuffer);
                    errorMsg = parsed.message || errorMsg;
                  } catch {
                    errorMsg = dataBuffer || errorMsg;
                  }
                  set(state => {
                    const msgs = [...state.messages];
                    const lastIdx = msgs.length - 1;
                    if (msgs[lastIdx]?.role === 'assistant') {
                      msgs[lastIdx] = { ...msgs[lastIdx], error: errorMsg };
                    }
                    return { messages: msgs, loading: false, loadingStep: '', abortController: null };
                  });
                  void persist();
                  return;
                }
              }
            } finally {
              dataBuffer = '';
              currentEvent = '';
            }
          }
        }
      }

      // Stream ended without done event
      if (get().loading) {
        set(state => {
          const msgs = [...state.messages];
          const lastIdx = msgs.length - 1;
          if (msgs[lastIdx]?.role === 'assistant' && !msgs[lastIdx].content) {
            msgs[lastIdx] = {
              ...msgs[lastIdx],
              content: streamingText || '',
              error: '响应异常: 未收到完成事件',
            };
          }
          return { messages: msgs, loading: false, loadingStep: '', abortController: null };
        });
      }
      void persist();
    } catch (e: any) {
      if (!ownsRequest()) return;
      if (e.name === 'AbortError') {
        set(state => {
          const msgs = [...state.messages];
          const lastIdx = msgs.length - 1;
          if (msgs[lastIdx]?.role === 'assistant') {
            msgs[lastIdx] = { ...msgs[lastIdx], content: msgs[lastIdx].content || '(已取消)' };
          }
          return { messages: msgs, loading: false, loadingStep: '', abortController: null };
        });
        void persist();
        return;
      }
      const errorMsg: AsBotMessage = {
        role: 'assistant',
        content: '',
        error: e.message || '请求失败',
      };
      set(state => ({
        messages: [...state.messages, errorMsg],
        loading: false,
        loadingStep: '',
        abortController: null,
      }));
      void persist();
    }
  },

  cancelMessage: () => {
    const { abortController } = get();
    if (abortController) {
      abortController.abort();
    }
  },
}));
