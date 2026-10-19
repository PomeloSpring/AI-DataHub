import client from '@/api/client';
import type { Dashboard } from '@/stores/dashboardStore';

// 看板目录(仪表盘组维度)数据契约: 浏览域唯一清单来源 GET /dashboard/groups/visible。
// 可见性由服务端按角色 fail-closed 裁决(adh_role_dashboard_access), 前端不再另判;
// editable(可写性)同样由服务端裁决: 角色授予的可见权不含编辑权, 前端只据此隐藏写入口。

/** 看板卡片预览区块(仅几何: 位置/尺寸/是否装饰组件, 不含 SQL 与样式配置) */
export interface PreviewBlock {
  x: number;
  y: number;
  w: number;
  h: number;
  widget: boolean;
}

/** 看板画布预览: 卡片瀑布按 width/height 决定高宽比, 按 blocks 画区块 */
export interface DashboardPreview {
  width: number;
  height: number;
  blocks: PreviewBlock[];
}

/** 组目录内的看板行: 阅读/播放所需轻量字段 + 卡片清单展示字段 */
export interface GroupBoard extends Pick<Dashboard, 'id' | 'name' | 'status' | 'is_default'> {
  sort_order?: number;
  description?: string;
  updated_at?: string;
  chart_count?: number;
  preview?: DashboardPreview;
  /** 服务端裁决: 仅看板属主/管理员可写; 角色授予的可见权不含编辑权 */
  editable?: boolean;
}

/** 仪表盘组目录项: 组按角色分配, 组内看板 ∩ 角色可见(可见性 fail-closed) */
export interface GroupView {
  id: number;
  name: string;
  description: string;
  sort: number;
  dashboards: GroupBoard[];
}

/** 拉取当前用户可见的仪表盘组(含"未分组"兜底组)。
 *
 * 列表→详情→返回会反复挂载并重取同一份目录, 这里做 30s 内存缓存(命中直接复用同一请求);
 * 只缓存「目录长什么样」, 看板实际可看仍由服务端每次实时裁决(不可见→详情 404 显式提示),
 * 因此缓存过期最多停留 30s, 不构成权限窗口。失败不缓存(下次调用重试)。
 * 本应用内的看板/图表/分组写操作经 invalidateCatalogCache() 立即失效。
 */
const CATALOG_TTL_MS = 30_000;
let catalogCache: { at: number; promise: Promise<GroupView[]> } | null = null;

export function fetchVisibleGroups(): Promise<GroupView[]> {
  const now = Date.now();
  if (catalogCache && now - catalogCache.at < CATALOG_TTL_MS) return catalogCache.promise;
  const promise = rawFetchVisibleGroups();
  catalogCache = { at: now, promise };
  promise.catch(() => {
    if (catalogCache?.promise === promise) catalogCache = null;
  });
  return promise;
}

/** 目录缓存失效: 看板/图表/分组发生写入后调用, 下次拉取回源。 */
export function invalidateCatalogCache(): void {
  catalogCache = null;
}

async function rawFetchVisibleGroups(): Promise<GroupView[]> {
  const { data } = await client.get('/dashboard/groups/visible');
  return Array.isArray(data) ? data : [];
}

/** 展平为看板清单(带所属组), 保持服务端顺序(is_default → sort_order) */
export function flattenGroupBoards(groups: GroupView[]): Array<GroupBoard & { group_id: number; group_name: string }> {
  return groups.flatMap(g => g.dashboards.map(d => ({ ...d, group_id: g.id, group_name: g.name })));
}
