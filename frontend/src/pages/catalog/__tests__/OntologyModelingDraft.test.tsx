import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import OntologyModeling from '../OntologyModeling';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/api/metrics', () => ({
  metricsApi: { assetPool: vi.fn(async () => ({ data: { counts: { total: 0 } } })) },
}));

/**
 * 本体建模 · 草案工作区回归（2026-10-28 用户否定旧设计后重做）。
 *
 * 用户原话：「有存草案按钮没有编辑草案的地方」「为什么是 JSON 草案，不能可视化为
 * 对象清单给用户看吗，无非是加一个状态区分」「（未归属资产池）单独抽出来，放在数据建模中也不合适」。
 *
 * 锁住的行为：
 * 1. 草案是工作区的一个**可编辑视图**（生效版只读、草案可改），不是只能看的 JSON；
 * 2. 编辑基线必须是草案：在草案视图新建对象提交的正文要含 AS-BOT 已写的草案对象（不顶掉）；
 * 3. 「变更对比」页签渲对象级可视化清单（业务名 + 状态徽标 + 属性/关系/别名明细），
 *    JSON 只作折叠附录（旧「查看草案」弹窗不得复活）；
 * 4. 发布/丢弃都由服务端草案状态驱动，动作后重拉真值（不出现点了没反应、标记还在）；
 * 5. 无 ontology:publish 权限不渲染发布入口（不给死开关）；基线失效/草案损坏必须禁用并说明；
 * 6. 建模待办已抽为独立页，本页只留跳转入口。
 */

const EFF_DOC = {
  objects: [
    { key: 'case', display_name: '案例', properties: [{ name: '工单号' }], links: [] },
    { key: 'isv', display_name: 'ISV', properties: [], links: [] },
  ],
};
/** 草案：新增 1 个对象 + 改动 case（多一个属性与一个别名） */
const DRAFT_DOC = {
  objects: [
    {
      key: 'case', display_name: '案例', aliases: ['案例单'],
      properties: [{ name: '工单号' }, { name: '创建时间' }], links: [],
    },
    EFF_DOC.objects[1],
    { key: 'isv_integration', display_name: '集成记录', properties: [{ name: '来源类型' }], links: [] },
  ],
};

const CASE_DELTA = {
  properties_added: [{ key: 'create_time', name: '创建时间' }],
  properties_removed: [], properties_modified: [],
  links_added: [], links_removed: [],
  aliases_added: ['案例单'], aliases_removed: [],
  renamed_from: '', table_changed: false,
  primary_table_from: '', primary_table_to: '', description_changed: false,
};
const OBJ_ADDED = {
  key: 'isv_integration', display_name: '集成记录', state: 'added',
  primary_table: '', property_count: 1, link_count: 0, delta: null,
};
const OBJ_CHANGED = {
  key: 'case', display_name: '案例', state: 'changed',
  primary_table: '', property_count: 2, link_count: 0, delta: CASE_DELTA,
};
const OBJ_UNCHANGED = {
  key: 'isv', display_name: 'ISV', state: 'unchanged',
  primary_table: '', property_count: 0, link_count: 0, delta: null,
};

const MODEL = {
  id: 901, name: '联耀业务本体', datasource_id: 0, kind: 'business', status: 'active',
  object_count: 2, created_by: 'admin', created_at: '2026-10-16T00:00:00Z',
  updated_at: '2026-10-16T00:00:00Z',
};

/** 内存服务端：草案/生效版正文与状态互相一致，动作后必须重拉才对得上 */
const state = {
  hasDraft: true, baseStale: false, parseError: false,
  effective: EFF_DOC as any,
};

const draftPayload = () => (state.hasDraft
  ? {
    has_draft: true, draft_updated_at: '2026-10-28T16:38:28', draft_updated_by: 'admin',
    effective_object_count: 2, draft_object_count: 3, unchanged_count: 1,
    objects: [OBJ_ADDED, OBJ_CHANGED, OBJ_UNCHANGED],
    added: [{ ...OBJ_ADDED }], changed: [{ ...OBJ_CHANGED }], removed: [],
    base_stale: state.baseStale, draft_parse_error: state.parseError,
  }
  : {
    has_draft: false, draft_updated_at: '', draft_updated_by: '',
    effective_object_count: state.effective.objects.length,
    draft_object_count: state.effective.objects.length,
    unchanged_count: state.effective.objects.length,
    // 无草案：服务端回全量 unchanged 行（发布后对象数要跟着生效正文走）
    objects: state.effective.objects.map((o: any) => ({
      key: o.key, display_name: o.display_name, state: 'unchanged',
      primary_table: o.primary_table || '', property_count: (o.properties || []).length,
      link_count: (o.links || []).length, delta: null,
    })),
    added: [], changed: [], removed: [],
    base_stale: false, draft_parse_error: false,
  });

