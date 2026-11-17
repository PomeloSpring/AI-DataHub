import client from './client';
import i18n from '../i18n';

export interface DesignWidget {
  key: string;
  title: string;
  chart_type: string;
  query: Record<string, unknown>;
  query_source: 'semantic' | 'raw_sql';
  config: Record<string, unknown>;
  position: { x: number; y: number; w: number; h: number };
}
export interface DesignSelection {
  workspace?: number;
  datasource_name: string;
  knowledge_base_ids: number[];
  dashboard_id?: number;
  chart_id?: number;
}
export interface DashboardDesign {
  design_id: string;
  version: number;
  status: string;
  name: string;
  request: string;
  operation: 'create' | 'append' | 'update';
  selection?: DesignSelection;
  widgets: DesignWidget[];
  steps: string[];
  questions: { key: string; label: string; options: string[] }[];
  answers: Record<string, string>;
  preview_valid: boolean;
  result?: { success: boolean; url: string; dashboard_id: number; chart_ids: number[] };
}
export interface DesignOptions {
  workspaces: { id: number; name: string }[];
  dashboards: { id: number; name: string; charts: { id: number; name: string; chart_type: string }[] }[];
  domains: { datasource_name: string; model_name: string;
    knowledge_bases: { id: number; name: string; kb_type: string }[] }[];
}
export interface DesignPreview {
  design: DashboardDesign;
  generated_at: string;
  charts: { key: string; columns: string[]; rows: Record<string, unknown>[]; row_count: number; possibly_truncated: boolean }[];
}
export const designPath = (id: string) => `/as-bot/dashboard-designs/${id}`;
export const getDesign = async (id: string) => (await client.get<DashboardDesign>(designPath(id))).data;
/**
 * 设计落成的看板 id（历史卡片「预览」的落点）。
 *
 * 以 `result.dashboard_id` 为准；老版本只写了 `result.url`（`/screen/<id>` 或
 * `/dashboard/editor/<id>`）时从路径解析；追加/改图没有 result 时用 `selection.dashboard_id`。
 * 都拿不到 = 这份设计从未生成过看板（已取消/未发布），卡片如实说明而不是给个点了没结果
 * 的入口。
 */
export function designDashboardId(design?: DashboardDesign | null): number {
  if (!design) return 0;
  const explicit = Number(design.result?.dashboard_id) || Number(design.selection?.dashboard_id) || 0;
  if (explicit) return explicit;
  const match = String(design.result?.url || '').match(/^\/(?:screen|dashboard\/editor)\/(\d+)/);
  return match ? Number(match[1]) : 0;
}
export function designError(error: any): string {
  const detail = error?.response?.data?.detail;
  return typeof detail === 'string' ? detail : detail?.message || '设计操作失败，请重试';
}
export const DESIGN_STATUS: Record<string, string> = {
  awaiting_selection: '待选择业务范围', designing: '设计中', preview_ready: '可预览',
  pending_confirmation: '待预览确认', published: '已发布', cancelled: '已取消', failed: '操作失败',
};

/** 设计状态业务名（渲染期经 i18n 解析；DESIGN_STATUS 保留给尚未迁移的消费方）。 */
export function designStatusLabel(status: string): string {
  const key = `chat:designStatus.${status}`;
  if (i18n.exists(key)) return i18n.t(key as 'chat:designStatus.designing');
  return DESIGN_STATUS[status] || status;
}
