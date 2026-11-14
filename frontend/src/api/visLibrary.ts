import client from './client';

export const VIS_CATEGORIES = [
  { id: 'theme_pack', label: '主题包' },
  // 动效预设与主题包正交：主题管配色与版式，动效管时间维度，每套主题都能搭任意预设
  // （所以它不进 sanitizePack，也不由 tp_* 携带，解析唯一入口见 lib/motion.ts）
  { id: 'motion_preset', label: '动效预设' },
  { id: 'chart_style', label: '图表样式' }, { id: 'widget_style', label: '控件样式' },
  { id: 'screen_background', label: '大屏背景' },
  { id: 'kpi_card', label: 'KPI 卡片' }, { id: 'layout_template', label: '布局模板' },
  { id: 'decoration_frame', label: '装饰边框' }, { id: 'color_theme', label: '配色主题' },
  { id: 'map_tile', label: '地图瓦片' },
] as const;
// report_template 是独立菜单，不在字模库 UI 分类中，但数据库中仍有该 category
export type VisCategory = typeof VIS_CATEGORIES[number]['id'] | 'report_template';
export interface VisComponent {
  id: number; code: string; name: string; category: VisCategory; chart_type?: string | null;
  style_config: Record<string, any>; source: string; is_builtin: number; is_active: number;
  /** 1=启用 0=禁用（停用但保留）：条目仍可查可再启用，但一律不可被选用 */
  is_enabled?: number;
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
export async function fetchVisComponents(includeInactive = false, includeDisabled = false) {
  const { data } = await client.get('/vis-library/components',
    { params: { include_inactive: includeInactive, include_disabled: includeDisabled } });
  return validateVisResponse(data);
}

/** 服务端真分页返回契约（与 /content/articles 同源）：管理页专用，不再拉全量前端切片。 */
export interface VisComponentPage {
  items: VisComponent[]; total: number; page: number; page_size: number;
}

/**
 * 拉一页字模（服务端真分页）：category 筛选 / q 搜索 / 下架·禁用两档 / page·page_size 全部下推，
 * 行总数由服务端 COUNT 得到。选用面（编辑器面板）需要全量在手，仍走 fetchVisComponents，不用本函数。
 */
export async function fetchVisComponentPage(opts: {
  page: number; pageSize: number; category?: string; q?: string;
  includeInactive?: boolean; includeDisabled?: boolean;
}): Promise<VisComponentPage> {
  const { data } = await client.get('/vis-library/components', {
    params: {
      category: opts.category || '', q: opts.q || '',
      include_inactive: !!opts.includeInactive, include_disabled: !!opts.includeDisabled,
      page: opts.page, page_size: opts.pageSize,
    },
  });
  // 分页契约取 data.items；容错：万一服务端回了裸数组也按全量解析（不静默丢数据）
  const raw = Array.isArray(data) ? data : (data?.items ?? []);
  const items = validateVisResponse(raw);
  return {
    items, total: Number(data?.total ?? items.length),
    page: Number(data?.page ?? opts.page), page_size: Number(data?.page_size ?? opts.pageSize),
  };
}

/**
 * 字模是否"在用"（唯一口径）：未下架且未禁用。
 * 下架=软删除（清单不再给出，历史引用靠挂载快照兜底）；禁用=停用但保留（管理页仍可查可再启用，
 * 但不得被选用：主题包套不上、系统主题菜单里对应主题直接不出现）。缺列（未跑迁移的存量库）按启用处理。
 */
export const isVisUsable = (c: Pick<VisComponent, 'is_active' | 'is_enabled'>): boolean =>
  !!c.is_active && c.is_enabled !== 0;
export async function createVisComponent(payload: {
  name: string; category: VisCategory; chart_type?: string; style_config: Record<string, any>;
}) {
  return client.post('/vis-library/components', { ...payload, is_builtin: false, source: 'custom' });
}
