import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  Database, FileText, Link, BookOpen, Users, Brain, Bot, Workflow, MessageSquare,
  Settings, LogOut, Menu, ArrowLeft, Palette, Sun, Moon, Zap, TrendingUp,
  Grid3x3, GlassWater, Heart, UserCircle, X, ChevronLeft, ChevronRight,
  Clock, Bell,
} from 'lucide-react';
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
import { useWorkspaceStore } from '../stores/workspaceStore';

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

const SYSTEM_MENU_ITEMS = [
  { section: '数据配置' },
  { key: '/system/datasources', icon: Database, label: '数据源管理' },
  { key: '/system/metadata', icon: FileText, label: '表元数据' },
  { key: '/system/relations', icon: Link, label: '表关联' },
  { key: '/system/templates', icon: FileText, label: 'SQL 模板' },
  { key: '/system/terms', icon: BookOpen, label: '业务术语' },
  { section: 'AI 配置' },
  { key: '/system/models', icon: Brain, label: '模型中心' },
  { key: '/system/mcp-agent', icon: Bot, label: 'MCP / Agent' },
  { key: '/system/workflows', icon: Workflow, label: '工作流配置' },
  { key: '/system/workflow-editor', icon: Workflow, label: '工作流编排' },
  { key: '/system/prompts', icon: MessageSquare, label: 'Prompt 管理' },
  { key: '/system/knowledge-base', icon: BookOpen, label: '知识库管理' },
  { section: '自动化' },
  { key: '/system/scheduled-tasks', icon: Clock, label: '定时任务' },
  { key: '/system/notification-channels', icon: Bell, label: '通知渠道' },
  { key: '/system/report-templates', icon: FileText, label: '报告模板' },
  { section: '系统管理' },
  { key: '/system/users', icon: Users, label: '用户管理' },
  { key: '/system/settings', icon: Settings, label: '系统设置' },
];

export default function SystemLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const { theme, setTheme } = useThemeStore();
  const { brand, fetchBrand } = useBrandStore();
  const { getDefaultWorkspaceId } = useWorkspaceStore();

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

  const handleBackToWorkspace = () => {
    const wsId = getDefaultWorkspaceId();
    navigate(`/ws/${wsId}/chat`);
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop Sidebar */}
      <div className={`hidden lg:flex flex-col h-full bg-sidebar border-r border-sidebar-border transition-all duration-200 ${collapsed ? 'w-[64px]' : 'w-[220px]'} overflow-hidden`}>
        {/* Logo */}
        <div className="h-12 flex items-center justify-center border-b border-sidebar-border gap-1.5">
          {brand.show_icon && brand.logo_url ? (
            <img src={brand.logo_url} alt="Logo" className="h-6 w-6 rounded object-contain flex-shrink-0" />
          ) : brand.show_icon ? (
            <span className="inline-flex items-center justify-center rounded bg-primary text-primary-foreground font-bold text-xs size-6 flex-shrink-0">AD</span>
          ) : null}
          {!collapsed && (
            <span className="font-bold text-sm text-sidebar-foreground truncate">系统配置</span>
          )}
        </div>

        {/* Back to workspace */}
        <Tooltip delayDuration={0}>
          <TooltipTrigger asChild>
            <button
              onClick={handleBackToWorkspace}
              className={`w-full flex items-center gap-2 px-3 py-3 hover:bg-sidebar-accent/50 transition-colors border-b border-sidebar-border text-sidebar-foreground/70 hover:text-sidebar-foreground ${collapsed ? 'justify-center' : ''}`}
            >
              <ArrowLeft className="h-4 w-4 flex-shrink-0" />
              {!collapsed && <span className="text-sm">返回工作空间</span>}
            </button>
          </TooltipTrigger>
          {collapsed && <TooltipContent side="right">返回工作空间</TooltipContent>}
        </Tooltip>

        {/* Menu — min-h-0 allows flex child to shrink below content size, enabling scroll */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <ScrollArea className="h-full py-2">
          <nav className="space-y-1 px-2" role="navigation" aria-label="系统配置导航">
            {SYSTEM_MENU_ITEMS.map((item, idx) => {
              if ('section' in item) {
                if (collapsed) return <div key={idx} className="my-2 mx-2 border-t border-sidebar-border" />;
                return (
                  <div key={idx} className="px-3 pt-5 pb-1.5">
                    <span className="text-xs font-semibold text-sidebar-foreground/60">
                      {item.section}
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
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors
                        ${isActive
                          ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                          : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
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

        {/* Collapse button */}
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
              <span className="font-bold text-lg text-sidebar-foreground">系统配置</span>
              <Button variant="ghost" size="sm" className="h-9 w-9 p-0" onClick={() => setMobileMenuOpen(false)}>
                <X className="h-5 w-5" />
              </Button>
            </div>
            <ScrollArea className="flex-1 py-3">
              <nav className="space-y-1 px-3">
                <button
                  onClick={() => { handleBackToWorkspace(); setMobileMenuOpen(false); }}
                  className="w-full flex items-center gap-3 px-4 py-3 rounded-md text-sm text-sidebar-foreground/70 hover:bg-sidebar-accent/50"
                >
                  <ArrowLeft className="h-5 w-5 flex-shrink-0" />
                  <span>返回工作空间</span>
                </button>
                <div className="my-2 border-t border-sidebar-border" />
                {SYSTEM_MENU_ITEMS.map((item, idx) => {
                  if ('section' in item) {
                    return (
                      <div key={idx} className="px-4 pt-5 pb-1.5">
                        <span className="text-xs font-semibold text-sidebar-foreground/60">
                          {item.section}
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
        <header className="h-12 flex items-center justify-between px-4 border-b bg-background flex-shrink-0">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" className="lg:hidden h-9 w-9 p-0" onClick={() => setMobileMenuOpen(true)}>
              <Menu className="h-5 w-5" />
            </Button>
          </div>
          <div className="flex items-center gap-2">
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
        <main className="flex-1 overflow-auto bg-background p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
