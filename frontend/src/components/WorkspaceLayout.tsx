import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation, useParams } from 'react-router-dom';
import {
  MessageSquare, History, Settings, LogOut, Menu, Sun, Moon,
  ChevronLeft, ChevronRight, X, ChevronDown, Palette, Zap, TrendingUp, Grid3x3,
  GlassWater, Folder, UserCircle, Brain, Heart, Check, ArrowRight,
  Clock, Gem,
} from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useAuthStore } from '../stores/authStore';
import { useThemeStore, applyTheme, type ThemeId } from '../stores/themeStore';
import { useBrandStore } from '../stores/brandStore';
import { useDashboardStore } from '../stores/dashboardStore';
import { useWorkspaceStore, type Workspace } from '../stores/workspaceStore';
import client from '../api/client';

const THEMES: { id: ThemeId; label: string; icon: typeof Sun; desc: string }[] = [
  { id: 'dark', label: '暗色', icon: Moon, desc: '深色背景，适合长时间使用' },
  { id: 'light', label: '亮色', icon: Sun, desc: '浅色背景，清晰明亮' },
  { id: 'datafoundry', label: 'DataFoundry', icon: Gem, desc: '克制优雅，近黑主色 + 宝石色调' },
  { id: 'tech', label: '科技风', icon: Zap, desc: '深蓝底色，霓虹高亮' },
  { id: 'finance', label: '金融风', icon: TrendingUp, desc: '深色海军蓝，金色主调' },
  { id: 'bento', label: 'Bento Grid', icon: Grid3x3, desc: '柔和圆角卡片，彩色区块布局' },
  { id: 'glass', label: '玻璃拟态', icon: GlassWater, desc: '深色半透明毛玻璃质感' },
  { id: 'ainative', label: 'AI-Native', icon: Brain, desc: '深空神经网络，动态光效边框' },
  { id: 'medical', label: '医疗平台', icon: Heart, desc: '清爽蓝绿，专业可信' },
];

function getMenuIcon(iconName?: string): React.ComponentType<{ className?: string }> {
  if (!iconName) return Folder;
  const icons = LucideIcons as Record<string, any>;
  return icons[iconName] || Folder;
}

// ── Workspace Selector (sidebar-embedded) ─────────────────────────

