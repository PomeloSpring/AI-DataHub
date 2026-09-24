import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { WorkspaceBoundary, WorkspaceEntry } from '@/components/WorkspaceRoute';
import { ArrowLeft, Edit, Maximize, Minimize, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { FittedDashboardCanvas } from '@/components/DashboardCanvas';
import { DashboardRuntimeParams } from '@/components/DashboardParams';
import { useDashboardStore } from '@/stores/dashboardStore';
import { useAuthStore } from '@/stores/authStore';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { isEditingTarget } from '@/lib/dashboardDesign';
import DashboardAutoRefresh from '@/components/DashboardAutoRefresh';
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger, DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { useDashboardFullscreen } from '@/hooks/useDashboardFullscreen';
import client from '@/api/client';

function playbackScope(searchText: string, state: { from?: string } | null) {
  const search = new URLSearchParams(searchText);
  const origin = search.get('from') || state?.from;
  const returnPath = typeof origin === 'string' && /^\/(?:ws|dashboards|system)\//.test(origin) && !origin.includes('\\') ? origin : '/dashboards';
  const sourceScope = returnPath.match(/^\/(?:ws|dashboards)\/(\d+)/)?.[1];
  const raw = search.get('workspace_id') ?? sourceScope;
  return { returnPath, scope: raw === undefined ? undefined : Number(raw), sourceScope };
}
export default function Screen() {
  const location = useLocation();
  const { scope, returnPath, sourceScope } = playbackScope(location.search, location.state);
  if (scope === undefined && !returnPath.startsWith('/system/')) return <WorkspaceEntry module="screen" legacyId />;
  if ((scope !== undefined && (!Number.isSafeInteger(scope) || scope < 0)) || (scope === 0 && !returnPath.startsWith('/system/')) || (sourceScope && Number(sourceScope) !== scope)) return <div role="alert" className="p-8">播放链接的工作空间范围无效。<a href="/dashboards" className="underline">返回数据看板</a></div>;
  return scope ? <WorkspaceBoundary scope={scope}><ScreenPlayer key={scope} /></WorkspaceBoundary> : <ScreenPlayer />;
}
function ScreenPlayer() {
  const { dashboardId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  // 退出大屏时回到用户进入前的页面（工作空间内嵌预览/系统看板列表）；默认回系统看板列表。
  const context = playbackScope(location.search, location.state);
  const { returnPath } = context;
  const scope = context.scope ?? 0;
  // 从工作空间内嵌页“播放”进入时，大屏仍属于工作空间展示域 → 保持只读（不开放“编辑外观与数据”入口）。
  const fromWorkspace = !returnPath.startsWith('/system/');
  const store = useDashboardStore();
  const user = useAuthStore(s => s.user);
  const library = useVisLibrary();
  const [menuIds, setMenuIds] = useState<number[] | null>(null);
  const [menuReady, setMenuReady] = useState(false);
  const [ready, setReady] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const { fullscreen, toggleFullscreen } = useDashboardFullscreen(root);
  const [visible, setVisible] = useState(true);
  const [menuError, setMenuError] = useState('');
  const [retry, setRetry] = useState(0);
  const [paused, setPaused] = useState(false);
  const [interval, setIntervalValue] = useState(() => {
    const value = Number(new URLSearchParams(location.search).get('interval'));
    return Number.isSafeInteger(value) && value >= 3 && value <= 3600 ? value : 0;
  });
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const enabled = store.currentWorkspaceId === scope ? store.dashboards.filter(d => d.status === 'enabled' && (!fromWorkspace || !menuIds || menuIds.includes(d.id) || d.id === Number(dashboardId))) : [];
  const current = menuReady && ready ? enabled.find(d => d.id === Number(dashboardId)) : undefined;
  const index = enabled.findIndex(d => d.id === current?.id);
  useEffect(() => {
    let cancelled = false; setReady(false);
    void store.loadDashboards(scope).then(() => { if (!cancelled) setReady(true); });
    return () => { cancelled = true; };
  }, [scope, retry]);
  useEffect(() => {
    let cancelled = false;
    setMenuReady(false); setMenuIds(null); setMenuError('');
    client.get(`/admin/menu-tree?workspace_id=${scope}`).then(({ data }) => {
      const ids: number[] = [];
      const walk = (items: any[]) => { for (const item of items) { if (item.page_id && item.link_type === 'screen') ids.push(Number(item.page_id)); if (Array.isArray(item.children)) walk(item.children); } };
      if (!Array.isArray(data)) throw new Error('播放目录格式错误');
      walk(data);
      if (!cancelled && ids.length) setMenuIds(ids);
    }).catch(() => { if (!cancelled) setMenuError('播放目录加载失败，请重试'); }).finally(() => { if (!cancelled) setMenuReady(true); });
    return () => { cancelled = true; };
  }, [scope, retry]);
  useEffect(() => {
    if (!current) return;
    store.setCurrent(current.id);
  }, [current?.id]);
  const go = useCallback((delta: number) => {
    if (!enabled.length) return;
    navigate(`/screen/${enabled[(index + delta + enabled.length) % enabled.length].id}${location.search}`, { replace: true, state: location.state });
  }, [enabled.map(d => d.id).join(','), index, navigate, location.state, location.search]);
  useEffect(() => {
    if (!current || !interval || paused || enabled.length < 2) return;
    const id = window.setInterval(() => go(1), interval * 1000);
    return () => window.clearInterval(id);
  }, [current?.id, interval, paused, go]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isEditingTarget(e.target) || document.querySelector('[role="dialog"],[role="menu"]')) return;
      if (e.key === 'Escape' && !document.fullscreenElement) navigate(returnPath);
      if (e.key.toLowerCase() === 'f' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); void toggleFullscreen(); }
      if (e.key === 'ArrowRight') go(1);
      if (e.key === 'ArrowLeft') go(-1);
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [go, toggleFullscreen, navigate, returnPath]);
  const showControls = () => { setVisible(true); clearTimeout(timer.current); timer.current = setTimeout(() => setVisible(false), 5000); };
  useEffect(() => { showControls(); return () => clearTimeout(timer.current); }, []);
  if (!dashboardId && ready && menuReady && !menuError && !store.error && enabled.length) return <Navigate to={`/screen/${(enabled.find(d => d.is_default) || enabled[0]).id}${location.search}`} replace state={location.state} />;
  if (!current || menuError || store.error) return <div className="p-8 text-center"><p role="status">{store.loading || !menuReady || !ready ? '正在加载…' : menuError || store.error || '该看板不存在、未启用或不在大屏展示范围'}</p>{(menuError || store.error) && <Button variant="outline" onClick={() => setRetry(v => v + 1)}>重试</Button>}<Button variant="link" onClick={() => navigate(returnPath)}>返回看板列表</Button></div>;
  return <div ref={root} className="fixed inset-0 z-40 flex flex-col overflow-hidden bg-background" onMouseMove={showControls} onTouchStart={showControls}>
    <header className={`z-10 flex h-12 shrink-0 items-center gap-1 border-b bg-background/95 px-3 ${fullscreen ? `absolute inset-x-0 top-0 transition-opacity ${visible ? '' : 'opacity-0 focus-within:opacity-100 hover:opacity-100'}` : ''}`}>
      <Button size="sm" variant="ghost" onClick={() => navigate(returnPath)}><ArrowLeft className="h-4 w-4" /><span className="sr-only">退出大屏</span></Button>
      <h1 className="min-w-0 flex-1 truncate font-semibold">{current.name}</h1>
      <DashboardAutoRefresh key={current.id} onRefresh={store.refreshCharts} loading={store.refreshing} menuItems={<>
        <div className="space-y-2 p-2">
          <label className="block text-xs">切换看板<select aria-label="切换看板" className="mt-1 w-full rounded border bg-background p-2" value={current.id} onChange={e => navigate(`/screen/${e.target.value}${location.search}`, { replace: true, state: location.state })}>{enabled.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
          <label className="block text-xs">轮播间隔<select aria-label="轮播间隔" className="mt-1 w-full rounded border bg-background p-2" value={interval} onChange={e => setIntervalValue(Number(e.target.value))}>{[...new Set([0, 5, 10, 15, 30, 60, 120, 300, interval])].sort((a, b) => a - b).map(v => <option key={v} value={v}>{v ? `每 ${v} 秒` : '关闭轮播'}</option>)}</select></label>
        </div>
        <DropdownMenuItem onClick={() => setPaused(v => !v)}>{paused ? '恢复轮播' : '暂停轮播'}</DropdownMenuItem>
        {enabled.length > 1 && <><DropdownMenuItem onClick={() => go(-1)}>上一看板</DropdownMenuItem><DropdownMenuItem onClick={() => go(1)}>下一看板</DropdownMenuItem></>}
      </>} />
      {!!(current.params?.length || current.page_params?.length) && <DropdownMenu><DropdownMenuTrigger asChild><Button size="icon" variant="ghost" title="筛选条件" aria-label="筛选条件"><SlidersHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end" className="w-80"><DashboardRuntimeParams dashboard={current} /></DropdownMenuContent></DropdownMenu>}
      <Button size="icon" variant="ghost" title="全屏" aria-label="切换全屏播放" onClick={toggleFullscreen}>{fullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}</Button>
      {!fromWorkspace && (user?.role === 'admin' || user?.id === current.owner_id) && <Button size="icon" variant="ghost" aria-label="编辑外观与数据" title="编辑外观与数据" onClick={() => navigate(`/dashboard/editor/${current.id}`, { state: { from: location.pathname + location.search } })}><Edit className="h-4 w-4" /></Button>}
    </header>
    {library.error && <div role="alert" className="px-4 text-xs">{library.error}<Button variant="link" onClick={library.refresh}>重试</Button></div>}
    <div className="relative min-h-0 flex-1"><FittedDashboardCanvas key={current.id} dashboard={current} items={library.items} /></div>
  </div>;
}
