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
import { toast } from '@/lib/toast';
import {
  Plus, Play, History, Edit, Trash2, RefreshCw, Workflow, Zap,
  Table2, ArrowRight,
} from 'lucide-react';
import { dagApi, type DagWorkflow, type DagRun, type DagGraph } from '@/api/dag';
import { udfApi, type Udf } from '@/api/udf';
import { DatasourceSelect } from './DatasourceSelect';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

// ── 任务中心：简单同步（单节点 DAG）与 DAG 编排统一列表 ──
// 模型统一：简单同步保存为 kind='simple' 的单节点 DAG，执行/调度/血缘/质量同构。

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  queued: { label: tr('k_e5ac1d2029'), className: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  running: { label: tr('k_5942497005'), className: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  success: { label: tr('k_51991a5d11'), className: 'bg-green-500/10 text-green-500 border-green-500/20' },
  partial: { label: tr('k_2f2b7e05e3'), className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  failed: { label: tr('k_3e3c8068bb'), className: 'bg-red-500/10 text-red-500 border-red-500/20' },
  timeout: { label: tr('k_ff06c243d7'), className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  cancelled: { label: tr('k_a5ffdc95ee'), className: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

function StatusBadge({ status }: { status: string | null }) {
  const conf = STATUS_MAP[status || ''] || { label: status || '—', className: 'bg-muted text-muted-foreground' };
  return <Badge variant="outline" className={conf.className}>{conf.label}</Badge>;
}

function KindBadge({ kind }: { kind: string }) {
  const { t } = useTranslation('ui');
  return kind === 'simple'
    ? <Badge variant="outline" className="bg-amber-500/10 text-amber-500 border-amber-500/20"><Zap className="w-3 h-3 mr-0.5" />{t("k_cb247d8135")}</Badge>
    : <Badge variant="outline" className="bg-blue-500/10 text-blue-500 border-blue-500/20"><Workflow className="w-3 h-3 mr-0.5" />{t("k_cb8daea8b9")}</Badge>;
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
  const { t } = useTranslation('ui');
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
      toast.error(tr('k_73bfa50d68'));
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
      toast.error(tr('k_d30a0de29d'));
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
          <h1 className="text-2xl font-bold">{t("k_a1ff6cf80c")}</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {tr('k_75523bc5ce')}</p>
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
                    <Button variant="ghost" size="sm" onClick={() => openEdit(task)} title={t("k_a7f814c0a4")}><Edit className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" onClick={() => openHistory(task)} title={t("k_57a96617b3")}><History className="w-4 h-4" /></Button>
                    <Button variant="ghost" size="sm" title={t("k_3755f56f2f")}
                      onClick={async () => {
                        try {
                          await dagApi.deleteWorkflow(task.id);
                          toast.success(tr('k_5cc232620c'));
                          load();
                        } catch (e: any) {
                          toast.error(e?.response?.data?.detail || tr('k_acf0664a54'));
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
          <DialogHeader><DialogTitle>{t("k_8f38747459")}</DialogTitle></DialogHeader>
          <div className="grid grid-cols-2 gap-4 py-2">
            <button className="border rounded-lg p-4 text-left hover:border-primary transition-colors"
              onClick={openSimpleCreate}>
              <Zap className="w-6 h-6 text-amber-500 mb-2" />
              <div className="font-medium">{t("k_cb247d8135")}</div>
              <div className="text-xs text-muted-foreground mt-1">
                {tr('k_19f108c00c')}</div>
            </button>
            <button className="border rounded-lg p-4 text-left hover:border-primary transition-colors"
              onClick={() => { setWizardOpen(false); navigate('/data/sync/dag/new'); }}>
              <Workflow className="w-6 h-6 text-blue-500 mb-2" />
              <div className="font-medium">{t("k_cb8daea8b9")}</div>
              <div className="text-xs text-muted-foreground mt-1">
                {tr('k_9f217071e4')}</div>
            </button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 简单同步表单 */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editTask ? tr('k_06e98f23b9') : tr('k_26eb3e90df')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">{tr('k_670e563ffb')}</label>
                <Input className="mt-1" value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div>
                <label className="text-sm font-medium">{t("k_0ffff13ec8")}</label>
                <Input className="mt-1" value={form.schedule_cron}
                  onChange={e => setForm(f => ({ ...f, schedule_cron: e.target.value }))}
                  placeholder={tr('k_e9846670f8')} />
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">{t("k_412f54dc38")}</label>
              <Input className="mt-1" value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">{tr('k_6abfc28824')}</label>
                <div className="mt-1">
                  <DatasourceSelect value={form.source_datasource}
                    onChange={v => setForm(f => ({ ...f, source_datasource: v }))} />
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">{tr('k_9bd9553623')}</label>
                <Input className="mt-1" value={form.source_table}
                  onChange={e => setForm(f => ({ ...f, source_table: e.target.value }))} />
              </div>
              <div>
                <label className="text-sm font-medium">{tr('k_ca1e476393')}</label>
                <div className="mt-1">
                  <DatasourceSelect value={form.target_datasource}
                    onChange={v => setForm(f => ({ ...f, target_datasource: v }))} />
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">{tr('k_c7c8c8f4b0')}</label>
                <Input className="mt-1" value={form.target_table}
                  onChange={e => setForm(f => ({ ...f, target_table: e.target.value }))} />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="text-sm font-medium">{t("k_c15748bd6f")}</label>
                <Select value={form.sync_mode} onValueChange={v => setForm(f => ({ ...f, sync_mode: v }))}>
                  <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="full">{t("k_07a5db8b5e")}</SelectItem>
                    <SelectItem value="incremental">{t("k_6078bac61a")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium">{t("k_244d6de40f")}</label>
                <Select value={form.write_mode} onValueChange={v => setForm(f => ({ ...f, write_mode: v }))}>
                  <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="append">{t("k_aec344de80")}</SelectItem>
                    <SelectItem value="overwrite">{t("k_a26056298a")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.sync_mode === 'incremental' && (
                <div>
                  <label className="text-sm font-medium">{tr('k_9c885540a5')}</label>
                  <Input className="mt-1" value={form.incremental_column}
                    onChange={e => setForm(f => ({ ...f, incremental_column: e.target.value }))} />
                </div>
              )}
            </div>
            <div>
              <label className="text-sm font-medium">{t("k_384b7b8aec")}</label>
              <Textarea className="mt-1 font-mono min-h-[90px]" value={form.transform_sql}
                onChange={e => setForm(f => ({ ...f, transform_sql: e.target.value }))}
                placeholder={'SELECT id, name_mask(name) AS name, ip2geo(ip_addr) AS region FROM {{source}}'} />
              <p className="text-xs text-muted-foreground mt-1">
                {tr('k_a24f1735c0')}
              </p>
            </div>
            <div>
              <label className="text-sm font-medium">{t("k_7714352a81")}</label>
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
                {udfs.length === 0 && <span className="text-xs text-muted-foreground">{t("k_0c90377902")}</span>}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>{t("k_fadf24dbc5")}</Button>
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
                  <td className="p-2">{run.trigger_type}</td>
                  <td className="p-2"><StatusBadge status={run.status} /></td>
                  <td className="p-2 text-xs">{fmtTime(run.started_at)}</td>
                  <td className="p-2 text-xs">{fmtTime(run.finished_at)}</td>
                  <td className="p-2 text-right">
                    <Button variant="ghost" size="sm"
                      onClick={() => navigate(`/data/sync/dag-runs/${run.id}`)}>
                      {tr('k_4f55ee1e68')}{' '}<ArrowRight className="w-3 h-3 ml-1" />
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
