import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ontologyApi } from '@/api/ontology';
import ObjectHubTab from '../ObjectHubTab';

vi.mock('@/api/ontology', () => ({
  ontologyApi: {
    sites: vi.fn(), saveDraft: vi.fn(), getDraft: vi.fn(), getDraftContent: vi.fn(),
    // RouteEditor 的目标源本体清单（选框选项）
    list: vi.fn(), get: vi.fn(),
    // 「编辑对象 → 属性列映射」的实存列候选
    objectColumns: vi.fn(),
  },
}));

/** 默认回放：对象无任何部署绑定行（具体用例自行 mockImplementation） */
beforeEach(() => {
  vi.mocked(ontologyApi.sites).mockReset();
  vi.mocked(ontologyApi.sites).mockResolvedValue(
    { data: { kind: 'business', sites: [], selected: [], contract_objects: [] } } as any);
  vi.mocked(ontologyApi.list).mockReset();
  vi.mocked(ontologyApi.list).mockResolvedValue({ data: { items: [] } } as any);
  vi.mocked(ontologyApi.objectColumns).mockReset();
  vi.mocked(ontologyApi.objectColumns).mockResolvedValue(
    { data: { object_key: '', table: '', bind_kind: 'primary', dataset_ref: '', columns: [], note: '' } } as any);
});

/**
 * 「对象 · Link」页签渲染回归。
 *
 * 背景：存量源本体（kind='source'）落库的 canonical doc 用旧字段名 object_key/name，
 * 组件直接 `o.key.toLowerCase()` 在渲染期崩溃（白屏 + Uncaught TypeError）。
 * 对象缺少可读身份时也不得静默显示成空白 —— 必须显式提示。
 */

afterEach(() => cleanup());

describe('ObjectHubTab — 对象清单渲染不因字段形状崩溃', () => {
  it('旧字段名（object_key/name）的对象照常显示业务名与关系', () => {
    render(
      <ObjectHubTab
        modelId={1}
        objects={[
          { object_key: 'case_stat', name: '案例统计', primary_table: 'daily_case_stats' } as any,
          { object_key: 'region', name: '地区', primary_table: 'dim_region' } as any,
        ]}
      />,
    );
    // 清单项 + 详情标题两处都显示业务名（不得出现裸 key/undefined）
    expect(screen.getAllByText('案例统计').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('地区')).toBeTruthy();
    expect(screen.queryByText('undefined')).toBeNull();
    expect(screen.getByText('对象（2）')).toBeTruthy();
  });

  it('完全无法定位身份的对象被剔除，但页面显式提示原因', () => {
    render(
      <ObjectHubTab
        modelId={1}
        objects={[{ description: '没有 key 也没有主表' } as any]}
        issues={[{ index: 0, label: '第 1 项', reason: 'no_identity', hint: '缺少 key' }]}
      />,
    );
    expect(screen.getByText(/1 个对象定义无法展示/)).toBeTruthy();
    expect(screen.getByText('该模型的对象定义均缺少 key，无法展示（见左侧提示）')).toBeTruthy();
    expect(screen.getByText('对象（0）')).toBeTruthy();
  });

  it('悬空 Link 目标仍标为孤儿引用（不静默放行）', () => {
    render(
      <ObjectHubTab
        modelId={1}
        objects={[{ key: 'case_file', display_name: '案例', links: [{ type: 'belongs_to', target: 'region' }] } as any]}
      />,
    );
    expect(screen.getByText(/1 条 Link 目标不在本模型/)).toBeTruthy();
  });
});

