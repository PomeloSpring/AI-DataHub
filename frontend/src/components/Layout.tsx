import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  MessageSquare, LayoutDashboard, History, Settings, Code,
  LogOut, Menu, Sun, Moon, ChevronLeft, ChevronRight, X, ChevronDown,
  Palette, Zap, TrendingUp, Grid3x3, GlassWater,
  Folder, FileText, UserCircle, FlaskConical, Brain, Heart,
  Database,
} from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useAuthStore } from '../stores/authStore';
import { useThemeStore, applyTheme, type ThemeId } from '../stores/themeStore';
import { useBrandStore } from '../stores/brandStore';
import { useDashboardStore } from '../stores/dashboardStore';
import client from '../api/client';
import WorkspaceSelectorV2 from './WorkspaceSelectorV2';

const THEMES: { id: ThemeId; label: string; icon: typeof Sun; desc: string }[] = [
  { id: 'dark', label: '暗色', icon: Moon, desc: '深色背景，适合长时间使用' },
  { id: 'light', label: '亮色', icon: Sun, desc: '浅色背景，清晰明亮' },
  { id: 'tech', label: '科技风', icon: Zap, desc: '深蓝底色，霓虹高亮' },
  { id: 'finance', label: '金融风', icon: TrendingUp, desc: '深色海军蓝，金色主调' },
  { id: 'bento', label: 'Bento Grid', icon: Grid3x3, desc: '柔和圆角卡片，彩色区块布局' },
  { id: 'glass', label: '玻璃拟态', icon: GlassWater, desc: '深色半透明毛玻璃质感' },
  { id: 'ainative', label: 'AI-Native', icon: Brain, desc: '深空神经网络，动态光效边框' },
  { id: 'medical', label: '医疗平台', icon: Heart, desc: '清爽蓝绿，专业可信' },
];

/** Get icon component by name from lucide-react */
function getMenuIcon(iconName?: string): React.ComponentType<{ className?: string }> {
  if (!iconName) return Folder;
  const icons = LucideIcons as Record<string, any>;
  return icons[iconName] || Folder;
}

/** Props for the recursive menu tree node. */
interface MenuTreeNodeProps {
  node: any;
  depth: number;
  collapsed: boolean;
  currentPath: string;
  expandedGroups: Set<number>;
  onToggle: (id: number) => void;
  onNavigate: (path: string) => void;
  mobile?: boolean;
}

