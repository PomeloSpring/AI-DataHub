import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import { RefreshCw, Search } from 'lucide-react';
import { syncApi, type SyncLog } from '@/api/sync';

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  success: { label: '成功', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  failed: { label: '失败', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  running: { label: '运行中', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  partial: { label: '部分成功', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
};

const TRIGGER_MAP: Record<string, string> = {
  cron: '定时触发',
  manual: '手动触发',
  retry: '重试',
};

export default function SyncLogs() {
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [taskIdFilter, setTaskIdFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [expandedLog, setExpandedLog] = useState<SyncLog | null>(null);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = { page, size: 20 };
      if (taskIdFilter) params.task_id = Number(taskIdFilter);
      if (statusFilter) params.status = statusFilter;
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      const res = await syncApi.listLogs(params);
      setLogs(res.data.items || res.data);
      setTotal(res.data.total || 0);
    } catch {
      toast.error('加载同步日志失败');
    } finally {
      setLoading(false);
    }
  }, [page, taskIdFilter, statusFilter, startDate, endDate]);

  useEffect(() => { loadLogs(); }, [loadLogs]);

  const handleSearch = () => {
    setPage(1);
    loadLogs();
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">同步日志</h1>
          <p className="text-muted-foreground text-sm mt-1">查看数据同步执行记录</p>
        </div>
        <Button variant="outline" size="sm" onClick={loadLogs}>
          <RefreshCw className="w-4 h-4 mr-1" />
          刷新
        </Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <Input
          className="w-[180px]"
          placeholder="任务 ID"
          value={taskIdFilter}
          onChange={e => setTaskIdFilter(e.target.value)}
        />
        <Select value={statusFilter || 'all'} onValueChange={v => setStatusFilter(v === 'all' ? '' : v)}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="全部状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="success">成功</SelectItem>
            <SelectItem value="failed">失败</SelectItem>
            <SelectItem value="running">运行中</SelectItem>
            <SelectItem value="partial">部分成功</SelectItem>
          </SelectContent>
        </Select>
        <Input
          className="w-[170px]"
          type="date"
          value={startDate}
          onChange={e => setStartDate(e.target.value)}
        />
        <Input
          className="w-[170px]"
          type="date"
          value={endDate}
          onChange={e => setEndDate(e.target.value)}
        />
        <Button size="sm" onClick={handleSearch}>
          <Search className="w-4 h-4 mr-1" />
          查询
        </Button>
      </div>

      {/* Logs Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">任务名称</th>
              <th className="text-left p-3 font-medium">状态</th>
              <th className="text-left p-3 font-medium">触发方式</th>
              <th className="text-right p-3 font-medium">读取行数</th>
              <th className="text-right p-3 font-medium">写入行数</th>
              <th className="text-right p-3 font-medium">失败行数</th>
              <th className="text-left p-3 font-medium">耗时</th>
              <th className="text-left p-3 font-medium">开始时间</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-muted-foreground">
                  加载中...
                </td>
              </tr>
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-muted-foreground">
                  暂无同步日志
                </td>
              </tr>
            ) : (
              logs.map(log => (
                <tr
                  key={log.id}
                  className="border-t hover:bg-muted/30 cursor-pointer"
                  onClick={() => setExpandedLog(log)}
                >
                  <td className="p-3 font-medium">{log.task_name || `任务 #${log.sync_task_id}`}</td>
                  <td className="p-3">
                    <Badge variant="outline" className={STATUS_MAP[log.status]?.color}>
                      {STATUS_MAP[log.status]?.label || log.status}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs">{TRIGGER_MAP[log.trigger_type] || log.trigger_type}</td>
                  <td className="p-3 text-right text-xs">{log.rows_read?.toLocaleString() ?? '-'}</td>
                  <td className="p-3 text-right text-xs">{log.rows_written?.toLocaleString() ?? '-'}</td>
                  <td className="p-3 text-right text-xs">
                    {log.rows_failed > 0 ? (
                      <span className="text-red-500">{log.rows_failed.toLocaleString()}</span>
                    ) : (
                      <span>{log.rows_failed?.toLocaleString() ?? '-'}</span>
                    )}
                  </td>
                  <td className="p-3 text-xs">
                    {log.elapsed_ms != null ? `${(log.elapsed_ms / 1000).toFixed(1)}s` : '-'}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {new Date(log.started_at).toLocaleString()}
                  </td>
                  <td className="p-3 text-right">
                    <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setExpandedLog(log); }}>
                      详情
                    </Button>
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
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage(p => p - 1)}
          >
            上一页
          </Button>
          <span className="text-sm text-muted-foreground leading-9">
            第 {page} 页 / 共 {Math.ceil(total / 20)} 页
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page * 20 >= total}
            onClick={() => setPage(p => p + 1)}
          >
            下一页
          </Button>
        </div>
      )}

      {/* Detail Dialog */}
      <Dialog open={!!expandedLog} onOpenChange={() => setExpandedLog(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>执行详情</DialogTitle>
          </DialogHeader>
          {expandedLog && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <span className="text-muted-foreground">任务名称：</span>
                  {expandedLog.task_name || `任务 #${expandedLog.sync_task_id}`}
                </div>
                <div>
                  <span className="text-muted-foreground">状态：</span>
                  <Badge variant="outline" className={STATUS_MAP[expandedLog.status]?.color}>
                    {STATUS_MAP[expandedLog.status]?.label || expandedLog.status}
                  </Badge>
                </div>
                <div>
                  <span className="text-muted-foreground">触发方式：</span>
                  {TRIGGER_MAP[expandedLog.trigger_type] || expandedLog.trigger_type}
                </div>
                <div>
                  <span className="text-muted-foreground">耗时：</span>
                  {expandedLog.elapsed_ms != null ? `${(expandedLog.elapsed_ms / 1000).toFixed(1)}s` : '-'}
                </div>
                <div>
                  <span className="text-muted-foreground">开始时间：</span>
                  {new Date(expandedLog.started_at).toLocaleString()}
                </div>
                <div>
                  <span className="text-muted-foreground">结束时间：</span>
                  {expandedLog.finished_at ? new Date(expandedLog.finished_at).toLocaleString() : '-'}
                </div>
                <div>
                  <span className="text-muted-foreground">读取行数：</span>
                  {expandedLog.rows_read?.toLocaleString() ?? '-'}
                </div>
                <div>
                  <span className="text-muted-foreground">写入行数：</span>
                  {expandedLog.rows_written?.toLocaleString() ?? '-'}
                </div>
                <div>
                  <span className="text-muted-foreground">失败行数：</span>
                  <span className={expandedLog.rows_failed > 0 ? 'text-red-500' : ''}>
                    {expandedLog.rows_failed?.toLocaleString() ?? '-'}
                  </span>
                </div>
              </div>
              {expandedLog.error_message && (
                <div>
                  <span className="text-muted-foreground text-red-500">错误信息：</span>
                  <pre className="mt-1 p-2 bg-red-500/10 rounded text-xs whitespace-pre-wrap text-red-500">
                    {expandedLog.error_message}
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
