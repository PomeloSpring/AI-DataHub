import { create } from 'zustand';
import client from '../api/client';
import { aiAssistantExecutor } from '../utils/aiAssistantExecutor';

// ── Types ────────────────────────────────────────────────────────────

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;  // 思考过程
  timestamp: Date;
  sources?: SourceInfo[];
}

interface SourceInfo {
  id: string;
  title: string;
  content: string;
  source: string;
  relevance: number;
}

interface PageContext {
  page: string;
  module: string;
  subModule?: string;
}

interface KnowledgeStatus {
  document_count: number;
  chunk_count: number;
  vector_count: number;
  last_sync: string | null;
  sync_status: string;
  sources: any[];
}

interface Document {
  id: string;
  title: string;
  doc_type: string;
  source: string;
  size: string;
  created_at: string;
  updated_at: string;
  tags: string[];
}

interface AIAssistantState {
  // UI状态
  isOpen: boolean;
  isLoading: boolean;

  // 聊天状态
  messages: Message[];
  sessionId: string | null;
  abortController: AbortController | null;

  // 上下文
  currentContext: PageContext | null;

  // 知识库
  knowledgeStatus: KnowledgeStatus | null;
  documents: Document[];

  // Actions
  toggleBox: () => void;
  openBox: () => void;
  closeBox: () => void;
  updateContext: (context: PageContext) => void;
  sendMessage: (message: string, context?: PageContext | null) => Promise<void>;
  stopGeneration: () => void;
  clearMessages: () => void;
  loadKnowledgeStatus: () => Promise<void>;
  loadDocuments: () => Promise<void>;
  updateKnowledge: (source: string, force: boolean) => Promise<void>;
  deleteDocument: (docId: string) => Promise<void>;
}

// ── Store ────────────────────────────────────────────────────────────

