import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation, useParams } from 'react-router-dom';
import {
  MessageSquare, LogOut, Menu, ChevronLeft, ChevronRight, X, ChevronDown, Palette, Folder,
  UserCircle, Brain, Check, ArrowRight, Clock, Archive, ChartNoAxesCombined, Trash2, Compass
} from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useAuthStore } from '../stores/authStore';
import { useThemeStore, applyTheme } from '../stores/themeStore';
import { useBrandStore } from '../stores/brandStore';
import { DEFAULT_APP_NAME } from '@/lib/brand';
import BrandLogo from './BrandLogo';
import { useWorkspaceStore, type Workspace } from '../stores/workspaceStore';
import { useMenuAccess } from '../hooks/useMenuAccess';
import { SidebarMenuSkeleton } from './SidebarMenuSkeleton';
import { pruneEmptySections } from '@/lib/menuUtils';
import SectionSwitcher from './SectionSwitcher';
import ReleaseNotesButton from './ReleaseNotesButton';
import { TOUR, navTour } from '@/lib/onboarding/guides';
import { useGuideStore } from '@/stores/guideStore';
import { WorkspaceDeleteDialog } from './WorkspaceDeleteDialog';
import { workspaceLandingPath } from './WorkspaceRoute';
import ThemeMenuItems from './ThemeMenuItems';

/** 将路由 key 转换为 menu_key: '/ws/3/chat' → 'workspace:chat' */
function toMenuKey(routeKey: string): string {
  return `workspace:${routeKey.split('/').pop()}`;
}

// ── Workspace Selector (sidebar-embedded) ─────────────────────────

