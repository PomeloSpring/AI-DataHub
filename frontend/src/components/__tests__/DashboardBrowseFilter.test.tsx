/**
 * 浏览域看板清单的名称 / 标签筛选（工作空间「数据看板」一级页 + 进组合后的看板详情页）。
 *
 * 需求：看板多了翻不动 —— 「App 使用·自研 App 分析」「App 使用·三方集成」是总集，
 * 下面又补了 24 张单 App 看板并归进一个组合，同组切换条一次要摆几十颗 pill。
 * 锁定四件事：
 *  1. 目录行必须带 tags（后端下发），否则标签筛选永远筛不出东西；
 *  2. 一级清单页有独立标签下拉框（与看板管理页同口径，不是芯片行），命中即跨组合平铺看板卡；
 *  3. 进组合后的详情页顶部有「筛选同组看板」入口，按名称/标签收窄 pill 切换条，
 *     并当场显示命中数；筛掉了当前看板要显式说出来，不能像"选中态坏了"；
 *  4. 看板卡上的标签显示业务名（#单App看板），不显示裸 id。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Analysis from '@/pages/Analysis';
import WorkspaceDashboards from '@/pages/WorkspaceDashboards';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useDashboardStore, type Dashboard } from '@/stores/dashboardStore';
import { usePermissionStore } from '@/stores/permissionStore';
import { invalidateCatalogCache } from '@/lib/dashboardCatalog';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({
  useVisLibrary: () => ({ items: [], error: '', refresh: vi.fn() }),
}));
vi.mock('@/components/DashboardCanvas', () => ({
  ResponsiveDashboardCanvas: () => <div>响应式网格</div>,
  DashboardThumbnail: () => null,
  ChartGlyph: () => <div>缩影</div>,
}));
vi.mock('@/components/DashboardParams', () => ({ DashboardRuntimeParams: () => null }));

const detail = (id: number): Dashboard => ({
  id, name: `App·板 ${id}`, status: 'enabled', is_default: false, charts: [], filters: {},
  description: '', layout: [], params: [], page_params: [], owner_id: 1, is_public: false,
  carousel_interval: 0, sort_order: id, created_at: '', updated_at: '',
});

/** 组内 6 张板：> 4 才给筛选入口（两三张时靠 pill 一眼认得出，多一个控件反而抖） */
const groupBoard = (id: number, tags: string[], name = `App·板 ${id}`) => ({
  id, name, status: 'enabled', is_default: id === 1, sort_order: id,
  description: `${name} 的口径说明`, chart_count: 4, tags, editable: false,
  updated_at: '2026-11-13T08:00:00',
  preview: { width: 1920, height: 1080, charts: [] },
});
const groupsFixture = () => ([
  { id: 7, name: 'App 单应用看板（联耀）', description: '', sort: 0, dashboards: [
    groupBoard(1, ['单App看板', '自研App']),
    groupBoard(2, ['单App看板', '自研App']),
    groupBoard(3, ['单App看板', '三方集成App']),
    groupBoard(4, ['单App看板', '三方集成App', '低样本']),
    groupBoard(5, ['单App看板', '平台主应用']),
    groupBoard(6, ['App总集', '三方集成App'], 'App 使用·三方集成'),
  ] },
  { id: 0, name: '未分组', description: '', sort: 9999, dashboards: [groupBoard(8, [], '未分组看板')] },
]);

function routes(path: string) {
  return render(<TooltipProvider><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/ws/:workspaceId/dashboards" element={<WorkspaceDashboards />} />
    <Route path="/ws/:workspaceId/dashboards/:dashboardId" element={<Analysis />} />
  </Routes></MemoryRouter></TooltipProvider>);
}

beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear();
  invalidateCatalogCache();
  useDashboardStore.getState().reset();
  usePermissionStore.setState({ loaded: true, unrestricted: true, perms: null, allowedMenus: null });
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/dashboard/groups/visible') return { data: groupsFixture() } as any;
    const m = /^\/dashboard\/(\d+)$/.exec(url);
    if (m) return { data: detail(Number(m[1])) } as any;
    return { data: [] } as any;
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('一级清单页 — 标签下拉筛选', () => {
  it('标签下拉选项 = 可见看板 tags 去重并集，按名字筛与按标签筛同一判据', async () => {
    routes('/ws/7/dashboards');
    await screen.findByText('App 单应用看板（联耀）');
    const sel = screen.getByRole('combobox', { name: '标签筛选' });
    const optionTexts = within(sel).getAllByRole('option').map(o => o.textContent);
    expect(optionTexts).toContain('全部标签');
    expect(optionTexts).toContain('#单App看板');
    expect(optionTexts).toContain('#低样本');
    // 去重：6 张板里 5 张带「单App看板」，选项只一个
    expect(optionTexts.filter(t => t === '#单App看板')).toHaveLength(1);

    fireEvent.change(sel, { target: { value: '低样本' } });
    // 命中即跨组合平铺看板卡（找看板的意图不该被层级挡住），且只剩那一张
    await waitFor(() => expect(screen.getByText('1 个匹配看板')).toBeTruthy());
    expect(screen.queryByText('App 使用·三方集成')).toBeNull();
    expect(screen.getByText('App·板 4')).toBeTruthy();
    // 看板卡上显示标签业务名（不是裸 id）；行高固定所以只摊前两个，余下的收进 +N 的 title
    const card = screen.getByText('App·板 4').closest('article')!;
    expect(within(card).getByText('#单App看板')).toBeTruthy();
    expect(within(card).getByText('+1')).toBeTruthy();
    expect(within(card).getByText('+1').getAttribute('title')).toContain('低样本');
  });

  it('名称 + 标签同时命中才留下；无命中时空文案把两个条件都说出来', async () => {
    routes('/ws/7/dashboards');
    await screen.findByText('App 单应用看板（联耀）');
    fireEvent.change(screen.getByRole('combobox', { name: '标签筛选' }), { target: { value: '三方集成App' } });
    fireEvent.change(screen.getByRole('textbox', { name: '搜索看板' }), { target: { value: '板 3' } });
    await waitFor(() => expect(screen.getByText('1 个匹配看板')).toBeTruthy());
    expect(screen.getByText('App·板 3')).toBeTruthy();
    expect(screen.queryByText('App·板 4')).toBeNull();
    expect(screen.queryByText('App 使用·三方集成')).toBeNull();

    fireEvent.change(screen.getByRole('textbox', { name: '搜索看板' }), { target: { value: '不存在的名字' } });
    const empty = await screen.findByText(/没有.*的看板/);
    expect(empty.textContent).toContain('匹配「不存在的名字」');
    expect(empty.textContent).toContain('带「三方集成App」标签');
  });

  it('清单里一个标签都没有时下拉置灰并说明原因（不做点开是空的死开关）', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/dashboard/groups/visible') {
        return { data: [{ id: 3, name: '老组合', description: '', sort: 0,
          dashboards: [1, 2].map(id => ({ ...groupBoard(id, []), tags: undefined })) }] } as any;
      }
      const m = /^\/dashboard\/(\d+)$/.exec(url);
      if (m) return { data: detail(Number(m[1])) } as any;
      return { data: [] } as any;
    });
    routes('/ws/7/dashboards');
    await screen.findByText('老组合');
    const sel = screen.getByRole('combobox', { name: '标签筛选' }) as HTMLSelectElement;
    expect(sel.disabled).toBe(true);
    expect(sel.textContent).toContain('全部标签（暂无）');
  });
});

describe('进组合后 — 同组切换条的名称/标签筛选', () => {
  it('同组超过 4 张才给筛选入口；按标签收窄 pill 条并当场显示命中数', async () => {
    routes('/ws/7/dashboards/1');
    // 组数据异步抵达，切换条与筛选入口同一批渲染（直接 getByRole 会抢跑）
    const trigger = await screen.findByRole('button', { name: '筛选同组看板' });
    const bar = () => screen.getByRole('navigation', { name: '同组看板' });
    expect(within(bar()).getAllByRole('button')).toHaveLength(6);
    expect(trigger.textContent).toContain('筛选');

    await userEvent.click(trigger);
    fireEvent.change(await screen.findByLabelText('按标签筛选看板'), { target: { value: '自研App' } });
    // 命中数当场可见：6 张里 2 张带「自研App」
    await waitFor(() => expect(screen.getByText(/命中 2 \/ 6 个看板/)).toBeTruthy());
    await waitFor(() => expect(within(bar()).getAllByRole('button')).toHaveLength(2));
    expect(within(bar()).getByText('App·板 1')).toBeTruthy();
    expect(within(bar()).queryByText('App·板 3')).toBeNull();
    // 触发器上留下选中态与摘要：收起浮层后也读得出“现在是被筛过的”
    expect(trigger.getAttribute('aria-pressed')).toBe('true');
    expect(trigger.textContent).toContain('#自研App');
  });

  it('当前看板被筛掉时显式说出来（否则切换条没有选中项，看着像选中态坏了）', async () => {
    routes('/ws/7/dashboards/1');
    await screen.findByRole('button', { name: '筛选同组看板' });
    await userEvent.click(screen.getByRole('button', { name: '筛选同组看板' }));
    fireEvent.change(await screen.findByLabelText('按标签筛选看板'), { target: { value: '平台主应用' } });
    // 当前板 1 带的是「自研App」，不命中「平台主应用」
    await waitFor(() => expect(screen.getByText('当前看板不在筛选结果内')).toBeTruthy());
    expect(screen.getByText(/命中 1 \/ 6 个看板/)).toBeTruthy();
    // 清除筛选后回到全量，提示消失
    await userEvent.click(screen.getByRole('button', { name: '清除' }));
    await waitFor(() => expect(screen.queryByText('当前看板不在筛选结果内')).toBeNull());
    expect(screen.getByRole('button', { name: '筛选同组看板' }).textContent).toContain('筛选');
  });

  it('组内不超过 4 张板时不给筛选入口', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/dashboard/groups/visible') {
        return { data: [{ id: 7, name: '小组合', description: '', sort: 0,
          dashboards: [1, 2, 3].map(id => groupBoard(id, ['单App看板'])) }] } as any;
      }
      const m = /^\/dashboard\/(\d+)$/.exec(url);
      if (m) return { data: detail(Number(m[1])) } as any;
      return { data: [] } as any;
    });
    routes('/ws/7/dashboards/1');
    await screen.findByRole('navigation', { name: '同组看板' });
    expect(screen.queryByRole('button', { name: '筛选同组看板' })).toBeNull();
  });
});
