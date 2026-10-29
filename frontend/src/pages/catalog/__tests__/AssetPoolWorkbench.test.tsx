import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AssetPoolWorkbench from '../AssetPoolWorkbench';
import client from '@/api/client';
import { metricsApi } from '@/api/metrics';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/api/metrics', () => ({
  metricsApi: {
    assetPool: vi.fn(),
    filters: vi.fn(),
    update: vi.fn(async () => ({ data: { success: true } })),
    updateDimension: vi.fn(async () => ({ data: { success: true } })),
    delete: vi.fn(async () => ({ data: { success: true } })),
    deleteDimension: vi.fn(async () => ({ data: { success: true } })),
    list: vi.fn(async () => ({ data: { items: [{ name: '订单金额', agg_type: 'SUM' }] } })),
    listAllDimensions: vi.fn(async () => ({ data: { items: [{ name: '渠道', category: '属性' }] } })),
  },
}));

/**
 * 建模待办工作台（/data/asset-pool，从「本体建模」内嵌子视图抽为独立页）。
 *
 * 用户报的两件事都在这里锁：
 * 1.「操作收编、驳回都不会自动刷新」→ 每个出口成功后必须重新拉取清单，处理掉的行消失；
 * 2. 工作台化（参考 Linear Triage / GitHub Inbox）：一条待办一行、左说清为什么在待办、
 *    右只给该类型对应的出口。
 * 另加三条护栏：显示业务名不裸显 id（ui-resource-display）、选择走选择框、
 * 出口可用性与服务端真实把关同源（evolution API 有 require_admin，非管理员不得渲染死开关）。
 */

const OBJ_FACETS = [
  { key: 'order_item', display_name: '订单明细', metric_count: 3, dimension_count: 1 },
  { key: 'customer', display_name: '客户', metric_count: 1, dimension_count: 0 },
];

let pool: any;
/** 服务端语义：处理掉的行不再回到清单（用例据此断言"刷新后消失"） */
const dropMetric = (id: number) => { pool.metrics = pool.metrics.filter((r: any) => r.id !== id); };
const dropCandidate = (id: number) => { pool.suggestions = pool.suggestions.filter((c: any) => c.id !== id); };

function resetPool() {
  pool = {
    metrics: [
      { id: 401, name: '近30天成交金额', name_en: 'gmv_30d', agg_type: 'SUM', pool_reason: 'unbound', description: '含退款口径' },
      { id: 402, name: '失效指标', bound_object_key: 'gone_obj', pool_reason: 'orphan' },
    ],
    dimensions: [{ id: 501, name: '渠道分布', category: '属性', pool_reason: 'unbound' }],
    terms: [{ id: 601, term_cn: '妥投率', term_en: 'delivery_rate', term_type: '比率', description: '签收量/发出量' }],
    suggestions: [
      {
        id: 701, term: '流水', kind: 'alias', source: 'parse_fail', datasource_name: 'prd-sls-cn',
        target_type: 'metric', target_ref: '订单金额', classify_reason: '与字典行「订单金额」近似',
        hit_count: 5, updated_at: '2026-10-28T10:00:00', candidates: [{ name: '订单金额', score: 0.82 }],
      },
      { id: 702, term: '退单率', kind: 'new_metric', source: 'feedback', hit_count: 3, object_hint: '订单' },
    ],
    counts: {},
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetPool();
  // 出口把关口径与服务端一致：evolution API 是 require_admin + 功能码
  useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin' } } as any);
  usePermissionStore.setState({ perms: null, allowedMenus: null, unrestricted: true, loaded: false });

  vi.mocked(metricsApi.assetPool).mockImplementation(async () => ({ data: JSON.parse(JSON.stringify(pool)) } as any));
  vi.mocked(metricsApi.filters).mockResolvedValue({
    data: {
      objects: OBJ_FACETS,
      metrics: { agg_types: [{ value: 'COUNT' }, { value: 'SUM' }] },
      dimensions: { categories: [{ value: '属性' }] },
    },
  } as any);
  vi.mocked(client.post).mockImplementation(async (url: string) => {
    // 服务端语义：候选一旦走通任一出口（收编/驳回/转术语/转草稿）就离开 pending 队列，不再回待办
    const m = /\/evolution\/candidates\/(\d+)\/(reject|alias|to-term|metric-draft|dimension-draft)/.exec(url);
    if (m) dropCandidate(Number(m[1]));
    return { data: { success: true } } as any;
  });
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/evolution/objects') {
      return { data: { items: [{ key: 'order_item', display_name: '订单明细' }] } } as any;
    }
    if (url === '/evolution/properties') {
      return { data: { items: [{ property: '渠道' }, { property: '下单日期' }] } } as any;
    }
    return { data: {} } as any;
  });
  vi.mocked(metricsApi.update).mockImplementation(async (id: number) => { dropMetric(id); return { data: { success: true } } as any; });
  // 服务端语义：字典行删除后不再回到清单（指标 402 可删）
  vi.mocked(metricsApi.delete).mockImplementation(async (id: number) => { dropMetric(id); return { data: { success: true } } as any; });
});

