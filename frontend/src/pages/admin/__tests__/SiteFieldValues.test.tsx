/**
 * 站点维护页的「字段取值映射」。
 *
 * 为什么这一页必须有：同一个站点在不同业务字段上写法不同（实测：新加坡站点在业务表
 * `site` 列存 `global`，在日志侧 `region` 列存 `en`）。看板下发站点代码 `int` 到 site 列
 * 实测 0 行 —— 翻译发生在服务端唯一翻译点，但**映射本身是配置**，必须给管理员一个
 * 看得见、改得了的入口（写死在代码里就没人知道现网为什么这么筛）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { listSites, removeSite, saveSite } from '@/api/sites';
import SiteManagement from '../SiteManagement';

vi.mock('@/api/sites', () => ({ listSites: vi.fn(), saveSite: vi.fn(), removeSite: vi.fn() }));
vi.mock('@/api/client', () => ({ default: { get: vi.fn() } }));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import client from '@/api/client';
import { toast } from '@/lib/toast';

const SITES = [
  { site_code: 'global', display_name: '全部站点', is_aggregate: 1, sort_order: 0, is_active: 1, field_values: {} },
  { site_code: 'cn', display_name: '中国站点', is_aggregate: 0, sort_order: 10, is_active: 1,
    field_values: { site: 'cn', region: 'cn' } },
  { site_code: 'int', display_name: '新加坡站点', is_aggregate: 0, sort_order: 20, is_active: 1,
    field_values: { site: 'global', region: 'en' } },
];

beforeEach(() => {
  vi.clearAllMocks();
  (listSites as any).mockResolvedValue(SITES.map((s) => ({ ...s })));
  (client.get as any).mockResolvedValue({ data: [] });
  (saveSite as any).mockResolvedValue({});
});
afterEach(() => vi.restoreAllMocks());

const card = (name: string) => within(screen.getByText(name).closest('div[class*="border"]') as HTMLElement);

describe('站点字段取值映射', () => {
  it('卡片上直接显示每个字段的取值（不用点进编辑才知道）', async () => {
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('新加坡站点')).toBeInTheDocument());
    expect(card('新加坡站点').getByText(/site=global/)).toBeInTheDocument();
    expect(card('新加坡站点').getByText(/region=en/)).toBeInTheDocument();
  });

  it('没配映射的站点说明按站点代码原样下发，不留"看起来配过了"的空白', async () => {
    (listSites as any).mockResolvedValue([
      { site_code: 'eu', display_name: '欧洲站点', is_aggregate: 0, sort_order: 30, is_active: 1 }]);
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('欧洲站点')).toBeInTheDocument());
    expect(card('欧洲站点').getByText(/未配置（按站点代码原样下发）/)).toBeInTheDocument();
  });

  it('聚合态不参与映射（选了就是不给值，不编一个具体取值去筛）', async () => {
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('全部站点')).toBeInTheDocument());
    expect(card('全部站点').getByText(/聚合态不参与/)).toBeInTheDocument();
  });

  it('编辑时可增删字段并保存为 field_values', async () => {
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('新加坡站点')).toBeInTheDocument());
    fireEvent.click(card('新加坡站点').getByTitle('编辑'));
    const dialog = await screen.findByRole('dialog');
    // 现有两份映射回显成两行
    expect(within(dialog).getByDisplayValue('site')).toBeInTheDocument();
    expect(within(dialog).getByDisplayValue('global')).toBeInTheDocument();
    // 改成 region 列不再写 en，而是写 sg（模拟业务改口径）
    fireEvent.change(within(dialog).getByDisplayValue('region'), { target: { value: 'zone' } });
    fireEvent.change(within(dialog).getAllByDisplayValue('en')[0], { target: { value: 'sg' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '更新' }));
    await waitFor(() => expect(saveSite).toHaveBeenCalled());
    expect((saveSite as any).mock.calls[0][0].field_values)
      .toEqual({ site: 'global', zone: 'sg' });
  });

  it('删除一行即撤销该字段的映射', async () => {
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('中国站点')).toBeInTheDocument());
    fireEvent.click(card('中国站点').getByTitle('编辑'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getAllByTitle('删除该字段')[0]);
    fireEvent.click(within(dialog).getByRole('button', { name: '更新' }));
    await waitFor(() => expect(saveSite).toHaveBeenCalled());
    expect((saveSite as any).mock.calls[0][0].field_values).toEqual({ region: 'cn' });
  });

  it('取值留空不许保存（空串会把"不筛"变成"筛空值"）', async () => {
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('新加坡站点')).toBeInTheDocument());
    fireEvent.click(card('新加坡站点').getByTitle('编辑'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByDisplayValue('global'), { target: { value: '' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '更新' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/不能为空/)));
    expect(saveSite).not.toHaveBeenCalled();
  });

  it('同一字段重复登记要报错，不静默覆盖', async () => {
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('新加坡站点')).toBeInTheDocument());
    fireEvent.click(card('新加坡站点').getByTitle('编辑'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /添加字段/ }));
    const fields = within(dialog).getAllByPlaceholderText(/字段名/);
    fireEvent.change(fields[fields.length - 1], { target: { value: 'SITE' } });
    fireEvent.change(within(dialog).getAllByPlaceholderText(/取值/)[fields.length - 1],
      { target: { value: 'x' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '更新' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/重复登记/)));
    expect(saveSite).not.toHaveBeenCalled();
  });

  it('字段名大小写归一（正文里是 Site 也应命中映射）', async () => {
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('新加坡站点')).toBeInTheDocument());
    fireEvent.click(card('新加坡站点').getByTitle('编辑'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /添加字段/ }));
    const fields = within(dialog).getAllByPlaceholderText(/字段名/);
    fireEvent.change(fields[fields.length - 1], { target: { value: ' Area ' } });
    fireEvent.change(within(dialog).getAllByPlaceholderText(/取值/)[fields.length - 1],
      { target: { value: 'sg' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '更新' }));
    await waitFor(() => expect(saveSite).toHaveBeenCalled());
    expect((saveSite as any).mock.calls[0][0].field_values.area).toBe('sg');
  });

  it('删除站点走后端裁决，被引用时原样转述占用清单', async () => {
    (removeSite as any).mockRejectedValue({ response: { data: { detail: '站点仍被数据源引用：prd-en' } } });
    render(<SiteManagement />);
    await waitFor(() => expect(screen.getByText('新加坡站点')).toBeInTheDocument());
    fireEvent.click(card('新加坡站点').getByTitle('删除'));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/仍被数据源引用/)));
  });
});
