import { useEffect, useRef, useState } from 'react';
import { Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Edit, Play, Maximize, Minimize } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ResponsiveDashboardCanvas } from '@/components/DashboardCanvas';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { useDashboardNavigation } from '@/components/DashboardNavigation';
import { useDashboardFullscreen } from '@/hooks/useDashboardFullscreen';
import { DashboardRuntimeParams } from '@/components/DashboardParams';
import DashboardAutoRefresh from '@/components/DashboardAutoRefresh';
import { useDashboardStore } from '@/stores/dashboardStore';
import { useVisLibrary } from '@/hooks/useVisLibrary';

export default function Analysis() {
  const { dashboardId, id, workspaceId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const store = useDashboardStore();
  const library = useVisLibrary();
  const scope = Number(workspaceId) || 0;
  const [readyScope, setReadyScope] = useState<number | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const { fullscreen, toggleFullscreen } = useDashboardFullscreen(root);
  const independent = location.pathname.startsWith('/dashboards/');
  const enabled = readyScope === scope && store.currentWorkspaceId === scope ? store.dashboards.filter(d => d.status === 'enabled') : [];
  const current = readyScope === scope && store.currentWorkspaceId === scope ? store.dashboards.find(d => d.id === Number(dashboardId || id)) : undefined;
  const navigation = useDashboardNavigation(independent ? scope : 0, enabled, current?.id, target => navigate(`/dashboards/${scope}/${target}`));
  const managementPreview = location.pathname.startsWith('/system/dashboards/');
  // 工作空间内嵌预览：保留侧栏/顶栏，不提供“返回看板列表”，有需要时由“播放”按钮进入 /screen/:id 全屏轮播。
  const allowed = current && (managementPreview || current.status === 'enabled');
  useEffect(() => {
    let cancelled = false;
    setReadyScope(null);
    void store.loadDashboards(scope).then(() => { if (!cancelled) setReadyScope(scope); });
    return () => { cancelled = true; };
  }, [scope, store.loadDashboards]);
  useEffect(() => { if (allowed) store.setCurrent(current.id); }, [current?.id, allowed]);
  if (readyScope !== scope || store.loading) return <div className="p-8" role="status">正在加载看板…</div>;
  if (!dashboardId && independent && !store.error && enabled.length) {
    const first = enabled.find(d => d.is_default) || enabled[0];
    return <Navigate to={`/dashboards/${scope}/${first.id}`} replace />;
  }
  if (!allowed || store.error) return <div className="p-8 space-y-3">
    {independent && navigation.directory}
    <p role="status">{store.error || (current ? '该仪表盘未启用' : dashboardId ? '看板不存在或无访问权限' : '当前工作空间暂无已启用的看板，请切换工作空间或联系管理员')}</p>
    {store.error && <Button variant="outline" onClick={() => void store.loadDashboards(scope)}>重试</Button>}
    {managementPreview && <Button variant="link" onClick={() => navigate('/system/dashboards')}>返回看板列表</Button>}
  </div>;
  const play = () => navigate(`/screen/${current.id}?workspace_id=${scope}&from=${encodeURIComponent(location.pathname)}`, { state: { from: location.pathname } });
  const edit = () => navigate(`/dashboard/editor/${current.id}`, { state: { from: location.pathname } });
  return <div ref={root} className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background">
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 sm:px-6">
      {independent && navigation.directory}
      {managementPreview && <Button size="icon" variant="ghost" aria-label="返回看板列表" onClick={() => navigate('/system/dashboards')}><ArrowLeft className="h-4 w-4" /></Button>}
      <h1 className="min-w-0 flex-1 truncate text-lg font-semibold" title={current.name}>{current.name}</h1>
      <div className="flex shrink-0 items-center gap-1" role="toolbar" aria-label="看板操作">
        <DashboardAutoRefresh key={current.id} onRefresh={store.refreshCharts} loading={store.refreshing} menuItems={<>
          <DropdownMenuItem disabled={current.status !== 'enabled'} onClick={play}><Play className="mr-2 h-4 w-4" />大屏播放</DropdownMenuItem>
          {managementPreview && <DropdownMenuItem onClick={edit}><Edit className="mr-2 h-4 w-4" />编辑看板</DropdownMenuItem>}
        </>} />
        {managementPreview && <Button className="hidden h-8 w-8 sm:inline-flex" size="icon" variant="ghost" title="编辑看板" aria-label="编辑看板" onClick={edit}><Edit className="h-4 w-4" /></Button>}
        <Button className="hidden h-8 w-8 sm:inline-flex" size="icon" variant="ghost" title="大屏播放" aria-label="大屏播放" disabled={current.status !== 'enabled'} onClick={play}><Play className="h-4 w-4" /></Button>
        <Button className="h-8 w-8" size="icon" variant="ghost" title={fullscreen ? '退出全屏' : '全屏阅读'} aria-label={fullscreen ? '退出全屏' : '全屏阅读'} onClick={() => void toggleFullscreen()}>{fullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}</Button>
      </div>
    </header>
    {independent && navigation.tabs}
    {navigation.error && independent && <p role="alert" className="px-6 py-1 text-xs text-destructive">{navigation.error}，可打开目录重试。</p>}
    {library.error && <p role="alert" className="px-4 text-sm">{library.error}<Button variant="link" onClick={library.refresh}>重试</Button></p>}
    <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-muted/20">
      <DashboardRuntimeParams dashboard={current} />
      <div className="p-3 sm:p-5"><ResponsiveDashboardCanvas key={current.id} dashboard={current} items={library.items} /></div>
    </div>
  </div>;
}
