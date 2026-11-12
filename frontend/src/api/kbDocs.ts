import client from './client';

/**
 * 知识库文档 API 契约（backend/modules/platform/api/kb_docs.py）。
 *
 * 三个口径必须记住，它们不是实现细节而是产品语义：
 * - **知识库是一个目录**，不是单个文件：树由 `source_id` / `parent_id` 组装，
 *   云端（qmind notebook）目前是扁平的就呈现为一层，前端不假造层级。
 * - **标识一律用知识库业务名 + source_id**：`notebook_id`、对象存储 `uri`、
 *   带签名的 `originUrl` 都属内部标识，服务端不下发，前端也不许拼。
 * - **保存只提交改动的这一篇**：云端没有原地更新接口（服务端是"传新件→删旧件"），
 *   所以保存后 `source_id` 会变（响应里的 `previous_source_id` → `source_id`），
 *   树上必须按新 id 定位，不能拿旧 id 继续用。
 */

export interface KbBase {
  name: string;
  description: string;
  kb_type: string;
  status: string;
  /** 该类型是否支持文档树（本批只有云端 qmind 支持） */
  supports_docs: boolean;
  /** supports_docs 且库已启用 —— 编辑动作给不给 */
  can_edit: boolean;
  /** 不支持/不可编辑时服务端给的原因（原样显示，不自行编文案） */
  reason: string;
}

export interface KbTreeNode {
  source_id: string;
  title: string;
  is_dir: boolean;
  parent_id: string;
  path: string;
  source_type: string;
  /** ready / pending / failed…：云端解析状态，非 ready 时检索还命中不到 */
  status: string;
  error_message: string;
  updated_at: string;
  /** 父节点不在清单里（云端层级不完整）：已挂到根并如实标注 */
  orphan?: boolean;
  children?: KbTreeNode[];
}

export interface KbTree {
  base: Pick<KbBase, 'name' | 'description' | 'kb_type' | 'status'>;
  nodes: KbTreeNode[];
  doc_count: number;
  folder_count: number;
  /** 整库没有任何父子关系（扁平库） */
  flat: boolean;
}

export interface KbDoc {
  source_id: string;
  title: string;
  is_dir: boolean;
  source_type: string;
  status: string;
  content: string;
  /** 打开时的内容基线：保存时原样带回做乐观并发，冲突由服务端 409 判 */
  content_sha256: string;
  /** 本体上云的派生文档：不许在这里改（下次同步会被整体覆盖） */
  derived: boolean;
  read_only_reason: string;
  updated_at: string;
}

export interface KbWriteResult {
  source_id: string;
  previous_source_id?: string;
  title: string;
  content_sha256?: string;
  message: string;
  /** 未阻断但必须看到的提醒（扫描提示、旧件没删掉的残留） */
  warnings?: string[];
  warning?: string;
}

/** 内容 → 语料的一次同步回执（`POST /content/knowledge-bases/{kb}/sync`） */
export interface KbSyncResult {
  kb_name: string;
  doc_title: string;
  source_id: string;
  /** 版本与正文都没变：一个云端请求都没发（不是失败，也不该假装“已重新提交”） */
  unchanged: boolean;
  synced_at: string;
  message: string;
  warnings?: string[];
}

const seg = (kb: string) => encodeURIComponent(kb);

/**
 * 知识库类型的业务名（与系统配置「知识库」的类型名册 KnowledgeBase.tsx KB_TYPES 一致）。
 * 未登记的类型原样显示（不编造名称）；空值说“未配置”而不是当未知处理。
 */
export const KB_TYPE_LABELS: Record<string, string> = {
  qmind: 'QMind 知识检索',
  local: '本地数据目录',
  vector_db: '向量数据库',
  cloud_rag: '云厂商 RAG',
};

export function kbTypeLabel(type: string): string {
  if (!type) return '未配置类型';
  return KB_TYPE_LABELS[type] || type;
}

export const kbDocsApi = {
  bases: () => client.get<{ items: KbBase[] }>('/content/knowledge-bases'),
  tree: (kb: string) => client.get<KbTree>(`/content/knowledge-bases/${seg(kb)}/tree`),
  doc: (kb: string, sourceId: string) =>
    client.get<KbDoc>(`/content/knowledge-bases/${seg(kb)}/documents/${encodeURIComponent(sourceId)}`),
  /** 只提交这一篇（其余文档不动） */
  saveDoc: (kb: string, sourceId: string, content: string, expectedSha256: string) =>
    client.put<KbWriteResult>(`/content/knowledge-bases/${seg(kb)}/documents/${encodeURIComponent(sourceId)}`,
      { content, expected_sha256: expectedSha256 }),
  createDoc: (kb: string, title: string, content: string, parentId = '') =>
    client.post<KbWriteResult>(`/content/knowledge-bases/${seg(kb)}/documents`,
      { title, content, parent_id: parentId }),
  createFolder: (kb: string, title: string, parentId = '') =>
    client.post<KbWriteResult>(`/content/knowledge-bases/${seg(kb)}/folders`,
      { title, parent_id: parentId }),
  /**
   * 把一篇**已发布**内容同步进本知识库（`article` 传 slug，不传内部 id）。
   *
   * 挂在知识库前缀而不是内容前缀是有意的：这一步写的是语料，裁决码必须是
   * `kbdoc:write`；放进 `/content/articles*` 会让“能编辑内容”的人自动拿到写云端
   * 语料的权（另造第二套裁决）。发布与同步是两个动作，不合并。
   */
  syncFromArticle: (kb: string, article: string, parentId = '') =>
    client.post<KbSyncResult>(`/content/knowledge-bases/${seg(kb)}/sync`,
      { article, parent_id: parentId }),
};

/** 取错误文案（与内容中心同一口径：detail 原样显示，失败不许静默成空串） */
export function kbDocError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }, message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (detail) return JSON.stringify(detail);
  return e?.message || '请求失败，请稍后重试';
}

/** 变更提醒文案：非 ready 的文档在云端还没解析完，检索暂时命中不到（不是"失败"） */
export function kbStatusHint(status: string): string {
  if (status === 'ready') return '';
  if (status === 'failed') return '云端解析失败：这篇现在检索命中不到，请看错误详情或在 Qoder 端重试';
  return `云端状态「${status || '未知'}」：解析完成后才会被 AS-BOT 检索命中`;
}
