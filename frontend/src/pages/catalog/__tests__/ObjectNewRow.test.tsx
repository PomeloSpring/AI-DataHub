import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ontologyApi } from '@/api/ontology';
import { ObjectNewRow } from '../ModelAssetTabs';

vi.mock('@/api/ontology', () => ({
  ontologyApi: {
    list: vi.fn(), get: vi.fn(), sites: vi.fn(),
    saveDraft: vi.fn(), getDraft: vi.fn(), getDraftContent: vi.fn(),
  },
}));

/**
 * 「新建对象」回归：**就地内联行**（设计稿 `docs/demo/ontology-modeling-redesign.html`
 * 的 `.inline-new`），不再弹居中对话框（用户 2026-11-19：就地新建，不是已经设计了新的交互页面了吗）。
 *
 * 形状描述仍然**可选**：业务本体可用「不绑表（先建概念）/ 绑主表 / 路由到源本体对象」三选，
 * 服务端 v0.5 B6b 起物理绑定唯一在部署绑定层（`adh_ontology_bindings`），发布级联才登记绑定行；
 * 存量 18/18 直绑对象按「绑主表」也能建，不被强制选源本体卡住。
 */
afterEach(cleanup);
beforeEach(() => {
  vi.mocked(ontologyApi.list).mockReset();
  vi.mocked(ontologyApi.list).mockResolvedValue({
    data: { items: [{ id: 1, name: '联耀源本体', kind: 'business', status: 'active' }] },
  } as any);
});

describe('ObjectNewRow — 就地新建对象（不弹对话框）', () => {
  const bizDoc = {
    kind: 'business',
    objects: [{ object_key: 'case_stat', name: '案例统计', primary_table: 'daily_case_stats' }],
  };
  // 行内 textbox 顺序：对象 key → 显示名 → 描述（选「绑定主表」后才有主表输入）
  const keyInput = () => screen.getAllByRole('textbox')[0];
  const pickShape = async (label: string) => {
    await userEvent.click(screen.getByRole('combobox', { name: '对象形状' }));
    await userEvent.click(await screen.findByRole('option', { name: label }));
  };

  it('整块表单直接渲染在页面上（无 dialog），业务本体只填物理表也能创建', async () => {
    const onSave = vi.fn();
    const onDone = vi.fn();
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    render(<ObjectNewRow jsonContent={JSON.stringify(bizDoc)} onSave={onSave}
      onCancel={() => {}} onDone={onDone} modelKind="business" />);

    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.type(keyInput(), 'region_stat');
    await userEvent.type(screen.getByPlaceholderText('如：订单明细'), '地区统计');
    await pickShape('绑定主表');
    await userEvent.type(screen.getByPlaceholderText('如：t_order_item'), 'dwd_region_stats');
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));

    expect(alertSpy).not.toHaveBeenCalled();
    expect(onSave).toHaveBeenCalledTimes(1);
    const doc = JSON.parse(onSave.mock.calls[0][0]);
    const obj = doc.objects[doc.objects.length - 1];
    expect(obj).toMatchObject({ key: 'region_stat', display_name: '地区统计', primary_table: 'dwd_region_stats' });
    expect(obj.route).toBeUndefined();
    expect(onDone).toHaveBeenCalledWith('region_stat');
    alertSpy.mockRestore();
  });

  it('两项都不填也合法（物理绑定属部署绑定层，不得在表单里硬拦）', async () => {
    const onSave = vi.fn();
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    render(<ObjectNewRow jsonContent={JSON.stringify(bizDoc)} onSave={onSave}
      onCancel={() => {}} modelKind="business" />);
    await userEvent.type(keyInput(), 'ghost_obj');
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
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
    render(<ObjectNewRow jsonContent={JSON.stringify(bizDoc)} onSave={onSave}
      onCancel={() => {}} modelKind="business" />);
    await userEvent.type(keyInput(), 'case_stat');
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    expect(String(alertSpy.mock.calls[0]?.[0] || '')).toContain('对象 key 已存在');
    expect(onSave).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it('非业务本体（系统能力本体）没有「路由到源本体对象」这个形状', async () => {
    const onSave = vi.fn();
    const sysDoc = { kind: 'system', objects: [{ key: 'role', display_name: '角色' }] };
    render(<ObjectNewRow jsonContent={JSON.stringify(sysDoc)} onSave={onSave}
      onCancel={() => {}} modelKind="system" />);
    await userEvent.click(screen.getByRole('combobox', { name: '对象形状' }));
    expect(await screen.findByRole('option', { name: '绑定主表' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: '路由到源本体对象' })).toBeNull();
    await userEvent.keyboard('{Escape}');
    await userEvent.type(keyInput(), 'menu');
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});