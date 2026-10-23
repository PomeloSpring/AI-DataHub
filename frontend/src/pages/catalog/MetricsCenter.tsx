import { useState, useEffect, useCallback, useMemo } from 'react';
import { metricsApi } from '@/api/metrics';
import type { DictFilters, MetricListQuery } from '@/api/metrics';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { SearchSelect, type SearchSelectOption } from '@/components/ui/search-select';
import { toast } from '@/lib/toast';
import {
  Plus,
  Search,
  Edit,
  Trash2,
  RefreshCw,
  BarChart3,
  Layers,
  Filter,
  CheckCircle2,
} from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import DimensionsDictTab from './DimensionsDictTab';
import {
  AGG_TYPE_OPTIONS,
  DICT_UNSET,
  METRIC_TYPE_OPTIONS,
  aggLabel,
  metricCategoryLabel,
  objectLabel,
  withCount,
} from '@/lib/dictLabels';

interface Metric {
  id: number;
  name: string;
  display_name: string;
  description?: string;
  metric_type: string;
  calculation_type: string;
  expression?: string;
  unit?: string;
  target_table?: string;
  target_column?: string;
  granularity?: string;
  owner?: string;
  tags?: string[];
  bound_object_key?: string;
  aliases?: string | string[];
  certified?: number;
  is_active?: number;
  created_at: string;
  updated_at: string;
}

interface Dimension {
  id: number;
  metric_id: number;
  name: string;
  display_name: string;
  column_name: string;
  data_type: string;
  description?: string;
}

interface MetricFormData {
  name: string;
  display_name: string;
  description: string;
  metric_type: string;
  calculation_type: string;
  expression: string;
  unit: string;
  target_table: string;
  target_column: string;
  granularity: string;
  bound_object_key: string;
  aliases: string;   // 逗号分隔, 提交时拆为数组(后端存 JSON 列)
}

/** 解析后端 aliases(JSON 字符串或数组) → 逗号串(输入框展示用) */
function aliasesToText(v: unknown): string {
  if (Array.isArray(v)) return v.join(', ');
  if (typeof v === 'string' && v.trim()) {
    try {
      const arr = JSON.parse(v);
      return Array.isArray(arr) ? arr.join(', ') : v;
    } catch { return v; }
  }
  return '';
}

/** 三元组预览(只读): 所见 = 所写 = 图谱投影, 与 to_rdf/字典行为同一主语结构 */
function metricRdfPreview(f: MetricFormData): string {
  const subj = `adh:metric:${f.display_name || f.name || '?'}`;
  const lines: string[] = [`${subj} a adh:Metric .`];
  if (f.bound_object_key) lines.push(`${subj} adh:boundToObject adh:obj:${f.bound_object_key} .`);
  const als = aliasesToText(f.aliases).split(',').map((s) => s.trim()).filter(Boolean);
  als.forEach((a) => lines.push(`${subj} skos:altLabel "${a}" .`));
  if (f.calculation_type) lines.push(`${subj} adh:aggregation "${f.calculation_type.toUpperCase()}" .`);
  if (f.unit) lines.push(`${subj} adh:unit "${f.unit}" .`);
  if (f.description) lines.push(`${subj} rdfs:comment "${f.description}" .`);
  lines.push(`# formula 仅存服务端, 不对外/不上云`);
  return lines.join('\n');
}

interface DimensionFormData {
  name: string;
  display_name: string;
  column_name: string;
  data_type: string;
  description: string;
}

const METRIC_TYPE_COLORS: Record<string, string> = {
  basic: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  derived: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  composite: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
};

const emptyMetricForm: MetricFormData = {
  name: '',
  display_name: '',
  description: '',
  metric_type: 'basic',
  calculation_type: 'sum',
  expression: '',
  unit: '',
  target_table: '',
  target_column: '',
  granularity: '',
  bound_object_key: '',
  aliases: '',
};

const emptyDimensionForm: DimensionFormData = {
  name: '',
  display_name: '',
  column_name: '',
  data_type: 'string',
  description: '',
};

