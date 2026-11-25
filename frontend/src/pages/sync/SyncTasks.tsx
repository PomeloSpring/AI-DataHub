import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import {
  Plus, Play, History, Edit, Trash2, RefreshCw, Workflow, Zap,
  Table2, ArrowRight, Eye, AlertTriangle, Loader2, StopCircle,
} from 'lucide-react';
import {
  dagApi, type DagWorkflow, type DagRun, type DagGraph, type DagNodeRun, type WatermarkItem,
} from '@/api/dag';
import { useHasPerm } from '@/stores/permissionStore';
import { DatasourceSelect } from './DatasourceSelect';
import { DatasetSelect, useMaterializableDatasets } from './DatasetSelect';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

// ── 任务中心：定时物化（数据集 → 已登记目标表）与多步 DAG 编排统一列表 ──
// 模型统一：单步物化保存为 kind='simple' 的单节点 DAG（type='materialize'），
// 执行/调度/对账/运行记录与多步编排同构（dag_executor + materialize_runner 单一执行核心）。

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  queued: { label: tr('k_e5ac1d2029'), className: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  running: { label: tr('k_5942497005'), className: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  success: { label: tr('k_51991a5d11'), className: 'bg-green-500/10 text-green-500 border-green-500/20' },
  partial: { label: tr('k_2f2b7e05e3'), className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  failed: { label: tr('k_3e3c8068bb'), className: 'bg-red-500/10 text-red-500 border-red-500/20' },
  timeout: { label: tr('k_ff06c243d7'), className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  cancelled: { label: tr('k_a5ffdc95ee'), className: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

const TRIGGER_MAP: Record<string, string> = {
  cron: tr('k_16c7773fd8'),
  manual: tr('k_0cc990ba99'),
  retry: tr('k_e2d53a6d3a'),
};

function StatusBadge({ status }: { status: string | null }) {
  const conf = STATUS_MAP[status || ''] || { label: status || '—', className: 'bg-muted text-muted-foreground' };
  return <Badge variant="outline" className={conf.className}>{conf.label}</Badge>;
}

// 原始枚举不上屏：运行历史/详情展示业务名，未知值才原样兜底（便于排查新状态）
function triggerText(value?: string | null): string {
  return TRIGGER_MAP[String(value || '')] || String(value || '—');
}

function statusText(value?: string | null): string {
  return STATUS_MAP[String(value || '')]?.label || String(value || '—');
}

function KindBadge({ kind }: { kind: string }) {
  const { t } = useTranslation('ui');
  return kind === 'simple'
    ? <Badge variant="outline" className="bg-amber-500/10 text-amber-500 border-amber-500/20"><Zap className="w-3 h-3 mr-0.5" />{t("k_8d95b6396b")}</Badge>
    : <Badge variant="outline" className="bg-blue-500/10 text-blue-500 border-blue-500/20"><Workflow className="w-3 h-3 mr-0.5" />{t("k_b9cb6d92ed")}</Badge>;
}

function fmtTime(value?: string | null): string {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '—';
}

// 节点类型 → 业务名（任务详情只读展示，不向用户抛原始英文 type）
const NODE_TYPE_LABEL: Record<string, string> = {
  materialize: tr('k_979c3cfe21'),
  sql_task: tr('k_c250192752'),
  sync: tr('k_a1ff6cf80c'),
  control: tr('k_ab40a96f7c'),
};
const GRAIN_LABEL: Record<string, string> = {
  day: tr('k_990ed9f667'), month: tr('k_cbed4cfed1'), year: tr('k_868ccb3b5a'),
};
const WRITE_MODE_LABEL: Record<string, string> = {
  overwrite: tr('k_8cd94a9232'),
  partition_overwrite: tr('k_83dbc5deef'),
  append: tr('k_b64615865c'),
};

interface TaskDetailState {
  workflow: DagWorkflow;
  watermarks: Record<string, WatermarkItem>;
  lastRun: DagRun | null;
  lastNodes: DagNodeRun[];
}

function DetailField({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={mono ? 'font-mono text-xs break-all' : 'text-sm break-all'}>{value || '—'}</div>
    </div>
  );
}

function writeModeText(mode?: string): string {
  return WRITE_MODE_LABEL[String(mode || '')] || String(mode || '—');
}

// 单个节点的只读配置：按类型给业务字段（不直接 dump JSON，避免把原始内部键名当展示）
function NodeConfigView({ node }: { node: DagGraph['nodes'][number] }) {
  const c: any = node.config || {};
  const rows: Array<[string, string]> = [];
  const push = (label: string, value?: string | number | null) => {
    if (value !== undefined && value !== null && String(value) !== '') rows.push([label, String(value)]);
  };
  if (node.type === 'materialize') {
    push(tr('k_fc2b4735c3'), c.dataset);
    push(tr('k_442be9c497'), [c.target_datasource, c.target_table].filter(Boolean).join('.'));
    push(tr('k_244d6de40f'), writeModeText(c.write_mode));
    if (c.window?.grain) {
      push(tr('k_5e68f5916f'), `${GRAIN_LABEL[c.window.grain] || c.window.grain}`
        + ` · ${tr('k_de6f9f5c4d')} ${c.window.lookback_days ?? '—'}`);
    }
    if (Array.isArray(c.sites) && c.sites.length) push(tr('k_236ed6c03b'), c.sites.join(', '));
    if (c.site_args && typeof c.site_args === 'object') {
      push(tr('k_f81a43d252'), Object.entries(c.site_args)
        .map(([site, args]: any) => `${site}: ${Object.entries(args || {}).map(([k, v]) => `${k}=${v}`).join(',')}`)
        .join(' | '));
    }
    if (c.partition) {
      push(tr('k_985763f7c5'), c.partition.column);
      push(tr('k_6ee8f215b6'), c.partition.date_arg);
      push(tr('k_c27e9e5166'), c.partition.target_partition);
      if (c.partition.grain) push(tr('k_baf051c677'), GRAIN_LABEL[c.partition.grain] || c.partition.grain);
      if (c.partition.lookback_days) push(tr('k_de6f9f5c4d'), c.partition.lookback_days);
    }
    push(tr('k_761c4fe18b'), c.budget?.max_rows);
    push(tr('k_7c9af8cf96'), c.budget?.max_seconds);
    push(tr('k_4d3fa9681c'), c.batch_size);
  } else if (node.type === 'sync') {
    push(tr('k_294b663c77'), [c.source_datasource, c.source_table].filter(Boolean).join('.'));
    push(tr('k_442be9c497'), [c.target_datasource, c.target_table].filter(Boolean).join('.'));
    push(tr('k_244d6de40f'), writeModeText(c.write_mode));
    push(tr('k_6a3efb716b'), c.incremental_column);
    push(tr('k_4d3fa9681c'), c.batch_size);
  } else if (node.type === 'sql_task') {
    push(tr('k_442be9c497'), [c.target?.datasource, c.target?.table].filter(Boolean).join('.'));
    push(tr('k_244d6de40f'), writeModeText(c.target?.write_mode));
    push('UDF', (c.udf_refs || []).join(', '));
  } else {
    push(tr('k_34e47d7e3e'), c.action);
    push(tr('k_26670dda42'), c.message);
  }
  return (
    <div className="border rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        {node.name || node.key}
        <Badge variant="outline" className="text-[10px]">{NODE_TYPE_LABEL[node.type] || node.type}</Badge>
        <code className="text-[10px] text-muted-foreground">{node.key}</code>
      </div>
      {rows.length === 0 ? (
        <div className="text-xs text-muted-foreground">—</div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {rows.map(([label, value]) => <DetailField key={label} label={label} value={value} />)}
        </div>
      )}
    </div>
  );
}

function toNumber(text: string): number | undefined {
  const n = Number(text);
  return text.trim() && Number.isFinite(n) ? n : undefined;
}

// ── 物化表单（生成单节点 materialize DAG） ──
export interface MaterializeForm {
  name: string; description: string; schedule_cron: string;
  dataset: string;
  target_datasource: string; target_table: string;
  write_mode: 'overwrite' | 'append' | 'partition_overwrite';
  partition_column: string; date_arg: string; target_partition: string; lookback_days: string;
  max_rows: string; max_seconds: string; batch_size: string;
}

const EMPTY_FORM: MaterializeForm = {
  name: '', description: '', schedule_cron: '',
  dataset: '',
  target_datasource: '', target_table: '',
  write_mode: 'partition_overwrite',
  partition_column: '', date_arg: '', target_partition: '', lookback_days: '',
  max_rows: '', max_seconds: '', batch_size: '',
};

// 导出供用例锁定「单步物化 = 单节点 materialize DAG」的落库形状
export function formToGraph(f: MaterializeForm): DagGraph {
  const partition = f.write_mode === 'partition_overwrite' ? {
    column: f.partition_column.trim(),
    date_arg: f.date_arg,
    target_partition: f.target_partition.trim() || undefined,
    lookback_days: toNumber(f.lookback_days),
  } : undefined;
  return {
    nodes: [{
      key: 'materialize', name: f.name, type: 'materialize',
      config: {
        dataset: f.dataset,
        target_datasource: f.target_datasource,
        target_table: f.target_table.trim(),
        write_mode: f.write_mode,
        partition,
        budget: { max_rows: toNumber(f.max_rows), max_seconds: toNumber(f.max_seconds) },
        batch_size: toNumber(f.batch_size),
      },
    }],
    edges: [],
  };
}

export default function SyncTasks() {
  const { t } = useTranslation('ui');
  const navigate = useNavigate();
  const datasets = useMaterializableDatasets();
  const [tasks, setTasks] = useState<DagWorkflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editTask, setEditTask] = useState<DagWorkflow | null>(null);
  const [form, setForm] = useState<MaterializeForm>(EMPTY_FORM);
  const [historyTask, setHistoryTask] = useState<DagWorkflow | null>(null);
  const [runs, setRuns] = useState<DagRun[]>([]);
  // 终止运行（残留实例手动收口）：写动作，只给持写码的角色渲染按钮
  const [stopTarget, setStopTarget] = useState<DagRun | null>(null);
  const [stopping, setStopping] = useState(false);
  const canManageRuns = useHasPerm('sync:manage') || useHasPerm('dag:manage');
  // 删除二次确认 / 只读详情（含最近一次运行环境参数）
  const [deleteTask, setDeleteTask] = useState<DagWorkflow | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [detailTask, setDetailTask] = useState<DagWorkflow | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<TaskDetailState | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await dagApi.listWorkflows({ page: 1, size: 100 });
      setTasks(data.items || []);
    } catch {
      toast.error(tr('k_73bfa50d68'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openSimpleCreate = () => {
    setWizardOpen(false);
    setEditTask(null);
    setForm(EMPTY_FORM);
    setFormOpen(true);
  };

  const openEdit = (task: DagWorkflow) => {
    const node = (task.graph_json?.nodes || [])[0] || ({} as DagGraph['nodes'][number]);
    // 非单步物化（多步编排 / 历史 sync 节点）回到 DAG 编辑器里改，避免表单覆盖掉图结构
    if ((task.kind || 'dag') !== 'simple' || node.type !== 'materialize') {
      navigate(`/data/sync/dag/${task.id}`);
      return;
    }
    const c: any = node.config || {};
    setEditTask(task);
    setForm({
      name: task.name, description: task.description || '',
      schedule_cron: task.cron_expression || '',
      dataset: c.dataset || '',
      target_datasource: c.target_datasource || '', target_table: c.target_table || '',
      write_mode: c.write_mode || 'overwrite',
      partition_column: c.partition?.column || '', date_arg: c.partition?.date_arg || '',
      target_partition: c.partition?.target_partition || '',
      lookback_days: c.partition?.lookback_days ? String(c.partition.lookback_days) : '',
      max_rows: c.budget?.max_rows ? String(c.budget.max_rows) : '',
      max_seconds: c.budget?.max_seconds ? String(c.budget.max_seconds) : '',
      batch_size: c.batch_size ? String(c.batch_size) : '',
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim() || !form.dataset || !form.target_datasource || !form.target_table.trim()) {
      toast.error(tr('k_9951a796a3'));
      return;
    }
    if (form.write_mode === 'partition_overwrite' && (!form.date_arg || !form.partition_column.trim())) {
      toast.error(tr('k_6c5b0977de'));
      return;
    }
    try {
      if (editTask) {
        await dagApi.updateWorkflow(editTask.id, {
          name: form.name, description: form.description,
          graph_json: formToGraph(form),
          cron_expression: form.schedule_cron || null,
        });
        toast.success(tr('k_1b92092d63'));
      } else {
        await dagApi.createWorkflow({
          name: form.name, description: form.description, kind: 'simple',
          graph_json: formToGraph(form),
          cron_expression: form.schedule_cron || null,
        });
        toast.success(tr('k_2d4c5dcc74'));
      }
      setFormOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_6de920b4e4'));
    }
  };

  const handleRun = async (task: DagWorkflow) => {
    try {
      await dagApi.runWorkflow(task.id);
      toast.success(tr('k_e2917cf924'));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_02b526816d'));
    }
  };

  const loadHistory = async (task: DagWorkflow) => {
    try {
      const { data } = await dagApi.listWorkflowRuns(task.id, { page: 1, size: 20 });
      setRuns(data.items || []);
    } catch {
      setRuns([]);
    }
  };

  const openHistory = async (task: DagWorkflow) => {
    setHistoryTask(task);
    await loadHistory(task);
  };

  // 终止 = 服务端条件更新终态 + 同批收尾节点。残留实例（worker 已消失、
  // 执行体失联）状态不会自己变，只能靠这里手动收口，否则任务因终态保护也删不掉。
  const handleStopRun = async () => {
    if (!stopTarget || !historyTask) return;
    setStopping(true);
    try {
      const { data } = await dagApi.stopRun(stopTarget.id);
      if (data?.cancelled) toast.success(tr('k_2530fb3f66'));
      else toast.error(tr('k_4e0b7c21da'));   // 已结束的实例没有可停的东西，如实说
      setStopTarget(null);
      await loadHistory(historyTask);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_fd59df83b0'));
    } finally {
      setStopping(false);
    }
  };

  // 只读详情：配置 + 水位线（上次成功位置）+ 最近一次运行的窗口/对账
  const openDetail = async (task: DagWorkflow) => {
    setDetailTask(task);
    setDetail(null);
    setDetailLoading(true);
    try {
      const [wfRes, wmRes] = await Promise.all([
        dagApi.getWorkflow(task.id),
        dagApi.listWatermarks(task.id),
      ]);
      let lastRun: DagRun | null = null;
      let lastNodes: DagNodeRun[] = [];
      const runsRes = await dagApi.listWorkflowRuns(task.id, { page: 1, size: 1 });
      const latest = (runsRes.data.items || [])[0];
      if (latest) {
        const runRes = await dagApi.getRun(latest.id);
        lastRun = runRes.data.run;
        lastNodes = runRes.data.nodes || [];
      }
      setDetail({
        workflow: wfRes.data,
        watermarks: wmRes.data.items || {},
        lastRun,
        lastNodes,
      });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_73bfa50d68'));
    } finally {
      setDetailLoading(false);
    }
  };

  // 删除必须二次确认：后端是物理删除（工作流 + 全部运行/节点记录），不可恢复
  const handleDelete = async () => {
    if (!deleteTask) return;
    setDeleting(true);
    try {
      await dagApi.deleteWorkflow(deleteTask.id);
      toast.success(tr('k_5cc232620c'));
      setDeleteTask(null);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_acf0664a54'));
    } finally {
      setDeleting(false);
    }
  };

  // 分区模式的日期参数候选 = 所选数据集声明的参数（与执行期同源 param_summary）
  const datasetParams = datasets.find(d => d.name === form.dataset)?.params || [];

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("k_8db7df9e2e")}</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {tr('k_d8425a17b3')}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="w-4 h-4 mr-1" />{tr('k_38108eaa1d')}</Button>
          <Button size="sm" onClick={() => setWizardOpen(true)}>
            <Plus className="w-4 h-4 mr-1" />{tr('k_b255a277b3')}</Button>
        </div>
      </div>

      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">{t("k_2e30469899")}</th>
              <th className="text-left p-3 font-medium">{t("k_e4e46c7235")}</th>
              <th className="text-left p-3 font-medium">{t("k_b2047f645e")}</th>
              <th className="text-left p-3 font-medium">{t("k_36019dc2f4")}</th>
              <th className="text-left p-3 font-medium">{t("k_b0155dd075")}</th>
              <th className="text-center p-3 font-medium">{t("k_d4e9ca3dd4")}</th>
              <th className="text-right p-3 font-medium">{t("k_f3ea6d345e")}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">{tr('k_26b5bd4947')}</td></tr>
            ) : tasks.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">{t("k_0e7aa874aa")}</td></tr>
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
                  <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{task.cron_expression || tr('k_2a3e7f5c38')}</code>
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
                      toast.success(v ? tr('k_dd92bb3294') : tr('k_5502616887'));
                      load();
                    } catch (e: any) {
                      toast.error(e?.response?.data?.detail || tr('k_5fa802bef5'));
                    }
                  }} />
                </td>
                <td className="p-3">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="sm" onClick={() => handleRun(task)} title={t("k_275f997278")}><Play className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" onClick={() => openDetail(task)} title={tr('k_faea8c1db9')}><Eye className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" onClick={() => openEdit(task)} title={t("k_a7f814c0a4")}><Edit className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" onClick={() => openHistory(task)} title={t("k_57a96617b3")}><History className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" title={t("k_3755f56f2f")}
                      onClick={() => setDeleteTask(task)}>
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
          <DialogHeader><DialogTitle>{t("k_8f38747459")}</DialogTitle></DialogHeader>
          <div className="grid grid-cols-2 gap-4 py-2">
            <button className="border rounded-lg p-4 text-left hover:border-primary transition-colors"
              onClick={openSimpleCreate}>
              <Zap className="w-6 h-6 text-amber-500 mb-2" />
              <div className="font-medium">{t("k_8d95b6396b")}</div>
              <div className="text-xs text-muted-foreground mt-1">
                {tr('k_82a5bf53e3')}</div>
            </button>
            <button className="border rounded-lg p-4 text-left hover:border-primary transition-colors"
              onClick={() => { setWizardOpen(false); navigate('/data/sync/dag/new'); }}>
              <Workflow className="w-6 h-6 text-blue-500 mb-2" />
              <div className="font-medium">{t("k_b9cb6d92ed")}</div>
              <div className="text-xs text-muted-foreground mt-1">
                {tr('k_c805588d6a')}</div>
            </button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 物化任务表单 */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editTask ? tr('k_6b873a64c2') : tr('k_be04ae864d')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">{tr('k_2e30469899')}</label>
                <Input className="mt-1" value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div>
                <label className="text-sm font-medium">{tr('k_3d39d32cdc')}</label>
                <Input className="mt-1" value={form.schedule_cron}
                  onChange={e => setForm(f => ({ ...f, schedule_cron: e.target.value }))}
                  placeholder={tr('k_e9846670f8')} />
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">{tr('k_26670dda42')}</label>
              <Input className="mt-1" value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
            </div>
            <div>
              <label className="text-sm font-medium">{t("k_fc2b4735c3")}</label>
              <div className="mt-1">
                <DatasetSelect value={form.dataset} datasets={datasets}
                  onChange={v => setForm(f => ({ ...f, dataset: v, date_arg: '' }))} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">{t("k_6daeb016b3")}</label>
                <div className="mt-1">
                  <DatasourceSelect value={form.target_datasource}
                    onChange={v => setForm(f => ({ ...f, target_datasource: v }))} />
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">{t("k_442be9c497")}</label>
                <Input className="mt-1" value={form.target_table}
                  onChange={e => setForm(f => ({ ...f, target_table: e.target.value }))} />
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">{t("k_244d6de40f")}</label>
              <Select value={form.write_mode}
                onValueChange={v => setForm(f => ({ ...f, write_mode: v as MaterializeForm['write_mode'] }))}>
                <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="partition_overwrite">{t("k_83dbc5deef")}</SelectItem>
                  <SelectItem value="overwrite">{t("k_8cd94a9232")}</SelectItem>
                  <SelectItem value="append">{t("k_b64615865c")}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">{tr('k_b2c1a0514b')}</p>
            </div>

            {form.write_mode === 'partition_overwrite' && (
              <div className="border rounded-lg p-3 space-y-3">
                <div className="text-sm font-medium">{tr('k_de6418afb3')}</div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">{tr('k_6ee8f215b6')}</label>
                    <Select value={form.date_arg} onValueChange={v => setForm(f => ({ ...f, date_arg: v }))}>
                      <SelectTrigger className="mt-1"><SelectValue placeholder={tr('k_6ee8f215b6')} /></SelectTrigger>
                      <SelectContent>
                        {datasetParams.length === 0 && (
                          <SelectItem value="__none__" disabled>{tr('k_f4bf86975d')}</SelectItem>
                        )}
                        {datasetParams.map(p => (
                          <SelectItem key={p.key} value={p.key}>{p.label || p.key}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">{tr('k_a4d4228a94')}</label>
                    <Input className="mt-1" value={form.partition_column}
                      onChange={e => setForm(f => ({ ...f, partition_column: e.target.value }))} />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">{tr('k_94ec865db6')}</label>
                    <Input className="mt-1" value={form.target_partition}
                      onChange={e => setForm(f => ({ ...f, target_partition: e.target.value }))}
                      placeholder="p{yyyyMMdd}" />
                    <p className="text-xs text-muted-foreground mt-1">{tr('k_aa9be99c63')}</p>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">{tr('k_56f5690c96')}</label>
                    <Input className="mt-1" type="number" min={1} max={366} value={form.lookback_days}
                      onChange={e => setForm(f => ({ ...f, lookback_days: e.target.value }))}
                      placeholder="1" />
                  </div>
                </div>
              </div>
            )}

            <div className="border rounded-lg p-3 space-y-3">
              <div className="text-sm font-medium">{tr('k_e2462800c6')}</div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">{tr('k_b7ef6de9b3')}</label>
                  <Input className="mt-1" type="number" min={1} value={form.max_rows}
                    onChange={e => setForm(f => ({ ...f, max_rows: e.target.value }))} />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">{tr('k_10225ebef2')}</label>
                  <Input className="mt-1" type="number" min={1} value={form.max_seconds}
                    onChange={e => setForm(f => ({ ...f, max_seconds: e.target.value }))} />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">{tr('k_3d2f6e5259')}</label>
                  <Input className="mt-1" type="number" min={1} max={20000} value={form.batch_size}
                    onChange={e => setForm(f => ({ ...f, batch_size: e.target.value }))} />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">{tr('k_56634acc0a')}</p>
              <p className="text-xs text-muted-foreground">{tr('k_f90bec6ec4')}</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>{t("k_fadf24dbc5")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除二次确认（后端是物理删除：工作流 + 全部运行/节点记录） */}
      <Dialog open={!!deleteTask} onOpenChange={o => { if (!o && !deleting) setDeleteTask(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-red-500" />{tr('k_033e8891a2')}
            </DialogTitle>
            <DialogDescription>{tr('k_c1777583bb', { p0: deleteTask?.name || '' })}</DialogDescription>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{tr('k_b35a9b0c5f')}</p>
          <DialogFooter>
            <Button variant="outline" disabled={deleting} onClick={() => setDeleteTask(null)}>
              {t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" disabled={deleting} onClick={handleDelete}>
              {deleting
                ? <><Loader2 className="w-4 h-4 mr-1 animate-spin" />{tr('k_fc2799c31b')}</>
                : tr('k_3c06abe116')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 只读详情：完整配置 + 运行环境（水位线/上次窗口），不做任何写入 */}
      <Dialog open={!!detailTask} onOpenChange={o => { if (!o) { setDetailTask(null); setDetail(null); } }}>
        <DialogContent className="max-w-4xl max-h-[88vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{tr('k_4a98bf0c68')}{detailTask ? ` · ${detailTask.name}` : ''}</DialogTitle>
            <DialogDescription>{tr('k_74c0f8cc2f')}</DialogDescription>
          </DialogHeader>
          {detailLoading || !detail ? (
            <div className="p-8 text-muted-foreground flex items-center justify-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" />{tr('k_26b5bd4947')}
            </div>
          ) : (
            <div className="space-y-5">
              <section>
                <h3 className="text-sm font-semibold mb-2">{tr('k_b122f813d5')}</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <DetailField label={tr('k_2e30469899')} value={detail.workflow.name} />
                  <DetailField label={tr('k_bb763c9e33')}
                    value={detail.workflow.kind === 'simple' ? t("k_8d95b6396b") : t("k_b9cb6d92ed")} />
                  <DetailField label={tr('k_41beddee6a')} mono
                    value={detail.workflow.cron_expression || tr('k_2a3e7f5c38')} />
                  <DetailField label={tr('k_1220b05a05')} value={detail.workflow.timezone || 'Asia/Shanghai'} />
                  <DetailField label={tr('k_a4fc486137')}
                    value={detail.workflow.is_active ? tr('k_d4e9ca3dd4') : tr('k_5c56a88945')} />
                  <DetailField label={tr('k_84e3802f60')} value={fmtTime(detail.workflow.created_at)} />
                  <DetailField label={tr('k_093dea88c9')} value={fmtTime(detail.workflow.updated_at)} />
                  <DetailField label={tr('k_7f0fd6b23a')} value={String(detail.workflow.timeout_seconds ?? '—')} />
                  <DetailField label={tr('k_987baa711c')} value={String(detail.workflow.max_retries ?? 0)} />
                </div>
                {detail.workflow.description && (
                  <p className="text-xs text-muted-foreground mt-2">{detail.workflow.description}</p>
                )}
              </section>

              <section>
                <h3 className="text-sm font-semibold mb-2">{tr('k_7eddbd9247')}</h3>
                <div className="space-y-2">
                  {(detail.workflow.graph_json?.nodes || []).map(node => {
                    const deps = (detail.workflow.graph_json?.edges || [])
                      .filter(e => e.to === node.key).map(e => e.from);
                    return (
                      <div key={node.key}>
                        <NodeConfigView node={node} />
                        <p className="text-[11px] text-muted-foreground mt-1 pl-1">
                          {tr('k_6860b943a9')}：{deps.length ? deps.join(', ') : tr('k_57a1d17493')}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </section>

              <section>
                <h3 className="text-sm font-semibold mb-2">{tr('k_74946aa207')}</h3>
                {/* 水位线单独点名：它就是“下次从哪继续”的那条事实，混在运行环境里没人看得出 */}
                <p className="text-xs font-medium text-muted-foreground mb-1">{tr('k_75569290ee')}</p>
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="text-left p-2 font-medium">{tr('k_e840cd6f1e')}</th>
                        <th className="text-left p-2 font-medium">{tr('k_62479c48c9')}</th>
                        <th className="text-left p-2 font-medium">{tr('k_2b8df296cc')}</th>
                        <th className="text-left p-2 font-medium">{tr('k_093dea88c9')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.keys(detail.watermarks).length === 0 ? (
                        <tr><td colSpan={4} className="p-3 text-center text-xs text-muted-foreground">
                          {tr('k_56b8fcde0f')}</td></tr>
                      ) : Object.entries(detail.watermarks).map(([key, wm]) => (
                        <tr key={key} className="border-t">
                          <td className="p-2 font-mono text-xs">{key}</td>
                          <td className="p-2 font-mono text-xs">{wm.position || '—'}</td>
                          <td className="p-2 text-xs">{wm.last_rows ?? 0}</td>
                          <td className="p-2 text-xs">{fmtTime(wm.updated_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section>
                <h3 className="text-sm font-semibold mb-2">{tr('k_416bcab8a0')}</h3>
                {!detail.lastRun ? (
                  <p className="text-xs text-muted-foreground">{tr('k_920daa51ce')}</p>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                      <DetailField label={tr('k_f67f1852d8')} value={triggerText(detail.lastRun.trigger_type)} />
                      <DetailField label={tr('k_a4fc486137')} value={statusText(detail.lastRun.status)} />
                      <DetailField label={tr('k_79cc871ca9')} value={fmtTime(detail.lastRun.started_at)} />
                      <DetailField label={tr('k_76b9880829')} value={fmtTime(detail.lastRun.finished_at)} />
                      <DetailField label={tr('k_9bbb81216e')} value={(() => {
                        const p = detail.lastRun.params_json || {};
                        const parts = [
                          p.start_date ? `${p.start_date} ~ ${p.end_date || ''}` : '',
                          p.grains?.length ? `${tr('k_baf051c677')}: ${p.grains.join(',')}` : '',
                        ].filter(Boolean);
                        return parts.join(' · ') || '—';
                      })()} />
                    </div>
                    {detail.lastNodes.some(n => n.result_json?.window) && (
                      <div className="border rounded-lg overflow-hidden">
                        <table className="w-full text-sm">
                          <thead className="bg-muted/50">
                            <tr>
                              <th className="text-left p-2 font-medium">{tr('k_e840cd6f1e')}</th>
                              <th className="text-left p-2 font-medium">{tr('k_1f556e28ab')}</th>
                              <th className="text-left p-2 font-medium">{tr('k_44b75fa528')}</th>
                              <th className="text-left p-2 font-medium">{tr('k_eddd9ee1db')}</th>
                              <th className="text-left p-2 font-medium">{tr('k_66914536fa')}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {detail.lastNodes.filter(n => n.result_json?.window).map(n => {
                              const w = n.result_json?.window || {};
                              return (
                                <tr key={n.id} className="border-t">
                                  <td className="p-2 font-mono text-xs">{n.node_key}</td>
                                  <td className="p-2 text-xs">
                                    {w.start ? `${w.start} ~ ${w.end || ''}` : (w.reason || w.mode || '—')}
                                    {w.cells ? ` · ${tr('k_4ccc3804a2')} ${w.cells}` : ''}
                                  </td>
                                  <td className="p-2 text-xs">{n.rows_written ?? 0}</td>
                                  <td className="p-2 text-xs">
                                    {n.result_json?.reconciled === undefined ? '—'
                                      : (n.result_json.reconciled ? tr('k_21901b1658') : tr('k_a6f60d7c84'))}
                                  </td>
                                  <td className="p-2 text-xs">{n.result_json?.strategy || '—'}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </section>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setDetailTask(null); setDetail(null); }}>
              {tr('k_6c14bd7f6f')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 运行历史 */}
      <Dialog open={!!historyTask} onOpenChange={o => { if (!o) setHistoryTask(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{tr('k_3cfa588355')}{' '}{historyTask?.name}</DialogTitle></DialogHeader>
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left p-2 font-medium">{t("k_27eccf9785")}</th>
                <th className="text-left p-2 font-medium">{t("k_62e951a692")}</th>
                <th className="text-left p-2 font-medium">{t("k_79cc871ca9")}</th>
                <th className="text-left p-2 font-medium">{t("k_76b9880829")}</th>
                <th className="text-right p-2 font-medium">{t("k_f3ea6d345e")}</th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 ? (
                <tr><td colSpan={5} className="p-6 text-center text-muted-foreground">{t("k_920daa51ce")}</td></tr>
              ) : runs.map(run => (
                <tr key={run.id} className="border-t">
                  <td className="p-2">{triggerText(run.trigger_type)}</td>
                  <td className="p-2">
                    {/* 未完成且超过租约/排队阈值：提示执行体可能不在了，可手动终止 */}
                    <span title={run.is_stale ? tr('k_3d81f9ac47') : undefined}
                      className={run.is_stale ? 'inline-flex rounded-md ring-1 ring-amber-500/60' : undefined}>
                      <StatusBadge status={run.status} />
                    </span>
                  </td>
                  <td className="p-2 text-xs">{fmtTime(run.started_at)}</td>
                  <td className="p-2 text-xs">{fmtTime(run.finished_at)}</td>
                  <td className="p-2 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {canManageRuns && (run.status === 'queued' || run.status === 'running') && (
                        <Button variant="ghost" size="sm" className="text-amber-600"
                          title={tr('k_578e6e096e')}
                          aria-label={tr('k_578e6e096e')}
                          onClick={() => setStopTarget(run)}>
                          <StopCircle className="w-4 h-4" />
                        </Button>
                      )}
                      <Button variant="ghost" size="sm"
                        onClick={() => navigate(`/data/sync/dag-runs/${run.id}`, { state: { from: '/data/sync' } })}>
                        {tr('k_4f55ee1e68')}{' '}<ArrowRight className="w-3 h-3 ml-1" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DialogContent>
      </Dialog>

      {/* 终止运行：残留实例只能手动收口（worker 已消失时状态不会自己变） */}
      <Dialog open={!!stopTarget} onOpenChange={o => { if (!o) setStopTarget(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{tr('k_578e6e096e')}</DialogTitle>
            <DialogDescription>{tr('k_9f2c1a7bd3')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStopTarget(null)} disabled={stopping}>
              {tr('k_6c14bd7f6f')}</Button>
            <Button variant="destructive" onClick={handleStopRun} disabled={stopping}>
              {stopping && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}
              {tr('k_095e938e2a')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
