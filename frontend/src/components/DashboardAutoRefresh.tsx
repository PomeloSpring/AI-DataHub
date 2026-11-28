import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, Clock, MoreHorizontal, Save } from 'lucide-react';
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger, DropdownMenuLabel, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { toast } from '@/lib/toast';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import client from '@/api/client';

export interface DashboardRefreshConfig {
  dashboard_id: number;
  schedule_type: 'disabled' | 'interval' | 'cron';
  interval_seconds: number | null;
  cron_expression: string | null;
  timezone: string;
  cache_ttl_seconds: number;
  is_active: boolean;
  version: number;
  last_run_at: string | null;
  last_status: string | null;
  run_count: number;
}

interface DashboardRefreshRun {
  run_id: number;
  status: 'queued' | 'running' | 'success' | 'partial' | 'failed' | 'timeout' | 'cancelled' | 'cache_hit';
  cache_hit?: boolean;
  result_summary?: string | null;
  error_message?: string | null;
}

interface DashboardAutoRefreshProps {
  dashboardId: number;
  refreshManageable: boolean;
  config?: DashboardRefreshConfig;
  cacheStale?: boolean;
  loading?: boolean;
  onCompleted?: () => void | Promise<void>;
  menuItems?: ReactNode;
}

const REFRESH_INTERVALS = [
  { value: 30, labelKey: 'interval30s' },
  { value: 60, labelKey: 'interval1m' },
  { value: 300, labelKey: 'interval5m' },
  { value: 600, labelKey: 'interval10m' },
  { value: 1800, labelKey: 'interval30m' },
  { value: 3600, labelKey: 'interval1h' },
  { value: 86400, labelKey: 'interval1d' },
  { value: 604800, labelKey: 'interval7d' },
  { value: 2592000, labelKey: 'interval30d' },
] as const;

const TERMINAL = new Set(['success', 'partial', 'failed', 'timeout', 'cancelled', 'cache_hit']);

