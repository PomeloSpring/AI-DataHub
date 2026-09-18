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
  cost_usd: number | null;
  errors: number | null;
  avg_duration_ms: number | null;
  thumbs_up: number | null;
  thumbs_down: number | null;
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
  cost_usd: number | null;
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
  cost_usd: number | null;
  input_text: string | null;
  output_text: string | null;
  error_text: string | null;
  started_at: string;
}

export interface TraceDetail {
  trace: TraceRow & { final_answer: string | null; input_tokens: number; output_tokens: number; feedback_reason?: string | null };
  spans: SpanRow[];
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
