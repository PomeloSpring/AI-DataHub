import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, Clock, MoreHorizontal } from 'lucide-react';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuLabel, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { toast } from '@/lib/toast';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import i18n from '@/i18n';

interface DashboardAutoRefreshProps {
  onRefresh: () => void | boolean | Promise<void | boolean>;
  loading?: boolean;
  menuItems?: ReactNode;
}

// 间隔档位只存 key，文案渲染期 t() 解析（模块顶层写死文案会冻结语言）
const REFRESH_INTERVALS = [
  { value: '0', labelKey: 'intervalOff' },
  { value: '5', labelKey: 'interval5s' },
  { value: '10', labelKey: 'interval10s' },
  { value: '30', labelKey: 'interval30s' },
  { value: '60', labelKey: 'interval1m' },
  { value: '300', labelKey: 'interval5m' },
  { value: '600', labelKey: 'interval10m' },
  { value: '1800', labelKey: 'interval30m' },
] as const;

export default function DashboardAutoRefresh({ onRefresh, loading, menuItems }: DashboardAutoRefreshProps) {
  const { t } = useTranslation(['dashboard']);
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
      if (mounted.current) { setFailed(true); toast.error(t('autoRefresh.failedToast')); }
    } finally { running.current = false; if (mounted.current) setBusy(false); }
  }, [t]);
  useEffect(() => {
    if (!Number(interval) || paused) return;
    const timer = window.setInterval(() => void refresh(), Number(interval) * 1000);
    return () => window.clearInterval(timer);
  }, [interval, paused, refresh]);
  return <div className="flex shrink-0 items-center gap-1" data-testid="dashboard-refresh-actions">
    <Tooltip><TooltipTrigger asChild>
      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={failed ? t('autoRefresh.retryAria') : t('autoRefresh.refreshAria')} disabled={busy || loading} onClick={() => void refresh()}>
        <RefreshCw className={`h-4 w-4 ${busy || loading ? 'animate-spin' : ''} ${failed ? 'text-destructive' : ''}`} />
      </Button>
    </TooltipTrigger><TooltipContent>{failed ? t('autoRefresh.partialFailed') : t('autoRefresh.refreshAria')}</TooltipContent></Tooltip>
    <DropdownMenu><DropdownMenuTrigger asChild>
      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={t('autoRefresh.moreAria')} title={t('autoRefresh.moreAria')}>
        <Clock className={`hidden h-4 w-4 sm:block ${Number(interval) && !paused ? 'text-primary' : ''}`} /><MoreHorizontal className="h-4 w-4 sm:hidden" />
      </Button>
    </DropdownMenuTrigger><DropdownMenuContent align="end" className="w-56">
      <DropdownMenuLabel>{t('autoRefresh.label')}</DropdownMenuLabel>
      <div className="px-2 py-1"><select aria-label={t('autoRefresh.intervalAria')} className="w-full rounded border bg-background p-2 text-sm" value={interval} onChange={e => { setInterval(e.target.value); setPaused(false); }}>
        {REFRESH_INTERVALS.map(item => <option key={item.value} value={item.value}>{item.value === '0' ? t('autoRefresh.offOption') : t('autoRefresh.every', { interval: t(`autoRefresh.${item.labelKey}` as 'dashboard:autoRefresh.intervalOff') })}</option>)}
      </select></div>
      {Number(interval) > 0 && <DropdownMenuItem onClick={() => setPaused(v => !v)}>{paused ? t('autoRefresh.resume') : t('autoRefresh.pause')}</DropdownMenuItem>}
      <p className="px-2 py-2 text-xs text-muted-foreground">{failed ? t('autoRefresh.partialFailed') : lastRefresh ? t('autoRefresh.lastSuccess', { time: lastRefresh.toLocaleTimeString(i18n.language) }) : t('autoRefresh.never')}</p>
      {menuItems && <><DropdownMenuSeparator />{menuItems}</>}
    </DropdownMenuContent></DropdownMenu>
  </div>;
}
