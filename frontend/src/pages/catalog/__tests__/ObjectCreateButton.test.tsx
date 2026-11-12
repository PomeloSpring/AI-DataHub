import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ontologyApi } from '@/api/ontology';
import { ObjectCreateButton } from '../ModelAssetTabs';

vi.mock('@/api/ontology', () => ({
  ontologyApi: {
    list: vi.fn(), get: vi.fn(), sites: vi.fn(),
    saveDraft: vi.fn(), getDraft: vi.fn(), getDraftContent: vi.fn(),
  },
}));

/**
 * 「新建对象」回归：业务本体对象的形状描述是**可选**的 —— 路由（route 指向源本体）
 * 和/或 primary_table。服务端 v0.5 B6b 起物理绑定唯一在部署绑定层，新对象可先建概念
 * 再在「编辑对象」里补物理表（发布级联负责登记部署绑定行）；旧写法硬要求选源本体，与服务端已退役的 route 校验不一致，
 * 存量 18/18 直绑对象压根没法照形状新建。
 */
afterEach(cleanup);
beforeEach(() => {
  vi.mocked(ontologyApi.list).mockReset();
  vi.mocked(ontologyApi.list).mockResolvedValue({
    data: { items: [{ id: 1, name: '联耀源本体', kind: 'business', status: 'active' }] },
  } as any);
});

describe('ObjectCreateButton — 业务本体按实际形状放行', () => {
  const bizDoc = {
    kind: 'business',
    objects: [{ object_key: 'case_stat', name: '案例统计', primary_table: 'daily_case_stats' }],
  };
  // key 输入框无 placeholder（Field 的 Label 未 htmlFor 关联），按 DOM 顺序取第一个 textbox
  const keyInput = () => screen.getAllByRole('textbox')[0];

  it('业务本体只填物理表也能创建（不再强制选源本体）', async () => {
    const onSave = vi.fn();
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    render(<ObjectCreateButton jsonContent={JSON.stringify(bizDoc)} onSave={onSave} modelKind="business" />);
    await userEvent.click(screen.getByRole('button', { name: '新建对象' }));
    await userEvent.type(keyInput(), 'region_stat');
    await userEvent.type(screen.getByPlaceholderText('如：订单明细'), '地区统计');
    await userEvent.type(screen.getByPlaceholderText('如：t_order_item'), 'dwd_region_stats');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));

    expect(alertSpy).not.toHaveBeenCalled();
    expect(onSave).toHaveBeenCalledTimes(1);
    const doc = JSON.parse(onSave.mock.calls[0][0]);
    const obj = doc.objects[doc.objects.length - 1];
    expect(obj).toMatchObject({ key: 'region_stat', display_name: '地区统计', primary_table: 'dwd_region_stats' });
    expect(obj.route).toBeUndefined();
    alertSpy.mockRestore();
  });

  it('两项都不填也合法（物理绑定属部署绑定层，不得在表单里硬拦）', async () => {
    const onSave = vi.fn();
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    render(<ObjectCreateButton jsonContent={JSON.stringify(bizDoc)} onSave={onSave} modelKind="business" />);
    await userEvent.click(screen.getByRole('button', { name: '新建对象' }));
    await userEvent.type(keyInput(), 'ghost_obj');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(alertSpy).not.toHaveBeenCalled();
    expect(onSave).toHaveBeenCalledTimes(1);
    const doc = JSON.parse(onSave.mock.calls[0][0]);
    const obj = doc.objects[doc.objects.length - 1];
    expect(obj.key).toBe('ghost_obj');
    expect(obj.route).toBeUndefined();
    expect(obj.primary_table).toBeUndefined();
    alertSpy.mockRestore();
  });

  it('查重按归一后的对象身份：旧字段名 object_key 的同名对象不会被当成不存在而重复写入', async () => {
    const onSave = vi.fn();
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    render(<ObjectCreateButton jsonContent={JSON.stringify(bizDoc)} onSave={onSave} modelKind="business" />);
    await userEvent.click(screen.getByRole('button', { name: '新建对象' }));
    await userEvent.type(keyInput(), 'case_stat');
    await userEvent.type(screen.getByPlaceholderText('如：t_order_item'), 'daily_case_stats');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(String(alertSpy.mock.calls[0]?.[0] || '')).toContain('对象 key 已存在');
    expect(onSave).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it('非业务本体（系统能力本体）仍按物理表表单，行为不变', async () => {
    const onSave = vi.fn();
    const sysDoc = { kind: 'system', objects: [{ key: 'role', display_name: '角色' }] };
    render(<ObjectCreateButton jsonContent={JSON.stringify(sysDoc)} onSave={onSave} modelKind="system" />);
    await userEvent.click(screen.getByRole('button', { name: '新建对象' }));
    expect(screen.queryByText(/目标源本体/)).toBeNull();
    await userEvent.type(keyInput(), 'menu');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
