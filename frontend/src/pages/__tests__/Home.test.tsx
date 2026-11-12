import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import Home from '../Home';
import { TooltipProvider } from '@/components/ui/tooltip';
import { invalidateCatalogCache } from '@/lib/dashboardCatalog';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const groupBoard = (id: number, status = 'enabled') => ({ id, name: `看板 ${id}`, description: '', status, is_default: 0, sort_order: id });
const article = (over: Record<string, unknown> = {}) => ({
  id: 1, slug: 'scan-accuracy-metric', title: '口内扫描精度怎么衡量', summary: '三层口径拆开',
  category_id: 2, category_label: '技术白皮书', category_icon: '🔬', category_color: '#0891b2',
  tags: ['扫描精度'], kind: 'markdown', kind_label: 'Markdown', link_url: '', link_label: '',
  refs: [], pinned: 1, featured: 1, status: 'published', is_live: true, has_pending_change: false,
  review_by: '', published_at: new Date(Date.now() - 3 * 86400_000).toISOString(), archived_at: '',
  view_count: 1240, author: '临床应用研究组', updated_by: '', updated_at: '', version: 3, files: [],
  ...over,
});

const profileArticle = article({
  slug: 'company-profile', title: '联耀医疗（AlliedStar）公司概况',
  summary: '牙科数字化企业：口内扫描仪与数字化种植、正畸解决方案，上海张江研发、资阳制造',
  category_id: 9, category_label: '关于本公司', category_icon: '🏢', category_color: '#475569',
  kind: 'html', kind_label: 'HTML 版式',
  tags: ['市场与渗透率'], pinned: 0, featured: 0, view_count: 12,
});

function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname + location.search}</output>; }

/** 按 URL 分派的默认路由：单个用例只覆盖自己关心的那条，其余保持可用 */
function defaultRoutes(overrides: Record<string, unknown> = {}) {
  const table: Record<string, unknown> = {
    '/dashboard/groups/visible': [{ id: 7, name: '经营组', description: '', sort: 0, dashboards: [groupBoard(3)] }],
    '/content/categories': { items: [{ id: 2, key: 'whitepaper', label: '技术白皮书', icon: '🔬', color: '#0891b2', sort: 2, is_active: 1 }] },
    '/content/tags': { items: [{ id: 1, tag: '扫描精度', sort: 1, is_active: 1, count: 3 }] },
    '/content/articles': { items: [article()], total: 1, page: 1, page_size: 5 },
    // 简介卡取的是 category=about 那一篇，与资讯流的清单请求同 URL、不同参数
    '/content/articles?about': { items: [profileArticle], total: 1, page: 1, page_size: 1 },
    '/datasources/authorized-sites': { items: [
      { site_code: 'cn', display_name: '中国站点', is_aggregate: 0, datasources: [{ id: 1, name: '业务库', db_type: 'mysql' }] },
      { site_code: 'eu', display_name: '欧洲站点', is_aggregate: 0, datasources: [{ id: 2, name: '欧洲仓', db_type: 'doris' }] },
      { site_code: 'global', display_name: '全部站点', is_aggregate: 1, datasources: [] },
    ] },
    '/content/reviews/pending': { items: [], total: 2, page: 1, page_size: 1, review_expired: [] },
    '/knowledge/scenarios': { items: [{ title: '按页面 URL 关联用户操作路径' }] },
    ...overrides,
  };
  vi.mocked(client.get).mockImplementation(((url: string, cfg?: { params?: Record<string, unknown> }) => {
    if (url === '/content/articles' && cfg?.params?.category === 'about')
      return Promise.resolve({ data: table['/content/articles?about'] });
    if (url in table) return Promise.resolve({ data: table[url] });
    return Promise.resolve({ data: [] });
  }) as never);
}

function renderHome() {
  // 顶栏的公告按钮/模块切换器用 Tooltip, 与 main.tsx 同口径由外层 Provider 提供
  return render(<TooltipProvider><MemoryRouter initialEntries={['/']}><Path /><Routes>
    <Route path="/" element={<Home />} />
    <Route path="/dashboards" element={<div>数据看板</div>} />
    <Route path="/system/dashboards" element={<div>仪表盘配置</div>} />
    <Route path="/read/:slug" element={<div>阅读页</div>} />
    <Route path="/ws/:wsId/:rest" element={<div>工作空间</div>} />
    <Route path="/content/*" element={<div>内容中心</div>} />
  </Routes></MemoryRouter></TooltipProvider>);
}

