import client from './client';

// ── Types ──────────────────────────────────────────────────────

export interface MonitorSummary {
  scheduled_tasks: { total: number; active: number; unclaimed: number };
  system_jobs: { total: number; paused: number };
  runs: {
    active: number;
    stuck: number;
    failed_total: number;
    timeout_total: number;
    by_kind: Record<string, { active: number; stuck: number; failed_total: number }>;
  };
  sync_tasks: { total: number; active: number };
  dashboard_refresh_tasks?: { total: number; active: number };
}

export interface MonitoredTask {
  kind: 'system_job' | 'scheduled_task' | 'dashboard_refresh';
  task_id: number | string;
  name: string;
  description: string;
  schedule: string;
  timezone?: string;
  task_type: string;
  source: 'system' | 'user';
  removable: boolean;
  enabled: boolean;
  workspace_id: number;
  ownership: 'system' | 'owned' | 'unclaimed';
  owner_name: string;
  questions_total?: number | null;
  last_run_at: string | null;
  last_status: string | null;
  last_result?: string;
  run_count: number;
  active_runs: number;
  stuck_runs: number;
  paused_at?: string | null;
  paused_by?: string;
  timeout_seconds?: number;
}

export interface QueuedRun {
  kind: 'scheduled' | 'report' | 'sync' | 'dashboard_refresh';
  run_id: number;
  task_id: number | null;
  task_name: string;
  workspace_id: number;
  raw_status: string;
  normalized_status: string;
  trigger_type: string;
  started_at: string;
  finished_at: string | null;
  elapsed_ms: number | null;
  worker_id: string | null;
  result_summary: string | null;
  stage_error_code: string | null;
  error_hint: string;
  done_count: number | null;
  fail_count: number | null;
  total_count: number | null;
  rows_read: number | null;
  rows_written: number | null;
  stuck: boolean;
}

export interface SyncTaskRow {
  id: number;
  name: string;
  description: string;
  sync_mode: string;
  source_type: string;
  target_type: string;
  schedule_cron: string | null;
  is_active: number;
  last_run_at: string | null;
  last_status: string | null;
  run_count: number;
  active_runs: number;
}

export interface KbSyncRow {
  model_id: number;
  model_name: string;
  notebook_id: string;
  synced_version: string;
  status: string;
  error_hint: string;
  synced_at: string | null;
}

// ── API ────────────────────────────────────────────────────────

export async function fetchMonitorSummary(): Promise<MonitorSummary> {
  const { data } = await client.get('/task-monitor/summary');
  return data;
}

export async function listMonitoredTasks(params?: {
  source?: 'system' | 'user';
  keyword?: string;
}): Promise<{ items: MonitoredTask[] }> {
  const { data } = await client.get('/task-monitor/tasks', { params });
  return data;
}

export async function stopMonitoredTask(taskId: number): Promise<{ cancelled_runs: number }> {
  const { data } = await client.post(`/task-monitor/tasks/${taskId}/stop`);
  return data;
}

export async function removeMonitoredTask(taskId: number): Promise<{ cancelled_runs: number }> {
  const { data } = await client.delete(`/task-monitor/tasks/${taskId}`);
  return data;
}

export async function toggleDashboardRefresh(taskId: number, isActive: boolean): Promise<{ is_active: boolean }> {
  const { data } = await client.post(`/task-monitor/dashboard-refresh/${taskId}/toggle`, { is_active: isActive });
  return data;
}

export async function triggerDashboardRefresh(taskId: number): Promise<{ run_id: number | null; status: string; cache_hit?: boolean }> {
  const { data } = await client.post(`/task-monitor/dashboard-refresh/${taskId}/trigger`);
  return data;
}

export async function stopDashboardRefresh(taskId: number): Promise<{ cancelled_runs: number }> {
  const { data } = await client.post(`/task-monitor/dashboard-refresh/${taskId}/stop`);
  return data;
}

export async function pauseSystemJob(jobKey: string, paused: boolean): Promise<MonitoredTask> {
  const { data } = await client.post(`/task-monitor/system-jobs/${jobKey}/pause`, { paused });
  return data;
}

export async function listQueuedRuns(params?: {
  kind?: 'scheduled' | 'report' | 'sync' | 'dashboard_refresh';
  status?: string;
  task_id?: number;
  page?: number;
  size?: number;
}): Promise<{ items: QueuedRun[]; total: number }> {
  const { data } = await client.get('/task-monitor/runs', { params });
  return data;
}

export async function stopQueuedRun(kind: string, runId: number): Promise<{ status: string }> {
  const { data } = await client.post(`/task-monitor/runs/${kind}/${runId}/stop`);
  return data;
}

export async function cleanupStaleRuns(timeoutMinutes: number = 10): Promise<{
  scheduled_runs: number; report_runs: number; sync_runs: number; dashboard_refresh_runs?: number;
  // DAG 收口结果：执行中租约过期 / 排队堵塞 / 孤儿节点（三者都算本次清理量）
  dag?: { dag_runs?: number; dag_queued_runs?: number; dag_node_runs?: number };
}> {
  const { data } = await client.post('/task-monitor/cleanup-stale', null, {
    params: { timeout_minutes: timeoutMinutes },
  });
  return data;
}

export async function listMonitoredSyncTasks(): Promise<{ items: SyncTaskRow[] }> {
  const { data } = await client.get('/task-monitor/sync-tasks');
  return data;
}

export async function toggleMonitoredSyncTask(taskId: number, isActive: boolean): Promise<void> {
  await client.post(`/task-monitor/sync-tasks/${taskId}/toggle`, { is_active: isActive });
}

export async function stopMonitoredSyncTask(taskId: number): Promise<{ cancelled_runs: number }> {
  const { data } = await client.post(`/task-monitor/sync-tasks/${taskId}/stop`);
  return data;
}

export async function removeMonitoredSyncTask(taskId: number): Promise<void> {
  await client.delete(`/task-monitor/sync-tasks/${taskId}`);
}

export async function listKbSyncState(): Promise<{ items: KbSyncRow[] }> {
  const { data } = await client.get('/task-monitor/kb-sync');
  return data;
}
