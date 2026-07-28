import { useState, useEffect, useCallback } from 'react';
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
import { toast } from 'sonner';
import { ArrowLeft, RefreshCw, Trash2, XCircle, CheckCircle, Clock } from 'lucide-react';
import {
  listScheduledLogs,
  getScheduledTaskStats,
  cleanupScheduledLogs,
  updateLogStatus,
  cleanupStaleLogs,
  type ScheduledLog,
} from '@/api/scheduledTask';

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  success: { label: '成功', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  failed: { label: '失败', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  running: { label: '运行中', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  timeout: { label: '超时', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  cancelled: { label: '已取消', color: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

const TRIGGER_MAP: Record<string, string> = {
  cron: '定时触发',
  manual: '手动触发',
  retry: '重试',
  webhook: 'Webhook 触发',
};

interface Props {
  taskId: number;
  onBack: () => void;
}

export default function ScheduledTaskLogs({ taskId, onBack }: Props) {
  const [logs, setLogs] = useState<ScheduledLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<any>(null);
  const [detailLog, setDetailLog] = useState<ScheduledLog | null>(null);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listScheduledLogs(taskId, {
        page,
        size: 20,
        status: statusFilter || undefined,
      });
      setLogs(res.items);
      setTotal(res.total);
    } catch {
      toast.error('加载执行历史失败');
    } finally {
      setLoading(false);
    }
  }, [taskId, page, statusFilter]);

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
      toast.success(`已清理 ${res.deleted} 条过期日志`);
      loadLogs();
    } catch {
      toast.error('清理失败');
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft className="w-4 h-4 mr-1" />
            返回
          </Button>
          <div>
            <h1 className="text-2xl font-bold">执行历史</h1>
            <p className="text-muted-foreground text-sm">任务 #{taskId}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadLogs}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button variant="outline" size="sm" onClick={handleCleanup}>
            <Trash2 className="w-4 h-4 mr-1" />
            清理30天前
          </Button>
          <Button variant="outline" size="sm" onClick={async () => {
            try {
              const res = await cleanupStaleLogs(10);
              toast.success(`已清理 ${res.cleaned} 条超时任务`);
              loadLogs();
              loadStats();
            } catch { toast.error('清理失败'); }
          }}>
            <Clock className="w-4 h-4 mr-1" />
            清理超时
          </Button>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-5 gap-3">
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold">{stats.total_runs}</div>
            <div className="text-xs text-muted-foreground">总执行次数</div>
          </div>
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-green-500">{stats.success_runs}</div>
            <div className="text-xs text-muted-foreground">成功</div>
          </div>
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-red-500">{stats.failed_runs}</div>
            <div className="text-xs text-muted-foreground">失败</div>
          </div>
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold">{stats.success_rate}%</div>
            <div className="text-xs text-muted-foreground">成功率</div>
          </div>
          <div className="border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold">{(stats.avg_elapsed_ms / 1000).toFixed(1)}s</div>
            <div className="text-xs text-muted-foreground">平均耗时</div>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex items-center gap-2">
        <Select value={statusFilter || 'all'} onValueChange={v => { setStatusFilter(v === 'all' ? '' : v); setPage(1); }}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="全部状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="success">成功</SelectItem>
            <SelectItem value="failed">失败</SelectItem>
            <SelectItem value="running">运行中</SelectItem>
            <SelectItem value="timeout">超时</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Logs Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">开始时间</th>
              <th className="text-left p-3 font-medium">状态</th>
              <th className="text-left p-3 font-medium">触发方式</th>
              <th className="text-left p-3 font-medium">耗时</th>
              <th className="text-left p-3 font-medium">结果摘要</th>
              <th className="text-left p-3 font-medium">Worker</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">加载中...</td></tr>
            ) : logs.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">暂无执行记录</td></tr>
            ) : (
              logs.map(log => (
                <tr key={log.id} className="border-t hover:bg-muted/30">
                  <td className="p-3 text-xs">{new Date(log.started_at).toLocaleString()}</td>
                  <td className="p-3">
                    <Badge variant="outline" className={STATUS_MAP[log.status]?.color}>
                      {STATUS_MAP[log.status]?.label || log.status}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs">{TRIGGER_MAP[log.trigger_type] || log.trigger_type}</td>
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
                          <a href={`/report/${log.report_id}${log.report_access_token ? `?token=${log.report_access_token}` : ''}`} target="_blank" rel="noopener noreferrer">
                            报告
                          </a>
                        </Button>
                      )}
                      <Button variant="ghost" size="sm" onClick={() => setDetailLog(log)}>
                        详情
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > 20 && (
        <div className="flex justify-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</Button>
          <span className="text-sm text-muted-foreground leading-9">
            第 {page} 页 / 共 {Math.ceil(total / 20)} 页
          </span>
          <Button variant="outline" size="sm" disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)}>下一页</Button>
        </div>
      )}

      {/* Detail Dialog */}
      <Dialog open={!!detailLog} onOpenChange={() => setDetailLog(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>执行详情</DialogTitle>
          </DialogHeader>
          {detailLog && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <div><span className="text-muted-foreground">状态：</span>{detailLog.status}</div>
                <div><span className="text-muted-foreground">触发方式：</span>{TRIGGER_MAP[detailLog.trigger_type]}</div>
                <div><span className="text-muted-foreground">开始时间：</span>{new Date(detailLog.started_at).toLocaleString()}</div>
                <div><span className="text-muted-foreground">结束时间：</span>{detailLog.finished_at ? new Date(detailLog.finished_at).toLocaleString() : '-'}</div>
                <div><span className="text-muted-foreground">耗时：</span>{detailLog.elapsed_ms != null ? `${(detailLog.elapsed_ms / 1000).toFixed(1)}s` : '-'}</div>
                <div><span className="text-muted-foreground">Worker：</span>{detailLog.worker_id || '-'}</div>
                <div><span className="text-muted-foreground">Celery Task ID：</span><code className="text-xs">{detailLog.celery_task_id || '-'}</code></div>
                <div><span className="text-muted-foreground">通知状态：</span>{detailLog.notify_status || '-'}</div>
                {detailLog.report_id && (
                  <div className="col-span-2">
                    <span className="text-muted-foreground">报告：</span>
                    <a
                      href={`/report/${detailLog.report_id}${detailLog.report_access_token ? `?token=${detailLog.report_access_token}` : ''}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary underline ml-1"
                    >
                      查看完整报告 →
                    </a>
                  </div>
                )}
              </div>

              {/* Status Actions */}
              {detailLog.status === 'running' && (
                <div className="flex gap-2 pt-1">
                  <Button size="sm" variant="destructive" onClick={async () => {
                    try {
                      await updateLogStatus(detailLog.id, 'cancelled', '手动中止');
                      toast.success('已中止');
                      setDetailLog(null);
                      loadLogs();
                      loadStats();
                    } catch { toast.error('操作失败'); }
                  }}>
                    <XCircle className="w-4 h-4 mr-1" /> 中止任务
                  </Button>
                </div>
              )}
              {detailLog.status !== 'running' && (
                <div className="flex gap-2 pt-1">
                  {detailLog.status !== 'success' && (
                    <Button size="sm" variant="outline" onClick={async () => {
                      try {
                        await updateLogStatus(detailLog.id, 'success');
                        toast.success('已标记为成功');
                        setDetailLog(null);
                        loadLogs();
                        loadStats();
                      } catch { toast.error('操作失败'); }
                    }}>
                      <CheckCircle className="w-4 h-4 mr-1" /> 标记成功
                    </Button>
                  )}
                  {detailLog.status !== 'failed' && (
                    <Button size="sm" variant="outline" onClick={async () => {
                      try {
                        await updateLogStatus(detailLog.id, 'failed', '手动标记');
                        toast.success('已标记为失败');
                        setDetailLog(null);
                        loadLogs();
                        loadStats();
                      } catch { toast.error('操作失败'); }
                    }}>
                      <XCircle className="w-4 h-4 mr-1" /> 标记失败
                    </Button>
                  )}
                  {detailLog.status !== 'timeout' && (
                    <Button size="sm" variant="outline" onClick={async () => {
                      try {
                        await updateLogStatus(detailLog.id, 'timeout', '手动标记超时');
                        toast.success('已标记为超时');
                        setDetailLog(null);
                        loadLogs();
                        loadStats();
                      } catch { toast.error('操作失败'); }
                    }}>
                      <Clock className="w-4 h-4 mr-1" /> 标记超时
                    </Button>
                  )}
                </div>
              )}
              {detailLog.result_summary && (
                <div>
                  <span className="text-muted-foreground">结果摘要：</span>
                  <pre className="mt-1 p-2 bg-muted rounded text-xs whitespace-pre-wrap">{detailLog.result_summary}</pre>
                </div>
              )}
              {detailLog.error_message && (
                <div>
                  <span className="text-muted-foreground text-red-500">错误信息：</span>
                  <pre className="mt-1 p-2 bg-red-500/10 rounded text-xs whitespace-pre-wrap text-red-500">{detailLog.error_message}</pre>
                </div>
              )}
              {detailLog.report_content && (
                <div>
                  <span className="text-muted-foreground">报告内容：</span>
                  <pre className="mt-1 p-2 bg-muted rounded text-xs whitespace-pre-wrap">{detailLog.report_content}</pre>
                </div>
              )}
              {detailLog.result_data && (
                <div>
                  <span className="text-muted-foreground">完整结果：</span>
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
