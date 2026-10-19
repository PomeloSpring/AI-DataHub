import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { WorkspaceBoundary, WorkspaceEntry } from '../WorkspaceRoute';
import SectionSwitcher from '../SectionSwitcher';
import Analysis from '@/pages/Analysis';
import Screen from '@/pages/Screen';
import DashboardPage from '@/pages/Dashboard';
import WorkspaceDashboards from '@/pages/WorkspaceDashboards';
import WorkspaceLayout from '../WorkspaceLayout';
import { TooltipProvider } from '../ui/tooltip';
import { useWorkspaceStore, type Workspace } from '@/stores/workspaceStore';
import { useDashboardStore, type Dashboard } from '@/stores/dashboardStore';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import { useChatStore } from '@/stores/chatStore';
import { invalidateCatalogCache } from '@/lib/dashboardCatalog';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => ({ items: [], error: '', refresh: vi.fn() }) }));
vi.mock('@/components/DashboardCanvas', () => ({ ResponsiveDashboardCanvas: () => <div>响应式网格</div>, FittedDashboardCanvas: () => <div>大屏画布</div>, DashboardThumbnail: () => null }));
vi.mock('@/components/DashboardParams', () => ({ DashboardRuntimeParams: () => null }));
const ws = (id = 7) => ({ id, name: `空间 ${id}`, user_default: true } as Workspace);
const board = (id: number, status: Dashboard['status'] = 'enabled', is_default = false): Dashboard => ({ id, name: `看板 ${id}`, status, is_default, charts: [], filters: {}, description: '', layout: [], params: [], page_params: [], owner_id: 1, is_public: false, carousel_interval: 10, sort_order: id, created_at: '', updated_at: '' });
// 组目录行(轻量) + 卡片清单展示字段(描述/图表数/几何预览/editable); 未分组兑底组
// editable=false 模拟「角色授予可见」的看板: 能看, 不能编辑
const groupBoard = (id: number, status: Dashboard['status'] = 'enabled', is_default = false) => ({
  id, name: `看板 ${id}`, status, is_default, sort_order: id, description: `看板 ${id} 的口径说明`, chart_count: 2,
  editable: id !== 4, owner_id: id === 4 ? 1 : 9,
  updated_at: '2026-10-01T08:00:00', preview: { width: 1920, height: 1080, blocks: [{ x: 40, y: 40, w: 600, h: 400, widget: false }] },
});
const groupsFixture = (opts?: { defaultId?: number | null }) => ([
  { id: 7, name: '经营组', description: '', sort: 0, dashboards: [groupBoard(1, 'closed', opts?.defaultId === 1), groupBoard(2, 'enabled', opts?.defaultId === 2), groupBoard(3, 'enabled', opts?.defaultId === 3 || opts?.defaultId === undefined)] },
  { id: 0, name: '未分组', description: '', sort: 9999, dashboards: [groupBoard(4)] },
]);
function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname}{location.search}</output>; }
// 数据看板已收进工作空间: 清单 = /ws/:ws/dashboards(卡片瀑布), 详情 = /ws/:ws/dashboards/:id;
// 旧独立看板域(/dashboards*)只保留重定向。
function routes(path: string) {
  return render(<TooltipProvider><MemoryRouter initialEntries={[path]}><Path /><Routes>
    <Route path="/" element={<WorkspaceEntry />} /><Route path="/page/:dashboardId" element={<WorkspaceEntry legacyId />} />
    <Route path="/dashboards" element={<WorkspaceEntry module="dashboards" />} />
    <Route path="/dashboards/:groupId" element={<WorkspaceEntry module="dashboards" />} />
    <Route path="/dashboards/:groupId/:dashboardId" element={<WorkspaceEntry module="dashboards" legacyId />} />
    <Route path="/ws/:workspaceId/dashboards" element={<WorkspaceDashboards />} />
    <Route path="/ws/:workspaceId/dashboards/:dashboardId" element={<Analysis />} />
    <Route path="/screen/:dashboardId" element={<Screen />} />
    <Route path="/system/dashboards" element={<DashboardPage />} />
    <Route path="/system/dashboards/:dashboardId" element={<Analysis />} />
  </Routes></MemoryRouter></TooltipProvider>);
}
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear();
  invalidateCatalogCache();  // 目录 30s 内存缓存: 用例之间不得串数据
  useWorkspaceStore.getState().reset(); useDashboardStore.getState().reset(); useChatStore.getState().reset();
  useWorkspaceStore.setState({ loaded: true, workspaces: [ws(7)], currentWorkspaceId: 7 });
  vi.mocked(client.get).mockImplementation(async url => {
    if (url === '/dashboard/groups/visible') return { data: groupsFixture() };
    if (url === '/dashboard/groups') return { data: [] };
    const m = /^\/dashboard\/(\d+)$/.exec(url);
    if (m) return { data: board(Number(m[1])) };
    if (url.startsWith('/dashboard/')) return { data: [board(1, 'closed', true), board(2), board(3, 'enabled', true)] };
    return { data: [] };
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null }); });

