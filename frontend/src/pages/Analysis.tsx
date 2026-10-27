import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Edit, Play, Maximize, Minimize, LayoutDashboard, Layers, ChevronDown, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { ResponsiveDashboardCanvas } from '@/components/DashboardCanvas';
import { useDashboardNavigation } from '@/components/DashboardNavigation';
import { useDashboardFullscreen } from '@/hooks/useDashboardFullscreen';
import { DashboardRuntimeParams } from '@/components/DashboardParams';
import DashboardAutoRefresh from '@/components/DashboardAutoRefresh';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import AsBotButton from '@/components/AsBotButton';
import { useAuthStore } from '@/stores/authStore';
import AsBotPanel from '@/components/asbot/AsBotPanel';
import { fetchVisibleGroups, type GroupView } from '@/lib/dashboardCatalog';
import client from '@/api/client';

/**
 * 看板详情页(数据看板已收进工作空间):
 * - `/ws/:workspaceId/dashboards/:dashboardId` 工作空间内的看板详情(侧栏/顶栏由 WorkspaceLayout 提供);
 * - `/ws/:workspaceId/page/:dashboardId` 旧内嵌预览链接(同页复用);
 * - `/system/dashboards/:dashboardId` 看板管理侧的只读预览。
 * 可见集与组目录一律取 GET /dashboard/groups/visible(服务端 fail-closed)。
 */
