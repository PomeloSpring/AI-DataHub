import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Edit, Play, Maximize, Minimize, Palette, UserCircle, LogOut, Sun, Moon, Zap, TrendingUp, Grid3x3, GlassWater, Brain, Heart, Gem, LayoutDashboard } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { ResponsiveDashboardCanvas } from '@/components/DashboardCanvas';
import { useDashboardNavigation } from '@/components/DashboardNavigation';
import { useDashboardFullscreen } from '@/hooks/useDashboardFullscreen';
import { DashboardRuntimeParams } from '@/components/DashboardParams';
import DashboardAutoRefresh from '@/components/DashboardAutoRefresh';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import SectionSwitcher from '@/components/SectionSwitcher';
import AsBotButton from '@/components/AsBotButton';
import AsBotPanel from '@/components/asbot/AsBotPanel';
import { useAuthStore } from '@/stores/authStore';
import { useThemeStore, applyTheme, type ThemeId } from '@/stores/themeStore';
import type { Dashboard } from '@/stores/dashboardStore';
import client from '@/api/client';

// 主题风格(与 SystemLayout/WorkspaceLayout 同一套)
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

/** 仪表盘组目录项: 组按角色分配, 组内看板 ∩ 角色可见(可见性 fail-closed) */
export interface GroupView {
  id: number;
  name: string;
  description: string;
  sort: number;
  dashboards: Array<Pick<Dashboard, 'id' | 'name' | 'status' | 'is_default'> & { sort_order?: number }>;
}

/** 拉取当前用户可见的仪表盘组(含"未分组"兜底组) */
export async function fetchVisibleGroups(): Promise<GroupView[]> {
  const { data } = await client.get('/dashboard/groups/visible');
  return Array.isArray(data) ? data : [];
}

