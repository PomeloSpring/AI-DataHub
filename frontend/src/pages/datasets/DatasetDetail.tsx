import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  ArrowLeft, Boxes, Code2, Play, Save, Plus, Trash2, Loader2, Users, Shield,
  Wand2, AlignLeft, BarChart3,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import client from '../../api/client';
import { hasPerm } from '../../stores/permissionStore';
import DashboardChart, { CHART_TYPES } from '../../components/DashboardChart';

interface DatasetField {
  field: string;
  label?: string;
  role: 'dimension' | 'measure';
  is_time?: boolean;
  aliases?: string[];
  enum?: Record<string, string>;
  unit?: string;
  calculation?: string;
  description?: string;
  // 双定义合并标记: semantic=语义字典(随本体演进, 只读) | config=field_config(可编辑)
  from?: 'semantic' | 'config';
  sql_column?: string;
  maps_to?: string;
  unmapped?: boolean;
}

interface Scope {
  id?: number;
  subject_type: 'user' | 'role';
  subject_id: number;
  filters: Array<{ field: string; op: string; value: any }>;
}

const OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'like'];
const SOURCE_LABELS: Record<string, string> = {
  semantic: '语义对象', sql: 'SQL', both: '语义+SQL',
};
const PRESET_CHART_TYPES = CHART_TYPES.filter(t => t.category !== 'widget');
const CHART_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  CHART_TYPES.map(t => [t.value, t.label]),
);

/** update/create 接口返回原始行, chart_preset 是 JSON 字符串 → 归一为对象 */
function normalizeDataset(data: any): any {
  let preset = data?.chart_preset;
  if (typeof preset === 'string') {
    try { preset = JSON.parse(preset || '{}'); } catch { preset = {}; }
  }
  return { ...(data || {}), chart_preset: preset || {} };
}

/** chart_preset 只落 xCol/yCol/groupCol/limit 四键(与看板图表 config 对齐) */
function cleanPreset(preset: any): Record<string, any> {
  const p = preset || {};
  const out: Record<string, any> = {};
  if (p.xCol) out.xCol = p.xCol;
  if (p.yCol) out.yCol = p.yCol;
  if (p.groupCol) out.groupCol = p.groupCol;
  if (p.limit) out.limit = Number(p.limit) || 0;
  return out;
}