export default function MetricsCenter({
  embedded = false,
  modelId,
  view = 'metrics',
  objects,
}: {
  /** 嵌入模型工作区: 隐藏页面标题与字典 Tab 头(由工作区顶层页签控制) */
  embedded?: boolean;
  /** 本体模型作用域: 只列/新建挂在该模型对象上的指标 */
  modelId?: number;
  view?: 'metrics' | 'dimensions';
  /** 模型内对象清单(嵌入时传): 新建指标的"归属对象"引用选择器 */
  objects?: { key: string; display_name: string }[];
} = {}) {
  const [tab, setTab] = useState<'metrics' | 'dimensions'>(view);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  // 加载失败与「确实没有数据」必须可区分：把接口错误吞成空列表等于把缺陷藏起来
  const [loadError, setLoadError] = useState<string | null>(null);
  // 筛选条件: 列表服务端分页, 过滤一律下推 SQL(不能先分页再在前端筛)
  const [search, setSearch] = useState('');
  const [fCategory, setFCategory] = useState<string>('all');
  const [fAgg, setFAgg] = useState<string>('all');
  const [fObject, setFObject] = useState<string>('');
  const [fCertified, setFCertified] = useState<string>('all');
  const [fStatus, setFStatus] = useState<string>('all');
  const [fTarget, setFTarget] = useState<string>('all');
  // 筛选词表(facet): 由后端在全量字典上聚合得出。前端不写死枚举，也拿不到分页外的值，
  // 所以词表加载失败必须显式提示 + 可重试，不能默默只给“全部”把缺陷藏成“字典里没这类值”。
  const [facets, setFacets] = useState<DictFilters | null>(null);
  const [facetsError, setFacetsError] = useState<string | null>(null);

  // Dialog states
  const [formOpen, setFormOpen] = useState(false);
  const [editMetric, setEditMetric] = useState<Metric | null>(null);
  const [formData, setFormData] = useState<MetricFormData>(emptyMetricForm);
  const [saving, setSaving] = useState(false);

  // Detail view
  const [detailMetric, setDetailMetric] = useState<Metric | null>(null);
  const [dimensions, setDimensions] = useState<Dimension[]>([]);
  const [dimensionFormOpen, setDimensionFormOpen] = useState(false);
  const [dimensionForm, setDimensionForm] = useState<DimensionFormData>(emptyDimensionForm);

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Metric | null>(null);

  const loadMetrics = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      // 指标字典是全局唯一口径, 不随工作空间切换(与维度字典同口径), 故不传 workspace_id
      const params: MetricListQuery = { page, size: 20 };
      if (search) params.search = search;
      if (fCategory !== 'all') params.category = fCategory;
      if (fAgg !== 'all') params.agg_type = fAgg;
      if (fObject) params.bound_object_key = fObject;
      if (fCertified !== 'all') params.certified = fCertified;
      if (fStatus !== 'all') params.is_active = fStatus;
      if (fTarget !== 'all') params.has_target_table = fTarget;
      // 模型工作区作用域: 只列 bound_object_key ∈ 本模型对象集的指标
      if (modelId) { params.model_id = modelId; params.scope = 'model'; }
      const res = await metricsApi.list(params);
      const items = res?.data?.items ?? res?.data;
      setMetrics(Array.isArray(items) ? items : []);
      setTotal(res?.data?.total || 0);
    } catch (e: any) {
      // 失败即暴露: 保留可读错误供空态渲染, 不把错误伪装成“暂无指标数据”
      setMetrics([]);
      setTotal(0);
      const msg = e?.response?.data?.detail || e?.message || '未知错误';
      setLoadError(String(msg));
      toast.error(`指标字典加载失败: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [page, search, fCategory, fAgg, fObject, fCertified, fStatus, fTarget, modelId]);

  useEffect(() => {
    loadMetrics();
  }, [loadMetrics]);

  /** 拉筛选词表(全局/模型作用域与列表同口径) */
  const loadFacets = useCallback(async () => {
    setFacetsError(null);
    try {
      const { data } = await metricsApi.filters(
        modelId ? { model_id: modelId, scope: 'model' } : undefined);
      setFacets(data ?? null);
    } catch (e: any) {
      // 降级要显式: 词表缺失时筛选项不可用是可见事实, 不能假装“字典里就这些值”
      setFacets(null);
      setFacetsError(String(e?.response?.data?.detail || e?.message || '未知错误'));
    }
  }, [modelId]);

  useEffect(() => {
    loadFacets();
  }, [loadFacets]);

  const loadDimensions = async (metricId: number) => {
    try {
      const res = await metricsApi.getDimensions(metricId);
      const items = res?.data;
      setDimensions(Array.isArray(items) ? items : []);
    } catch {
      setDimensions([]);
    }
  };

  const handleViewDetail = async (metric: Metric) => {
    setDetailMetric(metric);
    await loadDimensions(metric.id);
  };

  const handleOpenCreate = () => {
    setEditMetric(null);
    setFormData(emptyMetricForm);
    setFormOpen(true);
  };

  const handleOpenEdit = (metric: Metric) => {
    setEditMetric(metric);
    setFormData({
      name: metric.name,
      display_name: metric.display_name,
      description: metric.description || '',
      metric_type: metric.metric_type,
      calculation_type: (metric.calculation_type || 'sum').toLowerCase(),
      expression: metric.expression || '',
      unit: metric.unit || '',
      target_table: metric.target_table || '',
      target_column: metric.target_column || '',
      granularity: metric.granularity || '',
      bound_object_key: metric.bound_object_key || '',
      aliases: aliasesToText(metric.aliases),
    });
    setFormOpen(true);
  };

  const handleSaveMetric = async () => {
    if (!formData.name || !formData.display_name) {
      toast.error('请填写名称和显示名称');
      return;
    }
    setSaving(true);
    try {
      const payload: any = { ...formData,
        aliases: formData.aliases.split(',').map((s) => s.trim()).filter(Boolean) };
      if (editMetric) {
        await metricsApi.update(editMetric.id, payload);
        toast.success('指标已更新');
      } else {
        await metricsApi.create(payload);
        toast.success('指标已创建');
      }
      setFormOpen(false);
      loadMetrics();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await metricsApi.delete(deleteTarget.id);
      toast.success('指标已删除');
      setDeleteTarget(null);
      loadMetrics();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleAddDimension = async () => {
    if (!detailMetric || !dimensionForm.name || !dimensionForm.column_name) {
      toast.error('请填写维度名称和列名');
      return;
    }
    try {
      await metricsApi.addDimension(detailMetric.id, dimensionForm);
      toast.success('维度已添加');
      setDimensionFormOpen(false);
      setDimensionForm(emptyDimensionForm);
      await loadDimensions(detailMetric.id);
    } catch {
      toast.error('添加维度失败');
    }
  };

  // ── 筛选词表 → 下拉选项(值与计数均来自后端全量聚合) ───────────────────
  const categoryOptions = facets?.metrics?.categories || [];
  const aggOptions = facets?.metrics?.agg_types || [];
  const objectNames = useMemo(
    () => Object.fromEntries((facets?.objects || []).map((o) => [o.key, o.display_name] as const)),
    [facets]);
  const objectOptions = useMemo<SearchSelectOption[]>(() => {
    const list: SearchSelectOption[] = (facets?.objects || [])
      .filter((o) => (o.metric_count || 0) > 0)
      .map((o) => ({
        value: o.key,
        label: o.display_name || o.key,
        description: `${o.key} · 指标 ${o.metric_count} 条`,
      }));
    const unbound = facets?.metrics?.unbound_objects || 0;
    if (unbound > 0) {
      list.push({ value: DICT_UNSET, label: '未归属', description: `待归属指标 ${unbound} 条` });
    }
    return list;
  }, [facets]);
  // 词表未就绪(加载中或加载失败)时下拉不可用 —— 选项只能来自 facet, 不得凑数
  const facetsReady = !!facets && !facetsError;
  // 新建/编辑表单的分类候选: 表单固定口径 + 字典已有真实分类(+当前值兑底)
  const categoryFormOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const o of METRIC_TYPE_OPTIONS) seen.set(o.value, o.label);
    for (const f of facets?.metrics?.categories || []) {
      if (f.value !== DICT_UNSET) seen.set(f.value, metricCategoryLabel(f.value));
    }
    const current = (formData.metric_type || '').trim();
    if (current && !seen.has(current)) seen.set(current, metricCategoryLabel(current));
    return [...seen.entries()];
  }, [facets, formData.metric_type]);
  const hasFilters = !!(search || fCategory !== 'all' || fAgg !== 'all' || fObject
    || fCertified !== 'all' || fStatus !== 'all' || fTarget !== 'all');
  const resetFilters = () => {
    setSearch('');
    setFCategory('all');
    setFAgg('all');
    setFObject('');
    setFCertified('all');
    setFStatus('all');
    setFTarget('all');
    setPage(1);
  };

  return (
    <div className={embedded ? 'space-y-4' : 'p-6 space-y-4'}>
      {!embedded && (
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">指标中心</h1>
          <p className="text-muted-foreground text-sm mt-1">指标字典与维度字典的唯一管理入口（全局口径，不随工作空间切换）</p>
        </div>
      </div>
      )}

      <Tabs value={tab} onValueChange={(v) => setTab(v as 'metrics' | 'dimensions')}>
        {!embedded && (
        <TabsList>
          <TabsTrigger value="metrics" className="flex items-center gap-2">
            <BarChart3 className="w-4 h-4" /> 指标字典
          </TabsTrigger>
          <TabsTrigger value="dimensions" className="flex items-center gap-2">
            <Layers className="w-4 h-4" /> 维度字典
          </TabsTrigger>
        </TabsList>
        )}

        <TabsContent value="metrics" className="space-y-4">
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={loadMetrics}>
          <RefreshCw className="w-4 h-4 mr-1" />
          刷新
        </Button>
        <Button size="sm" onClick={handleOpenCreate}>
          <Plus className="w-4 h-4 mr-1" />
          新建指标
        </Button>
      </div>

      {/* Filters: 选项一律来自 GET /metrics/filters（在全量字典上聚合的真实值域）。
          列表是服务端分页的，拿“当前页出现过的值”凑下拉会漏值；写死枚举更糟——
          历史缺陷就是写死 basic/derived/composite 当 category 值域，与库内 可观测/客户/案例…
          无交集，选任何一项都查空，成了“能点但永远无结果”的死筛选。 */}
      <div className="flex flex-wrap items-center gap-3 p-3 border rounded-lg bg-muted/30">
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Filter className="w-4 h-4" /> 筛选
        </div>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="搜索名称/英文名/别名/口径..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="pl-8 h-8 w-56"
          />
        </div>
        <Select value={fCategory} disabled={!facetsReady}
                onValueChange={(v) => { setFCategory(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-36" aria-label="指标分类"><SelectValue placeholder="指标分类" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部分类</SelectItem>
            {categoryOptions.map((f) => (
              <SelectItem key={f.value} value={f.value}>
                {withCount(metricCategoryLabel(f.value), f.count)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={fAgg} disabled={!facetsReady}
                onValueChange={(v) => { setFAgg(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-36" aria-label="计算方式"><SelectValue placeholder="计算方式" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部计算方式</SelectItem>
            {aggOptions.map((f) => (
              <SelectItem key={f.value} value={f.value}>
                {withCount(aggLabel(f.value), f.count)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <SearchSelect
          value={fObject}
          onValueChange={(v) => { setFObject(v); setPage(1); }}
          options={objectOptions}
          placeholder="归属对象"
          searchPlaceholder="搜索对象名称..."
          emptyText="字典中暂无已挂接对象"
          loading={!facets && !facetsError}
          error={facetsError}
          onRetry={loadFacets}
          disabled={!facetsReady}
          className="h-8 w-44 text-xs"
          ariaLabel="归属对象"
        />
        <Select value={fCertified} disabled={!facetsReady}
                onValueChange={(v) => { setFCertified(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-32" aria-label="认证状态"><SelectValue placeholder="认证状态" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部认证状态</SelectItem>
            <SelectItem value="1">{`已认证（${facets?.metrics?.certified ?? 0}）`}</SelectItem>
            <SelectItem value="0">{`草稿（${facets?.metrics?.draft ?? 0}）`}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={fStatus} disabled={!facetsReady}
                onValueChange={(v) => { setFStatus(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-32" aria-label="口径状态"><SelectValue placeholder="口径状态" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部口径状态</SelectItem>
            <SelectItem value="1">{`启用（${facets?.metrics?.active ?? 0}）`}</SelectItem>
            <SelectItem value="0">{`已停用（${facets?.metrics?.inactive ?? 0}）`}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={fTarget} disabled={!facetsReady}
                onValueChange={(v) => { setFTarget(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-40" aria-label="物理表"><SelectValue placeholder="物理表" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部物理表</SelectItem>
            <SelectItem value="1">{`已派生（${facets?.metrics?.with_target_table ?? 0}）`}</SelectItem>
            <SelectItem value="0">{`未派生（${facets?.metrics?.without_target_table ?? 0}）`}</SelectItem>
          </SelectContent>
        </Select>
        {hasFilters && (
          <Button variant="ghost" size="sm" className="h-8 text-xs" onClick={resetFilters}>
            清除筛选
          </Button>
        )}
        <span className="ml-auto text-xs text-muted-foreground">共 {total} 条</span>
      </div>
      {facetsError && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-destructive">
          <span>筛选项加载失败：{facetsError}（分类/计算方式/归属对象等下拉暂不可用，关键词搜索仍可用）</span>
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={loadFacets}>
            <RefreshCw className="w-3 h-3 mr-1" />重试
          </Button>
        </div>
      )}

      {/* Metrics Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">指标名称</th>
              <th className="text-left p-3 font-medium">显示名称</th>
              <th className="text-left p-3 font-medium">分类</th>
              <th className="text-left p-3 font-medium">计算方式</th>
              <th className="text-left p-3 font-medium">单位</th>
              <th className="text-left p-3 font-medium">别名</th>
              <th className="text-left p-3 font-medium">归属对象</th>
              <th className="text-left p-3 font-medium">目标表</th>
              <th className="text-left p-3 font-medium">负责人</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={10} className="p-8 text-center text-muted-foreground">
                  加载中...
                </td>
              </tr>
            ) : metrics.length === 0 ? (
              <tr>
                <td colSpan={10} className="p-8 text-center text-muted-foreground">
                  {loadError ? (
                    <span className="text-destructive">指标字典加载失败：{loadError}</span>
                  ) : hasFilters ? (
                    <span>无符合筛选条件的指标 · <button
                      className="underline underline-offset-2 hover:text-foreground" onClick={resetFilters}>清除筛选</button></span>
                  ) : (
                    '暂无指标数据'
                  )}
                </td>
              </tr>
            ) : (
              metrics.map((m) => (
                <tr key={m.id} className="border-t hover:bg-muted/30 cursor-pointer" onClick={() => handleViewDetail(m)}>
                  <td className="p-3">
                    <div className="flex items-center gap-2">
                      <BarChart3 className="w-4 h-4 text-muted-foreground" />
                      <span className="font-medium">{m.name}</span>
                      {!!m.certified && (
                        <span title="已认证口径"><CheckCircle2 className="w-3.5 h-3.5 text-green-500" /></span>
                      )}
                      {m.is_active === 0 && (
                        <Badge variant="outline" className="text-[11px] font-normal text-muted-foreground">已停用</Badge>
                      )}
                    </div>
                  </td>
                  <td className="p-3 text-muted-foreground">{m.display_name}</td>
                  <td className="p-3">
                    <Badge variant="outline" className={METRIC_TYPE_COLORS[m.metric_type] || ''}>
                      {metricCategoryLabel(m.metric_type)}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <Badge variant="outline">{aggLabel(m.calculation_type)}</Badge>
                  </td>
                  <td className="p-3 text-muted-foreground">{m.unit || '-'}</td>
                  <td className="p-3">
                    <div className="flex flex-wrap gap-1 max-w-[180px]">
                      {aliasesToText(m.aliases).split(',').map((s) => s.trim()).filter(Boolean).map((a) => (
                        <Badge key={a} variant="outline" className="text-[11px] font-normal">{a}</Badge>
                      ))}
                    </div>
                  </td>
                  <td className="p-3">
                    {m.bound_object_key
                      ? (
                        /* 显示用对象业务名, key 降到 title 供排查(ui-resource-display) */
                        <Badge variant="secondary" className="text-xs max-w-[160px] truncate"
                               title={m.bound_object_key}>
                          {objectLabel(objectNames, m.bound_object_key)}
                        </Badge>
                      )
                      : <span className="text-xs text-amber-500">未归属</span>}
                  </td>
                  <td className="p-3 text-muted-foreground font-mono text-xs">{m.target_table || '-'}</td>
                  <td className="p-3 text-muted-foreground">{m.owner || '-'}</td>
                  <td className="p-3 text-right">
                    <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => handleOpenEdit(m)}>
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => setDeleteTarget(m)}>
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > 20 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          {/* 条数已在筛选栏呈现，这里只报分页位置(两处同一个数字是噪声) */}
          <span>第 {page} / {Math.max(1, Math.ceil(total / 20))} 页</span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              上一页
            </Button>
            <Button variant="outline" size="sm" disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>
              下一页
            </Button>
          </div>
        </div>
      )}
        </TabsContent>

        <TabsContent value="dimensions" className="space-y-4">
          <DimensionsDictTab
            modelId={modelId}
            scope={modelId ? 'model' : undefined}
            facets={facets}
            facetsError={facetsError}
            onRetryFacets={loadFacets}
          />
        </TabsContent>
      </Tabs>

      {/* Create/Edit Metric Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editMetric ? '编辑指标' : '新建指标'}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">指标名称 *</label>
              <Input
                placeholder="如: gmv"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">显示名称 *</label>
              <Input
                placeholder="如: 交易总额"
                value={formData.display_name}
                onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
              />
            </div>
            <div className="col-span-2 space-y-2">
              <label className="text-sm font-medium">描述</label>
              <Input
                placeholder="指标的业务含义说明"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">指标类型</label>
              {/* 候选 = 表单固定口径 + 字典已有分类(facet)，否则存量分类(可观测/客户…)
                  在编辑弹窗里渲染成空白，看起来像“类型被清空了” */}
              <Select value={formData.metric_type} onValueChange={(v) => setFormData({ ...formData, metric_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {categoryFormOptions.map(([value, label]) => (
                    <SelectItem key={value} value={value}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">计算方式</label>
              <Select value={formData.calculation_type} onValueChange={(v) => setFormData({ ...formData, calculation_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AGG_TYPE_OPTIONS.map((t) => (
                    <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="col-span-2 space-y-2">
              <label className="text-sm font-medium">计算表达式</label>
              <Input
                placeholder="如: SUM(amount) 或 COUNT(DISTINCT user_id)"
                value={formData.expression}
                onChange={(e) => setFormData({ ...formData, expression: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">单位</label>
              <Input
                placeholder="如: 元, 次, %"
                value={formData.unit}
                onChange={(e) => setFormData({ ...formData, unit: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">别名（多个用逗号分隔）</label>
              <Input
                placeholder="如: 病例数, total_cases — 语义查询按别名自动解析"
                value={formData.aliases}
                onChange={(e) => setFormData({ ...formData, aliases: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">粒度</label>
              <Input
                placeholder="如: daily, hourly"
                value={formData.granularity}
                onChange={(e) => setFormData({ ...formData, granularity: e.target.value })}
              />
            </div>
            {/* 归属对象(模型工作区内): 引用选择器, 只能选本模型对象 —— 不产生悬空/跨模型挂接 */
            }
            {embedded && objects && objects.length > 0 && (
            <div className="space-y-2">
              <label className="text-sm font-medium">归属对象 *</label>
              <Select
                value={formData.bound_object_key || undefined}
                onValueChange={(v) => setFormData({ ...formData, bound_object_key: v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择本模型内的业务对象" />
                </SelectTrigger>
                <SelectContent>
                  {objects.map((o) => (
                    <SelectItem key={o.key} value={o.key}>{o.display_name || o.key} ({o.key})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            )}
            <div className="space-y-2">
              <label className="text-sm font-medium">目标表</label>
              <Input
                placeholder="数据来源表名"
                value={formData.target_table}
                onChange={(e) => setFormData({ ...formData, target_table: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">目标列</label>
              <Input
                placeholder="数据来源列名"
                value={formData.target_column}
                onChange={(e) => setFormData({ ...formData, target_column: e.target.value })}
              />
            </div>
          </div>
          {/* 三元组预览: 所见=所写=图谱投影 */}
          <div className="mt-2">
            <label className="text-sm font-medium text-muted-foreground">三元组预览（保存后将形成以下字典/图节点事实）</label>
            <pre className="mt-1 p-3 rounded-md bg-muted/50 border text-[11px] font-mono whitespace-pre-wrap leading-relaxed">
              {metricRdfPreview(formData)}
            </pre>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSaveMetric} disabled={saving}>
              {saving ? '保存中...' : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Metric Detail Dialog */}
      <Dialog open={!!detailMetric} onOpenChange={(open) => { if (!open) setDetailMetric(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BarChart3 className="w-5 h-5" />
              {detailMetric?.display_name}
              <Badge variant="outline" className={METRIC_TYPE_COLORS[detailMetric?.metric_type || ''] || ''}>
                {metricCategoryLabel(detailMetric?.metric_type)}
              </Badge>
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div><span className="text-muted-foreground">名称：</span>{detailMetric?.name}</div>
              <div><span className="text-muted-foreground">计算方式：</span>{aggLabel(detailMetric?.calculation_type)}</div>
              <div><span className="text-muted-foreground">单位：</span>{detailMetric?.unit || '-'}</div>
              <div><span className="text-muted-foreground">粒度：</span>{detailMetric?.granularity || '-'}</div>
              <div><span className="text-muted-foreground">目标表：</span><span className="font-mono text-xs">{detailMetric?.target_table || '-'}</span></div>
              <div><span className="text-muted-foreground">目标列：</span><span className="font-mono text-xs">{detailMetric?.target_column || '-'}</span></div>
              {detailMetric?.expression && (
                <div className="col-span-2">
                  <span className="text-muted-foreground">表达式：</span>
                  <code className="ml-1 px-2 py-0.5 bg-muted rounded text-xs">{detailMetric.expression}</code>
                </div>
              )}
              {detailMetric?.description && (
                <div className="col-span-2">
                  <span className="text-muted-foreground">描述：</span>{detailMetric.description}
                </div>
              )}
            </div>

            {/* Dimensions */}
            <div className="border-t pt-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium flex items-center gap-2">
                  <Layers className="w-4 h-4" />
                  维度列表
                </h3>
                <Button variant="outline" size="sm" onClick={() => { setDimensionForm(emptyDimensionForm); setDimensionFormOpen(true); }}>
                  <Plus className="w-4 h-4 mr-1" />
                  添加维度
                </Button>
              </div>
              {dimensions.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">暂无维度</p>
              ) : (
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="text-left p-2 font-medium">维度名</th>
                        <th className="text-left p-2 font-medium">显示名</th>
                        <th className="text-left p-2 font-medium">列名</th>
                        <th className="text-left p-2 font-medium">类型</th>
                        <th className="text-left p-2 font-medium">描述</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dimensions.map((d) => (
                        <tr key={d.id} className="border-t">
                          <td className="p-2 font-medium">{d.name}</td>
                          <td className="p-2 text-muted-foreground">{d.display_name}</td>
                          <td className="p-2 font-mono text-xs">{d.column_name}</td>
                          <td className="p-2 text-muted-foreground">{d.data_type}</td>
                          <td className="p-2 text-muted-foreground">{d.description || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Add Dimension Dialog */}
      <Dialog open={dimensionFormOpen} onOpenChange={setDimensionFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>添加维度</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">维度名称 *</label>
              <Input
                placeholder="如: region"
                value={dimensionForm.name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">显示名称</label>
              <Input
                placeholder="如: 地区"
                value={dimensionForm.display_name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, display_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">列名 *</label>
              <Input
                placeholder="数据库中的列名"
                value={dimensionForm.column_name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, column_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">数据类型</label>
              <Select value={dimensionForm.data_type} onValueChange={(v) => setDimensionForm({ ...dimensionForm, data_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="string">字符串</SelectItem>
                  <SelectItem value="int">整数</SelectItem>
                  <SelectItem value="float">浮点数</SelectItem>
                  <SelectItem value="date">日期</SelectItem>
                  <SelectItem value="datetime">日期时间</SelectItem>
                  <SelectItem value="boolean">布尔值</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">描述</label>
              <Input
                placeholder="维度说明"
                value={dimensionForm.description}
                onChange={(e) => setDimensionForm({ ...dimensionForm, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDimensionFormOpen(false)}>取消</Button>
            <Button onClick={handleAddDimension}>添加</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要删除指标 <strong>{deleteTarget?.display_name}</strong> 吗？此操作不可恢复。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
