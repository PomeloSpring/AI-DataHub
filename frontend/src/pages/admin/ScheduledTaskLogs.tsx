import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { ArrowLeft, RefreshCw, Trash2, XCircle, Clock } from 'lucide-react';
import {
  listScheduledLogs,
  getScheduledTaskStats,
  cleanupScheduledLogs,
  updateLogStatus,
  cleanupStaleLogs,
  type ScheduledLog,
} from '@/api/scheduledTask';
import i18n from '@/i18n';

// 状态色板与任务清单同源；文案渲染期 t() 解析（与 ScheduledTasks 同一份 key）
const STATUS_MAP: Record<string, { labelKey: string; color: string }> = {
  success: { labelKey: 'success', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  partial: { labelKey: 'partial', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  queued: { labelKey: 'queued', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  failed: { labelKey: 'failed', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  running: { labelKey: 'running', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  timeout: { labelKey: 'timeout', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  cancelled: { labelKey: 'cancelled', color: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

const TRIGGER_KEYS: Record<string, string> = {
  cron: 'cron',
  manual: 'manual',
  retry: 'retry',
};

const statusLabel = (s: string): string =>
  STATUS_MAP[s] ? i18n.t(`workspace:schedule.status.${STATUS_MAP[s].labelKey}` as 'workspace:schedule.status.success') : s;
const triggerLabel = (s: string): string =>
  TRIGGER_KEYS[s] ? i18n.t(`workspace:schedule.logs.trigger.${TRIGGER_KEYS[s]}` as 'workspace:schedule.logs.trigger.cron') : s;

interface Props {
  taskId: number;
  onBack: () => void;
}

export default function ScheduledTaskLogs({ taskId, onBack }: Props) {
  const { t } = useTranslation(['workspace', 'common']);
  const [logs, setLogs] = useState<ScheduledLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<any>(null);
  const [detailLog, setDetailLog] = useState<ScheduledLog | null>(null);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listScheduledLogs(taskId, {
        page,
        size: pageSize,
        status: statusFilter || undefined,
      });
      setLogs(res.items);
      setTotal(res.total);
    } catch {
      toast.error(i18n.t('workspace:schedule.logs.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [taskId, page, pageSize, statusFilter]);

  const loadStats = useCallback(async () => {
    try {
      const s = await getScheduledTaskStats(taskId);
      setStats(s);
    } catch {}
  }, [taskId]);

  useEffect(() => { loadLogs(); loadStats(); }, [loadLogs, loadStats]);

  const handleCleanup = async () => {
    try {
      const res = await cleanupScheduledLogs(30);
      toast.success(t('schedule.logs.cleanedLogs', { count: res.deleted }));
      loadLogs();
    } catch {
      toast.error(t('schedule.logs.cleanupFailed'));
    }
  };

  return (
    <div className="flex flex-col h-full gap-4 p-6">
      <div className="flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft className="w-4 h-4 mr-1" />
            {t('common:action.back')}
          </Button>
          <div>
            <h1 className="text-2xl font-bold">{t('schedule.logsTitle')}</h1>
            <p className="text-muted-foreground text-sm">{t('schedule.logs.taskRef', { id: taskId })}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadLogs}>
            <RefreshCw className="w-4 h-4 mr-1" />
            {t('common:action.refresh')}
          </Button>
          <Button variant="outline" size="sm" onClick={handleCleanup}>
            <Trash2 className="w-4 h-4 mr-1" />
            {t('schedule.logs.cleanupOld')}
          </Button>
          <Button variant="outline" size="sm" onClick={async () => {
            try {
              const res = await cleanupStaleLogs(10);
              toast.success(t('schedule.logs.cleanedStale', { count: res.cleaned }));
              loadLogs();
              loadStats();
            } catch { toast.error(t('schedule.logs.cleanupFailed')); }
          }}>
            <Clock className="w-4 h-4 mr-1" />
            {t('schedule.logs.cleanupStale')}
          </Button>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-5 gap-3">
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold">{stats.total_runs}</div>
            <div className="text-xs text-muted-foreground">{t('schedule.logs.stat.total')}</div>
          </div>
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-green-500">{stats.success_runs}</div>
            <div className="text-xs text-muted-foreground">{t('schedule.status.success')}</div>
          </div>
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-red-500">{stats.failed_runs}</div>
            <div className="text-xs text-muted-foreground">{t('schedule.status.failed')}</div>
          </div>
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold">{stats.success_rate}%</div>
            <div className="text-xs text-muted-foreground">{t('schedule.logs.stat.rate')}</div>
          </div>
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold">{(stats.avg_elapsed_ms / 1000).toFixed(1)}s</div>
            <div className="text-xs text-muted-foreground">{t('schedule.logs.stat.avg')}</div>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex items-center gap-2">
        <Select value={statusFilter || 'all'} onValueChange={v => { setStatusFilter(v === 'all' ? '' : v); setPage(1); }}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder={t('schedule.logs.allStatus')} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('schedule.logs.allStatus')}</SelectItem>
            <SelectItem value="success">{t('schedule.status.success')}</SelectItem>
            <SelectItem value="partial">{t('schedule.status.partial')}</SelectItem>
            <SelectItem value="queued">{t('schedule.status.queued')}</SelectItem>
            <SelectItem value="cancelled">{t('schedule.status.cancelled')}</SelectItem>
            <SelectItem value="failed">{t('schedule.status.failed')}</SelectItem>
            <SelectItem value="running">{t('schedule.status.running')}</SelectItem>
            <SelectItem value="timeout">{t('schedule.status.timeout')}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Logs Table：仅此区滚动（页头/分页固定，参考表&字段） */}
      <div className="flex-1 min-h-0 overflow-auto border rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">{t('schedule.logs.col.startedAt')}</th>
              <th className="text-left p-3 font-medium">{t('common:label.status')}</th>
              <th className="text-left p-3 font-medium">{t('schedule.col.trigger')}</th>
              <th className="text-left p-3 font-medium">{t('schedule.logs.col.elapsed')}</th>
              <th className="text-left p-3 font-medium">{t('schedule.logs.col.summary')}</th>
              <th className="text-left p-3 font-medium">Worker</th>
              <th className="text-right p-3 font-medium">{t('common:label.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">{t('common:state.loading')}</td></tr>
            ) : logs.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">{t('schedule.logs.empty')}</td></tr>
            ) : (
              logs.map(log => (
                <tr key={log.id} className="border-t hover:bg-muted/30">
                  <td className="p-3 text-xs">{new Date(log.started_at).toLocaleString(i18n.language)}</td>
                  <td className="p-3">
                    <Badge variant="outline" className={STATUS_MAP[log.status]?.color}>
                      {statusLabel(log.status)}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs">{triggerLabel(log.trigger_type)}</td>
                  <td className="p-3 text-xs">
                    {log.elapsed_ms != null ? `${(log.elapsed_ms / 1000).toFixed(1)}s` : '-'}
                  </td>
                  <td className="p-3 text-xs truncate max-w-[300px]">
                    {log.result_summary || log.error_message || '-'}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">{log.worker_id || '-'}</td>
                  <td className="p-3 text-right">
                    <div className="flex justify-end gap-1">
                      {log.report_id && (
                        <Button variant="ghost" size="sm" asChild>
                          <a href={`/report/${log.report_id}`} target="_blank" rel="noopener noreferrer">
                            {t('schedule.logs.report')}
                          </a>
                        </Button>
                      )}
                      <Button variant="ghost" size="sm" onClick={() => setDetailLog(log)}>
                        {t('schedule.logs.detail')}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination —— 统一分页条：固定底栏 */}
      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        onChange={(p: number) => setPage(p)}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
        hideWhenSingle
        className="shrink-0 mt-2"
      />

      {/* Detail Dialog */}
      <Dialog open={!!detailLog} onOpenChange={() => setDetailLog(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t('schedule.logs.detailTitle')}</DialogTitle>
          </DialogHeader>
          {detailLog && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <div><span className="text-muted-foreground">{t('schedule.logs.d.status')}</span>{detailLog.status}</div>
                <div><span className="text-muted-foreground">{t('schedule.logs.d.trigger')}</span>{triggerLabel(detailLog.trigger_type)}</div>
                <div><span className="text-muted-foreground">{t('schedule.logs.d.startedAt')}</span>{new Date(detailLog.started_at).toLocaleString(i18n.language)}</div>
                <div><span className="text-muted-foreground">{t('schedule.logs.d.finishedAt')}</span>{detailLog.finished_at ? new Date(detailLog.finished_at).toLocaleString(i18n.language) : '-'}</div>
                <div><span className="text-muted-foreground">{t('schedule.logs.d.elapsed')}</span>{detailLog.elapsed_ms != null ? `${(detailLog.elapsed_ms / 1000).toFixed(1)}s` : '-'}</div>
                <div><span className="text-muted-foreground">Worker: </span>{detailLog.worker_id || '-'}</div>
                <div><span className="text-muted-foreground">Celery Task ID: </span><code className="text-xs">{detailLog.celery_task_id || '-'}</code></div>
                <div><span className="text-muted-foreground">{t('schedule.logs.d.notify')}</span>{detailLog.notify_status || '-'}</div>
                {detailLog.report_id && (
                  <div className="col-span-2">
                    <span className="text-muted-foreground">{t('schedule.logs.d.report')}</span>
                    <a
                      href={`/report/${detailLog.report_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary underline ml-1"
                    >
                      {t('schedule.logs.viewReport')}
                    </a>
                  </div>
                )}
              </div>

              {/* Status Actions */}
              {['queued', 'running'].includes(detailLog.status) && (
                <div className="flex gap-2 pt-1">
                  <Button size="sm" variant="destructive" onClick={async () => {
                    try {
                      // 「手动中止」是写入服务端审计的原因字段（后端契约值），不随界面语言变化
                      await updateLogStatus(detailLog.id, 'cancelled', '\u624b\u52a8\u4e2d\u6b62');
                      toast.success(t('schedule.logs.aborted'));
                      setDetailLog(null);
                      loadLogs();
                      loadStats();
                    } catch { toast.error(t('common:state.operationFailed')); }
                  }}>
                    <XCircle className="w-4 h-4 mr-1" /> {t('schedule.logs.abort')}
                  </Button>
                </div>
              )}
              {detailLog.stage_error_code && (
                <div className="text-amber-600">{t('schedule.logs.stageError', { code: detailLog.stage_error_code })}</div>
              )}
              {detailLog.run_key && (
                <div className="text-muted-foreground">{t('schedule.logs.runKey', { key: detailLog.run_key })}</div>
              )}
              {detailLog.result_summary && (
                <div>
                  <span className="text-muted-foreground">{t('schedule.logs.d.summary')}</span>
                  <pre className="mt-1 p-2 bg-muted rounded text-xs whitespace-pre-wrap">{detailLog.result_summary}</pre>
                </div>
              )}
              {detailLog.error_message && (
                <div>
                  <span className="text-muted-foreground text-red-500">{t('schedule.logs.d.error')}</span>
                  <pre className="mt-1 p-2 bg-red-500/10 rounded text-xs whitespace-pre-wrap text-red-500">{detailLog.error_message}</pre>
                </div>
              )}
              {detailLog.report_content && (
                <div>
                  <span className="text-muted-foreground">{t('schedule.logs.d.reportContent')}</span>
                  <pre className="mt-1 p-2 bg-muted rounded text-xs whitespace-pre-wrap">{detailLog.report_content}</pre>
                </div>
              )}
              {detailLog.result_data && (
                <div>
                  <span className="text-muted-foreground">{t('schedule.logs.d.fullResult')}</span>
                  <pre className="mt-1 p-2 bg-muted rounded text-xs whitespace-pre-wrap max-h-[300px] overflow-y-auto">
                    {typeof detailLog.result_data === 'string' ? detailLog.result_data : JSON.stringify(detailLog.result_data, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