describe('工作空间数据看板 — 卡片瀑布清单', () => {
  it('清单按可见集渲卡：只收已启用看板，未启用数量显式提示', async () => {
    routes('/ws/7/dashboards');
    await screen.findByRole('button', { name: '打开看板 看板 3' });
    expect(screen.getByRole('button', { name: '打开看板 看板 2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开看板 看板 4' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开看板 看板 1' })).not.toBeInTheDocument();
    expect(screen.getByText('3 个看板')).toBeInTheDocument();
    expect(screen.getByText(/另有 1 个可见看板未启用/)).toBeInTheDocument();
    // 卡片显示可读名称/描述与所属组合名(不拿裸 id 当主要展示)
    expect(screen.getByText('看板 3 的口径说明')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '经营组' })).toBeInTheDocument();
    // 工作空间清单纯消费(查看/播放), 不提供编辑入口 —— 编辑只在系统配置「看板管理」
    expect(screen.queryByRole('button', { name: /^编辑看板/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '大屏播放 看板 3' })).toBeInTheDocument();
  });
  it('点卡片进入对应看板详情', async () => {
    routes('/ws/7/dashboards');
    fireEvent.click(await screen.findByRole('button', { name: '打开看板 看板 3' }));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards/3'));
  });
  it('清单加载失败显式报错，重试后恢复', async () => {
    vi.mocked(client.get).mockRejectedValueOnce(new Error('offline'));
    routes('/ws/7/dashboards');
    expect(await screen.findByText('加载看板清单失败，请重试')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByRole('button', { name: '打开看板 看板 3' })).toBeInTheDocument();
  });
  it('角色无可见看板时清单给出空态引导(fail-closed 不伪造内容)', async () => {
    vi.mocked(client.get).mockImplementation(async url => (url === '/dashboard/groups/visible' ? { data: [] } : { data: [] }));
    routes('/ws/7/dashboards');
    expect(await screen.findByText('还没有分配给你的看板')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^打开看板/ })).not.toBeInTheDocument();
  });
  it('看板组合可筛选，筛后仅显示该组卡片', async () => {
    routes('/ws/7/dashboards');
    fireEvent.click(await screen.findByRole('button', { name: '未分组' }));
    expect(screen.getByRole('button', { name: '打开看板 看板 4' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开看板 看板 3' })).not.toBeInTheDocument();
  });
  it('目录 30s 缓存：列表→详情复用同一份目录，失效后回源', async () => {
    const groupCalls = () => vi.mocked(client.get).mock.calls.filter(([u]) => u === '/dashboard/groups/visible').length;
    const view = routes('/ws/7/dashboards');
    await screen.findByRole('button', { name: '打开看板 看板 3' });
    expect(groupCalls()).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: '打开看板 看板 3' }));
    await screen.findByText('响应式网格');
    expect(groupCalls()).toBe(1);  // 详情页复用缓存, 不重复拉整份目录
    view.unmount();
    invalidateCatalogCache();       // 看板/图表写操作后的失效路径
    routes('/ws/7/dashboards');
    await screen.findByRole('button', { name: '打开看板 看板 3' });
    expect(groupCalls()).toBe(2);  // 失效后回源
  });
  it('旧独立看板链接重定向到工作空间看板清单/详情', async () => {
    routes('/dashboards');
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards'));
  });
  it('旧看板深链重定向保留看板 id', async () => {
    routes('/dashboards/7/3');
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards/3'));
  });
  it('进入工作空间默认落数据看板清单（不是智能问数）', async () => {
    render(<TooltipProvider><MemoryRouter initialEntries={['/']}><Path /><Routes>
      <Route path="/" element={<SectionSwitcher current="home" />} />
      <Route path="/ws/:workspaceId/dashboards" element={<WorkspaceDashboards />} />
      <Route path="/ws/:workspaceId/chat" element={<div>智能问数主体</div>} />
    </Routes></MemoryRouter></TooltipProvider>);
    fireEvent.keyDown(screen.getByRole('button', { name: '切换模块' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: /工作空间/ }));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards'));
    expect(screen.queryByText('智能问数主体')).not.toBeInTheDocument();
  });
  it('工作空间入口(/workspace)重定向到看板清单', async () => {
    render(<TooltipProvider><MemoryRouter initialEntries={['/workspace']}><Path /><Routes>
      <Route path="/workspace" element={<WorkspaceEntry module="workspace" />} />
      <Route path="/ws/:workspaceId/dashboards" element={<WorkspaceDashboards />} />
    </Routes></MemoryRouter></TooltipProvider>);
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards'));
  });
});

