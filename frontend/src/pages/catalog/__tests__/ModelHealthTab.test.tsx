import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ontologyApi } from '@/api/ontology';
import ModelHealthTab from '../ModelHealthTab';

vi.mock('@/api/ontology', () => ({ ontologyApi: { sites: vi.fn() } }));

function mockSites(payload: any) {
  vi.mocked(ontologyApi.sites).mockResolvedValue({ data: { kind: 'business', sites: [], selected: [], ...payload } } as any);
}

beforeEach(() => {
  vi.mocked(ontologyApi.sites).mockReset();
  mockSites({ contract_objects: [] });
});
afterEach(() => cleanup());

/**
 * 「建模体检」= 本模型对象级缺口清单（2026-11-20 与「建模待办」拆分后的独立页签）。
 *
 * 这里锁三件事：
 * 1. 判定口径与对象画布同源（部署绑定层的 contract_objects / property_drift），不另造一套；
 * 2. 列不存在（要改定义）与列元数据未同步（要先同步）分开，草案新增对象未绑表不算硬阻断；
 * 3. 绑定层读不到时只说“无法判定 + 重试”，绝不把读不到当成“全都未绑表”（假告警）。
 */
describe('ModelHealthTab — 建模体检', () => {
  it('未绑表对象是发布前必须处理，草案新增对象单列为待确认', async () => {
    mockSites({
      contract_objects: [{ object_key: 'ordered', object_name: '订单', datasource: 'ds', physical_table: 'dwd_order' }],
    });
    const onLocate = vi.fn();
    render(
      <ModelHealthTab
        modelId={1}
        objects={[
          { key: 'ordered', display_name: '订单', primary_table: 'dwd_order' },
          { key: 'member', display_name: '会员' },
          { key: 'goods', display_name: '商品' },
        ] as any}
        draftStates={{ goods: { key: 'goods', state: 'added' } } as any}
        onLocate={onLocate}
      />,
    );

    expect(await screen.findByText('未绑定物理表的对象 1 个')).toBeTruthy();
    // 草案新增对象的未绑表是预期（发布级联登记），不得混进硬阻断组
    expect(screen.getByText('草案新增对象 1 个：发布时由部署级联登记物理表')).toBeTruthy();
    const hardGroup = screen.getByText('未绑定物理表的对象 1 个').closest('div.rounded-lg') as HTMLElement;
    expect(within(hardGroup).getByText('会员')).toBeTruthy();
    expect(within(hardGroup).queryByText('商品')).toBeNull();
    // 硬阻断组的定位只能指向真缺口（会员），不会把人引到「等发布就好」的草案新增对象

    await userEvent.click(within(hardGroup).getByRole('button', { name: '定位' }));
    expect(onLocate).toHaveBeenCalledWith('member', 'overview');
  });

  it('系统本体内置对象不列为未绑表缺口（含无物理表的纯概念对象）', async () => {
    mockSites({
      kind: 'system',
      contract_objects: [
        { object_key: 'role', object_name: '角色', physical_table: 'adh_roles', bind_kind: 'platform', binding_source: 'platform' },
        { object_key: 'skill', object_name: '技能', physical_table: '', bind_kind: 'platform', binding_source: 'platform' },
      ],
    });
    render(
      <ModelHealthTab
        modelId={9}
        modelKind="system"
        objects={[{ key: 'role', display_name: '角色' }, { key: 'skill', display_name: '技能' }] as any}
      />,
    );
    // 系统本体的契约行标了 binding_source=platform：不再套业务绑定口径（skill 无表也不报）
    expect(await screen.findByText('本模型暂无建模缺口')).toBeTruthy();
    expect(screen.queryByText(/未绑定物理表的对象/)).toBeNull();
  });

  it('列漂移按 verdict 分流：列不存在=阻断（定位到属性页签），未同步=待确认', async () => {
    mockSites({
      contract_objects: [{ object_key: 'ordered', object_name: '订单', datasource: 'ds', physical_table: 'dwd_order' }],
      property_drift: {
        ordered: [
          { property: '金额', column: 'amt', table: 'dwd_order', verdict: 'bad' },
          { property: '地区', column: 'region', table: 'dwd_order', verdict: 'unverified' },
        ],
      },
    });
    const onLocate = vi.fn();
    render(
      <ModelHealthTab modelId={1} objects={[{ key: 'ordered', display_name: '订单', primary_table: 'dwd_order' }] as any}
        onLocate={onLocate} />,
    );

    expect(await screen.findByText('列漂移 1 处')).toBeTruthy();
    expect(screen.getByText('订单 · 金额')).toBeTruthy();
    // 未同步不是「定义要改」，走各自的组与文案
    expect(screen.getByText('列元数据未同步 1 处')).toBeTruthy();
    expect(screen.getByText('订单 · 地区')).toBeTruthy();

    const driftGroup = screen.getByText('列漂移 1 处').closest('div.rounded-lg') as HTMLElement;
    await userEvent.click(within(driftGroup).getByRole('button', { name: '定位' }));
    // 定位要落到「属性」页签：改列映射就在那里
    expect(onLocate).toHaveBeenCalledWith('ordered', 'props');
  });

  it('孤儿关系显式列出并定位到关系页签（激活校验会拒绝）', async () => {
    mockSites({
      contract_objects: [
        { object_key: 'case_file', object_name: '案例', datasource: 'ds', physical_table: 'pc_case' },
        { object_key: 'region', object_name: '地区', datasource: 'ds', physical_table: 'dim_region' },
      ],
    });
    const onLocate = vi.fn();
    render(
      <ModelHealthTab
        modelId={1}
        objects={[
          { key: 'case_file', display_name: '案例', primary_table: 'pc_case', links: [{ type: 'belongs_to', target: 'customer' }] },
          { key: 'region', display_name: '地区', primary_table: 'dim_region' },
        ] as any}
        onLocate={onLocate}
      />,
    );

    expect(await screen.findByText('孤儿关系 1 条')).toBeTruthy();
    expect(screen.getByText('案例 → customer')).toBeTruthy();
    expect(screen.getByText('关系指向的对象不在本模型，激活校验会拒绝')).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: '定位' }));
    expect(onLocate).toHaveBeenCalledWith('case_file', 'links');
  });

  it('绑定层读失败时只报无法判定并可重试，绝不假报「未绑表」', async () => {
    vi.mocked(ontologyApi.sites).mockRejectedValue(new Error('gateway timeout'));
    render(<ModelHealthTab modelId={1} objects={[{ key: 'ordered', display_name: '订单' }] as any} />);

    expect(await screen.findByText(/绑定层读取失败：gateway timeout/)).toBeTruthy();
    expect(screen.queryByText(/未绑定物理表的对象/)).toBeNull();
    expect(screen.getByRole('button', { name: /重试/ })).toBeTruthy();
  });

  it('无缺口时给空态，并显式说明字典悬空检查尚未接入', async () => {
    mockSites({ contract_objects: [{ object_key: 'ordered', object_name: '订单', datasource: 'ds', physical_table: 'dwd_order' }] });
    const onSummary = vi.fn();
    render(
      <ModelHealthTab modelId={1} objects={[{ key: 'ordered', display_name: '订单', primary_table: 'dwd_order' }] as any}
        onSummary={onSummary} />,
    );

    expect(await screen.findByText('本模型暂无建模缺口')).toBeTruthy();
    expect(screen.getByText(/字典绑定悬空检查尚未接入/)).toBeTruthy();
    expect(onSummary).toHaveBeenLastCalledWith(0);
  });

  it('草案视图显式声明体检读的是已生效版本的绑定', async () => {
    mockSites({ contract_objects: [] });
    render(<ModelHealthTab modelId={1} objects={[]} viewMode="draft" />);
    expect(await screen.findByText(/体检按已生效版本的部署绑定判定/)).toBeTruthy();
  });

  it('对象缺 key（doc 无法定位）按阻断列出，但不给定位按钮', async () => {
    render(
      <ModelHealthTab
        modelId={1}
        objects={[]}
        issues={[{ index: 0, label: '第 1 项', reason: 'no_identity', hint: '缺少 key' }]}
      />,
    );
    expect(await screen.findByText('对象定义缺少 key 1 个')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '定位' })).toBeNull();
    expect(screen.getByText(/需在 JSON 中补齐/)).toBeTruthy();
  });
});
