/**
 * 看板管理页：标签过滤 + 看板列表/看板组合 Tab 拆分。
 *
 * 锁定：
 *  - 看板越来越多，除名称过滤外要能按「标签」过滤：筛选栏里一个独立标签下拉框，选项来自全部看板 tags 的去重并集；
 *  - 标签编辑只影响管理页清单（无裸 id、不参与任何取数/权限口径）；
 *  - 看板组合不再与看板列表挤在同一屏：默认「看板列表」Tab 显示清单，切到「看板组合」才显示组合管理。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import DashboardPage from '@/pages/Dashboard';
import { useDashboardStore } from '@/stores/dashboardStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => ({ items: [], error: '', refresh: vi.fn() }) }));
vi.mock('@/components/DashboardCanvas', () => ({ DashboardThumbnail: () => null }));
vi.mock('@/components/DashboardGroupsAdmin', () => ({
  default: () => <div>组合管理占位<button type="button">新建组合</button></div>,
}));

const board = (id: number, name: string, tags: string[] = []): any => ({
  id, name, status: 'enabled', is_default: false, editable: true, owner_id: 1,
  description: '', layout: [], filters: {}, params: [], page_params: [], tags, charts: [],
  carousel_interval: 0, sort_order: id, created_at: '', updated_at: '',
});

beforeEach(() => {
  vi.clearAllMocks();
  useDashboardStore.getState().reset();
  const boards = [board(1, '经营日报', ['经营', '日报']), board(2, '经营周报', ['经营']), board(3, '无标签看板')];
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url.startsWith('/dashboard/') && !url.includes('groups')) return { data: boards } as any;
    return { data: [] } as any;
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function mount() { return render(<MemoryRouter><DashboardPage /></MemoryRouter>); }

describe('看板管理页标签过滤 + Tab 拆分', () => {
  it('默认看板列表 Tab：标签筛选下拉框选项来自各看板 tags 去重并集（业务名，无裸 id）', async () => {
    mount();
    await screen.findByText('经营日报');
    const sel = await screen.findByRole('combobox', { name: '标签筛选' });
    expect(within(sel).getByRole('option', { name: '全部标签' })).toBeTruthy();
    expect(within(sel).getByRole('option', { name: '经营' })).toBeTruthy();
    expect(within(sel).getByRole('option', { name: '日报' })).toBeTruthy();
    // 去重：两条看板都带「经营」，选项只一个
    expect(within(sel).getAllByRole('option', { name: '经营' })).toHaveLength(1);
  });

  it('选「日报」只剩含该标签的看板', async () => {
    mount();
    await screen.findByText('经营日报');
    fireEvent.change(screen.getByRole('combobox', { name: '标签筛选' }), { target: { value: '日报' } });
    await waitFor(() => expect(screen.getByText('经营日报')).toBeTruthy());
    expect(screen.queryByText('经营周报')).toBeNull();
    expect(screen.queryByText('无标签看板')).toBeNull();
    expect(screen.getByText(/1 个看板/)).toBeTruthy();
  });

  it('选「经营」命中两张含该标签的看板；选回全部后都回来', async () => {
    mount();
    await screen.findByText('经营日报');
    const sel = () => screen.getByRole('combobox', { name: '标签筛选' });
    fireEvent.change(sel(), { target: { value: '经营' } });
    await waitFor(() => expect(screen.queryByText('无标签看板')).toBeNull());
    expect(screen.getByText('经营日报')).toBeTruthy();
    expect(screen.getByText('经营周报')).toBeTruthy();
    fireEvent.change(sel(), { target: { value: 'all' } });
    await waitFor(() => expect(screen.getByText('无标签看板')).toBeTruthy());
  });

  it('组合与列表分开：默认列表 Tab 不渲染组合管理，切到「看板组合」才出现', async () => {
    mount();
    await screen.findByText('经营日报');
    expect(screen.queryByText('组合管理占位')).toBeNull();
    // Radix Tabs 需 userEvent 才会激活（fireEvent.click 不触发 onValueChange）
    await userEvent.click(screen.getByRole('tab', { name: '看板组合' }));
    await waitFor(() => expect(screen.getByText('组合管理占位')).toBeTruthy());
    expect(screen.queryByText('经营日报')).toBeNull();
  });

  it('后端旧进程回 tags 为 JSON 字符串时也不崩（入 store 归一为数组）', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) =>
      url.startsWith('/dashboard/') && !url.includes('groups')
        ? ({ data: [
            { ...board(1, '字符串标签看板'), tags: '["\u7ecf\u8425","\u65e5\u62a5"]' },  // JSON 文本列原样回字符串
            { ...board(2, '空串标签'), tags: '[]' },
            { ...board(3, '无 tags 字段') },                                            // tags 缺失
          ] } as any)
        : ({ data: [] } as any));
    mount();
    await screen.findByText('字符串标签看板');
    // 归一后标签下拉正常生成选项（不再是 (d.tags||[]).forEach 报 not a function）
    const sel = await screen.findByRole('combobox', { name: '标签筛选' });
    expect(within(sel).getByRole('option', { name: '经营' })).toBeTruthy();
    expect(within(sel).getByRole('option', { name: '日报' })).toBeTruthy();
    // 卡内标签也渲染业务名
    const card = screen.getByText('字符串标签看板').closest('article')!;
    expect(within(card).getByText('#经营')).toBeTruthy();
  });

  it('设置弹窗可从「已有标签」点选加入，无需重复输入', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) =>
      url.startsWith('/dashboard/') && !url.includes('groups')
        ? ({ data: [board(1, '经营日报', ['经营']), board(2, '待打标')] } as any)
        : ({ data: [] } as any));
    mount();
    await screen.findByText('待打标');
    await userEvent.click(screen.getByRole('button', { name: '待打标 更多操作' }));
    await userEvent.click(screen.getByRole('menuitem', { name: '状态与参数设置' }));
    // 弹窗里「已有标签」列出别的看板用过的 #经营，未选中
    const pick = await screen.findByRole('button', { name: '#经营' });
    expect(pick.getAttribute('aria-pressed')).toBe('false');
    await userEvent.click(pick);
    // 点选后变选中态，并出现在当前看板可移除标签区
    expect((await screen.findByRole('button', { name: '#经营' })).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '移除标签 经营' })).toBeTruthy();
  });

  it('卡片上展示看板标签（同类靠标签区分），超出 4 个折叠 +N', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) =>
      url.startsWith('/dashboard/') && !url.includes('groups')
        ? ({ data: [board(1, '带标签看板', ['a', 'b', 'c', 'd', 'e'])] } as any)
        : ({ data: [] } as any));
    mount();
    await screen.findByText('带标签看板');
    // 卡内标签仍渲 #a（顶部改为下拉框，不再是芯片，故限定到卡片断言）
    const card = (await screen.findByText('带标签看板')).closest('article')!;
    expect(within(card).getByText('#a')).toBeTruthy();
    expect(within(card).getByText('+1')).toBeTruthy();  // 5 个只展 4 个，余 +1
  });
});
