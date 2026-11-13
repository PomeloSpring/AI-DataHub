import { create } from 'zustand';
import client from '../api/client';
import { toast } from '@/lib/toast';
import { useThemeStore } from './themeStore';
import { listAuthorizedSites, type AuthorizedSite } from '../api/sites';
import type { DecisionAdvicePayload } from '../api/decisions';
import { decisionsApi, runToOutcome } from '../api/decisions';
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
  /** 入库投影/保留策略标记（服务端 conversation_store 写入，不是前端产出的字段） */
  result_truncated?: boolean;
  result_omitted?: boolean;
  knowledge?: KnowledgeResult;
  /** 服务端轻量引用摘要（knowledge_search 结果被截断时的回退数据源） */
  citation_summary?: { question: string; sources: string[] }[];
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
  /** 来源:上传附件 or `@` 引用的既有文件(云空间归档资产/本会话产物)。
   *  引用类附件要等服务端落盘后才有 path，本轮发送瞬间既无 blobUrl 也无 path——
   *  此时气泡必须说"云空间引用"，不能套"附件已随会话清理"那句话（那是另一回事）。 */
  origin?: 'upload' | 'cloud';
}

/**
 * `@` 引用的文件凭据（随消息提交，服务端展开成附件）。
 *
 * 口径（2026-11-04）：上云 = 备份、归档 = 资产 —— 候选只有归档资产（账号级跨工作空间）
 * 与本会话工作区既有文件；source=asset 的 ref 是资产 id，source=session 的 ref 是工作区
 * 相对路径。文件名只做展示，服务端按 ref 取内容（重名/改名不会引用错文件）。
 */