export default function Analysis() {
  const params = useParams();
  const dashboardId = Number(params.dashboardId || params.id) || 0;
  const workspaceId = Number(params.workspaceId) || 0;
  const location = useLocation();
  const navigate = useNavigate();
  const library = useVisLibrary();
  const user = useAuthStore(s => s.user);
  const root = useRef<HTMLDivElement>(null);
  const { fullscreen, toggleFullscreen } = useDashboardFullscreen(root);
  const managementPreview = location.pathname.startsWith('/system/dashboards/');
  // 组模式 = 非管理预览(工作空间详情与旧内嵌预览同用组目录)
  const groupMode = !managementPreview;
  // 返回落点: 管理预览回看板管理, 工作空间详情回看板卡片清单
  const listPath = managementPreview ? '/system/dashboards' : workspaceId ? `/ws/${workspaceId}/dashboards` : '';
  const boardPath = (id: number) => managementPreview
    ? `/system/dashboards/${id}`
    : `/ws/${workspaceId}/dashboards/${id}`;

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
  // editable = 服务端裁决的可写性(仅属主/admin), 角色授予的可见权不含编辑权
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  // 刷新失败与首次加载失败分开：前者保留已渲染内容 + 横幅告知，不能把整页换成错误页
  const [refreshError, setRefreshError] = useState('');
  const detailCache = useRef(new Map<number, any>());
  const loadDetail = useCallback(async (id: number) => {
    if (!id) return;
    const cached = detailCache.current.get(Number(id));
    // 不再 setDetail(null)：那会把页头与内容整体换成“正在加载看板…”，看起来像重新加载了整个页面。
    // 命中缓存先渲染、未命中则继续展示上一份，后台取最新数据后原地替换。
    if (cached) setDetail(cached);
    setDetailLoading(true);
    setRefreshError('');
    try {
      const { data } = await client.get(`/dashboard/${id}`);
      detailCache.current.set(Number(id), data);
      setDetail(data);
    } catch {
      if (!cached) setError('加载看板失败');
      else setRefreshError('看板最新内容加载失败，当前显示上次加载的结果');
    } finally {
      setDetailLoading(false);
    }
  }, []);
  useEffect(() => {
    if (managementPreview && dashboardId) void loadDetail(dashboardId);
    else if (groupMode && current?.id) void loadDetail(current.id);
  }, [managementPreview, groupMode, dashboardId, current?.id, loadDetail]);

  const navigation = useDashboardNavigation(
    enabled,
    current?.id,
    target => navigate(boardPath(target)),
  );

  const allowed = managementPreview ? !!detail : (current && current.status === 'enabled');
  const switchGroup = (gid: number) => {
    const g = groups.find(x => x.id === gid);
    const first = (g?.dashboards || []).filter(d => d.status === 'enabled');
    const target = first.find(d => d.is_default) || first[0];
    navigate(target ? boardPath(target.id) : listPath);
  };

  if (groupMode && !ready) return <div className="p-8" role="status">正在加载看板…</div>;
  // 首帧没有任何内容才整页 loading；切换同组看板时保留页头与上一份内容，只在页头下显示进度条
  if (!detail && !error && (managementPreview || (current && current.status === 'enabled')))
    return <div className="p-8" role="status">正在加载看板…</div>;
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
              {!!listPath && <Button size="sm" variant="outline" onClick={() => navigate(listPath)}>返回看板清单</Button>}
              <Button size="sm" variant="ghost" onClick={() => navigate('/')}>返回首页</Button>
            </div>
          </>
        ) : (
          <>
            <p role="status">{error || (current ? '该仪表盘未启用' : '看板不存在或无访问权限')}</p>
            <div className="flex items-center gap-2">
              {error && <Button size="sm" variant="outline" onClick={() => (managementPreview ? void loadDetail(dashboardId) : void loadGroups())}>重试</Button>}
              {!!listPath && <Button size="sm" variant="link" onClick={() => navigate(listPath)}>{managementPreview ? '返回看板列表' : '返回看板清单'}</Button>}
              {!managementPreview && <Button size="sm" variant="link" onClick={() => navigate('/')}>返回首页</Button>}
            </div>
          </>
        )}
      </div>
    );
  }
  const board = detail as any;
  const wantedId = managementPreview ? dashboardId : current?.id ?? 0;
  const play = () => navigate(`/screen/${board.id}?group=${currentGroup?.id ?? 0}&from=${encodeURIComponent(location.pathname)}`, { state: { from: location.pathname } });
  const edit = () => navigate(`/dashboard/editor/${board.id}`, { state: { from: location.pathname } });
  // 看板切换不再单独占一行：组下拉 + 同组看板 pill 全部内联进页头，避免“两层页头 + 中间一条分隔线”的松散观感
  const switchTabs = groupMode ? navigation.tabs : null;
  const groupSwitch = groupMode && groups.length > 1 && <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button variant="outline" size="sm" className="h-8 shrink-0 gap-1.5 rounded-full px-3 text-xs" aria-label="切换仪表盘组">
        <Layers className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="max-w-[120px] truncate">{currentGroup?.name || '全部组合'}</span>
        <ChevronDown className="h-3.5 w-3.5 opacity-60" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="start" className="max-h-72 overflow-auto">
      {groups.map(g => <DropdownMenuItem key={g.id} onClick={() => switchGroup(g.id)} className="gap-2">
        <span className="flex-1 truncate">{g.name}</span>
        {g.id === (currentGroup?.id ?? 0) && <Check className="h-4 w-4 text-primary" />}
      </DropdownMenuItem>)}
    </DropdownMenuContent>
  </DropdownMenu>;
  return <div ref={root} className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background">
    {/* 页头不再是一片白底加一条线：card→background 的浅渐变 + 对象徽标把“工具栏”与“内容”拉开层次；
        色值一律走主题变量，9 套主题下都成立（不在组件里写裸 hex） */}
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 sm:px-5"
      style={{ backgroundImage: 'linear-gradient(180deg, hsl(var(--card)) 0%, hsl(var(--background)) 100%)' }}>
      {!!listPath && <Button size="icon" variant="ghost" className="h-8 w-8 shrink-0" aria-label={managementPreview ? '返回看板列表' : '返回看板清单'} onClick={() => navigate(listPath)}><ArrowLeft className="h-4 w-4" /></Button>}
      <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary ring-1 ring-primary/20">
        <LayoutDashboard className="h-4 w-4" />
      </span>
      {groupSwitch}
      {groupSwitch && !!switchTabs && <span aria-hidden className="h-4 w-px shrink-0 bg-border" />}
      {switchTabs
        ? <>
            {/* 同组看板切换条本身就是页头标题（选中 pill = 当前看板），不再另起一行重复同名大标题；
                h1 仍给屏幕阅读器与文档语义。 */}
            <h1 className="sr-only">{board.name}</h1>
            {switchTabs}
          </>
        : <h1 className="min-w-0 flex-1 truncate text-lg font-semibold" title={board.name}>{board.name}</h1>}
      <div className="ml-auto flex shrink-0 items-center gap-1" role="toolbar" aria-label="看板操作">
        <DashboardAutoRefresh key={board.id} onRefresh={() => void loadDetail(board.id)} loading={detailLoading} menuItems={<>
          <DropdownMenuItem disabled={board.status !== 'enabled'} onClick={play}><Play className="mr-2 h-4 w-4" />大屏播放</DropdownMenuItem>
          {managementPreview && board.editable && <DropdownMenuItem onClick={edit}><Edit className="mr-2 h-4 w-4" />编辑看板</DropdownMenuItem>}
        </>} />
        {managementPreview && board.editable && <Button className="hidden h-8 w-8 sm:inline-flex" size="icon" variant="ghost" title="编辑看板" aria-label="编辑看板" onClick={edit}><Edit className="h-4 w-4" /></Button>}
        <Button className="hidden h-8 w-8 sm:inline-flex" size="icon" variant="ghost" title="大屏播放" aria-label="大屏播放" disabled={board.status !== 'enabled'} onClick={play}><Play className="h-4 w-4" /></Button>
        <Button className="h-8 w-8" size="icon" variant="ghost" title={fullscreen ? '退出全屏' : '全屏阅读'} aria-label={fullscreen ? '退出全屏' : '全屏阅读'} onClick={() => void toggleFullscreen()}>{fullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}</Button>
      </div>
      {/* 工作空间顶栏已有模块切换/主题/个人中心, 本页只补 AS-BOT 入口 */}
      {!managementPreview && <div className="flex shrink-0 items-center gap-2 border-l pl-2 ml-1">
        <AsBotButton />
      </div>}
    </header>
    {/* 加载/切换中的细进度条：保留页头与上一份内容，不再整页换成“正在加载看板…” */}
    {detailLoading && <div role="progressbar" aria-label="正在加载看板" className="skeleton h-0.5 w-full shrink-0 rounded-none" />}
    {!!refreshError && <p role="alert" className="flex shrink-0 items-center gap-2 px-3 py-1 text-xs text-destructive sm:px-6">{refreshError}
      <Button size="sm" variant="link" className="h-auto p-0 text-xs" onClick={() => void loadDetail(wantedId || board.id)}>重试</Button></p>}
    {library.error && <p role="alert" className="px-4 text-sm">{library.error}<Button variant="link" onClick={library.refresh}>重试</Button></p>}
    <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden" style={{ backgroundImage: 'linear-gradient(180deg, hsl(var(--muted) / 0.45) 0%, hsl(var(--muted) / 0.15) 220px, transparent 100%)' }}>
      <DashboardRuntimeParams dashboard={board} />
      <div className="p-4 sm:p-6"><ResponsiveDashboardCanvas key={board.id} dashboard={board} items={library.items} /></div>
    </div>
    {!managementPreview && <AsBotPanel />}
  </div>;
}
