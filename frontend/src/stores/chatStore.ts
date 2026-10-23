import { create } from 'zustand';
import client from '../api/client';
import { toast } from '@/lib/toast';
import { useThemeStore } from './themeStore';
import { listAuthorizedSites, type AuthorizedSite } from '../api/sites';
import type { KnowledgeResult } from '../components/InlineDetails';

export interface ProgressStage {
  stage: string;
  message: string;
  timestamp: number;
  elapsed?: number;  // Step elapsed time in seconds
  step?: number;     // Step number (for agent_exec matching)
}

export interface ToolCall {
  step?: number;
  tool_call_id?: string;  // 执行层工具调用 ID(tool_result 回填匹配)
  tool: string;
  arguments?: Record<string, any>;
  result?: string;
  result_preview?: string;
  knowledge?: KnowledgeResult;
  error?: string;
  elapsed?: number;
}

// 执行过程有序时间线:思考与工具调用按实际发生顺序穿插记录
export type ProcessSegment =
  | { kind: 'thinking'; text: string }
  | { kind: 'tool'; tool_call_id?: string; step?: number };

export interface AttachmentInfo {
  filename: string;
  category: 'image' | 'table' | 'document' | 'model3d';
  size?: number;
  /** 会话工作区相对路径(如 uploads/x.png);发送后由 done 事件回带,历史预览走 session-file */
  path?: string;
  /** 本地 blob 预览地址(仅待发/刚发送的本轮消息,不持久化) */
  blobUrl?: string;
}

/** 待发附件:本地文件 + blob 预览,随消息以 multipart/files 上传,由服务端落盘到会话工作区 */
export interface PendingAttachment extends AttachmentInfo {
  file: File;
  blobUrl: string;
}

// 附件本地校验(与后端 send_payload 约束一致:白名单扩展名/20MB/单次 5 个)
export const ATTACH_MAX_FILES = 5;
export const ATTACH_MAX_SIZE = 20 * 1024 * 1024;
const ATTACH_EXT_CATEGORY: Record<string, AttachmentInfo['category']> = {
  '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.gif': 'image', '.webp': 'image',
  '.csv': 'table', '.xlsx': 'table',
  '.pdf': 'document', '.md': 'document', '.txt': 'document', '.docx': 'document',
  '.obj': 'model3d', '.glb': 'model3d', '.stl': 'model3d',
};

/** 本地校验单个附件并归类;不合规返回错误文案,合规返回 null */
export function validateLocalAttachment(file: File): { error: string } | { category: AttachmentInfo['category'] } {
  const ext = (file.name.match(/\.[^.]+$/)?.[0] || '').toLowerCase();
  const category = ATTACH_EXT_CATEGORY[ext];
  if (!category) return { error: `不支持的文件类型: ${ext || '(无扩展名)'}` };
  if (!file.size) return { error: `附件为空文件: ${file.name}` };
  if (file.size > ATTACH_MAX_SIZE) return { error: `文件过大(上限 20MB): ${file.name}` };
  return { category };
}

// 剪贴板粘贴的截图/文件常缺文件名与扩展名(如 image blob),按 MIME 补全,避免本地校验误拒
const CLIPBOARD_MIME_EXT: Record<string, string> = {
  'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp',
  'text/csv': '.csv', 'text/plain': '.txt', 'text/markdown': '.md', 'application/pdf': '.pdf',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
};

/** 粘贴文件补全上传名:无名截图命名为「截图_时间戳.ext」,无扩展名按 MIME 补后缀;无法补全时保留原样(交给 validateLocalAttachment 显式报错,不静默丢弃) */
export function ensureUploadName(file: File, now: number = Date.now()): File {
  if (file.name && /\.[^.]+$/.test(file.name)) return file;
  const ext = CLIPBOARD_MIME_EXT[file.type] || '';
  const base = file.name || `截图_${new Date(now).toISOString().replace(/[-:T]/g, '').slice(0, 14)}`;
  return new File([file], `${base}${ext}`, { type: file.type });
}

