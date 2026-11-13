// 报告中心 API：报告跟随属主（跨工作空间），内容型报告可新建/重命名/编辑/删除。
// 与资产清单的分工：资产=归档的文件（下载复用），报告=稳定内容（在线实时查看）。
//
// 正文不在详情响应里：adh_reports 只存对象 key，详情回「域名 + 签名」的短期直链，
// 正文由浏览器直连对象存储取（fetchReportContent）。这样清单/详情都只搬元信息。
import client from './client';

/** 报告来源分型：scheduled/analysis=数据派生（须过权限快照，正文只读）；
 *  manual/conversation=内容型（人写或会话保存，可在线编辑）；
 *  legacy=迁移前任务产出的历史报告（属主可看可删，但改了就是篡改取数结果 → 只读）。 */
export type ReportSourceType = 'scheduled' | 'analysis' | 'manual' | 'conversation' | 'legacy';

export const EDITABLE_SOURCES: ReportSourceType[] = ['manual', 'conversation'];
// 可删：内容型（含 legacy 存量清理）+ 分析生成（提交式分析、「用最新数据重新生成」追加的版本）。
// 唯 scheduled（定时任务产出）不可在报告中心删 —— 与执行记录一一对应，入口在任务调度。
export const DELETABLE_SOURCES: ReportSourceType[] = ['manual', 'conversation', 'legacy', 'analysis'];

export interface ReportOrigin {
  channel?: string;
  conversation_id?: number;
  conversation_title?: string;
  message_uuid?: string;
  /** 存报告时登记的可复跑问题正文（重新生成/建任务都基于它） */
  question?: string;
  /** 归位说明：非空 = 这句不是本轮原话（本轮只换交付形态），按会话取数事实归位来的 */
  question_note?: string;
  /** 新建时套用的报告模板（服务端登记，报告上能追溯“套的哪个模板”） */
  template_key?: string;
  template_name?: string;
  report_theme?: string;
}

/** 能否「按最新数据重新生成」+ 不可行原因：判断口径在服务端一处，前端只消费 */
export interface ReportRefreshability {
  /** 从这个页面点下去有没有能生效的动作（前端据此置灰，不得自己再拼一套） */
  available: boolean;
  channel: string;   // report=报告自己重跑 | task=触发所属任务 | 空=不可重跑
  /** 按钮该说什么：服务端一并给出（任务产出的报告是“触发所属任务重新生成”，不是重跑报告自） */
  action_label?: string;
  hint: string;
}

/** 「用最新数据重新生成」的版本有没有沿用原报告版式（服务端产出时判定，前端只回显）。
 *  preserved=已按原版式（章节 + 图表）只换了数据 | fact_only=没沿用，原因看 reasons。 */
export interface ReportLayoutStatus {
  has_layout?: boolean;
  status?: string;
  format?: string;
  reasons?: string[];
}

export interface ReportSummary {
  id: number;
  task_id: number | null;
  log_id: number | null;
  title: string;
  format: string;
  access_mode: string;
  view_count: number;
  created_at: string;
  updated_at?: string | null;
  generation_status?: string;
  publication_status?: string;
  task_name?: string | null;
  source_type?: ReportSourceType;
  workspace_id?: number;
  origin?: ReportOrigin;
  refresh?: ReportRefreshability;
  /** 归集键（服务端唯一口径）：同键的报告是同一份报告的历史版本，前端不得自己拼判断 */
  series_key?: string;
  /** 重跑版本的版式沿用结果（仅重跑产物下发；没这个字段说明这一版不是按最新数据生成的） */
  layout?: ReportLayoutStatus;
}

export interface ReportDetail extends ReportSummary {
  /** 正文直链：对象存储模式下是预签名 URL，本地回退模式下是同源代理路径。空 = 正文不可用 */
  content_url?: string;
  /** 正文字节数（服务端记录，用于「还没正文」与「正文为空」的区分） */
  content_size?: number;
  /** 直链到期时间（ISO）；过期后重新取详情即可换新的，正文本身已在内存里 */
  content_expires_at?: string | null;
  access_warning?: string;
  evidence_summary?: ReportEvidence;
}

