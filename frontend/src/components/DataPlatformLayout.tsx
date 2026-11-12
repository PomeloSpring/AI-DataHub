import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  Database, FileText, Settings, LogOut, Menu, Palette, UserCircle, X, ChevronLeft, ChevronRight,
  GitBranch, RefreshCw, Activity, Shield, ShieldCheck, Ruler, Brain, Boxes, Terminal, Layers,
  FunctionSquare, Network, BarChart3, Inbox, Compass, Workflow, Signpost
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

/** 将路由 key 转换为 menu_key: '/data/ontology' → 'data:ontology' */
function toMenuKey(routeKey: string): string {
  return routeKey.replace('/data/', 'data:').replace(/^\//, '');
}

const DATA_PLATFORM_MENU_ITEMS = [
  { section: '数据源' },
  { key: '/data/datasources', icon: Database, label: '数据源管理' },
  { key: '/data/tables', icon: FileText, label: '表 & 字段' },
  { key: '/data/playground', icon: Terminal, label: 'SQL Playground' },
  { section: '数据目录' },
  { key: '/data/datasets', icon: Layers, label: '数据集' },
  /* 菜单文案与 adh_menu_registry.label 同步（三者一个名字）：旧值「数据建模」与库内「本体工作区」飘移 */
  { key: '/data/ontology', icon: Boxes, label: '本体建模' },
  { key: '/data/asset-pool', icon: Inbox, label: '建模待办' },
  /* SQL 示例对已下线（与数据集重合），菜单登记与权限点同批退出：
     见 docker/mysql/sql_pairs_retire_migration.sql 与 App.tsx 的路由注释 */
  /* 分析场景(原「快速路径」, 系统配置迁入): 会改变 AI 取数行为的语义层语料, 归数据中台 */
  { key: '/data/scenario', icon: Workflow, label: '分析场景' },
  { key: '/data/metrics', icon: BarChart3, label: '指标与维度' },
  { key: '/data/knowledge-graph', icon: Network, label: '关系图谱' },
  /* 决策建议（配置面）：条件引用指标/维度字典，本质是口径资产，与本段的
     「指标与维度」「本体建模」同族（不放系统配置——那是一级 AI 模板/凭据域）。
     菜单登记 data:decisions ↔ decision:manage，sort=10 排在本段最后。 */
  { key: '/data/decisions', icon: Signpost, label: '决策建议' },
  { section: '数据安全配置' },
  { key: '/data/sensitive', icon: Shield, label: '敏感数据' },
  { key: '/data/rls', icon: ShieldCheck, label: '安全策略' },
  { section: '数据质量' },
  { key: '/data/quality', icon: Activity, label: '质量概览' },
  { key: '/data/quality/rules', icon: Settings, label: '质量规则' },
  { key: '/data/lineage', icon: GitBranch, label: '数据血缘' },
  { key: '/data/standards', icon: Ruler, label: '数据标准' },
  { section: '数据同步' },
  { key: '/data/sync', icon: RefreshCw, label: '同步任务' },
  { key: '/data/sync/logs', icon: FileText, label: '执行日志' },
  { key: '/data/udfs', icon: FunctionSquare, label: 'UDF 管理' },
];

/** 骨架行数 = 菜单项总数（权限未就绪时的占位高度贴近真实菜单上限） */
const MENU_SKELETON_ROWS = DATA_PLATFORM_MENU_ITEMS.filter(item => 'key' in item).length;

export default function DataPlatformLayout() {
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
            <span className="font-bold text-sm text-sidebar-foreground truncate">数据中台</span>
          )}
        </div>

        {/* Menu */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <ScrollArea className="h-full py-2">
            <nav className="space-y-1 px-2" role="navigation" aria-label="数据中台导航">
              {!menuAccess.ready && <SidebarMenuSkeleton rows={MENU_SKELETON_ROWS} collapsed={collapsed} />}
              {visibleMenu.map((item, idx) => {
                if ('section' in item) {
                  if (collapsed) return <div key={idx} className="my-2 mx-2 border-t border-sidebar-border" />;
                  return (
                    <div key={idx} className="px-3 pt-5 pb-1.5">
                      <span className="text-sm font-medium uppercase tracking-[0.08em] text-sidebar-foreground/50">{item.section}</span>
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
      </div>

      {/* Mobile Menu */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobileMenuOpen(false)} />
          <div className="relative w-[280px] h-full bg-sidebar border-r border-sidebar-border flex flex-col">
            <div className="h-12 flex items-center justify-between px-4 border-b border-sidebar-border">
              <span className="font-bold text-lg text-sidebar-foreground">数据中台</span>
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
                        <span className="text-sm font-medium uppercase tracking-[0.08em] text-sidebar-foreground/50">{item.section}</span>
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
                      <span>{item.label}</span>
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
                <DropdownMenuItem onClick={() => navigate('/data/profile')}>
                  <UserCircle className="h-4 w-4 mr-2" />
                  个人设置
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => navigate('/data/memory')}>
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
