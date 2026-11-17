import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  Database, FileText, BookOpen, Users, Brain, Bot, Settings, LogOut, Menu, Palette, UserCircle, X,
  ChevronLeft, ChevronRight, Clock, Bell, BarChart3, Shield, GitBranch, Eye, Activity, Server,
  Sparkles, Waypoints, LayoutTemplate, FlaskConical, Dna, Megaphone, Compass
} from 'lucide-react';
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
import BrandLogo from './BrandLogo';
import { useWorkspaceStore } from '../stores/workspaceStore';
import SectionSwitcher from './SectionSwitcher';
import ReleaseNotesButton from './ReleaseNotesButton';
import { TOUR, navTour } from '@/lib/onboarding/guides';
import { useGuideStore } from '@/stores/guideStore';
import AsBotButton from './AsBotButton';
import AsBotPanel from './asbot/AsBotPanel';
import { useMenuAccess } from '../hooks/useMenuAccess';
import { SidebarMenuSkeleton } from './SidebarMenuSkeleton';
import { pruneEmptySections } from '@/lib/menuUtils';
import ThemeMenuItems from './ThemeMenuItems';
import LanguageMenu from './LanguageMenu';
import { useTranslation } from 'react-i18next';

/** 将路由 key 转换为 menu_key: '/system/models' → 'system:models' */
function toMenuKey(routeKey: string): string {
  return routeKey.replace('/system/', 'system:').replace(/^\//, '');
}

/**
 * 侧栏菜单：`labelKey` / `section` 都是 menu 语言包的 key（渲染期解析，切语言即时重渲染）。
 * key 与 adh_menu_registry.menu_key 逐字对应；显隐反查仍用 menu_key（权限码口径不动）。
 */
const SYSTEM_MENU_ITEMS = [
  { section: 'section.ai' },
  { key: '/system/models', icon: Brain, labelKey: 'system.models' },
  { key: '/system/mcp', icon: Server, labelKey: 'system.mcp' },
  { key: '/system/as-bots', icon: Bot, labelKey: 'system.asBots' },
  { key: '/system/skills', icon: Sparkles, labelKey: 'system.skills' },
  { key: '/system/config-versions', icon: GitBranch, labelKey: 'system.promptConfig', hidden: true },
  { section: 'section.knowledge' },
  /* 知识库**管理**(连接/类型/同步目标)留系统配置; 知识库内容的查看与编辑在内容中心 */
  { key: '/system/knowledge-base', icon: BookOpen, labelKey: 'system.knowledgeBase' },
  { section: 'section.visual' },
  { key: '/system/dashboards', icon: BarChart3, labelKey: 'system.dashboards' },
  { key: '/system/vis-library', icon: LayoutTemplate, labelKey: 'system.visLibrary' },
  // 菜单名与 adh_menu_registry 的 label 同步改名（模板中心：报告版式 + IM/邮件/短信文案）
  { key: '/system/report-templates', icon: FileText, labelKey: 'system.reportTemplates' },
  { section: 'section.integration' },
  { key: '/system/notification-channels', icon: Bell, labelKey: 'system.notificationChannels' },
  { section: 'section.access' },
  { key: '/system/users', icon: Users, labelKey: 'system.users' },
  { key: '/system/workspaces', icon: Database, labelKey: 'system.workspaces' },
  { key: '/system/roles', icon: Shield, labelKey: 'system.roles' },
  { key: '/system/audit', icon: Eye, labelKey: 'system.audit' },
  { section: 'section.ops' },
  { key: '/system/monitoring', icon: Activity, labelKey: 'system.monitoring' },
  { key: '/system/task-monitor', icon: Clock, labelKey: 'system.taskMonitor' },
  { key: '/system/sandbox', icon: Server, labelKey: 'system.sandbox' },
  { key: '/system/observability', icon: Waypoints, labelKey: 'system.observability' },
  { key: '/system/eval', icon: FlaskConical, labelKey: 'system.eval' },
  { key: '/system/evolution', icon: Dna, labelKey: 'system.evolution' },
  { section: 'section.system' },
  // 与迁移里的菜单登记同一位置(menu_key='system:release-notes', section='系统', sort=75)
  { key: '/system/release-notes', icon: Megaphone, labelKey: 'system.releaseNotes' },
  { key: '/system/settings', icon: Settings, labelKey: 'system.settings' },
];

/** 骨架行数 = 菜单项总数（权限未就绪时的占位高度贴近真实菜单上限） */
const MENU_SKELETON_ROWS = SYSTEM_MENU_ITEMS.filter(item => 'key' in item).length;

export default function SystemLayout() {
  const { t } = useTranslation('ui');
  // 菜单 key 由常量数据驱动（动态 key），签名放宽以免逐处断言；重渲染订阅仍由 useTranslation 提供
  const tMenu = useTranslation('menu').t as unknown as (key: string) => string;
  const [collapsed, setCollapsed] = useState(false);
  const menuAccess = useMenuAccess();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const { theme } = useThemeStore();
  const { brand, fetchBrand } = useBrandStore();
  const { getDefaultWorkspaceId } = useWorkspaceStore();
  const startGuide = useGuideStore(s => s.startGuide);

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => { fetchBrand(); }, [fetchBrand]);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname]);

  // Redirect non-admin users
  useEffect(() => {
    if (user && user.role !== 'admin') {
      const wsId = getDefaultWorkspaceId();
      navigate(`/ws/${wsId}/chat`, { replace: true });
    }
  }, [user]);

  const currentPath = location.pathname;
  // 按权限过滤菜单; 一级分组下无可访问项时不渲染该分组标题
  const visibleMenu = menuAccess.ready ? pruneEmptySections(
    SYSTEM_MENU_ITEMS.filter(item => !('key' in item) || (!(item as any).hidden && menuAccess.allowed(toMenuKey((item as any).key))))
  ) : [];

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop Sidebar */}
      <div className={`hidden lg:flex flex-col h-full bg-sidebar border-r border-sidebar-border transition-all duration-200 ${collapsed ? 'w-[64px]' : 'w-[220px]'} overflow-hidden`}>
        {/* Logo */}
        <div className="h-12 flex items-center justify-center border-b border-sidebar-border gap-1.5">
          <BrandLogo brand={brand} size={24} />
          {!collapsed && (
            <span className="font-bold text-sm text-sidebar-foreground truncate">{t("k_3c71dca8a0")}</span>
          )}
        </div>

        {/* Menu — min-h-0 allows flex child to shrink below content size, enabling scroll */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <ScrollArea className="h-full py-2">
          <nav className="space-y-1 px-2" role="navigation" aria-label={t("k_129feba814")}>
            {/* 权限清单未就绪时先给骨架，避开无权限菜单先画出再抽走的闪现 */}
            {!menuAccess.ready && <SidebarMenuSkeleton rows={MENU_SKELETON_ROWS} collapsed={collapsed} />}
            {visibleMenu.map((item, idx) => {
              if ('section' in item) {
                if (collapsed) return <div key={idx} className="my-2 mx-2 border-t border-sidebar-border" />;
                return (
                  <div key={idx} className="px-3 pt-5 pb-1.5">
                    <span className="text-sm font-medium uppercase tracking-[0.08em] text-sidebar-foreground/50">
                      {item.section ? tMenu(item.section) : ''}
                    </span>
                  </div>
                );
              }
              const Icon = item.icon!;
              const isActive = currentPath === item.key || currentPath.startsWith(item.key! + '/');
              return (
                <Tooltip key={item.key} delayDuration={0}>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => navigate(item.key!)}
                      data-tour={navTour('sys', item.key!)}
                      className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors duration-200
                        ${isActive
                          ? 'bg-primary text-primary-foreground font-medium'
                          : 'text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground'
                        } ${collapsed ? 'justify-center' : ''}`}
                    >
                      <Icon className="h-4 w-4 flex-shrink-0" />
                      {!collapsed && <span className="truncate">{tMenu(item.labelKey)}</span>}
                    </button>
                  </TooltipTrigger>
                  {collapsed && <TooltipContent side="right">{tMenu(item.labelKey)}</TooltipContent>}
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
      </div>

      {/* Mobile Menu */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobileMenuOpen(false)} />
          <div className="relative w-[280px] h-full bg-sidebar border-r border-sidebar-border flex flex-col">
            <div className="h-12 flex items-center justify-between px-4 border-b border-sidebar-border">
              <span className="font-bold text-lg text-sidebar-foreground">{t("k_3c71dca8a0")}</span>
              <Button variant="ghost" size="sm" className="h-9 w-9 p-0" onClick={() => setMobileMenuOpen(false)}>
                <X className="h-5 w-5" />
              </Button>
            </div>
            <ScrollArea className="flex-1 py-3">
              <nav className="space-y-1 px-3">
                {!menuAccess.ready && <SidebarMenuSkeleton rows={MENU_SKELETON_ROWS} />}
                {visibleMenu.map((item, idx) => {
                  if ('section' in item) {
                    return (
                      <div key={idx} className="px-4 pt-5 pb-1.5">
                        <span className="text-sm font-semibold text-sidebar-foreground/60">
                          {item.section ? tMenu(item.section) : ''}
                        </span>
                      </div>
                    );
                  }
                  const Icon = item.icon!;
                  const isActive = currentPath === item.key;
                  return (
                    <button
                      key={item.key}
                      onClick={() => { navigate(item.key!); setMobileMenuOpen(false); }}
                      data-tour={navTour('sys', item.key!)}
                      className={`w-full flex items-center gap-3 px-4 py-3 rounded-md text-sm min-h-[44px]
                        ${isActive
                          ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                          : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
                        }`}
                    >
                      <Icon className="h-5 w-5 flex-shrink-0" />
                      <span>{tMenu(item.labelKey)}</span>
                    </button>
                  );
                })}
              </nav>
            </ScrollArea>
          </div>
        </div>
      )}

      {/* Main area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="h-12 flex items-center justify-between px-4 border-b border-border bg-card flex-shrink-0">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" className="lg:hidden h-9 w-9 p-0" onClick={() => setMobileMenuOpen(true)}>
              <Menu className="h-5 w-5" />
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <AsBotButton />
            {/* 版本公告: 模块切换器左侧常驻(三套布局同口径) */}
            <ReleaseNotesButton />
            {/* Module switcher — 工作空间 / 数据中台 / 系统配置 */}
            <SectionSwitcher current="system" />

            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label={t("k_297ca007c9")} data-tour={TOUR.headerTheme}>
                      <Palette className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent>{t("k_297ca007c9")}</TooltipContent>
              </Tooltip>
              <DropdownMenuContent align="end" className="w-48">
                <ThemeMenuItems />
              </DropdownMenuContent>
            </DropdownMenu>
            {/* 语言切换：顶栏常驻，与主题按钮并排（四套布局同口径） */}
            <LanguageMenu />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button aria-label={t("k_a375a2cf71")} data-tour={TOUR.headerUser} className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors min-h-[36px]">
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
                  {tMenu('userMenu.guide')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => navigate('/system/profile')}>
                  <UserCircle className="h-4 w-4 mr-2" />
                  {tMenu('userMenu.profile')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => navigate('/system/memory')}>
                  <Brain className="h-4 w-4 mr-2" />
                  {tMenu('userMenu.memory')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => { logout(); navigate('/login'); }}>
                  <LogOut className="h-4 w-4 mr-2" />
                  {tMenu('userMenu.logout')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        {/* min-h-0/min-w-0 与 WorkspaceLayout 同口径：flex 子项默认 min-height:auto 会被内容顶高，
            页内“仅内容区滚动”的全高布局就会退化成整页滚动（页头/工具条被卷走）。
            relative 是兜底护栏：页面内 position:absolute 且祖先链无定位元素的后代（如 sr-only 标签）
            默认以 ICB 为包含块，会越过本口的 overflow 裁剪把文档撑高，滚轮就会连侧栏/顶栏一起卷走。 */}
        <main className="relative min-h-0 min-w-0 flex-1 overflow-auto bg-background p-6">
          <Outlet />
        </main>
      </div>
      <AsBotPanel />
    </div>
  );
}