function mockApi() {
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/datasources') return { data: [{ id: 5, name: 'prd-sls-cn', db_type: 'mysql' }] } as any;
    if (url === '/knowledge-bases') return { data: [] } as any;
    if (url === '/catalog/ontology/models') {
      return { data: { items: [{ ...MODEL, has_draft: state.hasDraft }] } } as any;
    }
    if (url === '/catalog/ontology/models/901/draft/content') {
      return {
        data: {
          id: 901, name: MODEL.name, status: 'active', has_draft: state.hasDraft,
          draft_json_content: JSON.stringify(DRAFT_DOC),
          effective_json_content: JSON.stringify(state.effective),
        },
      } as any;
    }
    if (url === '/catalog/ontology/models/901/draft') return { data: draftPayload() } as any;
    if (url === '/catalog/ontology/models/901/sync-status') {
      return { data: { graph: { state: 'synced', objects: 2 }, kb: { state: 'synced', targets: 1 } } } as any;
    }
    if (url.startsWith('/catalog/ontology/models/')) {
      return { data: { ...MODEL, json_content: JSON.stringify(state.effective) } } as any;
    }
    if (url === '/catalog/ontology/generate/tasks') return { data: { items: [] } } as any;
    return { data: [] } as any;
  });
  vi.mocked(client.post).mockImplementation(async (url: string) => {
    if (url.endsWith('/publish')) {
      state.hasDraft = false;
      state.effective = DRAFT_DOC;   // 发布后生效版即草案内容
      return { data: { success: true, object_count: 3 } } as any;
    }
    return { data: { success: true } } as any;
  });
  vi.mocked(client.put).mockImplementation(async (url: string, body: any) => {
    if (url.endsWith('/draft')) {
      state.hasDraft = true;   // 存草案不碰生效内容
      return { data: { ...MODEL, draft_pending: true, note: '改动已存为草案，未生效' } } as any;
    }
    return { data: { ...MODEL, json_content: body?.json_content } } as any;
  });
  vi.mocked(client.delete).mockImplementation(async (url: string) => {
    if (url.endsWith('/draft')) {
      state.hasDraft = false;
      return { data: draftPayload() } as any;
    }
    return { data: { success: true } } as any;
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(state, { hasDraft: true, baseStale: false, parseError: false, effective: EFF_DOC });
  usePermissionStore.setState({ perms: null, allowedMenus: null, unrestricted: true, loaded: false });
  mockApi();
});

