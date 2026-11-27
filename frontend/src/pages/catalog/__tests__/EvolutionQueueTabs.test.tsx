/**
 * 进化中心「待建 / 核绑定 / 字典待办 / 知识更新 / 处置历史」五页签与建议列（spec §P2 / D7）。
 *
 * 锁六件事：
 * 1. 队列分流只读服务端 `suggestion.queue`：binding_gap 不出现在「待建」页签
 *    （混在一起会被当成新建任务，占着人工位置却没人能处置）；
 * 2. 档位文案来自服务端 `suggestion.tier`：new_* 命中 1 次显示「待观察」而不是 「推荐建」；
 * 3. 历史页签单独查状态：在历史页选状态不得把待办列表一起换掉（否则待建页签莫名空掉）；
 * 4. 写出口按权限码显隐：没有 metrics:manage 就不渲染「建指标草稿」（不造点了必 403 的死按钮）；
 * 5. 知识更新页签：提议只是备忘录（只按状态筛、处置按钮带码、标记失败必须显式报
 *    “已写入但未标记”），场景类提议跳到共享编辑器而不是另造一套表单；
 * 6. 字典待办页签（2026-11-21 由「建模待办」收敛）：只列未归属/孤儿字典行与未绑定术语，
 *    pending 候选只归待建/核绑定（两处各一套口径 = 漂移源，必须只剩一处）。
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EvolutionCenter from '../EvolutionCenter';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const CANDIDATES = [
  {
    id: 33, term: '渠道', kind: 'alias', source: 'parse_fail', datasource_name: 'prd-cn',
    target_ref: '应用渠道', target_type: 'dimension', classify_reason: '已命中权威归属',
    hit_count: 4, status: 'pending',
    // 证据里故意夹带物理表/列（历史缺陷）：页面任何位置都不得渲染它们
    evidence: {
      question: '渠道怎么算？', candidates: ['应用渠道'],
      target_table: 'pc_case', column_name: 'status',
      authoritative_hits: [{ ref: '应用渠道', target_table: 'pc_case', object_key: 'case' }],
    },
    suggestion: {
      suggested_kind: 'alias', queue: 'build', tier: 'act', confidence: 'high',
      ambiguous: false, drift: false, hit_count: 4,
      prefilled: { target_type: 'dimension', target_ref: '应用渠道' },
      suggested_refs: [], basis: '只加叫法不改口径', classify_reason: '已命中权威归属',
    },
  },
  {
    id: 36, term: '客户等级', kind: 'new_dimension', source: 'parse_fail', datasource_name: 'prd-cn',
    target_type: 'dimension', classify_reason: 'planner 结构化 type=dimension',
    hit_count: 4, status: 'pending',
    suggestion: {
      suggested_kind: 'new_dimension', queue: 'build', tier: 'recommend', confidence: 'high',
      ambiguous: false, drift: false, hit_count: 4,
      prefilled: { display_name: '客户等级' },
      suggested_refs: [], basis: '已命中 4 次（≥2）标「推荐建」', classify_reason: '',
    },
  },
  {
    id: 17, term: 'avg_cases_per_device', kind: 'new_metric', source: 'parse_fail',
    datasource_name: 'prd-cn', target_type: 'metric', classify_reason: 'planner 结构化 type=metric',
    hit_count: 1, status: 'pending',
    suggestion: {
      suggested_kind: 'new_metric', queue: 'build', tier: 'observe', confidence: 'high',
      ambiguous: false, drift: false, hit_count: 1,
      prefilled: { display_name: 'avg_cases_per_device', agg_type: 'COUNT' },
      suggested_refs: [], basis: '仅命中 1 次（<2）先标「待观察」', classify_reason: '',
    },
  },
  {
    id: 22, term: '事件时间', kind: 'new_dimension', source: 'parse_fail',
    datasource_name: 'arms-prd-cn', object_hint: 'rum_event', target_type: 'dimension',
    classify_reason: 'planner 结构化 type=dimension', hit_count: 1, status: 'pending',
    suggestion: {
      suggested_kind: 'new_dimension', queue: 'build', tier: 'observe', confidence: 'high',
      ambiguous: false, drift: false, hit_count: 1,
      prefilled: { display_name: '事件时间' },
      suggested_refs: [], basis: '仅命中 1 次（<2）先标「待观察」', classify_reason: '',
    },
  },
  {
    id: 11, term: 'DAU', kind: 'binding_gap', source: 'parse_fail', datasource_name: 'prd-cn',
    target_ref: 'DAU', target_type: 'metric', classify_reason: '字典行已登记但 planner 仍报未解析',
    hit_count: 1, status: 'pending',
    suggestion: {
      suggested_kind: 'binding_gap', queue: 'binding', tier: 'verify', confidence: 'high',
      ambiguous: false, drift: false, hit_count: 1,
      prefilled: {}, suggested_refs: [],
      basis: '字典行已建档但取数不可达，属绑定问题', classify_reason: '',
    },
  },
];

const OVERVIEW = {
  pending_by_kind: { alias: 1, new_dimension: 2, new_metric: 1, binding_gap: 1 },
  pending_by_source: { parse_fail: 5 },
  proposal_counts: { pending: 2, applied: 1, rejected: 0, superseded: 0 },
  outcome: {},
  feedback: { up: 0, down: 0, pending_negative: 0 },
  reasons: [], kinds: ['new_metric', 'new_dimension', 'alias', 'binding_gap', 'term'],
  sources: ['parse_fail'],
};

const PROPOSALS = [
  {
    id: 101, source: 'chat', target_type: 'kb_doc', title: '返利口径说明',
    content: '返利按确认收货月份计提，退货冲减当月。', rationale: '用户纠正了口径',
    target_hint: '业务知识库', datasource_name: '', status: 'pending', applied_ref: '',
    decided_by: '', decided_at: '', decided_note: '', origin_conversation_id: 88,
    origin_message_uuid: 'u-1', created_at: '2026-12-01 10:00:00', updated_at: '',
  },
  {
    id: 102, source: 'manual', target_type: 'scenario', title: '返利分析场景',
    content: '按月份分组看返利与退货。', rationale: '', target_hint: '',
    datasource_name: '', status: 'pending', applied_ref: '', decided_by: '',
    decided_at: '', decided_note: '', origin_conversation_id: 0,
    origin_message_uuid: '', created_at: '2026-12-01 09:00:00', updated_at: '',
  },
  {
    id: 103, source: 'evolution', target_type: 'ontology_object', title: '返利',
    content: '把说明改成按确认收货月份计提。', rationale: '', target_hint: '返利',
    datasource_name: '', status: 'applied', applied_ref: '订单模型 / 返利金额',
    decided_by: 'admin', decided_at: '2026-12-02 09:00:00', decided_note: '',
    origin_conversation_id: 0, origin_message_uuid: '',
    created_at: '2026-11-30 09:00:00', updated_at: '',
  },
];

const PROPOSAL_COUNTS = { pending: 2, applied: 1, rejected: 0, superseded: 0 };

function candidateStatusParams() {
  return vi.mocked(client.get).mock.calls
    .filter(([u]) => u === '/evolution/candidates')
    .map(([, cfg]) => (cfg as any)?.params?.status);
}

/** 页面实际挂在 /data/evolution 路由下，跳转按钮要用 Router 上下文，测试同样得包一层。 */
const renderPage = () => render(<MemoryRouter><EvolutionCenter /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  usePermissionStore.setState({ perms: null, allowedMenus: null, unrestricted: true, loaded: true });
  // 服务端真分页语义（P1-2）：queue/status/q 由请求参数下推，total 来自响应
  vi.mocked(client.get).mockImplementation(async (url: string, cfg?: any) => {
    const params = cfg?.params || {};
    if (url === '/evolution/candidates') {
      let rows = CANDIDATES.filter((c: any) => c.status === (params.status || 'pending'));
      if (params.queue === 'build') rows = rows.filter((c: any) => c.suggestion?.queue !== 'binding');
      if (params.queue === 'binding') rows = rows.filter((c: any) => c.suggestion?.queue === 'binding');
      return { data: { items: rows, total: rows.length, page: params.page || 1 } } as any;
    }
    if (url === '/evolution/overview') return { data: OVERVIEW } as any;
    if (url === '/evolution/proposals') {
      const rows = PROPOSALS.filter((p) => p.status === (params.status || 'pending'));
      return { data: { items: rows, counts: PROPOSAL_COUNTS, total: rows.length } } as any;
    }
    if (url === '/eval/suites') return { data: { suites: [], capabilities: [] } } as any;
    if (url === '/metrics/asset-pool') {
      const items = [{ queue: 'metric', id: 401, title: '近30天成交金额', name_en: '',
        aliases: [], bound_object_key: '', pool_reason: 'unbound', unit: '', agg_type: 'SUM',
        category: '', description: '', datasource_id: 0, term_type: '' }];
      return {
        data: { items, total: items.length, page: 1, page_size: 20,
          counts: { metrics: 1, dimensions: 0, terms: 0, total: 1 } },
      } as any;
    }
    return { data: { items: [] } } as any;
  });
});
afterEach(() => cleanup());

