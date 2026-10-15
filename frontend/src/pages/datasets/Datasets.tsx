import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Plus, Layers, Code2, Trash2, Eye, Loader2, Check,
  RefreshCw, BarChart3,
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
  sql: 'SQL',
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
            BI 治理建模层：执行 SQL 取数，看板、报表与 Chat 共用同一口径
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
          placeholder="搜索数据集名称 / 描述"
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
                  <Code2 className="h-3 w-3" />
                  {SOURCE_LABELS[ds.source_type] || ds.source_type || 'SQL'}
                </Badge>
              </div>
              <div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground flex-wrap">
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

// ── Create Dialog：执行 SQL + 图表预设 ──────────────────────────────────────

function CreateDatasetDialog({
  open, onClose, onCreated,
}: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  const [form, setForm] = useState({
    name: '', description: '', datasource_id: 0, sql_query: '',
    chart_type: '',
  });
  const [preset, setPreset] = useState({ xCol: '', yCol: '', groupCol: '' });
  const [datasources, setDatasources] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [inferredFields, setInferredFields] = useState<any[]>([]);

  useEffect(() => {
    if (!open) return;
    client.get('/datasources/').then(({ data }) => {
      setDatasources(Array.isArray(data) ? data : (data?.items || []));
    }).catch(() => setDatasources([]));
  }, [open]);

  // 图表预设可选列: 按 SQL 结果列
  const presetColumns = inferredFields.length
    ? inferredFields.map((f: any) => f.field)
    : [];

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
    if (!form.sql_query.trim()) {
      toast.error('请输入执行 SQL'); return;
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
        datasource_id: Number(form.datasource_id) || 0,
        sql_query: form.sql_query,
        chart_type: form.chart_type,
        chart_preset: chartPreset,
        field_config: inferredFields,
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
            执行 SQL 负责取数，经统一治理入口；看板、报表与 Chat 共用同一口径
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
                onValueChange={v => setForm({ ...form, datasource_id: Number(v) })}
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

          {/* ── 执行 SQL ── */}
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <Code2 className="h-4 w-4" /> 执行 SQL
                <Badge variant="outline" className="text-[10px]">仅 SELECT/WITH</Badge>
              </div>
              <Button size="sm" variant="outline" onClick={validateSql} disabled={validating}>
                {validating ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Check className="h-3.5 w-3.5 mr-1" />}
                校验并推断字段
              </Button>
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
