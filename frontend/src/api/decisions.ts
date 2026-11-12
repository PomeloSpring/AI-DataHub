import client from './client';

/**
 * 决策建议（Decision Advice）前端接口层。
 *
 * 路径分三段且与权限码一一对应（后端 `backend/modules/decision/api/decisions.py` 顶部有
 * 完整理由）：query* = decision:read、config* = decision:manage、publish* = decision:publish。
 * 前端不得把写动作"顺手"挂到 query 前缀下 —— 那等于让只读角色获得发布能力。
 */

export type DecisionVerdict = 'hit' | 'no_hit' | 'partial' | 'skipped';
export type TreeStatus = 'draft' | 'active' | 'retired';
export type AdviceSeverity = 'low' | 'medium' | 'high' | 'critical';

/** 一条判定依据：只有业务名/阈值/实测值（物理名在后端就被拦掉，见 service._assert_clean） */
export interface DecisionEvidence {
  metric: string;
  comparator: string;
  threshold: any;
  measured: any;
  unit?: string;
  dimension?: string;
  time_window?: string;
  label?: string;
  path?: string[];
  note?: string;
  /** false = 这一条没能判定（取数失败/不可比），必须显式显示，不能伪装成"风险未出现" */
  judged?: boolean;
}

export interface DecisionAdviceItem {
  advice_key: string;
  action: string;
  rationale?: string;
  severity: AdviceSeverity;
  kb_refs?: any[];
  /** 文案库里被删掉时后端回的一条显式说明 */
  note?: string;
}

/** 一次评估的对外载荷（Chat 卡片、决策中心、看板控件共用同一形状） */
export interface DecisionOutcomeView {
  tree_key: string;
  tree_name: string;
  tree_version: number;
  verdict: DecisionVerdict;
  reason?: string;
  hit_path: string[];
  advice: DecisionAdviceItem[];
  evidence: DecisionEvidence[];
  site_code?: string;
  mode?: string;
  missing_metrics?: string[];
  unresolved?: { term?: string; reason?: string; candidates?: string[] }[];
  run_id?: number;
  created_at?: string;
  /** 本次评估口径的附加说明（看板筛选参数是否套用）：hit 也必须看得到口径 */
  scope_notes?: string[];
}

export interface DecisionTreeRow {
  id: number;
  tree_key: string;
  name: string;
  description?: string;
  workspace_id: number;
  status: TreeStatus;
  tree_doc: DecisionTreeDocInput;
  required_metrics?: string[];
  bound_object_keys?: string[];
  apply_sites?: { site_code: string; datasource?: string }[];
  schedule_cron?: string;
  timezone?: string;
  notify_channel_id?: number;
  version: number;
  published_at?: string;
  updated_at?: string;
  is_active: number;
}

/** 最近一次命中摘要（清单列用，缺失就是"从未评估"，不显示成"没有风险"） */
export interface DecisionRunRow {
  id: number;
  tree_key: string;
  tree_name: string;
  tree_version: number;
  tree_deleted?: boolean;
  trigger_type: 'chat_turn' | 'manual' | 'scheduled' | 'dashboard' | string;
  site_code?: string;
  verdict: DecisionVerdict;
  verdict_reason?: string;
  hit_path: string[];
  advice_snapshot: DecisionAdviceItem[];
  evidence: DecisionEvidence[];
  elapsed_ms?: number;
  created_at?: string;
}

export interface DecisionTreeDocInput {
  root: DecisionTreeNode;
  note?: string;
}

export type DecisionTreeNode =
  | { type: 'branch'; label: string; op: 'all' | 'any'; children: DecisionTreeNode[] }
  | {
      type: 'leaf'; label: string; advice_key: string; severity?: AdviceSeverity | '';
      evidence_note?: string; when: DecisionConditionInput;
    };