describe('进化中心：待建 / 核绑定分屏', () => {
  it('默认「待建」页签不含 binding_gap（DAU），核绑定页签才显示', async () => {
    renderPage();

    await screen.findByText('avg_cases_per_device');
    expect(screen.getByText('事件时间')).toBeInTheDocument();
    // binding_gap 不在待建队列：该行不应出现
    expect(screen.queryByText('DAU')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: '核绑定' }));
    expect(await screen.findByText('DAU')).toBeInTheDocument();
    expect(screen.queryByText('avg_cases_per_device')).not.toBeInTheDocument();
  });

  it('档位文案来自服务端：命中 1 次的 new_* 标「待观察」，≥2 标「推荐建」', async () => {
    renderPage();

    await screen.findByText('avg_cases_per_device');
    // avg_cases_per_device 与 事件时间 都是 hit=1 → 两条「待观察」
    expect(screen.getAllByText('待观察')).toHaveLength(2);
    expect(screen.getAllByText('推荐建')).toHaveLength(1);
  });

  it('切到历史页签按所选状态单独查，不拿待办数据充历史', async () => {
    renderPage();
    await screen.findByText('avg_cases_per_device');

    await userEvent.click(screen.getByRole('tab', { name: '处置历史' }));
    await waitFor(() => {
      expect(candidateStatusParams()).toContain('converted');
    });
    // 待办请求仍必须是 pending，不能被历史状态污染
    expect(candidateStatusParams()).toContain('pending');
  });
});

