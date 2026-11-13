import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  FileEdit, Inbox, Tags, LogOut, Menu, Palette, X, ChevronLeft, ChevronRight, Compass,
  FolderTree, type LucideIcon
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
import { TOUR } from '@/lib/onboarding/guides';
import { useGuideStore } from '../stores/guideStore';
import { useMenuAccess } from '../hooks/useMenuAccess';
import { useHasPerm } from '../stores/permissionStore';
import { SidebarMenuSkeleton } from './SidebarMenuSkeleton';
import { pruneEmptySections } from '@/lib/menuUtils';
import ThemeMenuItems from './ThemeMenuItems';

interface ContentMenuItem {
  section?: string;
  key?: string;
  /**
   * 菜单登记键（adh_menu_registry.menu_key）。
   *
   * **显式写死而不用 toMenuKey(path) 派生**：`/content` 落地页没有第三段，派生只会
   * 算出 `content`；而 `/content/preview/:slug` 派生成 `content:preview` 又根本没登记。
   * 反查必须逐字对上登记行，否则就是"配了却不控制入口"的死开关。
   */
  menu?: string;
  icon?: LucideIcon;
  label?: string;
}

// 内容中心只管 创作 → 审核 → 发布；资讯门户是独立模块（尚未开发），不在这里给入口。
// 「审核发布」已于 2026-11-13 并入「内容管理」（审核与发布只是状态区别，都在行上发生）：
// 菜单登记同步置为 is_active=0，待审角标改挂在内容管理上。
const CONTENT_MENU_ITEMS: ContentMenuItem[] = [
  { section: '我的工作台' },
  { key: '/content/drafts', menu: 'content:drafts', icon: FileEdit, label: '我的草稿' },
  { section: '内容生产' },
  { key: '/content/manage', menu: 'content:manage', icon: Inbox, label: '内容管理' },
  { section: '编排配置' },
  { key: '/content/taxonomy', menu: 'content:taxonomy', icon: Tags, label: '栏目与标签' },
  // 知识运营：知识库是一个目录，这里看/改目录里的文档（kbdoc:*）；
  // 知识库本身的管理（建库/绑 AS-BOT/配凭据）仍在系统配置，入口不搬。menu 行与
  // adh_menu_registry 的 content:kb-docs 逐字对应（section/sort 也同口径）。
  { section: '知识运营' },
  { key: '/content/kb-docs', menu: 'content:kb-docs', icon: FolderTree, label: '知识库文档' },
];

const MENU_SKELETON_ROWS = CONTENT_MENU_ITEMS.filter(item => 'key' in item).length;