export interface DecisionConditionInput {
  metric: string;
  comparator: string;
  value?: any;
  threshold_ref?: string;
  dimension?: string;
  dimension_value?: any;
  time_window?: string | null;
  time_column?: string;
  unit?: string;
}

export interface DecisionAdviceRow {
  id: number;
  advice_key: string;
  action: string;
  rationale?: string;
  severity: AdviceSeverity;
  kb_refs: any[];
  workspace_id: number;
  is_active: number;
  usage_count?: number;
}

export interface DictTerm {
  name: string;
  name_en?: string;
  aliases?: string[];
  object_key?: string;
  has_object?: boolean;
}

export interface AdviceUpsert {
  advice_key: string;
  action: string;
  rationale?: string;
  severity: AdviceSeverity;
  kb_refs?: string[];
  workspace_id?: number;
  is_active?: boolean;
}

export interface TreeUpsert {
  tree_key: string;
  name: string;
  description?: string;
  workspace_id?: number;
  tree_doc: DecisionTreeDocInput;
  apply_sites?: { site_code: string; datasource?: string }[];
  schedule_cron?: string;
  timezone?: string;
  notify_channel_id?: number;
}

export interface ValidationIssue {
  where: string;
  term: string;
  type: string;
  reason?: string;
  candidates?: string[];
  message: string;
}

/** done 事件与会话回放共用同一个外层形状（items + 一个可解释的未判定说明） */
export interface DecisionAdvicePayload {
  items: DecisionOutcomeView[];
  /** 非 hit 分支的原因（无权限/本轮无取数/结果被截断），前端有值就显示 */
  skipped_reason?: string;
}

/**
 * 看板控件把哪个逻辑参数喂给哪个条件槽位（与后端 `decision.params.SLOTS` 同清单）。
 * 只声明绑定，不拼 filters —— 形状解析唯一在 `semantics.param_plan` 发生。
 */
export interface DecisionParamBinding {
  key: string;
  slot: 'time_window' | 'dimension' | 'site';
  dimension?: string;
  label?: string;
}

const base = '/decisions';

