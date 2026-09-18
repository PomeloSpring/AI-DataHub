import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ChevronLeft, ChevronRight, Edit, Maximize, Minimize, Pause, Play, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { FittedDashboardCanvas } from '@/components/DashboardCanvas';
import { DashboardRuntimeParams } from '@/components/DashboardParams';
import { useDashboardStore } from '@/stores/dashboardStore';
import { useAuthStore } from '@/stores/authStore';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { isEditingTarget } from '@/lib/dashboardDesign';
import client from '@/api/client';

export default function Screen() {
  const { dashboardId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const store = useDashboardStore();
  const user = useAuthStore(s => s.user);
  const library = useVisLibrary();
  const [menuIds, setMenuIds] = useState<number[] | null>(null);
  const [menuReady, setMenuReady] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [visible, setVisible] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [paused, setPaused] = useState(false);
  const [interval, setIntervalValue] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const enabled = store.dashboards.filter(d => d.status === 'enabled' && (!menuIds || menuIds.includes(d.id)));
  const current = menuReady ? enabled.find(d => d.id === Number(dashboardId)) : undefined;
  const index = enabled.findIndex(d => d.id === current?.id);
  useEffect(() => { void store.loadDashboards(); }, []);
  useEffect(() => {
    let cancelled = false;
    client.get('/admin/menu-tree').then(({ data }) => {
      const ids: number[] = [];
      const walk = (items: any[]) => { for (const item of items) { if (item.page_id && item.link_type === 'screen') ids.push(Number(item.page_id)); if (Array.isArray(item.children)) walk(item.children); } };
      if (Array.isArray(data)) walk(data);
      if (!cancelled && ids.length) setMenuIds(ids);
    }).catch(() => { /* 可访问看板仍由服务端授权控制。 */ }).finally(() => { if (!cancelled) setMenuReady(true); });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    if (!current) return;
    store.setCurrent(current.id);
    try { setRefresh(Number(localStorage.getItem(`screen_refresh_${current.id}`)) || 0); } catch { setRefresh(0); }
  }, [current?.id]);
  const go = useCallback((delta: number) => {
    if (!enabled.length) return;
    navigate(`/screen/${enabled[(index + delta + enabled.length) % enabled.length].id}`, { replace: true });
  }, [enabled.map(d => d.id).join(','), index, navigate]);
  useEffect(() => {
    if (!current || !interval || paused || enabled.length < 2) return;
    const id = window.setInterval(() => go(1), interval * 1000);
    return () => window.clearInterval(id);
  }, [current?.id, interval, paused, go]);
  useEffect(() => {
    if (!current || refresh <= 0 || paused) return;
    const id = window.setInterval(() => { if (useDashboardStore.getState().currentId === current.id && !useDashboardStore.getState().refreshing) void store.refreshCharts(); }, refresh * 1000);
    return () => window.clearInterval(id);
  }, [current?.id, refresh, paused]);
  const toggleFullscreen = useCallback(async () => {
    if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
    else await document.documentElement.requestFullscreen().catch(() => {});
  }, []);
  useEffect(() => {
    const change = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', change);
    return () => { document.removeEventListener('fullscreenchange', change); if (document.fullscreenElement) void document.exitFullscreen().catch(() => {}); };
  }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (isEditingTarget(e.target) || document.querySelector('[role="dialog"]')) return;
      if (e.key === 'Escape' && !document.fullscreenElement) navigate('/system/dashboards');
      if (e.key.toLowerCase() === 'f' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); void toggleFullscreen(); }
      if (e.key === 'ArrowRight') go(1);
      if (e.key === 'ArrowLeft') go(-1);
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [go, toggleFullscreen, navigate]);
  const showControls = () => { setVisible(true); clearTimeout(timer.current); timer.current = setTimeout(() => setVisible(false), 5000); };
  useEffect(() => { showControls(); return () => clearTimeout(timer.current); }, []);
  if (!current) return <div className="p-8 text-center"><p>{store.loading || !menuReady ? '正在加载…' : store.error || '该看板不存在、未启用或不在大屏展示范围'}</p><Button variant="link" onClick={() => navigate('/system/dashboards')}>返回看板列表</Button></div>;
  return <div className="fixed inset-0 z-40 flex flex-col overflow-hidden bg-background" onMouseMove={showControls} onTouchStart={showControls}>
    <header className={`z-10 flex flex-wrap items-center gap-2 border-b bg-background/95 p-3 ${fullscreen && !visible ? 'absolute inset-x-0 top-0 opacity-0 focus-within:opacity-100 hover:opacity-100' : ''}`}>
      <Button size="sm" variant="ghost" onClick={() => navigate('/system/dashboards')}><ArrowLeft className="h-4 w-4" /><span className="sr-only">退出大屏</span></Button>
      <h1 className="min-w-0 flex-1 truncate font-semibold">{current.name}</h1>
      <select aria-label="切换看板" className="max-w-[180px] rounded border bg-background p-2 text-xs" value={current.id} onChange={e => navigate(`/screen/${e.target.value}`, { replace: true })}>{enabled.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
      <select aria-label="自动刷新" className="rounded border bg-background p-2 text-xs" value={refresh} onChange={e => { const value = Number(e.target.value); setRefresh(value); try { localStorage.setItem(`screen_refresh_${current.id}`, String(value)); } catch { /* 本机偏好不可用不影响播放。 */ } }}>{[0, 5, 10, 30, 60, 300].map(v => <option key={v} value={v}>{v ? `刷新 ${v} 秒` : '关闭自动刷新'}</option>)}</select>
      <select aria-label="轮播间隔" className="rounded border bg-background p-2 text-xs" value={interval} onChange={e => setIntervalValue(Number(e.target.value))}>{[0, 5, 10, 15, 30, 60, 120, 300].map(v => <option key={v} value={v}>{v ? `轮播 ${v} 秒` : '关闭轮播'}</option>)}</select>
      <Button size="icon" variant="ghost" title={paused ? '恢复播放' : '暂停播放'} onClick={() => setPaused(v => !v)}>{paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}</Button>
      <Button size="icon" variant="ghost" title="刷新数据" disabled={store.refreshing} onClick={() => store.refreshCharts()}><RefreshCw className={`h-4 w-4 ${store.refreshing ? 'animate-spin' : ''}`} /></Button>
      <Button size="icon" variant="ghost" title="全屏" onClick={toggleFullscreen}>{fullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}</Button>
      {(user?.role === 'admin' || user?.id === current.owner_id) && <Button size="sm" variant="outline" onClick={() => navigate(`/dashboard/editor/${current.id}`, { state: { from: location.pathname } })}><Edit className="mr-2 h-4 w-4" />编辑外观与数据</Button>}
    </header>
    {library.error && <div role="alert" className="px-4 text-xs">{library.error}<Button variant="link" onClick={library.refresh}>重试</Button></div>}
    {(!fullscreen || visible) && <DashboardRuntimeParams dashboard={current} />}
    <div className="relative min-h-0 flex-1"><FittedDashboardCanvas key={current.id} dashboard={current} items={library.items} /></div>
    {!fullscreen && enabled.length > 1 && <footer className="flex items-center justify-center gap-4 border-t p-1"><Button size="icon" variant="ghost" onClick={() => go(-1)}><ChevronLeft className="h-4 w-4" /></Button><span className="text-xs">{index + 1} / {enabled.length}</span><Button size="icon" variant="ghost" onClick={() => go(1)}><ChevronRight className="h-4 w-4" /></Button></footer>}
  </div>;
}
