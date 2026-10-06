import client from './client';

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
export const listDesigns = async (conversationId: number) =>
  (await client.get<DashboardDesign[]>('/as-bot/dashboard-designs', { params: { conversation_id: conversationId } })).data;
export function designError(error: any): string {
  const detail = error?.response?.data?.detail;
  return typeof detail === 'string' ? detail : detail?.message || '设计操作失败，请重试';
}
export const DESIGN_STATUS: Record<string, string> = {
  awaiting_selection: '待选择业务范围', designing: '设计中', preview_ready: '可预览',
  pending_confirmation: '待预览确认', published: '已发布', cancelled: '已取消', failed: '操作失败',
};