describe('ObjectHubTab — 页面内编辑对象（2026-10-27 开放，不再仅 AS-BOT）', () => {
  const doc = {
    kind: 'source',
    objects: [{
      key: 'case', display_name: '案例', description: '旧描述', aliases: ['病例'],
      primary_table: 'pc_case',
      properties: [{ column: 'pc_case.id', name: '主键ID' }],
      links: [{ type: 'belongs_to', target: 'region' }],
    }],
  };
  const hub = doc.objects as any[];

  it('未传 canEdit/onSave 时不渲染编辑入口（只读展示场景不变）', () => {
    render(<ObjectHubTab modelId={1} objects={hub} />);
    expect(screen.queryByRole('button', { name: '编辑对象' })).toBeNull();
  });

  it('canEdit 时渲染编辑入口，保存后只改标量字段、不整份覆盖属性/关系', async () => {
    const onSave = vi.fn();
    render(
      <ObjectHubTab modelId={1} objects={hub} canEdit
        jsonContent={JSON.stringify(doc, null, 2)} onSave={onSave} />,
    );
    await userEvent.click(screen.getByRole('button', { name: '编辑对象' }));
    // 表单回填当前对象值
    const nameInput = screen.getByDisplayValue('案例');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, '案例档案');
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const saved = JSON.parse(onSave.mock.calls[0][0]);
    const obj = saved.objects.find((o: any) => o.key === 'case');
    expect(obj.display_name).toBe('案例档案');
    // 关键护栏：properties/links 必须原样保留（不整份覆盖）
    expect(obj.properties).toEqual([{ column: 'pc_case.id', name: '主键ID' }]);
    expect(obj.links).toEqual([{ type: 'belongs_to', target: 'region' }]);
    expect(obj.primary_table).toBe('pc_case');
    expect(obj.aliases).toEqual(['病例']);
  });

  it('无 ontology:save 权限（canEdit=false）时即使传了 onSave 也不给编辑入口', () => {
    render(
      <ObjectHubTab modelId={1} objects={hub} canEdit={false}
        jsonContent={JSON.stringify(doc)} onSave={vi.fn()} />,
    );
    expect(screen.queryByRole('button', { name: '编辑对象' })).toBeNull();
  });
});

describe('ObjectHubTab — 绑定徽标读部署绑定层（不拿 doc 字段误报）', () => {
  const objects = [{ key: 'rum_event', display_name: '前端事件', primary_table: '' } as any];

  it('有生效契约行时给 bound，不出现「未绑定物理表」假告警', async () => {
    vi.mocked(ontologyApi.sites).mockResolvedValue({
      data: { kind: 'business', sites: [], selected: [], contract_objects: [{
        object_key: 'rum_event', object_name: '前端事件', datasource: 'arms-prd-cn',
        physical_table: 'logstore-rum', bind_kind: 'primary', column_count: 25,
        sync_state: '', product_ref: '', allowed_sites: [],
      }] },
    } as any);
    render(<ObjectHubTab modelId={7} objects={objects} />);
    await waitFor(() => expect(screen.getByText('bound')).toBeTruthy());
    expect(screen.queryByText('未绑定物理表')).toBeNull();
    // 明细行用业务名（数据源名）展示，不要求手输/裸显 id
    expect(screen.getByText(/logstore-rum/).textContent).toContain('arms-prd-cn');
  });

  it('契约行缺失时显式给可诊断告警（含修复入口提示）', async () => {
    // 旧文案把人引到「同构站点部署」页登记物理表，而那页只配站点、根本不写绑定行
    // —— 假引导（用户照做仍然"未绑定"）。修复动作改成本页真能走通的那条：
    // 编辑对象填绑定物理表 → 保存草案 → 发布（发布级联登记部署绑定）。
    render(<ObjectHubTab modelId={7} objects={objects} />);
    await waitFor(() => expect(screen.getByText('未绑定物理表')).toBeTruthy());
    const hint = screen.getByText(/尚无生效的部署绑定行/).textContent || '';
    expect(hint).toContain('编辑对象');
    expect(hint).toContain('发布');
    expect(screen.getByText('未绑定物理表').getAttribute('title')).not.toContain('同构站点部署');
  });

  it('绑定接口失败时给「绑定状态未知」，不得拿 doc 字段猜成已绑定', async () => {
    vi.mocked(ontologyApi.sites).mockRejectedValue({ response: { statusText: 'Forbidden' } });
    render(<ObjectHubTab modelId={7}
      objects={[{ key: 'rum_event', display_name: '前端事件', primary_table: 'legacy_table' } as any]} />);
    await waitFor(() => expect(screen.getByText('绑定状态未知')).toBeTruthy());
    expect(screen.queryByText('bound')).toBeNull();
    expect(screen.getByText(/读取部署绑定失败/)).toBeTruthy();
  });
});