/** 报告证据摘要：服务端 `evidence_verdict` 一处判定，前端只翻译成人话（不得再拼一套）。 */
export interface ReportEvidence {
  /** governed = 数字都出自已鉴权取数；unverified = 证据不成立（reasons 逐条给原因） */
  validation_status?: string;
  /** 已登记的受治理取数条数 */
  query_count?: number;
  /** 取数通道业务名（execute_sql / run_semantic_query），不含物理表列与数据源标识 */
  sources?: string[];
  /** as_queried = 按查询条件全量；sample_only = 结果被行数上限截断 */
  completeness?: string;
  reasons?: string[];
}

const inSources = (list: ReportSourceType[], s?: ReportSourceType) => !!s && list.includes(s);

/** 正文可否在线编辑（重命名/改正文） */
export function isEditableReport(r: { source_type?: ReportSourceType }): boolean {
  return inSources(EDITABLE_SOURCES, r.source_type);
}

/** 可否在报告中心删除（内容型 + 分析生成；定时任务产出留给任务调度）。
 *  判断口径与后端 report_access.deletable 同源，卡片身份取原始报告后靠它决定删除入口。 */
export function isDeletableReport(r: { source_type?: ReportSourceType }): boolean {
  return inSources(DELETABLE_SOURCES, r.source_type);
}

/** 报告清单：默认拉全部工作空间（0=不过滤），后端按属主 fail-closed */
export async function listReports(workspaceId = 0): Promise<ReportSummary[]> {
  const { data } = await client.get('/reports', { params: { workspace_id: workspaceId, page_size: 200 } });
  const items = Array.isArray(data?.items) ? data.items : Array.isArray(data) ? data : null;
  if (!items) throw new Error('报告清单响应异常（非列表结构），请检查服务日志');
  return items;
}

export async function getReportDetail(reportId: number): Promise<ReportDetail> {
  const { data } = await client.get(`/reports/${reportId}/detail`);
  return data;
}

/**
 * 按详情给的直链取正文。
 *
 * 两条地址形式对应两种存储后端，但调用方只认一个函数：
 * * 绝对 URL = 对象存储预签名直链 → 浏览器直连对象存储。签名本身就是授权，
 *   所以**不带**本站 Authorization 头（带上反而可能被 CORS 预检拒）；
 * * 相对路径 = 同源代理（仅在未配置对象存储的本地回退模式下出现）→ 走 axios
 *   以带上登录态。
 *
 * 失败必须说清原因：直连失败最常见的是桶没允许跨域 GET 或签名过期，
 * 把这两种混成"加载失败"会让人以为报告丢了。
 */
export async function fetchReportContent(contentUrl?: string): Promise<string> {
  if (!contentUrl) throw new Error('报告正文不可用：详情未返回正文地址');
  if (/^https?:\/\//i.test(contentUrl)) {
    let res: Response;
    try {
      res = await fetch(contentUrl, { method: 'GET' });
    } catch {
      throw new Error('读取报告正文失败：无法直连对象存储。'
        + '请检查网络，以及对象存储桶是否允许当前站点跨域 GET（CORS）。');
    }
    if (!res.ok) {
      throw new Error(res.status === 403
        ? '读取报告正文失败：对象存储签名已过期或无效，请刷新报告'
        : `读取报告正文失败：对象存储返回 HTTP ${res.status}`);
    }
    return res.text();
  }
  const { data } = await client.get(contentUrl, {
    baseURL: '', responseType: 'text', transformResponse: [(raw: unknown) => raw],
  });
  return typeof data === 'string' ? data : String(data ?? '');
}