export default function DashboardAutoRefresh({
  dashboardId, refreshManageable, config, cacheStale, loading, onCompleted, menuItems,
}: DashboardAutoRefreshProps) {
  const { t } = useTranslation(['dashboard']);
  const [scheduleType, setScheduleType] = useState<'disabled' | 'interval' | 'cron'>(config?.schedule_type || 'disabled');
  const [intervalSeconds, setIntervalSeconds] = useState<number>(config?.interval_seconds || 60);
  const [cronExpression, setCronExpression] = useState(config?.cron_expression || '*/5 * * * *');
  const [cacheTtl, setCacheTtl] = useState<number>(config?.cache_ttl_seconds || 60);
  const [enabled, setEnabled] = useState<boolean>(!!config?.is_active);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const mounted = useRef(true);
  const pollToken = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; pollToken.current += 1; };
  }, []);

  useEffect(() => {
    setScheduleType(config?.schedule_type || 'disabled');
    setIntervalSeconds(config?.interval_seconds || 60);
    setCronExpression(config?.cron_expression || '*/5 * * * *');
    setCacheTtl(config?.cache_ttl_seconds || 60);
    setEnabled(!!config?.is_active);
  }, [config]);

  const pollRun = useCallback(async (runId: number) => {
    const token = ++pollToken.current;
    while (mounted.current && token === pollToken.current) {
      const { data } = await client.get<DashboardRefreshRun>(`/dashboard/${dashboardId}/refresh-runs/${runId}`);
      if (!mounted.current || token !== pollToken.current) return;
      if (TERMINAL.has(data.status)) {
        const ok = data.status === 'success' || data.status === 'partial';
        setFailed(!ok);
        if (data.status === 'cache_hit') toast.success(t('autoRefresh.cacheHit'));
        else if (data.status === 'success') toast.success(t('autoRefresh.refreshSuccess'));
        else if (data.status === 'partial') toast.error(data.result_summary || t('autoRefresh.partialFailed'));
        else toast.error(data.result_summary || data.error_message || t('autoRefresh.failedToast'));
        if (ok && onCompleted) await onCompleted();
        return;
      }
      await new Promise(resolve => window.setTimeout(resolve, 1000));
    }
  }, [dashboardId, onCompleted, t]);

  const refresh = useCallback(async () => {
    if (busy || loading) return;
    setBusy(true); setFailed(false);
    try {
      const { data } = await client.post<DashboardRefreshRun>(`/dashboard/${dashboardId}/refresh`);
      if (data.cache_hit || data.status === 'cache_hit') {
        toast.success(t('autoRefresh.cacheHit'));
        return;
      }
      await pollRun(data.run_id);
    } catch (error: any) {
      if (mounted.current) {
        setFailed(true);
        toast.error(error?.response?.data?.detail || t('autoRefresh.failedToast'));
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [busy, dashboardId, loading, pollRun, t]);

  const save = async () => {
    setSaving(true);
    try {
      const payload = scheduleType === 'interval'
        ? { schedule_type: scheduleType, interval_seconds: intervalSeconds, cache_ttl_seconds: cacheTtl, timezone: 'Asia/Shanghai' }
        : scheduleType === 'cron'
          ? { schedule_type: scheduleType, cron_expression: cronExpression, cache_ttl_seconds: cacheTtl, timezone: 'Asia/Shanghai' }
          : { schedule_type: 'disabled', cache_ttl_seconds: cacheTtl, timezone: 'Asia/Shanghai' };
      const { data } = await client.put<DashboardRefreshConfig>(`/dashboard/${dashboardId}/refresh-config`, payload);
      setEnabled(data.is_active);
      setScheduleType(data.schedule_type);
      setIntervalSeconds(data.interval_seconds || 60);
      setCronExpression(data.cron_expression || '*/5 * * * *');
      setCacheTtl(data.cache_ttl_seconds);
      toast.success(t('autoRefresh.saved'));
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || t('autoRefresh.saveFailed'));
    } finally {
      if (mounted.current) setSaving(false);
    }
  };

  const toggle = async () => {
    setSaving(true);
    try {
      const { data } = await client.patch<DashboardRefreshConfig>(`/dashboard/${dashboardId}/refresh-config`, { is_active: !enabled });
      setEnabled(data.is_active);
      toast.success(data.is_active ? t('autoRefresh.resumed') : t('autoRefresh.paused'));
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || t('autoRefresh.saveFailed'));
    } finally {
      if (mounted.current) setSaving(false);
    }
  };

  return <div className="flex shrink-0 items-center gap-1" data-testid="dashboard-refresh-actions">
    {refreshManageable && <Tooltip><TooltipTrigger asChild>
      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={failed ? t('autoRefresh.retryAria') : t('autoRefresh.refreshAria')} disabled={busy || loading} onClick={() => void refresh()}>
        <RefreshCw className={`h-4 w-4 ${busy || loading ? 'animate-spin' : ''} ${failed ? 'text-destructive' : ''}`} />
      </Button>
    </TooltipTrigger><TooltipContent>{failed ? t('autoRefresh.retryAria') : t('autoRefresh.refreshAria')}</TooltipContent></Tooltip>}
    <DropdownMenu><DropdownMenuTrigger asChild>
      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={t('autoRefresh.moreAria')} title={t('autoRefresh.moreAria')}>
        <Clock className={`hidden h-4 w-4 sm:block ${refreshManageable && enabled ? 'text-primary' : ''}`} /><MoreHorizontal className="h-4 w-4 sm:hidden" />
      </Button>
    </DropdownMenuTrigger><DropdownMenuContent align="end" className={refreshManageable ? 'w-72' : 'w-56'}>
      {refreshManageable && <>
      <DropdownMenuLabel>{t('autoRefresh.label')}</DropdownMenuLabel>
      <div className="space-y-3 px-2 py-2">
        <label className="block text-xs">
          {t('autoRefresh.intervalAria')}
          <select aria-label={t('autoRefresh.intervalAria')} className="mt-1 w-full rounded border bg-background p-2 text-sm"
            value={scheduleType} onChange={e => setScheduleType(e.target.value as typeof scheduleType)}>
            <option value="disabled">{t('autoRefresh.offOption')}</option>
            <option value="interval">{t('autoRefresh.fixedInterval')}</option>
            <option value="cron">{t('autoRefresh.customCron')}</option>
          </select>
        </label>
        {scheduleType === 'interval' && <label className="block text-xs">
          {t('autoRefresh.fixedInterval')}
          <select aria-label={t('autoRefresh.fixedIntervalAria')} className="mt-1 w-full rounded border bg-background p-2 text-sm"
            value={intervalSeconds} onChange={e => setIntervalSeconds(Number(e.target.value))}>
            {REFRESH_INTERVALS.map(item => <option key={item.value} value={item.value}>{t('autoRefresh.every', { interval: t(`autoRefresh.${item.labelKey}` as 'autoRefresh.intervalOff') })}</option>)}
          </select>
        </label>}
        {scheduleType === 'cron' && <label className="block text-xs">
          Cron
          <input className="mt-1 w-full rounded border bg-background p-2 font-mono text-sm" value={cronExpression}
            onChange={e => setCronExpression(e.target.value)} placeholder={t('autoRefresh.cronPlaceholder')} />
        </label>}
        <label className="block text-xs">
          {t('autoRefresh.cacheTtl')}
          <input type="number" min={30} max={2592000} className="mt-1 w-full rounded border bg-background p-2 text-sm"
            value={cacheTtl} onChange={e => setCacheTtl(Number(e.target.value))} />
        </label>
        <div className="flex items-center gap-2">
          <Button size="sm" className="flex-1" disabled={saving} onClick={() => void save()}><Save className="mr-2 h-4 w-4" />{t('autoRefresh.save')}</Button>
          {scheduleType !== 'disabled' && <Button size="sm" variant="outline" disabled={saving} onClick={() => void toggle()}>{enabled ? t('autoRefresh.pause') : t('autoRefresh.resume')}</Button>}
        </div>
      </div>
      {cacheStale && <p className="px-2 py-2 text-xs text-amber-600 dark:text-amber-400">{t('autoRefresh.stale')}</p>}
      <p className="px-2 py-2 text-xs text-muted-foreground">
        {config?.last_run_at ? t('autoRefresh.lastSuccess', { time: new Date(config.last_run_at).toLocaleString() }) : t('autoRefresh.never')}
      </p>
      </>}
      {menuItems && <><DropdownMenuSeparator />{menuItems}</>}
    </DropdownMenuContent></DropdownMenu>
  </div>;
}