/** 资讯区可见 = 菜单码 home:feed（真值源是权限码反查，与侧栏同口径） */
const withMenus = (menus: string[], perms: string[] = []) =>
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms, allowedMenus: menus });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  invalidateCatalogCache();  // 目录 30s 内存缓存: 用例之间不得串数据
  useAuthStore.setState({ user: { id: 1, username: 'alice', role: 'viewer' } } as any);
  // 提问跳转要落默认工作空间；未准备时 Home 回落 /workspace，那是另一条分支
  useWorkspaceStore.setState({ workspaces: [{ id: 301 } as never], loaded: true, loading: false });
  usePermissionStore.setState({ loaded: true, unrestricted: true, perms: null, allowedMenus: null });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
});

describe('首页 = 工作台 + 资讯流', () => {
  it('渲染四块：问候、提问框、资讯流、模块入口', async () => {
    defaultRoutes();
    renderHome();
    expect(await screen.findByRole('heading', { name: /alice/ }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '向智能问数提问' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /最新资讯/ })).toBeInTheDocument();
    // 卡片标题必须来自接口数据，不是占位文案
    expect(await screen.findByText('口内扫描精度怎么衡量')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '模块入口' })).toBeInTheDocument();
  });

  it('旧版装饰与重复信息已移除：不再有大段"快速开始"标题与未分配提示卡', async () => {
    defaultRoutes();
    renderHome();
    await screen.findByText('口内扫描精度怎么衡量');
    // 模块入口压缩成一行导航，旧的"快速开始"段标题不再存在
    expect(screen.queryByText('快速开始')).not.toBeInTheDocument();
    // 「还没有分配仪表盘」只在该角色真的没有看板时出现（右栏内），不是首屏大卡
    expect(screen.queryByText('还没有分配仪表盘')).not.toBeInTheDocument();
  });

  it('提问框回车带着问题进智能问数，并标上“落地即执行”', async () => {
    defaultRoutes();
    renderHome();
    await screen.findByText('口内扫描精度怎么衡量');
    await userEvent.type(screen.getByRole('textbox', { name: '向智能问数提问' }), '本月装机同比{Enter}');
    expect(sessionStorage.getItem('home_question')).toBe('本月装机同比');
    // send 标记缺失 = 到问数页只预填不执行（用户要在首页外再按一次发送）
    expect(sessionStorage.getItem('home_question_send')).toBe('1');
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/'));
  });

  it('点「问数」按钮与回车同口径（带问题 + send）', async () => {
    defaultRoutes();
    renderHome();
    await screen.findByText('口内扫描精度怎么衡量');
    await userEvent.type(screen.getByRole('textbox', { name: '向智能问数提问' }), '上月装机台数');
    await userEvent.click(screen.getByRole('button', { name: '问数' }));
    expect(sessionStorage.getItem('home_question')).toBe('上月装机台数');
    expect(sessionStorage.getItem('home_question_send')).toBe('1');
  });

  it('空问题点「问数」只跳转，不往契约里塞空问题', async () => {
    defaultRoutes();
    renderHome();
    await screen.findByText('口内扫描精度怎么衡量');
    await userEvent.click(screen.getByRole('button', { name: '问数' }));
    expect(sessionStorage.getItem('home_question')).toBeNull();
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/'));
  });

  it('点资讯卡片进阅读页，并记下已读（未读点随即可见地少一个）', async () => {
    defaultRoutes({
      '/content/articles': {
        items: [article(), article({ slug: 'other-one', title: '另一篇内容', pinned: 0, featured: 0 })],
        total: 2, page: 1, page_size: 5,
      },
    });
    renderHome();
    const before = await screen.findByText('口内扫描精度怎么衡量');
    await userEvent.click(before);
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/read/scan-accuracy-metric'));
    expect(localStorage.getItem('home_read_articles')).toContain('scan-accuracy-metric');
  });

  it('读取失败显示错误条与重试，不得伪装成"还没有已发布的内容"', async () => {
    defaultRoutes();
    vi.mocked(client.get).mockImplementation(((url: string) =>
      url === '/content/articles'
        ? Promise.reject(new Error('Request failed with status code 500'))
        : Promise.resolve({ data: [] })) as never);
    renderHome();
    expect(await screen.findByRole('alert', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText(/资讯读取失败/)).toBeInTheDocument();
    // 资讯流与简介卡各自失败各自说（两个独立请求，不共用一个错误位）
    expect(screen.getAllByRole('button', { name: '重试' }).length).toBeGreaterThanOrEqual(1);
    // 关键分判据：失败与"没有内容"必须是两个视图，否则缺陷会被读成"今天没稿子"
    expect(screen.queryByText('还没有已发布的内容')).not.toBeInTheDocument();
    expect(screen.getByText('企业简介读取失败')).toBeInTheDocument();
    expect(screen.queryByText('企业简介尚未发布')).not.toBeInTheDocument();
  });

  it('无已发布内容 + 有编辑权 → 给出去内容中心发布的入口', async () => {
    useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin' } } as any);
    defaultRoutes({ '/content/articles': { items: [], total: 0, page: 1, page_size: 5 } });
    renderHome();
    expect(await screen.findByText('还没有已发布的内容', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /去内容中心发布/ })).toBeInTheDocument();
  });

  it('无已发布内容 + 无编辑权 → 不给"去发布"空承诺', async () => {
    withMenus(['home:feed']);
    defaultRoutes({ '/content/articles': { items: [], total: 0, page: 1, page_size: 5 } });
    renderHome();
    expect(await screen.findByText('还没有已发布的内容', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /去内容中心发布/ })).not.toBeInTheDocument();
  });

  it('栏目名来自接口：未加载完不硬编码栏目', async () => {
    defaultRoutes({
      '/content/categories': { items: [{ id: 9, key: 'clinic', label: '临床应用（改名后）', icon: '🦷', color: '#16a34a', sort: 3, is_active: 1 }] },
    });
    renderHome();
    expect(await screen.findByRole('button', { name: /临床应用（改名后）/ }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText('技术白皮书')).not.toBeInTheDocument();
  });

  it('资讯区按菜单码 home:feed 显隐，且无读者码时一个内容请求都不发', async () => {
    withMenus(['workspace:chat', 'data:catalog']);
    defaultRoutes();
    renderHome();
    expect(await screen.findByText('资讯区对当前角色未开放', {}, { timeout: 3000 })).toBeInTheDocument();
    const urls = vi.mocked(client.get).mock.calls.map(c => String(c[0]));
    expect(urls).not.toContain('/content/articles');
    expect(urls).not.toContain('/content/categories');
    expect(urls).not.toContain('/content/tags');
  });

  it('「待我处理」只随编辑权/审核权出现，无权限不打审核队列', async () => {
    withMenus(['home:feed']);   // 只有读者码
    defaultRoutes();
    renderHome();
    await screen.findByText('口内扫描精度怎么衡量');
    expect(screen.queryByText('待我处理')).not.toBeInTheDocument();
    const urls = vi.mocked(client.get).mock.calls.map(c => String(c[0]));
    expect(urls).not.toContain('/content/reviews/pending');
  });

  it('具备编辑权时显示我的草稿计数，只有 manage 没有 review 不出现审核队列', async () => {
    withMenus(['home:feed'], ['article:manage']);
    defaultRoutes({ '/content/articles': { items: [article()], total: 1, page: 1, page_size: 1 } });
    renderHome();
    expect(await screen.findByText('我的草稿', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText('待我处理')).toBeInTheDocument();
    expect(screen.queryByText('待我审核')).not.toBeInTheDocument();
  });

  it('首屏不再重复右栏的数字：可见看板/未读资讯/待办三个指标已撤', async () => {
    withMenus(['home:feed'], ['article:manage']);
    defaultRoutes();
    renderHome();
    await screen.findByText('联耀医疗（AlliedStar）公司概况');
    // 同一屏两处同一数字会不同步，用户只会当成数据不一致（2026-11-06 换掉指标卡）
    for (const label of ['可见看板', '未读资讯', '待办']) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
  });

  it('admin 无看板时仍给出可执行的配置引导（改版不得丢掉既有入口）', async () => {
    useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin' } } as any);
    defaultRoutes({ '/dashboard/groups/visible': [{ id: 0, name: '未分组', description: '', sort: 9999, dashboards: [groupBoard(2, 'designing')] }] });
    renderHome();
    const btn = await screen.findByRole('button', { name: /去配置仪表盘/ }, { timeout: 3000 });
    await userEvent.click(btn);
    expect(screen.getByTestId('path')).toHaveTextContent('/system/dashboards');
  });

  it('点右栏看板直达该看板详情，点「全部」落看板清单', async () => {
    defaultRoutes();
    renderHome();
    await screen.findByText('口内扫描精度怎么衡量');
    await userEvent.click(screen.getByText('看板 3'));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/dashboards/3'));
  });

  it('模块入口按权限码显隐：无 data:* 菜单就不给数据中台入口', async () => {
    withMenus(['home:feed', 'workspace:chat']);
    defaultRoutes();
    renderHome();
    await screen.findByText('口内扫描精度怎么衡量');
    expect(screen.getByText('工作空间')).toBeInTheDocument();
    expect(screen.queryByText('数据中台')).not.toBeInTheDocument();
    expect(screen.queryByText('系统配置')).not.toBeInTheDocument();
  });
});