describe('进化中心：写出口按权限码显隐', () => {
  it('缺少 metrics:manage / ontology:save 时不渲染对应出口按钮', async () => {
    usePermissionStore.setState({
      perms: ['evolution:read'], allowedMenus: null, unrestricted: false, loaded: true,
    });
    renderPage();

    await screen.findByText('客户等级');
    expect(screen.queryByText('建维度草稿')).not.toBeInTheDocument();
    expect(screen.queryByText('建指标草稿')).not.toBeInTheDocument();
    expect(screen.queryByText('采纳建议')).not.toBeInTheDocument();
  });

  it('持有对应权限码时渲染出口按钮', async () => {
    usePermissionStore.setState({
      perms: ['ontology:save', 'metrics:manage'], allowedMenus: null, unrestricted: false, loaded: true,
    });
    renderPage();

    await screen.findByText('客户等级');
    // 两条 new_dimension（客户等级 / 事件时间）都带「建维度草稿」
    expect(screen.getAllByText('建维度草稿').length).toBeGreaterThan(0);
    expect(screen.getByText('采纳建议')).toBeInTheDocument();
  });
});

describe('进化中心：字典待办页签', () => {
  it('只列字典缺口；pending 候选只在待建/核绑定，不在这里重复出现', async () => {
    renderPage();
    await screen.findByText('avg_cases_per_device');

    await userEvent.click(screen.getByRole('tab', { name: '字典待办' }));
    expect(await screen.findByText('近30天成交金额')).toBeInTheDocument();
    // 候选不随页签搬过来（否则又成了两套候选出口）
    expect(screen.queryByText('avg_cases_per_device')).not.toBeInTheDocument();
    expect(screen.queryByText('客户等级')).not.toBeInTheDocument();
  });
});

