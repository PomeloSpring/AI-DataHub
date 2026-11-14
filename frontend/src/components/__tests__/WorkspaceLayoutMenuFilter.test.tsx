import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import WorkspaceLayout from '../WorkspaceLayout';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { useAuthStore } from '@/stores/authStore';
import { useWorkspaceStore, type Workspace } from '@/stores/workspaceStore';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

const ws = { id: 7, name: '空间 7', user_default: true } as Workspace;

function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname}</output>; }

function renderLayout(path = '/ws/7/chat') {
  return render(
    <TooltipProvider>
      <MemoryRouter initialEntries={[path]}>
        <Path />
        <Routes>
          <Route path="/ws/:workspaceId" element={<WorkspaceLayout />}>
            <Route path="chat" element={<div>对话主体</div>} />
            <Route path="dashboards" element={<div>看板清单</div>} />
            <Route path="dashboards/:dashboardId" element={<div>看板详情</div>} />
            <Route path="reports" element={<div>页面：报告中心</div>} />
            <Route path="decisions" element={<div>页面：决策建议</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </TooltipProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => ({
    data: url === '/admin/brand' ? { show_icon: true, show_text: true, app_name: 'AI-DataHub' } : [],
  }));
  useAuthStore.setState({ user: { id: 1, username: 'test', role: 'viewer' } as any });
  useWorkspaceStore.setState({ loaded: true, workspaces: [ws], currentWorkspaceId: 7 } as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('WorkspaceLayout 菜单按权限码过滤', () => {
  // 子页顶栏面包屑：看板详情这类自带页头的页面，顶栏不再空成一条白带
  it('菜单子页在顶栏显示当前菜单面包屑，菜单根页不重复显示', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, perms: null, allowedMenus: null });
    const sub = renderLayout('/ws/7/dashboards/3');
    const bar = sub.container.querySelector('header')!;
    expect(within(bar).getByText('数据看板')).toBeInTheDocument();
    sub.unmount();
    const root = renderLayout('/ws/7/dashboards');
    expect(within(root.container.querySelector('header')!).queryByText('数据看板')).toBeNull();
  });

  it('未授权菜单不渲染（workspace:chat/reports/scheduled 口径）', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat'], perms: [] });
    renderLayout();
    expect(screen.getByText('智能问数')).toBeInTheDocument();
    expect(screen.queryByText('报告中心')).not.toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
  });

  it('授权菜单按 allowedMenus 可见', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat', 'workspace:reports'], perms: [] });
    renderLayout();
    expect(screen.getByText('智能问数')).toBeInTheDocument();
    expect(screen.getByText('报告中心')).toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
  });

  it('unrestricted（admin/未限制）全部可见', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    expect(screen.getByText('智能问数')).toBeInTheDocument();
    expect(screen.getByText('报告中心')).toBeInTheDocument();
    expect(screen.getByText('任务调度')).toBeInTheDocument();
  });

  it('数据看板入口随菜单配置显隐（workspace:dashboards ↔ dashboard:read）', () => {
    // 菜单登记与角色权限同步后: 未授予菜单 → 不渲染; 授予后 → 可进, 落看板清单
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat'], perms: [] });
    const view = renderLayout();
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument();
    view.unmount();
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat', 'workspace:dashboards'], perms: ['dashboard:read'] });
    renderLayout();
    const entry = screen.getByText('数据看板').closest('button');
    expect(entry).toBeInTheDocument();
    fireEvent.click(entry!);
    expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/dashboards');
  });

  it('「云空间」段的资产清单同样按菜单码显隐（workspace:assets ↔ asset:read）', () => {
    // 旧「恒可见」例外已废除：未登记/未授权就不应出现在侧栏（否则角色页配了没用）
    usePermissionStore.setState({ loaded: true, unrestricted: false, allowedMenus: ['workspace:chat'], perms: [] });
    const view = renderLayout();
    expect(screen.getByText('智能问数')).toBeInTheDocument();
    expect(screen.queryByText('资产清单')).not.toBeInTheDocument();
    expect(screen.queryByText('云空间')).not.toBeInTheDocument();   // 段下无可访问项 → 不渲染分组标题
    view.unmount();
    usePermissionStore.setState({
      loaded: true, unrestricted: false,
      allowedMenus: ['workspace:chat', 'workspace:assets'], perms: ['asset:read'],
    });
    renderLayout();
    expect(screen.getByText('云空间')).toBeInTheDocument();
    fireEvent.click(screen.getByText('资产清单').closest('button')!);
    expect(screen.getByTestId('path')).toHaveTextContent('/ws/7/assets');
  });

  it('决策建议不占工作空间侧栏入口（即便菜单码被授权也不出现）', () => {
    /* 2026-11-12 拍板：不是所有问题都需要决策，一级入口会暗示“每轮都该看建议”。
       消费入口只留 Chat 建议卡 / 看板控件 / 通知实例链接；workspace:decisions 同批下架，
       所以就算旧角色手里还拿着这个菜单码，侧栏也不得先画出入口再抽走（双口径）。 */
    usePermissionStore.setState({
      loaded: true, unrestricted: false,
      allowedMenus: ['workspace:reports', 'workspace:decisions'], perms: ['decision:read'],
    });
    renderLayout('/ws/7/reports');
    expect(screen.getByText('报告中心')).toBeInTheDocument();
    expect(screen.queryByText('决策建议')).not.toBeInTheDocument();
    const labels = screen.getAllByRole('button').map(b => b.textContent?.trim());
    expect(labels.some(t => /决策/.test(t || ''))).toBe(false);
  });

  it('侧栏「数据分析」组顺序：智能问数 → 数据看板 → 报告中心', () => {
    /* 与角色权限页的 sort（workspace:chat=1 / dashboards=2 / reports=3）同批次，
       并且第一项就是工作空间默认页（登录落地/模块切换落点）。 */
    usePermissionStore.setState({ loaded: true, unrestricted: true, perms: null, allowedMenus: null });
    renderLayout();
    const labels = screen.getAllByRole('button').map(b => b.textContent?.trim());
    const at = (t: string) => labels.indexOf(t);
    expect(at('智能问数')).toBeGreaterThanOrEqual(0);
    expect(at('智能问数')).toBeLessThan(at('数据看板'));
    expect(at('数据看板')).toBeLessThan(at('报告中心'));
  });
});