/** 报告草稿初稿（服务端从已落库会话读该轮回答渲染，前端不传正文） */
export interface ReportDraftPayload {
  title: string;
  markdown: string;
  /** 正文格式：本轮交付 HTML 报告产物时为 'html'（正文=产物本身，不是 markdown 草稿） */
  format?: string;
  /** 正文来源的产物文件（服务端从会话工作区读）；无产物时为 null */
  artifact?: { filename: string; type: string } | null;
  /** 正文来源的交付文档对象（渲染机产出，服务端从受管文档表读）；与 artifact 两存其一 */
  document?: { doc_id: string; body_kind: string } | null;
  created_at: string;
  updated_at: string;
  revisions: number;
  message_uuid?: string;
  conversation_id?: number;
}

/**
 * 生成报告草稿（「存为报告」第一步：草稿留在会话里可反复改）。
 *
 * 不可存的回合（纯问答/系统操作）与含基础设施信息的回答都由服务端 400 拒回可解释原因，
 * 调用方必须把那句原因端给用户，不得当成“保存失败请重试”了事。
 */
export async function createReportDraft(payload: {
  conversation_id: number; message_index?: number; message_uuid?: string;
}): Promise<ReportDraftPayload> {
  const { data } = await client.post(
    `/chat/conversations/${payload.conversation_id}/report-draft`,
    { message_index: payload.message_index, message_uuid: payload.message_uuid || '' },
  );
  return data;
}

/** 把会话里的一轮分析（或其报告草稿）归档进报告中心（正文由服务端从已落库会话读） */
export async function saveConversationAsReport(payload: {
  conversation_id: number; title?: string; message_index?: number; message_uuid?: string;
}): Promise<{ report_id: number; title: string; already_archived?: boolean; archived_at?: string }> {
  const { data } = await client.post('/reports', {
    ...payload, source_type: 'conversation', workspace_id: 0,
  });
  return data;
}

export async function updateReport(reportId: number, patch: {
  title?: string; content?: string; format?: string;
}): Promise<{ report_id: number }> {
  const { data } = await client.put(`/reports/${reportId}`, patch);
  return data;
}

/** 删除报表。两个意图必须分开传（旧写法一次带走整份报告的全部历史版本）：
 *  - `version`（默认）：只删这一版，同系列其余历史版本保留；
 *  - `series`：删整份报告（全部历史版本）。 */
export async function deleteReport(
  reportId: number, scope: 'version' | 'series' = 'version',
): Promise<{ report_id: number; scope: string; deleted: number; remaining: number; remaining_base_id: number }> {
  const { data } = await client.delete(`/reports/${reportId}`, { params: { scope } });
  return data;
}

/** 「用最新数据重新生成」的过程事件（服务端一处产出，前端只按类型显示，不自己判结局）。
 *  字段按 type 取用：stage/capacity/data/tool_start/tool_result/text/done 各自的子集。 */
export interface RefreshStreamEvent {
  type: string;
  /** stage: 阶段名（prepared=新版本已建档） */
  step?: string;
  message?: string;
  /** 服务端建好的新版本 id（只有 stage/prepared 带；页面等它跑完再切过去） */
  report_id?: number;
  /** capacity: 并发满员时的排队位置 */
  event?: string;
  position?: number;
  limit?: number;
  estimated_wait_s?: number | null;
  /** data: 一条已登记的受治理取数（通道业务名 + 行数 + 有无截断） */
  query_count?: number;
  row_count?: number;
  truncated?: boolean;
  source?: string;
  /** tool_start / tool_result: 工具业务名与结局（不带 SQL 与结果正文） */
  tool?: string;
  tool_call_id?: string;
  call_index?: number;
  ok?: boolean;
  elapsed?: number;
  /** text: 模型正在写正文（只有字数） */
  chars?: number;
  /** done: 终态 */
  generation_status?: string;
  stage_error_code?: string;
  error_code?: string;
  reason?: string;
  layout_result?: ReportLayoutStatus;
  evidence?: ReportEvidence;
}

