// 报表中心 API：报表跟随属主（跨工作站），内容型报表可新建/重命名/编辑/删除。
// 与资产清单的分工：资产=归档的文件（下载复用），报表=稳定内容（在线实时查看）。
import client from './client';

/** 报表来源分型：scheduled/analysis=数据派生（须过权限快照，正文只读）；
 *  manual/conversation=内容型（人写或会话保存，可在线编辑）；
 *  legacy=迁移前任务产出的历史报表（属主可看可删，但改了就是篡改取数结果 → 只读）。 */
export type ReportSourceType = 'scheduled' | 'analysis' | 'manual' | 'conversation' | 'legacy';

export const EDITABLE_SOURCES: ReportSourceType[] = ['manual', 'conversation'];
export const DELETABLE_SOURCES: ReportSourceType[] = ['manual', 'conversation', 'legacy'];

export interface ReportOrigin {
  channel?: string;
  conversation_id?: number;
  conversation_title?: string;
  message_uuid?: string;
}

export interface ReportSummary {
  id: number;
  task_id: number | null;
  log_id: number | null;
  title: string;
  format: string;
  access_mode: string;
  view_count: number;
  created_at: string;
  updated_at?: string | null;
  generation_status?: string;
  publication_status?: string;
  task_name?: string | null;
  source_type?: ReportSourceType;
  workspace_id?: number;
  origin?: ReportOrigin;
}

export interface ReportDetail extends ReportSummary {
  content: string;
  access_warning?: string;
  evidence_summary?: {
    validation_status?: string; quality_status?: string; completeness?: string;
    quality?: string; complete?: boolean; semantic_version?: string; period?: string; validated?: boolean;
  };
}

const inSources = (list: ReportSourceType[], s?: ReportSourceType) => !!s && list.includes(s);

/** 正文可否在线编辑（重命名/改正文） */
export function isEditableReport(r: { source_type?: ReportSourceType }): boolean {
  return inSources(EDITABLE_SOURCES, r.source_type);
}

/** 可否在报表中心删除（含历史报表清理） */
export function isDeletableReport(r: { source_type?: ReportSourceType }): boolean {
  return inSources(DELETABLE_SOURCES, r.source_type);
}

/** 报表清单：默认拉全部工作站（0=不过滤），后端按属主 fail-closed */
export async function listReports(workspaceId = 0): Promise<ReportSummary[]> {
  const { data } = await client.get('/reports', { params: { workspace_id: workspaceId, page_size: 200 } });
  const items = Array.isArray(data?.items) ? data.items : Array.isArray(data) ? data : null;
  if (!items) throw new Error('报表清单响应异常（非列表结构），请检查服务日志');
  return items;
}

export async function getReportDetail(reportId: number): Promise<ReportDetail> {
  const { data } = await client.get(`/reports/${reportId}/detail`);
  return data;
}

/** 新建内容型报表（页面手写正文） */
export async function createReport(payload: {
  workspace_id: number; title: string; content: string; format?: string;
}): Promise<{ report_id: number }> {
  const { data } = await client.post('/reports', { ...payload, source_type: 'manual' });
  return data;
}

/** 把智能问数会话里的一轮回答存为报表（正文由服务端从已落库会话读取） */
export async function saveConversationAsReport(payload: {
  conversation_id: number; title?: string; message_index?: number; message_uuid?: string;
}): Promise<{ report_id: number; title: string }> {
  const { data } = await client.post('/reports', {
    ...payload, source_type: 'conversation', workspace_id: 0,
  });
  return data;
}

export async function updateReport(reportId: number, patch: {
  title?: string; content?: string; format?: string;
}): Promise<{ report_id: number }> {
  const { data } = await client.put(`/reports/${reportId}`, patch);
  return data;
}

export async function deleteReport(reportId: number): Promise<void> {
  await client.delete(`/reports/${reportId}`);
}
