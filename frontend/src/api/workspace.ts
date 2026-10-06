import client from './client';

export interface Workspace {
  id: number;
  name: string;
  description: string;
  workspace_type: string;
  user_id: number;
  is_default: boolean;
  is_public: boolean;
  allowed_modes: string;
  default_mode: string;
  config: Record<string, any>;
  icon: string;
  color: string;
  datasource_count: number;
  mcp_server_count: number;
  created_at: string;
  updated_at: string;
  datasources?: WorkspaceDatasource[];
  mcp_servers?: WorkspaceMCPServer[];
  agents?: WorkspaceAgent[];
}

export interface WorkspaceDatasource {
  id: number;
  name: string;
  db_type: string;
  is_primary: boolean;
  alias: string;
}

export interface WorkspaceMCPServer {
  id: number;
  name: string;
  description: string;
  alias: string;
}

export interface WorkspaceAgent {
  id: number;
  name: string;
  display_name: string;
  description: string;
  is_enabled: boolean;
}

export interface WorkspaceCreateRequest {
  name: string;
  description?: string;
  workspace_type?: string;
  is_default?: boolean;
  is_public?: boolean;
  allowed_modes?: string;
  default_mode?: string;
  config?: Record<string, any>;
  icon?: string;
  color?: string;
  datasource_ids?: number[];
  agent_names?: string[];
}

export interface WorkspaceUpdateRequest extends Partial<WorkspaceCreateRequest> {}

// ── API Functions ──────────────────────────────────────────────────

export async function listWorkspaces(): Promise<Workspace[]> {
  const res = await client.get('/workspaces');
  return res.data;
}

export async function getWorkspace(id: number): Promise<Workspace> {
  const res = await client.get(`/workspaces/${id}`);
  return res.data;
}

export async function createWorkspace(data: WorkspaceCreateRequest): Promise<Workspace> {
  const res = await client.post('/workspaces', data);
  return res.data;
}

export async function updateWorkspace(id: number, data: WorkspaceUpdateRequest): Promise<Workspace> {
  const res = await client.put(`/workspaces/${id}`, data);
  return res.data;
}

export async function deleteWorkspace(id: number): Promise<void> {
  await client.delete(`/workspaces/${id}`);
}

export async function setDefaultWorkspace(id: number): Promise<void> {
  await client.post(`/workspaces/${id}/set-default`);
}

export async function getWorkspaceTools(id: number): Promise<{
  datasources: any[];
  mcp_servers: any[];
  agents: any[];
  mcp_tools: any[];
}> {
  const res = await client.get(`/workspaces/${id}/tools`);
  return res.data;
}

// ── Resource Management ────────────────────────────────────────────

// 工作空间-数据源绑定 API 已下线：工作空间不再绑定数据源，
// 数据源可用集改由用户角色授权唯一裁决。
// “我的可用数据源”请改用 GET /datasources/authorized
// (见 services/datacatalog/api/datasources.py)。