/**
 * 回归（真实缺陷）：存量模型「数据仓库模型」的 canonical doc 用旧字段名 object_key/name，
 * 对象清单归一后看得见 `case_stat`，但「编辑对象 → 保存为草案」的写入口按裸 o.key 查找，
 * 永远落空 → 弹「对象不存在: case_stat」，该模型下一个对象都改不了。
 * 写入口必须与读边界同一口径（findObjectIndex），否则页面给的是无法回写的假象。
 */
describe('ObjectHubTab — 存量旧字段名 doc 必须能从页面编辑', () => {
  const legacyDoc = {
    // 顶层无 kind（后端 save_draft 才补写），对象用旧字段名 object_key/name
    objects: [{
      object_key: 'case_stat', name: '案例统计', aliases: ['案例日报'],
      description: '按日聚合的案例数量', primary_table: 'daily_case_stats',
      properties: [{ column: 'daily_case_stats.stat_date', name: '统计日期' }],
    }],
    metrics: [], dimensions: [],
  };
  const renderLegacy = (doc: unknown, onSave = vi.fn(), alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})) => {
    render(<ObjectHubTab modelId={1} objects={(doc as any).objects}
      jsonContent={JSON.stringify(doc, null, 2)} onSave={onSave} canEdit modelKind="business" />);
    return { onSave, alertSpy };
  };

  afterEach(() => vi.restoreAllMocks());

  it('旧字段名对象保存草案不再报「对象不存在」，且只改标量、同步旧名、保留属性', async () => {
    const { onSave, alertSpy } = renderLegacy(legacyDoc);
    await userEvent.click(screen.getByRole('button', { name: '编辑对象' }));
    const nameInput = screen.getByDisplayValue('案例统计');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, '案例数量统计');
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));

    expect(alertSpy).not.toHaveBeenCalled();
    expect(onSave).toHaveBeenCalledTimes(1);
    const saved = JSON.parse(onSave.mock.calls[0][0]);
    const obj = saved.objects[0];
    expect(obj.object_key).toBe('case_stat');
    // canonical 与旧别名字段同步（不留 name=旧值 / display_name=新值 的矛盾两份）
    expect(obj.display_name).toBe('案例数量统计');
    expect(obj.name).toBe('案例数量统计');
    // 关键护栏：properties 原样保留（不整份覆盖）
    expect(obj.properties).toEqual([{ column: 'daily_case_stats.stat_date', name: '统计日期' }]);
    expect(obj.primary_table).toBe('daily_case_stats');
  });

  it('业务本体但对象未声明 route（存量直绑形状）给物理表表单，不得新增强制路由门槛', async () => {
    renderLegacy(legacyDoc);
    await userEvent.click(screen.getByRole('button', { name: '编辑对象' }));
    // 只看弹窗里的表名输入框（不能用 /绑定物理表/ 匹配：对象清单徽标「未绑定物理表」也含这三字）
    expect(screen.getByText(/表名，与「表 & 字段」/)).toBeTruthy();
    expect(screen.getByDisplayValue('daily_case_stats')).toBeTruthy();
    expect(screen.queryByText(/目标源本体/)).toBeNull();
  });

  it('对象已声明 route 时仍给路由编辑器（kind 真值源是模型行，不依赖 doc 里的 kind）', async () => {
    const routed = {
      objects: [{
        key: 'case_daily', display_name: '案例日报',
        route: { mode: 'source', source_ontology: '联耀源本体' },
      }],
    };
    renderLegacy(routed);
    await userEvent.click(screen.getByRole('button', { name: '编辑对象' }));
    expect(screen.getByText(/目标源本体/)).toBeTruthy();
    expect(screen.queryByText(/表名，与「表 & 字段」/)).toBeNull();
  });

  it('对象真不在当前正文时仍 fail-loud（提示可诊断，不静默假成功）', async () => {
    const onSave = vi.fn();
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    // 清单（objects prop，来自生效版）与正文（jsonContent，草案已删除该对象）不一致：
    // 必须把错说在嘴里并中止保存，不得静默写成一份丢对象的草案
    render(<ObjectHubTab modelId={1} objects={legacyDoc.objects as any}
      jsonContent={JSON.stringify({ objects: [] })} onSave={onSave} canEdit modelKind="business" />);
    await userEvent.click(screen.getByRole('button', { name: '编辑对象' }));
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    expect(alertSpy).toHaveBeenCalledTimes(1);
    expect(String(alertSpy.mock.calls[0][0])).toContain('case_stat');
    expect(String(alertSpy.mock.calls[0][0])).toContain('案例统计');
    expect(onSave).not.toHaveBeenCalled();
  });
});


