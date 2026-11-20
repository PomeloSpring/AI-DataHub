import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  Database, FileText, Settings, LogOut, Menu, Palette, UserCircle, X, ChevronLeft, ChevronRight,
  GitBranch, RefreshCw, Activity, Shield, ShieldCheck, Ruler, Brain, Boxes, Terminal, Layers,
  FunctionSquare, Network, BarChart3, Compass, Workflow, Signpost
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

/** 将路由 key 转换为 menu_key: '/data/ontology' → 'data:ontology' */
function toMenuKey(routeKey: string): string {
  return routeKey.replace('/data/', 'data:').replace(/^\//, '');
}

/**
 * 侧栏菜单：`labelKey` / `section` 都是 menu 语言包的 key（渲染期解析，切语言即时重渲染）。
 * key 与 adh_menu_registry.menu_key 逐字对应；显隐反查仍用 menu_key（权限码口径不动）。
 */
const DATA_PLATFORM_MENU_ITEMS = [
  { section: 'section.dataSources' },
  { key: '/data/datasources', icon: Database, labelKey: 'data.datasources' },
  { key: '/data/tables', icon: FileText, labelKey: 'data.tables' },
  { key: '/data/playground', icon: Terminal, labelKey: 'data.playground' },
  { section: 'section.catalog' },
  { key: '/data/datasets', icon: Layers, labelKey: 'data.datasets' },
  /* 菜单文案与 adh_menu_registry.label 同步（三者一个名字）：旧值「数据建模」与库内「本体工作区」飘移 */
  { key: '/data/ontology', icon: Boxes, labelKey: 'data.ontology' },
  /* 建模待办已并入「本体建模」的左侧「跨模型」分区（页内页签），不再占侧栏菜单位；
     菜单/权限点登记同批退出，见 docker/mysql/asset_pool_retire_migration.sql */
  /* SQL 示例对已下线（与数据集重合），菜单登记与权限点同批退出：
     见 docker/mysql/sql_pairs_retire_migration.sql 与 App.tsx 的路由注释 */
  /* 分析场景(原「快速路径」, 系统配置迁入): 会改变 AI 取数行为的语义层语料, 归数据中台 */
  { key: '/data/scenario', icon: Workflow, labelKey: 'data.scenario' },
  { key: '/data/metrics', icon: BarChart3, labelKey: 'data.metrics' },
  { key: '/data/knowledge-graph', icon: Network, labelKey: 'data.knowledgeGraph' },
  /* 决策建议（配置面）：条件引用指标/维度字典，本质是口径资产，与本段的
     「指标与维度」「本体建模」同族（不放系统配置——那是一级 AI 模板/凭据域）。
     菜单登记 data:decisions ↔ decision:manage，sort=10 排在本段最后。 */
  { key: '/data/decisions', icon: Signpost, labelKey: 'data.decisions' },
  { section: 'section.security' },
  { key: '/data/sensitive', icon: Shield, labelKey: 'data.sensitive' },
  { key: '/data/rls', icon: ShieldCheck, labelKey: 'data.rls' },
  { section: 'section.quality' },
  { key: '/data/quality', icon: Activity, labelKey: 'data.quality' },
  { key: '/data/quality/rules', icon: Settings, labelKey: 'data.qualityRules' },
  { key: '/data/lineage', icon: GitBranch, labelKey: 'data.lineage' },
  { key: '/data/standards', icon: Ruler, labelKey: 'data.standards' },
  { section: 'section.sync' },
  { key: '/data/sync', icon: RefreshCw, labelKey: 'data.sync' },
  { key: '/data/sync/logs', icon: FileText, labelKey: 'data.syncLogs' },
  { key: '/data/udfs', icon: FunctionSquare, labelKey: 'data.udfs' },
];

/** 骨架行数 = 菜单项总数（权限未就绪时的占位高度贴近真实菜单上限） */
const MENU_SKELETON_ROWS = DATA_PLATFORM_MENU_ITEMS.filter(item => 'key' in item).length;

export default function DataPlatformLayout() {
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
  const startGuide = useGuideStore(s => s.startGuide);

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => { fetchBrand(); }, [fetchBrand]);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname]);

  const currentPath = location.pathname;
  // 按权限过滤菜单; 一级分组下无可访问项时不渲染该分组标题
  // 权限清单未就绪时不渲染真实菜单（isMenuAllowed 未加载时 fail-open 会把
  // 无权限项先画出来再抽走），改为同高骨架
  const visibleMenu = menuAccess.ready ? pruneEmptySections(
    DATA_PLATFORM_MENU_ITEMS.filter(item => !('key' in item) || menuAccess.allowed(toMenuKey((item as any).key)))
  ) : [];

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop Sidebar */}
      <div className={`hidden lg:flex flex-col h-full bg-sidebar border-r border-sidebar-border transition-all duration-200 ${collapsed ? 'w-[64px]' : 'w-[220px]'} overflow-hidden`}>
        {/* Logo */}
        <div className="h-12 flex items-center justify-center border-b border-sidebar-border gap-1.5">
          <BrandLogo brand={brand} size={24} />
          {!collapsed && (
            <span className="font-bold text-sm text-sidebar-foreground truncate">{t("k_4779f75f79")}</span>
          )}
        </div>

        {/* Menu */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <ScrollArea className="h-full py-2">
            <nav className="space-y-1 px-2" role="navigation" aria-label={t("k_ad4b924576")}>
              {!menuAccess.ready && <SidebarMenuSkeleton rows={MENU_SKELETON_ROWS} collapsed={collapsed} />}
              {visibleMenu.map((item, idx) => {
                if ('section' in item) {
                  if (collapsed) return <div key={idx} className="my-2 mx-2 border-t border-sidebar-border" />;
                  return (
                    <div key={idx} className="px-3 pt-5 pb-1.5">
                      <span className="text-sm font-medium uppercase tracking-[0.08em] text-sidebar-foreground/50">{item.section ? tMenu(item.section) : ''}</span>
                    </div>
                  );
                }
                const Icon = item.icon!;
                // 前缀匹配时排除存在更深层菜单项的情况，避免父子路由同时高亮（如 质量概览/质量规则、同步任务/执行日志）
                const hasDeeperMenuItem = visibleMenu.some(
                  o => 'key' in o && o.key!.startsWith(item.key! + '/') && currentPath.startsWith(o.key!)
                );
                const isActive = currentPath === item.key || (currentPath.startsWith(item.key! + '/') && !hasDeeperMenuItem);
                return (
                  <Tooltip key={item.key} delayDuration={0}>
                    <TooltipTrigger asChild>
                      <button
                        onClick={() => navigate(item.key!)}
                        data-tour={navTour('dp', item.key!)}
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
              <span className="font-bold text-lg text-sidebar-foreground">{t("k_4779f75f79")}</span>
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
                        <span className="text-sm font-medium uppercase tracking-[0.08em] text-sidebar-foreground/50">{item.section ? tMenu(item.section) : ''}</span>
                      </div>
                    );
                  }
                  const Icon = item.icon!;
                  const isActive = currentPath === item.key;
                  return (
                    <button
                      key={item.key}
                      onClick={() => { navigate(item.key!); setMobileMenuOpen(false); }}
                      data-tour={navTour('dp', item.key!)}
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
            <SectionSwitcher current="data" />

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
                <DropdownMenuItem onClick={() => navigate('/data/profile')}>
                  <UserCircle className="h-4 w-4 mr-2" />
                  {tMenu('userMenu.profile')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => navigate('/data/memory')}>
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
        {/* min-h-0 与 SystemLayout 同口径（flex 子项默认 min-height:auto 会被内容顶高，页内滚动口就失效）；
            relative 是兜底护栏：页面内 position:absolute 且祖先链无定位元素的后代（如 sr-only 标签）
            以 ICB 为包含块，会越过本口的裁剪把文档撑高 —— 表现为整个系统页面被滚动。 */}
        <main className="relative min-h-0 flex-1 overflow-auto bg-background p-6">
          <Outlet />
        </main>
      </div>
      <AsBotPanel />
    </div>
  );
}
