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
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface OntologyModel extends OntologyModelSummary {
  json_content: string;
  yaml_content: string;
  md_content: string;
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
  saveSites: (id: number, sites: { site_code: string; datasource: string }[]) =>
    client.put<{ success: boolean; sites: { site_code: string; datasource: string }[] }>(
      `/catalog/ontology/models/${id}/sites`, { sites }),
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
  /** 草案状态：有无待发布改动、谁改的、何时改的（不含草案正文，避免外泄） */
  getDraft: (id: number) =>
    client.get<{ has_draft: boolean; draft_updated_at?: string; draft_updated_by?: string }>(
      `/catalog/ontology/models/${id}/draft`),
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