/**
 * 按报告登记的原始问题重新取数：**在同一条 SSE 连接里跑完全过程**。
 *
 * 旧写法是 POST /refresh 派发后台任务，页面只得到一句“生成中”（用户实拍：进度看不到，
 * 关掉页面它照跑）。这里改成直播：取了几条数、走到哪个工具、正文在不在写、这一版最终
 * 是 ready 还是证据不全 / 没沿用原版式。
 *
 * **离开即终止**由调用方持有的 ``signal`` 决定：abort() 一按，服务端探到断连就中止本轮，
 * 新版本记为「已取消」（不是超时，也不是失败）。
 *
 * 建档阶段的拒绝（没登记可复跑问题 / 任务产出的报告 / 上一轮还没结束）走 HTTP 4xx，
 * 那句原因必须原样端给用户 —— 压成“重新生成失败请重试”就等于把出路藏起来。
 */
export async function refreshReportStream(reportId: number, handlers: {
  onEvent: (event: RefreshStreamEvent) => void;
  signal: AbortSignal;
}): Promise<void> {
  const token = localStorage.getItem('token');
  const response = await fetch(`/api/reports/${reportId}/refresh-stream`, {
    method: 'POST',
    signal: handlers.signal,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok || !response.body) {
    let reason = '';
    try {
      const body = await response.json();
      const d = body?.detail ?? body?.message;
      if (typeof d === 'string') reason = d;
    } catch { /* 非 JSON 响应体（如网关错误页）：只剩状态码 */ }
    throw new Error(reason ? `重新生成未能开始（${response.status}）：${reason}`
                            : `重新生成未能开始（HTTP ${response.status}）`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
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
        // 空行 = 一帧结束（与 Chat 同口径）；':' 开头的是心跳注释，不进这里
        try {
          handlers.onEvent({ ...JSON.parse(dataBuffer), type: currentEvent || 'stage' });
        } catch {
          console.warn('[report-refresh] 事件体解析失败，已跳过:', dataBuffer.slice(0, 120));
        }
        currentEvent = '';
        dataBuffer = '';
      }
    }
  }
}

/** 从已有报告快捷创建同款 Agent 定时任务（不选 AS-BOT/数据源：按属主角色继承） */
export async function createTaskFromReport(reportId: number, payload: {
  name?: string; cron_expression: string;
  /** 要复用的分析问题：预填服务端登记值，属主可就地改一句（改了会写回报告，重跑同一口径） */
  question?: string;
  notify_on_success?: boolean; notify_on_failure?: boolean;
  /** 选定后任务每次执行都按该模板版式出报告（值=字模 id，显示一律用 name） */
  report_template_key?: string;
}): Promise<{ task_id: number; name: string }> {
  const { data } = await client.post(`/reports/${reportId}/create-task`, payload);
  return data;
}

// ── 报告模板的使用面：报告中心既是消费者（套用模板）也是生产者（另存为模板）──────
// 版式契约形状与后端 report_service 的 layout 一一对应：**只存结构不存数据**，
// 抽取/校验/预览口径全服务端一处（前端不自己解析 markdown，避免预览与真实渲染两套样子）。
export interface ReportTemplateChartSpec {
  title?: string;
  chart_type: string;
  columns: string[];
}

export interface ReportTemplateLayout {
  format?: string;
  headings: string[];
  charts: ReportTemplateChartSpec[];
}

export interface ReportTemplateAnalyzeResult {
  format: string;
  layout: ReportTemplateLayout;
  has_layout: boolean;
  /** 预览用骨架（图表块带“示例甲/乙/丙”标注）；模板本身不保存数值 */
  skeleton: string;
}

/** 抽版式/预览：手工编过 layout 就以它为准，只给 content 则从正文抽。
 * `theme` 必传查看者当前主题：HTML 骨架在隔离 iframe 里渲染，主文档的 CSS 变量进不去，
 * 不带主题就只剩浏览器默认样式（实拍过的“预览没样式”缺陷）。 */
export async function analyzeReportTemplateLayout(payload: {
  content: string; format: string; layout?: ReportTemplateLayout | null; theme?: string;
}): Promise<ReportTemplateAnalyzeResult> {
  const { data } = await client.post('/reports/templates/analyze', payload);
  return data;
}