/**
 * 换库后的列漂移必须看得见（Doris 换 database 的回归位）。
 *
 * 历史缺陷：属性登记的列名与实际表结构对不上时，页面只有服务端日志知道，
 * 属性行照旧显示一个不存在的列名，用户看到的全是"正常"，直到取数失败才暴露。
 * 现在裁决在服务端实时算（property_drift），页面按属性逐个标红 + 对象卡计数徽标。
 */
describe('ObjectHubTab — 属性列漂移显式暴露', () => {
  const hub = [{
    key: 'equipment_operation_log', display_name: '设备操作日志',
    primary_table: 'dwd_equipment_operation_log',
    properties: [
      { column: 'dwd_equipment_operation_log.serial_number', name: '设备序列号' },
      { column: 'dwd_equipment_operation_log.client_version', name: '客户端版本' },
    ],
  }] as any[];

  const bound = {
    object_key: 'equipment_operation_log', object_name: '设备操作日志',
    datasource: 'Doris', physical_table: 'dwd_equipment_operation_log',
    bind_kind: 'primary', column_count: 2, sync_state: 'drifted', product_ref: '',
    allowed_sites: [],
  };

  it('已绑定但列漂移：对象卡带「列待修」徽标，属性行标出「列不存在」', async () => {
    vi.mocked(ontologyApi.sites).mockResolvedValue({
      data: {
        kind: 'business', sites: [], selected: [], contract_objects: [bound],
        property_drift: {
          equipment_operation_log: [{
            property: '客户端版本', column: 'dwd_equipment_operation_log.client_version',
            table: 'dwd_equipment_operation_log', verdict: 'bad',
          }],
        },
      },
    } as any);
    render(<ObjectHubTab modelId={7} objects={hub} modelKind="business" />);
    expect(await screen.findByText('列待修1')).toBeTruthy();
    expect(screen.getByText('列不存在')).toBeTruthy();
    expect(screen.getByText(/1 个属性列不在该表实存列里/)).toBeTruthy();
    // 列不存在的引导必须落在页面真能做的动作上（改选实存列 / 取消绑定）
    expect(screen.getByText(/改选实存列/)).toBeTruthy();
    expect(screen.queryByText(/同步这张表的字段/)).toBeNull();
    // 徽标不能只说"bound"就完事：漂移状态要一起看得见
    expect(screen.getByText(/状态：形状漂移/)).toBeTruthy();
  });

  it('列元数据未同步时按「未核验」出声，不谎称列不存在', async () => {
    vi.mocked(ontologyApi.sites).mockResolvedValue({
      data: {
        kind: 'business', sites: [], selected: [], contract_objects: [bound],
        property_drift: {
          equipment_operation_log: [{
            property: '设备序列号', column: 'serial_number',
            table: 'dwd_equipment_operation_log', verdict: 'unverified',
          }],
        },
      },
    } as any);
    render(<ObjectHubTab modelId={7} objects={hub} modelKind="business" />);
    expect(await screen.findByText('未核验')).toBeTruthy();
    expect(screen.queryByText('列不存在')).toBeNull();
    // 全量 unverified 时不得叫「列待修」：改了列名也没依据，引导必须指向同步元数据
    expect(screen.queryByText(/^列待修/)).toBeNull();
    expect(screen.getByText('列待同步1')).toBeTruthy();
    expect(screen.getByText(/同步这张表的字段/)).toBeTruthy();
    expect(screen.queryByText(/改选实存列/)).toBeNull();
  });

  it('列不存在与未核验混在一起时分开计数、分开引导', async () => {
    vi.mocked(ontologyApi.sites).mockResolvedValue({
      data: {
        kind: 'business', sites: [], selected: [], contract_objects: [bound],
        property_drift: {
          equipment_operation_log: [
            { property: '客户端版本', column: 'client_version',
              table: 'dwd_equipment_operation_log', verdict: 'bad' },
            { property: '设备序列号', column: 'serial_number',
              table: 'dwd_equipment_operation_log', verdict: 'unverified' },
          ],
        },
      },
    } as any);
    render(<ObjectHubTab modelId={7} objects={hub} modelKind="business" />);
    expect(await screen.findByText('列待修1')).toBeTruthy();
    expect(screen.getByText('列待同步1')).toBeTruthy();
    expect(screen.getByText(/1 个属性列不在该表实存列里/)).toBeTruthy();
    expect(screen.getByText(/1 个属性列无法核验/)).toBeTruthy();
  });

  it('无绑定时引导到页面可完成的动作（编辑对象填表 → 发布级联登记）', async () => {
    render(<ObjectHubTab modelId={7} objects={hub} modelKind="business" />);
    const badge = await screen.findByText('未绑定物理表');
    expect(badge.getAttribute('title')).toContain('发布');
    expect(screen.getByText(/该对象尚无生效的部署绑定行/)).toBeTruthy();
  });
});

