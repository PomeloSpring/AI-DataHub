import { create } from 'zustand';
import client from '../api/client';

export interface ProgressStage {
  stage: string;
  message: string;
  timestamp: number;
  elapsed?: number;  // Step elapsed time in seconds
  step?: number;     // Step number (for agent_exec matching)
}

export interface ToolCall {
  step?: number;
  tool: string;
  arguments?: Record<string, any>;
  result?: string;
  result_preview?: string;
  error?: string;
  elapsed?: number;
}

export interface AttachmentInfo {
  id: string;
  filename: string;
  category: 'image' | 'table' | 'document' | 'model3d';
  size?: number;
  url?: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  attachments?: AttachmentInfo[];  // Multimodal attachments uploaded with this message
  question?: string;  // Original user question (for assistant messages)
  intent?: string;
  reply?: string;
  sql?: string;
  warnings?: string[];
  thinking?: string;
  rag?: any;
  result?: any;
  error?: string;
  chart_type?: string;
  brief?: string;
  tokens?: { input: number; output: number; total: number };
  elapsed_ms?: number;
  viewMode?: 'chart' | 'table' | 'sql';
  ai_raw_response?: string;
  timings?: Record<string, number>;
  analysis?: string;
  prediction?: string;
  analyzing?: boolean;
  predicting?: boolean;
  feedback?: 'up' | 'down';  // User feedback on this result
  expected_table?: string;   // Expected table from negative feedback
  progressStages?: ProgressStage[];  // Workflow execution stage history
  activeStage?: string;              // Current active stage key
  workflow_info?: any;               // Deep mode workflow info
  tool_calls?: ToolCall[];           // Agent tool call history
  pendingAsk?: {                     // Agent ask_user interactive state
    request_id: string;
    question: string;
    options: string[];
  };
}

interface Conversation {
  id: number;
  title: string;
  datasource_id: number;
  workspace_id?: number;
  created_at: string;
  updated_at: string;
}

interface LLMModel {
  id: number;
  name: string;
  provider: string;
  model_name: string;
  is_default: number;
}

interface Workflow {
  id: number;
  name: string;
  description: string;
  is_default: boolean;
}

export interface WorkspaceExecutionLayer {
  layer_id: number;
  name: string;
  display_name: string;
  layer_type: string;  // builtin | cli | docker | remote
  allowed_tools: string[];
  model_source: 'system' | 'execution_layer';
  models: string[];  // cli 执行层的模型候选(如 provider/model_name)
}

interface ChatState {
  conversations: Conversation[];
  currentConvId: number | null;
  messages: ChatMessage[];
  loading: boolean;
  loadingStep: string;
  abortController: AbortController | null;
  selectedDsId: number;
  datasources: { id: number; name: string; db_type: string }[];
  selectedModelId: number | null;
  llmModels: LLMModel[];
  useLoopEngine: boolean;
  selectedWorkflowId: number | null;
  workflows: Workflow[];
  pipelineMode: 'quick' | 'deep' | 'agent' | null;  // null = use legacy endpoints
  retrievalStrategy: string;  // hybrid, full_table, column_first, two_stage, bidirectional, graph

  // Workspace state (for Agent mode)
  selectedWorkspaceId: number;
  workspaces: any[];
  workspaceConfig: {
    allowed_retrieval_strategies?: string[];
    allowed_pipeline_modes?: string[];
  };

  // 工作空间绑定的执行层(每工作空间至多一个)与运行时模型选择
  executionLayer: WorkspaceExecutionLayer | null;
  selectedModelRef: string | null;
  // 执行层 SDK 会话 ID(多轮对话 resume,done 事件回传)
  executorSessionId: string | null;

  // MCP tools state
  mcpServers: any[];

