import client from './client';

export const VIS_CATEGORIES = [
  { id: 'theme_pack', label: '主题包' },
  { id: 'chart_style', label: '图表样式' }, { id: 'screen_background', label: '大屏背景' },
  { id: 'kpi_card', label: 'KPI 卡片' }, { id: 'layout_template', label: '布局模板' },
  { id: 'decoration_frame', label: '装饰边框' }, { id: 'color_theme', label: '配色主题' },
  { id: 'map_tile', label: '地图瓦片' },
] as const;
// report_template 是独立菜单，不在字模库 UI 分类中，但数据库中仍有该 category
export type VisCategory = typeof VIS_CATEGORIES[number]['id'] | 'report_template';
export interface VisComponent {
  id: number; code: string; name: string; category: VisCategory; chart_type?: string | null;
  style_config: Record<string, any>; source: string; is_builtin: number; is_active: number;
  sort_order: number; description?: string;
  updated_at?: string;
}
export function validateVisResponse(data: unknown): VisComponent[] {
  const VALID_CATEGORIES = [...VIS_CATEGORIES.map(c => c.id), 'report_template'];
  if (!Array.isArray(data)) {
    throw new Error('字模接口未返回有效 JSON 数组，请检查服务或代理后重试');
  }
  // 宽容解析:单条结构损坏/未知类别(如历史 sql_template 存量行)只跳过该条并留痕,
  // 不整组拒绝 —— 否则一条历史数据会把地图瓦片等整个字模清单打挂
  const out: VisComponent[] = [];
  let skipped = 0;
  for (const c of data) {
    const ok = c && typeof c.code === 'string' && typeof c.name === 'string' &&
      VALID_CATEGORIES.includes(c.category) &&
      c.style_config && typeof c.style_config === 'object' && !Array.isArray(c.style_config);
    if (ok) out.push(c as VisComponent);
    else skipped += 1;
  }
  if (skipped > 0) {
    console.warn(`[visLibrary] 已跳过 ${skipped} 条结构损坏/未知类别的字模条目(不影响其余条目使用)`);
  }
  if (out.length === 0 && data.length > 0) {
    throw new Error('字模接口返回的条目均无法识别(类别/结构不符)，请检查服务版本');
  }
  return out;
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