export const useAIAssistantStore = create<AIAssistantState>((set, get) => ({
  // 初始状态
  isOpen: false,
  isLoading: false,
  messages: [],
  sessionId: null,
  abortController: null,
  currentContext: null,
  knowledgeStatus: null,
  documents: [],

  // 切换对话框
  toggleBox: () => {
    set((state) => ({ isOpen: !state.isOpen }));
  },

  // 打开对话框
  openBox: () => {
    set({ isOpen: true });
  },

  // 关闭对话框
  closeBox: () => {
    set({ isOpen: false });
  },

  // 更新上下文
  updateContext: (context: PageContext) => {
    set({ currentContext: context });
  },

  // 判断是否需要工具
  _needsTools: (message: string): boolean => {
    const messageLower = message.toLowerCase();

    // 明确的操作意图关键词
    const actionKeywords = ['帮我', '请帮我', '帮忙', '协助我', '我要', '我想', '我需要'];
    // 需要使用工具的关键词
    const toolKeywords = ['创建', '新建', '添加', '配置', '设置', '打开', '跳转', '填写', '保存', '提交', '删除', '编辑', '修改'];
    // 工具对象关键词
    const toolObjects = ['数据源', '定时任务', '通知', '工作流', 'agent', '报表', '渠道', '模板', '用户', '权限'];
    // 询问类关键词
    const questionKeywords = ['是什么', '什么是', '怎么理解', '如何理解', '为什么', '哪个页面', '当前页面', '现在在', '这是哪', '帮助', '说明', '介绍', '解释', '文档', '怎么用', '如何使用', '怎么配置', '如何配置'];

    // 如果是纯粹的询问类问题，不需要工具
    if (questionKeywords.some(kw => messageLower.includes(kw))) {
      // 但如果同时包含明确的操作意图，仍然需要工具
      if (actionKeywords.some(kw => messageLower.includes(kw))) {
        return true;
      }
      return false;
    }

    // 如果包含操作关键词 + 工具对象，需要工具
    const hasToolKeyword = toolKeywords.some(kw => messageLower.includes(kw));
    const hasToolObject = toolObjects.some(kw => messageLower.includes(kw));

    if (hasToolKeyword && hasToolObject) {
      return true;
    }

    return false;
  },

  // 发送消息（支持流式响应和思考过程）
  sendMessage: async (message: string, context?: PageContext | null) => {
    const { sessionId, messages, _needsTools } = get();

    // 判断是否需要工具
    const needsTools = _needsTools(message);

    // 创建AbortController
    const abortController = new AbortController();
    set({ abortController });

    // 添加用户消息
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: message,
      timestamp: new Date()
    };

    // 创建一个空的AI消息用于流式更新
    const aiMessageId = (Date.now() + 1).toString();
    const aiMessage: Message = {
      id: aiMessageId,
      role: 'assistant',
      content: '',
      thinking: '',
      timestamp: new Date()
    };

    set({
      messages: [...messages, userMessage, aiMessage],
      isLoading: true
    });

    try {
      if (needsTools) {
        // 需要工具时，使用非流式接口
        const response = await client.post('/ai-assistant/chat', {
          message,
          context: context || undefined,
          session_id: sessionId || undefined,
          module: context?.module || undefined
        });

        const data = response.data;

        // 更新sessionId
        if (data.session_id) {
          set({ sessionId: data.session_id });
        }

        // 更新消息内容
        set((state) => ({
          messages: state.messages.map(msg =>
            msg.id === aiMessageId
              ? {
                  ...msg,
                  content: data.message || '',
                  toolCalls: data.tool_calls,
                  pendingActions: data.pending_actions
                }
              : msg
          ),
          isLoading: false,
          abortController: null
        }));

        // 如果有待执行的操作，立即执行
        if (data.pending_actions && data.pending_actions.length > 0) {
          console.log('Executing pending actions:', data.pending_actions);
          await aiAssistantExecutor.fetchPendingActions();
          await aiAssistantExecutor.executeAllActions();
        }
      } else {
        // 不需要工具时，使用流式接口
        const response = await fetch('/api/ai-assistant/chat/stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token')}`
          },
          body: JSON.stringify({
            message,
            context: context || undefined,
            session_id: sessionId || undefined,
            module: context?.module || undefined
          }),
          signal: abortController.signal
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        // 从响应头获取sessionId
        const newSessionId = response.headers.get('X-Session-Id') || sessionId;

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('No reader available');
        }

        const decoder = new TextDecoder();
        let buffer = '';
        let currentContent = '';
        let currentThinking = '';
        let isThinking = false;

        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            break;
          }

          buffer += decoder.decode(value, { stream: true });

          // 处理SSE格式
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);

              if (data === '[DONE]') {
                break;
              }

              try {
                const parsed = JSON.parse(data);

                if (parsed.session_id) {
                  set({ sessionId: parsed.session_id });
                }

                if (parsed.type === 'thinking_start') {
                  isThinking = true;
                  currentThinking = '';
                } else if (parsed.type === 'thinking_end') {
                  isThinking = false;
                } else if (parsed.type === 'thinking' && parsed.content) {
                  currentThinking += parsed.content;
                  set((state) => ({
                    messages: state.messages.map(msg =>
                      msg.id === aiMessageId
                        ? { ...msg, thinking: currentThinking }
                        : msg
                    )
                  }));
                } else if (parsed.message) {
                  currentContent += parsed.message;
                  set((state) => ({
                    messages: state.messages.map(msg =>
                      msg.id === aiMessageId
                        ? { ...msg, content: currentContent }
                        : msg
                    )
                  }));
                }
              } catch {
                if (isThinking) {
                  currentThinking += data;
                  set((state) => ({
                    messages: state.messages.map(msg =>
                      msg.id === aiMessageId
                        ? { ...msg, thinking: currentThinking }
                        : msg
                    )
                  }));
                } else {
                  currentContent += data;
                  set((state) => ({
                    messages: state.messages.map(msg =>
                      msg.id === aiMessageId
                        ? { ...msg, content: currentContent }
                        : msg
                    )
                  }));
                }
              }
            }
          }
        }

        if (newSessionId) {
          set({ sessionId: newSessionId });
        }

        set({ isLoading: false, abortController: null });
      }
    } catch (error: any) {
      if (error.name === 'AbortError') {
        set((state) => ({
          messages: state.messages.map(msg =>
            msg.id === aiMessageId
              ? { ...msg, content: msg.content || '（已终止）' }
              : msg
          ),
          isLoading: false,
          abortController: null
        }));
        return;
      }

      console.error('Send message error:', error);

      set((state) => ({
        messages: state.messages.map(msg =>
          msg.id === aiMessageId
            ? {
                ...msg,
                content: `抱歉，处理请求时出现错误: ${error.message || '未知错误'}`
              }
            : msg
        ),
        isLoading: false,
        abortController: null
      }));
    }
  },

  // 终止生成
  stopGeneration: () => {
    const { abortController } = get();
    if (abortController) {
      abortController.abort();
      set({ abortController: null, isLoading: false });
    }
  },

  // 清空消息
  clearMessages: () => {
    set({
      messages: [],
      sessionId: null
    });
  },

  // 加载知识库状态
  loadKnowledgeStatus: async () => {
    try {
      const response = await client.get('/ai-assistant/knowledge/status');
      set({ knowledgeStatus: response.data });
    } catch (error) {
      console.error('Load knowledge status error:', error);
    }
  },

  // 加载文档列表
  loadDocuments: async () => {
    try {
      const response = await client.get('/ai-assistant/knowledge/documents');
      set({ documents: response.data.documents || [] });
    } catch (error) {
      console.error('Load documents error:', error);
    }
  },

  // 更新知识库
  updateKnowledge: async (source: string, force: boolean) => {
    set({ isLoading: true });
    try {
      await client.post('/ai-assistant/knowledge/update', {
        source,
        force
      });

      // 重新加载状态
      await get().loadKnowledgeStatus();
      await get().loadDocuments();

    } catch (error) {
      console.error('Update knowledge error:', error);
    } finally {
      set({ isLoading: false });
    }
  },

  // 删除文档
  deleteDocument: async (docId: string) => {
    try {
      await client.delete(`/ai-assistant/knowledge/documents/${docId}`);

      // 重新加载
      await get().loadKnowledgeStatus();
      await get().loadDocuments();

    } catch (error) {
      console.error('Delete document error:', error);
    }
  },

  // 上传文档
  uploadDocument: async (file: File, title?: string, docType?: string, tags?: string[]) => {
    set({ isLoading: true });
    try {
      const formData = new FormData();
      formData.append('file', file);

      if (title) {
        formData.append('title', title);
      }
      if (docType) {
        formData.append('doc_type', docType);
      }
      if (tags && tags.length > 0) {
        formData.append('tags', tags.join(','));
      }

      const response = await client.post('/ai-assistant/knowledge/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });

      // 重新加载
      await get().loadKnowledgeStatus();
      await get().loadDocuments();

      return response.data;

    } catch (error) {
      console.error('Upload document error:', error);
      throw error;
    } finally {
      set({ isLoading: false });
    }
  },

  // 批量上传文档
  uploadMultipleDocuments: async (files: File[], docType?: string, tags?: string[]) => {
    set({ isLoading: true });
    try {
      const formData = new FormData();

      files.forEach(file => {
        formData.append('files', file);
      });

      if (docType) {
        formData.append('doc_type', docType);
      }
      if (tags && tags.length > 0) {
        formData.append('tags', tags.join(','));
      }

      const response = await client.post('/ai-assistant/knowledge/upload-multiple', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });

      // 重新加载
      await get().loadKnowledgeStatus();
      await get().loadDocuments();

      return response.data;

    } catch (error) {
      console.error('Upload multiple documents error:', error);
      throw error;
    } finally {
      set({ isLoading: false });
    }
  }
}));
