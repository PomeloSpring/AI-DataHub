import { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  ArrowLeft, Code2, Play, Save, Plus, Trash2, Loader2, Users, Shield,
  BarChart3, Check, ChevronsUpDown,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Popover, PopoverContent, PopoverTrigger,
} from '@/components/ui/popover';
import client from '../../api/client';
import { hasPerm } from '../../stores/permissionStore';
import DashboardChart, { CHART_TYPES } from '../../components/DashboardChart';
import { CONTROL_LABELS, KIND_LABELS, ROLE_LABELS, VALUE_TYPE_LABELS } from '@/lib/dashboardParamAssembly';
import { useChartDesign } from '@/hooks/useChartDesign';
import { FIELD_DISPLAY_FORMATS, applyFieldFormats } from '@/lib/fieldFormat';

// Radix Select 不接受空串作为选项值，「不格式化」用哨兵位表示（存回后端仍是空 = 原样下发）
const NO_FORMAT = '__no_format__';

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
  // 显示格式令牌（时间列怎么给人看）：数据集声明，服务端随取数结果下发给各渲染面
  display_format?: string;
}

interface Scope {
  id?: number;
  subject_type: 'user' | 'role';
  subject_id: number;
  filters: Array<{ field: string; op: string; value: any }>;
}

const OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'like'];
// 来源类型显示业务名（内部枚举 semantic/both 不得直接当名称展示，ui-resource-display §1）
// 'es' 取值随内置通道退役（写入被后端显式拒）；ES 数据集的 source_type 就是 sql，
// 它的 ES 身份由服务端下发的执行通道位 `is_es` 表达，不在前端拿枚举反推。
const SOURCE_LABELS: Record<string, string> = {
  sql: 'SQL',
  semantic: '语义对象',
  both: '语义 + SQL',
};
const PRESET_CHART_TYPES = CHART_TYPES.filter(t => t.category !== 'widget');
const CHART_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  CHART_TYPES.map(t => [t.value, t.label]),
);

