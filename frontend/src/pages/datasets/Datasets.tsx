import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Plus, Layers, Database, Code2, Trash2, Eye, Boxes, Loader2, Check,
  RefreshCw, Wand2, BarChart3,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import client from '../../api/client';
import { hasPerm } from '../../stores/permissionStore';
import { CHART_TYPES } from '../../components/DashboardChart';

interface Dataset {
  id: number;
  name: string;
  description: string;
  source_type: 'semantic' | 'sql' | 'both';
  object_key: string;
  datasource_id: number;
  chart_type: string;
  owner_id: number;
  field_count: number;
  references: number;
  updated_at: string;
}

const SOURCE_LABELS: Record<string, string> = {
  semantic: '语义对象', sql: 'SQL', both: '语义+SQL',
};
const CHART_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  CHART_TYPES.map(t => [t.value, t.label]),
);
// 图表预设只支持真实图表(排除参数控件)
const PRESET_CHART_TYPES = CHART_TYPES.filter(t => t.category !== 'widget');

export default function Datasets() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<any>(null);
  const canManage = hasPerm('dataset:manage');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/datasets/', { params: { keyword } });
      setItems(data.items || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [keyword]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (ds: Dataset) => {
    if (!confirm(`确定删除数据集 "${ds.name}"？`)) return;
    try {
      await client.delete(`/datasets/${ds.id}`);
      toast.success('已删除');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  // 手动同步数据集到业务本体(走 generate-business-assets 的 save_draft/activate 级联)
  const handleSync = async () => {
    setSyncing(true);
    try {
      const { data } = await client.post('/catalog/ontology/generate-business-assets');
      setSyncResult(data);
      (data.notes || []).forEach((n: string) => toast.warning(n)); // 告警显式暴露, 不静默
      toast.success(`业务本体已同步：${data.object_count ?? 0} 个对象`);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '同步业务本体失败');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="h-full overflow-auto p-1">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Layers className="h-6 w-6" /> 数据集
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            BI 治理建模层：语义对象 × 执行 SQL 双定义互转，看板、报表与 Chat 共用同一口径
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canManage && (
            <Button size="sm" variant="outline" onClick={handleSync} disabled={syncing}>
              {syncing
                ? <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                : <RefreshCw className="h-4 w-4 mr-1" />}
              同步到业务本体
            </Button>
          )}
          {canManage && (
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4 mr-1" /> 新建数据集
            </Button>
          )}
        </div>
      </div>

      <div className="flex gap-2 mb-4">
        <Input
          placeholder="搜索数据集名称 / 描述 / 对象…"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          className="max-w-xs"
        />
      </div>

      {loading ? (
        <div className="flex justify-center py-12 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground border rounded-lg">
          暂无数据集{canManage ? '，点击右上角新建' : ''}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {items.map(ds => (
            <div key={ds.id} className="border rounded-lg p-4 hover:bg-muted/30 transition-colors">
              <div className="flex items-start justify-between gap-2">
                <button className="text-left min-w-0" onClick={() => navigate(`/data/datasets/${ds.id}`)}>
                  <div className="font-medium truncate">{ds.name}</div>
                  {ds.description && (
                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{ds.description}</p>
                  )}
                </button>
                <Badge variant="outline" className="shrink-0 flex items-center gap-1 text-[10px]">
                  {ds.source_type === 'semantic' ? <Boxes className="h-3 w-3" /> : <Code2 className="h-3 w-3" />}
                  {SOURCE_LABELS[ds.source_type] || ds.source_type}
                </Badge>
              </div>
              <div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground flex-wrap">
                {ds.object_key && (
                  <span className="flex items-center gap-1"><Database className="h-3 w-3" />{ds.object_key}</span>
                )}
                <span>{ds.field_count} 字段</span>
                <span>被 {ds.references} 图表引用</span>
                {ds.chart_type && (
                  <Badge variant="secondary" className="text-[10px] flex items-center gap-1">
                    <BarChart3 className="h-3 w-3" />
                    {CHART_TYPE_LABELS[ds.chart_type] || ds.chart_type}
                  </Badge>
                )}
              </div>
              <div className="flex justify-end gap-1 mt-2">
                <Button variant="ghost" size="sm" onClick={() => navigate(`/data/datasets/${ds.id}`)}>
                  <Eye className="h-3.5 w-3.5" />
                </Button>
                {canManage && (
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(ds)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <CreateDatasetDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => { setCreateOpen(false); navigate(`/data/datasets/${id}`); }}
      />

      {/* 同步结果弹窗: object_count 与 notes 显式展示 */}
      <Dialog open={!!syncResult} onOpenChange={() => setSyncResult(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>业务本体同步结果</DialogTitle>
            <DialogDescription>
              数据集已作为业务本体路由节点一并生成（save_draft/activate 级联）
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            <div>生成对象数：<b>{syncResult?.object_count ?? 0}</b></div>
            {(syncResult?.notes || []).length > 0 && (
              <div className="border rounded-md p-2.5 bg-amber-50 dark:bg-amber-950/30 space-y-1">
                <div className="text-xs font-medium text-amber-700 dark:text-amber-400">告警（治理数据未对齐项）</div>
                {(syncResult.notes || []).map((n: string, i: number) => (
                  <div key={i} className="text-xs text-amber-700 dark:text-amber-400">· {n}</div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => setSyncResult(null)}>知道了</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Create Dialog：语义对象 × 执行 SQL 组合表单 + 图表预设 ──────────────

function ChipToggle({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2 py-0.5 rounded-full border text-[11px] transition-colors ${
        active
          ? 'bg-primary text-primary-foreground border-primary'
          : 'bg-background text-muted-foreground hover:bg-muted'
      }`}
    >
      {children}
    </button>
  );
}

function CreateDatasetDialog({
  open, onClose, onCreated,
}: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  const [form, setForm] = useState({
    name: '', description: '', object_key: '', datasource_id: 0, sql_query: '',
    chart_type: '',
  });
  const [preset, setPreset] = useState({ xCol: '', yCol: '', groupCol: '' });
  const [datasources, setDatasources] = useState<any[]>([]);
  const [objects, setObjects] = useState<any[]>([]);
  const [modelId, setModelId] = useState('');
  const [semFields, setSemFields] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [compiling, setCompiling] = useState(false);
  const [inferredFields, setInferredFields] = useState<any[]>([]);
  const [compileDims, setCompileDims] = useState<string[]>([]);
  const [compileMeasures, setCompileMeasures] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;
    client.get('/datasources/').then(({ data }) => {
      setDatasources(Array.isArray(data) ? data : (data?.items || []));
    }).catch(() => setDatasources([]));
  }, [open]);

  // 选数据源 → 自动取其 active 本体模型的 objects 供选择
  useEffect(() => {
    if (!form.datasource_id) { setObjects([]); setModelId(''); return; }
    client.get('/catalog/ontology/models', { params: { datasource_id: form.datasource_id } })
      .then(({ data }) => {
        const ms = (data.items || []).filter((m: any) => m.status === 'active');
        setModelId(ms.length ? String(ms[0].id) : '');
      })
      .catch(() => { setObjects([]); setModelId(''); });
  }, [form.datasource_id]);

  useEffect(() => {
    if (!modelId) { setObjects([]); return; }
    client.get(`/catalog/ontology/models/${modelId}`)
      .then(({ data }) => {
        try {
          const doc = JSON.parse(data.json_content || '{}');
          setObjects((doc.objects || []).map((o: any) => ({
            key: o.key, display_name: o.display_name || o.key,
          })));
        } catch { setObjects([]); }
      })
      .catch(() => setObjects([]));
  }, [modelId]);

  // 选定本体对象 → 取语义字段(供编译勾选与图表预设列选择)
  useEffect(() => {
    setSemFields([]);
    setCompileDims([]); setCompileMeasures([]);
    if (!form.object_key) return;
    client.get('/datasets/semantic-fields', {
      params: { datasource_id: form.datasource_id, object_key: form.object_key },
    }).then(({ data }) => setSemFields(data.fields || []))
      .catch(() => setSemFields([]));
  }, [form.object_key, form.datasource_id]);

  // 图表预设可选列: 有执行 SQL 时按结果列(SQL 输出列), 仅语义时按语义字段
  const presetColumns = inferredFields.length
    ? inferredFields.map((f: any) => f.field)
    : semFields.map((f: any) => f.field);

  // 由语义对象编译生成执行 SQL(dataset:manage 把关, SQL 仅回显给数据集编辑者)
  const compileSql = async () => {
    if (!form.object_key) { toast.error('请先选择本体对象'); return; }
    if (!form.datasource_id) { toast.error('请先选择数据源'); return; }
    if (compileDims.length + compileMeasures.length === 0) {
      toast.error('请勾选参与查询的维度/度量'); return;
    }
    setCompiling(true);
    try {
      const { data } = await client.post('/datasets/compile-from-semantic', {
        object_key: form.object_key,
        datasource_id: form.datasource_id,
        dimensions: compileDims,
        measures: compileMeasures,
        limit: 200,
      });
      setForm(f => ({ ...f, sql_query: data.sql }));
      setInferredFields([]);
      (data.warnings || []).forEach((w: string) => toast.warning(w));
      toast.success('已生成执行 SQL（可继续手改，保存前请校验）');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '编译 SQL 失败');
    } finally {
      setCompiling(false);
    }
  };

  const validateSql = async () => {
    if (!form.sql_query.trim()) { toast.error('请先输入 SQL'); return; }
    setValidating(true);
    try {
      const { data } = await client.post('/datasets/validate-sql', {
        sql: form.sql_query, datasource_id: Number(form.datasource_id) || 0,
      });
      setInferredFields(data.fields || []);
      toast.success(`校验通过，推断 ${data.fields?.length || 0} 个字段`);
    } catch (e: any) {
      setInferredFields([]);
      toast.error(e.response?.data?.detail || 'SQL 校验失败');
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error('名称必填'); return; }
    if (!form.object_key && !form.sql_query.trim()) {
      toast.error('至少绑定本体对象或提供执行 SQL（两者可并存）'); return;
    }
    if (form.sql_query.trim() && inferredFields.length === 0) {
      toast.error('请先「校验并推断字段」'); return;
    }
    setSaving(true);
    try {
      const chartPreset: Record<string, any> = {};
      if (preset.xCol) chartPreset.xCol = preset.xCol;
      if (preset.yCol) chartPreset.yCol = preset.yCol;
      if (preset.groupCol) chartPreset.groupCol = preset.groupCol;
      const { data } = await client.post('/datasets/', {
        name: form.name.trim(),
        description: form.description,
        object_key: form.object_key,
        datasource_id: Number(form.datasource_id) || 0,
        sql_query: form.sql_query,
        chart_type: form.chart_type,
        chart_preset: chartPreset,
        // 双定义时字段映射在详情页「由 SQL 对齐语义字段」生成, 创建时仅纯 SQL 源落 field_config
        field_config: !form.object_key ? inferredFields : undefined,
      });
      toast.success('数据集已创建');
      onCreated(data.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '创建失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>新建数据集</DialogTitle>
          <DialogDescription>
            语义对象负责检索与业务口径，执行 SQL 负责取数，两者可并存互转；取数经统一治理入口
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>名称 *</Label>
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="如：案件分析数据集" />
            </div>
            <div className="space-y-1.5">
              <Label>数据源</Label>
              <Select
                value={form.datasource_id ? String(form.datasource_id) : '0'}
                onValueChange={v => setForm({ ...form, datasource_id: Number(v), object_key: '' })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">默认引擎</SelectItem>
                  {datasources.map((d: any) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Textarea rows={2} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
          </div>

          {/* ── 语义绑定(可选) ── */}
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-sm font-medium">
              <Boxes className="h-4 w-4" /> 语义对象绑定
              <Badge variant="outline" className="text-[10px]">可选 · 与执行 SQL 并存</Badge>
            </div>
            <Select
              value={form.object_key}
              onValueChange={v => setForm({ ...form, object_key: v })}
              disabled={!form.datasource_id || objects.length === 0}
            >
              <SelectTrigger><SelectValue placeholder={objects.length ? '选择本体对象' : '该数据源暂无 active 本体模型'} /></SelectTrigger>
              <SelectContent>
                {objects.map(o => <SelectItem key={o.key} value={o.key}>{o.display_name} ({o.key})</SelectItem>)}
              </SelectContent>
            </Select>
            {form.object_key && (
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">
                  语义字段（点选参与「由语义生成 SQL」的维度/度量）：
                </p>
                {semFields.length > 0 ? (
                  <>
                    <div className="flex flex-wrap gap-1.5">
                      {semFields.filter(f => f.role === 'dimension').map((f: any) => (
                        <ChipToggle
                          key={f.field}
                          active={compileDims.includes(f.field)}
                          onClick={() => setCompileDims(prev => prev.includes(f.field)
                            ? prev.filter(x => x !== f.field) : [...prev, f.field])}
                        >
                          {f.label || f.field}
                        </ChipToggle>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {semFields.filter(f => f.role === 'measure').map((f: any) => (
                        <ChipToggle
                          key={f.field}
                          active={compileMeasures.includes(f.field)}
                          onClick={() => setCompileMeasures(prev => prev.includes(f.field)
                            ? prev.filter(x => x !== f.field) : [...prev, f.field])}
                        >
                          {f.label || f.field}
                        </ChipToggle>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">该对象暂无语义字段（指标/维度）</p>
                )}
              </div>
            )}
          </div>

          {/* ── 执行 SQL(可选) ── */}
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <Code2 className="h-4 w-4" /> 执行 SQL
                <Badge variant="outline" className="text-[10px]">可选 · 仅 SELECT/WITH</Badge>
              </div>
              <div className="flex gap-1.5">
                {form.object_key && (
                  <Button size="sm" variant="outline" onClick={compileSql} disabled={compiling}>
                    {compiling
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                      : <Wand2 className="h-3.5 w-3.5 mr-1" />}
                    由语义对象生成 SQL
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={validateSql} disabled={validating}>
                  {validating ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Check className="h-3.5 w-3.5 mr-1" />}
                  校验并推断字段
                </Button>
              </div>
            </div>
            <Textarea
              rows={6} className="font-mono text-xs"
              value={form.sql_query}
              onChange={e => { setForm({ ...form, sql_query: e.target.value }); setInferredFields([]); }}
              placeholder="SELECT region, status, COUNT(*) AS cnt FROM t_case_records GROUP BY region, status"
            />
            {inferredFields.length > 0 && (
              <div className="border rounded-lg p-2.5 text-xs space-y-1">
                <div className="font-medium mb-1">推断字段（详情页可调整角色/别名）</div>
                <div className="flex flex-wrap gap-1.5">
                  {inferredFields.map((f: any) => (
                    <Badge key={f.field} variant="outline" className="text-[10px]">
                      {f.field} · {f.role === 'measure' ? '度量' : '维度'}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            {form.object_key && (
              <p className="text-xs text-muted-foreground">
                双定义数据集以执行 SQL 为实际取数通道；字段映射请在详情页「由 SQL 对齐语义字段」确认
              </p>
            )}
          </div>

          {/* ── 图表预设(可选) ── */}
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-sm font-medium">
              <BarChart3 className="h-4 w-4" /> 图表预设
              <Badge variant="outline" className="text-[10px]">可选 · 引入看板时一键生成</Badge>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>图表类型</Label>
                <Select value={form.chart_type} onValueChange={v => setForm({ ...form, chart_type: v })}>
                  <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
                  <SelectContent>
                    {PRESET_CHART_TYPES.map(t => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>X 轴列</Label>
                <Select value={preset.xCol} onValueChange={v => setPreset({ ...preset, xCol: v })}>
                  <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Y 轴列</Label>
                <Select value={preset.yCol} onValueChange={v => setPreset({ ...preset, yCol: v })}>
                  <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>分组列</Label>
                <Select value={preset.groupCol} onValueChange={v => setPreset({ ...preset, groupCol: v })}>
                  <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              预设列引用查询结果列名；看板编辑器「从数据集引入」时直接搬入图表配置
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? '创建中…' : '创建'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
