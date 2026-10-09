import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { WorkspaceBoundary, WorkspaceEntry } from '../WorkspaceRoute';
import SectionSwitcher from '../SectionSwitcher';
import Analysis from '@/pages/Analysis';
import Screen from '@/pages/Screen';
import DashboardPage from '@/pages/Dashboard';
import WorkspaceLayout from '../WorkspaceLayout';
import { TooltipProvider } from '../ui/tooltip';
import { useWorkspaceStore, type Workspace } from '@/stores/workspaceStore';
import { useDashboardStore, type Dashboard } from '@/stores/dashboardStore';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import { useChatStore } from '@/stores/chatStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => ({ items: [], error: '', refresh: vi.fn() }) }));
vi.mock('@/components/DashboardCanvas', () => ({ ResponsiveDashboardCanvas: () => <div>响应式网格</div>, FittedDashboardCanvas: () => <div>大屏画布</div>, DashboardThumbnail: () => null }));
vi.mock('@/components/DashboardParams', () => ({ DashboardRuntimeParams: () => null }));
const ws = (id = 7) => ({ id, name: `空间 ${id}`, user_default: true } as Workspace);
const board = (id: number, status: Dashboard['status'] = 'enabled', is_default = false): Dashboard => ({ id, name: `看板 ${id}`, status, is_default, charts: [], filters: {}, description: '', layout: [], params: [], page_params: [], owner_id: 1, is_public: false, carousel_interval: 10, sort_order: id, created_at: '', updated_at: '' });
// 组目录行(轻量) + 未分组兜底组
const groupBoard = (id: number, status: Dashboard['status'] = 'enabled', is_default = false) => ({ id, name: `看板 ${id}`, status, is_default, sort_order: id });
const groupsFixture = (opts?: { defaultId?: number | null }) => ([
  { id: 7, name: '经营组', description: '', sort: 0, dashboards: [groupBoard(1, 'closed', opts?.defaultId === 1), groupBoard(2, 'enabled', opts?.defaultId === 2), groupBoard(3, 'enabled', opts?.defaultId === 3 || opts?.defaultId === undefined)] },
  { id: 0, name: '未分组', description: '', sort: 9999, dashboards: [groupBoard(4)] },
]);
function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname}{location.search}</output>; }
function routes(path: string) {
  return render(<TooltipProvider><MemoryRouter initialEntries={[path]}><Path /><Routes>
    <Route path="/" element={<WorkspaceEntry />} /><Route path="/page/:dashboardId" element={<WorkspaceEntry legacyId />} />
    <Route path="/dashboards" element={<Analysis />} />
    <Route path="/dashboards/:groupId" element={<Analysis />} />
    <Route path="/dashboards/:groupId/:dashboardId" element={<Analysis />} />
    <Route path="/screen/:dashboardId" element={<Screen />} />
    <Route path="/system/dashboards" element={<DashboardPage />} />
  </Routes></MemoryRouter></TooltipProvider>);
}
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear();
  useWorkspaceStore.getState().reset(); useDashboardStore.getState().reset(); useChatStore.getState().reset();
  vi.mocked(client.get).mockImplementation(async url => {
    if (url === '/dashboard/groups/visible') return { data: groupsFixture() };
    if (url === '/dashboard/groups') return { data: [] };
    const m = /^\/dashboard\/(\d+)$/.exec(url);
    if (m) return { data: board(Number(m[1])) };
    if (url.startsWith('/dashboard/')) return { data: [board(1, 'closed', true), board(2), board(3, 'enabled', true)] };
    return { data: [] };
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('数据看板 — 仪表盘组维度导航', () => {
  it('组目录跳默认已启用看板，而非禁用默认项', async () => {
    routes('/dashboards');
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/3'), { timeout: 3000 });
    expect(client.get).toHaveBeenCalledWith('/dashboard/groups/visible');
    expect(screen.queryByRole('button', { name: '编辑看板' })).not.toBeInTheDocument();
  });
  it('没有默认项时选组内第一个已启用看板', async () => {
    vi.mocked(client.get).mockImplementation(async url => {
      if (url === '/dashboard/groups/visible') return { data: groupsFixture({ defaultId: null }) };
      const m = /^\/dashboard\/(\d+)$/.exec(url);
      if (m) return { data: board(Number(m[1])) };
      return { data: [] };
    });
    routes('/dashboards'); await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/2'), { timeout: 3000 });
  });
  it('深链看板归位所在组（跨组可解析）', async () => {
    routes('/dashboards/0/2');
    expect(await screen.findByText('响应式网格')).toBeInTheDocument();
  });
  it('无任何可见看板显示居中空态（没有仪表盘）', async () => {
    vi.mocked(client.get).mockResolvedValue({ data: [] });
    routes('/dashboards');
    expect(await screen.findByText('没有仪表盘', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();
  });
  it('组加载失败显示重试，重试后恢复跳转', async () => {
    vi.mocked(client.get).mockRejectedValueOnce(new Error('offline'));
    routes('/dashboards');
    expect(await screen.findByText('加载仪表盘组失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/3'), { timeout: 3000 });
  });
  it('可见集 fail-closed：角色无授权看板时不渲染组内容', async () => {
    vi.mocked(client.get).mockImplementation(async url => {
      if (url === '/dashboard/groups/visible') return { data: [{ id: 0, name: '未分组', description: '', sort: 9999, dashboards: [] }] };
      return { data: [] };
    });
    routes('/dashboards/0/2');
    expect(await screen.findByText(/当前仪表盘组暂无已启用的看板|看板不存在或无访问权限/)).toBeInTheDocument();
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
    const view = render(<TooltipProvider><MemoryRouter><SectionSwitcher current="dashboards" /></MemoryRouter></TooltipProvider>);
    fireEvent.keyDown(screen.getByRole('button', { name: '切换模块' }), { key: 'Enter' });
    expect((await screen.findAllByRole('menuitem')).map(item => item.textContent)).toEqual(['首页', '数据看板', '工作空间', '数据中台', '系统配置']);
    act(() => useAuthStore.setState({ user: { id: 2, username: 'test', role: 'user' } }));
    expect(screen.queryByRole('menuitem', { name: '系统配置' })).not.toBeInTheDocument(); view.unmount();
  });
  it('没有菜单权限就不显示对应一级模块入口', async () => {
    useAuthStore.setState({ user: { id: 2, username: 'bob', role: 'viewer' } });
    usePermissionStore.setState({ loaded: true, unrestricted: false, perms: ['chat:use'], allowedMenus: ['workspace:chat'] });
    const view = render(<TooltipProvider><MemoryRouter><SectionSwitcher current="home" /></MemoryRouter></TooltipProvider>);
    fireEvent.keyDown(screen.getByRole('button', { name: '切换模块' }), { key: 'Enter' });
    const labels = (await screen.findAllByRole('menuitem')).map(i => i.textContent);
    // 数据看板对登录用户恒可见(产品决策, 不参与权限码显隐, 内容级可见性由数据层裁决);
    // 无 data:* 菜单 → 无数据中台入口; 其余仅保留有权限的
    expect(labels).toEqual(['首页', '数据看板', '工作空间']);
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
  it('目录默认关闭、平铺展示组内可见项并支持搜索', async () => {
    routes('/dashboards/7/2'); await screen.findByText('响应式网格');
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
    expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/3');
  });
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
  it('裸播放链接无组范围时回数据看板', async () => {
    routes('/screen/2');
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards'));
  });
  it('非法播放范围不发起看板请求', () => {
    routes('/screen/2?group=-1'); expect(screen.getByRole('alert')).toHaveTextContent('范围无效');
    expect(client.get).not.toHaveBeenCalled();
  });
  it('播放携带组与来源，只读进入并正确返回', async () => {
    routes('/dashboards/7/2'); await screen.findByText('响应式网格');
    fireEvent.click(screen.getByRole('button', { name: '大屏播放' })); await screen.findByText('大屏画布');
    expect(screen.getByTestId('path')).toHaveTextContent('/screen/2?group=7&from=%2Fdashboards%2F7%2F2');
    expect(screen.queryByRole('button', { name: '编辑外观与数据' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '退出大屏' })); await screen.findByText('响应式网格');
    expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/2');
  });
});
