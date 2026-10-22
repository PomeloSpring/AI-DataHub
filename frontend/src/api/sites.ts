import client from './client';

/**
 * 站点 API（多站点部署拓扑）。
 *
 * 事实源：`adh_sites`（站点清单）+ `adh_datasources.site_code`（数据源归属）
 * + `adh_ontology_models.deploy_sites`（业务本体适用站点）。
 * 显示一律用 display_name、选择一律用选择框（ui-resource-display）。
 */

export interface Site {
  site_code: string;
  display_name: string;
  /** 1 = 聚合态（全球）：不对应单一数据源，只作为聊天查询范围 */
  is_aggregate: number;
  sort_order: number;
  is_active?: number;
}

export interface SiteDatasource {
  id: number;
  name: string;
  db_type: string;
}

/** 可查站点 = 角色数据源授权的派生视图（不是第二套授权裁决） */
export interface AuthorizedSite extends Site {
  datasources: SiteDatasource[];
}

/** 站点清单（管理页含停用行；聊天/表单选择器只要 active 且非聚合的具体站点） */
export async function listSites(opts: {
  includeAggregate?: boolean; includeInactive?: boolean;
} = {}): Promise<Site[]> {
  const params: Record<string, string> = {};
  if (opts.includeAggregate === false) params.include_aggregate = 'false';
  if (opts.includeInactive) params.include_inactive = 'true';
  const { data } = await client.get<{ items: Site[] }>('/datasources/sites', { params });
  return data.items || [];
}

/** 当前用户可查站点（聊天站点选择器） */
export async function listAuthorizedSites(workspaceId: number): Promise<AuthorizedSite[]> {
  const { data } = await client.get<{ items: AuthorizedSite[] }>(
    '/datasources/authorized-sites',
    { params: { workspace_id: workspaceId || 0 } },
  );
  return data.items || [];
}

export async function saveSite(payload: Partial<Site>): Promise<Site> {
  const { data } = await client.post<Site>('/datasources/sites', payload);
  return data;
}

export async function removeSite(siteCode: string): Promise<void> {
  await client.delete(`/datasources/sites/${encodeURIComponent(siteCode)}`);
}

/** 具体站点（可挂数据源）：数据源归属选择器用 */
export function concreteSites(sites: Site[]): Site[] {
  return sites.filter((s) => !s.is_aggregate);
}

export function siteNameOf(sites: Site[], code: string | undefined | null): string {
  if (!code) return '';
  return sites.find((s) => s.site_code === code)?.display_name || '';
}