/** update/create 接口返回原始行, chart_preset/available_datasources 是 JSON 字符串 → 归一为对象/数组 */
function normalizeDataset(data: any): any {
  let preset = data?.chart_preset;
  if (typeof preset === 'string') {
    try { preset = JSON.parse(preset || '{}'); } catch { preset = {}; }
  }
  let scope = data?.available_datasources;
  if (typeof scope === 'string') {
    try { scope = JSON.parse(scope || '[]'); } catch { scope = []; }
  }
  return {
    ...(data || {}),
    chart_preset: preset || {},
    available_datasources: Array.isArray(scope) ? scope : [],
  };
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
        sql_query: ds.sql_query || '',
        datasource_id: Number(ds.datasource_id) || 0,
        // 可用数据源范围：存数据源**业务名**数组（不存 id，删建仍存活）
        available_datasources: ds.available_datasources || [],
        chart_type: ds.chart_type || '',
        chart_preset: cleanPreset(ds.chart_preset),
        // 明细行集 vs 统计汇总：表值图能不能真翻页由这一位决定（在数据集层声明）
        paginable: !!ds.paginable,
      });
      setDs(normalizeDataset(data));
      load(true);
      toast.success('配置已保存（基本信息 / 查询定义 / 图表预设）');
      // 服务端告警（如跨引擎无方言正文）必须显式递到人眼前，不得静默存下
      (data?.warnings || []).forEach((w: string) => toast.warning(w));
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
          <Code2 className="h-3 w-3" />
          {SOURCE_LABELS[ds.source_type] || ds.source_type || 'SQL'}
        </Badge>
        {ds.chart_type && (
          <Badge variant="secondary" className="flex items-center gap-1">
            <BarChart3 className="h-3 w-3" /> {CHART_TYPE_LABELS[ds.chart_type] || ds.chart_type}
          </Badge>
        )}
      </div>

      <Tabs defaultValue="config" className="mt-2">
        <TabsList>
          <TabsTrigger value="config">配置</TabsTrigger>
          <TabsTrigger value="fields">字段定义 ({fields.length})</TabsTrigger>
        <TabsTrigger value="params">入参 ({(ds.param_plan || []).length})</TabsTrigger>
          <TabsTrigger value="scopes">行级范围</TabsTrigger>
          <TabsTrigger value="refs">引用</TabsTrigger>
        </TabsList>

        {/* ── 配置: 左=基本信息/执行 SQL/图表预设, 右=数据预览(取数 + 图表预览) 双栏 ── */}
        <TabsContent value="config" className="pt-3">
          <div className="grid gap-4 items-start xl:grid-cols-[minmax(0,44%)_minmax(0,1fr)]">
            {/* 左栏: 配置表单 */}
            <div className="space-y-4 min-w-0">
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
              <div className="border rounded-lg p-3 space-y-2">
                <div className="text-sm font-medium">基本信息</div>
                <div className="space-y-1.5">
                  <Label>描述</Label>
                  <Textarea rows={2} value={ds.description || ''} disabled={!canManage}
                    onChange={e => setDs({ ...ds, description: e.target.value })} />
                </div>
              </div>

              {/* 执行 SQL */}
              <ExecPanel
                ds={ds}
                fields={fields}
                canManage={canManage}
                onSaved={(_data: any) => { load(); }}
                onDsChange={(patch) => setDs((prev: any) => ({ ...prev, ...patch }))}
              />

              {/* 图表预设 */}
              <div className="border rounded-lg p-3">
                <div className="text-sm font-medium mb-2">图表预设</div>
                <ChartPresetPanel
                  ds={ds}
                  fields={fields}
                  canManage={canManage}
                  onDsChange={(patch) => setDs((prev: any) => ({ ...prev, ...patch }))}
                />
              </div>
            </div>

            {/* 右栏: 数据预览(预览取数 + 按图表预设渲染的图表预览); 宽屏吸附并自带滚动, 避免高内容被裁 */}
            <div className="border rounded-lg p-3 min-w-0 space-y-2 xl:sticky xl:top-2 xl:max-h-[calc(100vh-2rem)] xl:overflow-auto">
              <div className="text-sm font-medium">数据预览</div>
              <PreviewPanel datasetId={datasetId} fields={fields} ds={ds} />
            </div>
          </div>
        </TabsContent>

        {/* ── 字段定义(field_config 保存映射与 SQL 独有列) ── */}
        <TabsContent value="fields" className="pt-3">
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">字段</th>
                  <th className="text-left px-3 py-2 font-medium">SQL 列</th>
                  <th className="text-left px-3 py-2 font-medium">显示名</th>
                  <th className="text-left px-3 py-2 font-medium w-28">角色</th>
                  <th className="text-left px-3 py-2 font-medium w-40">显示格式</th>
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
                      <td className="px-3 py-2">
                        {/* 可编辑条件与落库形状一致：config 项原样写、语义项靠 sql_column 生成
                            {field, maps_to} 行才写得进去。没 sql_column 的语义字段根本不在
                            field_config 里，此处给下拉就是"配了存不下"的空承诺 —— 直接说明原因。 */}
                        {canManage && (f.from === 'config' || f.sql_column) ? (
                          <Select value={f.display_format || NO_FORMAT}
                            onValueChange={v => setFields(prev => prev.map((p, pi) =>
                              pi === i ? { ...p, display_format: v === NO_FORMAT ? '' : v } : p))}>
                            <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value={NO_FORMAT} className="text-xs">不格式化（原样）</SelectItem>
                              {FIELD_DISPLAY_FORMATS.filter(o => o.value).map(o => (
                                <SelectItem key={o.value} value={o.value} className="text-xs">
                                  {o.label} · {o.example}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            {f.display_format
                              ? FIELD_DISPLAY_FORMATS.find(o => o.value === f.display_format)?.example || f.display_format
                              : '原样'}
                          </span>
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
                  <tr><td colSpan={6} className="text-center py-6 text-muted-foreground text-sm">暂无字段</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            SQL 独有列与映射关系保存在 field_config。
            「显示格式」只改**怎么给人看**（取数结果仍是可比较、可运算的原值）：时间列配成
            「按日」后，图表 X 轴、tooltip、表值图与明细表同时变成 2026-10-12，
            不再是一排被截断的 2026-10-12T00:00:00.000。
          </p>
          {canManage && (
            <Button size="sm" className="mt-3" onClick={async () => {
              // 落库 field_config: config 项原样保存 + 语义字段的 SQL 列映射(maps_to)
              const cfg = fields
                .filter(f => f.from === 'config')
                .map(({ from, unmapped, ...rest }: any) => rest)
                .concat(fields
                  .filter(f => f.from === 'semantic' && f.sql_column)
                  .map(f => ({ field: f.sql_column, maps_to: f.field, role: f.role,
                               display_format: f.display_format || '' })));
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

        {/* ── 行级范围（ES 型不适用，面板内显式说明原因）── */}
        <TabsContent value="params" className="pt-3">
          <ParamsPanel ds={ds} datasetId={datasetId} canManage={canManage} onSaved={() => load(true)} />
        </TabsContent>

        <TabsContent value="scopes" className="pt-3">
          <ScopesPanel datasetId={datasetId} fields={fields} canManage={canManage} ds={ds} />
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

// ── 执行 SQL ────────────────────────────────────────────────────────────

function ExecPanel({ ds, canManage, onDsChange }: {
  ds: any; fields: DatasetField[]; canManage: boolean;
  onSaved: (data: any) => void; onDsChange: (patch: any) => void;
}) {
  // ES 正文也是“选执行数据源 + 可用范围”的正常数据集，只有一件事不同：方言受限。
  // 判据用服务端下发的执行通道位 `is_es`（= 取数分派的 execution_is_es），
  // 内置通道那一套“索引范围选择器 + source_type==='es'”已于阶段三退役。
  const isEs = !!ds.is_es;
  return (
    <div className="space-y-4">
      {/* 执行正文：所有通道都是“执行数据源 + 可用范围 + 正文”，不存在第二套登记口径 */}
      <div className="border rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <Code2 className="h-4 w-4" /> 执行 SQL
          <Badge variant="outline" className="text-[10px]">
            {isEs ? 'ES SQL 方言 · 单表 SELECT，必须 LIMIT' : '实际执行通道 · 仅 SELECT/WITH'}
          </Badge>
        </div>
        <div className="space-y-1.5">
          <Label>执行数据源</Label>
          <SqlDatasourceSelect
            value={ds.datasource_id || 0}
            disabled={!canManage}
            onChange={v => onDsChange({ datasource_id: v })}
          />
        </div>
        <div className="space-y-1.5">
          <Label>可用数据源范围</Label>
          <DatasourceScopeSelect
            value={ds.available_datasources || []}
            disabled={!canManage}
            execName={ds.datasource_name || ''}
            onChange={v => onDsChange({ available_datasources: v })}
          />
          <p className="text-xs text-muted-foreground">
            不填 = 仅能在执行数据源上跑。填入多个源后，同一份 SQL 可在这些源上执行
            （看板与 Chat 取数时服务端按当前用户的已授权源定源，多义时会要求指定站点）。
          </p>
        </div>
        {(ds.warnings || []).length > 0 && (
          <div className="border rounded-md p-2 bg-amber-50 dark:bg-amber-950/30 space-y-1">
            {(ds.warnings || []).map((w: string, i: number) => (
              <div key={i} className="text-xs text-amber-700 dark:text-amber-400">· {w}</div>
            ))}
          </div>
        )}
        <Textarea
          rows={8} className="font-mono text-xs"
          value={ds.sql_query || ''}
          disabled={!canManage}
          onChange={e => onDsChange({ sql_query: e.target.value })}
          placeholder={isEs
            ? 'SELECT DATE_TRUNC(\'DAY\', "@timestamp") AS d, COUNT(*) AS c\nFROM "logs-*" GROUP BY DATE_TRUNC(\'DAY\', "@timestamp") ORDER BY d ASC LIMIT 60'
            : 'SELECT region, status, COUNT(*) AS cnt FROM t_case_records GROUP BY region, status'}
        />
        {/* 方言规则只从服务端 `es_sql_hint` 读（与执行侧同一个解析器校准的事实源）：
            前端自己写一份就会与实机行为漂移，内置通道退役后这个出口从 /es-patterns 移到详情响应 */}
        {isEs && ds.es_sql_hint && (
          <p className="text-[10px] leading-relaxed text-muted-foreground">{ds.es_sql_hint}</p>
        )}
        <p className="text-xs text-muted-foreground">
          修改后点击上方「保存配置」统一提交（含基本信息与图表预设）
        </p>
      </div>

      {/* 结果形状声明：能不能按页取是正文决定的，所以登记在数据集而不是图表 */}
      <div className="border rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-2">
          <Switch
            checked={!!ds.paginable}
            disabled={!canManage}
            onCheckedChange={v => onDsChange({ paginable: v })}
          />
          <Label className="text-sm font-medium">支持分页（结果是明细行集）</Label>
        </div>
        <p className="text-xs text-muted-foreground">
          开启后，看板上的表值图会按「每页行数 / 页号」向服务端取数（每页上限 100 行），
          不再一次拉全量再在前端切片。数据源型数据集可直接跳页，ES 型数据集按游标逐页往后翻。
          正文是 GROUP BY / 聚合统计的（本身就是完整的一页）请关闭本项，
          否则翻页会对统计结果套页限、把趋势砍没。
        </p>
      </div>
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
    <div className="space-y-3">
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
        修改后点击上方「保存配置」统一提交；保存/改动会即时反映到右侧「数据预览」的图表预览
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

/**
 * 可用数据源范围多选（Popover + checkbox，按站点分组）。
 *
 * 选中值存数据源**业务名**（与本体「适用站点」同口径）：内部 id 是黑盒且会因删建漂移。
 * 执行数据源隐式属于可用范围，因此列表中置灰不可选（避免重复登记）。
 */
function DatasourceScopeSelect({ value, disabled, execName, onChange }: {
  value: string[]; disabled: boolean; execName?: string;
  onChange: (v: string[]) => void;
}) {
  const [list, setList] = useState<any[]>([]);
  const [sites, setSites] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    client.get('/datasources/')
      .then(({ data }) => setList(Array.isArray(data) ? data : (data?.items || [])))
      .catch(() => setList([]));
    client.get('/datasources/sites')
      .then(({ data }) => setSites(Array.isArray(data) ? data : (data?.items || [])))
      .catch(() => setSites([]));
  }, []);
  const selected = value || [];
  const siteLabel = (code: string) =>
    sites.find((s: any) => s.site_code === code)?.display_name || code || '未归属站点';
  const groups: Array<{ code: string; label: string; items: any[] }> = [];
  list.forEach((d: any) => {
    const code = String(d.site_code || '');
    let g = groups.find(x => x.code === code);
    if (!g) { g = { code, label: siteLabel(code), items: [] }; groups.push(g); }
    g.items.push(d);
  });
  const toggle = (name: string) => onChange(
    selected.includes(name) ? selected.filter(x => x !== name) : [...selected, name]);
  const display = selected.length === 0
    ? (execName ? `仅执行数据源（${execName}）` : '仅执行数据源')
    : selected.length <= 2
      ? selected.join('、')
      : `已选 ${selected.length} 个源`;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" role="combobox" disabled={disabled}
          className="w-full justify-between font-normal text-sm h-9 overflow-hidden">
          <span className="truncate">{display}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0 max-h-72 overflow-auto" align="start">
        {list.length === 0 && (
          <div className="px-3 py-2 text-sm text-muted-foreground">无可选数据源</div>
        )}
        {groups.map(g => (
          <div key={g.code || '_none'}>
            <div className="px-3 pt-2 pb-1 text-[11px] font-medium text-muted-foreground">{g.label}</div>
            {g.items.map((d: any) => {
              const isExec = !!execName && d.name === execName;
              const on = selected.includes(d.name) || isExec;
              return (
                <div key={d.id}
                  className={`flex items-center gap-2 px-3 py-1.5 text-sm ${
                    isExec ? 'opacity-60' : 'cursor-pointer hover:bg-accent'}`}
                  onClick={() => { if (!isExec) toggle(d.name); }}
                  title={isExec ? '已是执行数据源，默认属于可用范围' : undefined}
                >
                  <div className={`w-4 h-4 border rounded flex items-center justify-center ${
                    on ? 'bg-primary border-primary' : 'border-muted-foreground'}`}>
                    {on && <Check className="h-3 w-3 text-primary-foreground" />}
                  </div>
                  <span className="flex-1 truncate">{d.name}</span>
                  <span className="text-[10px] text-muted-foreground">{d.db_type}</span>
                </div>
              );
            })}
          </div>
        ))}
      </PopoverContent>
    </Popover>
  );
}

// ── 数据预览(治理取数, 服务端施加权限/RLS/scopes) ─────────────────────

// 导出供用例直接验证 ES 执行通道的预览契约（请求体形状与治理标注来源）
export function PreviewPanel({ datasetId, fields, ds }: { datasetId: number; fields: DatasetField[]; ds: any }) {
  const [result, setResult] = useState<any>(null);
  const [running, setRunning] = useState(false);
  // ES 型：取数形状由正文决定，不存在"选哪些维度/度量重新聚合"这回事（传了会被护栏显式拒）。
  // 判据必须用后端下发的**执行通道位** `is_es`：历史缺陷是拿 `source_type === 'es'` 判，
  // ES 数据源化后该标记恒为 false → 预览照旧发 dimensions/measures，被 ES 护栏显式拒
  // （同一份数据在看板上能出、在这一页必失败）。
  const isEs = !!ds?.is_es;
  // 内置通道退役后没有“索引范围徽标 / 不施加行列级策略”那一套了：ES 集就挂在 ES 数据源上，
  // 与其余源同一个治理口（列级/行级编译进待执行 SQL），“选哪个源”也照常可用。
  // 图表样式走 UI 字模库主题包管线(useChartDesign): 预览与看板/Chat 同观感
  const { chartStyle } = useChartDesign();
  const presetChart = chartStyle(cleanPreset(ds.chart_preset), ds.chart_type);
  const dims = fields.filter(f => f.role === 'dimension');
  const measures = fields.filter(f => f.role === 'measure');
  const [dimensions, setDimensions] = useState<string[]>(dims.slice(0, 1).map(f => f.field));
  const [measuresSel, setMeasuresSel] = useState<string[]>(measures.slice(0, 2).map(f => f.field));
  // 多源数据集：预览可指定跑哪个源（只给业务名，服务端在可用范围∩已授权集内解析）。
  // 不选 = 交服务端定源（唯一交集或执行源），多义时报错文案会列出候选。
  const scopeNames: string[] = (ds.usable_now && ds.usable_now.length
    ? ds.usable_now : ds.available_source_names) || [];
  const [sourceSel, setSourceSel] = useState<string>('');

  // 字段显示格式（服务端随取数结果下发）在进渲染面前归一一次：下方明细表直接读它，
  // 图表预览把它随 data 交给 DashboardChart（那边内部走同一实现）。
  // 不在这里归一就会出现"图上是 2026-10-12、同一屏的表里还挂着 T00:00:00.000"。
  const preview = useMemo(() => applyFieldFormats({
    columns: result?.columns || [],
    rows: result?.rows || [],
    field_formats: result?.field_formats,
  }), [result]);

  const run = async () => {
    setRunning(true);
    try {
      // 选源入参与其他通道同一形状（服务端在可用范围∩已授权集内解析）；
      // ES 只是不带维度/度量（无派生表，二次聚合不成立）
      const { data } = await client.post(`/datasets/${datasetId}/preview`, {
        ...(isEs ? {} : { dimensions, measures: measuresSel }),
        limit: 100,
        datasource: sourceSel || '',
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
        {!isEs && <div className="min-w-40">
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
        </div>}
        {!isEs && <div className="min-w-40">
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
        </div>}
        {/* 选源与其余通道同一条路：服务端把 datasource 业务名在“可用范围 ∩ 已授权集”内解成生效源
            （ES 也一样，见 query_dataset 的 resolve_effective_source），多源时才出现这一项 */}
        {scopeNames.length > 1 && (
          <div className="min-w-44">
            <Label className="text-xs">执行数据源</Label>
            <Select value={sourceSel || '__auto__'}
              onValueChange={v => setSourceSel(v === '__auto__' ? '' : v)}>
              <SelectTrigger className="h-8 text-xs" aria-label="执行数据源"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="__auto__">自动（服务端定源）</SelectItem>
                {scopeNames.map(n => <SelectItem key={n} value={n}>{n}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        )}
        <Button size="sm" onClick={run} disabled={running}>
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Play className="h-3.5 w-3.5 mr-1" />}
          预览取数
        </Button>
      </div>
      {isEs && (
        <p className="text-[10px] text-muted-foreground">
          ES 执行通道直接跑已保存正文（不做维度/度量重聚合）。
          列级脱敏/屏蔽与行级策略已编译进待执行 SQL（与其他数据源同口径）。
          超时预算按数据源配（「数据源」页高级配置），时间留空时按数据集声明的滚动窗取。
        </p>
      )}

      {result && (
        <>
          {/* 溯源标注：排查时能分辨这次走的是哪条执行通道（文案单一事实源 = 服务端 governance） */}
          {result.execution_mode === 'es' && (
            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              {/* 标注文案只从服务端下发的 governance 读：前端不得拿 `execution_mode==='es'` 自行
                  编“未施加列级/行级策略”——内置通道退役后那句话已不成立。 */}
              <Badge variant="outline" className="text-[10px]"
                title={result.governance?.note || ''}>
                ES 数据源 · 已施加列级/行级策略
              </Badge>
              <span title={result.governance?.note || ''}>
                范围: {(result.es_indexes || []).join(', ') || result.es_scope || '-'}
                {(result.es_indexes || []).length > 0 && `・命中索引: ${result.es_indexes.join(', ')}`}
                ・本次 {result.row_count ?? (result.rows || []).length} 行
              </span>
            </div>
          )}
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
                    data={preview}
                    config={presetChart.config}
                    visual={presetChart.visual}
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
          <div className="border rounded-lg overflow-auto max-h-[60vh]">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 sticky top-0">
                <tr>{preview.columns.map((c: string) => <th key={c} className="text-left px-2.5 py-1.5 font-medium whitespace-nowrap">{c}</th>)}</tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 100).map((row: any, ri: number) => (
                  <tr key={ri} className="border-t">
                    {preview.columns.map((c: string) => (
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

function ScopesPanel({ datasetId, fields, canManage, ds }: {
  datasetId: number; fields: DatasetField[]; canManage: boolean; ds?: any;
}) {
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  // ES 执行通道不支持行级范围（注入靠子查询包裹，ES SQL 不成立）。
  // 这里不给"能配但一取数就报错"的入口，而是直接说明为何不可用（不开空承诺）；
  // 行级隔离在 ES 上仍然可用——那是数据源层的「行级策略」（治理层把谓词 AND 进顶层 WHERE），
  // 不是这一页的数据集级范围。判据用 `is_es`（与取数分派同源）。
  const isEs = !!ds?.is_es;

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

  // ES 型：不渲染任何可编辑入口（配了也不会生效，保存还会被后端拒），只给原因
  // 文案与后端 `set_scopes` 的拒写理由同口径（不得把“无列级策略可改写”这种旧内置通道说法留在这里）
  if (isEs) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          ES 型数据集不支持本页的“行级范围”：该能力靠将正文包成子查询后拼 WHERE 注入，
          而 ES SQL 无派生表，不接受这种形状，配了也不会生效。
        </p>
        <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-700 dark:text-amber-400">
          要收窄数据请把条件直接写进查询正文（如 WHERE "region" = 'cn'）；
          要按角色隔离行可见性，请到「数据源」页给该 ES 数据源配「行级策略」
          （与其他数据源同一条治理口，谓词会 AND 进待执行 SQL 的顶层 WHERE）。
        </p>
      </div>
    );
  }

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


/**
 * 入参（看板参数）面板：把数据集声明的 `${}` 入参翻成"在看板上会长成什么样"。
 *
 * 为什么这一页必须有：入参声明过去只能靠建模人员手打 JSON，看板侧只能猜名字对齐，
 * 猜错就静默按数据集默认值出数（用户以为筛过了）。这里提供唯一的**改判入口**：
 *  - 「看板显示名」：装配时给用户看的词；
 *  - 「归属逻辑参数」：不同表叫法不同（如 site / region）时并成一个控件的登记处，
 *    留空 = 由平台按正文形状推断（推断结果一并显示，避免"改了不知道有没有生效"）。
 *
 * 形状判定不在这里重做：`ds.param_plan` 由服务端 `semantics.param_plan` 给出，
 * 与看板取数翻译同一份口径。
 */
export function ParamsPanel({ ds, datasetId, canManage, onSaved }: {
  ds: any; datasetId: number; canManage: boolean; onSaved: () => void;
}) {
  const plan: any[] = ds?.param_plan || [];
  const [saving, setSaving] = useState(false);
  const [candidates, setCandidates] = useState<string[]>([]);
  const [dashKeys, setDashKeys] = useState<Record<string, string>>({});
  const [groupLabels, setGroupLabels] = useState<Record<string, string>>({});
  // 入参值域（''=普通业务值 / 'site'=值是站点）：取数时要不要把站点换成该字段的实际写法，
  // 唯一判据就登记在这里（不靠变量名猜），所以必须给一个可见可改的入口
  const [domains, setDomains] = useState<Record<string, string>>({});

  useEffect(() => {
    // 候选逻辑参数清单：聚合所有可见数据集已用的键（只做聚合，不重做形状判定）
    client.get('/datasets/').then(({ data }) => {
      const keys = new Set<string>();
      (data.items || []).forEach((row: any) => (row.param_summary || []).forEach((p: any) => keys.add(p.key)));
      setCandidates([...keys].sort((a, b) => a.localeCompare(b, 'zh-CN')));
    }).catch(() => setCandidates([]));
  }, []);

  // 编辑态跟随服务端计划：切换数据集或重新加载后不残留上一份草稿
  useEffect(() => {
    const keys: Record<string, string> = {};
    const labels: Record<string, string> = {};
    const doms: Record<string, string> = {};
    for (const g of plan) {
      labels[g.key] = (g.targets || []).some((t: any) => t.label_declared) ? String(g.label || '') : '';
      for (const t of g.targets || []) {
        keys[t.var] = String(t.dash_key || '');
        doms[t.var] = String(t.value_domain || '');
      }
    }
    setDashKeys(keys);
    setGroupLabels(labels);
    setDomains(doms);
  }, [ds]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = useMemo(() => {
    let n = 0;
    for (const g of plan) {
      const declared = (g.targets || []).some((t: any) => t.label_declared) ? String(g.label || '') : '';
      if ((groupLabels[g.key] || '') !== declared) n += 1;
      for (const t of g.targets || []) {
        if ((dashKeys[t.var] || '') !== String(t.dash_key || '')) n += 1;
        if ((domains[t.var] || '') !== String(t.value_domain || '')) n += 1;
      }
    }
    return n;
  }, [plan, dashKeys, groupLabels, domains]);

  if (!plan.length) {
    return <div className="rounded-md border p-4 text-xs text-muted-foreground">
      该数据集没有声明入参。若正文里写了 <code className="font-mono">${'{'}参数名{'}'}</code>，
      请声明对应入参，否则看板无法为它提供筛选，取数会按正文默认值执行。
    </div>;
  }

  const save = async () => {
    setSaving(true);
    try {
      const base = parseVariables(ds.variables);
      for (const g of plan) {
        const label = (groupLabels[g.key] || '').trim();
        for (const t of g.targets || []) {
          const decl = base[t.var];
          if (!decl || typeof decl !== 'object') continue;
          const key = (dashKeys[t.var] || '').trim();
          if (key) decl.dash_key = key; else delete decl.dash_key;
          // 显示名属于逻辑参数整组，不是单个变量：同组每个变量都落同一个词
          if (label) decl.label = label; else delete decl.label;
          const domain = (domains[t.var] || '').trim();
          if (domain) decl.value_domain = domain; else delete decl.value_domain;
        }
      }
      await client.put(`/datasets/${datasetId}`, { variables: JSON.stringify(base) });
      toast.success('入参归属已保存');
      onSaved();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return <div className="space-y-3">
    <div className="flex items-center justify-between gap-2">
      <p className="text-xs text-muted-foreground">
        这些入参决定看板上会出现哪些筛选控件。控件在看板「引入数据集」时按这里的声明自动装配，
        口径仍由数据集正文负责（看板不能改正文）。
      </p>
      {canManage && <Button size="sm" onClick={save} disabled={saving || !dirty}>
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Save className="h-3.5 w-3.5 mr-1" />}
        保存入参归属{dirty ? `（${dirty} 处改动）` : ''}
      </Button>}
    </div>

    {plan.map((g: any) => (
      <div key={g.key} className="rounded-md border p-3 space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="text-sm font-medium">{g.label}</div>
          <div className="text-[11px] text-muted-foreground">
            {KIND_LABELS[g.kind] || g.kind}
            {' · '}{VALUE_TYPE_LABELS[g.value_type] || g.value_type || '形态待定'}
            {' · '}{g.control
              ? `看板控件：${CHART_TYPES.find(c => c.value === g.control)?.label || CONTROL_LABELS[g.control]}`
              : '不自动建控件'}
          </div>
        </div>

        <ul className="space-y-1">
          {(g.targets || []).map((t: any) => (
            <li key={t.var} className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-mono w-40 shrink-0">{t.var}</span>
              <span className="w-32 shrink-0 text-muted-foreground">{ROLE_LABELS[t.role] || t.role}</span>
              {canManage
                ? <Input className="h-6 w-40 text-xs font-mono" list="adh-param-key-candidates"
                    value={dashKeys[t.var] || ''} aria-label={`归属逻辑参数 ${t.var}`} placeholder={g.key}
                    onChange={e => setDashKeys(v => ({ ...v, [t.var]: e.target.value }))} />
                : <span className="font-mono text-muted-foreground">{g.key}</span>}
              {!(dashKeys[t.var] || '').trim() && (
                <span className="text-[10px] text-muted-foreground">留空 = 按正文形状归到 {g.key}</span>
              )}
              {canManage ? (
                <Select value={domains[t.var] || 'none'} onValueChange={(v) => setDomains((prev) => ({ ...prev, [t.var]: v === 'none' ? '' : v }))}>
                  <SelectTrigger className="h-6 w-36 text-xs" aria-label={`入参值域 ${t.var}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">值域：普通业务值</SelectItem>
                    <SelectItem value="site">值域：站点</SelectItem>
                  </SelectContent>
                </Select>
              ) : ((t.value_domain || '') === 'site' && (
                <Badge variant="secondary" className="text-[10px]">值域：站点</Badge>
              ))}
            </li>
          ))}
        </ul>

        {canManage && (
          <label className="flex flex-wrap items-center gap-2 text-xs">
            <span className="shrink-0 text-muted-foreground">看板显示名</span>
            <Input className="h-6 w-56 text-xs"
              value={groupLabels[g.key] || ''} aria-label={`看板显示名 ${g.key}`} placeholder={g.suggested_label || g.key}
              onChange={e => setGroupLabels(v => ({ ...v, [g.key]: e.target.value }))} />
            <span className="text-[10px] text-muted-foreground">装配控件与参数清单上给用户看的词</span>
          </label>
        )}

        {(g.conflicts || []).length > 0 && (
          <div role="alert" className="rounded-md border border-amber-300 bg-amber-50/60 px-2 py-1.5 text-[11px] text-amber-900">
            <ul className="list-disc pl-4 space-y-0.5">
              {g.conflicts.map((c: string) => <li key={c}>{c}</li>)}
            </ul>
          </div>
        )}
      </div>
    ))}
    {/* 归属逻辑参数的候选来自全平台清单聚合：可以选已有键，也可以新起一个规范名 */}
    <datalist id="adh-param-key-candidates">{candidates.map(k => <option key={k} value={k} />)}</datalist>
  </div>;
}

/** 保留原声明字段（type/default/values…），只改 dash_key/label：入参声明是数据集的口径载体，不能整份重写 */
function parseVariables(raw: any): Record<string, any> {
  let v = raw;
  if (typeof v === 'string') { try { v = JSON.parse(v || '{}'); } catch { v = {}; } }
  if (Array.isArray(v)) {
    return v.reduce((acc: Record<string, any>, item: any) => {
      if (item && item.name) acc[String(item.name)] = { ...item };
      return acc;
    }, {});
  }
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.entries(v).map(([k, val]) =>
      [k, (val && typeof val === 'object') ? { ...(val as any) } : { type: String(val) }]));
  }
  return {};
}