describe('进化中心：知识更新页签（提议只是备忘录）', () => {
  const grant = (perms: string[]) => usePermissionStore.setState({
    perms, allowedMenus: null, unrestricted: false, loaded: true,
  });

  it('默认只看待处理：页签徽标给 pending 数，已处置的不混进来', async () => {
    grant(['evolution:read', 'knowledge:manage']);
    renderPage();

    const tab = await screen.findByRole('tab', { name: /知识更新/ });
    await waitFor(() => expect(within(tab).getByText('2')).toBeInTheDocument());
    await userEvent.click(tab);

    expect(await screen.findByText('返利口径说明')).toBeInTheDocument();
    expect(screen.getByText('返利分析场景')).toBeInTheDocument();
    // applied 那条只该出现在「已处理」筛选下
    expect(screen.queryByText('返利')).not.toBeInTheDocument();
    // 来源与落点都得看得见（不知道谁提的、提到哪，就没法判断该不该信）
    expect(screen.getByText(/业务知识库/)).toBeInTheDocument();
    expect(screen.getByText('用户纠正了口径')).toBeInTheDocument();
  });

  it('切到「已处理」：显示处理人与落点，不把待处理当成已完成', async () => {
    grant(['evolution:read', 'knowledge:manage']);
    renderPage();
    await userEvent.click(await screen.findByRole('tab', { name: /知识更新/ }));

    await userEvent.click(screen.getByRole('combobox', { name: '状态' }));
    await userEvent.click(await screen.findByRole('option', { name: '已处理' }));

    expect(await screen.findByText('返利')).toBeInTheDocument();
    expect(screen.getByText(/admin/)).toBeInTheDocument();
    expect(screen.getByText(/订单模型 \/ 返利金额/)).toBeInTheDocument();
    expect(screen.queryByText('返利口径说明')).not.toBeInTheDocument();
  });

  it('缺少 knowledge:manage：不给处置按钮，但把缺哪条码写在页面上', async () => {
    grant(['evolution:read']);
    renderPage();
    await userEvent.click(await screen.findByRole('tab', { name: /知识更新/ }));

    expect(await screen.findByText('返利口径说明')).toBeInTheDocument();
    expect(screen.queryByText('去处理')).not.toBeInTheDocument();
    expect(screen.queryByText('驳回')).not.toBeInTheDocument();
    // 可见原因比"按钮静静消失"有用：用户知道该找谁要权限
    expect(screen.getByText('去处理需要「管理分析场景」权限')).toBeInTheDocument();
  });

  it('标记已处理失败必须显式提示：已写入但提议没标记，不许假装闭环', async () => {
    grant(['evolution:read', 'knowledge:manage']);
    vi.mocked(client.post).mockRejectedValue({
      response: { status: 500, data: { detail: '状态机忙' } },
    });
    renderPage();
    await userEvent.click(await screen.findByRole('tab', { name: /知识更新/ }));
    await screen.findByText('返利口径说明');

    const row = screen.getByText('返利口径说明').closest('div.border') as HTMLElement;
    await userEvent.click(within(row).getByText('标记已处理'));

    // applied_ref 必填：落点为空时确认按钮禁用（空落点假闭环在服务层也会被拒 422）
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: '标记已处理' });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByPlaceholderText(/落点业务名/), '业务知识库 · 返利口径');
    await userEvent.click(confirm);

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    const msg = vi.mocked(toast.error).mock.calls[0][0] as string;
    expect(msg).toContain('已写入目标资产，但提议未标记为已处理');
    expect(msg).toContain('状态机忙');
  });

  it('场景类提议：去处理→追加到已有→打开共享场景编辑器（不另造一套表单）', async () => {
    grant(['evolution:read', 'knowledge:manage']);
    renderPage();
    await userEvent.click(await screen.findByRole('tab', { name: /知识更新/ }));

    const row = (await screen.findByText('返利分析场景')).closest('div.border') as HTMLElement;
    await userEvent.click(within(row).getByText('去处理'));

    // 分支只做「新建 / 追加」选择，正文写入交给共享编辑器
    await userEvent.click(await screen.findByText('追加到已有场景（保留原内容）'));
    expect(await screen.findByRole('combobox', { name: '选择要追加到的场景 *' })).toBeInTheDocument();
    expect(screen.getByText(/正文会拼到所选场景正文之后/)).toBeInTheDocument();
  });
});

