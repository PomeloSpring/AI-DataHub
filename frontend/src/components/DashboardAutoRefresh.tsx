import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { RefreshCw, Clock, MoreHorizontal } from 'lucide-react';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuLabel, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

interface DashboardAutoRefreshProps {
  onRefresh: () => void | boolean | Promise<void | boolean>;
  loading?: boolean;
  menuItems?: ReactNode;
}

const REFRESH_INTERVALS = [
  { value: '0', label: '关闭' },
  { value: '5', label: '5秒' },
  { value: '10', label: '10秒' },
  { value: '30', label: '30秒' },
  { value: '60', label: '1分钟' },
  { value: '300', label: '5分钟' },
  { value: '600', label: '10分钟' },
  { value: '1800', label: '30分钟' },
];

export default function DashboardAutoRefresh({ onRefresh, loading, menuItems }: DashboardAutoRefreshProps) {
  const [interval, setInterval] = useState('0');
  const [paused, setPaused] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const mounted = useRef(true);
  const latest = useRef({ onRefresh, loading }); latest.current = { onRefresh, loading };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const refresh = useCallback(async () => {
    if (running.current || latest.current.loading) return;
    running.current = true; setBusy(true);
    try {
      const success = await latest.current.onRefresh();
      if (mounted.current) { setFailed(success === false); if (success !== false) setLastRefresh(new Date()); }
    } catch {
      if (mounted.current) { setFailed(true); toast.error('看板刷新失败，请重试'); }
    } finally { running.current = false; if (mounted.current) setBusy(false); }
  }, []);
  useEffect(() => {
    if (!Number(interval) || paused) return;
    const timer = window.setInterval(() => void refresh(), Number(interval) * 1000);
    return () => window.clearInterval(timer);
  }, [interval, paused, refresh]);
  return <div className="flex shrink-0 items-center gap-1" data-testid="dashboard-refresh-actions">
    <Tooltip><TooltipTrigger asChild>
      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={failed ? '刷新失败，重试' : '刷新数据'} disabled={busy || loading} onClick={() => void refresh()}>
        <RefreshCw className={`h-4 w-4 ${busy || loading ? 'animate-spin' : ''} ${failed ? 'text-destructive' : ''}`} />
      </Button>
    </TooltipTrigger><TooltipContent>{failed ? '部分图表刷新失败，请重试' : '刷新数据'}</TooltipContent></Tooltip>
    <DropdownMenu><DropdownMenuTrigger asChild>
      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="自动刷新与更多操作" title="自动刷新与更多操作">
        <Clock className={`hidden h-4 w-4 sm:block ${Number(interval) && !paused ? 'text-primary' : ''}`} /><MoreHorizontal className="h-4 w-4 sm:hidden" />
      </Button>
    </DropdownMenuTrigger><DropdownMenuContent align="end" className="w-56">
      <DropdownMenuLabel>自动刷新</DropdownMenuLabel>
      <div className="px-2 py-1"><select aria-label="自动刷新间隔" className="w-full rounded border bg-background p-2 text-sm" value={interval} onChange={e => { setInterval(e.target.value); setPaused(false); }}>
        {REFRESH_INTERVALS.map(item => <option key={item.value} value={item.value}>{item.value === '0' ? '关闭自动刷新' : `每 ${item.label}`}</option>)}
      </select></div>
      {Number(interval) > 0 && <DropdownMenuItem onClick={() => setPaused(v => !v)}>{paused ? '继续自动刷新' : '暂停自动刷新'}</DropdownMenuItem>}
      <p className="px-2 py-2 text-xs text-muted-foreground">{failed ? '部分图表刷新失败，请重试' : lastRefresh ? `最近成功刷新 ${lastRefresh.toLocaleTimeString('zh-CN')}` : '尚未手动或自动刷新'}</p>
      {menuItems && <><DropdownMenuSeparator />{menuItems}</>}
    </DropdownMenuContent></DropdownMenu>
  </div>;
}