afterEach(() => cleanup());

const renderPage = () => render(
  <MemoryRouter initialEntries={['/data/asset-pool']}>
    <AssetPoolWorkbench />
  </MemoryRouter>,
);

/** 取某条待办的整行容器（行内出口按钮只在 within(row) 里查，避免多行同名词撞车） */
function rowOf(title: string): HTMLElement {
  const el = screen.getByText(title).closest('.rounded-lg');
  if (!(el instanceof HTMLElement)) throw new Error(`待办行未找到：${title}`);
  return el;
}

describe('建模待办工作台', () => {
  it('分诊队列 + 一待办一出口：行显示业务名与在待办的原因，绝不裸显 id', async () => {
    renderPage();
    expect(await screen.findByText('建模待办')).toBeTruthy();
    expect(screen.getByText('6 项待处理')).toBeTruthy();

    // 左栏队列按类型分桶并给计数（不属于任一模型的缺口才单独成页）
    expect(within(screen.getByRole('button', { name: /指标缺归属/ })).getByText('2')).toBeTruthy();
    expect(screen.getByRole('button', { name: /待收编别名/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /术语未映射/ })).toBeTruthy();

    // 行内容：业务名 + 原因徽章 + 证据（指标与维度行同属未绑定，按多个断言）
    expect(screen.getByText('近30天成交金额')).toBeTruthy();
    expect(screen.getAllByText('未绑定本体对象').length).toBe(2);
    expect(screen.getByText('含退款口径')).toBeTruthy();
    expect(screen.getByText(/指向的对象已失效（gone_obj）/)).toBeTruthy();
    expect(screen.getByText('流水')).toBeTruthy();
    expect(screen.getByText(/近似目标 订单金额/)).toBeTruthy();
    expect(screen.getByText('命中 5 次')).toBeTruthy();
    // 数据源显示业务名（prd-sls-cn），不显示 datasource_id（标签与值分属两个节点）
    expect(screen.getByText('数据源：')).toBeTruthy();
    expect(screen.getByText('prd-sls-cn')).toBeTruthy();
    expect(screen.queryByText(/ID[:：] ?\d+/)).toBeNull();
    ['401', '402', '501', '601', '701', '702'].forEach((id) => {
      expect(screen.queryByText(id)).toBeNull();
    });
  });

  it('驳回候选 → 服务端留痕后自动重拉清单，该行消失（历史缺陷：点完列表不动）', async () => {
    renderPage();
    await screen.findByText('流水');
    expect(vi.mocked(metricsApi.assetPool)).toHaveBeenCalledTimes(1);

    await userEvent.click(within(rowOf('流水')).getByRole('button', { name: '驳回' }));

    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/evolution/candidates/701/reject'));
    await waitFor(() => expect(vi.mocked(metricsApi.assetPool)).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText('流水')).toBeNull());
    // 其它待办不受影响，处理计数显式回执
    expect(screen.getByText('退单率')).toBeTruthy();
    expect(screen.getByText('本次已处理 1 项')).toBeTruthy();
  });

  it('收编为别名 → 目标从字典清单里选（选择框显示业务名），成功后同样自动刷新', async () => {
    renderPage();
    await screen.findByText('流水');

    await userEvent.click(within(rowOf('流水')).getByRole('button', { name: '收编为别名' }));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText('收编为业务别名')).toBeTruthy();
    // 目标清单来自接口，不是让人手输标识
    await waitFor(() => expect(metricsApi.list).toHaveBeenCalled());
    const picked = await within(dlg).findByRole('button', { name: /订单金额/ });
    // 服务端算好的近似目标直接预填（可改选），不让人从头翻清单
    expect(within(dlg).getByPlaceholderText('已选「订单金额」，可继续搜索改选')).toBeTruthy();
    expect(within(dlg).queryByText(/^ds:|datasource_id/)).toBeNull();

    await userEvent.click(picked);
    await userEvent.click(within(dlg).getByRole('button', { name: '确定' }));

    await waitFor(() => expect(client.post).toHaveBeenCalledWith(
      '/evolution/candidates/701/alias', { target_type: 'metric', target_ref: '订单金额' }));
    await waitFor(() => expect(vi.mocked(metricsApi.assetPool)).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText('流水')).toBeNull());
  });

  it('归属到对象 → 从对象清单选择（显示业务名，不暴露 key），PUT 后自动刷新且行消失', async () => {
    renderPage();
    await screen.findByText('近30天成交金额');

    await userEvent.click(within(rowOf('近30天成交金额')).getByRole('button', { name: '归属到对象' }));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText('归属到本体对象')).toBeTruthy();
    await userEvent.click(within(dlg).getByRole('button', { name: /订单明细/ }));
    // 选择框主显示是业务名：内部 key 不得作为可见文本摊出来
    expect(within(dlg).queryByText('order_item')).toBeNull();
    expect(within(dlg).getByText(/当前：订单明细/)).toBeTruthy();

    await userEvent.click(within(dlg).getByRole('button', { name: '确定' }));

    await waitFor(() => expect(metricsApi.update).toHaveBeenCalledWith(401, { bound_object_key: 'order_item' }));
    await waitFor(() => expect(vi.mocked(metricsApi.assetPool)).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText('近30天成交金额')).toBeNull());
    expect(screen.getByText('失效指标')).toBeTruthy();
  });

  it('批量归属：勾选多行一次写同一对象，部分失败也如实报数不假装全成功', async () => {
    renderPage();
    await screen.findByText('近30天成交金额');

    expect(screen.getAllByRole('checkbox').length).toBe(3);   // 只有指标/维度行可勾选
    await userEvent.click(within(rowOf('近30天成交金额')).getByRole('checkbox'));
    await userEvent.click(within(rowOf('渠道分布')).getByRole('checkbox'));
    expect(await screen.findByText(/已选 2 项未归属资产/)).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: '批量归属到对象' }));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText('批量归属 2 项')).toBeTruthy();
    // 批量只给计数与汇总（弹窗里只摊第一条会误导）
    expect(within(dlg).getByText(/本次处理/)).toBeTruthy();
    await userEvent.click(within(dlg).getByRole('button', { name: /订单明细/ }));

    // 服务端语义：指标行 401 写不进，维度行 501 成功
    vi.mocked(metricsApi.update).mockImplementation(async (id: number) => {
      if (id === 401) throw { response: { data: { detail: '对象已失效' } } } as any;
      dropMetric(id);
      return { data: { success: true } } as any;
    });
    vi.mocked(metricsApi.updateDimension).mockImplementation(async (id: number) => {
      pool.dimensions = pool.dimensions.filter((r: any) => r.id !== id);
      return { data: { success: true } } as any;
    });
    await userEvent.click(within(dlg).getByRole('button', { name: '确定' }));

    await waitFor(() => expect(metricsApi.updateDimension).toHaveBeenCalledWith(501, { bound_object_key: 'order_item' }));
    await waitFor(() => expect(vi.mocked(metricsApi.assetPool)).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // 失败项在刷新后的清单里原样保留（不被“成功”文案盖掉），成功项已消失
    expect(screen.getByText('近30天成交金额')).toBeTruthy();
    expect(screen.queryByText('渠道分布')).toBeNull();
  });

  it('转指标草稿 → 服务端落未启用行后同样自动刷新（候选离开 pending）', async () => {
    renderPage();
    await screen.findByText('退单率');

    await userEvent.click(within(rowOf('退单率')).getByRole('button', { name: '转指标草稿' }));
    const dlg = await screen.findByRole('dialog');
    await userEvent.click(within(dlg).getByRole('button', { name: /订单明细/ }));
    await userEvent.click(within(dlg).getByRole('button', { name: '确定' }));

    await waitFor(() => expect(client.post).toHaveBeenCalledWith(
      '/evolution/candidates/702/metric-draft',
      expect.objectContaining({ display_name: '退单率', bound_object_key: 'order_item' })));
    await waitFor(() => expect(vi.mocked(metricsApi.assetPool)).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText('退单率')).toBeNull());
    expect(screen.getByText('流水')).toBeTruthy();
  });

  it('删除字典行 → 二次确认后真删并刷新，失败行不得从清单消失', async () => {
    renderPage();
    await screen.findByText('失效指标');

    await userEvent.click(within(rowOf('失效指标')).getByRole('button', { name: /删除字典行/ }));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText(/不可恢复/)).toBeTruthy();
    await userEvent.click(within(dlg).getByRole('button', { name: '确认删除' }));

    await waitFor(() => expect(metricsApi.delete).toHaveBeenCalledWith(402));
    await waitFor(() => expect(vi.mocked(metricsApi.assetPool)).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText('失效指标')).toBeNull());
    expect(screen.getByText('近30天成交金额')).toBeTruthy();
  });

  it('非管理员：候选出口一律禁用并说明服务端口径，不做"点了必 403"的死开关', async () => {
    useAuthStore.setState({ user: { id: 2, username: 'analyst', role: 'analyst' } } as any);
    renderPage();
    await screen.findByText('流水');

    expect(await screen.findByText(/当前角色为只读分诊/)).toBeTruthy();
    const aliasBtn = within(rowOf('流水')).getByRole('button', { name: '收编为别名' });
    expect(aliasBtn).toBeDisabled();
    expect(aliasBtn.getAttribute('title')).toContain('管理员');
    const rejectBtn = within(rowOf('流水')).getByRole('button', { name: '驳回' });
    expect(rejectBtn).toBeDisabled();
    expect(within(rowOf('退单率')).getByRole('button', { name: '转指标草稿' })).toBeDisabled();
    // 字典行归属只看 metrics:manage（未限制时放行），与候选出口分属两套口径
    expect(within(rowOf('近30天成交金额')).getByRole('button', { name: '归属到对象' })).not.toBeDisabled();
    expect(screen.getByText('妥投率')).toBeTruthy();   // 只读分诊：清单照常可看
  });

  it('缺 metrics:manage 权限 → 归属出口禁用并点名权限，而不是按下去报 403', async () => {
    usePermissionStore.setState({ perms: ['metrics:read', 'asset-pool:read'], unrestricted: false, loaded: true });
    renderPage();
    await screen.findByText('近30天成交金额');

    const btn = within(rowOf('近30天成交金额')).getByRole('button', { name: '归属到对象' });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute('title')).toContain('metrics:manage');
    expect(within(rowOf('退单率')).getByRole('button', { name: '转指标草稿' })).toBeDisabled();
  });

  it('清单加载失败 → 显式红条 + 重试，不伪装成"待办已清空"', async () => {
    vi.mocked(metricsApi.assetPool).mockRejectedValueOnce({
      response: { data: { detail: '数据库连接失败' } },
    } as any);
    renderPage();

    expect(await screen.findByText('数据库连接失败')).toBeTruthy();
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
    // 历史掩盖：pool=null ⇒ total=0 仍然渲绿色成功空态（失败当“已清空”）
    expect(screen.queryByText('语义层没有待办缺口')).toBeNull();
    expect(screen.getByText(/待办清单未加载成功/)).toBeTruthy();
    expect(screen.queryByText('已清空')).toBeNull();      // 顶栏计数徽章同样不得把失败说成清空

    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('近30天成交金额')).toBeTruthy();
  });

  it('对象清单加载失败 → 左栏点名"选择框可能为空"并给重试，不静默空白', async () => {
    vi.mocked(metricsApi.filters).mockRejectedValueOnce({
      response: { data: { detail: '词表接口 500' } },
    } as any);
    renderPage();

    expect(await screen.findByText(/对象\/词表清单未加载/)).toBeTruthy();
    expect(screen.getByText(/归属选择框可能为空/)).toBeTruthy();
  });

  it('待办清空 → 与"没有匹配"两种空态分开表达', async () => {
    pool = { metrics: [], dimensions: [], terms: [], suggestions: [], counts: {} };
    renderPage();
    expect(await screen.findByText('语义层没有待办缺口')).toBeTruthy();
    expect(screen.getByText('已清空')).toBeTruthy();

    pool = { ...resetPool0() };
    await userEvent.click(screen.getByRole('button', { name: '刷新' }));
    await screen.findByText('近30天成交金额');
    await userEvent.type(screen.getByPlaceholderText('搜索业务名 / 数据源'), '不存在词');
    expect(await screen.findByText(/当前队列没有匹配/)).toBeTruthy();
    expect(screen.queryByText('语义层没有待办缺口')).toBeNull();
  });
});

const resetPool0 = () => {
  resetPool();
  return pool;
};