/** 从粘贴事件剪贴板提取文件并补全上传名(items 优先,避免与 files 重复计数);纯文本粘贴返回空数组(走默认粘贴行为) */
export function filesFromClipboard(clipboardData: DataTransfer | null): File[] {
  if (!clipboardData) return [];
  const out: File[] = [];
  for (const item of Array.from(clipboardData.items || [])) {
    if (item.kind === 'file') {
      const f = item.getAsFile();
      if (f) out.push(ensureUploadName(f));
    }
  }
  if (out.length === 0) {
    for (const f of Array.from(clipboardData.files || [])) out.push(ensureUploadName(f));
  }
  return out;
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
  viewMode?: 'chart' | 'table' | 'sql' | 'profile';
  ai_raw_response?: string;
  timings?: Record<string, number>;
  analysis?: string;
  prediction?: string;
  analyzing?: boolean;
  predicting?: boolean;
  feedback?: 'up' | 'down';  // User feedback on this result
  expected_table?: string;   // Expected table from negative feedback
  trace_id?: string;         // 可观测回合 trace ID(done 事件回传,赞踩关联用)
  message_uuid?: string;     // 可观测消息统一主键(赞踩反馈写入 adh_message_feedback)
  // 前置意图漏斗：这一条是谁答的（agent=模型 / intent_gate=本地直答）。
  // 它只驱动「转深度分析」逃生门与归属记录；聊天界面不渲染"未调用模型"类内部术语，
  // 可审计性在管理侧（adh_llm_traces.handled_by / 意图漏斗面板）。
  handledBy?: 'agent' | 'intent_gate';
  gate?: {
    mode: string; layer: string; label: string; reason: string;
    signals?: string[]; latency_ms?: number; gate_error?: string;
  };
  // 「存为报表」入口标记: 由服务端报表域判定(done 事件与会话回放同一口径), 前端不自行推断
  reportable?: boolean;
  reportable_reason?: string;
  progressStages?: ProgressStage[];  // Workflow execution stage history
  activeStage?: string;              // Current active stage key
  workflow_info?: any;               // Deep mode workflow info
  tool_calls?: ToolCall[];           // Agent tool call history
  process?: ProcessSegment[];        // 思考/工具穿插的有序时间线(流式构建,done 对齐)
  executionStats?: {                 // 执行层统计(时间线摘要条)
    num_turns?: number;
    tool_call_count?: number;
    duration_ms?: number;
  };
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
  /** 会话查询站点(adh_sites.site_code)；空=未选定 */
  site_code?: string;
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

export interface WorkspaceExecutionLayer {
  layer_id: number;
  name: string;
  display_name: string;
  layer_type: string;  // cli | docker | remote
  allowed_tools: string[];
  model_source: 'system' | 'execution_layer';
  models: string[];  // cli 执行层的模型候选(如 provider/model_name)
}

// Chat 端可选的 AS-BOT(按 工作空间+角色 解析,含各自可用模型)
export interface ChatAsBot {
  id: number;
  as_bot_key: string;
  name: string;
  display_name: string;
  description: string;
  models: string[];
  is_default: boolean;
  available?: boolean;
  unavailable_reason?: string;
}

interface ChatState {
  reset: () => void;
  conversationError: string | null;
  conversations: Conversation[];
  currentConvId: number | null;
  messages: ChatMessage[];
  loading: boolean;
  loadingStep: string;
  abortController: AbortController | null;
  /** 已点“停止”但后端尚未释放执行权（断连后收尾核验中）；期间不得再发送。 */
  stopping: boolean;
  selectedDsId: number;
  datasources: { id: number; name: string; db_type: string }[];
  /** 可查站点 = 角色数据源授权的派生视图（不是第二套授权） */
  authorizedSites: AuthorizedSite[];
  /** 站点清单是否已拉到过（与 asBotsLoaded 同目的：控件一次定形，不逐个弹入） */
  sitesLoaded: boolean;
  siteError: string;
  /** 会话查询站点（全球=逐站汇总）；只影响默认取数范围 */
  selectedSiteCode: string;
  selectedModelId: number | null;
  llmModels: LLMModel[];
  pipelineMode: 'agent' | null;  // null = use legacy endpoints
  retrievalStrategy: string;  // hybrid only

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
  // Chat 端可选的 AS-BOT 清单与当前选中(空=未配置 AS-BOT,模型候选回退执行层)
  asBots: ChatAsBot[];
  selectedAsBotKey: string | null;
  /** AS-BOT 清单是否已拉到过：区分「还没加载」与「确实没配 AS-BOT」。
   *  不区分会让头部模型选择器先按兜底渲染、清单回来又消失（闪烁）。 */
  asBotsLoaded: boolean;
  capabilities: { tools: { name: string; description: string }[]; version: string; empty: boolean;
    unavailable_tools?: { name: string; reason: string }[] } | null;
  // 执行层 SDK 会话 ID(多轮对话 resume,done 事件回传)
  executorSessionId: string | null;

  // MCP tools state
  mcpServers: any[];

  loadConversations: () => Promise<void>;
  loadDatasources: () => Promise<void>;
  loadAuthorizedSites: (workspaceId: number) => Promise<void>;
  setSelectedSiteCode: (code: string) => void;
  loadLLMModels: () => Promise<void>;
  loadSystemConfig: () => Promise<void>;
  loadWorkspaces: () => Promise<void>;
  loadWorkspaceConfig: (workspaceId: number) => Promise<void>;
  loadExecutionLayer: (workspaceId: number) => Promise<void>;
  loadAsBots: (workspaceId: number) => Promise<void>;
  setSelectedAsBotKey: (key: string | null) => void;
  setSelectedModelRef: (ref: string | null) => void;
  setSelectedDsId: (id: number) => void;
  setSelectedModelId: (id: number | null) => void;
  setPipelineMode: (mode: 'agent' | null) => void;
  setRetrievalStrategy: (strategy: string) => void;
  setSelectedWorkspaceId: (id: number) => void;
  loadMcpTools: () => Promise<void>;
  createConversation: () => Promise<number>;
  startNewConversation: () => void;
  switchConversation: (convId: number) => Promise<void>;
  deleteConversation: (convId: number) => Promise<void>;
  renameConversation: (convId: number, title: string) => Promise<void>;
  sendMessage: (question: string, mcpTools?: string[], attachments?: PendingAttachment[],
                opts?: { forceAgent?: boolean }) => Promise<void>;
  cancelMessage: () => void;
  waitForExecutionRelease: (conversationId: number) => Promise<void>;
  respondToAsk: (requestId: string, response: string) => Promise<void>;
  cancelAsk: (requestId: string) => void;
  updateMessageFeedback: (idx: number, feedback: 'up' | 'down', expectedTable?: string) => void;
  setViewMode: (idx: number, mode: 'chart' | 'table' | 'sql' | 'profile') => void;
  analyzeData: (msgIdx: number, question: string) => Promise<void>;
  predictData: (msgIdx: number, question: string) => Promise<void>;
  clear: () => Promise<void>;
}

// ── 有序时间线(process)辅助 ──────────────────────────────────────

/** 追加思考增量:连续的 thinking 事件合并进同一段,被工具打断后另起新段。 */
function appendThinkingToProcess(process: ProcessSegment[] | undefined, text: string): ProcessSegment[] {
  const prev = process || [];
  const last = prev[prev.length - 1];
  if (last && last.kind === 'thinking') {
    return [...prev.slice(0, -1), { kind: 'thinking', text: last.text + text }];
  }
  return [...prev, { kind: 'thinking', text }];
}

/**
 * 执行层统计是否有可展示内容。
 *
 * 必须显式转布尔：`stats && (a || b || c) && <div/>` 在三项全 0 时整表达式求值为
 * 数字 0，React 会把它当文本渲染到气泡上方（前置意图层直答回合就是全 0）。
 */
export function hasModelStats(stats?: {
  num_turns?: number; tool_call_count?: number; duration_ms?: number;
} | null): boolean {
  if (!stats) return false;
  return (Number(stats.num_turns) || 0) + (Number(stats.tool_call_count) || 0)
    + (Number(stats.duration_ms) || 0) > 0;
}

/** 把流式构建的 process 与 done 的后端权威 tool_calls 对齐:
 *  1) tool_call_id 全部可匹配 → 保留;
 *  2) done 清单无 id 且数量一致 → 按位次回填 step;
 *  3) 对不上 → 丢弃,前端回落旧的分开展示。 */
function reconcileProcess(
  process: ProcessSegment[] | undefined,
  toolCalls: ToolCall[] | undefined,
): ProcessSegment[] | undefined {
  if (!process || process.length === 0) return undefined;
  const toolSegs = process.filter(p => p.kind === 'tool');
  const tc = toolCalls || [];
  if (toolSegs.length === 0) return tc.length === 0 ? process : undefined;
  if (tc.length === toolSegs.length) {
    const ids = new Set(tc.map(t => t.tool_call_id).filter(Boolean));
    if (toolSegs.every(s => s.kind === 'tool' && s.tool_call_id && ids.has(s.tool_call_id))) {
      return process;
    }
    if (tc.every(t => !t.tool_call_id)) {
      let idx = -1;
      return process.map(s => s.kind === 'tool'
        ? { kind: 'tool' as const, step: tc[++idx].step ?? idx + 1 }
        : s);
    }
  }
  return undefined;
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
        saved_datasets: slim.rag.saved_datasets,
        business_terms: slim.rag.business_terms,
        business_terms_count: slim.rag.business_terms_count,
        datasets_count: slim.rag.datasets_count,
      };
    }
    // 附件仅持久化工作区引用,本地 blob 预览地址不入库
    if (slim.attachments) {
      slim.attachments = slim.attachments.map(({ blobUrl: _blobUrl, ...rest }: any) => rest);
    }
    // reportable 是服务端读取时重算的展示派生标记, 不回存会话 JSON
    delete slim.reportable;
    delete slim.reportable_reason;
    return slim;
  });
  const payload: any = { messages: slimMessages };
  if (title) payload.title = title;
  try {
    await client.put(`/chat/conversations/${convId}`, payload);
  } catch (e) {
    console.error('Failed to save conversation:', e);
    toast.error('会话记录保存失败，请稍后重试');
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

let chatContextVersion = 0;
let conversationListVersion = 0;

// 「正在停止…」轮询参数：点停止只是断开 SSE，后端要先确认在跑的工具任务与
// SDK 子进程已收尾才会释放执行权（典型几秒，最坏到整轮跑完）。
// 超过上限就如实报错，不静默放开输入框让用户反复撞 409。
const STOP_POLL_INTERVAL_MS = 1000;
const STOP_CONFIRM_TIMEOUT_MS = 120000;
export const useChatStore = create<ChatState>((set, get) => ({
  reset: () => { ++chatContextVersion; ++conversationListVersion; get().abortController?.abort(); set(useChatStore.getInitialState()); },
  conversationError: null,
  conversations: [],
  currentConvId: null,
  messages: [],
  loading: false,
  loadingStep: '',
  abortController: null,
  stopping: false,
  selectedDsId: 0,
  datasources: [],
  authorizedSites: [],
  sitesLoaded: false,
  siteError: '',
  selectedSiteCode: '',
  selectedModelId: null,
  llmModels: [],
  pipelineMode: 'agent',
  retrievalStrategy: 'hybrid',
  mcpServers: [],

  // Workspace state
  selectedWorkspaceId: 0,
  workspaces: [],
  workspaceConfig: {},
  executionLayer: null,
  selectedModelRef: null,
  asBots: [],
  selectedAsBotKey: null,
  asBotsLoaded: false,
  capabilities: null,
  executorSessionId: null,

  loadMcpTools: async () => {
    const context = chatContextVersion;
    try {
      const { data } = await client.get('/chat/mcp-tools');
      if (context !== chatContextVersion) return;
      set({ mcpServers: data.servers || [] });
    } catch {
      // silently fail - MCP is optional
    }
  },

  loadWorkspaces: async () => {
    const context = chatContextVersion;
    try {
      const { data } = await client.get('/workspaces');
      if (context !== chatContextVersion) return;
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
    ++chatContextVersion; ++conversationListVersion;
    get().abortController?.abort();
    set({ selectedWorkspaceId: id, currentConvId: null, messages: [], conversations: [], conversationError: null, executorSessionId: null,
      workspaceConfig: {}, executionLayer: null, asBots: [], selectedAsBotKey: null, selectedModelRef: null,
      capabilities: null, loading: false, abortController: null });
  },

  loadWorkspaceConfig: async (workspaceId: number) => {
    const context = chatContextVersion;
    try {
      const { data } = await client.get(`/workspaces/${workspaceId}`);
      if (context !== chatContextVersion || workspaceId !== get().selectedWorkspaceId) return;
      const config = data?.config || {};
      set({ workspaceConfig: config });
      // 全面转向 Qoder 执行层:锁定 agent 模式,忽略旧 allowed_pipeline_modes
      set({ pipelineMode: 'agent' });
    } catch {
      if (context !== chatContextVersion || workspaceId !== get().selectedWorkspaceId) return;
      set({ workspaceConfig: {} });
      toast.error('工作空间配置加载失败，请重新进入工作空间');
    }
  },

  loadExecutionLayer: async (workspaceId: number) => {
    const context = chatContextVersion;
    // 工作空间生效的执行层,含模型候选
    try {
      const { data } = await client.get(`/admin/execution-layers/workspaces/${workspaceId}/execution-layer`);
      if (context !== chatContextVersion || workspaceId !== get().selectedWorkspaceId) return;
      set({ executionLayer: data, selectedModelRef: null, executorSessionId: null });
    } catch {
      if (context !== chatContextVersion || workspaceId !== get().selectedWorkspaceId) return;
      set({ executionLayer: null, selectedModelRef: null, executorSessionId: null });
      toast.error('工作空间模型配置加载失败，请重新进入工作空间');
    }
  },

  setSelectedModelRef: (ref) => set({ selectedModelRef: ref }),

  loadAsBots: async (workspaceId: number) => {
    const context = chatContextVersion;
    // Chat 端可选 AS-BOT 清单(按 工作空间+当前用户角色 解析,与后端 resolve_as_bots 口径一致)
    try {
      const { data } = await client.get('/chat/as-bots', { params: { workspace_id: workspaceId } });
      const list: ChatAsBot[] = Array.isArray(data) ? data : [];
      const usable = list.filter(w => w.available !== false);
      const def = usable.find((w) => w.is_default) || usable[0] || null;
      // 默认选中 is_default(或首个);selectedModelRef 置空→发送时派生为该 AS-BOT 首个模型
      if (context !== chatContextVersion || get().selectedWorkspaceId !== workspaceId) return;
      set({ asBots: list, selectedAsBotKey: get().currentConvId ? get().selectedAsBotKey : def?.as_bot_key ?? null, selectedModelRef: null, asBotsLoaded: true });
    } catch {
      if (context !== chatContextVersion || get().selectedWorkspaceId !== workspaceId) return;
      // 失败也算“已定形”：按无 AS-BOT 的兜底分支渲染，不保留一个永远转圈的空位
      set({ asBots: [], asBotsLoaded: true });
      toast.error('AS-BOT 权限清单加载失败，请重试；当前会话绑定未改变');
    }
  },

  setSelectedAsBotKey: (key) => {
    const { currentConvId, selectedAsBotKey, abortController } = get();
    if (key === selectedAsBotKey) return;            // 未变化: Radix 不会触发, 双保险
    abortController?.abort();
    // 打开的历史会话尚未绑定 AS-BOT(selectedAsBotKey 为空): 本次选择视为"绑定/继续该会话",
    // 保留已加载的消息, 不新开会话(修复: 点开历史→选 AS-BOT 消息被清空)。
    if (currentConvId != null && !selectedAsBotKey) {
      set({ selectedAsBotKey: key, selectedModelRef: null, capabilities: null,
        abortController: null, loading: false });
      return;
    }
    // 其余情况(新会话空选 / 从已绑定 AS-BOT 切到不同 AS-BOT): 会话与 AS-BOT 一一绑定,
    // 切换即开启新会话, 清空上下文与 resume 会话。
    set({ selectedAsBotKey: key, selectedModelRef: null, currentConvId: null, messages: [],
      executorSessionId: null, capabilities: null, loading: false, abortController: null });
  },

  loadConversations: async () => {
    const workspaceId = get().selectedWorkspaceId;
    if (!workspaceId) return;
    const version = ++conversationListVersion;
    const context = chatContextVersion;
    set({ conversationError: null });
    try {
      const { data } = await client.get(`/chat/conversations?workspace_id=${workspaceId}`);
      if (version !== conversationListVersion || context !== chatContextVersion) return;
      if (!Array.isArray(data)) throw new Error('会话列表格式错误');
      set({ conversations: data });
    } catch {
      if (version === conversationListVersion && context === chatContextVersion) set({ conversationError: '会话列表加载失败，请重试' });
    }
  },

  loadDatasources: async () => {
    const context = chatContextVersion;
    try {
      // 纯角色裁决: 选择器只列当前用户角色授权的数据源(空授权→空列表)。
      const { data } = await client.get('/datasources/authorized');
      if (context !== chatContextVersion) return;
      set({ datasources: data });
      // Auto-select default datasource
      if (data.length > 0 && !get().selectedDsId) {
        const defaultDs = data.find((d: any) => d.is_default) || data[0];
        set({ selectedDsId: defaultDs.id });
      }
    } catch {}
  },

  setSelectedDsId: (id) => set({ selectedDsId: id }),

  /**
   * 可查站点候选（聊天站点选择器）。空清单 = 该角色没有站点化数据源（如只接
   * 平台库），此时不渲染站点选择器，而不是摆一个选不出东西的死开关。
   * 加载失败显式记错并保留重试（不静默当成“无站点”）。
   */
  loadAuthorizedSites: async (workspaceId) => {
    const context = chatContextVersion;
    set({ siteError: '' });
    try {
      const sites = await listAuthorizedSites(workspaceId);
      if (context !== chatContextVersion) return;
      set({ authorizedSites: sites, sitesLoaded: true });
      const cur = get().selectedSiteCode;
      if (cur && sites.some((s) => s.site_code === cur)) return;
      // 默认站点：唯一具体站点直接选中；多站取「全球」（可用时）；否则清单首个
      const concrete = sites.filter((s) => !s.is_aggregate);
      const aggregate = sites.find((s) => s.is_aggregate);
      const fallback = concrete.length === 1 ? concrete[0].site_code
        : (aggregate ? aggregate.site_code : (concrete[0]?.site_code || ''));
      set({ selectedSiteCode: fallback });
    } catch (e: any) {
      if (context !== chatContextVersion) return;
      set({ authorizedSites: [], sitesLoaded: true, siteError: e?.response?.data?.detail || '站点清单加载失败' });
    }
  },

  setSelectedSiteCode: (code) => {
    set({ selectedSiteCode: code });
    // 站点是会话属性：已落库的会话同步持久化，重进/切回不丢选择
    const convId = get().currentConvId;
    if (convId) {
      client.put(`/chat/conversations/${convId}`, { site_code: code }).catch(() => {
        toast.error('站点选择保存失败，本轮仍按所选站点查询');
      });
    }
  },

  loadLLMModels: async () => {
    const context = chatContextVersion;
    try {
      const { data } = await client.get('/model-config/llm');
      if (context !== chatContextVersion) return;
      set({ llmModels: data });
      // Auto-select default model
      if (data.length > 0 && !get().selectedModelId) {
        const defaultModel = data.find((m: any) => m.is_default) || data[0];
        set({ selectedModelId: defaultModel.id });
      }
    } catch {}
  },

  loadSystemConfig: async () => {
    const context = chatContextVersion;
    try {
      const { data } = await client.get('/model-config/system');
      if (context !== chatContextVersion) return;
      if (data?.retrieval_strategy) {
        set({ retrievalStrategy: data.retrieval_strategy });
      }
    } catch {}
  },

  setSelectedModelId: (id) => set({ selectedModelId: id }),
  setPipelineMode: (mode) => set({ pipelineMode: mode }),
  setRetrievalStrategy: (strategy) => set({ retrievalStrategy: strategy }),

  createConversation: async () => {
    const origin = get();
    const workspaceId = get().selectedWorkspaceId;
    const siteCode = get().selectedSiteCode;
    // 选定站点后不再带 datasource_id：否则会话行会同时持久化一个自动选中的默认源，
    // 与站点冲突时会把 A 站数据当 B 站答案（服务端也会重算，不依赖前端正确）。
    const dsId = siteCode ? 0 : get().selectedDsId;
    const { data } = await client.post('/chat/conversations', {
      datasource_id: dsId,
      workspace_id: workspaceId,
      site_code: siteCode,
    });
    if (get().selectedWorkspaceId !== origin.selectedWorkspaceId ||
        get().selectedAsBotKey !== origin.selectedAsBotKey || get().messages !== origin.messages ||
        get().abortController !== origin.abortController) {
      throw new Error('会话上下文已改变，未接管迟到的新会话');
    }
    const conv: Conversation = {
      id: data.id,
      title: data.title,
      datasource_id: data.datasource_id || dsId,
      workspace_id: data.workspace_id || workspaceId,
      site_code: data.site_code || siteCode,
      created_at: data.created_at,
      updated_at: data.created_at,
    };
    set(state => ({ conversations: [conv, ...state.conversations], currentConvId: conv.id, messages: [], executorSessionId: null, capabilities: null }));
    return data.id;
  },

  // “新建对话”不预先落库：仅重置为未保存的新会话（currentConvId=null），
  // 首条消息发送时由 sendMessage 懒创建，避免点击/放弃留下空“新对话”。
  startNewConversation: () => {
    get().abortController?.abort();
    set({ currentConvId: null, messages: [], executorSessionId: null, capabilities: null,
      loading: false, loadingStep: '', abortController: null });
  },

  switchConversation: async (convId) => {
    get().abortController?.abort();
    const switching = new AbortController();
    set({ abortController: switching, loading: false, loadingStep: '' });
    try {
      const { data } = await client.get(`/chat/conversations/${convId}`);
      if (get().abortController !== switching) return;
      const msgs = Array.isArray(data.messages) ? data.messages : [];
      // 会话未绑定 AS-BOT(早期数据/创建时未带)时, 默认展示当前工作空间首个可用 AS-BOT, 而非空"选择 AS-BOT"。
      // 全局会话(ws=0, 面板等全局入口所建)跨工作空间互见: 允许用当前工作空间清单兜底; 跨工作空间交由工作空间切换重载处理。
      const sameWs = !data.workspace_id || data.workspace_id === get().selectedWorkspaceId;
      const usable = sameWs ? (get().asBots || []).filter((w) => w.available !== false) : [];
      const defaultAsBot = usable.find((w) => w.is_default)?.as_bot_key || usable[0]?.as_bot_key || null;
      set({
        currentConvId: convId,
        messages: msgs,
        abortController: null,
        // 恢复持久化的执行层会话 ID,重新打开对话即可 resume qoder 会话
        executorSessionId: null,
        // 全局会话不改变当前工作空间上下文（仅归属标记为 ws=0）
        selectedWorkspaceId: data.workspace_id || get().selectedWorkspaceId,
        selectedAsBotKey: data.as_bot_key || defaultAsBot,
        capabilities: null,
        selectedDsId: data.datasource_id || 0,
        // 会话站点回填：持久化行没有站点时保留当前选择（新会话未落库的常见情形）
        selectedSiteCode: data.site_code || get().selectedSiteCode,
      });
    } catch (e) {
      if (get().abortController !== switching) return;
      set({ abortController: null });
      console.error('Failed to load conversation:', e);
      toast.error('切换会话失败，未改变当前会话');
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
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : '删除会话失败，记录仍保留，请重试');
    }
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

  sendMessage: async (question, mcpTools, attachments, opts) => {
    const state = get();
    if (state.loading) return;
    if (state.stopping) {
      toast.error('上一轮正在停止，请稍候…');
      return;
    }
    const abortController = new AbortController();
    set({ loading: true, abortController });
    let convId = state.currentConvId;
    let createdHere = false;
    let persisted = false;
    // 本次新建但最终未落库(失败/取消/切走)的会话 → 回滚删除，避免留下空"新对话"。
    // 保留当前 messages，用户仍能看到本次提问与错误。
    const rollbackEmptyConv = async () => {
      if (!createdHere || !convId || persisted) return;
      try { await client.delete(`/chat/conversations/${convId}`); } catch { /* best-effort */ }
      set(st => ({
        conversations: st.conversations.filter(c => c.id !== convId),
        currentConvId: st.currentConvId === convId ? null : st.currentConvId,
      }));
    };
    try {
      if (!convId) { convId = await get().createConversation(); createdHere = true; }
    } catch {
      if (get().abortController === abortController) {
        set({ loading: false, abortController: null });
        toast.error('创建会话失败，未发送消息');
      }
      return;
    }
    if (get().abortController !== abortController) { await rollbackEmptyConv(); return; }
    let requestFinished = false;

    // Helper: check if we're still in the same conversation
    const isSameConv = () => get().currentConvId === convId &&
      (get().abortController === abortController || (requestFinished && get().abortController === null));

    const userMsg: ChatMessage = {
      role: 'user',
      content: question,
      ...(attachments?.length
        ? { attachments: attachments.map(a => ({ filename: a.filename, category: a.category, size: a.size, blobUrl: a.blobUrl })) }
        : {}),
    };
    const currentMessages = state.messages;

    // Create abort controller for cancellation
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
      // 站点已选时不传 datasource_id（会话作用域由站点决定，避免默认源静默压过站点）
      datasource_id: state.selectedSiteCode ? 0 : state.selectedDsId,
      site_code: state.selectedSiteCode || '',
      model_id: state.selectedModelId,
      mcp_tools: mcpTools || [],
      workspace_id: state.selectedWorkspaceId,
      // 报告交付主题: 选择"跟随"(空)时回落当前 App 主题
      report_theme: useThemeStore.getState().theme,  // 对话内切换已移除：默认继承用户主题
      // 「转深度分析」：只绕过分流，不关任何数据/权限护栏（服务端同样只认这一个开关）
      force_agent: !!opts?.forceAgent,
    };

    if (state.pipelineMode) {
      // Use pipeline endpoint with mode selection
      apiEndpoint = '/api/pipeline/send/stream';
      requestBody.pipeline_mode = state.pipelineMode;
      requestBody.retrieval_strategy = state.retrievalStrategy;
      // Agent(执行层)模式需要工作空间上下文
      if (state.pipelineMode === 'agent' && state.selectedWorkspaceId) {
        requestBody.workspace_id = state.selectedWorkspaceId;
      }
      if (state.pipelineMode === 'agent') {
        // Agent 模式仍透传会话已选数据源作为 ExecutionContext 权威作用域(不进 LLM 视野、数据源对工具黑盒不变)。
        // 旧写法 `delete requestBody.datasource_id` 会让语义层以 datasource_id=0 命中空目录 → 取不到数。
        // execute_sql 若需临时换源, 仍可用业务名 datasource 参数按查询覆盖。
        // 会话选了站点时传 0：生效源由服务端按站点重算（站点是作用域权威，
        // 不能拿自动选中的默认源去压过它——那会把英国站的提问查出中国数据）。
        requestBody.datasource_id = state.selectedSiteCode ? 0 : (state.selectedDsId || 0);
      }
      // AS-BOT 选择: 发送 as_bot_key;模型候选优先取自选中 AS-BOT 的可用模型,
      // 未显式选择时派生为其首个模型(仅 1 个时无需选择框);无 AS-BOT 配置时
      // 回退到执行层运行时模型(model_ref)。上一轮会话 ID 以 session_id 回传 resume。
      if (state.pipelineMode === 'agent') {
        const asBot = state.asBots.find(w => w.as_bot_key === state.selectedAsBotKey) || null;
        if (asBot) {
          // AS-BOT 与角色强绑定：不再向后端传 asBotId，后端按当前用户角色解析；这里仅派生 model_ref。
          const wm = asBot.models || [];
          const ref = state.selectedModelRef || (wm.length >= 1 ? wm[0] : '');
          if (ref) requestBody.model_ref = ref;
        } else if (state.executionLayer?.layer_type === 'cli' && state.selectedModelRef) {
          requestBody.model_ref = state.selectedModelRef;
        }
      } else if (state.executionLayer?.layer_type === 'cli' && state.selectedModelRef) {
        requestBody.model_ref = state.selectedModelRef;
      }
      // qoder 长对话池 key: 后端按 chat 会话维护持久 qodercli 进程,
      // 同会话后续轮次免起进程/恢复会话/重连 MCP, 显著降低首字延迟
      if (convId) requestBody.conversation_id = convId;
    } else {
      apiEndpoint = '/api/chat/send/stream';
    }

    try {
      // 有附件时整体改 multipart(payload + files),由服务端落盘到会话工作区;
      // 无附件保持 JSON(AS-BOT 同形态)。multipart 时不能手设 Content-Type(需带 boundary)。
      let reqBody: BodyInit;
      const reqHeaders: Record<string, string> = { ...(token ? { Authorization: `Bearer ${token}` } : {}) };
      if (attachments && attachments.length > 0) {
        const form = new FormData();
        form.append('payload', JSON.stringify(requestBody));
        attachments.forEach(a => form.append('files', a.file));
        reqBody = form;
      } else {
        reqHeaders['Content-Type'] = 'application/json';
        reqBody = JSON.stringify(requestBody);
      }
      const response = await fetch(apiEndpoint, {
        method: 'POST',
        signal: abortController.signal,
        headers: reqHeaders,
        body: reqBody,
      });

      if (!response.ok) {
        // 服务端错误带可诊断 detail（HTTPException），必须透传给用户，不得只报状态码
        let reason = '';
        try {
          const body = await response.json();
          const d = body?.detail ?? body?.error ?? body?.message;
          if (typeof d === 'string') reason = d;
          else if (d != null) reason = JSON.stringify(d);
        } catch { /* 非 JSON 响应体（如网关错误页）忽略 */ }
        throw new Error(reason ? `发送失败（${response.status}）：${reason}` : `发送失败（HTTP ${response.status}）`);
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

              if (currentEvent === 'capabilities') {
                if (isSameConv()) set({ capabilities: data });
              } else if (currentEvent === 'progress') {
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
                      msgs[lastIdx] = {
                        ...msgs[lastIdx],
                        thinking: streamingThinking,
                        process: appendThinkingToProcess(msgs[lastIdx].process, data.text),
                      };
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
              } else if (currentEvent === 'tool_start') {
                // 执行层工具调用开始:时间线追加 pending 步骤,并记入有序 process
                if (isSameConv()) {
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
                        process: [...(msgs[lastIdx].process || []), {
                          kind: 'tool' as const,
                          tool_call_id: data.tool_call_id,
                          step: prev.length + 1,
                        }],
                      };
                    }
                    return { messages: msgs };
                  });
                }
              } else if (currentEvent === 'tool_result') {
                // 执行层工具调用结果:按 tool_call_id 回填对应步骤
                if (isSameConv()) {
                  set(state => {
                    const msgs = [...state.messages];
                    const lastIdx = msgs.length - 1;
                    if (msgs[lastIdx]?.role === 'assistant') {
                      const updated = (msgs[lastIdx].tool_calls || []).map(tc =>
                        tc.tool_call_id && tc.tool_call_id === data.tool_call_id
                          ? {
                              ...tc,
                              result: data.error ? undefined : data.output,
                              result_preview: (data.output || '').slice(0, 200),
                              knowledge: data.knowledge || undefined,
                              error: data.error || undefined,
                              elapsed: data.elapsed,
                            }
                          : tc
                      );
                      msgs[lastIdx] = { ...msgs[lastIdx], tool_calls: updated };
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
      if (!isSameConv()) return;

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
        }
        return;
      }

      // Finalize the assistant message with done data (result now comes from SSE)
      console.debug('[SSE] Building finalMsg: intent=', doneData.intent, 'sql=', doneData.sql?.substring(0, 100), 'hasResult=', !!doneData.result, 'hasError=', !!doneData.error);
      // Preserve progress stages from streaming
      const existingMsg = get().messages[get().messages.length - 1];
      // done 携带完整清单优先(后端权威);否则保留流式聚合结果
      const finalToolCalls = (doneData.tool_calls && doneData.tool_calls.length > 0)
        ? doneData.tool_calls
        : existingMsg?.tool_calls;
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
        tool_calls: finalToolCalls,
        // 可观测关联键:赞踩反馈据此写入 adh_message_feedback 并能在 Trace 详情回显
        trace_id: doneData.trace_id,
        message_uuid: doneData.message_uuid,
        // 前置意图漏斗：处理方与判定快照（历史回放同样带徽章）
        handledBy: doneData.handled_by,
        gate: doneData.gate,
        // 报表入口标记(服务端判定, 只有真产出过数据/分析的回合才给按钮)
        reportable: doneData.reportable,
        reportable_reason: doneData.reportable_reason,
        // 有序时间线:流式 process 与最终 tool_calls 对齐,对不上则丢弃回落旧展示
        process: reconcileProcess(existingMsg?.process, finalToolCalls),
        executionStats: doneData.stats,
      };

      requestFinished = true;
      set(state => {
        const msgs = [...state.messages];
        msgs[msgs.length - 1] = finalMsg;
        // done 回带落盘后的附件清单(工作区相对路径)→ 回填用户消息附件引用,
        // 历史预览改走 session-file;文件名可能被服务端去重改名,以回带为准
        if (Array.isArray(doneData.attachments) && doneData.attachments.length > 0) {
          const uIdx = msgs.length - 2;
          if (uIdx >= 0 && msgs[uIdx].role === 'user' && msgs[uIdx].attachments?.length) {
            const prev = msgs[uIdx].attachments || [];
            msgs[uIdx] = {
              ...msgs[uIdx],
              attachments: doneData.attachments.map((a: any, i: number) => ({
                filename: a.filename,
                category: a.category,
                size: a.size,
                path: a.path,
                blobUrl: prev[i]?.blobUrl,
              })),
            };
          }
        }
        return {
          messages: msgs,
          loading: false,
          loadingStep: '',
          abortController: null,
          // 执行层会话 ID 留存,下一轮回传以 resume 会话
          executorSessionId: null,
          capabilities: doneData.capabilities || get().capabilities,
        };
      });

      // Save conversation (连同 qoder 会话 ID 一并持久化)
      if (isSameConv()) {
        const msgs = get().messages;
        const title = deriveTitle(msgs);
        await saveMessages(convId, msgs, title);
        persisted = true;
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
      }
    } finally {
      // 本次新建但未成功落库的会话统一回滚(成功保存过则 persisted=true 不删)。
      await rollbackEmptyConv();
    }
  },

  cancelMessage: () => {
    const { abortController, currentConvId } = get();
    if (!abortController) return;
    // 先置 stopping 再 abort：abort 会同步触发 sendMessage 的 AbortError 收尾，
    // 顺序颠倒会让输入框在“已断连但后端仍在收尾”的窗口里可用。
    set({ stopping: true });
    abortController.abort();
    if (!currentConvId) { set({ stopping: false }); return; }
    void get().waitForExecutionRelease(currentConvId);
  },

  waitForExecutionRelease: async (conversationId) => {
    const deadline = Date.now() + STOP_CONFIRM_TIMEOUT_MS;
    let released = false;
    let queryFailed = false;
    while (Date.now() < deadline) {
      try {
        const { data } = await client.get('/pipeline/send/status', {
          params: { conversation_id: conversationId },
        });
        if (!data?.busy) { released = true; break; }
      } catch {
        // 查不到状态 ≠ 已停止：按“未确认”处理并提示，不假装成功
        queryFailed = true;
        break;
      }
      await new Promise(r => setTimeout(r, STOP_POLL_INTERVAL_MS));
    }
    // 用户已切走/新开会话：只清 stopping，不弹提示打断新的上下文
    if (get().currentConvId !== conversationId) { set({ stopping: false }); return; }
    set({ stopping: false });
    if (!released) {
      toast.error(queryFailed
        ? '无法确认上一轮已停止（状态查询失败）。请稍候再发送，或联系管理员查看会话状态。'
        : '上一轮仍未确认停止（后端需先收尾工具任务与 SDK 子进程）。请稍候再发送，或联系管理员查看会话状态。');
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
    const { currentConvId, abortController } = get();
    abortController?.abort();
    // 清空 = 擦除「当前会话」的历史消息并重置模型上下文，但**不脱离/新开窗口**：
    // 保留 currentConvId，下一轮仍在同一会话以零上下文继续（后端 PUT messages=[] 已按此语义实现，
    // 会把会话 sdk_session_id 置空但不关闭）。旧实现将 currentConvId 置 null 会让 sendMessage
    // 懒建新会话，表现为“清空后再对话跳出一个新窗口”——那是错误行为。
    set({ messages: [], loading: false, loadingStep: '', executorSessionId: null,
      capabilities: null, abortController: null });
    // 已落库会话：清库历史消息；未落库的新会话（currentConvId=null）无需请求。
    if (currentConvId) {
      try {
        await client.put(`/chat/conversations/${currentConvId}`, { messages: [] });
      } catch (e) {
        console.error('Failed to clear conversation:', e);
        toast.error('会话记录未能清空（会话仍在运行中请先停止），请重试');
      }
    }
  },
}));