export default function Analysis() {
  const params = useParams();
  const dashboardId = Number(params.dashboardId || params.id) || 0;
  const location = useLocation();
  const navigate = useNavigate();
  const library = useVisLibrary();
  const user = useAuthStore(s => s.user);
  const logout = useAuthStore(s => s.logout);
  const { theme, setTheme } = useThemeStore();
  useEffect(() => { applyTheme(theme); }, [theme]);
  const root = useRef<HTMLDivElement>(null);
  const { fullscreen, toggleFullscreen } = useDashboardFullscreen(root);
  const independent = location.pathname === '/dashboards' || location.pathname.startsWith('/dashboards/');
  const managementPreview = location.pathname.startsWith('/system/dashboards/');
  // 组模式 = 非管理预览(独立看板域 + 工作空间内嵌预览同用组目录)
  const groupMode = !managementPreview;

  // 看板目录 = 仪表盘组(切换维度, 替代工作空间切换)
  const [groups, setGroups] = useState<GroupView[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const loadGroups = useCallback(async () => {
    setReady(false);
    setError('');
    try {
      setGroups(await fetchVisibleGroups());
    } catch {
      setError('加载仪表盘组失败');
    } finally {
      setReady(true);
    }
  }, []);
  useEffect(() => { if (groupMode) void loadGroups(); }, [groupMode, loadGroups]);

  // 深链鲁棒: 全组找看板, 所在组即当前组(旧工作空间维度链接落组后同样可解析)
  const allBoards = groupMode ? groups.flatMap(g => g.dashboards.map(d => ({ ...d, __gid: g.id }))) : [];
  const current = allBoards.find(d => d.id === dashboardId);
  const urlGroupId = params.groupId ? Number(params.groupId) : NaN;
  const resolvedGroupId = current ? current.__gid : urlGroupId;
  const currentGroup = groupMode
    ? (groups.find(g => g.id === resolvedGroupId) || groups.find(g => g.dashboards.some(d => d.status === 'enabled')) || groups[0])
    : undefined;
  const boards = groupMode ? (currentGroup?.dashboards || []) : [];
  const enabled = boards.filter(d => d.status === 'enabled');

  // 完整看板(含 charts)按需加载; 管理预览(/system/dashboards/:id)同用
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const loadDetail = useCallback(async (id: number) => {
    if (!id) return;
    setDetailLoading(true);
    setDetail(null);
    try {
      const { data } = await client.get(`/dashboard/${id}`);
      setDetail(data);
    } catch {
      setError('加载看板失败');
    } finally {
      setDetailLoading(false);
    }
  }, []);
  useEffect(() => {
    if (managementPreview && dashboardId) void loadDetail(dashboardId);
    else if (groupMode && current?.id) void loadDetail(current.id);
  }, [managementPreview, groupMode, dashboardId, current?.id, loadDetail]);

  const navigation = useDashboardNavigation(
    independent ? (currentGroup?.id ?? 0) : 0,
    enabled,
    current?.id,
    target => navigate(`/dashboards/${currentGroup?.id ?? 0}/${target}`),
  );

  const allowed = managementPreview ? !!detail : (current && current.status === 'enabled');
  const switchGroup = (gid: number) => {
    const g = groups.find(x => x.id === gid);
    const first = (g?.dashboards || []).filter(d => d.status === 'enabled');
    const target = first.find(d => d.is_default) || first[0];
    navigate(target ? `/dashboards/${gid}/${target.id}` : `/dashboards/${gid}`);
  };

  if (groupMode && !ready) return <div className="p-8" role="status">正在加载看板…</div>;
  if ((detailLoading || (!detail && !error)) && (managementPreview || (current && current.status === 'enabled'))) return <div className="p-8" role="status">正在加载看板…</div>;
  if (independent && !dashboardId && !error && enabled.length && currentGroup) {
    const first = enabled.find(d => d.is_default) || enabled[0];
    return <Navigate to={`/dashboards/${currentGroup.id}/${first.id}`} replace />;
  }
  if (!allowed || error) {
    // 完全没有仪表盘(非报错): 居中空态引导; 其余情况(未启用/无权限/加载失败)居中提示+操作
    const emptyCase = !error && !dashboardId && !current;
    return (
      <div className="flex h-full min-h-0 flex-col items-center justify-center gap-3 p-8 text-center">
        {emptyCase ? (
          <>
            <LayoutDashboard className="h-10 w-10 text-muted-foreground" />
            <p className="text-lg font-medium">没有仪表盘</p>
            <p className="text-sm text-muted-foreground">
              {(user?.role === 'admin')
                ? '创建看板并编排组合、分配角色后，将在此展示。'
                : '当前没有分配给你的仪表盘，可联系管理员为你的角色分配看板组合。'}
            </p>
            <div className="flex items-center gap-2">
              {user?.role === 'admin' && (
                <Button size="sm" onClick={() => navigate('/system/dashboards')}>去配置仪表盘</Button>
              )}
              <Button size="sm" variant="outline" onClick={() => navigate('/')}>返回首页</Button>
            </div>
          </>
        ) : (
          <>
            <p role="status">{error || (current ? '该仪表盘未启用' : '看板不存在或无访问权限')}</p>
            <div className="flex items-center gap-2">
              {error && <Button size="sm" variant="outline" onClick={() => (managementPreview ? void loadDetail(dashboardId) : void loadGroups())}>重试</Button>}
              {independent && <Button size="sm" variant="link" onClick={() => navigate('/')}>返回首页</Button>}
              {managementPreview && <Button size="sm" variant="link" onClick={() => navigate('/system/dashboards')}>返回看板列表</Button>}
            </div>
          </>
        )}
      </div>
    );
  }
  const board = detail as any;
  const play = () => navigate(`/screen/${board.id}?group=${currentGroup?.id ?? 0}&from=${encodeURIComponent(location.pathname)}`, { state: { from: location.pathname } });
  const edit = () => navigate(`/dashboard/editor/${board.id}`, { state: { from: location.pathname } });
  return <div ref={root} className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background">
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 sm:px-6">
      {independent && navigation.directory}
      {managementPreview && <Button size="icon" variant="ghost" aria-label="返回看板列表" onClick={() => navigate('/system/dashboards')}><ArrowLeft className="h-4 w-4" /></Button>}
      {independent && groups.length > 1 && (
        <select
          aria-label="切换仪表盘组"
          className="border rounded px-2 py-1 text-sm bg-background max-w-[160px]"
          value={currentGroup?.id ?? 0}
          onChange={e => switchGroup(Number(e.target.value))}
        >
          {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
      )}
      <h1 className="min-w-0 flex-1 truncate text-lg font-semibold" title={board.name}>{board.name}</h1>
      <div className="flex shrink-0 items-center gap-1" role="toolbar" aria-label="看板操作">
        <DashboardAutoRefresh key={board.id} onRefresh={() => void loadDetail(board.id)} loading={detailLoading} menuItems={<>
          <DropdownMenuItem disabled={board.status !== 'enabled'} onClick={play}><Play className="mr-2 h-4 w-4" />大屏播放</DropdownMenuItem>
          {managementPreview && <DropdownMenuItem onClick={edit}><Edit className="mr-2 h-4 w-4" />编辑看板</DropdownMenuItem>}
        </>} />
        {managementPreview && <Button className="hidden h-8 w-8 sm:inline-flex" size="icon" variant="ghost" title="编辑看板" aria-label="编辑看板" onClick={edit}><Edit className="h-4 w-4" /></Button>}
        <Button className="hidden h-8 w-8 sm:inline-flex" size="icon" variant="ghost" title="大屏播放" aria-label="大屏播放" disabled={board.status !== 'enabled'} onClick={play}><Play className="h-4 w-4" /></Button>
        <Button className="h-8 w-8" size="icon" variant="ghost" title={fullscreen ? '退出全屏' : '全屏阅读'} aria-label={fullscreen ? '退出全屏' : '全屏阅读'} onClick={() => void toggleFullscreen()}>{fullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}</Button>
      </div>
      {/* 统一顶栏右侧: 与其他模块一致(AS-BOT / 模块切换 / 主题 / 个人中心) */}
      {independent && <div className="flex shrink-0 items-center gap-2 border-l pl-2 ml-1">
        <AsBotButton />
        <SectionSwitcher current="dashboards" />
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
            <button className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors min-h-[36px]" aria-label="个人中心">
              <Avatar className="h-6 w-6">
                <AvatarFallback className="text-xs">{user?.username?.charAt(0)?.toUpperCase() || 'U'}</AvatarFallback>
              </Avatar>
              <span className="text-sm font-medium hidden sm:inline">{user?.username}</span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => navigate('/system/profile')}>
              <UserCircle className="h-4 w-4 mr-2" />
              个人设置
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => { logout(); navigate('/login'); }}>
              <LogOut className="h-4 w-4 mr-2" />
              退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>}
    </header>
    {independent && navigation.tabs}
    {navigation.error && independent && <p role="alert" className="px-6 py-1 text-xs text-destructive">{navigation.error}，可打开目录重试。</p>}
    {library.error && <p role="alert" className="px-4 text-sm">{library.error}<Button variant="link" onClick={library.refresh}>重试</Button></p>}
    <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-muted/20">
      <DashboardRuntimeParams dashboard={board} />
      <div className="p-3 sm:p-5"><ResponsiveDashboardCanvas key={board.id} dashboard={board} items={library.items} /></div>
    </div>
    {independent && <AsBotPanel />}
  </div>;
}
