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
 *
 * 后端路由挂在 `/api/audit/logs`（main.py: mount("auth", audit_router, "/api/audit")），
 * 不是历史上的 `/api/auth/audit-logs` —— 旧路径 404 会被页面 catch 吞成“暂无审计日志”。
 */
export async function listAuditLogs(params: AuditLogParams = {}): Promise<AuditLogResponse> {
  const { data } = await client.get('/audit/logs', { params });
  return data;
}
