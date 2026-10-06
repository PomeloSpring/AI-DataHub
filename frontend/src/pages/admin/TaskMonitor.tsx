/**
 * 任务监控 — 系统配置「运维管理」下的统一任务监控台
 *
 * 三个页签：
 * 1. 定时任务：系统内置任务（可暂停不可移除）+ 用户创建的定时任务（可停止/移除）；
 * 2. 队列任务：定时执行 / 报表生成 / 同步执行 三类队列执行实例（进度、结果、卡死标记、停止）；
 * 3. 同步任务：数据同步任务定义 + 本体知识库同步水位线。
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import {
  Activity, RefreshCw, Play, Square, Trash2, Pause, ListChecks, Timer,
  AlertTriangle, XCircle, Clock, Database, Search,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  fetchMonitorSummary, listMonitoredTasks, stopMonitoredTask, removeMonitoredTask,
  pauseSystemJob, listQueuedRuns, stopQueuedRun, cleanupStaleRuns,
  listMonitoredSyncTasks, toggleMonitoredSyncTask, stopMonitoredSyncTask,
  removeMonitoredSyncTask, listKbSyncState,
  type MonitorSummary, type MonitoredTask, type QueuedRun, type SyncTaskRow, type KbSyncRow,
} from '@/api/taskMonitor';
import { dagApi, type DagRun } from '@/api/dag';

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  queued: { label: '排队中', className: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  running: { label: '运行中', className: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  success: { label: '成功', className: 'bg-green-500/10 text-green-500 border-green-500/20' },
  partial: { label: '部分成功', className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  failed: { label: '失败', className: 'bg-red-500/10 text-red-500 border-red-500/20' },
  timeout: { label: '超时', className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  cancelled: { label: '已取消', className: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
  paused: { label: '已暂停', className: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

const RUN_KIND_LABEL: Record<string, string> = {
  scheduled: '定时执行', report: '报表生成', sync: '同步执行',
};

function StatusBadge({ status }: { status: string | null }) {
  const conf = STATUS_MAP[status || ''] || { label: status || '未知', className: 'bg-muted text-muted-foreground' };
  return <Badge variant="outline" className={conf.className}>{conf.label}</Badge>;
}

function fmtTime(value: string | null | undefined): string {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '—';
}

function fmtElapsed(run: QueuedRun): string {
  if (run.elapsed_ms) return `${(run.elapsed_ms / 1000).toFixed(1)}s`;
  return '—';
}

function runProgress(run: QueuedRun): string {
  if (run.kind === 'scheduled') {
    const done = (run.done_count ?? 0) + (run.fail_count ?? 0);
    return run.total_count ? `${done}/${run.total_count} 项` : '—';
  }
  if (run.kind === 'report') {
    return run.raw_status === 'queued' ? '排队中' : run.raw_status === 'running' ? '生成中' : '—';
  }
  return `读 ${run.rows_read ?? 0} 行 / 写 ${run.rows_written ?? 0} 行`;
}

function StatCard({ icon: Icon, label, value, accent, warn }: {
  icon: any; label: string; value: number; accent?: string; warn?: boolean;
}) {
  return (
    <Card>
      <CardContent className="p-4 flex items-center gap-3">
        <div className={`h-10 w-10 rounded-lg flex items-center justify-center ${accent || 'bg-primary/10'}`}>
          <Icon className={`h-5 w-5 ${warn && value > 0 ? 'text-destructive' : 'text-primary'}`} />
        </div>
        <div>
          <div className={`text-2xl font-bold leading-tight ${warn && value > 0 ? 'text-destructive' : ''}`}>{value}</div>
          <div className="text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  );
}

/** 详情弹窗内的最近执行历史：点一行打开该次执行的完整详情 */
function RunHistoryTable({ runs, loading, onOpenRun }: {
  runs: QueuedRun[]; loading: boolean; onOpenRun: (run: QueuedRun) => void;
}) {
  return (
    <div className="border rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            <th className="text-left p-2 font-medium">开始时间</th>
            <th className="text-left p-2 font-medium">状态</th>
            <th className="text-left p-2 font-medium">进度</th>
            <th className="text-left p-2 font-medium">耗时</th>
            <th className="text-left p-2 font-medium">结果</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">加载中...</td></tr>
          ) : runs.length === 0 ? (
            <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">暂无执行记录</td></tr>
          ) : runs.map(run => (
            <tr key={`${run.kind}-${run.run_id}`}
                className="border-t hover:bg-muted/30 cursor-pointer"
                onClick={() => onOpenRun(run)}>
              <td className="p-2">{fmtTime(run.started_at)}</td>
              <td className="p-2">
                <div className="flex items-center gap-1">
                  <StatusBadge status={run.normalized_status} />
                  {run.stuck && (
                    <Badge variant="outline" className="bg-red-500/10 text-red-500 border-red-500/20">卡住</Badge>
                  )}
                </div>
              </td>
              <td className="p-2">{runProgress(run)}</td>
              <td className="p-2">{fmtElapsed(run)}</td>
              <td className="p-2 max-w-[220px]">
                <div className="truncate text-muted-foreground" title={run.result_summary || run.error_hint || ''}>
                  {run.result_summary || run.error_hint || '—'}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function TaskMonitor() {
  const [summary, setSummary] = useState<MonitorSummary | null>(null);
  const [tasks, setTasks] = useState<MonitoredTask[]>([]);
  const [taskSource, setTaskSource] = useState<'all' | 'system' | 'user'>('all');
  const [keyword, setKeyword] = useState('');
  const [runs, setRuns] = useState<QueuedRun[]>([]);
  const [runsTotal, setRunsTotal] = useState(0);
  const [runKind, setRunKind] = useState<'all' | 'scheduled' | 'report' | 'sync'>('all');
  const [runStatus, setRunStatus] = useState('active');
  const [runPage, setRunPage] = useState(1);
  const [syncTasks, setSyncTasks] = useState<SyncTaskRow[]>([]);
  const [kbRows, setKbRows] = useState<KbSyncRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<MonitoredTask | SyncTaskRow | null>(null);
  const [removeKind, setRemoveKind] = useState<'task' | 'sync'>('task');
  const [detailRun, setDetailRun] = useState<QueuedRun | null>(null);
  const [detailTask, setDetailTask] = useState<MonitoredTask | null>(null);
  const [detailSync, setDetailSync] = useState<SyncTaskRow | null>(null);
  const [taskRuns, setTaskRuns] = useState<QueuedRun[]>([]);
  const [taskRunsLoading, setTaskRunsLoading] = useState(false);
  const [dagRuns, setDagRuns] = useState<DagRun[]>([]);
  const navigate = useNavigate();
  const PAGE_SIZE = 20;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sum, taskRes, runRes, syncRes, kbRes] = await Promise.all([
        fetchMonitorSummary(),
        listMonitoredTasks({ source: taskSource === 'all' ? undefined : taskSource }),
        listQueuedRuns({
          kind: runKind === 'all' ? undefined : runKind,
          status: runStatus === 'all' ? undefined : runStatus,
          page: runPage, size: PAGE_SIZE,
        }),
        listMonitoredSyncTasks(),
        listKbSyncState(),
      ]);
      setSummary(sum);
      setTasks(taskRes.items);
      setRuns(runRes.items);
      setRunsTotal(runRes.total);
      setSyncTasks(syncRes.items);
      setKbRows(kbRes.items);
      dagApi.listRuns({ page: 1, size: 10 })
        .then(({ data }) => setDagRuns(data.items || []))
        .catch(() => setDagRuns([]));
    } catch (error: any) {
      toast.error(`任务监控加载失败：${error?.response?.data?.detail || error?.message || '请稍后重试'}`);
    } finally {
      setLoading(false);
    }
  }, [taskSource, runKind, runStatus, runPage]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [autoRefresh, load]);

  // ── 任务详情（点开行查看，含最近执行历史）──────────────────────────

  const openTaskDetail = (task: MonitoredTask) => {
    setDetailTask(task);
    setDetailSync(null);
    setTaskRuns([]);
    if (task.kind !== 'scheduled_task') return;
    setTaskRunsLoading(true);
    listQueuedRuns({ kind: 'scheduled', task_id: Number(task.task_id), page: 1, size: 5 })
      .then(res => setTaskRuns(res.items))
      .catch((error: any) => toast.error(`执行历史加载失败：${error?.response?.data?.detail || error?.message || '请稍后重试'}`))
      .finally(() => setTaskRunsLoading(false));
  };

  const openSyncDetail = (task: SyncTaskRow) => {
    setDetailSync(task);
    setDetailTask(null);
    setTaskRuns([]);
    setTaskRunsLoading(true);
    listQueuedRuns({ kind: 'sync', task_id: task.id, page: 1, size: 5 })
      .then(res => setTaskRuns(res.items))
      .catch((error: any) => toast.error(`执行历史加载失败：${error?.response?.data?.detail || error?.message || '请稍后重试'}`))
      .finally(() => setTaskRunsLoading(false));
  };

  // ── Actions ────────────────────────────────────────────────────

  const handlePauseJob = async (task: MonitoredTask, paused: boolean) => {
    try {
      await pauseSystemJob(String(task.task_id), paused);
      toast.success(paused ? '内置任务已暂停' : '内置任务已恢复');
      load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '操作失败');
    }
  };

  const handleStopTask = async (task: MonitoredTask) => {
    try {
      const res = await stopMonitoredTask(Number(task.task_id));
      toast.success(`任务已停止，取消运行中实例 ${res.cancelled_runs} 个`);
      load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '停止失败');
    }
  };

  const handleStopRun = async (run: QueuedRun) => {
    try {
      await stopQueuedRun(run.kind, run.run_id);
      toast.success('已停止该执行实例');
      load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '停止失败');
    }
  };

  const handleCleanupStale = async () => {
    try {
      const res = await cleanupStaleRuns();
      const total = (res.scheduled_runs || 0) + (res.report_runs || 0) + (res.sync_runs || 0);
      toast.success(total > 0 ? `已清理 ${total} 个卡住的执行实例` : '没有需要清理的卡住任务');
      load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '清理失败');
    }
  };

  const confirmRemove = async () => {
    if (!removeTarget) return;
    try {
      if (removeKind === 'task') {
        await removeMonitoredTask(Number((removeTarget as MonitoredTask).task_id));
      } else {
        await removeMonitoredSyncTask((removeTarget as SyncTaskRow).id);
      }
      toast.success('已移除');
      setRemoveTarget(null);
      load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '移除失败');
    }
  };

  const visibleTasks = tasks.filter(t =>
    !keyword.trim() ||
    t.name.toLowerCase().includes(keyword.trim().toLowerCase()) ||
    (t.description || '').toLowerCase().includes(keyword.trim().toLowerCase()));

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <ListChecks className="h-5 w-5 text-primary" />
            任务监控
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            定时任务与队列任务的统一监控：查看进度与结果、停止/移除任务，避免异常任务卡住系统运行
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
            <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} />
            自动刷新 (30s)
          </label>
          <Button variant="outline" size="sm" onClick={handleCleanupStale}>
            <AlertTriangle className="h-4 w-4 mr-1.5" />清理卡住任务
          </Button>
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />刷新
          </Button>
        </div>
      </div>

      {/* Summary */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <StatCard icon={Timer} label="定时任务（启用）" value={summary.scheduled_tasks.active} />
          <StatCard icon={Database} label="系统内置任务" value={summary.system_jobs.total} />
          <StatCard icon={Activity} label="排队/运行中" value={summary.runs.active} />
          <StatCard icon={AlertTriangle} label="卡住待清理" value={summary.runs.stuck} warn />
          <StatCard icon={XCircle} label="累计失败" value={summary.runs.failed_total + summary.runs.timeout_total} warn />
        </div>
      )}

      <Tabs defaultValue="tasks" className="space-y-4">
        <TabsList>
          <TabsTrigger value="tasks">定时任务</TabsTrigger>
          <TabsTrigger value="runs">队列任务</TabsTrigger>
          <TabsTrigger value="sync">同步任务</TabsTrigger>
        </TabsList>

        {/* ── Tab 1: 定时任务 ─────────────────────────────────── */}
        <TabsContent value="tasks" className="space-y-4">
          <div className="flex items-center gap-2 flex-wrap">
            <Select value={taskSource} onValueChange={v => { setTaskSource(v as any); setRunPage(1); }}>
              <SelectTrigger className="w-[160px]"><SelectValue placeholder="任务来源" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部来源</SelectItem>
                <SelectItem value="system">系统内置任务</SelectItem>
                <SelectItem value="user">用户创建任务</SelectItem>
              </SelectContent>
            </Select>
            <div className="relative">
              <Search className="h-4 w-4 absolute left-2.5 top-2.5 text-muted-foreground" />
              <Input className="pl-8 w-[220px]" placeholder="按任务名搜索" value={keyword}
                     onChange={e => setKeyword(e.target.value)} />
            </div>
          </div>

          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left p-3 font-medium">任务名称</th>
                  <th className="text-left p-3 font-medium">来源</th>
                  <th className="text-left p-3 font-medium">调度</th>
                  <th className="text-left p-3 font-medium">状态</th>
                  <th className="text-left p-3 font-medium">上次运行</th>
                  <th className="text-left p-3 font-medium">累计次数</th>
                  <th className="text-left p-3 font-medium">运行中实例</th>
                  <th className="text-right p-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {loading && visibleTasks.length === 0 ? (
                  <tr><td colSpan={8} className="p-8 text-center text-muted-foreground">加载中...</td></tr>
                ) : visibleTasks.length === 0 ? (
                  <tr><td colSpan={8} className="p-8 text-center text-muted-foreground">暂无任务</td></tr>
                ) : visibleTasks.map(task => (
                  <tr key={`${task.kind}-${task.task_id}`}
                      className="border-t hover:bg-muted/30 cursor-pointer"
                      onClick={() => openTaskDetail(task)}>
                    <td className="p-3 max-w-[260px]">
                      <div className="font-medium truncate" title={task.description}>{task.name}</div>
                      <div className="text-xs text-muted-foreground truncate">
                        {task.task_type === 'system' ? '系统任务' : task.task_type === 'agent' ? 'Agent 分析' : '查询任务'}
                        {task.ownership === 'unclaimed' && ' · 待认领'}
                      </div>
                    </td>
                    <td className="p-3">
                      {task.source === 'system'
                        ? <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20">系统内置</Badge>
                        : <Badge variant="outline">用户创建</Badge>}
                    </td>
                    <td className="p-3 text-muted-foreground">{task.schedule || '—'}</td>
                    <td className="p-3">
                      {task.kind === 'system_job'
                        ? <StatusBadge status={task.enabled ? 'running' : 'paused'} />
                        : <StatusBadge status={task.enabled ? 'running' : 'cancelled'} />}
                      {task.stuck_runs > 0 && (
                        <Badge variant="outline" className="ml-1 bg-red-500/10 text-red-500 border-red-500/20">
                          {task.stuck_runs} 个卡住
                        </Badge>
                      )}
                    </td>
                    <td className="p-3">
                      <div className="flex items-center gap-1.5">
                        <span>{fmtTime(task.last_run_at)}</span>
                        {task.last_status && <StatusBadge status={task.last_status} />}
                      </div>
                      {task.kind === 'system_job' && task.last_result && (
                        <div className="text-xs text-muted-foreground truncate max-w-[220px]" title={task.last_result}>
                          {task.last_result}
                        </div>
                      )}
                    </td>
                    <td className="p-3">{task.run_count}</td>
                    <td className="p-3">{task.active_runs}</td>
                    <td className="p-3" onClick={e => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        {task.kind === 'system_job' ? (
                          <>
                            <Button variant="ghost" size="sm" title={task.enabled ? '暂停内置任务' : '恢复内置任务'}
                                    onClick={() => handlePauseJob(task, task.enabled)}>
                              {task.enabled ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                            </Button>
                            <Button variant="ghost" size="sm" disabled title="系统内置任务不可移除">
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button variant="ghost" size="sm" title="停止任务并取消运行中实例"
                                    disabled={!task.enabled && task.active_runs === 0}
                                    onClick={() => handleStopTask(task)}>
                              <Square className="h-4 w-4" />
                            </Button>
                            <Button variant="ghost" size="sm" title="移除任务"
                                    onClick={() => { setRemoveKind('task'); setRemoveTarget(task); }}>
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TabsContent>

        {/* ── Tab 2: 队列任务 ─────────────────────────────────── */}
        <TabsContent value="runs" className="space-y-4">
          <div className="flex items-center gap-2 flex-wrap">
            <Select value={runKind} onValueChange={v => { setRunKind(v as any); setRunPage(1); }}>
              <SelectTrigger className="w-[160px]"><SelectValue placeholder="队列来源" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部队列</SelectItem>
                <SelectItem value="scheduled">定时执行</SelectItem>
                <SelectItem value="report">报表生成</SelectItem>
                <SelectItem value="sync">同步执行</SelectItem>
              </SelectContent>
            </Select>
            <Select value={runStatus} onValueChange={v => { setRunStatus(v); setRunPage(1); }}>
              <SelectTrigger className="w-[160px]"><SelectValue placeholder="状态" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="active">排队/运行中</SelectItem>
                <SelectItem value="stuck">卡住</SelectItem>
                <SelectItem value="success">成功</SelectItem>
                <SelectItem value="partial">部分成功</SelectItem>
                <SelectItem value="failed">失败</SelectItem>
                <SelectItem value="timeout">超时</SelectItem>
                <SelectItem value="cancelled">已取消</SelectItem>
              </SelectContent>
            </Select>
            <span className="text-sm text-muted-foreground">共 {runsTotal} 条</span>
          </div>

          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left p-3 font-medium">任务名称</th>
                  <th className="text-left p-3 font-medium">队列</th>
                  <th className="text-left p-3 font-medium">状态</th>
                  <th className="text-left p-3 font-medium">进度</th>
                  <th className="text-left p-3 font-medium">开始时间</th>
                  <th className="text-left p-3 font-medium">耗时</th>
                  <th className="text-left p-3 font-medium">Worker</th>
                  <th className="text-left p-3 font-medium">结果</th>
                  <th className="text-right p-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {loading && runs.length === 0 ? (
                  <tr><td colSpan={9} className="p-8 text-center text-muted-foreground">加载中...</td></tr>
                ) : runs.length === 0 ? (
                  <tr><td colSpan={9} className="p-8 text-center text-muted-foreground">暂无执行实例</td></tr>
                ) : runs.map(run => (
                  <tr key={`${run.kind}-${run.run_id}`}
                      className={`border-t hover:bg-muted/30 cursor-pointer ${run.stuck ? 'bg-red-500/5' : ''}`}
                      onClick={() => setDetailRun(run)}>
                    <td className="p-3 max-w-[220px]">
                      <div className="font-medium truncate">{run.task_name || '未命名任务'}</div>
                      <div className="text-xs text-muted-foreground">触发：{run.trigger_type || '—'}</div>
                    </td>
                    <td className="p-3 text-muted-foreground">{RUN_KIND_LABEL[run.kind] || run.kind}</td>
                    <td className="p-3">
                      <div className="flex items-center gap-1.5">
                        <StatusBadge status={run.normalized_status} />
                        {run.stuck && (
                          <Badge variant="outline" className="bg-red-500/10 text-red-500 border-red-500/20">卡住</Badge>
                        )}
                      </div>
                    </td>
                    <td className="p-3">{runProgress(run)}</td>
                    <td className="p-3">{fmtTime(run.started_at)}</td>
                    <td className="p-3">{fmtElapsed(run)}</td>
                    <td className="p-3 text-muted-foreground">{run.worker_id || '—'}</td>
                    <td className="p-3 max-w-[240px]">
                      <div className="truncate text-muted-foreground"
                           title={run.result_summary || run.error_hint || ''}>
                        {run.result_summary || run.error_hint || '—'}
                      </div>
                    </td>
                    <td className="p-3 text-right" onClick={e => e.stopPropagation()}>
                      {(run.raw_status === 'queued' || run.raw_status === 'running') ? (
                        <Button variant="ghost" size="sm" title="停止该执行实例"
                                onClick={() => handleStopRun(run)}>
                          <Square className="h-4 w-4" />
                        </Button>
                      ) : <span className="text-xs text-muted-foreground">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between">
            <Button variant="outline" size="sm" disabled={runPage <= 1}
                    onClick={() => setRunPage(p => Math.max(1, p - 1))}>上一页</Button>
            <span className="text-sm text-muted-foreground">第 {runPage} 页</span>
            <Button variant="outline" size="sm" disabled={runPage * PAGE_SIZE >= runsTotal}
                    onClick={() => setRunPage(p => p + 1)}>下一页</Button>
          </div>
        </TabsContent>

        {/* ── Tab 3: 同步任务 ─────────────────────────────────── */}
        <TabsContent value="sync" className="space-y-6">
          <section className="space-y-2">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Database className="h-4 w-4" />数据同步任务
            </h2>
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="text-left p-3 font-medium">任务名称</th>
                    <th className="text-left p-3 font-medium">同步方式</th>
                    <th className="text-left p-3 font-medium">调度</th>
                    <th className="text-left p-3 font-medium">状态</th>
                    <th className="text-left p-3 font-medium">上次运行</th>
                    <th className="text-left p-3 font-medium">运行中实例</th>
                    <th className="text-right p-3 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {syncTasks.length === 0 ? (
                    <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">暂无同步任务</td></tr>
                  ) : syncTasks.map(task => (
                    <tr key={task.id} className="border-t hover:bg-muted/30 cursor-pointer"
                        onClick={() => openSyncDetail(task)}>
                      <td className="p-3">
                        <div className="font-medium">{task.name}</div>
                        <div className="text-xs text-muted-foreground truncate max-w-[240px]" title={task.description}>
                          {task.description || `${task.source_type || '—'} → ${task.target_type || '—'}`}
                        </div>
                      </td>
                      <td className="p-3 text-muted-foreground">{task.sync_mode || '—'}</td>
                      <td className="p-3 text-muted-foreground">{task.schedule_cron || '手动'}</td>
                      <td className="p-3">
                        <div className="flex items-center gap-2">
                          <Switch checked={!!task.is_active}
                                  onCheckedChange={async v => {
                                    try {
                                      await toggleMonitoredSyncTask(task.id, v);
                                      toast.success(v ? '已启用' : '已停用');
                                      load();
                                    } catch (error: any) {
                                      toast.error(error?.response?.data?.detail || '操作失败');
                                    }
                                  }} />
                          {task.last_status && <StatusBadge status={task.last_status} />}
                        </div>
                      </td>
                      <td className="p-3">{fmtTime(task.last_run_at)}</td>
                      <td className="p-3">{task.active_runs}</td>
                      <td className="p-3" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1">
                          <Button variant="ghost" size="sm" title="停止任务并取消运行中实例"
                                  disabled={!task.is_active && task.active_runs === 0}
                                  onClick={async () => {
                                    try {
                                      const res = await stopMonitoredSyncTask(task.id);
                                      toast.success(`已停止，取消实例 ${res.cancelled_runs} 个`);
                                      load();
                                    } catch (error: any) {
                                      toast.error(error?.response?.data?.detail || '停止失败');
                                    }
                                  }}>
                            <Square className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="sm" title="移除同步任务"
                                  onClick={() => { setRemoveKind('sync'); setRemoveTarget(task); }}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="space-y-2">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Activity className="h-4 w-4" />DAG 工作流运行（近 10 条）
              <Button variant="ghost" size="sm" className="ml-auto" onClick={() => navigate('/data/sync/dag')}>
                工作流管理
              </Button>
            </h2>
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="text-left p-3 font-medium">工作流</th>
                    <th className="text-left p-3 font-medium">触发</th>
                    <th className="text-left p-3 font-medium">状态</th>
                    <th className="text-left p-3 font-medium">节点</th>
                    <th className="text-left p-3 font-medium">开始</th>
                    <th className="text-left p-3 font-medium">结束</th>
                    <th className="text-right p-3 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {dagRuns.length === 0 ? (
                    <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">暂无 DAG 运行</td></tr>
                  ) : dagRuns.map(run => (
                    <tr key={run.id} className="border-t hover:bg-muted/30">
                      <td className="p-3 font-medium">{run.workflow_name || `工作流 ${run.workflow_id}`}</td>
                      <td className="p-3 text-muted-foreground">{run.trigger_type}</td>
                      <td className="p-3"><StatusBadge status={run.status} /></td>
                      <td className="p-3 text-muted-foreground">{run.node_count ?? '—'} 个</td>
                      <td className="p-3">{fmtTime(run.started_at)}</td>
                      <td className="p-3">{fmtTime(run.finished_at)}</td>
                      <td className="p-3 text-right">
                        <Button variant="ghost" size="sm" onClick={() => navigate(`/data/sync/dag-runs/${run.id}`)}>
                          详情
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="space-y-2">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Clock className="h-4 w-4" />本体知识库同步水位
              <span className="text-xs font-normal text-muted-foreground">
                由系统内置任务「知识库同步对账」维护，可暂停该任务以停止自动重推
              </span>
            </h2>
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="text-left p-3 font-medium">本体模型</th>
                    <th className="text-left p-3 font-medium">同步版本</th>
                    <th className="text-left p-3 font-medium">状态</th>
                    <th className="text-left p-3 font-medium">同步时间</th>
                    <th className="text-left p-3 font-medium">提示</th>
                  </tr>
                </thead>
                <tbody>
                  {kbRows.length === 0 ? (
                    <tr><td colSpan={5} className="p-8 text-center text-muted-foreground">暂无同步记录</td></tr>
                  ) : kbRows.map(row => (
                    <tr key={row.model_id} className="border-t hover:bg-muted/30">
                      <td className="p-3 font-medium">{row.model_name}</td>
                      <td className="p-3 text-muted-foreground">{row.synced_version || '—'}</td>
                      <td className="p-3"><StatusBadge status={row.status === 'success' ? 'success' : 'failed'} /></td>
                      <td className="p-3">{fmtTime(row.synced_at)}</td>
                      <td className="p-3 max-w-[280px]">
                        <div className="truncate text-muted-foreground" title={row.error_hint}>{row.error_hint || '—'}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </TabsContent>
      </Tabs>

      {/* 任务详情（定时任务 / 系统内置任务） */}
      <Dialog open={!!detailTask} onOpenChange={open => { if (!open) setDetailTask(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{detailTask?.name}</DialogTitle>
            <DialogDescription>
              {detailTask?.source === 'system' ? '系统内置任务' : '用户创建的定时任务'}
              {detailTask?.description ? ` · ${detailTask.description}` : ''}
            </DialogDescription>
          </DialogHeader>
          {detailTask && (
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div><span className="text-muted-foreground">任务类型：</span>
                  {detailTask.task_type === 'system' ? '系统任务' : detailTask.task_type === 'agent' ? 'Agent 分析' : '查询任务'}
                </div>
                <div><span className="text-muted-foreground">调度：</span>
                  {detailTask.schedule || '—'}
                  {detailTask.timezone ? `（${detailTask.timezone}）` : ''}
                </div>
                <div><span className="text-muted-foreground">创建者：</span>
                  {detailTask.owner_name}{detailTask.ownership === 'unclaimed' ? '（待认领）' : ''}
                </div>
                <div><span className="text-muted-foreground">运行状态：</span>
                  {detailTask.enabled ? '启用中' : detailTask.kind === 'system_job' ? '已暂停' : '已停用'}
                </div>
                <div><span className="text-muted-foreground">上次运行：</span>
                  {fmtTime(detailTask.last_run_at)}
                  {detailTask.last_status && <span className="ml-1.5"><StatusBadge status={detailTask.last_status} /></span>}
                </div>
                <div><span className="text-muted-foreground">累计运行：</span>{detailTask.run_count} 次</div>
                <div><span className="text-muted-foreground">运行中实例：</span>{detailTask.active_runs}</div>
                <div><span className="text-muted-foreground">卡住实例：</span>
                  <span className={detailTask.stuck_runs > 0 ? 'text-destructive font-medium' : ''}>
                    {detailTask.stuck_runs}
                  </span>
                </div>
                {detailTask.kind === 'system_job' ? (
                  <>
                    <div><span className="text-muted-foreground">暂停时间：</span>{fmtTime(detailTask.paused_at)}</div>
                    <div><span className="text-muted-foreground">暂停操作者：</span>{detailTask.paused_by || '—'}</div>
                  </>
                ) : (
                  <>
                    <div><span className="text-muted-foreground">执行问题数：</span>
                      {detailTask.questions_total ?? '—'} 项
                    </div>
                    <div><span className="text-muted-foreground">超时限制：</span>
                      {detailTask.timeout_seconds ? `${detailTask.timeout_seconds} 秒` : '—'}
                    </div>
                  </>
                )}
              </div>

              {detailTask.kind === 'system_job' ? (
                <div>
                  <div className="text-muted-foreground mb-1">最近一次运行结果</div>
                  <div className="p-2 rounded bg-muted/50 whitespace-pre-wrap">
                    {detailTask.last_result || '尚未运行'}
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="text-muted-foreground">最近执行历史（点击行查看详情）</div>
                  <RunHistoryTable runs={taskRuns} loading={taskRunsLoading}
                    onOpenRun={run => { setDetailTask(null); setDetailRun(run); }} />
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            {detailTask && detailTask.kind === 'system_job' ? (
              <Button variant="outline" onClick={() => { handlePauseJob(detailTask, detailTask.enabled); setDetailTask(null); }}>
                {detailTask.enabled ? <Pause className="h-4 w-4 mr-1.5" /> : <Play className="h-4 w-4 mr-1.5" />}
                {detailTask.enabled ? '暂停' : '恢复'}
              </Button>
            ) : detailTask && (
              <>
                <Button variant="outline" onClick={() => { handleStopTask(detailTask); setDetailTask(null); }}>
                  <Square className="h-4 w-4 mr-1.5" />停止
                </Button>
                <Button variant="destructive" onClick={() => {
                  setRemoveKind('task'); setRemoveTarget(detailTask); setDetailTask(null);
                }}>
                  <Trash2 className="h-4 w-4 mr-1.5" />移除
                </Button>
              </>
            )}
            <Button variant="outline" onClick={() => setDetailTask(null)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 同步任务详情 */}
      <Dialog open={!!detailSync} onOpenChange={open => { if (!open) setDetailSync(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{detailSync?.name}</DialogTitle>
            <DialogDescription>{detailSync?.description || '数据同步任务'}</DialogDescription>
          </DialogHeader>
          {detailSync && (
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div><span className="text-muted-foreground">同步方式：</span>{detailSync.sync_mode || '—'}</div>
                <div><span className="text-muted-foreground">同步方向：</span>
                  {detailSync.source_type || '—'} → {detailSync.target_type || '—'}
                </div>
                <div><span className="text-muted-foreground">调度：</span>{detailSync.schedule_cron || '手动触发'}</div>
                <div><span className="text-muted-foreground">运行状态：</span>{detailSync.is_active ? '启用中' : '已停用'}</div>
                <div><span className="text-muted-foreground">上次运行：</span>
                  {fmtTime(detailSync.last_run_at)}
                  {detailSync.last_status && <span className="ml-1.5"><StatusBadge status={detailSync.last_status} /></span>}
                </div>
                <div><span className="text-muted-foreground">累计运行：</span>{detailSync.run_count} 次</div>
                <div><span className="text-muted-foreground">运行中实例：</span>{detailSync.active_runs}</div>
              </div>
              <div className="space-y-2">
                <div className="text-muted-foreground">最近执行历史（点击行查看详情）</div>
                <RunHistoryTable runs={taskRuns} loading={taskRunsLoading}
                  onOpenRun={run => { setDetailSync(null); setDetailRun(run); }} />
              </div>
            </div>
          )}
          <DialogFooter>
            {detailSync && (
              <>
                <Button variant="outline" onClick={async () => {
                  try {
                    const res = await stopMonitoredSyncTask(detailSync.id);
                    toast.success(`已停止，取消实例 ${res.cancelled_runs} 个`);
                    setDetailSync(null);
                    load();
                  } catch (error: any) {
                    toast.error(error?.response?.data?.detail || '停止失败');
                  }
                }}>
                  <Square className="h-4 w-4 mr-1.5" />停止
                </Button>
                <Button variant="destructive" onClick={() => {
                  setRemoveKind('sync'); setRemoveTarget(detailSync); setDetailSync(null);
                }}>
                  <Trash2 className="h-4 w-4 mr-1.5" />移除
                </Button>
              </>
            )}
            <Button variant="outline" onClick={() => setDetailSync(null)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 执行实例详情 */}
      <Dialog open={!!detailRun} onOpenChange={open => { if (!open) setDetailRun(null); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>执行实例详情</DialogTitle>
            <DialogDescription>
              {detailRun ? `${detailRun.task_name || '未命名任务'} · ${RUN_KIND_LABEL[detailRun.kind] || detailRun.kind}` : ''}
            </DialogDescription>
          </DialogHeader>
          {detailRun && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div><span className="text-muted-foreground">状态：</span><StatusBadge status={detailRun.normalized_status} /></div>
                <div><span className="text-muted-foreground">触发方式：</span>{detailRun.trigger_type || '—'}</div>
                <div><span className="text-muted-foreground">开始时间：</span>{fmtTime(detailRun.started_at)}</div>
                <div><span className="text-muted-foreground">结束时间：</span>{fmtTime(detailRun.finished_at)}</div>
                <div><span className="text-muted-foreground">耗时：</span>{fmtElapsed(detailRun)}</div>
                <div><span className="text-muted-foreground">Worker：</span>{detailRun.worker_id || '—'}</div>
                <div><span className="text-muted-foreground">进度：</span>{runProgress(detailRun)}</div>
                <div><span className="text-muted-foreground">阶段错误码：</span>{detailRun.stage_error_code || '—'}</div>
              </div>
              <div>
                <div className="text-muted-foreground mb-1">结果摘要</div>
                <div className="p-2 rounded bg-muted/50 whitespace-pre-wrap">
                  {detailRun.result_summary || '—'}
                </div>
              </div>
              {detailRun.error_hint && (
                <div>
                  <div className="text-muted-foreground mb-1">失败提示</div>
                  <div className="p-2 rounded bg-destructive/10 text-destructive whitespace-pre-wrap">
                    {detailRun.error_hint}
                  </div>
                </div>
              )}
              {detailRun.stuck && (
                <div className="flex items-center gap-2 text-destructive">
                  <AlertTriangle className="h-4 w-4" />
                  该实例已超出执行租约仍未结束，可用「清理卡住任务」或「停止」释放。
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            {detailRun && (detailRun.raw_status === 'queued' || detailRun.raw_status === 'running') && (
              <Button variant="outline" onClick={() => { handleStopRun(detailRun); setDetailRun(null); }}>
                <Square className="h-4 w-4 mr-1.5" />停止
              </Button>
            )}
            <Button variant="outline" onClick={() => setDetailRun(null)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 移除确认 */}
      <Dialog open={!!removeTarget} onOpenChange={open => { if (!open) setRemoveTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认移除任务</DialogTitle>
            <DialogDescription>
              将移除「{removeTarget?.name}」及其执行记录，此操作不可恢复。运行中的实例会先被停止。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRemoveTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={confirmRemove}>
              <Trash2 className="h-4 w-4 mr-1.5" />移除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
