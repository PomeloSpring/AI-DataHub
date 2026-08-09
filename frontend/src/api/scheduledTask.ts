import client from './client';

// ── Types ──────────────────────────────────────────────────────

export interface ScheduledTask {
  id: number;
  name: string;
  description: string;
  task_type: 'query' | 'agent';
  task_config: TaskConfig;
  report_template_key: string | null;
  cron_expression: string;
  timezone: string;
  channel_id: number | null;
  notify_on_success: boolean;
  notify_on_failure: boolean;
  is_active: boolean;
  workspace_id: number;
  owner_id: number;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  run_count: number;
  timeout_seconds: number;
  max_retries: number;
  created_at: string;
  updated_at: string;
}

export interface TaskConfig {
  datasource_id: number;
  questions: TaskQuestion[];
  agent_name?: string;
  context?: string;
}

export interface TaskQuestion {
  sql?: string;
  question?: string;
  title: string;
}

export interface ScheduledLog {
  id: number;
  scheduled_task_id: number;
  workspace_id: number;
  status: string;
  trigger_type: string;
  celery_task_id: string | null;
  result_summary: string | null;
  result_data: any;
  error_message: string | null;
  questions_executed: string[] | null;
  questions_succeeded: number;
  questions_failed: number;
  report_content: string | null;
  channel_response: string | null;
  notify_status: string | null;
  elapsed_ms: number | null;
  token_usage: any;
  worker_id: string | null;
  started_at: string;
  finished_at: string | null;
  created_at: string;
}

export interface NotificationChannel {
  id: number;
  name: string;
  channel_type: string;
  config: Record<string, any>;
  is_active: boolean;
  workspace_id: number;
  owner_id: number;
  last_test_at: string | null;
  last_test_status: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduledTaskCreateRequest {
  name: string;
  description?: string;
  task_type: 'query' | 'agent';
  task_config: TaskConfig;
  report_template_key?: string;
  cron_expression: string;
  timezone?: string;
  channel_id?: number;
  notify_on_success?: boolean;
  notify_on_failure?: boolean;
  is_active?: boolean;
  workspace_id?: number;
  timeout_seconds?: number;
  max_retries?: number;
}

export type ScheduledTaskUpdateRequest = Partial<ScheduledTaskCreateRequest>;

export interface NotificationChannelCreateRequest {
  name: string;
  channel_type: string;
  config: Record<string, any>;
  is_active?: boolean;
  workspace_id?: number;
}

export type NotificationChannelUpdateRequest = Partial<NotificationChannelCreateRequest>;

// ── Scheduled Tasks API ────────────────────────────────────────

export async function listScheduledTasks(params?: {
  workspace_id?: number;
  page?: number;
  size?: number;
}): Promise<{ items: ScheduledTask[]; total: number }> {
  const { data } = await client.get('/scheduled-tasks/tasks', { params });
  return data;
}

export async function getScheduledTask(id: number): Promise<ScheduledTask> {
  const { data } = await client.get(`/scheduled-tasks/tasks/${id}`);
  return data;
}

export async function createScheduledTask(req: ScheduledTaskCreateRequest): Promise<{ id: number }> {
  const { data } = await client.post('/scheduled-tasks/tasks', req);
  return data;
}

export async function updateScheduledTask(id: number, req: ScheduledTaskUpdateRequest): Promise<void> {
  await client.put(`/scheduled-tasks/tasks/${id}`, req);
}

export async function deleteScheduledTask(id: number): Promise<void> {
  await client.delete(`/scheduled-tasks/tasks/${id}`);
}

export async function toggleScheduledTask(id: number, isActive: boolean): Promise<void> {
  await client.patch(`/scheduled-tasks/tasks/${id}/toggle`, null, { params: { is_active: isActive } });
}

export async function triggerScheduledTask(id: number): Promise<{ celery_task_id: string }> {
  const { data } = await client.post(`/scheduled-tasks/tasks/${id}/trigger`);
  return data;
}

export async function regenerateWebhookToken(id: number): Promise<{ webhook_token: string }> {
  const { data } = await client.post(`/scheduled-tasks/tasks/${id}/regenerate-webhook-token`);
  return data;
}

// ── Execution Logs API ─────────────────────────────────────────

export async function listScheduledLogs(
  taskId: number,
  params?: { page?: number; size?: number; status?: string }
): Promise<{ items: ScheduledLog[]; total: number }> {
  const { data } = await client.get(`/scheduled-tasks/tasks/${taskId}/logs`, { params });
  return data;
}

export async function getScheduledLog(logId: number): Promise<ScheduledLog> {
  const { data } = await client.get(`/scheduled-tasks/logs/${logId}`);
  return data;
}

export async function getScheduledTaskStats(taskId: number): Promise<{
  total_runs: number;
  success_runs: number;
  failed_runs: number;
  success_rate: number;
  avg_elapsed_ms: number;
}> {
  const { data } = await client.get(`/scheduled-tasks/tasks/${taskId}/stats`);
  return data;
}

export async function cleanupScheduledLogs(days: number = 30): Promise<{ deleted: number }> {
  const { data } = await client.delete('/scheduled-tasks/logs/cleanup', { params: { days } });
  return data;
}

export async function updateLogStatus(logId: number, status: string, errorMessage?: string): Promise<void> {
  await client.patch(`/scheduled-tasks/logs/${logId}/status`, null, {
    params: { status, error_message: errorMessage },
  });
}

export async function cleanupStaleLogs(timeoutMinutes: number = 10): Promise<{ cleaned: number }> {
  const { data } = await client.post('/scheduled-tasks/logs/cleanup-stale', null, {
    params: { timeout_minutes: timeoutMinutes },
  });
  return data;
}

// ── Notification Channels API ──────────────────────────────────

export async function listNotificationChannels(workspaceId?: number): Promise<NotificationChannel[]> {
  const { data } = await client.get('/notification/channels', { params: { workspace_id: workspaceId } });
  return data;
}

export async function getNotificationChannel(id: number): Promise<NotificationChannel> {
  const { data } = await client.get(`/notification/channels/${id}`);
  return data;
}

export async function createNotificationChannel(req: NotificationChannelCreateRequest): Promise<{ id: number }> {
  const { data } = await client.post('/notification/channels', req);
  return data;
}

export async function updateNotificationChannel(id: number, req: NotificationChannelUpdateRequest): Promise<void> {
  await client.put(`/notification/channels/${id}`, req);
}

export async function deleteNotificationChannel(id: number): Promise<void> {
  await client.delete(`/notification/channels/${id}`);
}

export async function testNotificationChannel(id: number): Promise<{ success: boolean; result: any }> {
  const { data } = await client.post(`/notification/channels/${id}/test`);
  return data;
}

// ── Report Templates API ────────────────────────────────────────

export interface ReportTemplate {
  id: number;
  name: string;
  description: string;
  content: string;
  format: 'markdown' | 'html';
  is_system: boolean;
  workspace_id: number;
  owner_id: number;
  created_at: string;
  updated_at: string;
}

export async function listReportTemplates(workspaceId?: number): Promise<ReportTemplate[]> {
  const { data } = await client.get('/scheduled-tasks/templates', { params: { workspace_id: workspaceId } });
  return data;
}