export default function DatasetDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const datasetId = Number(id);
  const [ds, setDs] = useState<any>(null);
  const [fields, setFields] = useState<DatasetField[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const canManage = hasPerm('dataset:manage');

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const { data } = await client.get(`/datasets/${datasetId}`);
      setDs(normalizeDataset(data));
      setFields((data.fields || []).map((f: any) => ({ ...f })));
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '加载数据集失败');
      navigate('/data/datasets');
    } finally {
      setLoading(false);
    }
  }, [datasetId, navigate]);

  useEffect(() => { load(); }, [load]);

  // 统一保存: 基本信息/查询定义/图表预设一次落库(不再分块各自保存)
  const saveConfig = async () => {
    setSaving(true);
    try {
      const { data } = await client.put(`/datasets/${datasetId}`, {
        description: ds.description || '',
        object_key: ds.object_key || '',
        sql_query: ds.sql_query || '',
        datasource_id: Number(ds.datasource_id) || 0,
        chart_type: ds.chart_type || '',
        chart_preset: cleanPreset(ds.chart_preset),
      });
      setDs(normalizeDataset(data));
      load(true);
      toast.success('配置已保存（基本信息 / 查询定义 / 图表预设）');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-16 text-muted-foreground"><Loader2 className="h-6 w-6 animate-spin" /></div>;
  }
  if (!ds) return null;

  return (
    <div className="h-full overflow-auto p-1">
      <div className="flex items-center gap-3 mb-1">
        <Button variant="ghost" size="sm" onClick={() => navigate('/data/datasets')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="text-2xl font-bold flex items-center gap-2">{ds.name}</h1>
        <Badge variant="outline" className="flex items-center gap-1">
          {ds.source_type === 'semantic' ? <Boxes className="h-3 w-3" /> : <Code2 className="h-3 w-3" />}
          {SOURCE_LABELS[ds.source_type] || ds.source_type}
        </Badge>
        {ds.chart_type && (
          <Badge variant="secondary" className="flex items-center gap-1">
            <BarChart3 className="h-3 w-3" /> {CHART_TYPE_LABELS[ds.chart_type] || ds.chart_type}
          </Badge>
        )}
      </div>
      {ds.object_key && (
        <p className="text-sm text-muted-foreground ml-11 mb-4">
          绑定本体对象 <code className="px-1 rounded bg-muted">{ds.object_key}</code>，语义字段随语义层/本体更新自动演进
          {ds.sql_query && <>；实际取数走执行 SQL（固定结果形状）</>}
        </p>
      )}

      <Tabs defaultValue="config" className="mt-2">
        <TabsList>
          <TabsTrigger value="config">配置</TabsTrigger>
          <TabsTrigger value="fields">字段定义 ({fields.length})</TabsTrigger>
          <TabsTrigger value="scopes">行级范围</TabsTrigger>
          <TabsTrigger value="refs">引用</TabsTrigger>
        </TabsList>

        {/* ── 配置: 基本信息 + 执行 SQL + 图表预设 + 数据预览(同页, 不分 Tab) ── */}
        <TabsContent value="config" className="pt-3 space-y-4">
          {/* 统一保存: 一次提交基本信息/查询定义/图表预设 */}
          {canManage && (
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={saveConfig} disabled={saving}>
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Save className="h-3.5 w-3.5 mr-1" />}
                保存配置
              </Button>
              <span className="text-xs text-muted-foreground">基本信息 / 查询定义 / 图表预设一次保存，不分块提交</span>
            </div>
          )}

          {/* 基本信息 */}
          <div className="border rounded-lg p-3 space-y-2 max-w-3xl">
            <div className="text-sm font-medium">基本信息</div>
            <div className="space-y-1.5">
              <Label>描述</Label>
              <Textarea rows={2} value={ds.description || ''} disabled={!canManage}
                onChange={e => setDs({ ...ds, description: e.target.value })} />
            </div>
          </div>

          {/* 执行 SQL 与语义互转 */}
          <ExecPanel
            ds={ds}
            fields={fields}
            canManage={canManage}
            onSaved={(data) => { setDs(normalizeDataset(data)); load(); }}
            onDsChange={(patch) => setDs((prev: any) => ({ ...prev, ...patch }))}
          />

          {/* 图表预设 */}
          <div className="border rounded-lg p-3 max-w-3xl">
            <div className="text-sm font-medium mb-2">图表预设</div>
            <ChartPresetPanel
              ds={ds}
              fields={fields}
              canManage={canManage}
              onDsChange={(patch) => setDs((prev: any) => ({ ...prev, ...patch }))}
            />
          </div>

          {/* 数据预览 */}
          <div className="border rounded-lg p-3">
            <div className="text-sm font-medium mb-2">数据预览</div>
            <PreviewPanel datasetId={datasetId} fields={fields} ds={ds} />
          </div>
        </TabsContent>

        {/* ── 字段定义(语义字典为主, field_config 保存映射与 SQL 独有列) ── */}
        <TabsContent value="fields" className="pt-3">
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">字段</th>
                  <th className="text-left px-3 py-2 font-medium">SQL 列</th>
                  <th className="text-left px-3 py-2 font-medium">显示名</th>
                  <th className="text-left px-3 py-2 font-medium w-28">角色</th>
                  <th className="text-left px-3 py-2 font-medium">说明</th>
                </tr>
              </thead>
              <tbody>
                {fields.map((f, i) => {
                  const editable = canManage && f.from === 'config';
                  return (
                    <tr key={`${f.field}-${i}`} className="border-t">
                      <td className="px-3 py-2">
                        <code className="text-xs">{f.field}</code>
                        {f.is_time && <Badge variant="secondary" className="ml-2 text-[10px]">时间</Badge>}
                        {f.from === 'semantic' && (
                          <Badge variant="outline" className="ml-2 text-[10px]">语义字典</Badge>
                        )}
                        {f.unmapped && (
                          <Badge variant="destructive" className="ml-2 text-[10px]">悬空映射</Badge>
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {f.sql_column
                          ? <code>{f.sql_column}</code>
                          : <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="px-3 py-2">
                        {editable ? (
                          <Input className="h-7 text-sm" value={f.label || ''}
                            onChange={e => setFields(prev => prev.map((p, pi) => pi === i ? { ...p, label: e.target.value } : p))} />
                        ) : (f.label || f.field)}
                      </td>
                      <td className="px-3 py-2">
                        {editable ? (
                          <Select value={f.role} onValueChange={v => setFields(prev => prev.map((p, pi) => pi === i ? { ...p, role: v as any } : p))}>
                            <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="dimension">维度</SelectItem>
                              <SelectItem value="measure">度量</SelectItem>
                            </SelectContent>
                          </Select>
                        ) : (
                          <Badge variant={f.role === 'measure' ? 'default' : 'secondary'} className="text-[10px]">
                            {f.role === 'measure' ? '度量' : '维度'}
                          </Badge>
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        {f.description || (f.aliases?.length ? `别名: ${f.aliases.join(', ')}` : '')}
                        {f.calculation ? ` · ${f.calculation}` : ''}
                      </td>
                    </tr>
                  );
                })}
                {fields.length === 0 && (
                  <tr><td colSpan={5} className="text-center py-6 text-muted-foreground text-sm">暂无字段</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            语义字典字段随本体自动演进（只读）；SQL 独有列与映射关系保存在 field_config，可在「配置」页用「由 SQL 对齐语义字段」生成
          </p>
          {canManage && (
            <Button size="sm" className="mt-3" onClick={async () => {
              // 落库 field_config: config 项原样保存 + 语义字段的 SQL 列映射(maps_to)
              const cfg = fields
                .filter(f => f.from === 'config')
                .map(({ from, unmapped, ...rest }: any) => rest)
                .concat(fields
                  .filter(f => f.from === 'semantic' && f.sql_column)
                  .map(f => ({ field: f.sql_column, maps_to: f.field, role: f.role })));
              try {
                await client.put(`/datasets/${datasetId}`, { field_config: cfg });
                toast.success('字段配置已保存');
                load();
              } catch (e: any) { toast.error(e.response?.data?.detail || '保存失败'); }
            }}>
              <Save className="h-3.5 w-3.5 mr-1" /> 保存字段配置
            </Button>
          )}
        </TabsContent>

        {/* ── 行级范围 ── */}
        <TabsContent value="scopes" className="pt-3">
          <ScopesPanel datasetId={datasetId} fields={fields} canManage={canManage} />
        </TabsContent>

        {/* ── 引用 ── */}
        <TabsContent value="refs" className="pt-3">
          <div className="border rounded-lg divide-y">
            {(ds.references || []).map((r: any) => (
              <div key={r.chart_id} className="flex items-center justify-between px-3 py-2 text-sm">
                <span>{r.chart_name}</span>
                <Link className="text-xs text-primary hover:underline" to={`/dashboard/editor/${r.dashboard_id}`}>
                  看板: {r.dashboard_name}
                </Link>
              </div>
            ))}
            {(ds.references || []).length === 0 && (
              <div className="text-center py-6 text-muted-foreground text-sm">暂未被看板引用</div>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── 执行 SQL + 语义互转(双定义) ────────────────────────────────────────

function ChipToggle({ active, onClick, disabled, children }: {
  active: boolean; onClick: () => void; disabled?: boolean; children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`px-2 py-0.5 rounded-full border text-[11px] transition-colors disabled:opacity-50 ${
        active
          ? 'bg-primary text-primary-foreground border-primary'
          : 'bg-background text-muted-foreground hover:bg-muted'
      }`}
    >
      {children}
    </button>
  );
}

function ExecPanel({ ds, fields, canManage, onSaved, onDsChange }: {
  ds: any; fields: DatasetField[]; canManage: boolean;
  onSaved: (data: any) => void; onDsChange: (patch: any) => void;
}) {
  const [objects, setObjects] = useState<any[]>([]);
  const [modelId, setModelId] = useState('');
  const [compiling, setCompiling] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [selDims, setSelDims] = useState<string[]>([]);
  const [selMeasures, setSelMeasures] = useState<string[]>([]);
  const [mapping, setMapping] = useState<any[] | null>(null);
  const [semFieldNames, setSemFieldNames] = useState<string[]>([]);

  // 本体对象候选(数据源 → active 模型 → objects)
  useEffect(() => {
    if (!ds.datasource_id) { setObjects([]); setModelId(''); return; }
    client.get('/catalog/ontology/models', { params: { datasource_id: ds.datasource_id } })
      .then(({ data }) => {
        const ms = (data.items || []).filter((m: any) => m.status === 'active');
        setModelId(ms.length ? String(ms[0].id) : '');
      })
      .catch(() => { setObjects([]); setModelId(''); });
  }, [ds.datasource_id]);

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

  // 由语义生成 SQL: 声明式意图 → 编译 SQL(仅 dataset:manage 可见, 人工编辑面板)
  const compileSql = async () => {
    if (!ds.object_key) { toast.error('请先绑定本体对象'); return; }
    if (selDims.length + selMeasures.length === 0) {
      toast.error('请勾选参与查询的维度/度量'); return;
    }
    setCompiling(true);
    try {
      const { data } = await client.post('/datasets/compile-from-semantic', {
        object_key: ds.object_key,
        datasource_id: Number(ds.datasource_id) || 0,
        dimensions: selDims,
        measures: selMeasures,
        limit: 200,
      });
      onDsChange({ sql_query: data.sql });
      (data.warnings || []).forEach((w: string) => toast.warning(w));
      toast.success('已生成执行 SQL（人工确认后保存）');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '编译 SQL 失败');
    } finally {
      setCompiling(false);
    }
  };

  // 由 SQL 对齐语义字段: 探测输出列 → 映射建议(未命中列显式待映射, 不静默绑定)
  const extractMapping = async () => {
    if (!(ds.sql_query || '').trim()) { toast.error('请先填写执行 SQL'); return; }
    setExtracting(true);
    try {
      const { data } = await client.post('/datasets/extract-from-sql', {
        sql_query: ds.sql_query,
        object_key: ds.object_key || '',
        datasource_id: Number(ds.datasource_id) || 0,
      });
      setMapping(data.fields || []);
      setSemFieldNames(data.semantic_fields || []);
      if ((data.unmapped || []).length > 0) {
        toast.warning(`${data.unmapped.length} 列未命中语义字段，请人工确认映射`);
      } else {
        toast.success('全部列已命中语义字段，请确认映射');
      }
    } catch (e: any) {
      setMapping(null);
      toast.error(e.response?.data?.detail || '字段对齐失败');
    } finally {
      setExtracting(false);
    }
  };

  // 映射确认 → 落 field_config(SQL 列 → 语义字段 maps_to, 未命中列独立保存)
  const confirmMapping = async () => {
    if (!mapping) return;
    const cfg = mapping.map(m => ({
      field: m.field,
      maps_to: m.maps_to || '',
      role: m.role || 'dimension',
    }));
    try {
      await client.put(`/datasets/${ds.id}`, { field_config: cfg });
      toast.success('字段映射已保存');
      setMapping(null);
      onSaved(ds);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  };

  const semFields = fields.filter(f => f.from === 'semantic');

  return (
    <div className="space-y-4 max-w-3xl">
      {/* 语义绑定 */}
      <div className="border rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <Boxes className="h-4 w-4" /> 语义对象绑定
          <Badge variant="outline" className="text-[10px]">可选 · 与执行 SQL 并存</Badge>
        </div>
        <Select
          value={ds.object_key || ''}
          onValueChange={v => onDsChange({ object_key: v })}
          disabled={!canManage}
        >
          <SelectTrigger className="max-w-sm"><SelectValue placeholder={objects.length ? '选择本体对象' : '该数据源暂无 active 本体模型'} /></SelectTrigger>
          <SelectContent>
            {objects.map(o => <SelectItem key={o.key} value={o.key}>{o.display_name} ({o.key})</SelectItem>)}
          </SelectContent>
        </Select>
        {ds.object_key && semFields.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">语义字段（点选参与「由语义生成 SQL」的维度/度量）：</p>
            <div className="flex flex-wrap gap-1.5">
              {semFields.filter(f => f.role === 'dimension').map(f => (
                <ChipToggle key={f.field} disabled={!canManage}
                  active={selDims.includes(f.field)}
                  onClick={() => setSelDims(p => p.includes(f.field) ? p.filter(x => x !== f.field) : [...p, f.field])}>
                  {f.label || f.field}
                </ChipToggle>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {semFields.filter(f => f.role === 'measure').map(f => (
                <ChipToggle key={f.field} disabled={!canManage}
                  active={selMeasures.includes(f.field)}
                  onClick={() => setSelMeasures(p => p.includes(f.field) ? p.filter(x => x !== f.field) : [...p, f.field])}>
                  {f.label || f.field}
                </ChipToggle>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 执行 SQL */}
      <div className="border rounded-lg p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-sm font-medium">
            <Code2 className="h-4 w-4" /> 执行 SQL
            <Badge variant="outline" className="text-[10px]">实际执行通道 · 仅 SELECT/WITH</Badge>
          </div>
          {canManage && (
            <div className="flex gap-1.5">
              {ds.object_key && (
                <Button size="sm" variant="outline" onClick={compileSql} disabled={compiling}>
                  {compiling
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                    : <Wand2 className="h-3.5 w-3.5 mr-1" />}
                  由语义生成 SQL
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={extractMapping} disabled={extracting}>
                {extracting
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                  : <AlignLeft className="h-3.5 w-3.5 mr-1" />}
                由 SQL 对齐语义字段
              </Button>
            </div>
          )}
        </div>
        <div className="space-y-1.5">
          <Label>执行数据源</Label>
          <SqlDatasourceSelect
            value={ds.datasource_id || 0}
            disabled={!canManage}
            onChange={v => onDsChange({ datasource_id: v })}
          />
        </div>
        <Textarea
          rows={8} className="font-mono text-xs"
          value={ds.sql_query || ''}
          disabled={!canManage}
          onChange={e => onDsChange({ sql_query: e.target.value })}
          placeholder="SELECT region, status, COUNT(*) AS cnt FROM t_case_records GROUP BY region, status"
        />
        <p className="text-xs text-muted-foreground">
          修改后点击上方「保存配置」统一提交（含基本信息与图表预设）
        </p>
      </div>

      {/* 映射建议确认(未命中列显式待映射, 不静默绑定) */}
      {mapping && (
        <div className="border rounded-lg p-3 space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium">字段映射建议（SQL 输出列 → 语义字段）</div>
            <div className="flex gap-1.5">
              <Button size="sm" variant="outline" onClick={() => setMapping(null)}>取消</Button>
              <Button size="sm" onClick={confirmMapping} disabled={!canManage}>
                <Save className="h-3.5 w-3.5 mr-1" /> 确认映射并保存
              </Button>
            </div>
          </div>
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 text-muted-foreground">
                <tr>
                  <th className="text-left px-2.5 py-1.5 font-medium">SQL 列</th>
                  <th className="text-left px-2.5 py-1.5 font-medium">映射语义字段</th>
                  <th className="text-left px-2.5 py-1.5 font-medium">角色</th>
                  <th className="text-left px-2.5 py-1.5 font-medium">匹配方式</th>
                </tr>
              </thead>
              <tbody>
                {mapping.map((m, i) => (
                  <tr key={m.field} className="border-t">
                    <td className="px-2.5 py-1.5"><code>{m.field}</code></td>
                    <td className="px-2.5 py-1.5">
                      <Select
                        value={m.maps_to || '__none__'}
                        onValueChange={v => setMapping(prev => prev
                          ? prev.map((p, pi) => pi === i ? { ...p, maps_to: v === '__none__' ? '' : v } : p)
                          : prev)}
                      >
                        <SelectTrigger className="h-7 w-48 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">
                            {m.maps_to ? '不映射' : '（待映射）'}
                          </SelectItem>
                          {semFieldNames.map(n => <SelectItem key={n} value={n}>{n}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </td>
                    <td className="px-2.5 py-1.5">
                      <Badge variant={m.role === 'measure' ? 'default' : 'secondary'} className="text-[10px]">
                        {m.role === 'measure' ? '度量' : '维度'}
                      </Badge>
                    </td>
                    <td className="px-2.5 py-1.5 text-muted-foreground">
                      {m.match_source === 'name' && '精确名'}
                      {m.match_source === 'name_en' && '英文名'}
                      {m.match_source === 'alias' && '别名'}
                      {m.match_source === 'none' && (
                        <span className="text-amber-600">未命中{m.candidates?.length ? '（可选候选）' : ''}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground">
            未命中列不会静默绑定，请人工选择映射或保留为 SQL 独有字段（宁缺勿错）
          </p>
        </div>
      )}
    </div>
  );
}

// ── 图表预设(引入看板时一键生成) ──────────────────────────────────────

function ChartPresetPanel({ ds, fields, canManage, onDsChange }: {
  ds: any; fields: DatasetField[]; canManage: boolean;
  onDsChange: (patch: any) => void;
}) {
  const preset = ds.chart_preset || {};
  // 预设列引用查询结果列名: 有 SQL 映射用 SQL 列, 否则用字段名
  const resultColumns = fields.map(f => f.sql_column || f.field);

  return (
    <div className="space-y-3 max-w-xl">
      <p className="text-sm text-muted-foreground">
        预设图表类型与字段映射，看板编辑器「从数据集引入」时直接搬入图表配置。
      </p>
      <div className="space-y-1.5">
        <Label>图表类型</Label>
        <Select
          value={ds.chart_type || ''}
          onValueChange={v => onDsChange({ chart_type: v })}
          disabled={!canManage}
        >
          <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
          <SelectContent>
            {PRESET_CHART_TYPES.map(t => (
              <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>X 轴列</Label>
          <Select
            value={preset.xCol || ''}
            onValueChange={v => onDsChange({ chart_preset: { ...preset, xCol: v } })}
            disabled={!canManage}
          >
            <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
            <SelectContent>
              {resultColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>Y 轴列</Label>
          <Select
            value={preset.yCol || ''}
            onValueChange={v => onDsChange({ chart_preset: { ...preset, yCol: v } })}
            disabled={!canManage}
          >
            <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
            <SelectContent>
              {resultColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>分组列</Label>
          <Select
            value={preset.groupCol || ''}
            onValueChange={v => onDsChange({ chart_preset: { ...preset, groupCol: v } })}
            disabled={!canManage}
          >
            <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
            <SelectContent>
              {resultColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>行数上限</Label>
          <Input
            type="number" min={0} value={preset.limit || ''} disabled={!canManage}
            onChange={e => onDsChange({ chart_preset: { ...preset, limit: Number(e.target.value) || 0 } })}
            placeholder="默认"
          />
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        修改后点击上方「保存配置」统一提交；保存/改动会即时反映到下方「数据预览」的图表预览
      </p>
    </div>
  );
}

// ── SQL 源执行数据源选择 ────────────────────────────────────────────────

function SqlDatasourceSelect({ value, disabled, onChange }: {
  value: number; disabled: boolean; onChange: (v: number) => void;
}) {
  const [list, setList] = useState<any[]>([]);
  useEffect(() => {
    client.get('/datasources/').then(({ data }) => setList(Array.isArray(data) ? data : (data?.items || []))).catch(() => {});
  }, []);
  return (
    <Select value={String(value)} onValueChange={v => onChange(Number(v))} disabled={disabled}>
      <SelectTrigger><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value="0">默认引擎</SelectItem>
        {list.map((d: any) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

// ── 数据预览(治理取数, 服务端施加权限/RLS/scopes) ─────────────────────

function PreviewPanel({ datasetId, fields, ds }: { datasetId: number; fields: DatasetField[]; ds: any }) {
  const [result, setResult] = useState<any>(null);
  const [running, setRunning] = useState(false);
  const dims = fields.filter(f => f.role === 'dimension');
  const measures = fields.filter(f => f.role === 'measure');
  const [dimensions, setDimensions] = useState<string[]>(dims.slice(0, 1).map(f => f.field));
  const [measuresSel, setMeasuresSel] = useState<string[]>(measures.slice(0, 2).map(f => f.field));

  const run = async () => {
    setRunning(true);
    try {
      const { data } = await client.post(`/datasets/${datasetId}/preview`, {
        dimensions, measures: measuresSel, limit: 100,
      });
      setResult(data);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '预览失败');
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-end">
        <div className="min-w-40">
          <Label className="text-xs">维度</Label>
          <div className="flex flex-wrap gap-1 mt-1">
            {dims.map(f => (
              <Badge key={f.field}
                variant={dimensions.includes(f.field) ? 'default' : 'outline'}
                className="cursor-pointer text-[11px]"
                onClick={() => setDimensions(p => p.includes(f.field) ? p.filter(x => x !== f.field) : [...p, f.field])}>
                {f.label || f.field}
              </Badge>
            ))}
          </div>
        </div>
        <div className="min-w-40">
          <Label className="text-xs">度量</Label>
          <div className="flex flex-wrap gap-1 mt-1">
            {measures.map(f => (
              <Badge key={f.field}
                variant={measuresSel.includes(f.field) ? 'default' : 'outline'}
                className="cursor-pointer text-[11px]"
                onClick={() => setMeasuresSel(p => p.includes(f.field) ? p.filter(x => x !== f.field) : [...p, f.field])}>
                {f.label || f.field}
              </Badge>
            ))}
          </div>
        </div>
        <Button size="sm" onClick={run} disabled={running}>
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Play className="h-3.5 w-3.5 mr-1" />}
          预览取数
        </Button>
      </div>

      {result && (
        <>
          {/* 图表预览: 按已保存的图表预设同步渲染(预设改动即时反映, 不需重新取数) */}
          {ds.chart_type && (
            <div className="border rounded-lg p-3">
              <div className="text-sm font-medium mb-2 flex items-center gap-1.5">
                <BarChart3 className="h-4 w-4" /> 图表预览 · {CHART_TYPE_LABELS[ds.chart_type] || ds.chart_type}
                <Badge variant="outline" className="text-[10px]">按图表预设渲染</Badge>
              </div>
              {(result.rows || []).length > 0 ? (
                <div className="relative h-[320px]">
                  <DashboardChart
                    chartType={ds.chart_type}
                    data={{ columns: result.columns || [], rows: result.rows || [] }}
                    config={cleanPreset(ds.chart_preset)}
                    draft
                  />
                </div>
              ) : (
                <p className="py-6 text-center text-sm text-muted-foreground">查询成功，当前范围没有数据，无法渲染图表。</p>
              )}
            </div>
          )}
          <div className="text-xs text-muted-foreground flex gap-3 flex-wrap">
            <span>{result.row_count} 行</span>
            {result.execution_mode && (
              <span>执行方式: {result.execution_mode === 'sql' ? '执行 SQL' : '语义查询'}</span>
            )}
            {result.elapsed_ms != null && <span>{Math.round(result.elapsed_ms)}ms</span>}
            {result.applied_rls ? <span className="text-amber-600">已应用行级权限</span> : <span>无 RLS 规则</span>}
            {!!result.masked_columns?.length && <span className="text-destructive">脱敏列: {result.masked_columns.join(', ')}</span>}
          </div>
          <div className="border rounded-lg overflow-auto max-h-96">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 sticky top-0">
                <tr>{(result.columns || []).map((c: string) => <th key={c} className="text-left px-2.5 py-1.5 font-medium whitespace-nowrap">{c}</th>)}</tr>
              </thead>
              <tbody>
                {(result.rows || []).slice(0, 100).map((row: any, ri: number) => (
                  <tr key={ri} className="border-t">
                    {(result.columns || []).map((c: string) => (
                      <td key={c} className="px-2.5 py-1 whitespace-nowrap">{row[c] == null ? '—' : String(row[c])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {!result && !running && (
        <div className="text-center py-8 text-muted-foreground text-sm border rounded-lg">
          点击「预览取数」经统一治理入口查询(自动施加你的权限/RLS/敏感脱敏/行级范围)
        </div>
      )}
    </div>
  );
}

// ── 行级范围配置 ──────────────────────────────────────────────────────

function ScopesPanel({ datasetId, fields, canManage }: {
  datasetId: number; fields: DatasetField[]; canManage: boolean;
}) {
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    client.get(`/datasets/${datasetId}/scopes`).then(({ data }) => setScopes(data.scopes || [])).catch(() => {});
    if (canManage) {
      client.get('/users/', { params: { size: 200 } }).then(({ data }) => setUsers(data.items || data || [])).catch(() => {});
      client.get('/roles/').then(({ data }) => setRoles(Array.isArray(data) ? data : [])).catch(() => {});
    }
  }, [datasetId, canManage]);

  const update = (i: number, patch: Partial<Scope>) =>
    setScopes(prev => prev.map((s, si) => si === i ? { ...s, ...patch } : s));

  const updateFilter = (i: number, fi: number, patch: any) =>
    setScopes(prev => prev.map((s, si) => si === i ? {
      ...s, filters: s.filters.map((f, fli) => fli === fi ? { ...f, ...patch } : f),
    } : s));

  const save = async () => {
    setSaving(true);
    try {
      await client.put(`/datasets/${datasetId}/scopes`, { scopes });
      const { data } = await client.get(`/datasets/${datasetId}/scopes`);
      setScopes(data.scopes || []);
      toast.success('行级范围已保存');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        为指定用户/角色追加行过滤条件（在 RLS 与敏感基线之上叠加，只收紧不放宽）。
      </p>
      {scopes.map((s, i) => {
        const subjectName = s.subject_type === 'user'
          ? users.find(u => u.id === s.subject_id)?.username || `用户#${s.subject_id}`
          : roles.find(r => r.id === s.subject_id)?.display_name || `角色#${s.subject_id}`;
        return (
          <div key={i} className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              {s.subject_type === 'user' ? <Users className="h-4 w-4 text-muted-foreground" /> : <Shield className="h-4 w-4 text-muted-foreground" />}
              {canManage ? (
                <>
                  <Select value={s.subject_type} onValueChange={v => update(i, { subject_type: v as any, subject_id: 0 })}>
                    <SelectTrigger className="h-8 w-24 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="user">用户</SelectItem>
                      <SelectItem value="role">角色</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select
                    value={s.subject_id ? String(s.subject_id) : '__none__'}
                    onValueChange={v => update(i, { subject_id: v === '__none__' ? 0 : Number(v) })}
                  >
                    <SelectTrigger className="h-8 w-52 text-xs"><SelectValue placeholder="选择用户/角色" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">选择用户/角色</SelectItem>
                      {(s.subject_type === 'user'
                        ? users.map(u => ({ id: u.id, label: u.username || u.display_name || `用户#${u.id}` }))
                        : roles.map(r => ({ id: r.id, label: r.display_name || r.name || `角色#${r.id}` }))
                      ).map(o => <SelectItem key={o.id} value={String(o.id)}>{o.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </>
              ) : (
                <span className="text-sm font-medium">{subjectName}</span>
              )}
              {canManage && (
                <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setScopes(p => p.filter((_, x) => x !== i))}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
            {s.filters.map((f, fi) => (
              <div key={fi} className="flex gap-2 items-center flex-wrap">
                <Select value={f.field} onValueChange={v => updateFilter(i, fi, { field: v })} disabled={!canManage}>
                  <SelectTrigger className="h-8 w-40 text-xs"><SelectValue placeholder="字段" /></SelectTrigger>
                  <SelectContent>
                    {fields.map(fd => <SelectItem key={fd.field} value={fd.field}>{fd.label || fd.field}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Select value={f.op} onValueChange={v => updateFilter(i, fi, { op: v })} disabled={!canManage}>
                  <SelectTrigger className="h-8 w-24 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>{OPS.map(o => <SelectItem key={o} value={o}>{o}</SelectItem>)}</SelectContent>
                </Select>
                <Input className="h-8 w-44 text-xs" value={Array.isArray(f.value) ? f.value.join(',') : String(f.value ?? '')}
                  disabled={!canManage} placeholder="值(in 用逗号分隔)"
                  onChange={e => updateFilter(i, fi, {
                    value: f.op === 'in' ? e.target.value.split(',').map(x => x.trim()).filter(Boolean) : e.target.value,
                  })} />
              </div>
            ))}
            {canManage && (
              <Button variant="outline" size="sm" onClick={() => update(i, {
                filters: [...s.filters, { field: fields[0]?.field || '', op: 'eq', value: '' }],
              })}>
                <Plus className="h-3 w-3 mr-1" /> 条件
              </Button>
            )}
          </div>
        );
      })}
      {canManage && (
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setScopes(p => [...p, {
            subject_type: 'user', subject_id: 0,  // 不默认第一个用户, 主体由选择器显式指定
            filters: [{ field: fields[0]?.field || '', op: 'eq', value: '' }],
          }])}>
            <Plus className="h-3.5 w-3.5 mr-1" /> 添加范围
          </Button>
          <Button size="sm" onClick={save} disabled={saving || scopes.some(s => !s.subject_id)}>
            {saving ? '保存中…' : '保存行级范围'}
          </Button>
        </div>
      )}
      {scopes.length === 0 && !canManage && (
        <div className="text-center py-6 text-muted-foreground text-sm border rounded-lg">未配置行级范围</div>
      )}
    </div>
  );
}
