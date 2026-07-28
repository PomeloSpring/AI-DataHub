import client from './client';

// ── Types ──────────────────────────────────────────────────────────

export interface RLSPolicy {
  id: number;
  name: string;
  description: string;
  workspace_id: number;
  datasource_id: number;
  table_name: string;
  policy_type: 'row' | 'column' | 'both';
  filter_type: 'condition' | 'user_attribute';
  filter_expr: string;
  user_attribute: string;
  is_active: number;
  created_by: number;
  created_at: string;
  updated_at: string;
}

export interface RLSColumnPolicy {
  id: number;
  policy_id: number;
  column_name: string;
  access_type: 'visible' | 'hidden' | 'masked';
  mask_pattern: string;
  description: string;
}

export interface RLSUserAttribute {
  attr_key: string;
  attr_value: string;
}

export interface RLSAuditLog {
  id: number;
  user_id: number;
  workspace_id: number;
  policy_id: number;
  policy_name: string;
  table_name: string;
  action: string;
  original_sql: string;
  filtered_sql: string;
  created_at: string;
}

// ── API Functions ──────────────────────────────────────────────────

export async function listRLSPolicies(
  workspaceId: number,
  datasourceId?: number,
  tableName?: string,
  page = 1,
  size = 20
): Promise<{ total: number; items: RLSPolicy[] }> {
  const params: Record<string, any> = { workspace_id: workspaceId, page, size };
  if (datasourceId) params.datasource_id = datasourceId;
  if (tableName) params.table_name = tableName;
  const { data } = await client.get('/admin/rls-policies', { params });
  return data;
}

export async function getRLSPolicy(policyId: number): Promise<RLSPolicy> {
  const { data } = await client.get(`/admin/rls-policies/${policyId}`);
  return data;
}

export async function createRLSPolicy(policy: Partial<RLSPolicy>): Promise<{ success: boolean; id: number }> {
  const { data } = await client.post('/admin/rls-policies', policy);
  return data;
}

export async function updateRLSPolicy(policyId: number, updates: Partial<RLSPolicy>): Promise<{ success: boolean }> {
  const { data } = await client.put(`/admin/rls-policies/${policyId}`, updates);
  return data;
}

export async function deleteRLSPolicy(policyId: number): Promise<{ success: boolean }> {
  const { data } = await client.delete(`/admin/rls-policies/${policyId}`);
  return data;
}

export async function getRLSColumnPolicies(policyId: number): Promise<RLSColumnPolicy[]> {
  const { data } = await client.get(`/admin/rls-policies/${policyId}/columns`);
  return data;
}

export async function setRLSColumnPolicies(
  policyId: number,
  columns: Partial<RLSColumnPolicy>[]
): Promise<{ success: boolean }> {
  const { data } = await client.put(`/admin/rls-policies/${policyId}/columns`, { columns });
  return data;
}

export async function getRLSUserAttributes(userId: number, workspaceId: number): Promise<Record<string, string>> {
  const { data } = await client.get(`/admin/rls-user-attributes/${userId}`, {
    params: { workspace_id: workspaceId },
  });
  return data;
}

export async function setRLSUserAttributes(
  userId: number,
  workspaceId: number,
  attributes: Record<string, string>
): Promise<{ success: boolean }> {
  const { data } = await client.put(`/admin/rls-user-attributes/${userId}`, {
    workspace_id: workspaceId,
    attributes,
  });
  return data;
}

export async function listRLSAuditLogs(
  workspaceId: number,
  userId?: number,
  page = 1,
  size = 20
): Promise<{ total: number; items: RLSAuditLog[] }> {
  const params: Record<string, any> = { workspace_id: workspaceId, page, size };
  if (userId) params.user_id = userId;
  const { data } = await client.get('/admin/rls-audit-logs', { params });
  return data;
}