  loadConversations: () => Promise<void>;
  loadDatasources: () => Promise<void>;
  loadLLMModels: () => Promise<void>;
  loadWorkflows: () => Promise<void>;
  loadSystemConfig: () => Promise<void>;
  loadWorkspaces: () => Promise<void>;
  loadWorkspaceConfig: (workspaceId: number) => Promise<void>;
  loadExecutionLayer: (workspaceId: number) => Promise<void>;
  setSelectedModelRef: (ref: string | null) => void;
  setSelectedDsId: (id: number) => void;
  setSelectedModelId: (id: number | null) => void;
  setUseLoopEngine: (use: boolean) => void;
  setSelectedWorkflowId: (id: number | null) => void;
  setPipelineMode: (mode: 'quick' | 'deep' | 'agent' | null) => void;
  setRetrievalStrategy: (strategy: string) => void;
  setSelectedWorkspaceId: (id: number) => void;
  loadMcpTools: () => Promise<void>;
  createConversation: () => Promise<number>;
  switchConversation: (convId: number) => Promise<void>;
  deleteConversation: (convId: number) => Promise<void>;
  renameConversation: (convId: number, title: string) => Promise<void>;
  sendMessage: (question: string, mcpTools?: string[], attachments?: AttachmentInfo[]) => Promise<void>;
  uploadAttachment: (files: File[], workspaceId?: number) => Promise<AttachmentInfo[]>;
  cancelMessage: () => void;
  respondToAsk: (requestId: string, response: string) => Promise<void>;
  cancelAsk: (requestId: string) => void;
  updateMessageFeedback: (idx: number, feedback: 'up' | 'down', expectedTable?: string) => void;
  setViewMode: (idx: number, mode: 'chart' | 'table' | 'sql') => void;
  analyzeData: (msgIdx: number, question: string) => Promise<void>;
  predictData: (msgIdx: number, question: string) => Promise<void>;
  clear: () => Promise<void>;
}

export async function saveMessages(convId: number, messages: ChatMessage[], title?: string) {
  const slimMessages = messages.map(m => {
    const slim: any = { ...m };
    // Keep full result (SQL already has LIMIT 1000)
    if (slim.result) {
      slim.result = {
        columns: slim.result.columns,
        row_count: slim.result.row_count,
        elapsed_ms: slim.result.elapsed_ms,
        rows: slim.result.rows || [],
      };
    }
    // Keep RAG detail arrays for inline details
    if (slim.rag) {
      slim.rag = {
        rag_source: slim.rag.rag_source,
        table_info: slim.rag.table_info,
        table_info_count: slim.rag.table_info_count,
        column_metadata: slim.rag.column_metadata,
        column_metadata_count: slim.rag.column_metadata_count,
        sql_templates: slim.rag.sql_templates,
        sql_templates_count: slim.rag.sql_templates_count,
        business_terms: slim.rag.business_terms,
        business_terms_count: slim.rag.business_terms_count,
        datasets_count: slim.rag.datasets_count,
      };
    }
    return slim;
  });
  const payload: any = { messages: slimMessages };
  if (title) payload.title = title;
  try {
    await client.put(`/chat/conversations/${convId}`, payload);
  } catch (e) {
    console.error('Failed to save conversation:', e);
  }
}

export function deriveTitle(messages: ChatMessage[]): string | undefined {
  const firstUser = messages.find(m => m.role === 'user');
  if (firstUser) {
    const t = firstUser.content.slice(0, 30);
    return t.length < firstUser.content.length ? t + '...' : t;
  }
  return undefined;
}

