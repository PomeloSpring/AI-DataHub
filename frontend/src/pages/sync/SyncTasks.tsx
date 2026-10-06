import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  Plus, Play, History, Edit, Trash2, RefreshCw, Workflow, Zap,
  Table2, ArrowRight,
} from 'lucide-react';
import { dagApi, type DagWorkflow, type DagRun, type DagGraph } from '@/api/dag';
import { udfApi, type Udf } from '@/api/udf';
import { DatasourceSelect } from './DatasourceSelect';

// ── 任务中心：简单同步（单节点 DAG）与 DAG 编排统一列表 ──
// 模型统一：简单同步保存为 kind='simple' 的单节点 DAG，执行/调度/血缘/质量同构。

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  queued: { label: '排队中', className: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  running: { label: '运行中', className: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  success: { label: '成功', className: 'bg-green-500/10 text-green-500 border-green-500/20' },
  partial: { label: '部分成功', className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  failed: { label: '失败', className: 'bg-red-500/10 text-red-500 border-red-500/20' },
  timeout: { label: '超时', className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  cancelled: { label: '已取消', className: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

function StatusBadge({ status }: { status: string | null }) {
  const conf = STATUS_MAP[status || ''] || { label: status || '—', className: 'bg-muted text-muted-foreground' };
  return <Badge variant="outline" className={conf.className}>{conf.label}</Badge>;
}

function KindBadge({ kind }: { kind: string }) {
  return kind === 'simple'
    ? <Badge variant="outline" className="bg-amber-500/10 text-amber-500 border-amber-500/20"><Zap className="w-3 h-3 mr-0.5" />简单同步</Badge>
    : <Badge variant="outline" className="bg-blue-500/10 text-blue-500 border-blue-500/20"><Workflow className="w-3 h-3 mr-0.5" />DAG 编排</Badge>;
}

function fmtTime(value?: string | null): string {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '—';
}

// ── 简单同步表单（生成单节点 DAG） ──
interface SimpleForm {
  name: string; description: string;
  source_datasource: string; source_table: string;
  target_datasource: string; target_table: string;
  sync_mode: string; incremental_column: string; write_mode: string;
  transform_sql: string; udf_refs: string[];
  schedule_cron: string;
}

const EMPTY_FORM: SimpleForm = {
  name: '', description: '',
  source_datasource: '', source_table: '',
  target_datasource: '', target_table: '',
  sync_mode: 'full', incremental_column: '', write_mode: 'append',
  transform_sql: '', udf_refs: [], schedule_cron: '',
};

function formToGraph(f: SimpleForm): DagGraph {
  return {
    nodes: [{
      key: 'sync', name: f.name, type: 'sync',
      config: {
        source_datasource: f.source_datasource, source_table: f.source_table,
        target_datasource: f.target_datasource, target_table: f.target_table,
        sync_mode: f.sync_mode as any,
        incremental_column: f.sync_mode === 'incremental' ? f.incremental_column : undefined,
        write_mode: f.write_mode as any,
        transform_sql: f.transform_sql.trim() || undefined,
        udf_refs: f.udf_refs.length ? f.udf_refs : undefined,
      },
    }],
    edges: [],
  };
}

export default function SyncTasks() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<DagWorkflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editTask, setEditTask] = useState<DagWorkflow | null>(null);
  const [form, setForm] = useState<SimpleForm>(EMPTY_FORM);
  const [udfs, setUdfs] = useState<Udf[]>([]);
  const [historyTask, setHistoryTask] = useState<DagWorkflow | null>(null);
  const [runs, setRuns] = useState<DagRun[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await dagApi.listWorkflows({ page: 1, size: 100 });
      setTasks(data.items || []);
    } catch {
      toast.error('任务列表加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    udfApi.list().then(({ data }) => setUdfs(data.items || [])).catch(() => setUdfs([]));
  }, [load]);

  const openSimpleCreate = () => {
    setWizardOpen(false);
    setEditTask(null);
    setForm(EMPTY_FORM);
    setFormOpen(true);
  };

  const openEdit = (task: DagWorkflow) => {
    if ((task.kind || 'dag') !== 'simple') {
      navigate(`/data/sync/dag/${task.id}`);
      return;
    }
    const node = (task.graph_json?.nodes || [])[0] || {};
    const c = node.config || {};
    setEditTask(task);
    setForm({
      name: task.name, description: task.description || '',
      source_datasource: c.source_datasource || '', source_table: c.source_table || '',
      target_datasource: c.target_datasource || '', target_table: c.target_table || '',
      sync_mode: c.sync_mode || 'full', incremental_column: c.incremental_column || '',
      write_mode: c.write_mode || 'append',
      transform_sql: c.transform_sql || '', udf_refs: c.udf_refs || [],
      schedule_cron: task.cron_expression || '',
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim() || !form.source_datasource || !form.source_table
        || !form.target_datasource || !form.target_table) {
      toast.error('请完整填写名称、源/目标数据源与表');
      return;
    }
    try {
      if (editTask) {
        await dagApi.updateWorkflow(editTask.id, {
          name: form.name, description: form.description,
          graph_json: formToGraph(form),
          cron_expression: form.schedule_cron || null,
        });
        toast.success('已更新');
      } else {
        await dagApi.createWorkflow({
          name: form.name, description: form.description, kind: 'simple',
          graph_json: formToGraph(form),
          cron_expression: form.schedule_cron || null,
        });
        toast.success('已创建');
      }
      setFormOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    }
  };

  const handleRun = async (task: DagWorkflow) => {
    try {
      await dagApi.runWorkflow(task.id);
      toast.success('已进入执行队列');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '触发失败');
    }
  };

  const openHistory = async (task: DagWorkflow) => {
    setHistoryTask(task);
    try {
      const { data } = await dagApi.listWorkflowRuns(task.id, { page: 1, size: 20 });
      setRuns(data.items || []);
    } catch {
      setRuns([]);
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">数据同步</h1>
          <p className="text-muted-foreground text-sm mt-1">
            离线数据同步与转换任务中心：简单同步与 DAG 编排统一管理，支持 cron 调度、数据流查看与运行监控
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="w-4 h-4 mr-1" />刷新
          </Button>
          <Button size="sm" onClick={() => setWizardOpen(true)}>
            <Plus className="w-4 h-4 mr-1" />新建任务
          </Button>
        </div>
      </div>

      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">任务名称</th>
              <th className="text-left p-3 font-medium">类型</th>
              <th className="text-left p-3 font-medium">数据流</th>
              <th className="text-left p-3 font-medium">调度</th>
              <th className="text-left p-3 font-medium">上次状态</th>
              <th className="text-center p-3 font-medium">启用</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">加载中...</td></tr>
            ) : tasks.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">暂无同步任务</td></tr>
            ) : tasks.map(task => (
              <tr key={task.id} className="border-t hover:bg-muted/30">
                <td className="p-3">
                  <div className="font-medium">{task.name}</div>
                  {task.description && (
                    <div className="text-xs text-muted-foreground truncate max-w-[220px]">{task.description}</div>
                  )}
                </td>
                <td className="p-3"><KindBadge kind={task.kind || 'dag'} /></td>
                <td className="p-3">
                  <span className="text-xs text-muted-foreground flex items-center gap-1">
                    <Table2 className="w-3 h-3 shrink-0" />
                    <span className="truncate max-w-[260px]" title={task.dataflow_summary}>
                      {task.dataflow_summary || '—'}
                    </span>
                  </span>
                </td>
                <td className="p-3">
                  <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{task.cron_expression || '手动'}</code>
                </td>
                <td className="p-3">
                  <div className="flex items-center gap-2">
                    <StatusBadge status={task.last_status || null} />
                    <span className="text-xs text-muted-foreground">{fmtTime(task.last_run_at)}</span>
                  </div>
                </td>
                <td className="p-3 text-center">
                  <Switch checked={!!task.is_active} onCheckedChange={async v => {
                    try {
                      await dagApi.updateWorkflow(task.id, { is_active: v });
                      toast.success(v ? '已启用调度' : '已停用调度');
                      load();
                    } catch (e: any) {
                      toast.error(e?.response?.data?.detail || '操作失败');
                    }
                  }} />
                </td>
                <td className="p-3">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="sm" onClick={() => handleRun(task)} title="立即运行"><Play className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" onClick={() => openEdit(task)} title="编辑"><Edit className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" onClick={() => openHistory(task)} title="运行历史"><History className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" title="删除"
                      onClick={async () => {
                        try {
                          await dagApi.deleteWorkflow(task.id);
                          toast.success('已删除');
                          load();
                        } catch (e: any) {
                          toast.error(e?.response?.data?.detail || '删除失败');
                        }
                      }}>
                      <Trash2 className="w-4 h-4 text-red-500" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 新建向导：选形态 */}
      <Dialog open={wizardOpen} onOpenChange={setWizardOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>新建同步任务</DialogTitle></DialogHeader>
          <div className="grid grid-cols-2 gap-4 py-2">
            <button className="border rounded-lg p-4 text-left hover:border-primary transition-colors"
              onClick={openSimpleCreate}>
              <Zap className="w-6 h-6 text-amber-500 mb-2" />
              <div className="font-medium">简单同步</div>
              <div className="text-xs text-muted-foreground mt-1">
                表单配置：源表 → 转换（可选 UDF）→ 目标表；内部为单节点 DAG，执行/血缘/质量同构
              </div>
            </button>
            <button className="border rounded-lg p-4 text-left hover:border-primary transition-colors"
              onClick={() => { setWizardOpen(false); navigate('/data/sync/dag/new'); }}>
              <Workflow className="w-6 h-6 text-blue-500 mb-2" />
              <div className="font-medium">DAG 编排</div>
              <div className="text-xs text-muted-foreground mt-1">
                画布编排多节点（同步 / SQL 任务 / 控制），支持依赖、并行与数据流视图
              </div>
            </button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 简单同步表单 */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editTask ? '编辑简单同步任务' : '新建简单同步任务'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">任务名称 *</label>
                <Input className="mt-1" value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div>
                <label className="text-sm font-medium">Cron 调度（可选）</label>
                <Input className="mt-1" value={form.schedule_cron}
                  onChange={e => setForm(f => ({ ...f, schedule_cron: e.target.value }))}
                  placeholder="如 0 2 * * *（留空仅手动）" />
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">描述</label>
              <Input className="mt-1" value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">源数据源 *</label>
                <div className="mt-1">
                  <DatasourceSelect value={form.source_datasource}
                    onChange={v => setForm(f => ({ ...f, source_datasource: v }))} />
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">源表 *</label>
                <Input className="mt-1" value={form.source_table}
                  onChange={e => setForm(f => ({ ...f, source_table: e.target.value }))} />
              </div>
              <div>
                <label className="text-sm font-medium">目标数据源 *</label>
                <div className="mt-1">
                  <DatasourceSelect value={form.target_datasource}
                    onChange={v => setForm(f => ({ ...f, target_datasource: v }))} />
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">目标表 *</label>
                <Input className="mt-1" value={form.target_table}
                  onChange={e => setForm(f => ({ ...f, target_table: e.target.value }))} />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="text-sm font-medium">同步模式</label>
                <Select value={form.sync_mode} onValueChange={v => setForm(f => ({ ...f, sync_mode: v }))}>
                  <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="full">全量</SelectItem>
                    <SelectItem value="incremental">增量</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium">写入模式</label>
                <Select value={form.write_mode} onValueChange={v => setForm(f => ({ ...f, write_mode: v }))}>
                  <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="append">追加</SelectItem>
                    <SelectItem value="overwrite">覆盖</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.sync_mode === 'incremental' && (
                <div>
                  <label className="text-sm font-medium">增量列 *</label>
                  <Input className="mt-1" value={form.incremental_column}
                    onChange={e => setForm(f => ({ ...f, incremental_column: e.target.value }))} />
                </div>
              )}
            </div>
            <div>
              <label className="text-sm font-medium">转换 SQL（可选，可引用 UDF）</label>
              <Textarea className="mt-1 font-mono min-h-[90px]" value={form.transform_sql}
                onChange={e => setForm(f => ({ ...f, transform_sql: e.target.value }))}
                placeholder={'SELECT id, name_mask(name) AS name, ip2geo(ip_addr) AS region FROM {{source}}'} />
              <p className="text-xs text-muted-foreground mt-1">
                {'留空 = 整表同步；填写后按此 SELECT 取数（引用源表或 {{source}} 占位）'}
              </p>
            </div>
            <div>
              <label className="text-sm font-medium">UDF 引用</label>
              <div className="mt-1 flex flex-wrap gap-1">
                {udfs.map(u => {
                  const ref = `${u.name}:${u.version}`;
                  const active = form.udf_refs.includes(ref) || form.udf_refs.includes(u.name);
                  return (
                    <Badge key={ref} variant={active ? 'default' : 'outline'}
                      className="cursor-pointer text-xs"
                      onClick={() => setForm(f => {
                        const refs = new Set(f.udf_refs);
                        if (active) { refs.delete(ref); refs.delete(u.name); } else refs.add(ref);
                        return { ...f, udf_refs: [...refs] };
                      })}>
                      {u.name}
                    </Badge>
                  );
                })}
                {udfs.length === 0 && <span className="text-xs text-muted-foreground">暂无 UDF（数据中台-UDF 管理查看）</span>}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 运行历史 */}
      <Dialog open={!!historyTask} onOpenChange={o => { if (!o) setHistoryTask(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>运行历史 — {historyTask?.name}</DialogTitle></DialogHeader>
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left p-2 font-medium">触发</th>
                <th className="text-left p-2 font-medium">状态</th>
                <th className="text-left p-2 font-medium">开始</th>
                <th className="text-left p-2 font-medium">结束</th>
                <th className="text-right p-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 ? (
                <tr><td colSpan={5} className="p-6 text-center text-muted-foreground">暂无运行记录</td></tr>
              ) : runs.map(run => (
                <tr key={run.id} className="border-t">
                  <td className="p-2">{run.trigger_type}</td>
                  <td className="p-2"><StatusBadge status={run.status} /></td>
                  <td className="p-2 text-xs">{fmtTime(run.started_at)}</td>
                  <td className="p-2 text-xs">{fmtTime(run.finished_at)}</td>
                  <td className="p-2 text-right">
                    <Button variant="ghost" size="sm"
                      onClick={() => navigate(`/data/sync/dag-runs/${run.id}`)}>
                      详情 <ArrowRight className="w-3 h-3 ml-1" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DialogContent>
      </Dialog>
    </div>
  );
}
