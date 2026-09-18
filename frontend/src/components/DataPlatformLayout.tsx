import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  Database, FileText, Link, BookOpen, Settings, LogOut, Menu,
  Sun, Moon, Palette, Zap, TrendingUp, Grid3x3, GlassWater, Heart,
  UserCircle, X, ChevronLeft, ChevronRight, BarChart3, Tag, GitBranch,
  RefreshCw, Activity, Shield, Ruler, Eye, Brain, Gem, Boxes, Network, Terminal,
  FileQuestion,
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
import SectionSwitcher from './SectionSwitcher';
import AsBotButton from './AsBotButton';
import AsBotPanel from './asbot/AsBotPanel';
import { isMenuAllowed } from '../stores/permissionStore';

/** 将路由 key 转换为 menu_key: '/data/ontology' → 'data:ontology' */
function toMenuKey(routeKey: string): string {
  return routeKey.replace('/data/', 'data:').replace(/^\//, '');
}

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

const DATA_PLATFORM_MENU_ITEMS = [
  { section: '数据源' },
  { key: '/data/datasources', icon: Database, label: '数据源管理' },
  { key: '/data/tables', icon: FileText, label: '表 & 字段' },
  { key: '/data/playground', icon: Terminal, label: 'SQL Playground' },
  { section: '数据目录' },
  { key: '/data/ontology', icon: Boxes, label: '本体建模' },
  { key: '/data/knowledge-graph', icon: Network, label: '本体可视化' },
  { key: '/data/metrics', icon: BarChart3, label: '指标中心' },
  { key: '/data/tags', icon: Tag, label: '标签管理' },
  { key: '/data/sql-pairs', icon: FileQuestion, label: 'SQL 示例对' },
  { key: '/data/glossary', icon: BookOpen, label: '业务术语' },
  { section: '数据质量' },
  { key: '/data/quality', icon: Activity, label: '质量概览' },
  { key: '/data/quality/rules', icon: Settings, label: '质量规则' },
  { key: '/data/lineage', icon: GitBranch, label: '数据血缘' },
  { key: '/data/standards', icon: Ruler, label: '数据标准' },
  { key: '/data/sensitive', icon: Shield, label: '敏感数据' },
  { section: '数据同步' },
  { key: '/data/sync', icon: RefreshCw, label: '同步任务' },
  { key: '/data/sync/logs', icon: FileText, label: '执行日志' },
];

export default function DataPlatformLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const { theme, setTheme } = useThemeStore();
  const { brand, fetchBrand } = useBrandStore();

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => { fetchBrand(); }, [fetchBrand]);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname]);

  const currentPath = location.pathname;

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
            <span className="font-bold text-sm text-sidebar-foreground truncate">数据中台</span>
          )}
        </div>

        {/* Menu */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <ScrollArea className="h-full py-2">
            <nav className="space-y-1 px-2" role="navigation" aria-label="数据中台导航">
              {DATA_PLATFORM_MENU_ITEMS.filter(item => !('key' in item) || isMenuAllowed(toMenuKey((item as any).key))).map((item, idx) => {
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
                const hasDeeperMenuItem = DATA_PLATFORM_MENU_ITEMS.some(
                  o => 'key' in o && o.key!.startsWith(item.key! + '/') && currentPath.startsWith(o.key!)
                );
                const isActive = currentPath === item.key || (currentPath.startsWith(item.key! + '/') && !hasDeeperMenuItem);
                return (
                  <Tooltip key={item.key} delayDuration={0}>
                    <TooltipTrigger asChild>
                      <button
                        onClick={() => navigate(item.key!)}
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
                {DATA_PLATFORM_MENU_ITEMS.filter(item => !('key' in item) || isMenuAllowed(toMenuKey((item as any).key))).map((item, idx) => {
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
            {/* AS-BOT 系统助手 */}
            <AsBotButton />
            {/* Module switcher — 工作空间 / 数据中台 / 系统配置 */}
            <SectionSwitcher current="data" />

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
                <DropdownMenuItem onClick={() => navigate('/data/profile')}>
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
      <AsBotPanel />
    </div>
  );
}