afterEach(() => cleanup());

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/data/ontology']}>
      <Routes>
        <Route path="/data/ontology" element={<OntologyModeling />} />
        <Route path="/data/asset-pool" element={<div>建模待办工作台</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** 选中左侧模型（草案状态是选中模型后向服务端拉的） */
async function pickModel() {
  renderPage();
  await screen.findByText('联耀业务本体');
  await userEvent.click(screen.getByRole('button', { name: /联耀业务本体/ }));
}

/** 切到草案视图（顶栏的视图切换按钮） */
async function toDraftView() {
  await userEvent.click(await screen.findByRole('button', { name: /^草案 · / }));
}

/** 打开「变更对比」页签 */
async function toChangesTab() {
  await userEvent.click(await screen.findByRole('tab', { name: /变更对比/ }));
}

describe('本体建模 · 草案工作区（双视图 + 可视化审阅）', () => {
  it('服务端有草案 → 顶栏给双视图、横幅点名差异，发布按钮可用（不再凭本地布尔）', async () => {
    await pickModel();

    expect(await screen.findByText(/新增「集成记录」/)).toBeTruthy();
    expect((await screen.findAllByText(/修改 1 个对象/)).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /^生效版 · 2$/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^草案 · 3/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^发布草案（2）$/ })).not.toBeDisabled();
    expect(screen.getAllByText('待发布').length).toBeGreaterThan(0);
    // 拉过服务端真值，不是凭本地布尔下结论
    expect(vi.mocked(client.get).mock.calls.some(([u]) => u === '/catalog/ontology/models/901/draft')).toBe(true);
  });

  it('生效版只读：有未发布草案时不给「新建对象」，只给「切到草案后编辑」（防整份顶掉草案）', async () => {
    await pickModel();
    await screen.findByText(/新增「集成记录」/);

    expect(screen.queryByRole('button', { name: /新建对象/ })).toBeNull();
    const goEdit = screen.getByRole('button', { name: /切到草案后编辑/ });
    expect(screen.getByText('生效版只读（线上正在用的口径）；要改建模请切到草案视图。')).toBeTruthy();

    await userEvent.click(goEdit);
    expect(await screen.findByText('草案视图·未生效')).toBeTruthy();
    expect(screen.getByRole('button', { name: /新建对象/ })).toBeTruthy();
  });

  it('草案是工作区的一个可编辑视图：对象清单带状态区分，编辑提交的正文基于草案', async () => {
    await pickModel();
    await toDraftView();

    // 可视化对象清单（不是 JSON）：草案独有的业务对象带「新增」状态徽标
    expect(screen.getByText('此处显示的是草案内容：新建/编辑只写草案，不会改动线上生效版；核对后到「变更对比」点发布才级联。')).toBeTruthy();
    const addedRow = await screen.findByRole('button', { name: /集成记录/ });
    expect(within(addedRow).getByText('新增')).toBeTruthy();
    expect(within(addedRow).getByText('isv_integration')).toBeTruthy();
    // 草案里改过的对象带「修改」状态
    expect(within(screen.getByRole('button', { name: /案例/ })).getByText('修改')).toBeTruthy();

    // 在草案上新建对象 → 提交正文必须仍是草案（含 AS-BOT 写的 isv_integration）
    await userEvent.click(screen.getByRole('button', { name: /新建对象/ }));
    const dlg = await screen.findByRole('dialog');
    const boxes = within(dlg).getAllByRole('textbox');
    await userEvent.type(boxes[0], 'isv_token');      // 对象 key
    await userEvent.type(within(dlg).getByPlaceholderText('如：订单明细'), '密钥记录');
    await userEvent.click(within(dlg).getByRole('button', { name: '创建' }));

    await waitFor(() => expect(vi.mocked(client.put)).toHaveBeenCalled());
    const call = vi.mocked(client.put).mock.calls.find(([u]) => u === '/catalog/ontology/models/901/draft');
    expect(call).toBeTruthy();
    const keys = JSON.parse((call![1] as any).json_content).objects.map((o: any) => o.key);
    expect(keys).toEqual(expect.arrayContaining(['case', 'isv', 'isv_integration', 'isv_token']));
    expect(keys).not.toContain('undefined');
  });

  it('「变更对比」渲对象级可视化清单：状态分组 + 属性/别名明细，JSON 只作折叠附录', async () => {
    await pickModel();
    await toChangesTab();

    expect(await screen.findByText(/待发布草案：对象 2 → 3/)).toBeTruthy();
    expect(screen.getByText('全部 3')).toBeTruthy();
    expect(screen.getByText('新增 1')).toBeTruthy();
    expect(screen.getByText('修改 1')).toBeTruthy();
    expect(screen.getByText('未改动 1')).toBeTruthy();
    // 行内人话摘要（显示业务名，key 只作次要信息）
    expect(await screen.findByText('集成记录')).toBeTruthy();
    expect(screen.getByText('1 个属性 · 0 条关系')).toBeTruthy();
    expect(screen.getByText('属性 +1 · 别名 +1')).toBeTruthy();

    // 展开修改明细：属性 +1、别名 +1 是结构化项，不要求人比 JSON
    const toggles = screen.getAllByTitle('展开变更明细');
    await userEvent.click(toggles[1]);
    expect(await screen.findByText('创建时间')).toBeTruthy();
    expect(screen.getByText('案例单')).toBeTruthy();
    expect(screen.getByText(/属性 \+1 · 别名 \+1/)).toBeTruthy();

    // 主视图不摊 JSON；原始正文收在「原始内容」附录里
    expect(screen.queryByText(/"objects"/)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /原始内容/ }));
    expect(await screen.findByText('草案原始内容（JSON 事实源）')).toBeTruthy();
    expect((await screen.findAllByText(/"objects"/)).length).toBeGreaterThan(0);
    // 旧「查看草案」JSON 弹窗入口不得复活
    expect(screen.queryByRole('button', { name: /查看草案/ })).toBeNull();
  });

  it('丢弃草案 → DELETE 后回到生效版，待发布标记与变更对比页签一起消失', async () => {
    await pickModel();
    await toChangesTab();
    await userEvent.click(await screen.findByRole('button', { name: /丢弃草案/ }));
    expect(await screen.findByText('草案丢弃后不可恢复；生效版与图谱/知识库不受影响。')).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: /确认丢弃/ }));
    await waitFor(() => expect(vi.mocked(client.delete)).toHaveBeenCalledWith(
      '/catalog/ontology/models/901/draft'));

    await waitFor(() => expect(screen.queryByText(/待发布改动尚未生效/)).toBeNull());
    expect(screen.queryByText('待发布')).toBeNull();
    expect(screen.queryByRole('tab', { name: /变更对比/ })).toBeNull();
    expect(screen.getByRole('button', { name: /^草案 · / })).toBeDisabled();
  });

  it('发布成功 → 重拉生效版正文（对象 2 → 3）且草案标记清零，不停在旧数量', async () => {
    await pickModel();
    await screen.findByText(/新增「集成记录」/);

    await userEvent.click(screen.getByRole('button', { name: /^发布草案（2）$/ }));
    await waitFor(() => expect(vi.mocked(client.post)).toHaveBeenCalledWith(
      '/catalog/ontology/models/901/publish', {}));

    await waitFor(() => expect(screen.getByRole('button', { name: /^生效版 · 3$/ })).toBeTruthy());
    expect(screen.queryByText('待发布')).toBeNull();
    // 发布后视图回生效版：工单/属性不再带草案状态徽标（状态区分只属于未发布内容）
    expect(screen.queryByText('草案视图·未生效')).toBeNull();
    expect(within(screen.getByRole('button', { name: /集成记录/ })).queryByText('新增')).toBeNull();
  });

  it('无 ontology:publish 权限 → 两个发布入口都不渲染（不给按下去必拒的死开关）', async () => {
    usePermissionStore.setState({
      perms: ['metrics:read', 'ontology:save'], unrestricted: false, loaded: true,
    });
    await pickModel();
    await screen.findByText(/新增「集成记录」/);

    expect(screen.queryByRole('button', { name: /^发布草案/ })).toBeNull();
    await toChangesTab();
    expect(screen.queryByRole('button', { name: /^发布草案/ })).toBeNull();
    // 有 save 码：审阅面板仍给编辑与丢弃（不是整页瘫掉）
    expect(screen.getByRole('button', { name: /在草案中编辑/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /丢弃草案/ })).toBeTruthy();
  });

  it('基线已失效 → 明示发布会被阻断并禁用发布（不假装能一键发布）', async () => {
    state.baseStale = true;
    await pickModel();
    expect(await screen.findByText(/发布会被阻断，需重做草案/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /^发布草案（2）$/ })).toBeDisabled();

    await toChangesTab();
    expect(screen.getByText(/生效版在草案写入后又被改动，发布会被并发保护阻断/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /^发布草案$/ })).toBeDisabled();
  });

  it('草案正文结构异常 → 显式报警并禁用发布（不渲成“空审阅列表”假称无改动）', async () => {
    state.parseError = true;
    await pickModel();
    await toChangesTab();
    expect(await screen.findByText(/草案正文解析不出对象清单/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /^发布草案$/ })).toBeDisabled();
  });

  it('服务端无草案 → 草案视图禁用并说明原因，不给变更对比页签', async () => {
    state.hasDraft = false;
    await pickModel();
    const draftBtn = await screen.findByRole('button', { name: /^草案 · / });
    expect(draftBtn).toBeDisabled();
    expect(draftBtn.getAttribute('title')).toBe('当前没有待发布的草案');
    expect(screen.queryByRole('tab', { name: /变更对比/ })).toBeNull();
    expect(screen.queryByText('待发布')).toBeNull();
    // 无草案时编辑入口照常开放（生效版即编辑基线，不存在顶掉草案的风险）
    expect(screen.getByRole('button', { name: /新建对象/ })).toBeTruthy();
  });

  it('建模待办已抽为独立页：本页只留入口，点击跳 /data/asset-pool', async () => {
    renderPage();
    const entry = await screen.findByRole('button', { name: /建模待办（未归属资产）/ });
    await userEvent.click(entry);
    expect(await screen.findByText('建模待办工作台')).toBeTruthy();
    // 旧的「未归属资产池」内嵌视图不得残留
    expect(screen.queryByText('联耀业务本体')).toBeNull();
  });
});
