import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import {
  CheckSquare, Loader2, RefreshCw, Square, StopCircle, Trash2, XCircle,
} from 'lucide-react';
import { dagApi, type DagRun, type DagRunBatchResult, type DagWorkflow } from '@/api/dag';
import { useHasPerm } from '@/stores/permissionStore';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

// 运行记录 = DAG 运行实例（物化任务的唯一执行记录）。
// 分页在服务端（`/dag/runs` 返回 {items,total}，LIMIT/OFFSET 由服务端算），
// 筛选条件全部下推 SQL（任务/状态/触发方式/开始日期区间/任务名关键字），
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

const ACTIVE_STATUS = new Set(['queued', 'running']);

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
  // 筛选条件（全部下推服务端）：任务 / 状态 / 触发方式 / 开始日期区间 / 任务名关键字
  const [kwInput, setKwInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const [taskIdFilter, setTaskIdFilter] = useState('');
  const [taskOptions, setTaskOptions] = useState<DagWorkflow[]>([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [triggerFilter, setTriggerFilter] = useState('');
  const [startFrom, setStartFrom] = useState('');
  const [startTo, setStartTo] = useState('');
  // 单条删除
  const [deleteTarget, setDeleteTarget] = useState<DagRun | null>(null);
  const [deleting, setDeleting] = useState(false);
  // 单条终止：残留实例（worker 已消失、执行体失联）必须能直接收口，不必先切批量模式
  const [stopTarget, setStopTarget] = useState<DagRun | null>(null);
  const [stopping, setStopping] = useState(false);
  // 批量操作：开关式批量模式（默认不挂勾选框，避免表格拥挤）+ 多选 + 逐条回报结果
  const [batchMode, setBatchMode] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [batchConfirm, setBatchConfirm] = useState<'delete' | 'stop' | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResult, setBatchResult] = useState<DagRunBatchResult | null>(null);
  // 删除/停止是写动作：只给持写码的角色渲染（不渲染点了必 403 的按钮）。
  // 后端按权限码并集放行（sync:manage / dag:manage 都覆盖 /api/dag/runs*），这里同口径。
  const canSyncManage = useHasPerm('sync:manage');
  const canDagManage = useHasPerm('dag:manage');
  const canDelete = canSyncManage || canDagManage;

  useEffect(() => {
    // 任务筛选用选择框（不手填 ID）：候选 = 物化任务清单
    dagApi.listWorkflows({ page: 1, size: 100 })
      .then(res => setTaskOptions(res.data.items || []))
      .catch(() => setTaskOptions([]));
  }, []);

  // 关键字防抖（停手再发请求）
  useEffect(() => {
    const id = setTimeout(() => setKeyword(kwInput.trim()), 300);
    return () => clearTimeout(id);
  }, [kwInput]);

  // 筛选/搜索/每页条数变化 → 回第 1 页并清空勾选（跨筛选的旧勾选提交会删错对象）
  useEffect(() => {
    setPage(1);
    setSelected([]);
  }, [keyword, taskIdFilter, statusFilter, triggerFilter, startFrom, startTo, pageSize]);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = { page, size: pageSize };
      if (taskIdFilter) params.workflow_id = Number(taskIdFilter);
      if (statusFilter) params.status = statusFilter;
      if (triggerFilter) params.trigger_type = triggerFilter;
      if (startFrom) params.start_from = startFrom;
      if (startTo) params.start_to = startTo;
      if (keyword) params.q = keyword;
      const { data } = await dagApi.listRuns(params);
      setRuns(data.items || []);
      setTotal(data.total || 0);
    } catch (error: any) {
      // 拉清单失败要显式告知（服务端 400 的筛选拒因原样转述），不能静默渲成空清单
      setRuns([]);
      setTotal(0);
      toast.error(error?.response?.data?.detail || tr('k_1f53c773e3'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, taskIdFilter, statusFilter, triggerFilter, startFrom, startTo, keyword]);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const hasFilter = !!(kwInput || taskIdFilter || statusFilter || triggerFilter || startFrom || startTo);
  const resetFilters = () => {
    setKwInput('');
    setTaskIdFilter('');
    setStatusFilter('');
    setTriggerFilter('');
    setStartFrom('');
    setStartTo('');
  };

  // 进入详情带上来源：详情页的「返回」要回到运行记录，而不是物化任务页
  const openRun = (run: DagRun) =>
    navigate(`/data/sync/dag-runs/${run.id}`, { state: { from: '/data/sync/logs' } });

  // ── 勾选：只对当前页生效（翻页/换筛选即清空，避免拿看不见的 id 提交）──
  const pageIds = runs.map(run => run.id);
  const allPageSelected = runs.length > 0 && runs.every(run => selected.includes(run.id));
  const toggleSelect = (id: number) =>
    setSelected(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]));
  const toggleSelectPage = () => setSelected(prev => (
    allPageSelected ? prev.filter(id => !pageIds.includes(id)) : [...new Set([...prev, ...pageIds])]
  ));
  const exitBatchMode = () => { setBatchMode(false); setSelected([]); };

  // 单条终止 = 服务端条件更新终态 + 同批收尾节点；已结束的如实回“无需停止”，不当作成功
  const handleStop = async () => {
    if (!stopTarget) return;
    setStopping(true);
    try {
      const { data } = await dagApi.stopRun(stopTarget.id);
      if (data?.cancelled) toast.success(tr('k_82e116a001'));
      else toast.error(tr('k_4e0b7c21da'));
      setStopTarget(null);
      await loadRuns();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || tr('k_fd59df83b0'));
    } finally {
      setStopping(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await dagApi.deleteRun(deleteTarget.id);
      toast.success(tr('k_d09aa69974'));
      setDeleteTarget(null);
      // 末页删空要收敛到最后一个有效页，停在空白页会让人以为没删掉
      const lastPage = Math.max(1, Math.ceil((total - 1) / pageSize));
      if (page > lastPage) setPage(lastPage);
      else await loadRuns();
    } catch (error: any) {
      // 服务端拒因（如「运行中请先停止」）必须原样到达用户，不能笼统报失败
      toast.error(error?.response?.data?.detail || tr('k_11da547f2d'));
    } finally {
      setDeleting(false);
    }
  };

  // 批量删除/停止：服务端逐条复用单条实现并逐条回报，这里不做本地预判
  const runBatch = async () => {
    if (!batchConfirm || !selected.length) return;
    const action = batchConfirm;
    setBatchBusy(true);
    try {
      const { data } = action === 'delete'
        ? await dagApi.batchDeleteRuns(selected)
        : await dagApi.batchStopRuns(selected);
      setBatchResult(data);
      // 未处理的项保持勾选，可修正后重试（已成功的不再勾）
      setSelected((data.failed || []).map(f => f.id));
      setBatchConfirm(null);
      if (action === 'delete' && data.succeeded_count > 0) {
        const lastPage = Math.max(1, Math.ceil((total - data.succeeded_count) / pageSize));
        if (page > lastPage) setPage(lastPage);
        else await loadRuns();
      } else {
        await loadRuns();
      }
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || tr('k_bd017e6feb'));
    } finally {
      setBatchBusy(false);
    }
  };

  const colCount = canDelete && batchMode ? 10 : 9;

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-2xl font-bold">{t("k_b39a4b0e31")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t("k_a8797a4108")}</p>
        </div>
        <div className="flex items-center gap-2">
          {canDelete && (
            <Button
              variant={batchMode ? 'secondary' : 'outline'}
              size="sm"
              aria-pressed={batchMode}
              onClick={() => (batchMode ? exitBatchMode() : setBatchMode(true))}
            >
              <CheckSquare className="w-4 h-4 mr-1" />
              {batchMode ? tr('k_07c2859e08') : tr('k_7f7c624a84')}</Button>
          )}
          <Button variant="outline" size="sm" onClick={loadRuns}>
            <RefreshCw className="w-4 h-4 mr-1" />
            {tr('k_38108eaa1d')}</Button>
        </div>
      </div>

      {/* Filters：改筛选即回第 1 页（停在旧页码会拿到错位空清单）；条件全部下推服务端 */}
      <div className="flex items-center gap-2 flex-wrap shrink-0">
        <Input
          value={kwInput}
          onChange={e => setKwInput(e.target.value)}
          placeholder={t("k_053775bef2")}
          aria-label={t("k_053775bef2")}
          className="w-[200px] h-9"
        />
        <Select value={taskIdFilter || 'all'}
          onValueChange={v => setTaskIdFilter(v === 'all' ? '' : v)}>
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
          onValueChange={v => setStatusFilter(v === 'all' ? '' : v)}>
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
        <Select value={triggerFilter || 'all'}
          onValueChange={v => setTriggerFilter(v === 'all' ? '' : v)}>
          <SelectTrigger className="w-[150px]" aria-label={t("k_f67f1852d8")}>
            <SelectValue placeholder={t("k_5718ebb5e1")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_5718ebb5e1")}</SelectItem>
            {Object.entries(TRIGGER_MAP).map(([value, label]) => (
              <SelectItem key={value} value={value}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {/* 时间维度按「开始时间（未启动则创建时间）」判定，含首尾当日 */}
        <Input
          type="date"
          value={startFrom}
          onChange={e => setStartFrom(e.target.value)}
          aria-label={t("k_1f29196891")}
          title={t("k_1f29196891")}
          className="w-[150px] h-9"
        />
        <span className="text-muted-foreground text-sm">—</span>
        <Input
          type="date"
          value={startTo}
          onChange={e => setStartTo(e.target.value)}
          aria-label={t("k_1d468be946")}
          title={t("k_1d468be946")}
          className="w-[150px] h-9"
        />
        {hasFilter && (
          <Button size="sm" variant="ghost" className="h-9" onClick={resetFilters}>
            {tr('k_3d81345303')}</Button>
        )}
      </div>

      {/* 批量操作条：进入批量模式即出现（含全选，未选时也可先全选）；未选时动作按钮置灰 */}
      {canDelete && batchMode && (
        <div className="flex items-center gap-2 shrink-0 rounded-md border bg-muted/30 px-3 py-2 flex-wrap">
          <Button size="sm" variant="outline" className="h-8 text-xs" onClick={toggleSelectPage}>
            {allPageSelected ? <CheckSquare className="w-4 h-4 mr-1" /> : <Square className="w-4 h-4 mr-1" />}
            {tr('k_c8121d9d63')}</Button>
          {selected.length > 0 && (
            <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => setSelected([])}>
              {tr('k_a6d3de0e86')}</Button>
          )}
          <span className="text-sm text-muted-foreground">
            {tr('k_2b02a72e32', { p0: selected.length })}</span>
          <div className="flex-1" />
          <Button size="sm" variant="outline" onClick={() => setBatchConfirm('stop')}
            disabled={batchBusy || selected.length === 0}>
            {batchBusy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <XCircle className="w-4 h-4 mr-1" />}
            {tr('k_4e683b59ac')}</Button>
          <Button size="sm" variant="destructive" onClick={() => setBatchConfirm('delete')}
            disabled={batchBusy || selected.length === 0}>
            {batchBusy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Trash2 className="w-4 h-4 mr-1" />}
            {tr('k_7fb62b3011')}</Button>
        </div>
      )}

      {/* 运行记录表：仅此区滚动（页头/分页固定） */}
      <div className="flex-1 min-h-0 overflow-auto border rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              {canDelete && batchMode && <th className="w-10 p-3" />}
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
                <td colSpan={colCount} className="p-8 text-center text-muted-foreground">
                  {tr('k_26b5bd4947')}</td>
              </tr>
            ) : runs.length === 0 ? (
              <tr>
                <td colSpan={colCount} className="p-8 text-center text-muted-foreground">
                  {tr('k_ae733366a8')}</td>
              </tr>
            ) : (
              runs.map(run => {
                const stats = run.stats_json || {};
                const ms = elapsedMs(run);
                const active = ACTIVE_STATUS.has(run.status);
                return (
                  <tr
                    key={run.id}
                    className="border-t hover:bg-muted/30 cursor-pointer"
                    onClick={() => openRun(run)}
                  >
                    {canDelete && batchMode && (
                      <td className="w-10 p-3" onClick={e => e.stopPropagation()}>
                        <button
                          type="button"
                          aria-label={`${tr('k_3e44b2a933')} #${run.id}`}
                          aria-pressed={selected.includes(run.id)}
                          onClick={() => toggleSelect(run.id)}
                          className="text-muted-foreground hover:text-foreground"
                        >
                          {selected.includes(run.id)
                            ? <CheckSquare className="w-4 h-4 text-primary" />
                            : <Square className="w-4 h-4" />}
                        </button>
                      </td>
                    )}
                    <td className="p-3 font-medium">{run.workflow_name || '—'}</td>
                    <td className="p-3">
                      <span
                        title={run.is_stale ? tr('k_3d81f9ac47') : undefined}
                        className={run.is_stale
                          ? 'inline-flex rounded-md ring-1 ring-amber-500/60'
                          : undefined}
                      >
                        <Badge variant="outline" className={STATUS_MAP[run.status]?.color}>
                          {STATUS_MAP[run.status]?.label || run.status}
                        </Badge>
                      </span>
                    </td>
                    <td className="p-3 text-xs">{TRIGGER_MAP[run.trigger_type] || run.trigger_type}</td>
                    <td className="p-3 text-right text-xs">{Number(stats.rows_read || 0).toLocaleString()}</td>
                    <td className="p-3 text-right text-xs">{Number(stats.rows_written || 0).toLocaleString()}</td>
                    <td className="p-3 text-xs">{ms != null ? `${(ms / 1000).toFixed(1)}s` : '-'}</td>
                    <td className="p-3 text-xs text-muted-foreground">{fmtTime(run.started_at)}</td>
                    <td className="p-3 text-xs text-muted-foreground">{fmtTime(run.finished_at)}</td>
                    <td className="p-3 text-right" onClick={e => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        <Button variant="ghost" size="sm" onClick={() => openRun(run)}>
                          {t("k_f7acefd2d4")}</Button>
                        {canDelete && active && (
                        <Button variant="ghost" size="sm"
                          className="text-amber-600"
                          title={tr('k_578e6e096e')}
                          aria-label={tr('k_578e6e096e')}
                          onClick={() => setStopTarget(run)}>
                          <StopCircle className="w-4 h-4" />
                        </Button>
                        )}
                        {canDelete && (
                        <Button variant="ghost" size="sm"
                          className="text-red-500"
                          aria-label={t("k_3755f56f2f")}
                          disabled={active}
                          title={active ? tr('k_053d8e9534') : undefined}
                          onClick={() => setDeleteTarget(run)}>
                          <Trash2 className="w-4 h-4" />
                        </Button>
                        )}
                      </div>
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

      {/* 终止运行：残留实例只能手动收口（worker 已消失时状态不会自己变） */}
      <Dialog open={!!stopTarget} onOpenChange={() => setStopTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{tr('k_578e6e096e')}</DialogTitle>
            <DialogDescription>
              {stopTarget?.workflow_name || '—'} · {fmtTime(stopTarget?.started_at)}
              <span className="block mt-1">{tr('k_9f2c1a7bd3')}</span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStopTarget(null)} disabled={stopping}>
              {t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleStop} disabled={stopping}>
              {stopping && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}
              {tr('k_095e938e2a')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除运行记录：不可恢复，必须二次确认（含节点明细与对账结果） */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {deleteTarget?.workflow_name || '—'} · {fmtTime(deleteTarget?.started_at)}
              <span className="block mt-1">{tr('k_87ee9f364c')}</span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              {t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {t("k_3755f56f2f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 批量确认：逐条语义说清（运行中删除会被逐条拒、已结束停止算未处理） */}
      <Dialog open={!!batchConfirm} onOpenChange={() => setBatchConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {batchConfirm === 'stop' ? tr('k_4e683b59ac') : t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {batchConfirm === 'stop'
                ? tr('k_1080b885cf', { p0: selected.length })
                : tr('k_ef34354f2b', { p0: selected.length })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBatchConfirm(null)}>
              {t("k_4d0b4688c7")}</Button>
            <Button variant={batchConfirm === 'stop' ? 'default' : 'destructive'}
              onClick={runBatch} disabled={batchBusy}>
              {batchBusy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}
              {batchConfirm === 'stop' ? tr('k_095e938e2a') : tr('k_7fb62b3011')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 批量结果：逐条点名未处理的记录与原因（失败项仍保持勾选，可重试） */}
      <Dialog open={!!batchResult} onOpenChange={() => setBatchResult(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {batchResult?.action === 'stop' ? tr('k_2d04611ee6') : tr('k_a72199c91f')}</DialogTitle>
            <DialogDescription>{batchResult?.message}</DialogDescription>
          </DialogHeader>
          {!!batchResult?.failed?.length && (
            <div className="max-h-64 overflow-auto space-y-1 text-xs">
              {batchResult.failed.map(item => (
                <div key={item.id} className="flex gap-2">
                  <span className="text-muted-foreground shrink-0">#{item.id}</span>
                  <span className="font-medium shrink-0">{item.workflow_name || '—'}</span>
                  <span className="text-destructive">{item.reason}</span>
                </div>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setBatchResult(null)}>
              {t("k_cb63c62e50")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}