/**
 * 「列待修」必须能在页面修掉（Doris 换 database 后的第二段断链）。
 *
 * 历史：服务端亮徽标并告诉用户“请修正属性”，但草案 JSON 视图只读、属性行不可编——
 * 告警指向一个不存在的操作，用户只能改库或跑脚本。现在「编辑对象」里直接给属性列映射
 * 选择框（选项=该表实存列 + 列业务名），保存写回 doc 的 properties[].column。
 */
describe('ObjectHubTab — 编辑对象里可改属性物理列', () => {
  const legacy = {
    objects: [{
      object_key: 'equipment_operation_log', name: '设备操作日志',
      primary_table: 'dwd_equipment_operation_log',
      properties: [
        { column: 'dwd_equipment_operation_log.serial_number', name: '设备序列号' },
        { column: 'dwd_equipment_operation_log.client_version', name: '客户端版本' },
      ],
    }],
  };
  const boundRow = {
    object_key: 'equipment_operation_log', object_name: '设备操作日志',
    datasource: 'Doris', physical_table: 'dwd_equipment_operation_log',
    bind_kind: 'primary', column_count: 2, sync_state: 'drifted', product_ref: '',
    allowed_sites: [],
  };
  const driftPayload = {
    equipment_operation_log: [{
      property: '客户端版本', column: 'dwd_equipment_operation_log.client_version',
      table: 'dwd_equipment_operation_log', verdict: 'bad',
    }],
  };
  const realColumns = [
    { name: 'serial_number', type: 'varchar', comment: '设备序列号' },
    { name: 'c_version', type: 'varchar', comment: '客户端版本号' },
  ];

  const openEdit = async (columns: any[] = realColumns, note = '') => {
    const onSave = vi.fn();
    vi.mocked(ontologyApi.sites).mockResolvedValue({
      data: { kind: 'business', sites: [], selected: [],
        contract_objects: [boundRow], property_drift: driftPayload },
    } as any);
    vi.mocked(ontologyApi.objectColumns).mockResolvedValue({
      data: { object_key: 'equipment_operation_log', table: 'dwd_equipment_operation_log',
        bind_kind: 'primary', dataset_ref: '', columns, note },
    } as any);
    render(<ObjectHubTab modelId={7} objects={legacy.objects as any}
      jsonContent={JSON.stringify(legacy, null, 2)} onSave={onSave} canEdit modelKind="business" />);
    await userEvent.click(await screen.findByRole('button', { name: '编辑对象' }));
    expect(await screen.findByText('属性列映射（2）')).toBeTruthy();
    return { onSave };
  };

  it('漂移行标红，选实存列后保存只改那一行（其余属性写法不动）', async () => {
    const { onSave } = await openEdit();
    expect(await screen.findByText(/1 个属性的列名不在该表实存列里/)).toBeTruthy();
    const trigger = await screen.findByRole('combobox', { name: '客户端版本 的物理列' }, { timeout: 3000 });
    await userEvent.click(trigger);
    // 选项显示业务名（列注释）而不是只堆裸列名
    await userEvent.click(await screen.findByRole('option', { name: /客户端版本号/ }));
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const props = JSON.parse(onSave.mock.calls[0][0]).objects[0].properties;
    expect(props[1].column).toBe('c_version');
    expect(props[0].column).toBe('dwd_equipment_operation_log.serial_number');
    // 属性其余字段（name）不得丢
    expect(props[1].name).toBe('客户端版本');
  });

  it('当前列不在候选里时不假装选中：给「不修改」哨兵项，选它等于不写回', async () => {
    const { onSave } = await openEdit();
    const trigger = await screen.findByRole('combobox', { name: '客户端版本 的物理列' }, { timeout: 3000 });
    await userEvent.click(trigger);
    const keep = await screen.findByRole('option', { name: /不修改（当前 dwd_equipment_operation_log\.client_version/ });
    await userEvent.click(keep);
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const props = JSON.parse(onSave.mock.calls[0][0]).objects[0].properties;
    expect(props[1].column).toBe('dwd_equipment_operation_log.client_version');
  });

  it('该表确实没有这个概念时可「不绑定物理列」：写回空值而不是留着不存在的列名', async () => {
    // 换库后一类常见真实情况：概念在新库里没了对应列（如订单表的金额列被拆走）。
    // 留着 client_version 这种不存在的列名会被 planner 编译进 SQL（取数报错），
    // 所以页面必须给“取消绑定”这个出口，而不是只让人在“改选”与“不修改”之间二选一。
    const { onSave } = await openEdit();
    const trigger = await screen.findByRole('combobox', { name: '客户端版本 的物理列' }, { timeout: 3000 });
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByRole('option', { name: /不绑定物理列（该表没有这个概念）/ }));
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const props = JSON.parse(onSave.mock.calls[0][0]).objects[0].properties;
    expect(props[1].column).toBe('');
    expect(props[1].name).toBe('客户端版本');
  });

  it('未漂移的行不提供「不绑定物理列」（避免健康映射被误清空）', async () => {
    await openEdit();
    const okTrigger = await screen.findByRole('combobox', { name: '设备序列号 的物理列' }, { timeout: 3000 });
    await userEvent.click(okTrigger);
    expect(await screen.findByRole('option', { name: /serial_number/ })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /不绑定物理列/ })).toBeNull();
    await userEvent.keyboard('{Escape}');
  });

  it('列候选接口失败必须出声，并回退为手填（不静默当作“没列可改”）', async () => {
    const onSave = vi.fn();
    vi.mocked(ontologyApi.sites).mockResolvedValue({
      data: { kind: 'business', sites: [], selected: [],
        contract_objects: [boundRow], property_drift: driftPayload },
    } as any);
    vi.mocked(ontologyApi.objectColumns).mockRejectedValue({
      response: { data: { detail: '服务异常' } },
    });
    render(<ObjectHubTab modelId={7} objects={legacy.objects as any}
      jsonContent={JSON.stringify(legacy, null, 2)} onSave={onSave} canEdit modelKind="business" />);
    await userEvent.click(await screen.findByRole('button', { name: '编辑对象' }));
    expect(await screen.findByText(/列候选读取失败（服务异常）/)).toBeTruthy();
    expect(screen.queryByRole('combobox', { name: '客户端版本 的物理列' })).toBeNull();
    const input = screen.getByLabelText('客户端版本 的物理列');
    await userEvent.clear(input);
    await userEvent.type(input, 'c_version');
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    const props = JSON.parse(onSave.mock.calls[0][0]).objects[0].properties;
    expect(props[1].column).toBe('c_version');
  });

  it('元数据未同步时给出原因而不是空候选', async () => {
    await openEdit([], '表 dwd_equipment_operation_log 没有已同步的列元数据');
    expect(await screen.findByText(/没有已同步的列元数据/)).toBeTruthy();
  });

  it('路由对象不渲染属性列映射（物理列在源本体对象上维护）', async () => {
    const routed = {
      objects: [{
        key: 'case_daily', display_name: '案例日报',
        route: { mode: 'source', source_ontology: '联耀源本体' },
        properties: [{ column: 'x.y', name: '某属性' }],
      }],
    };
    vi.mocked(ontologyApi.sites).mockResolvedValue({
      data: { kind: 'business', sites: [], selected: [], contract_objects: [] },
    } as any);
    render(<ObjectHubTab modelId={7} objects={routed.objects as any}
      jsonContent={JSON.stringify(routed, null, 2)} onSave={vi.fn()} canEdit modelKind="business" />);
    await userEvent.click(await screen.findByRole('button', { name: '编辑对象' }));
    expect(screen.queryByText(/属性列映射/)).toBeNull();
    expect(vi.mocked(ontologyApi.objectColumns)).not.toHaveBeenCalled();
  });
});