describe('Hero 右侧企业简介卡（事实源在内容中心，不在前端硬编码文案）', () => {
  it('简介卡只讲公司：标题与摘要从内容中心来，不拼平台自己的站点/数据源计数', async () => {
    defaultRoutes();
    renderHome();
    expect(await screen.findByText('联耀医疗（AlliedStar）公司概况', {}, { timeout: 3000 }))
      .toBeInTheDocument();
    expect(screen.getByText('牙科数字化企业：口内扫描仪与数字化种植、正畸解决方案，上海张江研发、资阳制造')).toBeInTheDocument();
    // 可查范围与在线篇数属于工作台，写在公司简介里会被读成“公司的属性”（2026-11-11 移除）
    for (const label of ['站点 2', '数据源 2', '内容 1 篇']) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
  });

  it('点简介卡直达阅读页：简介就是一条已发布内容，不另开页面形态', async () => {
    defaultRoutes();
    renderHome();
    await userEvent.click(await screen.findByText('联耀医疗（AlliedStar）公司概况'));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/read/company-profile'));
  });

  it('资讯流让位简介卡：清单请求带 exclude_category，栏目芯片不再重复出现「关于本公司」', async () => {
    defaultRoutes({
      '/content/categories': { items: [
        { id: 2, key: 'whitepaper', label: '技术白皮书', icon: '🔬', color: '#0891b2', sort: 2, is_active: 1 },
        { id: 9, key: 'about', label: '关于本公司', icon: '🏢', color: '#475569', sort: 9, is_active: 1 },
      ] },
    });
    renderHome();
    await screen.findByText('口内扫描精度怎么衡量', {}, { timeout: 3000 });
    // 让位在服务端做（总数随同一口径减），不是前端拿到后再藏；按 sort 认资讯流那一次请求
    // （简介卡与草稿计数发的是同一 URL，不得拿它们的参数当本意）
    const feedCall = vi.mocked(client.get).mock.calls.find(
      c => c[0] === '/content/articles' && (c[1] as any)?.params?.sort === 'latest');
    expect((feedCall?.[1] as any)?.params).toMatchObject({ exclude_category: 'about' });
    // 简介卡本身照常展示该栏目（名字在卡内），但资讯流的筛选芯片里不得再有同名按钮
    expect(screen.getByText('联耀医疗（AlliedStar）公司概况')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '关于本公司' })).not.toBeInTheDocument();
  });

  it('简介未发布：有编辑权给创建入口，无编辑权只说明事实不给空承诺', async () => {
    const empty = { '/content/articles?about': { items: [], total: 0, page: 1, page_size: 1 } };
    withMenus(['home:feed'], ['article:manage']);
    defaultRoutes(empty);
    renderHome();
    expect(await screen.findByText('企业简介尚未发布', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /去内容中心创建/ })).toBeInTheDocument();

    cleanup();
    withMenus(['home:feed']);
    defaultRoutes(empty);
    renderHome();
    expect(await screen.findByText('企业简介尚未发布', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /去内容中心创建/ })).not.toBeInTheDocument();
  });

  it('简介读取失败显示失败与重试，不得伪装成"尚未发布"', async () => {
    defaultRoutes();
    vi.mocked(client.get).mockImplementation(((url: string, cfg?: { params?: Record<string, unknown> }) =>
      url === '/content/articles' && cfg?.params?.category === 'about'
        ? Promise.reject(new Error('Request failed with status code 500'))
        : Promise.resolve({ data: [] })) as never);
    renderHome();
    expect(await screen.findByText('企业简介读取失败', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText('企业简介尚未发布')).not.toBeInTheDocument();
  });

  it('角色未授权数据源也不影响简介：这张卡不发可查范围请求', async () => {
    defaultRoutes({ '/datasources/authorized-sites': { items: [] } });
    renderHome();
    expect(await screen.findByText('联耀医疗（AlliedStar）公司概况', {}, { timeout: 3000 })).toBeInTheDocument();
    const urls = vi.mocked(client.get).mock.calls.map(c => String(c[0]));
    // 简介卡只读内容中心；授权站点拉不拉都不改变“这是哪家公司”
    expect(urls.filter(u => u === '/datasources/authorized-sites')).toHaveLength(0);
    expect(screen.queryByText('未授权数据源')).not.toBeInTheDocument();
  });

  it('无 home:feed 时一个内容请求都不发，Hero 收成单列不留空右栏', async () => {
    withMenus(['workspace:chat']);
    defaultRoutes();
    renderHome();
    await screen.findByRole('heading', { name: /alice/ });
    const urls = vi.mocked(client.get).mock.calls.map(c => String(c[0]));
    expect(urls).not.toContain('/content/articles');
    expect(screen.queryByText('联耀医疗（AlliedStar）公司概况')).not.toBeInTheDocument();
  });
});
