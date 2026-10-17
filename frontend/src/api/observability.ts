/**
 * Observability API — 管理端 LLM 交互可观测（只读）
 */
import client from './client';

// 可观测表使用无时区的 UTC DATETIME；请求保持该格式，界面显示本地时间。
export function toObservabilityUTC(date: Date): string {
  return date.toISOString().slice(0, -1).replace('T', ' ');
}

export function parseObservabilityTime(value: string): Date {
  const iso = value.replace(' ', 'T');
  return new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`);
}

export function observabilityTimeToInput(value: string): string {
  if (!value) return '';
  const date = parseObservabilityTime(value);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function observabilityTimeFromInput(value: string): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : toObservabilityUTC(date);
}

export function observabilityTimeRange(hours: number, now = new Date()): { start: string; end: string } {
  if (hours <= 0) return { start: '', end: '' };
  return {
    start: toObservabilityUTC(new Date(now.getTime() - hours * 3600 * 1000)),
    end: toObservabilityUTC(now),
  };
}

export interface CommonFilters {
  user_id?: number;
  workspace_id?: number;
  datasource_id?: number;
  entrypoint?: string;
  status?: string;
  model_ref?: string;
  /** 处理方式：agent=走了执行层模型 / intent_gate=前置意图层本地直答 */
  handled_by?: string;
  start?: string;
  end?: string;
}

export interface UsageSummary {
  turns: number;
  sessions: number;
  users: number;
  llm_calls: number;
  total_tokens: number;
  credits: number | null;
  /** 本回合成本：CLI 上报（USD）或按 credit 单价折算（币种由 OBSERVABILITY_CREDIT_COST_* 配置） */
  cost_amount: number | null;
  cost_currency: string | null;
  errors: number | null;
  avg_duration_ms: number | null;
  thumbs_up: number | null;
  thumbs_down: number | null;
  // 前置意图漏斗：拦截回合数与拦截率（trace.handled_by='intent_gate'）
  intercepted?: number | null;
  intercept_rate?: number | null;
  by_handled_by?: Array<{ handled_by: string; turns: number; total_tokens: number; credits: number | null }>;
  avg_agent_turn_credits?: number | null;
  /** 估算值：拦截回合数 × 同窗口 agent 回合均值 credit（Qoder 不回报单回合成本，无实测口径） */
  estimated_saved_credits?: number | null;
  saved_credits_is_estimate?: boolean;
}

export interface DailyPoint {
  dt: string;
  turns: number;
  total_tokens: number;
  credits: number | null;
  errors: number | null;
}

export interface ModelUsage {
  model_ref: string;
  turns: number;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  credits: number | null;
  cost_amount: number | null;
  cost_currency: string | null;
}

export interface UserUsage {
  user_id: number;
  username: string;
  user_role: string;
  turns: number;
  sessions: number;
  total_tokens: number;
  credits: number | null;
  errors: number | null;
  last_active: string;
}

export interface SessionRow {
  conversation_id: number;
  title: string | null;
  user_id: number;
  username: string;
  workspace_id: number;
  datasource_id: number;
  turns: number;
  total_tokens: number;
  credits: number | null;
  session_credits: number | null;
  errors: number | null;
  first_active: string;
  last_active: string;
}

export interface TraceRow {
  trace_id: string;
  message_uuid: string;
  conversation_id: number;
  user_id: number;
  username: string;
  entrypoint: string;
  /** 处理方：agent=模型回答 / intent_gate=前置意图层本地直答（未调用模型） */
  handled_by?: string;
  model_ref: string;
  status: string;
  question: string;
  error_message: string | null;
  started_at: string;
  duration_ms: number;
  total_tokens: number;
  credits: number | null;
  session_credits: number | null;
  llm_call_count: number;
  span_count: number;
  feedback_satisfied: number | null;
}

export interface SpanRow {
  span_id: string;
  trace_id: string;
  kind: string;
  name: string;
  status: string;
  duration_ms: number;
  model_ref: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  credits: number | null;
  original_credits: number | null;
  billable: number | null;
  cost_amount: number | null;
  input_text: string | null;
  output_text: string | null;
  error_text: string | null;
  started_at: string;
}

export interface TraceDetail {
  trace: TraceRow & { final_answer: string | null; input_tokens: number; output_tokens: number; feedback_reason?: string | null };
  spans: SpanRow[];
}

/** Qoder 账号 credit 配额桶（执行层 get_usage_info 实测值；Qoder 不回报美元成本） */
export interface QuotaBucket {
  source: string;
  label: string;
  unit: string;
  used: number | null;
  total: number | null;
  remaining: number | null;
  percentage: number | null;
  available: boolean;
  detail_url: string;
}

export interface QuotaInfo {
  available: boolean;
  reason?: string;
  cached?: boolean;
  layer?: string;
  user_type?: string;
  unit?: string;
  buckets?: QuotaBucket[];
  total_usage_percentage?: number | null;
  is_quota_exceeded?: boolean;
  expires_at?: string;
  upgrade_url?: string;
  fetched_at?: string;
}

export async function getUsageSummary(f: CommonFilters = {}) {
  const { data } = await client.get('/observability/usage/summary', { params: f });
  return data as { summary: UsageSummary; daily: DailyPoint[] };
}

export async function getUsageByModel(f: CommonFilters = {}) {
  const { data } = await client.get('/observability/usage/models', { params: f });
  return data.items as ModelUsage[];
}

export async function getUsageByUser(f: CommonFilters = {}) {
  const { data } = await client.get('/observability/usage/users', { params: f });
  return data.items as UserUsage[];
}

export async function listSessions(f: CommonFilters & { page?: number; size?: number } = {}) {
  const { data } = await client.get('/observability/sessions', { params: f });
  return data as { items: SessionRow[]; total: number };
}

export async function listTraces(f: CommonFilters & { conversation_id?: number; page?: number; size?: number } = {}) {
  const { data } = await client.get('/observability/traces', { params: f });
  return data as { items: TraceRow[]; total: number };
}

export async function getTrace(traceId: string) {
  const { data } = await client.get(`/observability/traces/${traceId}`);
  return data as TraceDetail;
}

/** Qoder 账号 credit 配额（需起一次执行层 CLI，服务端带 TTL 缓存；refresh=true 强制回源） */
export async function getQuota(refresh = false) {
  const { data } = await client.get('/observability/quota', { params: { refresh } });
  return data as QuotaInfo;
}

// ── 前置意图漏斗（adh_intent_intercepts 判定流水）───────────────────────

export interface FunnelFilters {
  workspace_id?: number;
  user_id?: number;
  datasource_id?: number;
  conversation_id?: number;
  as_bot_key?: string;
  mode?: string;
  label?: string;
  decision?: string;
  start?: string;
  end?: string;
}

export interface FunnelStats {
  available: boolean;
  reason?: string;
  overall?: {
    judged: number; local: number | null; shadow_local: number | null; to_agent: number | null;
    gate_errors: number | null; forced_agent: number | null; avg_latency_ms: number | null;
  };
  intercept_rate?: number;
  eligible_rate?: number;
  by_label?: Array<{ label: string; decision: string; cnt: number; avg_latency_ms: number | null }>;
  daily?: Array<{ dt: string; judged: number; local: number | null; shadow_local: number | null; gate_errors: number | null }>;
}

export interface InterceptRow {
  id: number;
  dt: string;
  trace_id: string;
  conversation_id: number;
  user_id: number;
  username: string;
  workspace_id: number;
  datasource_id: number;
  as_bot_key: string;
  as_bot_name: string;
  mode: string;
  layer: string;
  label: string;
  decision: string;
  reason: string;
  signals: string;
  question_digest: string;
  latency_ms: number;
  gate_error: string;
  force_agent: number;
  reviewed: number;
  review_note: string;
}

export async function getFunnelStats(f: FunnelFilters = {}) {
  const { data } = await client.get('/observability/funnel/stats', { params: f });
  return data as FunnelStats;
}

export async function listFunnelIntercepts(f: FunnelFilters & { page?: number; size?: number } = {}) {
  const { data } = await client.get('/observability/funnel/intercepts', { params: f });
  return data as { available: boolean; reason?: string; items: InterceptRow[]; total: number };
}

export async function reviewFunnelIntercept(id: number, reviewed: boolean, note = '') {
  const { data } = await client.post(`/observability/funnel/intercepts/${id}/review`, { reviewed, note });
  return data as { ok: boolean };
}
