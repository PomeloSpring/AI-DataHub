import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation, useParams } from 'react-router-dom';
import {
  MessageSquare, LogOut, Menu, Sun, Moon,
  ChevronLeft, ChevronRight, X, ChevronDown, Palette, Zap, TrendingUp, Grid3x3,
  GlassWater, Folder, UserCircle, Brain, Heart, Check, ArrowRight,
  Clock, Gem, Plus, Archive,
} from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useAuthStore } from '../stores/authStore';
import { useThemeStore, applyTheme, type ThemeId } from '../stores/themeStore';
import { useBrandStore } from '../stores/brandStore';
import { useWorkspaceStore, type Workspace } from '../stores/workspaceStore';
import { isMenuAllowed } from '../stores/permissionStore';
import { pruneEmptySections } from '@/lib/menuUtils';
import SectionSwitcher from './SectionSwitcher';
import { toast } from 'sonner';
import client from '@/api/client';
import { Input } from '@/components/ui/input';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';

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

/** 将路由 key 转换为 menu_key: '/ws/3/chat' → 'workspace:chat' */
function toMenuKey(routeKey: string): string {
  return `workspace:${routeKey.split('/').pop()}`;
}

// ── Workspace Selector (sidebar-embedded) ─────────────────────────

function WorkspaceSelectorSidebar({ collapsed, module = 'workspace' }: { collapsed: boolean; module?: 'workspace' | 'ask' | 'dashboards' }) {
  const { workspaces, currentWorkspaceId, setWorkspace, loadWorkspaces } = useWorkspaceStore();
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
    navigate(module === 'workspace' ? `/ws/${ws.id}/chat` : `/${module}/${ws.id}`);
  };

  // 新建工作空间(个人工作站, 随用户走; 受管理员配额限制)
  const [createOpen, setCreateOpen] = useState(false);
  const [newWsName, setNewWsName] = useState('');
  const [creating, setCreating] = useState(false);
  const handleCreate = async () => {
    const name = newWsName.trim();
    if (!name) { toast.error('请输入工作空间名称'); return; }
    setCreating(true);
    try {
      const { data } = await client.post('/workspaces', { name });
      toast.success('工作空间已创建');
      setCreateOpen(false);
      setNewWsName('');
      await loadWorkspaces();
      if (data?.id) {
        setWorkspace(data.id);
        navigate(module === 'workspace' ? `/ws/${data.id}/chat` : `/${module}/${data.id}`);
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '创建失败');
    } finally {
      setCreating(false);
    }
  };
  const createDialog = (
    <Dialog open={createOpen} onOpenChange={setCreateOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建工作空间</DialogTitle>
          <DialogDescription>工作空间是你的个人工作站，可按项目分开组织会话与资产。</DialogDescription>
        </DialogHeader>
        <Input
          autoFocus aria-label="工作空间名称" placeholder="输入工作空间名称" value={newWsName}
          onChange={e => setNewWsName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') void handleCreate(); }}
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button>
          <Button onClick={handleCreate} disabled={creating}>{creating ? '创建中...' : '创建'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  if (collapsed) {
    return (
      <>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button aria-label="切换工作空间" className="w-full flex items-center justify-center py-3 hover:bg-sidebar-accent/50 transition-colors">
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
            <DropdownMenuItem onClick={() => setCreateOpen(true)} className="flex items-center gap-2">
              <Plus className="h-4 w-4" />
              <span className="flex-1">新建工作空间</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        {createDialog}
      </>
    );
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button aria-label="切换工作空间" className={`w-full min-w-0 flex items-center gap-2 px-3 py-2 hover:bg-muted/50 transition-colors ${module === 'workspace' ? 'border-b border-sidebar-border' : 'rounded-md'}`}>
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
          <DropdownMenuItem onClick={() => setCreateOpen(true)} className="flex items-center gap-2">
            <Plus className="h-4 w-4" />
            <span className="flex-1">新建工作空间</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {createDialog}
    </>
  );
}

// ── Workspace Layout ──────────────────────────────────────────────

export default function WorkspaceLayout({ module = 'workspace' }: { module?: 'workspace' | 'ask' | 'dashboards' }) {
  const independent = module !== 'workspace';
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { workspaceId } = useParams();
  const { user, logout } = useAuthStore();
  const { theme, setTheme } = useThemeStore();
  const { brand, fetchBrand } = useBrandStore();

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    fetchBrand();
  }, [fetchBrand]);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname]);

  // 菜单可见性按权限码裁决（与 SystemLayout/DataPlatformLayout 同口径）
  // 资产清单是工作站基础功能(随用户走), 不按权限码过滤; 一级分组下无可访问项时不渲染分组标题
  const menuItems = pruneEmptySections([
    { section: '数据分析' },
    { key: `/ws/${workspaceId}/chat`, icon: MessageSquare, label: 'Chat 智能问答' },
    { key: `/ws/${workspaceId}/reports`, icon: Folder, label: '报表中心' },
    { section: '工作站' },
    { key: `/ws/${workspaceId}/assets`, icon: Archive, label: '资产清单', always: true },
    { section: '自动化' },
    { key: `/ws/${workspaceId}/scheduled`, icon: Clock, label: '任务调度' },
  ].filter(item => !('key' in item) || (item as any).always || isMenuAllowed(toMenuKey((item as any).key))));

  const currentPath = location.pathname;

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
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        <header className="h-12 flex items-center justify-between px-4 border-b border-border bg-card flex-shrink-0">
          <div className="flex min-w-0 items-center gap-2">
            {!independent && <Button variant="ghost" size="sm" className="lg:hidden h-9 w-9 p-0" onClick={() => setMobileMenuOpen(true)}><Menu className="h-5 w-5" /></Button>}
            {independent && <>
              <span className="hidden shrink-0 font-semibold sm:inline">{brand.app_name || 'AI-DataHub'}</span>
              <span className="hidden text-muted-foreground sm:inline">/</span>
              <span className="shrink-0 text-sm font-medium">{module === 'ask' ? '工作空间' : '数据看板'}</span>
              <div className="min-w-0 max-w-56"><WorkspaceSelectorSidebar collapsed={false} module={module} /></div>
            </>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {/* Module switcher — 工作空间 / 数据中台 / 系统配置 */}
            <SectionSwitcher current={module} />

            {/* Theme selector */}
            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label="主题风格">
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
                <button aria-label="用户菜单" className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors min-h-[36px]">
                  <Avatar className="h-6 w-6">
                    <AvatarFallback className="text-xs">{user?.username?.charAt(0)?.toUpperCase() || 'U'}</AvatarFallback>
                  </Avatar>
                  <span className="text-sm font-medium hidden sm:inline">{user?.username}</span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => navigate(module === 'workspace' ? `/ws/${workspaceId}/profile` : `/${module}/${workspaceId}/profile`)}>
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
        <main className="min-h-0 min-w-0 flex-1 overflow-auto bg-background">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