describe('权限未就绪时不得闪现无权限菜单', () => {
  it('权限清单未加载时只渲染骨架（isMenuAllowed 未加载时 fail-open，不能直接画全量菜单）', () => {
    usePermissionStore.setState({ loaded: false, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    expect(screen.getByLabelText('菜单加载中')).toBeInTheDocument();
    expect(screen.queryByText('自动化')).not.toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument();
  });

  it('只授报告中心时：加载完成后「自动化」分组与任务调度不出现（先闪现后消失即此回归）', () => {
    usePermissionStore.setState({ loaded: false, unrestricted: true, allowedMenus: null, perms: null });
    renderLayout();
    act(() => usePermissionStore.setState({
      loaded: true, unrestricted: false,
      allowedMenus: ['workspace:dashboards', 'workspace:chat', 'workspace:reports'],
      perms: ['report:read'],
    }));
    expect(screen.getByText('报告中心')).toBeInTheDocument();
    expect(screen.queryByText('任务调度')).not.toBeInTheDocument();
    expect(screen.queryByText('自动化')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('菜单加载中')).not.toBeInTheDocument();
  });
});

describe('工作空间切换下拉的入口取舍', () => {
  it('只有一个工作空间时不展示空间名与切换弹窗（无项可切就是空承诺）', () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, perms: null, allowedMenus: null });
    renderLayout();
    expect(screen.queryByLabelText('切换工作空间')).not.toBeInTheDocument();
    expect(screen.queryByText('空间 7')).not.toBeInTheDocument();
    // 收起下拉不影响路由与页面渲染（loadWorkspaces/URL 同步照旧）
    expect(screen.getByText('对话主体')).toBeInTheDocument();
  });

  it('有多个工作空间时列出既有空间，但不给「新建工作空间」入口（产品决策暂不开放，后端能力保留）', async () => {
    usePermissionStore.setState({ loaded: true, unrestricted: true, perms: null, allowedMenus: null });
    useWorkspaceStore.setState({
      loaded: true, currentWorkspaceId: 7,
      workspaces: [ws, { ...ws, id: 8, name: '空间 8', is_default: false, user_default: false }],
    } as any);
    renderLayout();
    // Radix 下拉靠键盘/指针事件开合，fireEvent.click 打不开（旧写法“空间 7”
    //   其实是触发器自己的名字，断言的是没开弹窗也能过）
    fireEvent.keyDown(screen.getByLabelText('切换工作空间'), { key: 'Enter' });
    const names = (await screen.findAllByRole('menuitem')).map(i => i.textContent);
    expect(names).toEqual([expect.stringContaining('空间 7'), expect.stringContaining('空间 8')]);
    expect(screen.queryByText('新建工作空间')).not.toBeInTheDocument();
  });
});
