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
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { RefreshCw, Search } from 'lucide-react';
import { syncApi, type SyncLog, type SyncTask } from '@/api/sync';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  success: { label: tr('k_51991a5d11'), color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  failed: { label: tr('k_3e3c8068bb'), color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  running: { label: tr('k_5942497005'), color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  partial: { label: tr('k_2f2b7e05e3'), color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
};

const TRIGGER_MAP: Record<string, string> = {
  cron: tr('k_16c7773fd8'),
  manual: tr('k_0cc990ba99'),
  retry: tr('k_e2d53a6d3a'),
};

export default function SyncLogs() {
  const { t } = useTranslation('ui');
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [taskIdFilter, setTaskIdFilter] = useState('');
  // 资源筛选用选择框(不手填 ID): 任务清单供下拉选择
  const [taskOptions, setTaskOptions] = useState<SyncTask[]>([]);
  useEffect(() => {
    syncApi.list({ page: 1, size: 100 })
      .then(res => setTaskOptions(res.data.items || res.data || []))
      .catch(() => setTaskOptions([]));
  }, []);
  const [statusFilter, setStatusFilter] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [expandedLog, setExpandedLog] = useState<SyncLog | null>(null);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = { page, size: pageSize };
      if (taskIdFilter) params.task_id = Number(taskIdFilter);
      if (statusFilter) params.status = statusFilter;
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      const res = await syncApi.listLogs(params);
      setLogs(res.data.items || res.data);
      setTotal(res.data.total || 0);
    } catch {
      toast.error(tr('k_1f53c773e3'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, taskIdFilter, statusFilter, startDate, endDate]);

  useEffect(() => { loadLogs(); }, [loadLogs]);

  const handleSearch = () => {
    setPage(1);
    loadLogs();
  };

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-2xl font-bold">{t("k_75772ede80")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t("k_1791ce6567")}</p>
        </div>
        <Button variant="outline" size="sm" onClick={loadLogs}>
          <RefreshCw className="w-4 h-4 mr-1" />
          {tr('k_38108eaa1d')}</Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap shrink-0">
        <Select value={taskIdFilter || 'all'} onValueChange={v => setTaskIdFilter(v === 'all' ? '' : v)}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder={t("k_e12f5f8e8c")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_e12f5f8e8c")}</SelectItem>
            {taskOptions.map(t => (
              <SelectItem key={t.id} value={String(t.id)}>{t.name || tr('k_45da45dbfc', { p0: t.id })}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={statusFilter || 'all'} onValueChange={v => setStatusFilter(v === 'all' ? '' : v)}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder={t("k_1a4c26d92d")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_1a4c26d92d")}</SelectItem>
            <SelectItem value="success">{t("k_51991a5d11")}</SelectItem>
            <SelectItem value="failed">{t("k_3e3c8068bb")}</SelectItem>
            <SelectItem value="running">{t("k_5942497005")}</SelectItem>
            <SelectItem value="partial">{t("k_2f2b7e05e3")}</SelectItem>
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
          {tr('k_711363c424')}</Button>
      </div>

      {/* Logs Table：仅此区滚动（页头/分页固定，参考表&字段） */}
      <div className="flex-1 min-h-0 overflow-auto border rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">{t("k_2e30469899")}</th>
              <th className="text-left p-3 font-medium">{t("k_62e951a692")}</th>
              <th className="text-left p-3 font-medium">{t("k_f67f1852d8")}</th>
              <th className="text-right p-3 font-medium">{t("k_3634f29fb1")}</th>
              <th className="text-right p-3 font-medium">{t("k_44b75fa528")}</th>
              <th className="text-right p-3 font-medium">{t("k_9085659645")}</th>
              <th className="text-left p-3 font-medium">{t("k_a9704e1997")}</th>
              <th className="text-left p-3 font-medium">{t("k_e8868af6eb")}</th>
              <th className="text-right p-3 font-medium">{t("k_f3ea6d345e")}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-muted-foreground">
                  {tr('k_26b5bd4947')}</td>
              </tr>
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-muted-foreground">
                  {tr('k_ae733366a8')}</td>
              </tr>
            ) : (
              logs.map(log => (
                <tr
                  key={log.id}
                  className="border-t hover:bg-muted/30 cursor-pointer"
                  onClick={() => setExpandedLog(log)}
                >
                  <td className="p-3 font-medium">{log.task_name || tr('k_e30b5c21bf', { p0: log.sync_task_id })}</td>
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
                      {tr('k_4f55ee1e68')}</Button>
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
      <Dialog open={!!expandedLog} onOpenChange={() => setExpandedLog(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t("k_8d0204b083")}</DialogTitle>
          </DialogHeader>
          {expandedLog && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <span className="text-muted-foreground">{t("k_0e6b3399a6")}</span>
                  {expandedLog.task_name || tr('k_e30b5c21bf', { p0: expandedLog.sync_task_id })}
                </div>
                <div>
                  <span className="text-muted-foreground">{t("k_bf0ac89783")}</span>
                  <Badge variant="outline" className={STATUS_MAP[expandedLog.status]?.color}>
                    {STATUS_MAP[expandedLog.status]?.label || expandedLog.status}
                  </Badge>
                </div>
                <div>
                  <span className="text-muted-foreground">{t("k_b576c74e33")}</span>
                  {TRIGGER_MAP[expandedLog.trigger_type] || expandedLog.trigger_type}
                </div>
                <div>
                  <span className="text-muted-foreground">{t("k_c8b22316eb")}</span>
                  {expandedLog.elapsed_ms != null ? `${(expandedLog.elapsed_ms / 1000).toFixed(1)}s` : '-'}
                </div>
                <div>
                  <span className="text-muted-foreground">{t("k_68bf8fa2d4")}</span>
                  {new Date(expandedLog.started_at).toLocaleString()}
                </div>
                <div>
                  <span className="text-muted-foreground">{t("k_c57df104eb")}</span>
                  {expandedLog.finished_at ? new Date(expandedLog.finished_at).toLocaleString() : '-'}
                </div>
                <div>
                  <span className="text-muted-foreground">{t("k_03264a4670")}</span>
                  {expandedLog.rows_read?.toLocaleString() ?? '-'}
                </div>
                <div>
                  <span className="text-muted-foreground">{t("k_d144ecea54")}</span>
                  {expandedLog.rows_written?.toLocaleString() ?? '-'}
                </div>
                <div>
                  <span className="text-muted-foreground">{t("k_a25ddc0d72")}</span>
                  <span className={expandedLog.rows_failed > 0 ? 'text-red-500' : ''}>
                    {expandedLog.rows_failed?.toLocaleString() ?? '-'}
                  </span>
                </div>
              </div>
              {expandedLog.error_message && (
                <div>
                  <span className="text-muted-foreground text-red-500">{t("k_2c9c72a7f9")}</span>
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
