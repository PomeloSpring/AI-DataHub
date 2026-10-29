import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelSitesTab } from '../ModelAssetTabs';
import { ontologyApi } from '@/api/ontology';
import { toast } from '@/lib/toast';

/**
 * 同构站点部署页的对象级站点白名单（「同构多站 + 个别站点才有某对象」）。
 *
 * 锁住四条行为：
 * 1. 默认免配：没配过的对象其「适用站点」= 上方全部已勾选站点（不给未部署站点开权限）；
 * 2. 点掉站点 = 显式收紧，保存时按 `{object_key: [site_code]}` 全量提交，且只带仍勾选的站点；
 * 3. 已收紧的对象渲染出「不限」入口，点了即取消限制（提交里不再包含该对象）；
 * 4. 白名单不允许空（全部点掉必须显式报错，不得静默当成"不限"或提交空数组）。
 */

vi.mock('@/api/ontology', () => ({
  ontologyApi: { sites: vi.fn(), saveSites: vi.fn() },
}));
vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/components/graph/KnowledgeGraph', () => ({ default: () => null }));
vi.mock('@/stores/graphStore', () => ({
  useGraphStore: { getState: () => ({ fetchGraphData: vi.fn() }) },
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(), info: vi.fn(), warning: vi.fn(), error: vi.fn(), dismiss: vi.fn(),
  }),
}));

const SITE_ROWS = [
  { site_code: 'cn', display_name: '中国站点', datasource: 'prd-cn', selected: true, datasource_options: ['prd-cn'] },
  { site_code: 'jp', display_name: '日本站点', datasource: 'prd-jp', selected: true, datasource_options: ['prd-jp'] },
  { site_code: 'eu', display_name: '欧洲站点', datasource: '', selected: false, datasource_options: ['prd-eu'] },
];

const objRow = (key: string, name: string, allowedSites: string[]) => ({
  object_key: key, object_name: name, datasource: 'prd-cn', physical_table: `t_${key}`,
  bind_kind: 'primary', column_count: 8, sync_state: 'bound', product_ref: '',
  allowed_sites: allowedSites,
});

const OBJECT_ROWS = [
  objRow('case', '案例', []),
  objRow('rum_event', '观测事件', ['cn']),
];

const sitesResp = () => ({
  data: { kind: 'business', sites: SITE_ROWS, selected: [], contract_objects: OBJECT_ROWS },
});

const rowOf = (name: string) => (screen.getByText(name).closest('tr') as HTMLElement);
const chipOf = (name: string, site: string) => within(rowOf(name)).getByRole('button', { name: site });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(ontologyApi.sites).mockResolvedValue(sitesResp() as any);
  vi.mocked(ontologyApi.saveSites).mockResolvedValue({ data: { success: true, sites: [] } } as any);
});

afterEach(() => cleanup());

describe('对象级站点白名单（默认全可用 · 配置才收紧）', () => {
  it('未配置的对象默认全部勾选站点可用，且不给未部署站点开权限', async () => {
    render(<ModelSitesTab modelId={7} />);
    await screen.findByText('案例');
    const row = rowOf('案例');
    // 已勾选的两站都点亮（未收紧）
    expect(chipOf('案例', '中国站点')).toHaveClass('bg-primary/10');
    expect(chipOf('案例', '日本站点')).toHaveClass('bg-primary/10');
    // 未勾选站点不出现在芯片清单里（白名单只能收紧，不能扩权）
    expect(within(row).queryByRole('button', { name: '欧洲站点' })).toBeNull();
    // 未收紧的对象不显示「不限」复位入口
    expect(within(row).queryByText('不限')).toBeNull();
  });

  it('已收紧的对象按站点渲染可用/不适用，并给出「不限」复位入口', async () => {
    render(<ModelSitesTab modelId={7} />);
    await screen.findByText('观测事件');
    expect(chipOf('观测事件', '中国站点')).toHaveClass('bg-primary/10');
    expect(chipOf('观测事件', '日本站点')).toHaveClass('line-through');
    expect(within(rowOf('观测事件')).getByText('不限')).toBeInTheDocument();
  });

  it('点掉站点后保存：按 {object_key:[site_code]} 全量提交，只带仍勾选的站点', async () => {
    render(<ModelSitesTab modelId={7} />);
    await screen.findByText('案例');
    const saveBtn = screen.getByRole('button', { name: '保存站点部署' });
    expect(saveBtn).toBeDisabled();          // 无变更时禁存（配置已是对的）
    await userEvent.click(chipOf('案例', '日本站点'));
    expect(saveBtn).toBeEnabled();           // 收紧即为变更
    await userEvent.click(saveBtn);
    await waitFor(() => expect(ontologyApi.saveSites).toHaveBeenCalled());
    const [modelId, sites, rules] = vi.mocked(ontologyApi.saveSites).mock.calls[0];
    expect(modelId).toBe(7);
    expect(sites).toEqual([
      { site_code: 'cn', datasource: 'prd-cn' },
      { site_code: 'jp', datasource: 'prd-jp' },
    ]);
    // 案例被物化为只剩 cn；观测事件沿用已存的 ['cn']
    expect(rules).toEqual({ case: ['cn'], rum_event: ['cn'] });
    expect(toast.success).toHaveBeenCalled();
  });

  it('点「不限」= 取消该对象的站点限制（提交里不再包含它）', async () => {
    render(<ModelSitesTab modelId={7} />);
    await screen.findByText('观测事件');
    await userEvent.click(within(rowOf('观测事件')).getByText('不限'));
    await userEvent.click(screen.getByRole('button', { name: '保存站点部署' }));
    await waitFor(() => expect(ontologyApi.saveSites).toHaveBeenCalled());
    const rules = vi.mocked(ontologyApi.saveSites).mock.calls[0][2];
    expect(rules).toEqual({});
  });

  it('全部站点被点掉必须显式报错，不得静默提交空清单', async () => {
    render(<ModelSitesTab modelId={7} />);
    await screen.findByText('观测事件');
    // 观测事件已限定 cn：再点掉 cn 就空了
    await userEvent.click(chipOf('观测事件', '中国站点'));
    expect(screen.getByText(/至少保留一个适用站点/)).toBeInTheDocument();
    expect(chipOf('观测事件', '中国站点')).toHaveClass('bg-primary/10');  // 拒绝改动，原状态保留
    await userEvent.click(screen.getByRole('button', { name: '保存站点部署' }));
    expect(ontologyApi.saveSites).not.toHaveBeenCalled();
  });

  it('只读态不得改站点适用性', async () => {
    render(<ModelSitesTab modelId={7} readonly />);
    await screen.findByText('案例');
    await userEvent.click(chipOf('案例', '日本站点'));
    expect(chipOf('案例', '日本站点')).toHaveClass('bg-primary/10');
    expect(screen.queryByRole('button', { name: '保存站点部署' })).toBeNull();
  });
});
