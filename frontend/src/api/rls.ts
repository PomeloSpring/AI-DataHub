import client from './client';

// ── Types ──────────────────────────────────────────────────────────

export interface RLSPolicy {
  id: number;
  name: string;
  description: string;
  workspace_id: number;
  datasource_id: number;
  table_name: string;
  policy_type: 'row' | 'column' | 'table' | 'both';
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
  // 列策略是纯黑名单: 行 = 被剔除/脱敏的列; 'visible'(白名单语义)后端写入口拒绝
  access_type: 'hidden' | 'masked';
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
  deny_reason?: string | null;
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

// ── 选择搜索框候选清单 (ui-resource-display §2: 选择用选择框, 不手填) ──

export interface SelectOption {
  value: string;
  label?: string;
  description?: string;
}

/** 数据源内表清单(源表名 + 注释): 策略表单"目标表"候选 */
export async function listDatasourceTables(datasourceId: number): Promise<SelectOption[]> {
  const { data } = await client.get(`/datasources/${datasourceId}/tables`);
  return (Array.isArray(data) ? data : [])
    .map((r: any) => ({
      value: String(r.TABLE_NAME ?? ''),
      description: r.TABLE_COMMENT ? String(r.TABLE_COMMENT) : undefined,
    }))
    .filter(o => o.value);
}

/** 表内列清单(列名 + 类型/注释): 列策略黑名单"列名"候选 */
export async function listDatasourceColumns(datasourceId: number, tableName: string): Promise<SelectOption[]> {
  const { data } = await client.get(`/datasources/${datasourceId}/tables/${encodeURIComponent(tableName)}/columns`);
  return (Array.isArray(data) ? data : [])
    .map((r: any) => ({
      value: String(r.COLUMN_NAME ?? ''),
      description: [r.DATA_TYPE, r.COLUMN_COMMENT].filter(Boolean).join(' · ') || undefined,
    }))
    .filter(o => o.value);
}

/** 角色属性 key 清单: 行策略"属性名"候选(:user_xxx 替换来源) */
export async function listRoleAttrKeys(): Promise<SelectOption[]> {
  const { data } = await client.get('/admin/role-attr-keys');
  return (Array.isArray(data?.keys) ? data.keys : []).map((k: string) => ({ value: k }));
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