export const decisionsApi = {
  // ── 读面（decision:read）─────────────────────────────────────────
  listTrees: (params?: { workspace_id?: number; status?: string; include_retired?: boolean }) =>
    client.get<{ items: DecisionTreeRow[]; total: number }>(`${base}/query/trees`, { params }),
  getTree: (treeKey: string) =>
    client.get<DecisionTreeRow>(`${base}/query/trees/${encodeURIComponent(treeKey)}`),
  listAdvice: (workspaceId = 0) =>
    client.get<{ items: DecisionAdviceRow[]; total: number }>(`${base}/query/advice`,
      { params: { workspace_id: workspaceId } }),
  listRuns: (params: { workspace_id?: number; tree_key?: string; verdict?: string; page?: number; size?: number }) =>
    client.get<{ items: DecisionRunRow[]; total: number; page: number; size: number }>(
      `${base}/query/runs`, { params }),
  getRun: (runId: number) => client.get<DecisionRunRow>(`${base}/query/runs/${runId}`),
  dictionary: (kind: 'metric' | 'dimension', search = '') =>
    client.get<{ items: DictTerm[]; total: number }>(`${base}/query/dictionary`,
      { params: { kind, search } }),
  /** 批量重算已发布树（决策中心「重新评估」） */
  evaluateAll: (workspaceId: number, site = '') =>
    client.post<{ items: DecisionOutcomeView[]; hit_count: number; evaluated: number; errors: any[] }>(
      `${base}/query/evaluate`, { workspace_id: workspaceId, site }),
  /** 单棵树重算；看板控件额外带 params/param_bindings（后端落 trigger='dashboard'） */
  evaluateOne: (treeKey: string, workspaceId: number, site = '',
                opts?: { params?: Record<string, any>; paramBindings?: DecisionParamBinding[] }) =>
    client.post<DecisionOutcomeView>(`${base}/query/trees/${encodeURIComponent(treeKey)}/evaluate`,
      { workspace_id: workspaceId, site,
        params: opts?.params || {}, param_bindings: opts?.paramBindings || [] }),
  /** 编辑器即时校验（不落库，read 码即可用） */
  validate: (treeDoc: DecisionTreeDocInput) =>
    client.post<{ valid: boolean; errors: ValidationIssue[]; warnings: ValidationIssue[] }>(
      `${base}/query/validate`, { tree_doc: treeDoc }),

  /** 历史回放：按消息 uuid 批量取已落的实例（服务端不重算，只看自己会话） */
  turnAdvice: (conversationId: number, messageUuids: string[]) =>
    client.get<{ advice: Record<string, DecisionRunRow[]> }>(`${base}/query/turn-advice`, {
      params: { conversation_id: conversationId, message_uuids: messageUuids.join(',') },
    }),

  // ── 写面（decision:manage）───────────────────────────────────────
  createTree: (data: TreeUpsert) => client.post(`${base}/config/trees`, data),
  updateTree: (treeKey: string, data: TreeUpsert) =>
    client.put(`${base}/config/trees/${encodeURIComponent(treeKey)}`, data),
  deleteTree: (treeKey: string) => client.delete(`${base}/config/trees/${encodeURIComponent(treeKey)}`),
  createAdvice: (data: AdviceUpsert) => client.post(`${base}/config/advice`, data),
  updateAdvice: (adviceKey: string, data: AdviceUpsert) =>
    client.put(`${base}/config/advice/${encodeURIComponent(adviceKey)}`, data),
  deleteAdvice: (adviceKey: string) =>
    client.delete(`${base}/config/advice/${encodeURIComponent(adviceKey)}`),

  // ── 发布面（decision:publish，两段式的第二段独立权限码）──────────
  publish: (treeKey: string) => client.post(`${base}/publish/${encodeURIComponent(treeKey)}`),
  retire: (treeKey: string) => client.post(`${base}/publish/${encodeURIComponent(treeKey)}/retire`),
  restore: (treeKey: string) => client.post(`${base}/publish/${encodeURIComponent(treeKey)}/restore`),
};

/** 状态/结论的中文口径（显示层唯一出处，页面与卡片不得各写一套） */
export const TREE_STATUS_LABELS: Record<TreeStatus, string> = {
  draft: '草案', active: '已发布', retired: '已下架',
};

export const VERDICT_LABELS: Record<DecisionVerdict, string> = {
  hit: '命中', no_hit: '未命中', partial: '未完整评估', skipped: '未评估',
};

export const SEVERITY_LABELS: Record<AdviceSeverity, string> = {
  low: '低', medium: '中', high: '高', critical: '紧急',
};

/** 触发出口的可读名（实例清单/卡片都从这里取，避免各处自译） */
export const TRIGGER_LABELS: Record<string, string> = {
  chat_turn: '智能问数', manual: '手动评估', scheduled: '定时预警', dashboard: '看板',
};

export function errorText(err: any): string {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail) return JSON.stringify(detail);
  return err?.message || '请求失败';
}

/**
 * 实例行 → 建议载荷（回放与 done 事件共用一个渲染形状）。
 *
 * 服务端两边给的字段名不同（实例行是 advice_snapshot/verdict_reason，done 载荷是
 * advice/reason）；这里只修名字，**不在这层重算也不在这一层判定**：
 * 判定只在服务端引擎发生一次。
 */
export function runToOutcome(run: DecisionRunRow): DecisionOutcomeView {
  return {
    tree_key: run.tree_key,
    tree_name: run.tree_name,
    tree_version: run.tree_version,
    verdict: run.verdict,
    reason: run.verdict_reason,
    hit_path: run.hit_path || [],
    advice: run.advice_snapshot || [],
    evidence: run.evidence || [],
    site_code: run.site_code,
    run_id: run.id,
    created_at: run.created_at,
  };
}
