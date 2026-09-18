import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, X, Pause, Play, Maximize } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { FittedDashboardCanvas } from './DashboardCanvas';
import { DashboardRuntimeParams } from './DashboardParams';
import DashboardAutoRefresh from './DashboardAutoRefresh';
import { useDashboardStore, type Dashboard } from '@/stores/dashboardStore';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { isEditingTarget } from '@/lib/dashboardDesign';

interface Props { dashboards: Dashboard[]; startIndex?: number; interval?: number; onClose: () => void }
export default function CarouselView({ dashboards, startIndex = 0, interval = 10, onClose }: Props) {
  const store = useDashboardStore();
  const library = useVisLibrary();
  const previous = useRef(store.currentId);
  const [index, setIndex] = useState(startIndex);
  const [paused, setPaused] = useState(false);
  const [seconds, setSeconds] = useState(interval);
  const available = dashboards.filter(d => d.status === 'enabled');
  const selected = available[index % Math.max(available.length, 1)];
  const current = store.dashboards.find(d => d.id === selected?.id) || selected;
  const go = useCallback((delta: number) => { if (available.length) setIndex(i => (i + delta + available.length) % available.length); }, [available.length]);
  useEffect(() => {
    if (paused || seconds <= 0 || available.length < 2) return;
    const timer = setInterval(() => go(1), seconds * 1000); return () => clearInterval(timer);
  }, [paused, seconds, go, available.length]);
  useEffect(() => { if (current) store.setCurrent(current.id); }, [current?.id]);
  useEffect(() => () => { useDashboardStore.getState().setCurrent(previous.current); if (document.fullscreenElement) void document.exitFullscreen().catch(() => {}); }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (isEditingTarget(e.target) || document.querySelector('[role="dialog"]')) return;
      if (e.key === 'Escape' && !document.fullscreenElement) onClose();
      if (e.key === 'ArrowRight') go(1);
      if (e.key === 'ArrowLeft') go(-1);
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [go, onClose]);
  return <div className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-background">
    <header className="flex flex-wrap items-center gap-3 border-b p-3"><h2 className="min-w-0 flex-1 truncate font-semibold">{current?.name || '暂无已启用看板'}</h2>
      <select aria-label="轮播秒数" className="rounded border bg-background p-2 text-xs" value={seconds} onChange={e => setSeconds(Number(e.target.value))}>{[...new Set([5, 10, 30, 60, interval])].map(v => <option key={v} value={v}>{v} 秒</option>)}</select>
      <Button size="icon" variant="ghost" title={paused ? '恢复轮播' : '暂停轮播'} onClick={() => setPaused(v => !v)}>{paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}</Button>
      <Button size="icon" variant="ghost" title="全屏" onClick={() => document.fullscreenElement ? document.exitFullscreen().catch(() => {}) : document.documentElement.requestFullscreen().catch(() => {})}><Maximize className="h-4 w-4" /></Button>
      <Button size="icon" variant="ghost" title="退出轮播" onClick={onClose}><X className="h-4 w-4" /></Button>
    </header>
    {library.error && <p role="alert" className="px-4 text-xs">{library.error}<Button variant="link" onClick={library.refresh}>重试</Button></p>}
    {current && <><DashboardAutoRefresh key={current.id} onRefresh={store.refreshCharts} loading={store.refreshing} /><DashboardRuntimeParams dashboard={current} /><div className="relative min-h-0 flex-1"><FittedDashboardCanvas key={current.id} dashboard={current} items={library.items} /></div></>}
    {available.length > 1 && <footer className="flex items-center justify-center gap-4 border-t p-2"><Button size="icon" variant="ghost" onClick={() => go(-1)}><ChevronLeft className="h-4 w-4" /></Button><div className="flex flex-wrap gap-2">{available.map((d, i) => <button aria-label={`播放 ${d.name}`} key={d.id} className={`h-2 rounded-full ${current?.id === d.id ? 'w-6 bg-foreground' : 'w-2 bg-muted-foreground/30'}`} onClick={() => setIndex(i)} />)}</div><Button size="icon" variant="ghost" onClick={() => go(1)}><ChevronRight className="h-4 w-4" /></Button></footer>}
  </div>;
}
