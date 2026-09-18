import client from './client';

export const VIS_CATEGORIES = [
  { id: 'chart_style', label: '图表样式' }, { id: 'screen_background', label: '大屏背景' },
  { id: 'kpi_card', label: 'KPI 卡片' }, { id: 'layout_template', label: '布局模板' },
  { id: 'decoration_frame', label: '装饰边框' }, { id: 'color_theme', label: '配色主题' },
  { id: 'sql_template', label: 'SQL 模板' },
] as const;
export type VisCategory = typeof VIS_CATEGORIES[number]['id'];
export interface VisComponent {
  id: number; code: string; name: string; category: VisCategory; chart_type?: string | null;
  style_config: Record<string, any>; source: string; is_builtin: number; is_active: number;
  sort_order: number; description?: string; query_template?: Record<string, any> | null;
  updated_at?: string;
}
export function validateVisResponse(data: unknown): VisComponent[] {
  if (!Array.isArray(data) || data.some(c => !c || typeof c.code !== 'string' ||
    typeof c.name !== 'string' || !VIS_CATEGORIES.some(cat => cat.id === c.category) ||
    !c.style_config || typeof c.style_config !== 'object' || Array.isArray(c.style_config))) {
    throw new Error('字模接口未返回有效 JSON 数组，请检查服务或代理后重试');
  }
  return data;
}
export async function fetchVisComponents(includeInactive = false) {
  const { data } = await client.get('/vis-library/components', { params: { include_inactive: includeInactive } });
  return validateVisResponse(data);
}
export async function createVisComponent(payload: {
  name: string; category: VisCategory; chart_type?: string; style_config: Record<string, any>;
}) {
  return client.post('/vis-library/components', { ...payload, is_builtin: false, source: 'custom' });
}