describe('进化中心：服务端真分页 / URL 状态 / 决策安全（P1-1/P1-2）', () => {
  const grant = (perms: string[]) => usePermissionStore.setState({
    perms, allowedMenus: null, unrestricted: false, loaded: true,
  });

  it('翻页发服务端请求（page=2），total 来自响应（共 N 条，不是页长）', async () => {
    grant(['evolution:read', 'ontology:save']);
    // 响应 total=50、页内只有 2 条：分页条必须显示服务端 total，不得拿页长充数
    vi.mocked(client.get).mockImplementation(async (url: string, cfg?: any) => {
      const params = cfg?.params || {};
      if (url === '/evolution/candidates') {
        const rows = CANDIDATES.filter((c: any) => c.status === 'pending'
          && c.suggestion?.queue !== 'binding');
        return { data: { items: rows.slice(0, 2), total: 50, page: params.page || 1 } } as any;
      }
      if (url === '/evolution/overview') return { data: OVERVIEW } as any;
      return { data: { items: [] } } as any;
    });
    renderPage();
    await screen.findByText('渠道');
    expect(screen.getByText(/共 50 条/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => {
      const pages = vi.mocked(client.get).mock.calls
        .filter(([u]) => u === '/evolution/candidates')
        .map(([, cfg]) => (cfg as any)?.params?.page);
      expect(pages).toContain(2);
    });
  });

  it('搜索防抖后下推服务端并回第 1 页', async () => {
    grant(['evolution:read', 'ontology:save']);
    renderPage();
    await screen.findByText('avg_cases_per_device');
    await userEvent.type(screen.getByRole('textbox', { name: /搜索业务词/ }), 'DAU');
    await waitFor(() => {
      const calls = vi.mocked(client.get).mock.calls
        .filter(([u]) => u === '/evolution/candidates')
        .map(([, cfg]) => (cfg as any)?.params);
      expect(calls.some((p: any) => p?.q === 'DAU')).toBe(true);
      expect(calls[calls.length - 1]?.page).toBe(1);
    }, { timeout: 2000 });
  });

  it('决策 409（被并发改动）→ 显式提示并刷新列表，不覆盖既有结论', async () => {
    grant(['evolution:read', 'ontology:save']);
    vi.mocked(client.post).mockRejectedValue({
      response: { status: 409, data: { detail: '该条已被并发决策修改' } },
    });
    renderPage();
    await screen.findByText('avg_cases_per_device');

    await userEvent.click(screen.getAllByRole('button', { name: '驳回' })[0]);
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByRole('textbox'), '不是真实业务叫法');
    await userEvent.click(within(dialog).getByRole('button', { name: '驳回' }));

    await waitFor(() => expect(toast.warning).toHaveBeenCalled());
    expect(vi.mocked(toast.warning).mock.calls[0][0]).toContain('已被并发决策修改');
  });

  it('驳回理由必填：空理由时确认按钮禁用（与服务层同口径）', async () => {
    grant(['evolution:read', 'ontology:save']);
    renderPage();
    await screen.findByText('avg_cases_per_device');

    await userEvent.click(screen.getAllByRole('button', { name: '驳回' })[0]);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: '驳回' })).toBeDisabled();
  });

  it('「直接维护」入口不存在，物理表/列字段不渲染', async () => {
    grant(['evolution:read', 'ontology:save', 'metrics:manage', 'knowledge:manage']);
    renderPage();
    await screen.findByText('avg_cases_per_device');

    // P1-1：进化中心不再承载本体/知识库/场景的第二套维护界面
    expect(screen.queryByText('直接维护')).toBeNull();
    expect(screen.queryByText('更新本体对象')).toBeNull();
    // 证据里的物理表/列（含 authoritative_hits.target_table）任何位置都不渲染
    expect(screen.queryByText(/pc_case/)).toBeNull();
    expect(screen.queryByText(/column_name/)).toBeNull();
  });
});