function MenuTreeNode({
  node,
  depth,
  collapsed,
  currentPath,
  expandedGroups,
  onToggle,
  onNavigate,
  mobile = false,
}: MenuTreeNodeProps) {
  const hasChildren = node.children && node.children.length > 0;
  const isLeaf = !!node.page_id;
  const isExpanded = expandedGroups.has(node.id);
  const NodeIcon = getMenuIcon(node.icon);

  // Leaf node -- navigates to /page/{page_id} or /screen/{page_id}
  if (isLeaf) {
    const path = node.link_type === 'screen' ? `/screen/${node.page_id}` : `/page/${node.page_id}`;
    const isActive = currentPath === path;

    if (mobile) {
      return (
        <button
          onClick={() => onNavigate(path)}
          className={`w-full flex items-center gap-2 px-4 py-2 rounded-md text-sm transition-colors
            ${isActive
              ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
              : 'text-sidebar-foreground/60 hover:bg-sidebar-accent/30 hover:text-sidebar-foreground'
            }`}
          style={{ paddingLeft: `${16 + depth * 16}px` }}
        >
          <NodeIcon className="h-3.5 w-3.5 flex-shrink-0" />
          <span className="truncate">{node.name}</span>
        </button>
      );
    }

    return (
      <Tooltip delayDuration={0}>
        <TooltipTrigger asChild>
          <button
            onClick={() => onNavigate(path)}
            className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-xs transition-colors
              ${isActive
                ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                : 'text-sidebar-foreground/60 hover:bg-sidebar-accent/30 hover:text-sidebar-foreground'
              } ${collapsed ? 'justify-center' : ''}`}
            style={!collapsed ? { paddingLeft: `${12 + depth * 12}px` } : undefined}
          >
            <NodeIcon className="h-3.5 w-3.5 flex-shrink-0" />
            {!collapsed && <span className="truncate">{node.name}</span>}
          </button>
        </TooltipTrigger>
        {collapsed && <TooltipContent side="right">{node.name}</TooltipContent>}
      </Tooltip>
    );
  }

  // Group node -- expandable folder
  if (mobile) {
    return (
      <div>
        <button
          onClick={() => onToggle(node.id)}
          className={`w-full flex items-center gap-2 px-4 py-2.5 rounded-md text-sm transition-colors
            text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground`}
          style={{ paddingLeft: `${16 + depth * 16}px` }}
        >
          <NodeIcon className="h-4 w-4 flex-shrink-0" />
          <span className="truncate flex-1 text-left">{node.name}</span>
          {hasChildren && (
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${isExpanded ? '' : '-rotate-90'}`} />
          )}
        </button>
        {hasChildren && isExpanded && (
          <div className="space-y-0.5">
            {node.children.map((child: any) => (
              <MenuTreeNode
                key={child.id}
                node={child}
                depth={depth + 1}
                collapsed={false}
                currentPath={currentPath}
                expandedGroups={expandedGroups}
                onToggle={onToggle}
                onNavigate={onNavigate}
                mobile
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      <Tooltip delayDuration={0}>
        <TooltipTrigger asChild>
          <button
            onClick={() => {
              if (collapsed) return;
              onToggle(node.id);
            }}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar
              text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground
              ${collapsed ? 'justify-center' : ''}`}
            style={!collapsed ? { paddingLeft: `${12 + depth * 12}px` } : undefined}
          >
            <NodeIcon className="h-4 w-4 flex-shrink-0" />
            {!collapsed && (
              <>
                <span className="truncate flex-1 text-left">{node.name}</span>
                {hasChildren && (
                  <ChevronDown className={`h-3 w-3 transition-transform ${isExpanded ? '' : '-rotate-90'}`} />
                )}
              </>
            )}
          </button>
        </TooltipTrigger>
        {collapsed && <TooltipContent side="right">{node.name}</TooltipContent>}
      </Tooltip>
      {!collapsed && hasChildren && isExpanded && (
        <div className="space-y-0.5">
          {node.children.map((child: any) => (
            <MenuTreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              collapsed={false}
              currentPath={currentPath}
              expandedGroups={expandedGroups}
              onToggle={onToggle}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [menuTree, setMenuTree] = useState<any[]>([]);
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(new Set());
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const { theme, setTheme } = useThemeStore();
  const { brand, fetchBrand } = useBrandStore();
  const { loadDashboards } = useDashboardStore();

  // Apply theme class to html
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Fetch brand settings and dashboards on mount
  useEffect(() => {
    fetchBrand();
    loadDashboards();
  }, [fetchBrand, loadDashboards]);

  // Fetch menu tree
  useEffect(() => {
    client.get('/admin/menu-tree')
      .then(({ data }) => setMenuTree(data || []))
      .catch(() => {});
  }, [location.pathname]);

  // Close mobile menu on route change
  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  const menuItems = [
    { key: '/chat', icon: MessageSquare, label: 'Chat 数据分析' },
    { key: '/dashboard', icon: LayoutDashboard, label: '页面设计' },
    ...(user?.role === 'admin' ? [
      { key: '/admin/data', icon: Database, label: '数据管理' },
    ] : []),
    { key: '/history', icon: History, label: '查询历史' },
    ...(user?.role === 'admin' ? [
      { key: '/admin/model', icon: Brain, label: '模型中心' },
      { key: '/admin/mcp-agent', icon: LucideIcons.Bot, label: 'MCP / Agent' },
      { key: '/admin', icon: Settings, label: '系统设置' },
    ] : []),
  ];

  const currentPath = location.pathname === '/' ? '/chat' : location.pathname;

  const toggleGroup = (id: number) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleNavigate = (path: string) => {
    navigate(path);
    setMobileMenuOpen(false);
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop Sidebar */}
      <div
        className={`hidden lg:flex flex-col bg-sidebar border-r border-sidebar-border transition-all duration-200 ${
          collapsed ? 'w-[64px]' : 'w-[200px]'
        }`}
      >
        {/* Logo */}
        <div className="h-12 flex items-center justify-center border-b border-sidebar-border gap-1.5">
          {brand.show_icon && brand.logo_url ? (
            <img src={brand.logo_url} alt="Logo" className="h-6 w-6 rounded object-contain flex-shrink-0" />
          ) : brand.show_icon ? (
            <span className={`inline-flex items-center justify-center rounded bg-primary text-primary-foreground font-bold ${collapsed ? 'text-[10px] size-5' : 'text-xs size-6'} flex-shrink-0`}>
              AD
            </span>
          ) : null}
          {!collapsed && brand.show_text && (
            <span className="font-bold text-xl text-sidebar-foreground truncate">
              {brand.app_name || 'AI-DataHub'}
            </span>
          )}
          {collapsed && !brand.show_icon && brand.show_text && (
            <span className="font-bold text-base text-sidebar-foreground">
              {(brand.app_name || 'AI-DataHub').charAt(0)}
            </span>
          )}
        </div>

        {/* Menu */}
        <ScrollArea className="flex-1 py-2">
          <nav className="space-y-1 px-2" role="navigation" aria-label="主导航">
            {/* Dynamic menu tree */}
            {menuTree.length > 0 && (
              <>
                {menuTree.map((node) => (
                  <MenuTreeNode
                    key={node.id}
                    node={node}
                    depth={0}
                    collapsed={collapsed}
                    currentPath={currentPath}
                    expandedGroups={expandedGroups}
                    onToggle={toggleGroup}
                    onNavigate={handleNavigate}
                  />
                ))}
                <div className="my-1.5 mx-2 border-t border-sidebar-border" />
              </>
            )}

            {/* Static menu items */}
            {menuItems.map((item) => {
              const Icon = item.icon;
              const isActive = currentPath === item.key;

              return (
                <Tooltip key={item.key} delayDuration={0}>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => handleNavigate(item.key)}
                      aria-current={isActive ? 'page' : undefined}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors
                        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar
                        ${isActive
                          ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                          : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
                        } ${collapsed ? 'justify-center' : ''}`}
                    >
                      <Icon className="h-4 w-4 flex-shrink-0" />
                      {!collapsed && <span className="truncate">{item.label}</span>}
                    </button>
                  </TooltipTrigger>
                  {collapsed && (
                    <TooltipContent side="right">
                      {item.label}
                    </TooltipContent>
                  )}
                </Tooltip>
              );
            })}
          </nav>
        </ScrollArea>

        {/* Collapse button */}
        <div className="p-2 border-t border-sidebar-border">
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-center text-sidebar-foreground/70 hover:text-sidebar-foreground"
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
        </div>
      </div>

      {/* Mobile Menu Overlay */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/50 animate-fade-in"
            onClick={() => setMobileMenuOpen(false)}
          />
          <div className="relative w-[280px] h-full bg-sidebar border-r border-sidebar-border flex flex-col animate-slide-up">
            <div className="h-12 flex items-center justify-between px-4 border-b border-sidebar-border">
              <div className="flex items-center gap-1.5">
                {brand.show_icon && brand.logo_url ? (
                  <img src={brand.logo_url} alt="Logo" className="h-6 w-6 rounded object-contain" />
                ) : brand.show_icon ? (
                  <span className="inline-flex items-center justify-center rounded bg-primary text-primary-foreground font-bold text-xs size-6">AD</span>
                ) : null}
                {brand.show_text && (
                  <span className="font-bold text-xl text-sidebar-foreground">
                    {brand.app_name || 'AI-DataHub'}
                  </span>
                )}
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-9 w-9 p-0"
                onClick={() => setMobileMenuOpen(false)}
                aria-label="关闭菜单"
              >
                <X className="h-5 w-5" />
              </Button>
            </div>
            <ScrollArea className="flex-1 py-3">
              <nav className="space-y-1 px-3" role="navigation" aria-label="主导航">
                {/* Dynamic menu tree */}
                {menuTree.length > 0 && (
                  <>
                    {menuTree.map((node) => (
                      <MenuTreeNode
                        key={node.id}
                        node={node}
                        depth={0}
                        collapsed={false}
                        currentPath={currentPath}
                        expandedGroups={expandedGroups}
                        onToggle={toggleGroup}
                        onNavigate={handleNavigate}
                        mobile
                      />
                    ))}
                    <div className="my-1.5 mx-3 border-t border-sidebar-border" />
                  </>
                )}

                {/* Static menu items */}
                {menuItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = currentPath === item.key;

                  return (
                    <button
                      key={item.key}
                      onClick={() => handleNavigate(item.key)}
                      aria-current={isActive ? 'page' : undefined}
                      className={`w-full flex items-center gap-3 px-4 py-3 rounded-md text-sm transition-colors min-h-[44px]
                        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
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
            {/* User info in mobile menu */}
            <div className="p-4 border-t border-sidebar-border">
              <div className="flex items-center gap-3">
                <Avatar className="h-8 w-8">
                  <AvatarFallback className="text-sm">
                    {user?.username?.charAt(0)?.toUpperCase() || 'U'}
                  </AvatarFallback>
                </Avatar>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{user?.username}</div>
                  <div className="text-xs text-muted-foreground">{user?.role}</div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-9 w-9 p-0"
                  onClick={() => { logout(); navigate('/login'); }}
                  aria-label="退出登录"
                >
                  <LogOut className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <header className="h-12 flex items-center justify-between px-4 border-b bg-background flex-shrink-0">
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="lg:hidden h-9 w-9 p-0"
              onClick={() => setMobileMenuOpen(true)}
              aria-label="打开菜单"
            >
              <Menu className="h-5 w-5" />
            </Button>
          </div>

          <div className="flex items-center gap-2">
            {/* Workspace selector */}
            <WorkspaceSelectorV2 />

            {/* Theme selector */}
            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-9 w-9 p-0"
                      aria-label="切换主题风格"
                    >
                      <Palette className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent>主题风格</TooltipContent>
              </Tooltip>
              <DropdownMenuContent align="end" className="w-48">
                {THEMES.map((t) => {
                  const Icon = t.icon;
                  const isActive = theme === t.id;
                  return (
                    <DropdownMenuItem
                      key={t.id}
                      onClick={() => setTheme(t.id)}
                      className={`flex items-center gap-3 ${isActive ? 'bg-accent' : ''}`}
                    >
                      <Icon className="h-4 w-4" />
                      <div className="flex-1 min-w-0">
                        <div className={`text-sm ${isActive ? 'font-medium' : ''}`}>{t.label}</div>
                        <div className="text-xs text-muted-foreground truncate">{t.desc}</div>
                      </div>
                      {isActive && <span className="text-xs text-primary">✓</span>}
                    </DropdownMenuItem>
                  );
                })}
              </DropdownMenuContent>
            </DropdownMenu>

            {/* User menu */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors min-h-[36px]
                    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label="用户菜单"
                >
                  <Avatar className="h-6 w-6">
                    <AvatarFallback className="text-xs">
                      {user?.username?.charAt(0)?.toUpperCase() || 'U'}
                    </AvatarFallback>
                  </Avatar>
                  <span className="text-sm font-medium hidden sm:inline">{user?.username}</span>
                  <span className="text-xs text-muted-foreground hidden sm:inline">({user?.role})</span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => navigate('/profile')}>
                  <UserCircle className="h-4 w-4 mr-2" />
                  个人设置
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => { logout(); navigate('/login'); }}>
                  <LogOut className="h-4 w-4 mr-2" />
                  退出登录
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-auto bg-background">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
