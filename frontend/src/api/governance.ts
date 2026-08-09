import client from './client';

export interface Role {
  id: number;
  name: string;
  display_name: string;
  description: string;
  is_system: boolean;
  permissions: RolePermission[];
  created_at: string;
  updated_at: string;
}

export interface RolePermission {
  id: number;
  role_id: number;
  resource: string;
  action: string;
}

export interface AuditLog {
  id: number;
  user_id: number;
  username: string;
  action: string;
  target_type: string;
  target_id: string;
  detail: string;
  ip_address: string;
  created_at: string;
}

export interface DataStandard {
  id: number;
  name: string;
  standard_type: 'naming' | 'encoding' | 'measurement' | 'format';
  description: string;
  rule_config: Record<string, any>;
  is_active: boolean;
  workspace_id: number;
  created_at: string;
  updated_at: string;
}

export interface SensitiveField {
  id: number;
  datasource_id: number;
  table_name: string;
  column_name: string;
  sensitivity_level: 'low' | 'medium' | 'high' | 'critical';
  mask_type: string;
  workspace_id: number;
  created_at: string;
  updated_at: string;
}

export const governanceApi = {
  // Roles
  listRoles: () => client.get('/roles'),
  getRole: (id: number) => client.get(`/roles/${id}`),
  createRole: (data: any) => client.post('/roles', data),
  updateRole: (id: number, data: any) => client.put(`/roles/${id}`, data),
  deleteRole: (id: number) => client.delete(`/roles/${id}`),
  listPermissions: (roleId: number) => client.get(`/roles/${roleId}/permissions`),
  updatePermissions: (roleId: number, permissions: { resource: string; action: string }[]) =>
    client.put(`/roles/${roleId}/permissions`, { permissions }),

  // Audit Logs
  listAuditLogs: (params?: {
    user_id?: number;
    action?: string;
    start_date?: string;
    end_date?: string;
    page?: number;
    size?: number;
  }) => client.get('/audit/logs', { params }),

  // Standards
  getStandards: (workspaceId: number, type?: string) =>
    client.get('/standards', { params: { workspace_id: workspaceId, standard_type: type } }),
  listStandards: (params?: { workspace_id?: number; page?: number; size?: number }) =>
    client.get('/standards', { params }),
  createStandard: (data: any) => client.post('/standards', data),
  updateStandard: (id: number, data: any) => client.put(`/standards/${id}`, data),
  deleteStandard: (id: number) => client.delete(`/standards/${id}`),
  checkCompliance: (id: number) => client.post(`/standards/${id}/check`),

  // Sensitive Fields
  getSensitiveFields: (params: any) => client.get('/security/sensitive-fields', { params }),
  createSensitiveField: (data: any) => client.post('/security/sensitive-fields', data),
  updateSensitiveField: (id: number, data: any) => client.put(`/security/sensitive-fields/${id}`, data),
  deleteSensitiveField: (id: number) => client.delete(`/security/sensitive-fields/${id}`),
  scanSensitiveFields: (datasourceId: number, workspaceId: number) =>
    client.post('/security/scan', null, { params: { datasource_id: datasourceId, workspace_id: workspaceId } }),
};
