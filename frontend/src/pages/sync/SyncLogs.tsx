import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { RefreshCw } from 'lucide-react';
import { dagApi, type DagRun, type DagWorkflow } from '@/api/dag';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

// 运行记录 = DAG 运行实例（物化任务的唯一执行记录）。
// 分页在服务端（`/dag/runs` 返回 {items,total}，LIMIT/OFFSET 由服务端算），
// 不再读 adh_sync_logs（那条通道已收窄为物化，日志与节点对账结果统一落 adh_dag_*）。

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  queued: { label: tr('k_e5ac1d2029'), color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  running: { label: tr('k_5942497005'), color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  success: { label: tr('k_51991a5d11'), color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  partial: { label: tr('k_2f2b7e05e3'), color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  failed: { label: tr('k_3e3c8068bb'), color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  timeout: { label: tr('k_ff06c243d7'), color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  cancelled: { label: tr('k_a5ffdc95ee'), color: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

const TRIGGER_MAP: Record<string, string> = {
  cron: tr('k_16c7773fd8'),
  manual: tr('k_0cc990ba99'),
  retry: tr('k_e2d53a6d3a'),
};

function elapsedMs(run: DagRun): number | null {
  if (!run.started_at || !run.finished_at) return null;
  const start = new Date(run.started_at).getTime();
  const end = new Date(run.finished_at).getTime();
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? end - start : null;
}

function fmtTime(value?: string | null): string {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '—';
}

export default function SyncLogs() {
  const { t } = useTranslation('ui');
  const navigate = useNavigate();
  const [runs, setRuns] = useState<DagRun[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [taskIdFilter, setTaskIdFilter] = useState('');
  const [taskOptions, setTaskOptions] = useState<DagWorkflow[]>([]);
  const [statusFilter, setStatusFilter] = useState('');

  useEffect(() => {
    // 任务筛选用选择框（不手填 ID）：候选 = 物化任务清单
    dagApi.listWorkflows({ page: 1, size: 100 })
      .then(res => setTaskOptions(res.data.items || []))
      .catch(() => setTaskOptions([]));
  }, []);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = { page, size: pageSize };
      if (taskIdFilter) params.workflow_id = Number(taskIdFilter);
      if (statusFilter) params.status = statusFilter;
      const { data } = await dagApi.listRuns(params);
      setRuns(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error(tr('k_1f53c773e3'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, taskIdFilter, statusFilter]);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-2xl font-bold">{t("k_b39a4b0e31")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t("k_a8797a4108")}</p>
        </div>
        <Button variant="outline" size="sm" onClick={loadRuns}>
          <RefreshCw className="w-4 h-4 mr-1" />
          {tr('k_38108eaa1d')}</Button>
      </div>

      {/* Filters：改筛选即回第 1 页（停在旧页码会拿到错位空清单） */}
      <div className="flex items-center gap-3 flex-wrap shrink-0">
        <Select value={taskIdFilter || 'all'}
          onValueChange={v => { setTaskIdFilter(v === 'all' ? '' : v); setPage(1); }}>
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder={t("k_e12f5f8e8c")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_e12f5f8e8c")}</SelectItem>
            {taskOptions.map(task => (
              <SelectItem key={task.id} value={String(task.id)}>{task.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={statusFilter || 'all'}
          onValueChange={v => { setStatusFilter(v === 'all' ? '' : v); setPage(1); }}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder={t("k_1a4c26d92d")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_1a4c26d92d")}</SelectItem>
            {Object.entries(STATUS_MAP).map(([value, conf]) => (
              <SelectItem key={value} value={value}>{conf.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* 运行记录表：仅此区滚动（页头/分页固定） */}
      <div className="flex-1 min-h-0 overflow-auto border rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">{t("k_0e46d8d6b7")}</th>
              <th className="text-left p-3 font-medium">{t("k_62e951a692")}</th>
              <th className="text-left p-3 font-medium">{t("k_f67f1852d8")}</th>
              <th className="text-right p-3 font-medium">{t("k_3634f29fb1")}</th>
              <th className="text-right p-3 font-medium">{t("k_44b75fa528")}</th>
              <th className="text-left p-3 font-medium">{t("k_a9704e1997")}</th>
              <th className="text-left p-3 font-medium">{t("k_e8868af6eb")}</th>
              <th className="text-left p-3 font-medium">{t("k_f782779e8b")}</th>
              <th className="text-right p-3 font-medium">{t("k_f3ea6d345e")}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-muted-foreground">
                  {tr('k_26b5bd4947')}</td>
              </tr>
            ) : runs.length === 0 ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-muted-foreground">
                  {tr('k_ae733366a8')}</td>
              </tr>
            ) : (
              runs.map(run => {
                const stats = run.stats_json || {};
                const ms = elapsedMs(run);
                return (
                  <tr
                    key={run.id}
                    className="border-t hover:bg-muted/30 cursor-pointer"
                    onClick={() => navigate(`/data/sync/dag-runs/${run.id}`)}
                  >
                    <td className="p-3 font-medium">{run.workflow_name || '—'}</td>
                    <td className="p-3">
                      <Badge variant="outline" className={STATUS_MAP[run.status]?.color}>
                        {STATUS_MAP[run.status]?.label || run.status}
                      </Badge>
                    </td>
                    <td className="p-3 text-xs">{TRIGGER_MAP[run.trigger_type] || run.trigger_type}</td>
                    <td className="p-3 text-right text-xs">{Number(stats.rows_read || 0).toLocaleString()}</td>
                    <td className="p-3 text-right text-xs">{Number(stats.rows_written || 0).toLocaleString()}</td>
                    <td className="p-3 text-xs">{ms != null ? `${(ms / 1000).toFixed(1)}s` : '-'}</td>
                    <td className="p-3 text-xs text-muted-foreground">{fmtTime(run.started_at)}</td>
                    <td className="p-3 text-xs text-muted-foreground">{fmtTime(run.finished_at)}</td>
                    <td className="p-3 text-right">
                      <Button variant="ghost" size="sm"
                        onClick={(e) => { e.stopPropagation(); navigate(`/data/sync/dag-runs/${run.id}`); }}>
                        {t("k_f7acefd2d4")}</Button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination —— 服务端分页：total 来自 COUNT，翻页带 page/page_size 重新请求 */}
      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        onChange={(p: number) => setPage(p)}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
        hideWhenSingle
        className="shrink-0 mt-2"
      />
    </div>
  );
}