export interface CloudFileRef {
  source: 'asset' | 'session';
  ref: string;
  filename: string;
  name?: string;
  category?: AttachmentInfo['category'];
  size?: number;
  origin_workspace_name?: string;
  created_at?: string;
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

/** 批量删除的单条失败（title 供 UI 说清“哪个没删掉”，不拿裸 id 面用户）。 */
export interface ConversationDeleteFailure { id: number; title: string; reason: string }
export interface BatchDeleteResult { deleted: number[]; failed: ConversationDeleteFailure[] }

/**
 * 上下文占用（进度条数据源）。
 *
 * 数字唯一来自执行层 SDK 的 `get_context_usage()`（CLI `/context` 同源），服务端已把
 * 分类 type 映射成中文 label（前后端同源，不在前端再写一份）。
 * `available=false` 意为“从未测量”或“上次测量失败”，此时 `used_percentage` 是 null
 * 而不是 0（0% 会被读成“还有很大余量”，是假信号）。
 */
export interface ContextCategory { type: string; label: string; percentage: number }
export interface ContextUsage {
  available: boolean;
  reason?: string;
  /** 采集/读取失败事实（非空 = “显示不出来”，区别于“还没测过”；后者前端直接不渲染） */
  error?: string;
  used_percentage: number | null;
  threshold_percentage: number | null;
  categories: ContextCategory[];
  auto_compact_enabled?: boolean;
  measured_at?: string;
  compact_count?: number;
  model?: string;
  /** 服务端权威判定的“这一会儿不能压缩”（会话执行权被占），不靠前端自己猜 loading */
  busy?: boolean;
}

/** 手动压缩的成功回执（前后占用对比由服务端给，不由前端自己算差值）。 */
export interface CompactResult {
  before_percentage: number | null;
  after_percentage: number | null;
  saved_percentage: number | null;
  compact_count: number;
}

/**
 * 并发排队/提供商繁忙的可见状态（由 SSE `capacity` 事件驱动）。
 *
 * `phase='idle'` 是“本轮没排队也没重试”；终态耗尽时给 `exhausted`，前端据此展示
 * “重试本轮 / 切换模型”两个出口，而不是只留一个“执行失败”。
 */
export interface CapacityState {
  phase: 'idle' | 'queued' | 'started' | 'retrying' | 'exhausted';
  position?: number;
  limit?: number;
  estimated_wait_s?: number | null;
  attempt?: number;
  max_attempts?: number;
  wait_s?: number;
  waited_ms?: number;
  reason?: string;
}

export const IDLE_CAPACITY: CapacityState = { phase: 'idle' };

/** 失败终止时待回填到输入框的草稿（问题 + 待发附件）：不让用户重新打一遍字。 */
export interface DraftRestore { question: string; attachments: PendingAttachment[] }

/**
 * 回答转成的可编辑文档（「转文档编辑」产物）。
 *
 * 正文以富文本 HTML 承载（工具栏的格式都能原样回来，转 markdown 会丢下划线/颜色）；
 * 随会话消息 JSON 一起落库（adh_conversations.messages 是 LONGTEXT），
 * 因此文档与会话同生命周期：会话删了就没了，不另建一套资产表。
 */
export interface ChatDoc {
  title: string;
  /** 正文 HTML；空串 = 还没从回答初稿化（首次打开面板时播种） */
  html: string;
  created_at: string;
  updated_at: string;
  /** 自动保存次数（面板上的「编辑次数」，不是版本号） */
  revisions: number;
}

/**
 * 报告草稿（「存为报告」的第一段）。
 *
 * 为什么留在会话里而不是直接落报告中心：报告中心清单不过滤 publication_status，
 * 草稿一建行就混进正式产物；而草稿的意义正是“还要继续调”。所以草稿随会话消息 JSON
 * 落库（与会话同生命周期，删会话即消失，不新增存储），**点「归档」才进报告中心**
 * （服务端读这份草稿建报告，见 report_service.save_conversation_report）。
 */
export interface ChatReportDraft {
  title: string;
  /** 报告正文：markdown 草稿，或本轮 HTML 报告产物的原文（看 format） */
  markdown: string;
  /** 正文格式（服务端随草稿给）：'html' = 正文是报告产物，不能在 markdown 框里改 */
  format?: string;
  /** 正文来源的产物文件（报告中心按它渲染 HTML） */
  artifact?: { filename: string; type: string } | null;
  /** 正文来源的交付文档对象（渲染机产出）；与 artifact 两存其一 */
  document?: { doc_id: string; body_kind: string } | null;
  created_at: string;
  updated_at: string;
  /** 保存次数（面板上的「已改 N 次」，不是版本号） */
  revisions: number;
  /** 已归档回执：非空 = 已进报告中心，不再给重复归档入口 */
  archived?: { report_id: number; title: string; archived_at: string } | null;
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
  /** 会话存储侧标记：明细被入库投影截断 / 被保留策略裁剪（正文仍在） */
  thinking_truncated?: boolean;
  tool_results_truncated?: boolean;
  process_truncated?: boolean;
  storage_trimmed?: boolean;
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
  // 「存为报告」入口标记: 由服务端报告域判定(done 事件与会话回放同一口径), 前端不自行推断
  reportable?: boolean;
  reportable_reason?: string;
  /**
   * 本轮的决策建议（服务端 decision 出口判定，前端只消费）。
   * 不回存会话 JSON（与 reportable 同处理）：会话回放按 message_uuid 从
   * adh_decision_runs 读回来 —— 重算要逐回合重新取数，而且口径可能已被人改过，
   * 重算出来的不是“当时那条建议”。
   */
  decision_advice?: DecisionAdvicePayload;
  // 「转文档编辑」产物：一条回答最多一份文档（重复点开会继续编辑而不是重复新建）
  doc?: ChatDoc;
  // 「存为报告」草稿：一条回答最多一份，归档后才算报告中心的报告
  reportDraft?: ChatReportDraft;
  progressStages?: ProgressStage[];  // Workflow execution stage history
  activeStage?: string;              // Current active stage key
  workflow_info?: any;               // Deep mode workflow info
  tool_calls?: ToolCall[];           // Agent tool call history
  process?: ProcessSegment[];        // 思考/工具穿插的有序时间线(流式构建,done 对齐)
  executionStats?: {                 // 执行层统计(时间线摘要条)
    num_turns?: number;
    tool_call_count?: number;
    duration_ms?: number;
    // 服务端单调钟口径的回合耗时（与 trace.duration_ms 同源，消息 elapsed 以此为准）
    elapsed_ms?: number;
    // 撞线/触顶时的实况：中止文案要能回答「跑了多久、发了多少次工具、其中取数几次」
    queries?: number;
    timed_out?: boolean;
    turn_limit_seconds?: number;
    tool_rounds?: { limit?: number; count?: number; blocked?: number };
    // 提供商繁忙时服务端自动重试的次数（本轮是"试了几次才成"要看得见）
    retry_attempts?: number;
  };
  pendingAsk?: {                     // Agent ask_user interactive state
    request_id: string;
    question: string;
    options: string[];
  };
  // 输入框 `/` 显式选中的技能名、分析场景 id 与快捷指令 key（随会话消息落库，回放时仍能看到“本轮用了什么”）
  // scenes 必须记：服务端把场景方案注入提示词后，答案看不出“按哪个场景做的”，
  // 不记下来就无法核对“选了没生效”（与 skills/command 同口径）。
  skills?: string[];
  scenes?: string[];
  command?: string;
  // 失败分类（服务端归类，前端不猜错误文案）：繁忙/限流/超时/中止/失败
  errorKind?: 'provider_busy' | 'rate_limited' | 'timeout' | 'aborted' | 'capacity' | 'failed';
  // 服务端认为可重试时才给“重试本轮”按钮（避免把重复取数当无害操作）
  retryable?: boolean;
  retry_after_s?: number;
  retry_attempts?: number;
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
  /**
   * 分页读窗口（长会话不再一次拉全部）。
   * `messageBaseSeq` = 本地 messages[0] 对应的服务端 seq（0 = 整份历史）；
   * `hasMoreEarlier` = 服务端还有更早的消息没加载（渲染“加载更早消息”）。
   * 写回必须带上窗口起点：否则服务端会把“没加载进来的更早消息”当成
   * “数组变短”而删掉 —— 那是抹历史，不是同步。
   */
  messageBaseSeq: number;
  messageTotal: number;
  hasMoreEarlier: boolean;
  loadingEarlier: boolean;
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
  /** 会话查询站点（全部站点=逐站汇总）；只影响默认取数范围 */
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