function WorkspaceSelectorSidebar({ collapsed }: { collapsed: boolean }) {
  const { workspaces, currentWorkspaceId, setWorkspace, loadWorkspaces } = useWorkspaceStore();
  const navigate = useNavigate();
  const { workspaceId } = useParams();

  useEffect(() => {
    loadWorkspaces();
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
    navigate(`/ws/${ws.id}/chat`);
  };

  const handleManage = () => {
    navigate('/workspaces');
  };

  if (collapsed) {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button className="w-full flex items-center justify-center py-3 hover:bg-sidebar-accent/50 transition-colors">
            <span className="text-lg">{currentWs?.icon || '📊'}</span>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="start" className="w-56">
          {workspaces.map(ws => (
            <DropdownMenuItem key={ws.id} onClick={() => handleSwitch(ws)} className="flex items-center gap-2">
              <span>{ws.icon}</span>
              <span className="flex-1 truncate">{ws.name}</span>
              {ws.id === currentWorkspaceId && <Check className="h-4 w-4 text-primary" />}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={handleManage}>
            <Settings className="h-4 w-4 mr-2" />
            管理工作空间
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="w-full flex items-center gap-2 px-3 py-3 hover:bg-sidebar-accent/50 transition-colors border-b border-sidebar-border">
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
          <DropdownMenuItem key={ws.id} onClick={() => handleSwitch(ws)} className="flex items-center gap-2">
            <span>{ws.icon}</span>
            <div className="flex-1 min-w-0">
              <div className="truncate">{ws.name}</div>
              {ws.description && (
                <div className="text-xs text-muted-foreground truncate">{ws.description}</div>
              )}
            </div>
            {ws.id === currentWorkspaceId && <Check className="h-4 w-4 text-primary shrink-0" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={handleManage}>
          <Settings className="h-4 w-4 mr-2" />
          管理工作空间
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ── Dynamic Menu Tree (reuse from Layout) ─────────────────────────

interface MenuTreeNodeProps {
  node: any;
  depth: number;
  collapsed: boolean;
  currentPath: string;
  expandedGroups: Set<number>;
  onToggle: (id: number) => void;
  onNavigate: (path: string) => void;
  workspaceId?: string;
}

function MenuTreeNode({
  node, depth, collapsed, currentPath, expandedGroups, onToggle, onNavigate, workspaceId,
}: MenuTreeNodeProps) {
  const hasChildren = node.children && node.children.length > 0;
  const isLeaf = !!node.page_id;
  const isExpanded = expandedGroups.has(node.id);
  const NodeIcon = getMenuIcon(node.icon);

  if (isLeaf) {
    const wsPrefix = workspaceId ? `/ws/${workspaceId}` : '';
    const path = node.link_type === 'screen' ? `/screen/${node.page_id}` : `${wsPrefix}/page/${node.page_id}`;
    const isActive = currentPath === path;

    return (
      <Tooltip delayDuration={0}>
        <TooltipTrigger asChild>
          <button
            onClick={() => onNavigate(path)}
            className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors
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

  return (
    <div>
      <Tooltip delayDuration={0}>
        <TooltipTrigger asChild>
          <button
            onClick={() => { if (!collapsed) onToggle(node.id); }}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors
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
              workspaceId={workspaceId}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Workspace Layout ──────────────────────────────────────────────

export default function WorkspaceLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [menuTree, setMenuTree] = useState<any[]>([]);
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(new Set());
  const navigate = useNavigate();
  const location = useLocation();
  const { workspaceId } = useParams();
  const { user, logout } = useAuthStore();
  const { theme, setTheme } = useThemeStore();
  const { brand, fetchBrand } = useBrandStore();
  const { loadDashboards } = useDashboardStore();

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    fetchBrand();
    if (workspaceId) loadDashboards(Number(workspaceId));
  }, [fetchBrand, workspaceId]);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname]);

  // Fetch menu tree (workspace-scoped)
  useEffect(() => {
    if (workspaceId) {
      client.get(`/admin/menu-tree?workspace_id=${workspaceId}`)
        .then(({ data }) => setMenuTree(data || []))
        .catch(() => {});
    }
  }, [location.pathname, workspaceId]);

  const menuItems = [
    { section: '数据分析' },
    { key: `/ws/${workspaceId}/chat`, icon: MessageSquare, label: 'Chat 智能问答' },
    { key: `/ws/${workspaceId}/reports`, icon: Folder, label: '报表中心' },
    { section: '自动化' },
    { key: `/ws/${workspaceId}/scheduled`, icon: Clock, label: '任务调度' },
    { section: '' },
    { key: `/ws/${workspaceId}/history`, icon: History, label: '查询历史' },
  ];

  const currentPath = location.pathname;

  const toggleGroup = (id: number) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
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
      <div className={`hidden lg:flex flex-col min-h-0 bg-sidebar border-r border-sidebar-border transition-all duration-200 ${collapsed ? 'w-[64px]' : 'w-[220px]'}`}>
        {/* Logo */}
        <div className="h-12 flex items-center justify-center border-b border-sidebar-border gap-1.5">
          {brand.show_icon && brand.logo_url ? (
            <img src={brand.logo_url} alt="Logo" className="h-6 w-6 rounded object-contain flex-shrink-0" />
          ) : brand.show_icon ? (
            <span className={`inline-flex items-center justify-center rounded bg-primary text-primary-foreground font-bold ${collapsed ? 'text-[10px] size-5' : 'text-xs size-6'} flex-shrink-0`}>AD</span>
          ) : null}
          {!collapsed && brand.show_text && (
            <span className="font-bold text-xl text-sidebar-foreground truncate">{brand.app_name || 'AI-DataHub'}</span>
          )}
        </div>

        {/* Workspace Selector */}
        <WorkspaceSelectorSidebar collapsed={collapsed} />

        {/* Menu — min-h-0 allows flex child to shrink below content size, enabling scroll */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <ScrollArea className="h-full py-2">
          <nav className="space-y-1 px-2" role="navigation" aria-label="工作空间导航">
            {/* Dynamic menu tree */}
            {menuTree.length > 0 && (
              <>
                {menuTree.map(node => (
                  <MenuTreeNode
                    key={node.id}
                    node={node}
                    depth={0}
                    collapsed={collapsed}
                    currentPath={currentPath}
                    expandedGroups={expandedGroups}
                    onToggle={toggleGroup}
                    onNavigate={handleNavigate}
                    workspaceId={workspaceId}
                  />
                ))}
                <div className="my-1.5 mx-2 border-t border-sidebar-border" />
              </>
            )}

            {/* Static menu items */}
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

        {/* Bottom Navigation */}
        <div className="p-2 border-t border-sidebar-border space-y-1">
          <Tooltip delayDuration={0}>
            <TooltipTrigger asChild>
              <button
                onClick={() => navigate('/data')}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors duration-200
                  text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground
                  ${collapsed ? 'justify-center' : ''}`}
              >
                <LucideIcons.Database className="h-4 w-4 flex-shrink-0" />
                {!collapsed && (
                  <>
                    <span className="flex-1 text-left">数据中台</span>
                    <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 opacity-50" />
                  </>
                )}
              </button>
            </TooltipTrigger>
            {collapsed && <TooltipContent side="right">数据中台</TooltipContent>}
          </Tooltip>
          {user?.role === 'admin' && (
            <Tooltip delayDuration={0}>
              <TooltipTrigger asChild>
                <button
                  onClick={() => navigate('/system')}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors duration-200
                    text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground
                    ${collapsed ? 'justify-center' : ''}`}
                >
                  <LucideIcons.Settings className="h-4 w-4 flex-shrink-0" />
                  {!collapsed && (
                    <>
                      <span className="flex-1 text-left">系统配置</span>
                      <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 opacity-50" />
                    </>
                  )}
                </button>
              </TooltipTrigger>
              {collapsed && <TooltipContent side="right">系统配置</TooltipContent>}
            </Tooltip>
          )}
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

      {/* Mobile Menu Overlay */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobileMenuOpen(false)} />
          <div className="relative w-[280px] h-full bg-sidebar border-r border-sidebar-border flex flex-col min-h-0">
            <div className="h-12 flex items-center justify-between px-4 border-b border-sidebar-border">
              <span className="font-bold text-lg text-sidebar-foreground">{brand.app_name || 'AI-DataHub'}</span>
              <Button variant="ghost" size="sm" className="h-9 w-9 p-0" onClick={() => setMobileMenuOpen(false)}>
                <X className="h-5 w-5" />
              </Button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
            <ScrollArea className="h-full py-3">
              <nav className="space-y-1 px-3">
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
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="h-12 flex items-center justify-between px-4 border-b border-border bg-card flex-shrink-0">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" className="lg:hidden h-9 w-9 p-0" onClick={() => setMobileMenuOpen(true)}>
              <Menu className="h-5 w-5" />
            </Button>
          </div>
          <div className="flex items-center gap-2">
            {/* Theme selector */}
            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm" className="h-9 w-9 p-0">
                      <Palette className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent>主题风格</TooltipContent>
              </Tooltip>
              <DropdownMenuContent align="end" className="w-48">
                {THEMES.map(t => {
                  const Icon = t.icon;
                  return (
                    <DropdownMenuItem key={t.id} onClick={() => setTheme(t.id)} className={`flex items-center gap-3 ${theme === t.id ? 'bg-accent' : ''}`}>
                      <Icon className="h-4 w-4" />
                      <div className="flex-1 min-w-0">
                        <div className={`text-sm ${theme === t.id ? 'font-medium' : ''}`}>{t.label}</div>
                        <div className="text-xs text-muted-foreground truncate">{t.desc}</div>
                      </div>
                      {theme === t.id && <span className="text-xs text-primary">✓</span>}
                    </DropdownMenuItem>
                  );
                })}
              </DropdownMenuContent>
            </DropdownMenu>

            {/* User menu */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors min-h-[36px]">
                  <Avatar className="h-6 w-6">
                    <AvatarFallback className="text-xs">{user?.username?.charAt(0)?.toUpperCase() || 'U'}</AvatarFallback>
                  </Avatar>
                  <span className="text-sm font-medium hidden sm:inline">{user?.username}</span>
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
        <main className="flex-1 overflow-auto bg-background">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