/** 下拉项: 显示用 name, 默认工作空间不给删(仅可删自建空间) */
export function WorkspaceMenuItem({ ws, isCurrent, onSwitch, onAskDelete }: {
  ws: Workspace; isCurrent: boolean; onSwitch: () => void; onAskDelete: () => void;
}) {
  return (
    <DropdownMenuItem onClick={onSwitch} className="flex items-center gap-2">
      <span>{ws.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="truncate">{ws.name}</div>
        {ws.description && (
          <div className="text-xs text-muted-foreground truncate">{ws.description}</div>
        )}
      </div>
      {isCurrent && <Check className="h-4 w-4 shrink-0 text-primary" />}
      {ws.is_default ? (
        <span
          title="默认工作空间不可删除，仅可删除自行创建的自定义工作空间"
          className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground"
        >
          默认
        </span>
      ) : (
        <button
          type="button"
          aria-label={`删除工作空间 ${ws.name}`}
          title="删除工作空间"
          onPointerDown={e => e.preventDefault()}
          onClick={e => { e.preventDefault(); e.stopPropagation(); onAskDelete(); }}
          className="shrink-0 p-1 rounded text-muted-foreground/60 hover:text-destructive hover:bg-destructive/10 transition-colors"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
    </DropdownMenuItem>
  );
}

function WorkspaceSelectorSidebar({ collapsed, module = 'workspace' }: { collapsed: boolean; module?: 'workspace' | 'ask' }) {
  const { workspaces, currentWorkspaceId, loaded, setWorkspace, loadWorkspaces } = useWorkspaceStore();
  const navigate = useNavigate();
  const { workspaceId } = useParams();

  useEffect(() => {
    if (!useWorkspaceStore.getState().loaded) void loadWorkspaces();
  }, []);

  // Sync URL workspaceId to store
  useEffect(() => {
    if (workspaceId) {
      const id = Number(workspaceId);
      if (id && id !== currentWorkspaceId) {
        setWorkspace(id);
      }
    }
  }, [workspaceId]);

  const currentWs = workspaces.find(w => w.id === currentWorkspaceId);

  const handleSwitch = (ws: Workspace) => {
    setWorkspace(ws.id);
    // 切空间 = 进入另一个工作空间，落它的默认页（与登录/模块切换同一口径，现 = 智能问数）
    navigate(module === 'workspace' ? workspaceLandingPath(ws.id) : `/${module}/${ws.id}`);
  };

  // 「新建工作空间」入口按产品决策暂不开放（一个账号一个默认工作空间够用）：
  //   后端 POST /api/workspaces 与配额治理照旧存在，这里不挂入口；需要时去掉注释即可恢复。

  // 删除工作空间(仅自建空间): 确认框内先拉影响清单再让人确认（见 WorkspaceDeleteDialog）
  const [deleteTarget, setDeleteTarget] = useState<Workspace | null>(null);
  const afterWorkspaceDeleted = async (deletedId: number) => {
    const wasCurrent = deletedId === currentWorkspaceId;
    await loadWorkspaces();
    if (!wasCurrent) return;
    // 删的是当前正在用的空间 → 落回默认工作空间，不留空白路由（落点与登录/模块切换同一口径）
    const next = useWorkspaceStore.getState().getDefaultWorkspaceId();
    if (next) {
      setWorkspace(next);
      navigate(module === 'workspace' ? workspaceLandingPath(next) : `/${module}/${next}`);
    } else {
      navigate('/dashboards');
    }
  };
  const deleteDialog = (
    <WorkspaceDeleteDialog
      workspace={deleteTarget} open={!!deleteTarget}
      onOpenChange={open => { if (!open) setDeleteTarget(null); }}
      onDeleted={afterWorkspaceDeleted}
    />
  );

  // 只有一个工作空间时不挂这个下拉（2026-11-12 拍板）：新建入口按产品决策不开放，
  //   一个账号一个默认空间，这里既无可选项也无可切换目标，摆着就是“点了只看到自己”
  //   的空承诺（与「决策建议不占一级入口」同口径）。出现第二个空间（自建/区域分域）
  //   自动回来；删除自建空间的入口在系统配置「工作空间管理」共用同一个确认框，
  //   不会因为收起而丢能力。上面的 loadWorkspaces / URL 同步必须照常跑（路由与
  //   currentWorkspaceId 依赖它），所以判定放在 hooks 之后。
  if (loaded && workspaces.length <= 1) return null;

  if (collapsed) {
    return (
      <>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button aria-label="切换工作空间" data-tour={TOUR.wsSelector} className="w-full flex items-center justify-center py-3 hover:bg-sidebar-accent/50 transition-colors">
              <span className="text-lg">{currentWs?.icon || '📊'}</span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="right" align="start" className="w-56">
            {workspaces.map(ws => (
              <WorkspaceMenuItem
                key={ws.id} ws={ws} isCurrent={ws.id === currentWorkspaceId}
                onSwitch={() => handleSwitch(ws)} onAskDelete={() => setDeleteTarget(ws)}
              />
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        {deleteDialog}
      </>
    );
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button aria-label="切换工作空间" data-tour={TOUR.wsSelector} className={`w-full min-w-0 flex items-center gap-2 px-3 py-2 hover:bg-muted/50 transition-colors ${module === 'workspace' ? 'border-b border-sidebar-border' : 'rounded-md'}`}>
            <span className="text-lg flex-shrink-0">{currentWs?.icon || '📊'}</span>
            <div className="flex-1 min-w-0 text-left">
              <div className="text-sm font-medium truncate text-sidebar-foreground">
                {currentWs?.name || '选择工作空间'}
              </div>
            </div>
            <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-sidebar-foreground/50" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="start" className="w-56">
          {workspaces.map(ws => (
            <WorkspaceMenuItem
              key={ws.id} ws={ws} isCurrent={ws.id === currentWorkspaceId}
              onSwitch={() => handleSwitch(ws)} onAskDelete={() => setDeleteTarget(ws)}
            />
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {deleteDialog}
    </>
  );
}

// ── Workspace Layout ──────────────────────────────────────────────

export default function WorkspaceLayout({ module = 'workspace' }: { module?: 'workspace' | 'ask' }) {
  const independent = module !== 'workspace';
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { workspaceId } = useParams();
  const { user, logout } = useAuthStore();
  const { theme } = useThemeStore();
  const { brand, fetchBrand } = useBrandStore();
  const startGuide = useGuideStore(s => s.startGuide);

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    fetchBrand();
  }, [fetchBrand]);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname]);

  // 菜单可见性按权限码裁决（与 SystemLayout/DataPlatformLayout 同口径）
  // 「云空间」段的资产清单同样参与菜单配置（菜单登记 workspace:assets ↔ asset:read，
  // 默认已授给全部生效角色，管理员可按角色收回）——旧「恒可见」例外（产品决策 2026-10）
  // 已随本轮改名废除，否则角色页出现“该配的看不到”的空承诺。
  // 数据看板入口同口径（workspace:dashboards ↔ dashboard:read），内容级可见性仍由
  // adh_role_dashboard_access 在数据层 fail-closed 裁决。一级分组下无可访问项时不渲染分组标题。
  // 权限清单未就绪时不渲染真实菜单（isMenuAllowed 未加载时 fail-open，
  // 会把无权限的分组先画出来再抽走 → 菜单闪一下消失），改为同高骨架
  const menuAccess = useMenuAccess();
  const menuItems = menuAccess.ready ? pruneEmptySections([
    { section: '数据分析' },
    { key: `/ws/${workspaceId}/chat`, icon: MessageSquare, label: '智能问数' },
    { key: `/ws/${workspaceId}/dashboards`, icon: ChartNoAxesCombined, label: '数据看板' },
    { key: `/ws/${workspaceId}/reports`, icon: Folder, label: '报告中心' },
    /* 决策建议不在工作空间侧栏单独占一项（2026-11-12 拍板）：不是每个问题都需要决策，
       把它做成一级入口会暗示“每轮都该看建议”。消费入口仍是 Chat 建议卡、看板控件
       与通知里的实例链接；/ws/:id/decisions 路由保留（那些链接的落地面，菜单码
       workspace:decisions 同步下架，见 decision_tree_migration.sql）。配置面在数据中台。 */
    { section: '云空间' },
    { key: `/ws/${workspaceId}/assets`, icon: Archive, label: '资产清单' },
    { section: '自动化' },
    { key: `/ws/${workspaceId}/scheduled`, icon: Clock, label: '任务调度' },
  ].filter(item => !('key' in item) || menuAccess.allowed(toMenuKey((item as any).key)))) : [];

  const currentPath = location.pathname;
  // 顶栏左侧过去只在独立模块下才填内容：看板详情这类自带页头的子页，顶栏空成一条白带，
  // 与页头叠起来就是“两条光秃秃的横杠”。补当前菜单面包屑（图标 + 名称）。
  // 只在菜单**子路径**上出现（key + '/'）：菜单根页侧栏已高亮，再写一遍只是噪声。
  const currentMenu = menuItems.find(m => 'key' in m && currentPath.startsWith(`${(m as any).key}/`)) as
    { label: string; icon: typeof Clock } | undefined;

  const handleNavigate = (path: string) => {
    navigate(path);
    setMobileMenuOpen(false);
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop Sidebar */}
      {!independent && <div className={`hidden lg:flex flex-col min-h-0 bg-sidebar border-r border-sidebar-border transition-all duration-200 ${collapsed ? 'w-[64px]' : 'w-[220px]'}`}>
        {/* Logo */}
        <div className="h-12 flex items-center justify-center border-b border-sidebar-border gap-1.5">
          <BrandLogo brand={brand} size={collapsed ? 20 : 24} />
          {!collapsed && brand.show_text && (
            <span className="font-bold text-xl text-sidebar-foreground truncate">{brand.app_name || DEFAULT_APP_NAME}</span>
          )}
        </div>

        {/* Workspace Selector */}
        <WorkspaceSelectorSidebar collapsed={collapsed} />

        {/* Menu — min-h-0 allows flex child to shrink below content size, enabling scroll */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <ScrollArea className="h-full py-2">
          <nav className="space-y-1 px-2" role="navigation" aria-label="工作空间导航">
            {/* Static menu items */}
            {!menuAccess.ready && <SidebarMenuSkeleton rows={6} collapsed={collapsed} />}
            {menuItems.map((item, idx) => {
              if ('section' in item) {
                if (!item.section) return <div key={idx} className="my-2 mx-2 border-t border-sidebar-border" />;
                if (collapsed) return <div key={idx} className="my-2 mx-2 border-t border-sidebar-border" />;
                return (
                  <div key={idx} className="px-3 pt-5 pb-1.5">
                    <span className="text-sm font-medium uppercase tracking-[0.08em] text-sidebar-foreground/50">{item.section}</span>
                  </div>
                );
              }
              const Icon = item.icon!;
              const isActive = currentPath === item.key || currentPath.startsWith(item.key + '/');
              return (
                <Tooltip key={item.key} delayDuration={0}>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => handleNavigate(item.key!)}
                      data-tour={navTour('ws', item.key!)}
                      className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors duration-200
                        ${isActive
                          ? 'bg-primary text-primary-foreground font-medium'
                          : 'text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground'
                        } ${collapsed ? 'justify-center' : ''}`}
                    >
                      <Icon className="h-4 w-4 flex-shrink-0" />
                      {!collapsed && <span className="truncate">{item.label}</span>}
                    </button>
                  </TooltipTrigger>
                  {collapsed && <TooltipContent side="right">{item.label}</TooltipContent>}
                </Tooltip>
              );
            })}
          </nav>
        </ScrollArea>
        </div>

        {/* Bottom Navigation — module switching moved to header (see SectionSwitcher) */}
        <div className="p-2 border-t border-sidebar-border">
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-center text-sidebar-foreground/70 hover:text-sidebar-foreground"
            onClick={() => setCollapsed(!collapsed)}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
        </div>
      </div>}

      {/* Mobile Menu Overlay */}
      {!independent && mobileMenuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobileMenuOpen(false)} />
          <div className="relative w-[280px] h-full bg-sidebar border-r border-sidebar-border flex flex-col min-h-0">
            <div className="h-12 flex items-center justify-between px-4 border-b border-sidebar-border">
              <span className="font-bold text-lg text-sidebar-foreground">{brand.app_name || DEFAULT_APP_NAME}</span>
              <Button variant="ghost" size="sm" className="h-9 w-9 p-0" onClick={() => setMobileMenuOpen(false)}>
                <X className="h-5 w-5" />
              </Button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
            <ScrollArea className="h-full py-3">
              <nav className="space-y-1 px-3">
                {!menuAccess.ready && <SidebarMenuSkeleton rows={6} />}
                {menuItems.map((item, idx) => {
                  if ('section' in item) {
                    if (!item.section) return <div key={idx} className="my-2 border-t border-sidebar-border" />;
                    return (
                      <div key={idx} className="px-4 pt-5 pb-1.5">
                        <span className="text-sm font-semibold text-sidebar-foreground/60">{item.section}</span>
                      </div>
                    );
                  }
                  const Icon = item.icon!;
                  const isActive = currentPath === item.key;
                  return (
                    <button
                      key={item.key}
                      onClick={() => handleNavigate(item.key!)}
                      className={`w-full flex items-center gap-3 px-4 py-3 rounded-md text-sm min-h-[44px]
                        ${isActive
                          ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                          : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
                        }`}
                    >
                      <Icon className="h-5 w-5 flex-shrink-0" />
                      <span>{item.label}</span>
                    </button>
                  );
                })}
                {user?.role === 'admin' && (
                  <button
                    onClick={() => handleNavigate('/system')}
                    className="w-full flex items-center gap-3 px-4 py-3 rounded-md text-sm text-sidebar-foreground/70 hover:bg-sidebar-accent/50"
                  >
                    <LucideIcons.Settings className="h-5 w-5 flex-shrink-0" />
                    <span>系统配置</span>
                    <ArrowRight className="h-4 w-4 ml-auto opacity-50" />
                  </button>
                )}
              </nav>
            </ScrollArea>
            </div>
            {/* User info */}
            <div className="p-4 border-t border-sidebar-border">
              <div className="flex items-center gap-3">
                <Avatar className="h-8 w-8">
                  <AvatarFallback className="text-sm">{user?.username?.charAt(0)?.toUpperCase() || 'U'}</AvatarFallback>
                </Avatar>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{user?.username}</div>
                  <div className="text-xs text-muted-foreground">{user?.role}</div>
                </div>
                <Button variant="ghost" size="sm" className="h-9 w-9 p-0" onClick={() => { logout(); navigate('/login'); }}>
                  <LogOut className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main area */}
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        <header className="h-12 flex items-center justify-between px-4 border-b border-border bg-card flex-shrink-0">
          <div className="flex min-w-0 items-center gap-2">
            {!independent && <Button variant="ghost" size="sm" className="lg:hidden h-9 w-9 p-0" onClick={() => setMobileMenuOpen(true)}><Menu className="h-5 w-5" /></Button>}
            {independent && <>
              <span className="hidden shrink-0 font-semibold sm:inline">{brand.app_name || DEFAULT_APP_NAME}</span>
              <span className="hidden text-muted-foreground sm:inline">/</span>
              <span className="shrink-0 text-sm font-medium">工作空间</span>
              <div className="min-w-0 max-w-56"><WorkspaceSelectorSidebar collapsed={false} module={module} /></div>
            </>}
            {!independent && currentMenu && <>
              {/* 子路径顶栏与「数据看板」列表页页头同一格式（同一 lockup：徽标 + gap-2.5 + text-lg 半粗），
                  图标仍取菜单注册表（与侧栏同源），不另画一套；徽标走主题变量，9 套主题下都成立。
                  （用户拍板：面包屑小字版太单薄，两级页面标题要同构） */}
              <span className="flex shrink-0 items-center gap-2.5">
                <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary ring-1 ring-primary/20">
                  <currentMenu.icon className="h-4 w-4" />
                </span>
                <span className="shrink-0 text-lg font-semibold tracking-tight">{currentMenu.label}</span>
              </span>
            </>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {/* 版本公告: 与模块切换器左侧常驻(三套布局同口径) */}
            <ReleaseNotesButton />

            {/* Module switcher — 工作空间 / 数据中台 / 系统配置 */}
            <SectionSwitcher current={module} />

            {/* Theme selector */}
            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label="主题风格" data-tour={TOUR.headerTheme}>
                      <Palette className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent>主题风格</TooltipContent>
              </Tooltip>
              <DropdownMenuContent align="end" className="w-48">
                <ThemeMenuItems />
              </DropdownMenuContent>
            </DropdownMenu>

            {/* User menu */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button aria-label="用户菜单" data-tour={TOUR.headerUser} className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors min-h-[36px]">
                  <Avatar className="h-6 w-6">
                    <AvatarFallback className="text-xs">{user?.username?.charAt(0)?.toUpperCase() || 'U'}</AvatarFallback>
                  </Avatar>
                  <span className="text-sm font-medium hidden sm:inline">{user?.username}</span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {/* 手动重看入口: 不受 guide:all 静音影响, 永远可用 */}
                <DropdownMenuItem onClick={() => startGuide('welcome')}>
                  <Compass className="h-4 w-4 mr-2" />
                  新手引导
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => navigate(module === 'workspace' ? `/ws/${workspaceId}/profile` : `/${module}/${workspaceId}/profile`)}>
                  <UserCircle className="h-4 w-4 mr-2" />
                  个人设置
                </DropdownMenuItem>
                {/* 记忆入口: AI 在对话里说"已记住"后, 用户必须能在**同一场景**核对/删除,
                    而不是只能去系统配置页或手输 URL */}
                <DropdownMenuItem onClick={() => navigate(module === 'workspace' ? `/ws/${workspaceId}/memory` : `/${module}/${workspaceId}/memory`)}>
                  <Brain className="h-4 w-4 mr-2" />
                  我的记忆
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => { logout(); navigate('/login'); }}>
                  <LogOut className="h-4 w-4 mr-2" />
                  退出登录
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        {/* relative 与 SystemLayout 同口径：页面内 position:absolute 且祖先链无定位元素的后代
            （如 sr-only 标签）以 ICB 为包含块，会越过本口的裁剪把文档撑高 —— 表现为整个系统页面被滚动。 */}
        <main className="relative min-h-0 min-w-0 flex-1 overflow-auto bg-background">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
