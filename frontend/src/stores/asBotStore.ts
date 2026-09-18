import { create } from 'zustand';
import client from '../api/client';

// ── Types ──────────────────────────────────────────────────────────

export interface AsBotMessage {
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  // 审批相关
  pendingApproval?: ApprovalRequest;
  approvalStatus?: 'pending' | 'approved' | 'rejected' | 'executed' | 'failed';
  approvalResult?: any;
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

export interface ApprovalRequest {
  approval_id: number;
  action_key: string;
  action_label: string;
  description: string;
  payload: Record<string, any>;
}

interface AsBotPermissions {
  role: string;
  permissions: Record<string, boolean>;
  can_access: boolean;
}

interface AsBotState {
  // Panel state
  panelOpen: boolean;
  // Messages
  messages: AsBotMessage[];
  loading: boolean;
  loadingStep: string;
  abortController: AbortController | null;
  // Permissions
  permissions: AsBotPermissions | null;
  permissionsLoaded: boolean;
  // Pending approval count (for badge)
  pendingApprovalCount: number;

  // Actions
  togglePanel: () => void;
  openPanel: () => void;
  closePanel: () => void;
  sendMessage: (text: string) => Promise<void>;
  cancelMessage: () => void;
  approveAction: (approvalId: number, msgIdx: number) => Promise<void>;
  rejectAction: (approvalId: number, msgIdx: number) => Promise<void>;
  loadPermissions: () => Promise<void>;
  loadPendingApprovals: () => Promise<void>;
  clearMessages: () => void;
}

export const useAsBotStore = create<AsBotState>((set, get) => ({
  panelOpen: false,
  messages: [],
  loading: false,
  loadingStep: '',
  abortController: null,
  permissions: null,
  permissionsLoaded: false,
  pendingApprovalCount: 0,

  togglePanel: () => set(state => ({ panelOpen: !state.panelOpen })),
  openPanel: () => set({ panelOpen: true }),
  closePanel: () => set({ panelOpen: false }),

  clearMessages: () => set({ messages: [] }),

  loadPermissions: async () => {
    try {
      const { data } = await client.get('/as-bot/permissions');
      set({ permissions: data, permissionsLoaded: true });
    } catch {
      set({ permissions: { role: '', permissions: {}, can_access: false }, permissionsLoaded: true });
    }
  },

  loadPendingApprovals: async () => {
    try {
      const { data } = await client.get('/as-bot/approvals', { params: { status: 'pending' } });
      set({ pendingApprovalCount: (data.approvals || []).length });
    } catch {
      // silently fail
    }
  },

  sendMessage: async (text: string) => {
    const state = get();
    if (state.loading || !text.trim()) return;

    const userMsg: AsBotMessage = { role: 'user', content: text };
    const currentMessages = state.messages;

    const abortController = new AbortController();
    set({
      loading: true,
      loadingStep: '正在分析...',
      messages: [...currentMessages, userMsg],
      abortController,
    });

    const history = currentMessages.map(m => ({
      role: m.role,
      content: m.content,
    }));

    const token = localStorage.getItem('token');

    const requestBody = {
      question: text,
      history,
      pipeline_mode: 'agent',
      waker_key: '__system_bot__',
      workspace_id: 0,
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
                // Check if the response contains an approval request
                let pendingApproval: ApprovalRequest | undefined;

                // Parse approval request from tool results
                if (data.tool_calls) {
                  for (const tc of data.tool_calls) {
                    try {
                      const output = typeof tc.result === 'string' ? JSON.parse(tc.result) : tc.result;
                      if (output?.approval_required) {
                        pendingApproval = {
                          approval_id: output.approval_id || 0,
                          action_key: output.action_key,
                          action_label: output.action_label,
                          description: output.description,
                          payload: output.payload,
                        };
                        break;
                      }
                    } catch {
                      // Not a JSON result, skip
                    }
                  }
                }

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
                      pendingApproval,
                    };
                  }
                  return { messages: msgs, loading: false, loadingStep: '', abortController: null };
                });

                // If there's a pending approval, create it on the backend
                if (pendingApproval && pendingApproval.action_key) {
                  try {
                    const { data: created } = await client.post('/as-bot/approvals/create', {
                      action_key: pendingApproval.action_key,
                      payload: pendingApproval.payload,
                    }).catch(() => ({ data: { id: 0 } }));
                    // Update with real approval_id
                    if (created?.id) {
                      set(state => {
                        const msgs = [...state.messages];
                        const lastIdx = msgs.length - 1;
                        if (msgs[lastIdx]?.pendingApproval) {
                          msgs[lastIdx] = {
                            ...msgs[lastIdx],
                            pendingApproval: { ...msgs[lastIdx].pendingApproval!, approval_id: created.id },
                          };
                        }
                        return { messages: msgs };
                      });
                    }
                  } catch {
                    // Approval creation failed, continue
                  }
                }
              } else if (currentEvent === 'error') {
                throw new Error(data.message);
              }
            } catch (e: any) {
              if (currentEvent === 'error' || currentEvent === 'done' || !currentEvent) {
                // Only throw for actual errors
                if (currentEvent === 'error') {
                  set(state => {
                    const msgs = [...state.messages];
                    const lastIdx = msgs.length - 1;
                    if (msgs[lastIdx]?.role === 'assistant') {
                      msgs[lastIdx] = { ...msgs[lastIdx], error: dataBuffer || e.message };
                    }
                    return { messages: msgs, loading: false, loadingStep: '', abortController: null };
                  });
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
    } catch (e: any) {
      if (e.name === 'AbortError') {
        set(state => {
          const msgs = [...state.messages];
          const lastIdx = msgs.length - 1;
          if (msgs[lastIdx]?.role === 'assistant') {
            msgs[lastIdx] = { ...msgs[lastIdx], content: msgs[lastIdx].content || '(已取消)' };
          }
          return { messages: msgs, loading: false, loadingStep: '', abortController: null };
        });
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
    }
  },

  cancelMessage: () => {
    const { abortController } = get();
    if (abortController) {
      abortController.abort();
    }
  },

  approveAction: async (approvalId: number, msgIdx: number) => {
    set(state => {
      const msgs = [...state.messages];
      if (msgs[msgIdx]) {
        msgs[msgIdx] = { ...msgs[msgIdx], approvalStatus: 'approved' };
      }
      return { messages: msgs, loading: true, loadingStep: '正在执行...' };
    });

    try {
      const { data } = await client.post('/as-bot/approve', { approval_id: approvalId });
      set(state => {
        const msgs = [...state.messages];
        if (msgs[msgIdx]) {
          msgs[msgIdx] = {
            ...msgs[msgIdx],
            approvalStatus: data.status || 'executed',
            approvalResult: data.result,
          };
        }
        return { messages: msgs, loading: false, loadingStep: '' };
      });

      // Add a system message with the result
      const result = data.result;
      const resultMsg: AsBotMessage = {
        role: 'assistant',
        content: result?.success
          ? `操作已执行成功。${result?.model_id ? `模型 ID: ${result.model_id}` : ''}`
          : `执行失败: ${result?.error || '未知错误'}`,
      };
      set(state => ({ messages: [...state.messages, resultMsg] }));
    } catch (e: any) {
      set(state => {
        const msgs = [...state.messages];
        if (msgs[msgIdx]) {
          msgs[msgIdx] = { ...msgs[msgIdx], approvalStatus: 'failed', approvalResult: { error: e.message } };
        }
        return { messages: msgs, loading: false, loadingStep: '' };
      });
    }
  },

  rejectAction: async (approvalId: number, msgIdx: number) => {
    try {
      await client.post('/as-bot/reject', { approval_id: approvalId });
    } catch {
      // Continue even if reject API fails
    }
    set(state => {
      const msgs = [...state.messages];
      if (msgs[msgIdx]) {
        msgs[msgIdx] = { ...msgs[msgIdx], approvalStatus: 'rejected' };
      }
      return { messages: msgs };
    });

    // Add a message noting rejection
    const rejectMsg: AsBotMessage = {
      role: 'assistant',
      content: '操作已拒绝。如需其他操作,请继续提问。',
    };
    set(state => ({ messages: [...state.messages, rejectMsg] }));
  },
}));
