import client from './client';

export interface SyncTask {
  id: number;
  name: string;
  description: string;
  source_type: string;
  source_config: Record<string, any>;
  target_type: string;
  target_config: Record<string, any>;
  sync_mode: 'full' | 'incremental' | 'cdc';
  schedule_cron: string;
  is_active: boolean;
  last_status: string | null;
  last_run_at: string | null;
  workspace_id: number;
  owner_id: number;
  created_at: string;
  updated_at: string;
}

export interface SyncLog {
  id: number;
  sync_task_id: number;
  task_name: string;
  status: string;
  trigger_type: string;
  rows_read: number;
  rows_written: number;
  rows_failed: number;
  elapsed_ms: number | null;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
  created_at: string;
}

export const syncApi = {
  list: (params: any) => client.get('/sync/tasks', { params }),
  create: (data: any) => client.post('/sync/tasks', data),
  get: (id: number) => client.get(`/sync/tasks/${id}`),
  update: (id: number, data: any) => client.put(`/sync/tasks/${id}`, data),
  delete: (id: number) => client.delete(`/sync/tasks/${id}`),
  run: (id: number) => client.post(`/sync/tasks/${id}/run`),
  toggle: (id: number, is_active: boolean) =>
    client.patch(`/sync/tasks/${id}/toggle`, null, { params: { is_active } }),
  getLogs: (id: number, params?: any) => client.get(`/sync/tasks/${id}/logs`, { params }),
  listLogs: (params?: {
    task_id?: number;
    status?: string;
    start_date?: string;
    end_date?: string;
    page?: number;
    size?: number;
  }) => client.get('/sync/logs', { params }),
};