  /**
   * 本会话上下文占用（聊天框下进度条）。null = 还没拉过或本会话尚未测量过。
   * 只读服务端采集值，不在前端估算 token、不写第二套阈值。
   */
  contextUsage: ContextUsage | null;
  /** CLI autoCompact 正在压缩（SSE `context` 事件驱动）：进度条走 indeterminate 动画。 */
  contextCompacting: boolean;
  /** 手动压缩进行中（按钮自己的 pending 态，不与 contextCompacting 混用）。 */
  compacting: boolean;
  /** 排队/重试可见状态（SSE `capacity` 事件驱动）。 */
  capacity: CapacityState;
  /**
   * 本轮失败/排队后待回填的输入草稿。
   * 现状是失败后正文只剩错误气泡、用户要重新打字 —— 这里把问题与待发附件送回输入框。
   */
  draftRestore: DraftRestore | null;

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
  /** 往前翻一页更早的消息（prepend），并同步后移写回窗口起点。 */
  loadEarlierMessages: () => Promise<void>;
  deleteConversation: (convId: number) => Promise<void>;
  /** 批量删除会话：返回逐条结果，失败原因必须让调用方看得见（不静默丢）。 */
  deleteConversations: (convIds: number[]) => Promise<BatchDeleteResult>;
  renameConversation: (convId: number, title: string) => Promise<void>;
  sendMessage: (question: string, mcpTools?: string[], attachments?: PendingAttachment[],
                opts?: { forceAgent?: boolean; skills?: string[]; scenes?: string[]; command?: string;
                         cloudFiles?: CloudFileRef[] }) => Promise<void>;
  cancelMessage: () => void;
  /** 拉本会话最近一次上下文占用（切换/新建会话与回合结束后重拉）。 */
  loadContextUsage: (conversationId: number | null) => Promise<void>;
  /** 手动压缩上下文；失败显式抛错（由调用方 toast），不返回“假成功”。 */
  compactContext: () => Promise<CompactResult | null>;
  /** 回填输入框后清空草稿（一次性消费，避免下次失败时重复回填旧问题）。 */
  consumeDraftRestore: () => DraftRestore | null;
  /**
   * 重试本轮：先摘掉待重试的那一轮（用户气泡 + 它的错误回复）再原样重发。
   *
   * 为什么要先摘：`sendMessage` 会自己追加一条用户气泡，并把它之前的内容当 history 带走。
   * 不摘就直接重发，记录里会出现两条一模一样的提问（看上去像发了两次），history 里还夹着
   * 那条失败轮次。附件取失败时递回的草稿（与输入框回填同源），无草稿则只重试正文。
   */
  retryLastTurn: () => Promise<void>;
  waitForExecutionRelease: (conversationId: number) => Promise<void>;
  respondToAsk: (requestId: string, response: string) => Promise<void>;
  cancelAsk: (requestId: string) => void;
  updateMessageFeedback: (idx: number, feedback: 'up' | 'down', expectedTable?: string) => void;
  /**
   * 写回某条回答的文档并持久化（「转文档编辑」的自动保存走这里）。
   * 返回是否真的落库：面板要据此显示「已保存 / 保存失败」，不能默默假装成功。
   */
  saveMessageDoc: (idx: number, doc: ChatDoc) => Promise<boolean>;
  /** 写报告草稿（存草稿 / 改稿 / 回填归档回执），落库口径与 saveMessageDoc 一致 */
  saveMessageReportDraft: (idx: number, draft: ChatReportDraft) => Promise<boolean>;
  setViewMode: (idx: number, mode: 'chart' | 'table' | 'sql' | 'profile') => void;
  /**
   * 建议卡上的「完整评估」：由人显式发起，经语义层重新取数并完整判定。
   * turn 模式拿不出完整结论时不自动补这一枪（那会拖慢首答还没人说清），
   * 但必须留下这个入口，否则 partial 就是一句无法行动的公告。
   */
  evaluateTurnDecision: (idx: number, treeKey: string) => Promise<void>;
  analyzeData: (msgIdx: number, question: string) => Promise<void>;
  predictData: (msgIdx: number, question: string) => Promise<void>;
  clear: () => Promise<boolean>;
  /** 删除某一轮（用户提问 + 其回答）；返回是否已落库。 */
  deleteTurn: (idx: number) => Promise<boolean>;
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

/** 把排队/重试的 SSE 事件翻译成可见状态 + 进度文案。
 *
 * 为什么不只看 loading："正在思考"掩盖了三种完全不同事实（在排队、提供商繁忙在重试、
 * 已耗尽），用户面对同样的转圈只能反复刷新。排队位置与预计等待由服务端给出真实值
 * （基于已完成回合均值），前端不自己编倒计时。
 */
function capacityPatch(data: any): { patch: Partial<CapacityState>; step: string } {
  const event = data?.event;
  if (event === 'queued') {
    const pos = Number(data.position) || 1;
    const wait = data.estimated_wait_s;
    return {
      patch: { phase: 'queued', position: pos, limit: Number(data.limit) || 0,
               estimated_wait_s: (typeof wait === 'number' ? wait : null) },
      step: typeof wait === 'number'
        ? `当前使用人数较多，你在第 ${pos} 位，预计约 ${Math.max(1, Math.round(wait))} 秒`
        : `当前使用人数较多，你在第 ${pos} 位（前面还有人待处理）`,
    };
  }
  if (event === 'started') {
    const waited = Math.round((Number(data.waited_ms) || 0) / 1000);
    return { patch: { phase: 'started', waited_ms: Number(data.waited_ms) || 0 },
             step: waited > 0 ? `排队结束，开始处理（已等 ${waited} 秒）` : '开始处理本轮…' };
  }
  if (event === 'retrying') {
    const attempt = Number(data.attempt) || 1;
    const max = Number(data.max_attempts) || attempt;
    const wait = Math.max(1, Math.round(Number(data.wait_s) || 5));
    return {
      patch: { phase: 'retrying', attempt, max_attempts: max, wait_s: wait,
               reason: data.reason || '模型服务繁忙' },
      step: `${data.reason || '模型服务繁忙'}，正在自动重试（第 ${attempt}/${max} 次，等待 ${wait} 秒）`,
    };
  }
  return { patch: { phase: 'idle' }, step: '' };
}

/**
 * 会话入库投影的前端侧镜像（只省带宽，不是权威口径）。
 *
 * 权威规则在 backend/core/conversation_store.py:project_message()（服务端写入前必过），
 * 这里的上限与它保持一致：工具原始返回体/思考链不是用户资产而是中间产物，
 * 实测占会话体积 ~50%（tool_calls.result 22% + process 14% + thinking 13%），
 * 把整份数组 PUT 上去时先截一刀，避免每次保存都上传几百 KB 无人可看的明细。
 */
const PERSIST_TOOL_RESULT_MAX = 4000;
const PERSIST_THINKING_MAX = 4000;

function clipForPersist(value: unknown, max: number): { value: unknown; clipped: boolean } {
  if (typeof value !== 'string') return { value, clipped: false };
  return value.length > max ? { value: value.slice(0, max) + '…', clipped: true } : { value, clipped: false };
}

/** 长会话详情页首屏只读最近这么多条（更早的按需“加载更早消息”）。 */
export const MSG_PAGE_SIZE = 60;

/**
 * 会话回放：把已落的决策建议挂到对应助手消息上（按 message_uuid 对齐）。
 *
 * 为何要单独拉一次：建议不回存会话 JSON（否则同一份结论会在会话正文与
 * 实例表各存一份，一边改了另一边就是双口径）；服务端也不重算（重算=逐回合
 * 重新取数，而且口径可能已被人改过，得到的不是“当时那条建议”）。
 *
 * 拉不到就当没有：未授 decision:read 时这里本来就该 403，而“看不到建议”
 * 正是权限给出的答案（侧栏入口也同步不在），不能为了这个弹错打断开会话。
 */
async function attachDecisionAdvice(convId: number, msgs: ChatMessage[]): Promise<ChatMessage[]> {
  const uuids = msgs.filter(m => m.role === 'assistant' && m.message_uuid).map(m => m.message_uuid!);
  if (!convId || !uuids.length) return msgs;
  try {
    const { data } = await decisionsApi.turnAdvice(Number(convId), uuids);
    const advice = (data && data.advice) || {};
    if (!Object.keys(advice).length) return msgs;
    return msgs.map(m => {
      const rows = m.role === 'assistant' && m.message_uuid ? advice[m.message_uuid] : undefined;
      if (!rows || !rows.length) return m;
      const payload: DecisionAdvicePayload = { items: rows.map(runToOutcome), skipped_reason: '' };
      return { ...m, decision_advice: payload };
    });
  } catch (e) {
    console.debug('[decision] 会话建议回放未取到（无权限或无实例）', e);
    return msgs;
  }
}

export async function saveMessages(
  convId: number, messages: ChatMessage[], title?: string, baseSeq?: number,
): Promise<boolean> {
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
    // 工具原始返回体按上限截断（预览仍在 result_preview，明细在工作区产物里）
    if (Array.isArray(slim.tool_calls) && slim.tool_calls.length) {
      slim.tool_calls = slim.tool_calls.map((tc: any) => {
        if (!tc || typeof tc !== 'object' || tc.result === undefined) return tc;
        const raw = typeof tc.result === 'string' ? tc.result : JSON.stringify(tc.result);
        const { value, clipped } = clipForPersist(raw, PERSIST_TOOL_RESULT_MAX);
        return clipped ? { ...tc, result: value, result_truncated: true } : { ...tc, result: value };
      });
    }
    // 思考链同理截断
    if (typeof slim.thinking === 'string' && slim.thinking) {
      const { value, clipped } = clipForPersist(slim.thinking, PERSIST_THINKING_MAX);
      slim.thinking = value;
      if (clipped) slim.thinking_truncated = true;
    }
    // 原始模型输出与正文同值就是同一份文本的第三份拷贝，不重复上传
    if (typeof slim.ai_raw_response === 'string'
      && slim.ai_raw_response === (slim.content ?? slim.reply)) {
      delete slim.ai_raw_response;
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
    // 决策建议同样不回存：回放走 /decisions/query/turn-advice（实例是唯一来源，
    // 不把同一份建议复制进会话历史正文）
    delete slim.decision_advice;
    return slim;
  });
  const payload: any = { messages: slimMessages };
  if (title) payload.title = title;
  // 窗口起点：不传就是“整份历史”旧口径。分页加载后本地数组并不完整，
  // 必须告诉服务端从哪个 seq 开始，否则服务端会把更早的消息删掉。
  const base = Math.max(0, Math.floor(baseSeq ?? useChatStore.getState().messageBaseSeq ?? 0));
  if (base > 0) payload.message_base_seq = base;
  try {
    await client.put(`/chat/conversations/${convId}`, payload);
    return true;
  } catch (e: any) {
    const detail = String(e?.response?.data?.detail || '');
    if (e?.response?.status === 422 && detail.includes('会话历史已变化')) {
      // 本地窗口与服务端对不上（例如别处刚清空）：重拉会话，不拿旧窗口继续写
      console.warn('会话窗口已过期，重新加载:', detail);
      await useChatStore.getState().switchConversation(convId);
      toast.error('会话记录已变化，已重新加载，请重试刚才的操作');
      return false;
    }
    console.error('Failed to save conversation:', e);
    toast.error('会话记录保存失败，请稍后重试');
    return false;
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
// 空会话回滚前的执行权释放等待上限（清理是后台动作，不值得像"续聊"那样等 120s）
const EMPTY_CONV_RELEASE_TIMEOUT_MS = 20000;

/** 有界轮询会话执行权是否已释放；查不到状态按"未确认"处理（不假装可以安全删除）。 */
async function waitForEmptyConvRelease(conversationId: number): Promise<boolean> {
  const deadline = Date.now() + EMPTY_CONV_RELEASE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const { data } = await client.get('/pipeline/send/status', {
        params: { conversation_id: conversationId },
      });
      if (!data?.busy) return true;
    } catch {
      return false;
    }
    await new Promise(r => setTimeout(r, STOP_POLL_INTERVAL_MS));
  }
  return false;
}
export const useChatStore = create<ChatState>((set, get) => ({
  reset: () => { ++chatContextVersion; ++conversationListVersion; get().abortController?.abort(); set(useChatStore.getInitialState()); },
  conversationError: null,
  conversations: [],
  currentConvId: null,
  messages: [],
  messageBaseSeq: 0,
  messageTotal: 0,
  hasMoreEarlier: false,
  loadingEarlier: false,
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

  contextUsage: null,
  contextCompacting: false,
  compacting: false,
  capacity: IDLE_CAPACITY,
  draftRestore: null,

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
      capabilities: null, loading: false, abortController: null,
      contextUsage: null, contextCompacting: false, capacity: IDLE_CAPACITY });
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
      // 默认站点：唯一具体站点直接选中；多站取聚合态「全部站点」（可用时）；否则清单首个
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
      loading: false, loadingStep: '', abortController: null,
      messageBaseSeq: 0, messageTotal: 0, hasMoreEarlier: false, loadingEarlier: false,
      // 新会话 = 零上下文：进度条必须跟着归零（不能拿上一个会话的占用冒充本会话）
      contextUsage: null, contextCompacting: false, capacity: IDLE_CAPACITY });
  },

  switchConversation: async (convId) => {
    get().abortController?.abort();
    const switching = new AbortController();
    set({ abortController: switching, loading: false, loadingStep: '' });
    try {
      const { data } = await client.get(`/chat/conversations/${convId}`,
        { params: { msg_limit: MSG_PAGE_SIZE } });
      if (get().abortController !== switching) return;
      const msgs = await attachDecisionAdvice(convId, Array.isArray(data.messages) ? data.messages : []);
      // 会话未绑定 AS-BOT(早期数据/创建时未带)时, 默认展示当前工作空间首个可用 AS-BOT, 而非空"选择 AS-BOT"。
      // 全局会话(ws=0, 面板等全局入口所建)跨工作空间互见: 允许用当前工作空间清单兜底; 跨工作空间交由工作空间切换重载处理。
      const sameWs = !data.workspace_id || data.workspace_id === get().selectedWorkspaceId;
      const usable = sameWs ? (get().asBots || []).filter((w) => w.available !== false) : [];
      const defaultAsBot = usable.find((w) => w.is_default)?.as_bot_key || usable[0]?.as_bot_key || null;
      set({
        currentConvId: convId,
        messages: msgs,
        // 分页窗口：首屏只拿最近 MSG_PAGE_SIZE 条，写回起点与“还有更早”跟着走
        messageBaseSeq: Number.isFinite(data.oldest_seq) ? data.oldest_seq : 0,
        messageTotal: Number.isFinite(data.message_total) ? data.message_total : msgs.length,
        hasMoreEarlier: !!data.has_more_earlier,
        loadingEarlier: false,
        abortController: null,
        // 上下文占用属于“当前会话”视图：先清空，由页面据 currentConvId 重拉
        contextUsage: null, contextCompacting: false, capacity: IDLE_CAPACITY,
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

  /**
   * 往前翻一页更早的消息（prepend）。
   *
   * 游标只用服务端给的 oldest_seq，不拿本地数组长度猜（本地可能删过回合，两者会差）。
   * 翻完同步后移写回起点：否则下一次 saveMessages 会拿着旧窗口起点 + 变长的数组
   * 去 diff，把刚拉到的旧消息当成“新增”插到错误位置。
   */
  loadEarlierMessages: async () => {
    const currentConvId = get().currentConvId;
    const oldestSeq = get().messageBaseSeq;
    if (!currentConvId || get().loadingEarlier) return;
    if (oldestSeq <= 0) { set({ hasMoreEarlier: false }); return; }
    set({ loadingEarlier: true });
    try {
      const { data } = await client.get(`/chat/conversations/${currentConvId}`, {
        params: { msg_limit: MSG_PAGE_SIZE, before_seq: oldestSeq },
      });
      const older = await attachDecisionAdvice(currentConvId,
        Array.isArray(data.messages) ? data.messages : []);
      const state = get();
      if (state.currentConvId !== currentConvId) { set({ loadingEarlier: false }); return; }
      set({
        messages: [...older, ...state.messages],
        messageBaseSeq: Number.isFinite(data.oldest_seq) ? data.oldest_seq : oldestSeq,
        messageTotal: Number.isFinite(data.message_total) ? data.message_total : state.messageTotal,
        hasMoreEarlier: !!data.has_more_earlier,
        loadingEarlier: false,
      });
    } catch (e: any) {
      set({ loadingEarlier: false });
      const detail = e?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : '更早消息加载失败，当前记录仍完整，请重试');
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

  deleteConversations: async (convIds) => {
    const ids = Array.from(new Set(convIds.filter(id => Number.isFinite(id) && id > 0)))
    if (!ids.length) return { deleted: [], failed: [] }
    try {
      const { data } = await client.post('/chat/conversations/batch-delete', { conversation_ids: ids })
      const deleted: number[] = data?.deleted || []
      const failed: ConversationDeleteFailure[] = data?.failed || []
      if (deleted.length) {
        set(state => {
          // 当前会话在删除名单里：回到“未保存的新会话”，与单条删除同一语义
          const currentGone = state.currentConvId != null && deleted.includes(state.currentConvId)
          return {
            conversations: state.conversations.filter(c => !deleted.includes(c.id)),
            ...(currentGone
              ? { currentConvId: null, messages: [], executorSessionId: null, capabilities: null }
              : {}),
          }
        })
      }
      return { deleted, failed }
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      // 整批被拒（超限/身份异常/网络）= 一条都没删，全部记为失败，不得报成功
      return {
        deleted: [],
        failed: ids.map(id => ({
          id, title: '',
          reason: typeof detail === 'string' ? detail : '批量删除请求失败，记录仍保留',
        })),
      }
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
      // 断连/异常收场时服务端还要收尾（停 SDK 子进程、释放执行权）几秒到几十秒，
      // 立刻 DELETE 会撞上 409「会话仍在执行」。历史写法是 `catch {}` best-effort：
      // 前端把会话从列表里抹掉、库里那条 messages=[] 的行永远留着（刷新又冒出来，
      // 用户既不知道它存在也没法清理）—— 静默留存视同缺陷。
      // 现在：等执行权释放（有界轮询）→ 删 → 失败重试一次 → 仍失败就显式提醒可手动删除，
      // 并把会话留在列表里（记录确实还在库里，藏起来等于撒谎）。
      const released = await waitForEmptyConvRelease(convId);
      let lastError = '';
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          await client.delete(`/chat/conversations/${convId}`);
          lastError = '';
          break;
        } catch (e: any) {
          lastError = e?.response?.data?.detail || e?.message || '未知错误';
          if (!released && attempt === 0) continue;
          break;
        }
      }
      if (lastError) {
        toast.warning(`本轮未产出内容的会话没能自动清理：${lastError}。稍后可在会话列表手动删除。`);
        return;
      }
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

    // `@` 引用的文件也进本轮附件气泡（origin=cloud，此刻还没有落盘路径，
    // done 事件回带真实 path 后自动换成可预览的普通附件）
    const cloudAtts: AttachmentInfo[] = (opts?.cloudFiles || []).map(c => ({
      filename: c.filename,
      category: c.category || 'document',
      size: c.size,
      origin: 'cloud' as const,
    }));
    const userMsg: ChatMessage = {
      role: 'user',
      content: question,
      ...(attachments?.length || cloudAtts.length
        ? {
            attachments: [
              ...(attachments || []).map(a => ({ filename: a.filename, category: a.category, size: a.size, blobUrl: a.blobUrl })),
              ...cloudAtts,
            ],
          }
        : {}),
      ...(opts?.skills?.length ? { skills: opts.skills } : {}),
      ...(opts?.scenes?.length ? { scenes: opts.scenes } : {}),
      ...(opts?.command ? { command: opts.command } : {}),
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
      // `/` 选中的技能与指令：只影响提示词组装（展开哪份作业细则 / 本轮要做什么），
      // 不是权限开关；服务端对未登记指令显式拒（400）。
      selected_skills: opts?.skills || [],
      // `/` 选中的分析场景 id：服务端按“场景所绑主体”裁决后强制注入查询方案，
      // 无权/不存在的连同原因写进提示词告知用户（不静默丢弃）
      selected_scenes: opts?.scenes || [],
      slash_command: opts?.command || '',
      // `@` 选中的文件引用：服务端在 send_payload 收口点展开成 attachments
      // （归档资产由对象存储取回落盘 uploads/，会话文件沿用工作区校验）
      ...(opts?.cloudFiles?.length
        ? { cloud_files: opts.cloudFiles.map(c => ({ source: c.source, ref: c.ref })) }
        : {}),
      // 问答模式（快速问答/深度分析）入口已撤（用户拍板不区分）：不再上送 chat_mode。
      // 服务端收到空 = 不注入任何模式的作业要求 = 存量单一主路；保留字段只会让人
      // 以为还能靠它切行为（chatStore 里的 chatMode 状态已一并删除，不留死配置）。
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
      // error 事件不再中断读流：后端每条 error 之后必发同文案的 done（带 tool_calls / gate /
      // trace_id 等元信息）。提前 throw 会让已流式输出的正文连同这些元信息一起被丢弃，
      // 用户只看到一张错误卡 —— 中断要显式暴露，但不该把跑出来的结果一并抹掉。
      let streamError = '';

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
              } else if (currentEvent === 'context') {
                // CLI autoCompact 正在进行/已压缩：进度条走 indeterminate 动画。
                // 压缩后的占用数值由 done 事件带回（回合中间发控制请求会抢同一读流，不值得冒险）
                if (isSameConv()) set({ contextCompacting: data?.event === 'compacting' });
              } else if (currentEvent === 'capacity') {
                // 排队 / 自动重试：位置、预计等待、第几次重试都是服务端给的实况
                const { patch, step } = capacityPatch(data);
                if (isSameConv()) {
                  set(state => ({
                    capacity: { ...state.capacity, ...patch },
                    ...(step ? { loadingStep: step } : {}),
                  }));
                }
              } else if (currentEvent === 'done') {
                doneData = data;
              } else if (currentEvent === 'error') {
                streamError = data.message || '服务端返回错误';
                console.error('[SSE] error event:', streamError);
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
                // 断流前已输出的内容保留展示，错误只说明「为什么没跑完」
                content: streamingText || msgs[lastIdx].content || '',
                error: streamError || '服务端响应异常：未收到完成事件，请检查后端日志',
                warnings: [...(msgs[lastIdx].warnings || []), 'SSE 流未正常结束，缺少 done 事件'],
              };
            }
            return { messages: msgs, loading: false, loadingStep: '', abortController: null,
              capacity: IDLE_CAPACITY, contextCompacting: false,
              // 本轮没跑出来：问题与待发附件送回输入框，不让用户重新打一遍
              draftRestore: { question, attachments: attachments || [] } };
          });
          // 同异常收场：有正文就落库，不让刷新后连「跑到哪断的」都看不到
          if (streamingText) {
            const msgs = get().messages;
            await saveMessages(convId, msgs, deriveTitle(msgs));
            persisted = true;
          }
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
      // done 带 error（超时/中断/执行失败）时，已流式输出的正文不得清空：
      // 错误必须显式暴露，但不是把已经跑出来的答案一并抹掉只剩错误卡。
      // streamError 只在 done 本身没给出成功回复时才生效（避免先到的 error 事件覆盖正常结果）。
      const doneError = doneData.error || doneData.result?.error
        || (streamError && !doneData.reply ? streamError : '');
      const partialText = streamingText || existingMsg?.content || '';
      // 失败分类由服务端给（前端不猜错误文案）：繁忙/限流类才拿“重试本轮/切换模型”出口，
      // 否则用户分不清是自己问法问题还是平台过载。
      const doneKind = doneData.error_kind || (doneError ? 'failed' : undefined);
      const capacityExhausted = !!doneError && (doneData.retryable === true
        || doneKind === 'provider_busy' || doneKind === 'rate_limited' || doneKind === 'capacity');
      const finalMsg: ChatMessage = {
        role: 'assistant',
        content: doneError ? partialText : (doneData.reply || streamingText),
        question: question,  // Save original user question for re-execute
        intent: doneData.intent,
        // 错误回合服务端会把错误文案同时塞进 reply；已有正文时不再重复承载，
        // 避免错误文案以「正常回答」的身份进渲染与持久化
        reply: doneError ? undefined : doneData.reply,
        sql: doneData.error ? undefined : doneData.sql,
        warnings: doneData.warnings,
        thinking: doneData.thinking || streamingThinking,
        rag: doneData.rag,
        result: doneData.result,  // Result from SSE stream (auto-executed on backend)
        chart_type: doneData.chart_type,
        brief: doneData.brief,
        tokens: doneData.tokens,
        // 耗时口径以服务端为准（done.stats.elapsed_ms 与 trace.duration_ms 同源）：
        // 浏览器 Date.now() 差值在长回合里会与实际跑满时间差出十几秒，
        // 同一个回合两套数字就没法归因（服务端没给时退回本地计时，仅缺精度不改结论）。
        elapsed_ms: (typeof doneData.stats?.elapsed_ms === 'number' && doneData.stats.elapsed_ms > 0)
          ? doneData.stats.elapsed_ms : elapsed,
        viewMode: doneData.chart_type && doneData.chart_type !== 'table' ? 'chart' : 'table',
        ai_raw_response: doneData.ai_raw_response || streamingText,
        timings: doneData.timings,
        error: doneError,
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
        // 报告入口标记(服务端判定, 只有真产出过数据/分析的回合才给按钮)
        reportable: doneData.reportable,
        reportable_reason: doneData.reportable_reason,
        // 决策建议卡（服务端已判定，无该字段=本轮不相关，不渲染）
        decision_advice: doneData.decision_advice,
        // 有序时间线:流式 process 与最终 tool_calls 对齐,对不上则丢弃回落旧展示
        process: reconcileProcess(existingMsg?.process, finalToolCalls),
        executionStats: doneData.stats,
        errorKind: doneError ? (doneKind as ChatMessage['errorKind']) : undefined,
        retryable: doneError ? !!doneData.retryable : undefined,
        retry_after_s: doneError ? doneData.retry_after_s : undefined,
        retry_attempts: doneData.stats?.retry_attempts ?? doneData.retry_attempts,
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
          // 本轮客户端还活着时采集的上下文占用：直接落状态，进度条不必额外请求就能更新
          contextUsage: doneData.context_usage
            ? { ...doneData.context_usage, available: true, reason: '' } as ContextUsage
            : state.contextUsage,
          contextCompacting: false,
          capacity: capacityExhausted
            ? { ...state.capacity, phase: 'exhausted', reason: doneError } : IDLE_CAPACITY,
          draftRestore: doneError && (!partialText.trim() || doneData.retryable === true)
            ? { question, attachments: attachments || [] } : state.draftRestore,
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

      // 异常收场（断连 / 解析失败 / error 事件后没等到 done）同样不得丢掉已输出的正文：
      // 流式 token 已逐个写进最后一条 assistant 消息，这里只补错误、不重置正文。
      const prev = isSameConv() ? get().messages[get().messages.length - 1] : undefined;
      const mergeable = !!prev && prev.role === 'assistant' && !prev.sql;
      const errorMsg: ChatMessage = {
        ...(mergeable ? prev! : {}),
        role: 'assistant',
        content: prev?.content || '',
        error: e.message,
        elapsed_ms: elapsed,
      };
      if (isSameConv()) {
        set(state => {
          const msgs = [...state.messages];
          if (msgs[msgs.length - 1]?.role === 'assistant' && !msgs[msgs.length - 1]?.sql) {
            msgs[msgs.length - 1] = errorMsg;
          } else {
            msgs.push(errorMsg);
          }
          return { messages: msgs, loading: false, loadingStep: '', abortController: null,
            capacity: IDLE_CAPACITY, contextCompacting: false,
            // 断连/解析异常同样不能把问题丢在错误气泡里让用户重打
            draftRestore: { question, attachments: attachments || [] } };
        });
        // 已输出的正文是用户资产：本轮失败也要落库，刷新后仍能看到中断前的内容
        if (errorMsg.content.trim()) {
          const msgs = get().messages;
          await saveMessages(convId, msgs, deriveTitle(msgs));
          persisted = true;
        }
      }
    } finally {
      // 本次新建但未成功落库的会话统一回滚(成功保存过则 persisted=true 不删)。
      await rollbackEmptyConv();
    }
  },

  loadContextUsage: async (conversationId) => {
    const context = chatContextVersion;
    if (!conversationId) { set({ contextUsage: null, contextCompacting: false }); return; }
    try {
      const { data } = await client.get('/chat/context-usage', {
        params: { conversation_id: conversationId },
      });
      if (context !== chatContextVersion || get().currentConvId !== conversationId) return;
      set({ contextUsage: (data as ContextUsage) || null });
    } catch (e: any) {
      if (context !== chatContextVersion || get().currentConvId !== conversationId) return;
      // 读不到就是读不到：进度条要改说“取不到”并带上原因，不能拿上一轮的旧数字冒充当前值，
      // 也不能显示 0%（那会被读成“还有很大余量”）。
      set({ contextUsage: { available: false, used_percentage: null, threshold_percentage: null,
        categories: [],
        reason: e?.response?.data?.detail || '上下文占用读取失败，请稍后重试',
        error: e?.response?.data?.detail || '上下文占用读取失败，请稍后重试' } });
    }
  },

  compactContext: async () => {
    const { currentConvId, loading, stopping } = get();
    if (!currentConvId) throw new Error('会话尚未落库，没有可压缩的上下文');
    // 前端先拦一层（按钮本应已禁用），服务端仍是权威：两边拿的是同一个执行权事实。
    if (loading || stopping) throw new Error('本轮任务进行中，等它结束或先停止后再压缩');
    set({ compacting: true });
    try {
      const { data } = await client.post('/chat/context-compact', {
        conversation_id: currentConvId,
      });
      const result = data as CompactResult & {
        categories?: ContextCategory[]; threshold_percentage?: number | null;
      };
      // 先用回执立即更新条（用户点它就是要立刻看到效果），再后台重拉服务端权威值
      set(state => (state.contextUsage ? {
        contextUsage: {
          ...state.contextUsage,
          available: result.after_percentage != null,
          used_percentage: result.after_percentage ?? null,
          threshold_percentage: result.threshold_percentage != null
            ? result.threshold_percentage : state.contextUsage.threshold_percentage,
          categories: result.categories?.length ? result.categories : state.contextUsage.categories,
          compact_count: result.compact_count,
        },
      } : {}));
      void get().loadContextUsage(currentConvId);
      return result;
    } finally {
      set({ compacting: false });
    }
  },

  consumeDraftRestore: () => {
    const draft = get().draftRestore;
    if (draft) set({ draftRestore: null });
    return draft;
  },

  retryLastTurn: async () => {
    const state = get();
    if (state.loading || state.stopping) return;
    const idx = [...state.messages].map(m => m.role).lastIndexOf('user');
    if (idx < 0) return;
    const last = state.messages[idx];
    const draft = state.draftRestore;
    const question = (draft?.question || last.question || last.content || '').trim();
    if (!question) return;
    // 技能/场景/指令随原轮次一起重试：只重发正文会变成“同样句话走了另一套作业细则”的隐形改变
    set({ messages: state.messages.slice(0, idx), draftRestore: null });
    await get().sendMessage(question, undefined, draft?.attachments,
      { skills: last.skills, scenes: last.scenes, command: last.command });
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

  evaluateTurnDecision: async (idx, treeKey) => {
    const state = get();
    const msg = state.messages[idx];
    if (!msg || !treeKey) return;
    try {
      const { data } = await decisionsApi.evaluateOne(
        treeKey, Number(state.selectedWorkspaceId || 0));
      set(s => {
        const msgs = [...s.messages];
        const cur = msgs[idx];
        if (!cur) return {};
        const items = (cur.decision_advice?.items || [])
          .map(it => (it.tree_key === data.tree_key ? data : it));
        msgs[idx] = { ...cur, decision_advice: { items, skipped_reason: '' } };
        return { messages: msgs };
      });
    } catch (e: any) {
      // 评估失败必须当场面告知：不能把“没跑起来”演成“这次没问题”
      const detail = e?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : '完整评估未能运行，本轮结论仍按已取到的部分显示');
    }
  },

  saveMessageDoc: async (idx, doc) => {
    const state = get();
    const msgs = [...state.messages];
    if (!msgs[idx]) return false;
    msgs[idx] = { ...msgs[idx], doc };
    set({ messages: msgs });
    if (!state.currentConvId) return false;
    return await saveMessages(state.currentConvId, msgs);
  },

  saveMessageReportDraft: async (idx, draft) => {
    const state = get();
    const msgs = [...state.messages];
    if (!msgs[idx]) return false;
    msgs[idx] = { ...msgs[idx], reportDraft: draft };
    set({ messages: msgs });
    if (!state.currentConvId) return false;
    return await saveMessages(state.currentConvId, msgs);
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

  /**
   * 删除会话中的一轮：以用户提问为锚点，连同该提问之后的回答一起删。
   *
   * 为什么不只删问题：只删提问会留下一个没有上文的回答，回放与后续 history
   * 数组都会错乱（模型会把那句回答当成上一题的继续）。
   * 诚实边界：这只影响展示记录与下一轮上送的 history；执行层 SDK 会话记忆不会
   * 回退（要彻底重来请用 clear / 新建会话），因此调用方的确认框必须把这句说出来。
   * 删到空等价于“清空当前会话”，走 clear() 以同语义重置模型上下文。
   */
  deleteTurn: async (idx: number): Promise<boolean> => {
    const state = get();
    if (state.loading || state.stopping) {
      toast.error('本轮任务进行中，请先停止后再删除记录');
      return false;
    }
    const target = state.messages[idx];
    if (!target || target.role !== 'user') {
      toast.error('只能删除你自己的提问（会连同该问题的回答一起删）');
      return false;
    }
    let end = idx + 1;
    while (end < state.messages.length && state.messages[end].role !== 'user') end += 1;
    const next = [...state.messages.slice(0, idx), ...state.messages.slice(end)];
    if (next.length === 0) {
      // 删完 = 清空：必须走 clear()，它会把执行层会话一并重置（PUT messages=[] 的语义）
      await state.clear();
      return true;
    }
    set({ messages: next });
    if (!state.currentConvId) return true;
    return saveMessages(state.currentConvId, next);
  },

  clear: async () => {
    const { currentConvId, abortController } = get();
    abortController?.abort();
    // 清空 = 擦除「当前会话」的历史消息并重置模型上下文，但**不脱离/新开窗口**：
    // 保留 currentConvId，下一轮仍在同一会话以零上下文继续（后端 PUT messages=[] 已按此语义实现，
    // 会把会话 sdk_session_id 置空但不关闭）。旧实现将 currentConvId 置 null 会让 sendMessage
    // 懒建新会话，表现为“清空后再对话跳出一个新窗口”——那是错误行为。
    set({ messages: [], loading: false, loadingStep: '', executorSessionId: null,
      capabilities: null, abortController: null,
      // 清空后本地就是完整历史（空），窗口起点回到 0：留着旧起点会让下一次
      // 写回把新消息插到已不存在的 seq 上
      messageBaseSeq: 0, messageTotal: 0, hasMoreEarlier: false, loadingEarlier: false,
      // 清空历史同时重置模型上下文，占用条不能还留着压缩前的百分比
      contextUsage: null, contextCompacting: false });
    // 已落库会话：清库历史消息；未落库的新会话（currentConvId=null）无需请求。
    // 返回值供调用方区分“真清完了”与“没清成”（旧实现只 console.error + toast，
    // 调用方无法知道结果，会给用户报一条假的“已清空”）。
    if (!currentConvId) return true;
    try {
      await client.put(`/chat/conversations/${currentConvId}`, { messages: [] });
      return true;
    } catch (e) {
      console.error('Failed to clear conversation:', e);
      toast.error('会话记录未能清空（会话仍在运行中请先停止），请重试');
      return false;
    }
  },
}));
