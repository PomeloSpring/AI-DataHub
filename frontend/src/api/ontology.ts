import client from './client';

export type OntologyStatus = 'draft' | 'active' | 'archived';

export interface OntologyModelSummary {
  id: number;
  datasource_id: number;
  name: string;
  /** 本体类型：source=源本体(某源物理表语义) / business=业务本体(跨源, 按业务域) / system=系统本体 */
  kind?: 'source' | 'business' | 'system' | '';
  /** 业务域归属（business 本体的归属键） */
  domain?: string;
  status: OntologyStatus;
  object_count: number;
  /** 部署摘要（部署绑定派生，事实源=部署绑定）：≥2 站即契约模型 */
  deployment_count?: number;
  deployments?: { site: string; datasource: string }[];
  kb_id?: number | null;
  /** 有未发布的草案（服务端派生布尔，不含正文）：左侧列表据此提醒“AS-BOT 改了但还没生效” */
  has_draft?: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface OntologyModel extends OntologyModelSummary {
  json_content: string;
  yaml_content: string;
  md_content: string;
}

/** 草案差异项（对象级）：key 为身份键，display_name 为业务显示名 */
export interface DraftObjectDiff {
  key: string;
  display_name: string;
  /** 该对象在草案里的状态（审阅面板按此着色/分组） */
  state?: DraftObjectState;
  /** 变更明细（仅 changed 有值；added/removed 为 null） */
  delta?: DraftObjectDelta | null;
  primary_table?: string;
  property_count?: number;
  link_count?: number;
}

export type DraftObjectState = 'added' | 'changed' | 'removed' | 'unchanged';

/** 对象级变更明细：服务端算好的“人可读 diff”，前端不再拿 JSON 自己比 */
export interface DraftObjectDelta {
  properties_added: { key: string; name: string }[];
  properties_removed: { key: string; name: string }[];
  properties_modified: { key: string; name: string; from: string; to: string }[];
  links_added: { key: string; label: string }[];
  links_removed: { key: string; label: string }[];
  aliases_added: string[];
  aliases_removed: string[];
  renamed_from?: string;
  table_changed?: boolean;
  primary_table_from?: string;
  primary_table_to?: string;
  description_changed?: boolean;
}

/** GET /models/{id}/draft 响应：草案状态 + 与生效版的可视化审阅差异 */
export interface DraftStatus {
  has_draft: boolean;
  draft_updated_at?: string;
  draft_updated_by?: string;
  effective_object_count: number;
  draft_object_count: number;
  /** 全量审阅清单（含未改动对象），按 added→changed→removed→unchanged 排序 */
  objects?: DraftObjectDiff[];
  added: DraftObjectDiff[];
  removed: DraftObjectDiff[];
  changed: DraftObjectDiff[];
  unchanged_count?: number;
  /** 草案正文解析不出对象：审阅面板必须显式报错，不得渲成“无改动” */
  draft_parse_error?: boolean;
  /** 生效版在草案写入后又被人改动：此时发布会被并发保护阻断 */
  base_stale: boolean;
}

/** 模型级适用站点的一项：站点 + 该站路由到的数据源业务名 */
export interface ModelSiteRow {
  site_code: string;
  display_name: string;
  /** 已配置的路由目标（未选时为空串） */
  datasource: string;
  /** 该站点已被选中 */
  selected: boolean;
  /** 该站点内可选的数据源业务名 */
  datasource_options: string[];
}

/** 对象契约行（一份契约的形状描述，不再逐站重复登记） */
export interface ContractObjectRow {
  object_key: string;
  object_name: string;
  datasource: string;
  physical_table: string;
  bind_kind: string;
  column_count: number;
  sync_state: string;
  product_ref: string;
  /** 对象级站点白名单：显式限定的站点 code；空 = 不收紧（上方勾选的站点全可用） */
  allowed_sites: string[];
}

export interface OntologyObjectHit {
  object_key: string;
  display_name: string;
  aliases: string;
  description: string;
  distance: number | null;
  object: Record<string, any> | null;
}

/** 『生成本体模型』后台任务状态（服务端进程内后台执行，进度同时写 AS-BOT 会话转录）。 */
export interface GenerateTask {
  conversation_id: number;
  workspace_id: number;
  datasource_id: number;
  datasource_name: string;
  /** interrupted = 服务重启导致任务失去宿主（不当成功看） */
  status: 'running' | 'done' | 'error' | 'cancelled' | 'interrupted';
  progress: { stage: string; detail: string }[];
  model_id: number | null;
  object_count: number;
  warnings: string[];
  error: string;
  started_at?: string | number;
  finished_at?: string | number;
}

export const GENERATE_TASK_DONE_STATUSES = ['done', 'error', 'cancelled', 'interrupted'];

/** 派发『生成本体模型』后台任务（立即返回，不随页面/面板关闭而中止）。
 * kbIds 为辅助生成的知识库（服务端校验后按名参与检索，id 不进 LLM 视野）。 */
export async function startOntologyGenerate(datasourceId: number, kbIds: number[] = []): Promise<GenerateTask> {
  const { data } = await client.post<GenerateTask>('/catalog/ontology/generate',
    { datasource_id: datasourceId, kb_ids: kbIds });
  return data;
}

/** 任务进度/终态（轮询用；与 AS-BOT 侧边栏看到的内容同源）。 */
export async function getOntologyGenerateTask(conversationId: number): Promise<GenerateTask> {
  const { data } = await client.get<GenerateTask>(`/catalog/ontology/generate/tasks/${conversationId}`);
  return data;
}

/** 本用户的后台生成任务清单（页面重进时挂回正在跑的任务）。 */
export async function listOntologyGenerateTasks(activeOnly = true): Promise<GenerateTask[]> {
  const { data } = await client.get<{ items: GenerateTask[] }>('/catalog/ontology/generate/tasks',
    { params: { active_only: activeOnly } });
  return Array.isArray(data?.items) ? data.items : [];
}

/** 取消正在后台执行的任务（写动作，服务端同样经权限码把关）。 */
export async function cancelOntologyGenerateTask(conversationId: number): Promise<GenerateTask> {
  const { data } = await client.post<GenerateTask>(
    `/catalog/ontology/generate/tasks/${conversationId}/cancel`);
  return data;
}

export const ontologyApi = {
  list: (datasourceId?: number, includeArchived = false) =>
    client.get<{ items: OntologyModelSummary[] }>('/catalog/ontology/models', {
      params: { ...(datasourceId ? { datasource_id: datasourceId } : {}), include_archived: includeArchived },
    }),
  get: (id: number) => client.get<OntologyModel>(`/catalog/ontology/models/${id}`),
  /** 业务本体适用站点 + 对象契约清单 */
  sites: (id: number) =>
    client.get<{
      kind: string;
      sites: ModelSiteRow[];
      selected: { site_code: string; datasource: string }[];
      contract_objects: ContractObjectRow[];
    }>(`/catalog/ontology/models/${id}/sites`),
  saveSites: (
    id: number,
    sites: { site_code: string; datasource: string }[],
    objectSiteRules?: Record<string, string[]>,
  ) =>
    client.put<{
      success: boolean;
      sites: { site_code: string; datasource: string }[];
      object_site_rules?: Record<string, string[]>;
      pruned?: string[];
    }>(`/catalog/ontology/models/${id}/sites`, {
      sites,
      ...(objectSiteRules === undefined ? {} : { object_site_rules: objectSiteRules }),
    }),
  syncDoc: (id: number) =>
    client.get<{ markdown: string }>(`/catalog/ontology/models/${id}/sync-doc`),
  versions: (id: number) =>
    client.get<{ items: OntologyModelSummary[] }>(`/catalog/ontology/models/${id}/versions`),
  save: (id: number, jsonContent: string, name?: string) =>
    client.put<OntologyModel>(`/catalog/ontology/models/${id}`, {
      json_content: jsonContent,
      name,
    }),
  /** 两段式：只存草案，不碰生效内容、不级联（等 publish 才生效） */
  saveDraft: (id: number, jsonContent: string, name?: string) =>
    client.put<OntologyModel & { draft_pending?: boolean; note?: string }>(
      `/catalog/ontology/models/${id}/draft`, { json_content: jsonContent, name }),
  /** 发布草案：生效 + 级联（重展开对象/重建图谱/同步知识库），需 ontology:publish */
  publishDraft: (id: number) =>
    client.post<{ success: boolean; object_count?: number }>(`/catalog/ontology/models/${id}/publish`, {}),
  /** 草案状态 + 对象级差异（服务端真值，不信本地布尔）；不含草案正文 */
  getDraft: (id: number) =>
    client.get<DraftStatus>(`/catalog/ontology/models/${id}/draft`),
  /** 草案正文预览（只读）：发布前要能核对 AS-BOT 到底改了什么 */
  getDraftContent: (id: number) =>
    client.get<{
      id: number; name: string; status: string; has_draft: boolean;
      draft_json_content: string; effective_json_content: string;
    }>(`/catalog/ontology/models/${id}/draft/content`),
  /** 丢弃草案（只清草案，生效版不动），需 ontology:save */
  discardDraft: (id: number) =>
    client.delete<DraftStatus & { discarded?: boolean }>(`/catalog/ontology/models/${id}/draft`),
  activate: (id: number) => client.post<OntologyModel>(`/catalog/ontology/models/${id}/activate`),
  archive: (id: number) => client.post<OntologyModel>(`/catalog/ontology/models/${id}/archive`),
  restore: (id: number) => client.post<OntologyModel>(`/catalog/ontology/models/${id}/restore`),
  syncKb: (id: number, kbId?: number | null) => client.post<
    { synced?: number; removed?: number; skipped?: string; targets?: string[]; error?: string }>(
    `/catalog/ontology/models/${id}/kb-sync`, null,
    { params: kbId ? { kb_id: kbId } : {} }),
  kbSyncOptions: () =>
    client.get<{ items: { id: number; name: string }[] }>('/catalog/ontology/kb-sync-options'),
  remove: (id: number) => client.delete(`/catalog/ontology/models/${id}`),
  search: (q: string, datasourceId: number, limit = 5) =>
    client.get<{ items: OntologyObjectHit[] }>('/catalog/ontology/search', {
      params: { q, datasource_id: datasourceId, limit },
    }),
};