describe('工作空间数据看板 — 详情与仪表盘组维度', () => {
  it('深链看板归位所在组（跨组可解析）', async () => {
    routes('/ws/7/dashboards/2');
    expect(await screen.findByText('响应式网格')).toBeInTheDocument();
  });
  it('详情返回落点为看板清单', async () => {
    routes('/ws/7/dashboards/2');
    await screen.findByText('响应式网格');
    fireEvent.click(screen.getByRole('button', { name: '返回看板清单' }));
    expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards');
  });
  it('目录默认关闭、平铺展示组内可见项并支持搜索切换', async () => {
    routes('/ws/7/dashboards/2'); await screen.findByText('响应式网格');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开看板目录' }));
    const dialog = await screen.findByRole('dialog');
    // 平铺目录 = 当前组内 ∩ 角色可见的已启用项(组只做编排不授予可见)
    expect(within(dialog).getByRole('button', { name: '看板 2' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '看板 3' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '看板 1' })).not.toBeInTheDocument();
    expect(client.get).not.toHaveBeenCalledWith(expect.stringContaining('/admin/menu-tree'));
    fireEvent.change(screen.getByLabelText('搜索看板'), { target: { value: '3' } });
    expect(within(dialog).queryByRole('button', { name: '看板 2' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索看板'), { target: { value: '9' } });
    expect(screen.getByText('没有匹配的可用看板')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索看板'), { target: { value: '3' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '看板 3' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards/3');
  });
  it('组加载失败显示重试，重试后恢复渲染', async () => {
    vi.mocked(client.get).mockRejectedValueOnce(new Error('offline'));
    routes('/ws/7/dashboards/2');
    expect(await screen.findByText('加载仪表盘组失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('响应式网格')).toBeInTheDocument();
  });
  it('可见集 fail-closed：角色无授权看板时不渲染内容', async () => {
    vi.mocked(client.get).mockImplementation(async url => {
      if (url === '/dashboard/groups/visible') return { data: [{ id: 0, name: '未分组', description: '', sort: 9999, dashboards: [] }] };
      return { data: [] };
    });
    routes('/ws/7/dashboards/2');
    expect(await screen.findByText('看板不存在或无访问权限')).toBeInTheDocument();
    expect(screen.queryByText('响应式网格')).not.toBeInTheDocument();
  });
  it('跨空间请求竞态只保留最后响应，退出账号清理并拒绝迟到响应', async () => {
    let resolveOld!: (value: any) => void;
    vi.mocked(client.get).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; })).mockResolvedValueOnce({ data: [board(8)] });
    const old = useDashboardStore.getState().loadDashboards(7);
    await useDashboardStore.getState().loadDashboards(8);
    resolveOld({ data: [board(7)] }); await old;
    expect(useDashboardStore.getState().dashboards[0].id).toBe(8);
    useChatStore.getState().setSelectedWorkspaceId(8);
    vi.mocked(client.get).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const convs = useChatStore.getState().loadConversations();
    useAuthStore.getState().logout(); resolveOld({ data: [{ id: 7, title: '旧账号' }] }); await convs;
    expect(useChatStore.getState().conversations).toEqual([]);
    expect(useDashboardStore.getState().dashboards).toEqual([]);
    expect(useWorkspaceStore.getState().currentWorkspaceId).toBe(0);
  });
  it('模块顺序为四项，普通用户不显示系统配置', async () => {
    useAuthStore.setState({ user: { id: 1, username: 'test', role: 'admin' } });
    const view = render(<TooltipProvider><MemoryRouter><SectionSwitcher current="workspace" /></MemoryRouter></TooltipProvider>);
    fireEvent.keyDown(screen.getByRole('button', { name: '切换模块' }), { key: 'Enter' });
    // 数据看板不再是独立模块(已收进工作空间侧栏), 一级入口只剩首页/工作空间/数据中台/系统配置
    expect((await screen.findAllByRole('menuitem')).map(item => item.textContent)).toEqual(['首页', '工作空间', '数据中台', '系统配置']);
    act(() => useAuthStore.setState({ user: { id: 2, username: 'test', role: 'user' } }));
    expect(screen.queryByRole('menuitem', { name: '系统配置' })).not.toBeInTheDocument(); view.unmount();
  });
  it('没有菜单权限就不显示对应一级模块入口', async () => {
    useAuthStore.setState({ user: { id: 2, username: 'bob', role: 'viewer' } });
    usePermissionStore.setState({ loaded: true, unrestricted: false, perms: ['chat:use'], allowedMenus: ['workspace:chat'] });
    const view = render(<TooltipProvider><MemoryRouter><SectionSwitcher current="home" /></MemoryRouter></TooltipProvider>);
    fireEvent.keyDown(screen.getByRole('button', { name: '切换模块' }), { key: 'Enter' });
    const labels = (await screen.findAllByRole('menuitem')).map(i => i.textContent);
    // 无 data:* 菜单 → 无数据中台入口; 其余仅保留有权限的
    expect(labels).toEqual(['首页', '工作空间']);
    view.unmount();
    usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
  });
  it('独立问数隐藏综合侧栏，切换工作空间保留独立模块', async () => {
    useWorkspaceStore.setState({ loaded: true, workspaces: [ws(7), ws(8)], currentWorkspaceId: 7 });
    render(<TooltipProvider><MemoryRouter initialEntries={['/ask/7']}><Path /><Routes><Route path="/ask/:workspaceId" element={<WorkspaceBoundary><WorkspaceLayout module="ask" /></WorkspaceBoundary>}><Route index element={<div>独立对话主体</div>} /></Route></Routes></MemoryRouter></TooltipProvider>);
    expect(screen.queryByRole('navigation', { name: '工作空间导航' })).not.toBeInTheDocument();
    expect(screen.getByText('独立对话主体')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('button', { name: '切换工作空间' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: /空间 8/ }));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ask/8'));
    expect(client.get).not.toHaveBeenCalledWith('/dashboard/groups/visible');
    expect(client.get).not.toHaveBeenCalledWith('/admin/menu-tree?workspace_id=8');
  });
});

describe('看板管理域与大屏播放', () => {
  it('管理入口轮播只在可见已启用看板间播放', async () => {
    // 默认但已停用/设计中的看板不进轮播集: current=设计中项时起点回落到首个可见启用项
    useDashboardStore.setState({ currentWorkspaceId: 7, currentId: 2, dashboards: [board(99)] });
    vi.mocked(client.get).mockImplementation(async url => {
      if (url === '/dashboard/groups/visible') return { data: [{ id: 7, name: '经营组', description: '', sort: 0, dashboards: [groupBoard(1, 'closed', true), groupBoard(2, 'designing'), groupBoard(3), groupBoard(4)] }] };
      if (url === '/dashboard/groups') return { data: [] };
      const m = /^\/dashboard\/(\d+)$/.exec(url);
      if (m) return { data: board(Number(m[1])) };
      if (url.startsWith('/dashboard/')) return { data: [board(1, 'closed', true), board(2, 'designing'), board(3), board(4)] };
      return { data: [] };
    });
    routes('/system/dashboards');
    await screen.findByRole('button', { name: '预览 看板 3' });
    expect(client.get).toHaveBeenCalledWith('/dashboard/');
    fireEvent.click(screen.getByRole('button', { name: '轮播' }));
    await screen.findByText('大屏画布');
    expect(screen.getByTestId('path')).toHaveTextContent('/screen/3?group=0&from=%2Fsystem%2Fdashboards&interval=10');
    expect(screen.getByTestId('dashboard-refresh-actions').closest('header')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '退出大屏' }));
    await screen.findByRole('button', { name: '轮播' });
    expect(screen.getByTestId('path')).toHaveTextContent('/system/dashboards');
  });
  it('看板管理页：角色授予的看板不提供编辑入口（仅属主/admin 可写）', async () => {
    vi.mocked(client.get).mockImplementation(async url => {
      if (url.startsWith('/dashboard/')) return { data: [{ ...board(21), editable: true }, { ...board(22), editable: false }] };
      return { data: [] };
    });
    routes('/system/dashboards');
    await screen.findByRole('button', { name: '预览 看板 22' });
    expect(screen.getAllByRole('button', { name: '编辑' })).toHaveLength(1);  // 仅可写的看板有编辑
    fireEvent.keyDown(screen.getByRole('button', { name: '看板 22 更多操作' }), { key: 'Enter' });
    const items = (await screen.findAllByRole('menuitem')).map(i => i.textContent);
    expect(items.some(t => t?.includes('重命名'))).toBe(false);
    expect(items.some(t => t?.includes('删除'))).toBe(false);
    expect(items.some(t => t?.includes('仅查看'))).toBe(true);
    expect(items.some(t => t?.includes('复制'))).toBe(true);  // 复制为我的看板仍是读+建, 不改原看板
  });
  it('裸播放链接无组范围时回看板清单', async () => {
    routes('/screen/2');
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards'));
  });
  it('非法播放范围不发起看板请求', () => {
    routes('/screen/2?group=-1'); expect(screen.getByRole('alert')).toHaveTextContent('范围无效');
    expect(client.get).not.toHaveBeenCalled();
  });
  it('播放携带组与来源，只读进入并正确返回', async () => {
    routes('/ws/7/dashboards/2'); await screen.findByText('响应式网格');
    fireEvent.click(screen.getByRole('button', { name: '大屏播放' })); await screen.findByText('大屏画布');
    expect(screen.getByTestId('path')).toHaveTextContent('/screen/2?group=7&from=%2Fws%2F7%2Fdashboards%2F2');
    expect(screen.queryByRole('button', { name: '编辑外观与数据' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '退出大屏' })); await screen.findByText('响应式网格');
    expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards/2');
  });
  it('大屏顶栏刷新会对每个数据图表 push 重查询（不只回读 DB 缓存）', async () => {
    // 锁定行为：刷新按钮必须逐个 POST charts/{id}/refresh 向数据源重查询；
    // widget_ 组件无 SQL 不参与；GET /dashboard/{id} 只回 data_cache，不能当刷新。
    const withCharts = { ...board(2), charts: [
      { id: 21, chart_type: 'bar', sql_query: 'SELECT 1', config: {} },
      { id: 22, chart_type: 'line', semantic_query: { object: 'orders' }, config: {} },
      { id: 23, chart_type: 'widget_search', config: {} },
    ] };
    vi.mocked(client.get).mockImplementation(async url => {
      if (url === '/dashboard/groups/visible') return { data: groupsFixture() };
      if (/^\/dashboard\/2$/.test(url)) return { data: withCharts };
      const m = /^\/dashboard\/(\d+)$/.exec(url);
      if (m) return { data: board(Number(m[1])) };
      return { data: [] };
    });
    vi.mocked(client.post).mockResolvedValue({ data: { columns: ['c'], rows: [[1]] } } as any);
    routes('/screen/2?group=7');
    await screen.findByText('大屏画布');
    fireEvent.click(await screen.findByRole('button', { name: '刷新数据' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledTimes(2));
    const posts = vi.mocked(client.post).mock.calls.map(([url]) => url).sort();
    expect(posts).toEqual(['/dashboard/2/charts/21/refresh', '/dashboard/2/charts/22/refresh']);
    expect(vi.mocked(client.post).mock.calls[0][1]).toEqual(expect.objectContaining({ params: expect.any(Object) }));
  });
});