/** 模板候选项（任务表单/报告中心选模板同一清单 = 报告模板）。
 * 显示一律用 name，value 用模板 id（内部标识不进 UI 主面，见 ui-resource-display §1）；
 * `usable=false` 的模板约束不了任何东西（既没登记版式也抽不出结构），选到时要看得出来。 */
export interface ReportTemplateOption {
  id: string; name: string; format: string; theme: string; usable: boolean;
}

/** 报告模板行（模板中心表格）：版式摘要 + 被任务引用数 */
export interface ReportTemplateRow {
  id: string; code: string; name: string; format: string; theme: string;
  description: string; is_builtin: boolean; is_active: number;
  /** 版式是显式登记的，还是只有旧正文可抽 */
  layout_registered: boolean;
  has_layout: boolean;
  headings: string[];
  charts: ReportTemplateChartSpec[];
  content_preview: string;
  /** 完整正文：编辑对话框回填用（只有 preview 会把正文截断写回去） */
  content: string;
  used_by_tasks: number;
  updated_at: string;
}

// 走报告域 `/reports/templates` 而不是 `/vis-library`：菜单勾「报告模板」就该能读到候选，
// 不该反过来依赖 vis:* 权限（report-template:* 过去绑的是已退役路径，本批收口）。
export async function listReportTemplates(): Promise<ReportTemplateOption[]> {
  const rows = await listReportTemplateRows();
  return rows.filter(r => Number(r.is_active) !== 0).map(r => ({
    id: String(r.id),
    name: r.name,
    format: r.format === 'html' ? 'html' : 'markdown',
    theme: r.theme,
    usable: !!r.has_layout,
  }));
}

export async function listReportTemplateRows(includeInactive = false): Promise<ReportTemplateRow[]> {
  const { data } = await client.get('/reports/templates', {
    params: { include_inactive: includeInactive },
  });
  return Array.isArray(data) ? data : [];
}

/** 主题包选项：配置页主题下拉的唯一来源（免得页面为拿主题再去读字模库） */
export async function listReportThemeOptions(): Promise<{ code: string; theme: string; name: string }[]> {
  const { data } = await client.get('/reports/templates/theme-options');
  return Array.isArray(data) ? data : [];
}

export interface ReportTemplateSavePayload {
  name: string; format: string; theme?: string;
  layout?: ReportTemplateLayout | null; content?: string;
  description?: string; is_builtin?: boolean;
}

/** 新建/更新报告模板：服务端也会拒“没章节也没图表”的空壳版式，前端挡一次不算数 */
export async function saveReportTemplate(payload: ReportTemplateSavePayload, id?: string | number) {
  if (id) {
    const { data } = await client.put(`/reports/templates/${id}`, payload);
    return data as { id: number; layout: ReportTemplateLayout };
  }
  const { data } = await client.post('/reports/templates', payload);
  return data as { id: number; layout: ReportTemplateLayout };
}

/** 被定时任务引用的模板服务端拒删并回引用数，调用方必须把 detail 端给用户 */
export async function deleteReportTemplate(id: string | number): Promise<{ success: boolean }> {
  const { data } = await client.delete(`/reports/templates/${id}`);
  return data;
}

/** 按模板取报告骨架（「套用模板」起稿）；模板不可用时后端 422 回原因，调用方必须端给用户 */
export async function fetchReportTemplateSkeleton(templateKey: string, theme?: string): Promise<{
  name: string; format: string; theme: string;
  layout: ReportTemplateLayout; skeleton: string;
}> {
  // theme = 查看者当前主题（模板自己锁了主题时服务端以模板为准）：新建的报告第一版就该带样式
  const { data } = await client.get(
    `/reports/templates/${encodeURIComponent(templateKey)}/skeleton`,
    { params: theme ? { theme } : undefined });
  return data;
}

/** 把本报告的版式沉淀成报告模板；无可沉淀结构时后端 422 回可解释原因 */
export async function saveReportAsTemplate(reportId: number, payload: {
  name?: string; description?: string;
}): Promise<{ component_id: number; name: string; layout: ReportTemplateLayout }> {
  const { data } = await client.post(`/reports/${reportId}/save-as-template`, payload);
  return data;
}