export const useChatStore = create<ChatState>((set, get) => ({
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
  pipelineMode: 'quick',
  retrievalStrategy: 'hybrid',
  mcpServers: [],

  // Workspace state
  selectedWorkspaceId: 0,
  workspaces: [],
  workspaceConfig: {},
  executionLayer: null,
  selectedModelRef: null,
  executorSessionId: null,

  loadMcpTools: async () => {
    try {
      const { data } = await client.get('/chat/mcp-tools');
      set({ mcpServers: data.servers || [] });
    } catch {
      // silently fail - MCP is optional
    }
  },

  loadWorkspaces: async () => {
    try {
      const { data } = await client.get('/workspaces');
      set({ workspaces: data || [] });
      // Auto-select default workspace
      if (data.length > 0 && !get().selectedWorkspaceId) {
        const defaultWs = data.find((w: any) => w.is_default) || data[0];
        set({ selectedWorkspaceId: defaultWs.id });
      }
    } catch {
      // silently fail - workspaces are optional
    }
  },

  setSelectedWorkspaceId: (id: number) => {
    set({ selectedWorkspaceId: id });
  },

  loadWorkspaceConfig: async (workspaceId: number) => {
    try {
      const { data } = await client.get(`/workspaces/${workspaceId}`);
      const config = data?.config || {};
      set({ workspaceConfig: config });
      // Validate pipelineMode against allowed modes
      const modes = config.allowed_pipeline_modes;
      if (Array.isArray(modes) && modes.length > 0) {
        const current = get().pipelineMode;
        if (!current || !modes.includes(current)) {
          set({ pipelineMode: modes[0] as 'quick' | 'deep' | 'agent' });
        }
      }
    } catch {
      set({ workspaceConfig: {} });
    }
  },

  loadExecutionLayer: async (workspaceId: number) => {
    // 工作空间生效的执行层(未绑定时后端回退内置层),含模型候选
    try {
      const { data } = await client.get(`/admin/execution-layers/workspaces/${workspaceId}/execution-layer`);
      set({ executionLayer: data, selectedModelRef: null, executorSessionId: null });
    } catch {
      set({ executionLayer: null, selectedModelRef: null, executorSessionId: null });
    }
  },

  setSelectedModelRef: (ref) => set({ selectedModelRef: ref }),

  loadConversations: async () => {
    try {
      const workspaceId = get().selectedWorkspaceId;
      const params = workspaceId ? `?workspace_id=${workspaceId}` : '';
      const { data } = await client.get(`/chat/conversations${params}`);
      set({ conversations: data });
    } catch {}
  },

  loadDatasources: async () => {
    try {
      const { data } = await client.get('/datasources/');
      set({ datasources: data });
      // Auto-select default datasource
      if (data.length > 0 && !get().selectedDsId) {
        const defaultDs = data.find((d: any) => d.is_default) || data[0];
        set({ selectedDsId: defaultDs.id });
      }
    } catch {}
  },

  setSelectedDsId: (id) => set({ selectedDsId: id }),

  loadLLMModels: async () => {
    try {
      const { data } = await client.get('/model-config/llm');
      set({ llmModels: data });
      // Auto-select default model
      if (data.length > 0 && !get().selectedModelId) {
        const defaultModel = data.find((m: any) => m.is_default) || data[0];
        set({ selectedModelId: defaultModel.id });
      }
    } catch {}
  },

  loadSystemConfig: async () => {
    try {
      const { data } = await client.get('/model-config/system');
      if (data?.retrieval_strategy) {
        set({ retrievalStrategy: data.retrieval_strategy });
      }
    } catch {}
  },

  setSelectedModelId: (id) => set({ selectedModelId: id }),
  setUseLoopEngine: (use) => set({ useLoopEngine: use }),
  setSelectedWorkflowId: (id) => set({ selectedWorkflowId: id }),
  setPipelineMode: (mode) => set({ pipelineMode: mode }),
  setRetrievalStrategy: (strategy) => set({ retrievalStrategy: strategy }),

  loadWorkflows: async () => {
    try {
      const { data } = await client.get('/admin/workflows');
      const workflows = data.items || [];
      set({ workflows });
      // Auto-select default workflow
      if (workflows.length > 0 && !get().selectedWorkflowId) {
        const defaultWorkflow = workflows.find((w: Workflow) => w.is_default) || workflows[0];
        set({ selectedWorkflowId: defaultWorkflow.id });
      }
    } catch {}
  },

  createConversation: async () => {
    const dsId = get().selectedDsId;
    const workspaceId = get().selectedWorkspaceId;
    const { data } = await client.post('/chat/conversations', {
      datasource_id: dsId,
      workspace_id: workspaceId,
    });
    const conv: Conversation = {
      id: data.id,
      title: data.title,
      datasource_id: data.datasource_id || dsId,
      workspace_id: data.workspace_id || workspaceId,
      created_at: data.created_at,
      updated_at: data.created_at,
    };
    set(state => ({ conversations: [conv, ...state.conversations], currentConvId: conv.id, messages: [], executorSessionId: null }));
    return data.id;
  },

  switchConversation: async (convId) => {
    try {
      const { data } = await client.get(`/chat/conversations/${convId}`);
      const msgs = Array.isArray(data.messages) ? data.messages : [];
      set({
        currentConvId: convId,
        messages: msgs,
        executorSessionId: null,  // 切换会话后执行层会话重新开始
        // Don't override selectedDsId — keep user's dropdown selection
      });
    } catch (e) {
      console.error('Failed to load conversation:', e);
      set({ currentConvId: convId, messages: [], executorSessionId: null });
    }
  },

  deleteConversation: async (convId) => {
    try {
      await client.delete(`/chat/conversations/${convId}`);
      set(state => {
        const conversations = state.conversations.filter(c => c.id !== convId);
        const isCurrent = state.currentConvId === convId;
        return {
          conversations,
          currentConvId: isCurrent ? null : state.currentConvId,
          messages: isCurrent ? [] : state.messages,
          executorSessionId: isCurrent ? null : state.executorSessionId,
        };
      });
    } catch {}
  },

  renameConversation: async (convId, title) => {
    try {
      await client.put(`/chat/conversations/${convId}`, { title });
      set(state => ({
        conversations: state.conversations.map(c =>
          c.id === convId ? { ...c, title } : c
        ),
      }));
    } catch {}
  },

  uploadAttachment: async (files, workspaceId) => {
    const formData = new FormData();
    files.forEach(f => formData.append('files', f));
    formData.append('workspace_id', String(workspaceId ?? get().selectedWorkspaceId ?? 0));
    const resp = await client.post('/chat/attachments/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return resp.data.attachments || [];
  },

  sendMessage: async (question, mcpTools, attachments) => {
    const state = get();
    let convId = state.currentConvId;

    if (!convId) {
      convId = await get().createConversation();
    }

    // Helper: check if we're still in the same conversation
    const isSameConv = () => get().currentConvId === convId;

    const userMsg: ChatMessage = { role: 'user', content: question, ...(attachments?.length ? { attachments } : {}) };
    const currentMessages = state.messages;

    // Create abort controller for cancellation
    const abortController = new AbortController();
    set({ loading: true, loadingStep: '正在分析意图...', messages: [...currentMessages, userMsg], abortController });

    const history = currentMessages.map(m => ({
      role: m.role,
      content: m.content,
      sql: m.sql,
      result: m.result ? { row_count: m.result.row_count, elapsed_ms: m.result.elapsed_ms } : undefined,
      feedback: m.feedback,
      expected_table: m.expected_table,
    }));

    const startTime = Date.now();
    const token = localStorage.getItem('token');

    // Choose API endpoint: pipeline > loop engine > default
    let apiEndpoint: string;
    const requestBody: any = {
      question,
      history,
      datasource_id: state.selectedDsId,
      model_id: state.selectedModelId,
      mcp_tools: mcpTools || [],
      workspace_id: state.selectedWorkspaceId,
      attachments: (attachments || []).map(a => a.id),
    };

    if (state.pipelineMode) {
      // Use pipeline endpoint with mode selection
      apiEndpoint = '/api/pipeline/send/stream';
      requestBody.pipeline_mode = state.pipelineMode;
      requestBody.retrieval_strategy = state.retrievalStrategy;
      if (state.selectedWorkflowId) {
        requestBody.workflow_id = state.selectedWorkflowId;
      }
      // Deep(内置 Agent)与 Agent(执行层)模式都需要工作空间上下文
      if ((state.pipelineMode === 'agent' || state.pipelineMode === 'deep') && state.selectedWorkspaceId) {
        requestBody.workspace_id = state.selectedWorkspaceId;
      }
      if (state.pipelineMode === 'agent') {
        delete requestBody.datasource_id;  // 执行层自带数据源选择(execute_sql 工具)
      }
      // CLI 执行层(qoder/opencode):运行时选择的模型以 model_ref 透传,
      // 上一轮会话 ID 以 session_id 回传实现 SDK 多轮对话
      if (state.executionLayer?.layer_type === 'cli' && state.selectedModelRef) {
        requestBody.model_ref = state.selectedModelRef;
      }
      if (state.executorSessionId) {
        requestBody.session_id = state.executorSessionId;
      }
    } else if (state.useLoopEngine) {
      apiEndpoint = '/api/chat/send/loop/stream';
      if (state.selectedWorkflowId) {
        requestBody.workflow_id = state.selectedWorkflowId;
      }
    } else {
      apiEndpoint = '/api/chat/send/stream';
    }

    try {
      const response = await fetch(apiEndpoint, {
        method: 'POST',
        signal: abortController.signal,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let streamingText = '';
      let streamingThinking = '';
      let doneData: any = null;

      // Insert empty assistant message for streaming (only if still same conversation)
      if (isSameConv()) {
        const streamingMsg: ChatMessage = { role: 'assistant', content: '', thinking: '' };
        set({ messages: [...get().messages, streamingMsg] });
      }

      let currentEvent = '';
      let dataBuffer = '';

      while (true) {
        const { done, value } = await reader.read();
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
            // Empty line after data: means end of SSE event — process it
            try {
              const data = JSON.parse(dataBuffer);
              console.debug('[SSE]', currentEvent, data);

              if (currentEvent === 'progress') {
                if (isSameConv()) {
                  set({ loadingStep: data.message });
                  // Track progress stages on the streaming message
                  set(state => {
                    const msgs = [...state.messages];
                    const lastIdx = msgs.length - 1;
                    if (msgs[lastIdx]?.role === 'assistant') {
                      const prev = msgs[lastIdx].progressStages || [];
                      const stage = data.stage || '';
                      const entry = { stage, message: data.message || '', timestamp: Date.now(), elapsed: data.elapsed, step: data.step };
                      msgs[lastIdx] = {
                        ...msgs[lastIdx],
                        progressStages: [...prev, entry],
                        activeStage: stage,
                      };
                    }
                    return { messages: msgs };
                  });
                }
              } else if (currentEvent === 'thinking') {
                streamingThinking += data.text;
                if (isSameConv()) {
                  set(state => {
                    const msgs = [...state.messages];
                    const lastIdx = msgs.length - 1;
                    if (msgs[lastIdx]?.role === 'assistant') {
                      msgs[lastIdx] = { ...msgs[lastIdx], thinking: streamingThinking };
                    }
                    return { messages: msgs };
                  });
                }
              } else if (currentEvent === 'token') {
                streamingText += data.text;
                if (isSameConv()) {
                  set(state => {
                    const msgs = [...state.messages];
                    const lastIdx = msgs.length - 1;
                    if (msgs[lastIdx]?.role === 'assistant') {
                      msgs[lastIdx] = { ...msgs[lastIdx], content: streamingText };
                    }
                    return { messages: msgs };
                  });
                }
              } else if (currentEvent === 'ask_user') {
                // Agent is asking the user a question — show interactive card
                if (isSameConv()) {
                  set(state => {
                    const msgs = [...state.messages];
                    const lastIdx = msgs.length - 1;
                    if (msgs[lastIdx]?.role === 'assistant') {
                      msgs[lastIdx] = {
                        ...msgs[lastIdx],
                        pendingAsk: {
                          request_id: data.request_id,
                          question: data.question,
                          options: data.options || [],
                        },
                      };
                    }
                    return { messages: msgs, loading: false, loadingStep: '' };
                  });
                }
                // Keep reading SSE — the stream will resume after user responds
              } else if (currentEvent === 'done') {
                doneData = data;
              } else if (currentEvent === 'error') {
                throw new Error(data.message);
              }
            } catch (e: any) {
              if (currentEvent === 'error' || currentEvent === 'done' || !currentEvent) {
                console.error('[SSE] Parse error on', currentEvent, e);
                throw e;
              }
            } finally {
              dataBuffer = '';
              currentEvent = '';
            }
          }
        }
      }

      const elapsed = Date.now() - startTime;
      console.debug('[SSE] Stream ended, doneData=', doneData, 'streamingTextLen=', streamingText.length);

      // User switched conversation during streaming — just clean up loading state
      if (!isSameConv()) {
        set({ loading: false, loadingStep: '', abortController: null });
        return;
      }

      if (!doneData) {
        // Stream ended without a 'done' event — show fallback error message
        console.error('[SSE] No done event received, stream ended unexpectedly');
        if (isSameConv()) {
          set(state => {
            const msgs = [...state.messages];
            const lastIdx = msgs.length - 1;
            if (msgs[lastIdx]?.role === 'assistant') {
              msgs[lastIdx] = {
                ...msgs[lastIdx],
                content: streamingText || '',
                error: '服务端响应异常：未收到完成事件，请检查后端日志',
                warnings: ['SSE 流未正常结束，缺少 done 事件'],
              };
            }
            return { messages: msgs, loading: false, loadingStep: '', abortController: null };
          });
        } else {
          set({ loading: false, loadingStep: '', abortController: null });
        }
        return;
      }

      // Finalize the assistant message with done data (result now comes from SSE)
      console.debug('[SSE] Building finalMsg: intent=', doneData.intent, 'sql=', doneData.sql?.substring(0, 100), 'hasResult=', !!doneData.result, 'hasError=', !!doneData.error);
      // Preserve progress stages from streaming
      const existingMsg = get().messages[get().messages.length - 1];
      const finalMsg: ChatMessage = {
        role: 'assistant',
        content: doneData.error ? '' : (doneData.reply || streamingText),
        question: question,  // Save original user question for re-execute
        intent: doneData.intent,
        reply: doneData.reply,
        sql: doneData.error ? undefined : doneData.sql,
        warnings: doneData.warnings,
        thinking: doneData.thinking || streamingThinking,
        rag: doneData.rag,
        result: doneData.result,  // Result from SSE stream (auto-executed on backend)
        chart_type: doneData.chart_type,
        brief: doneData.brief,
        tokens: doneData.tokens,
        elapsed_ms: elapsed,
        viewMode: doneData.chart_type && doneData.chart_type !== 'table' ? 'chart' : 'table',
        ai_raw_response: doneData.ai_raw_response || streamingText,
        timings: doneData.timings,
        error: doneData.error || doneData.result?.error,
        progressStages: existingMsg?.progressStages,
        activeStage: 'completed',
        analysis: doneData.analysis,
        workflow_info: doneData.workflow_info,
        tool_calls: doneData.tool_calls,
      };

      set(state => {
        const msgs = [...state.messages];
        msgs[msgs.length - 1] = finalMsg;
        return {
          messages: msgs,
          loading: false,
          loadingStep: '',
          abortController: null,
          // 执行层会话 ID 留存,下一轮回传以 resume 会话
          executorSessionId: doneData.session_id || state.executorSessionId,
        };
      });

      // Save conversation
      if (isSameConv()) {
        const msgs = get().messages;
        const title = deriveTitle(msgs);
        await saveMessages(convId, msgs, title);
        if (title) {
          set(s => ({
            conversations: s.conversations.map(c =>
              c.id === convId ? { ...c, title, updated_at: new Date().toISOString() } : c
            ),
          }));
        }
      }
    } catch (e: any) {
      const elapsed = Date.now() - startTime;

      // User cancelled — mark message as cancelled
      if (e.name === 'AbortError') {
        if (isSameConv()) {
          set(state => {
            const msgs = [...state.messages];
            const lastIdx = msgs.length - 1;
            if (msgs[lastIdx]?.role === 'assistant') {
              msgs[lastIdx] = {
                ...msgs[lastIdx],
                warnings: [...(msgs[lastIdx].warnings || []), '已取消'],
                elapsed_ms: elapsed,
              };
            }
            return { messages: msgs, loading: false, loadingStep: '', abortController: null };
          });
        } else {
          set({ loading: false, loadingStep: '', abortController: null });
        }
        return;
      }

      const errorMsg: ChatMessage = {
        role: 'assistant', content: '', error: e.message, elapsed_ms: elapsed,
      };
      if (isSameConv()) {
        set(state => {
          const msgs = [...state.messages];
          if (msgs[msgs.length - 1]?.role === 'assistant' && !msgs[msgs.length - 1]?.sql) {
            msgs[msgs.length - 1] = errorMsg;
          } else {
            msgs.push(errorMsg);
          }
          return { messages: msgs, loading: false, loadingStep: '', abortController: null };
        });
      } else {
        set({ loading: false, loadingStep: '', abortController: null });
      }
    }
  },

  cancelMessage: () => {
    const { abortController } = get();
    if (abortController) {
      abortController.abort();
    }
  },

  respondToAsk: async (requestId, response) => {
    // Show user's response in the chat and clear the pending ask
    set(state => {
      const msgs = state.messages.map(m =>
        m.pendingAsk?.request_id === requestId
          ? { ...m, pendingAsk: undefined, content: m.content + `\n\n💬 ${response}` }
          : m
      );
      return { messages: msgs, loading: true, loadingStep: 'Agent 正在继续分析...' };
    });
    // POST response to backend to resume the agent loop
    const token = localStorage.getItem('token');
    try {
      await fetch('/api/pipeline/ask/respond', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ request_id: requestId, response }),
      });
    } catch (e) {
      console.error('[respondToAsk] Failed to send response:', e);
    }
    // The SSE stream will resume automatically after the backend receives this
  },

  cancelAsk: async (requestId) => {
    // Cancel the agent loop and clear the pending ask
    set(state => {
      const msgs = state.messages.map(m =>
        m.pendingAsk?.request_id === requestId
          ? { ...m, pendingAsk: undefined, content: m.content + '\n\n🚫 任务已取消' }
          : m
      );
      return { messages: msgs, loading: false, loadingStep: '' };
    });
    // Notify backend to cancel the agent loop
    const token = localStorage.getItem('token');
    try {
      await fetch('/api/pipeline/ask/respond', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ request_id: requestId, response: '__CANCEL__' }),
      });
    } catch (e) {
      console.error('[cancelAsk] Failed to cancel:', e);
    }
  },

  setViewMode: (idx, mode) => {
    set(state => {
      const msgs = [...state.messages];
      if (msgs[idx]) {
        msgs[idx] = { ...msgs[idx], viewMode: mode };
      }
      return { messages: msgs };
    });
  },

  updateMessageFeedback: (idx, feedback, expectedTable) => {
    const state = get();
    const msgs = [...state.messages];
    if (msgs[idx]) {
      msgs[idx] = { ...msgs[idx], feedback, expected_table: expectedTable };
      set({ messages: msgs });
      // Persist to backend
      if (state.currentConvId) {
        saveMessages(state.currentConvId, msgs);
      }
    }
  },

  analyzeData: async (msgIdx, question) => {
    const msg = get().messages[msgIdx];
    if (!msg?.result) return;

    set(state => {
      const msgs = [...state.messages];
      if (msgs[msgIdx]) msgs[msgIdx] = { ...msgs[msgIdx], analyzing: true };
      return { messages: msgs };
    });

    try {
      const { data } = await client.post('/chat/analyze', {
        question,
        columns: msg.result.columns || [],
        rows: (msg.result.rows || []).slice(0, 100),
      });
      set(state => {
        const msgs = [...state.messages];
        if (msgs[msgIdx]) msgs[msgIdx] = { ...msgs[msgIdx], analysis: data.reply, analyzing: false };
        return { messages: msgs };
      });
    } catch {
      set(state => {
        const msgs = [...state.messages];
        if (msgs[msgIdx]) msgs[msgIdx] = { ...msgs[msgIdx], analyzing: false };
        return { messages: msgs };
      });
    }
  },

  predictData: async (msgIdx, question) => {
    const msg = get().messages[msgIdx];
    if (!msg?.result) return;

    set(state => {
      const msgs = [...state.messages];
      if (msgs[msgIdx]) msgs[msgIdx] = { ...msgs[msgIdx], predicting: true };
      return { messages: msgs };
    });

    try {
      const { data } = await client.post('/chat/predict', {
        question,
        columns: msg.result.columns || [],
        rows: (msg.result.rows || []).slice(0, 100),
      });
      set(state => {
        const msgs = [...state.messages];
        if (msgs[msgIdx]) msgs[msgIdx] = { ...msgs[msgIdx], prediction: data.reply, predicting: false };
        return { messages: msgs };
      });
    } catch {
      set(state => {
        const msgs = [...state.messages];
        if (msgs[msgIdx]) msgs[msgIdx] = { ...msgs[msgIdx], predicting: false };
        return { messages: msgs };
      });
    }
  },

  clear: async () => {
    const { currentConvId } = get();
    // Clear conversation messages in database
    if (currentConvId) {
      try {
        await client.put(`/chat/conversations/${currentConvId}`, { messages: [] });
      } catch (e) {
        console.error('Failed to clear conversation:', e);
      }
    }
    set({ messages: [], loading: false, loadingStep: '', executorSessionId: null });
  },
}));
