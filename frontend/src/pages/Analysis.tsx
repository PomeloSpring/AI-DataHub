import { useEffect } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Edit, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { FittedDashboardCanvas } from '@/components/DashboardCanvas';
import { DashboardRuntimeParams } from '@/components/DashboardParams';
import DashboardAutoRefresh from '@/components/DashboardAutoRefresh';
import { useDashboardStore } from '@/stores/dashboardStore';
import { useVisLibrary } from '@/hooks/useVisLibrary';

export default function Analysis() {
  const { dashboardId, id } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const store = useDashboardStore();
  const library = useVisLibrary();
  const current = store.dashboards.find(d => d.id === Number(dashboardId || id));
  const managementPreview = location.pathname.startsWith('/system/dashboards/');
  const allowed = current && (managementPreview || current.status === 'enabled');
  useEffect(() => { void store.loadDashboards(); }, []);
  useEffect(() => { if (allowed) store.setCurrent(current.id); }, [current?.id, allowed]);
  if (!allowed) return <div className="p-8"><p>{store.loading ? '正在加载…' : store.error || (current ? '该仪表盘未启用' : '未找到仪表盘')}</p><Button variant="link" onClick={() => navigate('/system/dashboards')}>返回看板列表</Button></div>;
  return <div className="flex h-full min-h-0 flex-col bg-background">
    <header className="flex flex-wrap items-center gap-3 border-b p-4"><Button size="sm" variant="ghost" onClick={() => navigate('/system/dashboards')}><ArrowLeft className="mr-2 h-4 w-4" />返回</Button><h1 className="min-w-0 flex-1 truncate font-semibold">{current.name}</h1><span className="text-xs text-muted-foreground">只读预览</span>
      <Button size="sm" variant="outline" onClick={() => navigate(`/dashboard/editor/${current.id}`, { state: { from: location.pathname } })}><Edit className="mr-2 h-4 w-4" />编辑</Button>
      <Button size="sm" disabled={current.status !== 'enabled'} onClick={() => navigate(`/screen/${current.id}`)}><Play className="mr-2 h-4 w-4" />播放</Button>
    </header>
    {library.error && <p role="alert" className="px-4 text-sm">{library.error}<Button variant="link" onClick={library.refresh}>重试</Button></p>}
    <DashboardAutoRefresh key={current.id} onRefresh={store.refreshCharts} loading={store.refreshing} />
    <DashboardRuntimeParams dashboard={current} />
    <div className="relative min-h-0 flex-1 bg-muted/20"><FittedDashboardCanvas key={current.id} dashboard={current} items={library.items} /></div>
  </div>;
}