export default function ContentLayout() {
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
  // 待审数角标: 只在拿得到审核权限码时请求（无 article:review 的角色不反查队列）。
  // 判据用权限码而不是菜单 key：页面合并后角标挂在内容管理上，但内容管理对
  // “只能编草稿”的角色也可见，拿菜单 key 当条件会给没审核权的人白拉一次队列。
  const canReview = useHasPerm('article:review');
  const [pendingCount, setPendingCount] = useState(0);
  useEffect(() => {
    if (!menuAccess.ready || !canReview) { setPendingCount(0); return; }
    let alive = true;
    import('@/api/content').then(({ contentApi }) =>
      contentApi.pendingReviews({ page: 1, page_size: 1 })
        .then(res => { if (alive) setPendingCount(res.data.total + res.data.review_expired.length); })
        // 拉不到就显示"无角标"而不是假装 0 条待审 —— 静默归零会让审核人以为没事干
        .catch(() => { if (alive) setPendingCount(-1); })
    );
    return () => { alive = false; };
  }, [menuAccess, canReview, currentPath]);

  // 权限未就绪时渲染骨架（避免无权限项先画出来再抽走）
  const visibleMenu = menuAccess.ready ? pruneEmptySections(
    CONTENT_MENU_ITEMS.filter(item => !('menu' in item && item.menu)
      || menuAccess.allowed(item.menu!))
  ) : [];

  const renderMenu = (mobile = false) => visibleMenu.map((item, idx) => {
    if ('section' in item && item.section) {
      if (!mobile && collapsed) return <div key={idx} className="my-2 mx-2 border-t border-sidebar-border" />;
      return (
        <div key={idx} className={mobile ? 'px-4 pt-5 pb-1.5' : 'px-3 pt-5 pb-1.5'}>
          <span className="text-sm font-medium uppercase tracking-[0.08em] text-sidebar-foreground/50">{item.section}</span>
        </div>
      );
    }
    const Icon = item.icon!;
    // 稿件预览页 (/content/preview/<slug>) 从「内容管理」进入，高亮要跟着管理项走；
    // 但 /content/manage 与 /content/drafts 互为前缀无关，不能靠 startsWith 误命中
    const isPreviewOfManage = item.key === '/content/manage' && currentPath.startsWith('/content/preview/');
    const isActive = currentPath === item.key
      || currentPath.startsWith(item.key! + '/')
      || isPreviewOfManage;
    return (
      <Tooltip key={item.key} delayDuration={0}>
        <TooltipTrigger asChild>
          <button
            onClick={() => navigate(item.key!)}
            className={`w-full flex items-center gap-3 rounded-lg text-sm transition-colors duration-200
              ${isActive
                ? 'bg-primary text-primary-foreground font-medium'
                : 'text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground'
              } ${mobile ? 'px-4 py-3 min-h-[44px]' : 'px-3 py-2'} ${collapsed && !mobile ? 'justify-center' : ''}`}
          >
            <Icon className={`${mobile ? 'h-5 w-5' : 'h-4 w-4'} flex-shrink-0`} />
            {!collapsed || mobile ? <span className="truncate flex-1 text-left">{item.label}</span> : null}
            {item.menu === 'content:manage' && pendingCount !== 0 && (
              <span className={`rounded-full px-1.5 text-[10px] leading-4 flex-shrink-0
                ${isActive ? 'bg-primary-foreground/25 text-primary-foreground' : 'bg-destructive/15 text-destructive'}`}>
                {pendingCount > 0 ? pendingCount : '·'}
              </span>
            )}
          </button>
        </TooltipTrigger>
        {collapsed && !mobile && <TooltipContent side="right">{item.label}</TooltipContent>}
      </Tooltip>
    );
  });

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop Sidebar */}
      <div className={`hidden lg:flex flex-col h-full bg-sidebar border-r border-sidebar-border transition-all duration-200 ${collapsed ? 'w-[64px]' : 'w-[220px]'} overflow-hidden`}>
        <div className="h-12 flex items-center justify-center border-b border-sidebar-border gap-1.5">
          <BrandLogo brand={brand} size={24} />
          {!collapsed && <span className="font-bold text-sm text-sidebar-foreground truncate">内容中心</span>}
        </div>

        <div className="flex-1 min-h-0 overflow-hidden">
          <ScrollArea className="h-full py-2">
            <nav className="space-y-1 px-2" role="navigation" aria-label="内容中心导航">
              {!menuAccess.ready && <SidebarMenuSkeleton rows={MENU_SKELETON_ROWS} collapsed={collapsed} />}
              {renderMenu()}
            </nav>
          </ScrollArea>
        </div>

        <div className="p-2 border-t border-sidebar-border">
          <Button
            variant="ghost" size="sm"
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
              <span className="font-bold text-lg text-sidebar-foreground">内容中心</span>
              <Button variant="ghost" size="sm" className="h-9 w-9 p-0" onClick={() => setMobileMenuOpen(false)}>
                <X className="h-5 w-5" />
              </Button>
            </div>
            <ScrollArea className="flex-1 py-3">
              <nav className="space-y-1 px-3">
                {!menuAccess.ready && <SidebarMenuSkeleton rows={MENU_SKELETON_ROWS} />}
                {renderMenu(true)}
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
            <ReleaseNotesButton />
            <SectionSwitcher current="content" />

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
                <DropdownMenuItem onClick={() => startGuide('welcome')}>
                  <Compass className="h-4 w-4 mr-2" />
                  新手引导
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
    </div>
  );
}
