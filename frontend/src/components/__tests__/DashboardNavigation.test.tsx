import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
import { useChatStore } from '@/stores/chatStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => ({ items: [], error: '', refresh: vi.fn() }) }));
vi.mock('@/components/DashboardCanvas', () => ({ ResponsiveDashboardCanvas: () => <div>响应式网格</div>, FittedDashboardCanvas: () => <div>大屏画布</div>, DashboardThumbnail: () => null }));
vi.mock('@/components/DashboardParams', () => ({ DashboardRuntimeParams: () => null }));
const ws = (id = 7) => ({ id, name: `空间 ${id}`, user_default: true } as Workspace);
const board = (id: number, status: Dashboard['status'] = 'enabled', is_default = false): Dashboard => ({ id, name: `看板 ${id}`, status, is_default, charts: [], filters: {}, description: '', layout: [], params: [], page_params: [], owner_id: 1, is_public: false, carousel_interval: 10, sort_order: id, created_at: '', updated_at: '' });
function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname}{location.search}</output>; }
function routes(path: string) {
  return render(<TooltipProvider><MemoryRouter initialEntries={[path]}><Path /><Routes>
    <Route path="/" element={<WorkspaceEntry />} /><Route path="/page/:dashboardId" element={<WorkspaceEntry legacyId />} />
    <Route path="/dashboards/:workspaceId" element={<WorkspaceBoundary><Analysis /></WorkspaceBoundary>} />
    <Route path="/dashboards/:workspaceId/:dashboardId" element={<WorkspaceBoundary><Analysis /></WorkspaceBoundary>} />
    <Route path="/screen/:dashboardId" element={<Screen />} />
    <Route path="/system/dashboards" element={<DashboardPage />} />
  </Routes></MemoryRouter></TooltipProvider>);
}
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear();
  useWorkspaceStore.getState().reset(); useDashboardStore.getState().reset(); useChatStore.getState().reset();
  vi.mocked(client.get).mockImplementation(async url => ({ data: url === '/workspaces' ? [ws()] : url.startsWith('/dashboard/') ? [board(1, 'closed', true), board(2), board(3, 'enabled', true)] : [] }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('独立入口与导航', () => {
  it('根入口校验工作空间并跳转已启用默认看板，而非禁用默认项', async () => {
    localStorage.setItem('currentWorkspace', '999'); routes('/');
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/3'));
    expect(client.get).toHaveBeenCalledWith('/dashboard/?workspace_id=7');
    expect(screen.queryByRole('button', { name: '编辑看板' })).not.toBeInTheDocument();
  });
  it('没有默认项时选既有顺序中第一个已启用看板', async () => {
    vi.mocked(client.get).mockImplementation(async url => ({ data: url === '/workspaces' ? [ws()] : url.startsWith('/dashboard/') ? [board(1, 'closed'), board(4), board(2)] : [] }));
    routes('/'); await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/4'));
  });
  it('旧看板链接保留 ID', async () => {
    routes('/page/2'); await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/2'));
  });
  it('没有工作空间显示明确空态', async () => {
    vi.mocked(client.get).mockResolvedValue({ data: [] }); routes('/');
    expect(await screen.findByText(/暂无可访问的工作空间/)).toBeInTheDocument();
    expect(client.get).not.toHaveBeenCalledWith('/dashboard/');
  });
  it('工作空间加载失败显示重试，不改查全量', async () => {
    vi.mocked(client.get).mockRejectedValueOnce(new Error('offline')); routes('/');
    expect(await screen.findByText('工作空间加载失败，请重试')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/3'));
  });
  it('工作空间无看板不跳问数', async () => {
    vi.mocked(client.get).mockImplementation(async url => ({ data: url === '/workspaces' ? [ws()] : [] }));
    routes('/'); expect(await screen.findByText(/当前工作空间暂无已启用/)).toBeInTheDocument();
    expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7');
  });
  it('权限撤回后阻止继续渲染原空间数据', async () => {
    routes('/dashboards/7/2'); await screen.findByText('响应式网格');
    vi.mocked(client.get).mockResolvedValue({ data: [ws(8)] });
    await act(async () => { await useWorkspaceStore.getState().loadWorkspaces(); });
    expect(screen.queryByText('响应式网格')).not.toBeInTheDocument();
    expect(screen.getByText(/当前工作空间已不可访问/)).toBeInTheDocument();
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
  it('模块顺序为五项，普通用户不显示系统配置', async () => {
    useAuthStore.setState({ user: { id: 1, username: 'test', role: 'admin' } });
    const view = render(<TooltipProvider><MemoryRouter><SectionSwitcher current="dashboards" /></MemoryRouter></TooltipProvider>);
    fireEvent.keyDown(screen.getByRole('button', { name: '切换模块' }), { key: 'Enter' });
    expect((await screen.findAllByRole('menuitem')).map(item => item.textContent)).toEqual(['数据看板', '智能问数', '工作空间', '数据中台', '系统配置']);
    act(() => useAuthStore.setState({ user: { id: 2, username: 'test', role: 'user' } }));
    expect(screen.queryByRole('menuitem', { name: '系统配置' })).not.toBeInTheDocument(); view.unmount();
  });
  it('独立问数隐藏综合侧栏，切换工作空间保留独立模块', async () => {
    useWorkspaceStore.setState({ loaded: true, workspaces: [ws(7), ws(8)], currentWorkspaceId: 7 });
    render(<TooltipProvider><MemoryRouter initialEntries={['/ask/7']}><Path /><Routes><Route path="/ask/:workspaceId" element={<WorkspaceBoundary><WorkspaceLayout module="ask" /></WorkspaceBoundary>}><Route index element={<div>独立对话主体</div>} /></Route></Routes></MemoryRouter></TooltipProvider>);
    expect(screen.queryByRole('navigation', { name: '工作空间导航' })).not.toBeInTheDocument();
    expect(screen.getByText('独立对话主体')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('button', { name: '切换工作空间' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: /空间 8/ }));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ask/8'));
    expect(client.get).not.toHaveBeenCalledWith('/dashboard/');
    expect(client.get).not.toHaveBeenCalledWith('/admin/menu-tree?workspace_id=8');
  });
  it('目录默认关闭、失败重试留在抽屉，仅展示已授权启用项', async () => {
    let attempts = 0;
    vi.mocked(client.get).mockImplementation(async url => {
      if (url === '/workspaces') return { data: [ws()] };
      if (url.startsWith('/dashboard/')) return { data: [board(1, 'closed'), board(2), board(3)] };
      if (url.startsWith('/admin/menu-tree')) {
        if (++attempts === 1) throw new Error('目录暂不可用');
        return { data: [{ id: 5, name: '运营', children: [{ id: 11, page_id: 1 }, { id: 12, page_id: 2 }, { id: 13, page_id: 3 }, { id: 14, page_id: 999 }] }] };
      }
      return { data: [] };
    });
    routes('/dashboards/7/2'); await screen.findByText('响应式网格');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开看板目录' }));
    fireEvent.click(await screen.findByRole('button', { name: '重试' }));
    await screen.findByRole('button', { name: '看板 3' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '看板 1' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索看板'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: '看板 3' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/3');
  });
  it('管理入口重置工作空间范围，轮播复用统一播放页并携带来源', async () => {
    useDashboardStore.setState({ currentWorkspaceId: 7, dashboards: [board(99)] });
    routes('/system/dashboards');
    await screen.findByRole('button', { name: '预览 看板 2' });
    expect(client.get).toHaveBeenCalledWith('/dashboard/');
    fireEvent.click(screen.getByRole('button', { name: '轮播' }));
    await screen.findByText('大屏画布');
    expect(screen.getByTestId('path')).toHaveTextContent('/screen/2?workspace_id=0&from=%2Fsystem%2Fdashboards&interval=10');
    expect(screen.getByTestId('dashboard-refresh-actions').closest('header')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '退出大屏' }));
    await screen.findByRole('button', { name: '轮播' });
    expect(screen.getByTestId('path')).toHaveTextContent('/system/dashboards');
  });
  it('旧播放链接保留看板 ID 并补齐可验证工作空间', async () => {
    routes('/screen/2'); await screen.findByText('大屏画布');
    expect(screen.getByTestId('path')).toHaveTextContent('/screen/2?workspace_id=7&from=%2Fdashboards%2F7');
    expect(client.get).not.toHaveBeenCalledWith('/dashboard/');
  });
  it('非法播放范围不发起看板请求', () => {
    routes('/screen/2?workspace_id=-1'); expect(screen.getByRole('alert')).toHaveTextContent('范围无效');
    expect(client.get).not.toHaveBeenCalled();
  });
  it('播放携带作用域与来源，刷新链接后仍只读并正确返回', async () => {
    routes('/dashboards/7/2'); await screen.findByText('响应式网格');
    fireEvent.click(screen.getByRole('button', { name: '大屏播放' })); await screen.findByText('大屏画布');
    expect(screen.getByTestId('path')).toHaveTextContent('/screen/2?workspace_id=7&from=%2Fdashboards%2F7%2F2');
    expect(screen.queryByRole('button', { name: '编辑外观与数据' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '退出大屏' })); await screen.findByText('响应式网格');
    expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/7/2');
  });
});
