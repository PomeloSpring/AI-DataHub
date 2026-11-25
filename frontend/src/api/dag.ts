import client from './client';

// ── 类型 ────────────────────────────────────────────────────────
export interface DagNodeConfig {
  // materialize 节点（定时物化主形态：数据集 → 已登记目标表）
  dataset?: string;
  site?: string;
  args?: Record<string, string>;
  partition?: {
    column?: string;
    date_arg?: string;
    target_partition?: string;
    /** 物化粒度：day=逐日 / month=逐月 / year=逐年（决定一格的长度与回看单位） */
    grain?: 'day' | 'month' | 'year';
    /** 回看格数（天/月/年，含当前格；日常调度口径） */
    lookback_days?: number;
    key_columns?: string[];
    fixed?: Record<string, string>;
  };
  budget?: { max_rows?: number; max_seconds?: number };
  // sync 节点
  source_datasource?: string;
  source_table?: string;
  target_datasource?: string;
  target_table?: string;
  sync_mode?: 'full' | 'incremental';
  incremental_column?: string;
  write_mode?: 'append' | 'overwrite' | 'partition_overwrite';
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
  type: 'materialize' | 'sync' | 'sql_task' | 'control';
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
  /** 执行结果摘要（物化：dataset/target/partitions/reconciled/strategy/budget） */
  result_json?: Record<string, any>;
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
  /** 本次运行参数（历史初始化窗口；服务端校验后落库） */
  params_json?: RunParams;
  error_code?: string;
  error_message?: string;
  node_count?: number;
  /** 仍在执行态但租约已过期 = 执行体失联（残留实例，可手动终止） */
  is_stale?: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string;
}

export interface RunParams {
  /** 起始日期（YYYY-MM-DD）：历史初始化窗口起点 */
  start_date?: string;
  /** 结束日期（YYYY-MM-DD）：留空 = 今天；只给 end 不给 start = 从上次成功位置续跑 */
  end_date?: string;
  /** 本次只跑哪些粒度（day/month/year 子集；其余节点本次跳过并回报原因） */
  grains?: Array<'day' | 'month' | 'year'>;
}

/** 节点「上次执行成功的位置」（水位线，键为节点 key） */
export interface WatermarkItem {
  position: string | null;
  last_rows: number;
  updated_at?: string | null;
}

export interface DagRunQuery {
  page?: number;
  size?: number;
  workflow_id?: number;
  status?: string;
  /** 触发方式：cron / manual / retry */
  trigger_type?: string;
  /** 开始日期下限（YYYY-MM-DD，含当日） */
  start_from?: string;
  /** 开始日期上限（YYYY-MM-DD，含当日） */
  start_to?: string;
  /** 任务名关键字（服务端 LIKE，前端不本地过滤） */
  q?: string;
}

/** 批量动作的逐条回报（成功/失败各自点名，失败项保留勾选可重试） */
export interface DagRunBatchResult {
  action: 'delete' | 'stop';
  succeeded: Array<{ id: number; workflow_name?: string }>;
  failed: Array<{ id: number; workflow_name?: string; reason: string }>;
  succeeded_count: number;
  failed_count: number;
  message: string;
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
  runWorkflow: (id: number, params?: RunParams) =>
    client.post(`/dag/workflows/${id}/run`, params || {}),
  listWatermarks: (id: number) =>
    client.get<{ items: Record<string, WatermarkItem> }>(`/dag/workflows/${id}/watermarks`),
  listWorkflowRuns: (id: number, params?: { page?: number; size?: number; status?: string }) =>
    client.get(`/dag/workflows/${id}/runs`, { params }),
  listRuns: (params?: DagRunQuery) => client.get('/dag/runs', { params }),
  getRun: (runId: number) => client.get(`/dag/runs/${runId}`),
  /** 删除运行记录（含节点明细）；排队/执行中的实例需先 stop（服务端 409） */
  deleteRun: (runId: number) => client.delete(`/dag/runs/${runId}`),
  /** 批量删除：逐条复用单条实现，失败项带原因回（服务端不整批回滚） */
  batchDeleteRuns: (ids: number[]) =>
    client.post<DagRunBatchResult>('/dag/runs/batch-delete', { ids }),
  /** 批量停止：已结束的逐条标“本次未处理” */
  batchStopRuns: (ids: number[]) =>
    client.post<DagRunBatchResult>('/dag/runs/batch-stop', { ids }),
  stopRun: (runId: number) => client.post(`/dag/runs/${runId}/stop`),
  retryRun: (runId: number) => client.post(`/dag/runs/${runId}/retry`),
  sqlToDag: (sql: string, datasource: string, target_datasource?: string) =>
    client.post('/dag/translate/sql-to-dag', {
      sql, datasource, target_datasource: target_datasource || '',
    }),
  // 导出的是可执行 SQL（materialize 节点按数据集编译出真实 SELECT + INSERT）；
  // run_params 可覆盖窗口（留空则按节点自身配置的窗口）
  dagToSql: (graph_json: DagGraph, run_params?: RunParams) =>
    client.post('/dag/translate/dag-to-sql', { graph_json, run_params }),
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
