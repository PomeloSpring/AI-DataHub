import client from './client';

// ── 类型 ────────────────────────────────────────────────────────
export interface DagNodeConfig {
  // sync 节点
  source_datasource?: string;
  source_table?: string;
  target_datasource?: string;
  target_table?: string;
  sync_mode?: 'full' | 'incremental';
  incremental_column?: string;
  write_mode?: 'append' | 'overwrite';
  batch_size?: number;
  // sql_task 节点
  sql?: string;
  udf_refs?: string[];
  target?: { datasource: string; table: string; write_mode?: string };
  // control 节点
  action?: 'pass' | 'fail';
  message?: string;
  [key: string]: any;
}

export interface DagNode {
  key: string;
  name?: string;
  type: 'sync' | 'sql_task' | 'control';
  config: DagNodeConfig;
}

export interface DagEdge {
  from: string;
  to: string;
}

export interface DagGraph {
  nodes: DagNode[];
  edges: DagEdge[];
}

export interface DagWorkflow {
  id: number;
  name: string;
  description?: string;
  /** simple=简单同步（单节点） / dag=多节点编排 */
  kind?: 'simple' | 'dag';
  /** 列表展示用数据流摘要：源表 → 目标表 */
  dataflow_summary?: string;
  graph_json?: DagGraph;
  version?: number;
  cron_expression?: string | null;
  timezone?: string;
  is_active?: number | boolean;
  workspace_id?: number;
  owner_id?: number;
  last_run_at?: string | null;
  last_status?: string | null;
  run_count?: number;
  timeout_seconds?: number;
  max_retries?: number;
  created_at?: string;
  updated_at?: string;
}

export interface DagNodeRun {
  id: number;
  node_key: string;
  node_type: string;
  status: string;
  attempt: number;
  rows_read: number;
  rows_written: number;
  elapsed_ms: number | null;
  error_code?: string;
  error_message?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface DagRun {
  id: number;
  workflow_id: number;
  workflow_name?: string;
  run_key: string;
  trigger_type: string;
  status: string;
  stats_json?: Record<string, any>;
  error_code?: string;
  error_message?: string;
  node_count?: number;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string;
}

// ── API ─────────────────────────────────────────────────────────
export const dagApi = {
  listWorkflows: (params?: { page?: number; size?: number; workspace_id?: number }) =>
    client.get('/dag/workflows', { params }),
  createWorkflow: (data: Partial<DagWorkflow>) => client.post('/dag/workflows', data),
  getWorkflow: (id: number) => client.get(`/dag/workflows/${id}`),
  updateWorkflow: (id: number, data: Partial<DagWorkflow> & { version?: number }) =>
    client.put(`/dag/workflows/${id}`, data),
  deleteWorkflow: (id: number) => client.delete(`/dag/workflows/${id}`),
  runWorkflow: (id: number) => client.post(`/dag/workflows/${id}/run`),
  listWorkflowRuns: (id: number, params?: { page?: number; size?: number; status?: string }) =>
    client.get(`/dag/workflows/${id}/runs`, { params }),
  listRuns: (params?: { page?: number; size?: number; workflow_id?: number; status?: string }) =>
    client.get('/dag/runs', { params }),
  getRun: (runId: number) => client.get(`/dag/runs/${runId}`),
  stopRun: (runId: number) => client.post(`/dag/runs/${runId}/stop`),
  retryRun: (runId: number) => client.post(`/dag/runs/${runId}/retry`),
  sqlToDag: (sql: string, datasource: string, target_datasource?: string) =>
    client.post('/dag/translate/sql-to-dag', {
      sql, datasource, target_datasource: target_datasource || '',
    }),
  dagToSql: (graph_json: DagGraph) => client.post('/dag/translate/dag-to-sql', { graph_json }),
  dataflow: (graph_json: DagGraph) => client.post('/dag/translate/dataflow', { graph_json }),
  workflowDataflow: (workflowId: number, verify = false) =>
    client.get(`/dag/workflows/${workflowId}/dataflow`, { params: { verify } }),
  explain: (sql: string, datasource: string, federated_names?: string[]) =>
    client.post('/dag/explain', { sql, datasource, federated_names }),
};

// ── 数据流拓扑（源表 → 转换 → 目标表，与血缘图同构） ──
export interface DataflowNode {
  id: string;
  kind: 'table' | 'transform';
  label: string;
  roles?: string[];       // table: source/target
  udfs?: string[];        // transform
  sql_digest?: string;    // transform
  /** transform 的主数据源名（执行诊断用） */
  datasource?: string;
}

export interface DataflowEdge {
  from: string;
  to: string;
  label: '读取' | '写入' | 'JOIN' | '同步' | string;
  /** verify=true 时：是否已被实际运行的血缘验证（预期 vs 实际） */
  verified?: boolean;
}

export interface DataflowGraph {
  nodes: DataflowNode[];
  flows: DataflowEdge[];
}
