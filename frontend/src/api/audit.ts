/**
 * Audit Log API — 查询系统审计日志
 */
import client from './client';

export interface AuditLogItem {
  id: number;
  user_id: number;
  username: string;
  action: string;
  module: string;
  target_type: string;
  target_id: number;
  detail: string;
  ip_address: string;
  created_at: string;
}

export interface AuditLogResponse {
  items: AuditLogItem[];
  total: number;
}

export interface AuditLogParams {
  page?: number;
  size?: number;
  user_id?: number;
  action?: string;
  module?: string;
  start_date?: string;
  end_date?: string;
  keyword?: string;
}

/**
 * 获取审计日志列表
 */
export async function listAuditLogs(params: AuditLogParams = {}): Promise<AuditLogResponse> {
  const { data } = await client.get('/auth/audit-logs', { params });
  return data;
}
