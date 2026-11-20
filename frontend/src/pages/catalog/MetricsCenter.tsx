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
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import DimensionsDictTab from './DimensionsDictTab';
import MetricFormDialog, { type MetricRecord } from './MetricFormDialog';
import {
  DICT_UNSET,
  METRIC_TAG_COLORS,
  aggLabel,
  aliasText,
  metricCategoryLabel,
  objectLabel,
  tagList,
  withCount,
} from '@/lib/dictLabels';
import { useTranslation } from 'react-i18next';

/** adh_metrics 行（表单/列表/详情共用，形状定义在 MetricFormDialog，避免两处漂移） */
type Metric = MetricRecord;

interface Dimension {
  id: number;
  metric_id: number;
  name: string;
  display_name: string;
  column_name: string;
  data_type: string;
  description?: string;
}

interface DimensionFormData {
  name: string;
  display_name: string;
  column_name: string;
  data_type: string;
  description: string;
}

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
  const { t } = useTranslation('ui');
  const [tab, setTab] = useState<'metrics' | 'dimensions'>(view);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  // 加载失败与「确实没有数据」必须可区分：把接口错误吞成空列表等于把缺陷藏起来
  const [loadError, setLoadError] = useState<string | null>(null);
  // 筛选条件: 列表服务端分页, 过滤一律下推 SQL(不能先分页再在前端筛)
  const [search, setSearch] = useState('');
  const [fCategory, setFCategory] = useState<string>('all');
  const [fTag, setFTag] = useState<string>('all');
  const [fAgg, setFAgg] = useState<string>('all');
  const [fObject, setFObject] = useState<string>('');
  const [fCertified, setFCertified] = useState<string>('all');
  const [fStatus, setFStatus] = useState<string>('all');
  const [fTarget, setFTarget] = useState<string>('all');
  // 筛选词表(facet): 由后端在全量字典上聚合得出。前端不写死枚举，也拿不到分页外的值，
  // 所以词表加载失败必须显式提示 + 可重试，不能默默只给“全部”把缺陷藏成“字典里没这类值”。
  const [facets, setFacets] = useState<DictFilters | null>(null);
  const [facetsError, setFacetsError] = useState<string | null>(null);

  // Dialog states: 口径表单（新建/编辑）由 MetricFormDialog 承载，同一份表单
  // 也给本体对象检查器的「引用」页签复用（两处不得各写一套字段/校验）
  const [formOpen, setFormOpen] = useState(false);
  const [editMetric, setEditMetric] = useState<Metric | null>(null);

  // Detail view
  const [detailMetric, setDetailMetric] = useState<Metric | null>(null);
  const [dimensions, setDimensions] = useState<Dimension[]>([]);
  const [dimensionFormOpen, setDimensionFormOpen] = useState(false);
  const [dimensionForm, setDimensionForm] = useState<DimensionFormData>(emptyDimensionForm);

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Metric | null>(null);
  // 口径状态变更确认（两个方向都要确认）：启用会把口径重新广告给 ChatBI，
  // 停用会把它从取数里拿掉 —— 两者都影响线上答案，不能一点即生效。
  const [statusTarget, setStatusTarget] = useState<{ metric: Metric; next: 0 | 1 } | null>(null);
  const [statusBusy, setStatusBusy] = useState(false);

  const loadMetrics = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      // 指标字典是全局唯一口径, 不随工作空间切换(与维度字典同口径), 故不传 workspace_id
      const params: MetricListQuery = { page, size: pageSize };
      if (search) params.search = search;
      if (fCategory !== 'all') params.category = fCategory;
      if (fTag !== 'all') params.tags = fTag;
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
      const msg = e?.response?.data?.detail || e?.message || t('k_974e748456');
      setLoadError(String(msg));
      toast.error(t('k_5827ba9348', { p0: msg }));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, search, fCategory, fTag, fAgg, fObject, fCertified, fStatus, fTarget, modelId]);

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
      setFacetsError(String(e?.response?.data?.detail || e?.message || t('k_974e748456')));
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
    setFormOpen(true);
  };

  const handleOpenEdit = (metric: Metric) => {
    setEditMetric(metric);
    setFormOpen(true);
  };

  /**
   * 口径状态开关（is_active）：停用 = 不参与语义检索与取数（与 AS-BOT 未发布同义）。
   * 历史上字典行只能被脚本/AS-BOT 写成停用，页面上没有恢复口（只能改库）。
   * 注意：现存量停用行多为 backend/scripts/retire_*_pseudo_metrics.py 主动退役的
   * 行级派生视图/跨对象伪指标（底列已作属性/维度存在），启用前必须人工确认口径，
   * 否则会把“目录挂着、编译用不了”的错答案重新广告给 ChatBI（故启用也走确认）。
   * 只发 {is_active} 部分更新（update_metric 仅写传入字段，不整份覆盖）。
   */
  const applyStatus = async (metric: Metric, next: 0 | 1) => {
    setStatusBusy(true);
    try {
      await metricsApi.update(metric.id, { is_active: next });
      const label = metric.display_name || metric.name;
      toast.success(next
        // 字典行按 is_active 实时读，启用后取数/语义解析即生效；但图谱与云端知识库
        // 只收录生效行，不提醒会造成“已启用但图谱里查不到”的隐形不一致。
        ? t('k_50a21ee45d', { p0: label })
        : t('k_c4ce706c5a', { p0: label }));
      setStatusTarget(null);
      loadMetrics();
    } catch (e: any) {
      // 透传服务端错因（如权限/参数校验），不把失败伪装成“已提交”
      toast.error(e?.response?.data?.detail || t('k_a39e0c3839'));
    } finally {
      setStatusBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await metricsApi.delete(deleteTarget.id);
      toast.success(t('k_6aa57f5472'));
      setDeleteTarget(null);
      loadMetrics();
    } catch {
      toast.error(t('k_acf0664a54'));
    }
  };

  const handleAddDimension = async () => {
    if (!detailMetric || !dimensionForm.name || !dimensionForm.column_name) {
      toast.error(t('k_44803b69c0'));
      return;
    }
    try {
      await metricsApi.addDimension(detailMetric.id, dimensionForm);
      toast.success(t('k_d0d3a2966f'));
      setDimensionFormOpen(false);
      setDimensionForm(emptyDimensionForm);
      await loadDimensions(detailMetric.id);
    } catch {
      toast.error(t('k_74f6165ed7'));
    }
  };

  // ── 筛选词表 → 下拉选项(值与计数均来自后端全量聚合) ───────────────────
  const categoryOptions = facets?.metrics?.categories || [];
  const tagOptions = facets?.metrics?.tags || [];
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
        description: t('k_13bb7be27f', { p0: o.key, p1: o.metric_count }),
      }));
    const unbound = facets?.metrics?.unbound_objects || 0;
    if (unbound > 0) {
      list.push({ value: DICT_UNSET, label: t('k_46a9e40651'), description: t('k_56aacf7c89', { p0: unbound }) });
    }
    return list;
  }, [facets]);
  // 词表未就绪(加载中或加载失败)时下拉不可用 —— 选项只能来自 facet, 不得凑数
  const facetsReady = !!facets && !facetsError;
  // 新建/编辑表单的「归属对象」候选 = **全量**对象（含尚无指标的）。
  // 不可复用 objectOptions —— 它按 metric_count>0 过滤（那是筛选用的），新建时目标对象往往是尚未挂指标的那个。
  // 嵌入态优先用传入的本模型对象（限定不跨模型挂接）；独立页退到 facet 全量对象。
  const formObjectList = useMemo(() => (objects?.length ? objects : (facets?.objects || []).map(o => ({ key: o.key, display_name: o.display_name }))), [objects, facets]);
  const hasFilters = !!(search || fCategory !== 'all' || fTag !== 'all' || fAgg !== 'all' || fObject
    || fCertified !== 'all' || fStatus !== 'all' || fTarget !== 'all');
  const resetFilters = () => {
    setSearch('');
    setFCategory('all');
    setFTag('all');
    setFAgg('all');
    setFObject('');
    setFCertified('all');
    setFStatus('all');
    setFTarget('all');
    setPage(1);
  };

  return (
    <div className={embedded ? 'space-y-4' : 'flex flex-col h-full gap-4'}>
      {!embedded && (
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-2xl font-bold">{t("k_c06c97ef35")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t("k_afd729c98c")}</p>
        </div>
      </div>
      )}

      {/* self-start: Tabs 被改成 flex-col 后, inline-flex 的 TabsList 会被交叉轴拉伸成通栏色带
          (历史表现: 页签跑到页面正中)。与 DagEditor/DagRunDetail 同口径左对齐。 */}
      <Tabs value={tab} onValueChange={(v) => setTab(v as 'metrics' | 'dimensions')} className={embedded ? undefined : 'flex-1 min-h-0 flex flex-col'}>
        {!embedded && (
        <TabsList className="shrink-0 self-start">
          <TabsTrigger value="metrics" className="flex items-center gap-2">
            <BarChart3 className="w-4 h-4" /> {t('k_a652828a2b')}
          </TabsTrigger>
          <TabsTrigger value="dimensions" className="flex items-center gap-2">
            <Layers className="w-4 h-4" /> {t('k_d8fec2efb4')}
          </TabsTrigger>
        </TabsList>
        )}

        <TabsContent value="metrics" className={embedded ? 'space-y-4' : 'flex-1 min-h-0 flex flex-col gap-4'}>
      <div className="flex justify-end gap-2 shrink-0">
        <Button variant="outline" size="sm" onClick={loadMetrics}>
          <RefreshCw className="w-4 h-4 mr-1" />
          {t('k_38108eaa1d')}
        </Button>
        <Button size="sm" onClick={handleOpenCreate}>
          <Plus className="w-4 h-4 mr-1" />
          {t('k_e4f7015bed')}
        </Button>
      </div>

      {/* Filters: 选项一律来自 GET /metrics/filters（在全量字典上聚合的真实值域）。
          列表是服务端分页的，拿“当前页出现过的值”凑下拉会漏值；写死枚举更糟——
          历史缺陷就是写死 basic/derived/composite 当 category 值域，与库内 可观测/客户/案例…
          无交集，选任何一项都查空，成了“能点但永远无结果”的死筛选。
          业务域分类与口径标签分两个下拉（两个正交维度不再挤在同一列/同一个下拉）。 */}
      <div className="flex flex-wrap items-center gap-3 p-3 border rounded-lg bg-muted/30 shrink-0">
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Filter className="w-4 h-4" /> {t('k_dcce9a144a')}
        </div>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder={t('k_576d820446')}
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="pl-8 h-8 w-56"
          />
        </div>
        <Select value={fCategory} disabled={!facetsReady}
                onValueChange={(v) => { setFCategory(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-36" aria-label={t("k_c695bf5821")}><SelectValue placeholder={t("k_c695bf5821")} /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_a92105b3af")}</SelectItem>
            {categoryOptions.map((f) => (
              <SelectItem key={f.value} value={f.value}>
                {withCount(metricCategoryLabel(f.value), f.count)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={fTag} disabled={!facetsReady}
                onValueChange={(v) => { setFTag(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-32" aria-label={t("k_bcfb3e80e0")}><SelectValue placeholder={t("k_bcfb3e80e0")} /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_ffd92f5524")}</SelectItem>
            {tagOptions.map((f) => (
              <SelectItem key={f.value} value={f.value}>
                {withCount(f.value === DICT_UNSET ? t('k_7bde2eeec3') : f.value, f.count)}
              </SelectItem>
            ))}
            {/* 词表只有固定三档：未采纳过标签时也要可筛（计数 = 总数 - 已打标数） */}
            {tagOptions.length === 0 && (
              <SelectItem value={DICT_UNSET}>{t('k_7d74909d16', { p0: facets?.metrics?.total ?? 0 })}</SelectItem>
            )}
          </SelectContent>
        </Select>
        <Select value={fAgg} disabled={!facetsReady}
                onValueChange={(v) => { setFAgg(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-36" aria-label={t("k_e13f097b9b")}><SelectValue placeholder={t("k_e13f097b9b")} /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_f7d485ed18")}</SelectItem>
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
          placeholder={t("k_3d44b0cfee")}
          searchPlaceholder={t('k_916389c59c')}
          emptyText={t('k_22672a86f3')}
          loading={!facets && !facetsError}
          error={facetsError}
          onRetry={loadFacets}
          disabled={!facetsReady}
          className="h-8 w-44 text-xs"
          ariaLabel={t('k_3d44b0cfee')}
        />
        <Select value={fCertified} disabled={!facetsReady}
                onValueChange={(v) => { setFCertified(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-32" aria-label={t("k_ccac8dbb04")}><SelectValue placeholder={t("k_ccac8dbb04")} /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_ee14c7f323")}</SelectItem>
            <SelectItem value="1">{t('k_939e480466', { p0: facets?.metrics?.certified ?? 0 })}</SelectItem>
            <SelectItem value="0">{t('k_7149d49e7f', { p0: facets?.metrics?.draft ?? 0 })}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={fStatus} disabled={!facetsReady}
                onValueChange={(v) => { setFStatus(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-32" aria-label={t("k_b9dc554215")}><SelectValue placeholder={t("k_b9dc554215")} /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_552e6c1d17")}</SelectItem>
            <SelectItem value="1">{t('k_bf2728bf7b', { p0: facets?.metrics?.active ?? 0 })}</SelectItem>
            <SelectItem value="0">{t('k_a5af738077', { p0: facets?.metrics?.inactive ?? 0 })}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={fTarget} disabled={!facetsReady}
                onValueChange={(v) => { setFTarget(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-40" aria-label={t("k_1b729397db")}><SelectValue placeholder={t("k_1b729397db")} /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("k_da505dba05")}</SelectItem>
            <SelectItem value="1">{t('k_b1f9e7160a', { p0: facets?.metrics?.with_target_table ?? 0 })}</SelectItem>
            <SelectItem value="0">{t('k_61bd197009', { p0: facets?.metrics?.without_target_table ?? 0 })}</SelectItem>
          </SelectContent>
        </Select>
        {hasFilters && (
          <Button variant="ghost" size="sm" className="h-8 text-xs" onClick={resetFilters}>
            {t('k_4bbc4e5fd9')}
          </Button>
        )}
        <span className="ml-auto text-xs text-muted-foreground">{t('k_1d961ef9b0', { p0: total })}</span>
      </div>
      {facetsError && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-destructive shrink-0">
          <span>{t('k_e6385b26bf', { p0: facetsError })}</span>
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={loadFacets}>
            <RefreshCw className="w-3 h-3 mr-1" />{t('k_e2d53a6d3a')}
          </Button>
        </div>
      )}

      {/* Metrics Table */}
      <div className={embedded ? 'border rounded-lg overflow-hidden' : 'flex-1 min-h-0 overflow-auto border rounded-lg'}>
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">{t("k_cea5ea7cea")}</th>
              <th className="text-left p-3 font-medium">{t("k_75ae6a8a7d")}</th>
              <th className="text-left p-3 font-medium">{t("k_8c045213f7")}</th>
              <th className="text-left p-3 font-medium">{t("k_bcfb3e80e0")}</th>
              <th className="text-left p-3 font-medium">{t("k_e13f097b9b")}</th>
              <th className="text-left p-3 font-medium">{t("k_8ec8c8c712")}</th>
              <th className="text-left p-3 font-medium">{t("k_ec537c546d")}</th>
              <th className="text-left p-3 font-medium">{t("k_3d44b0cfee")}</th>
              <th className="text-left p-3 font-medium">{t("k_442be9c497")}</th>
              <th className="text-left p-3 font-medium">{t("k_974d383f36")}</th>
              <th className="text-right p-3 font-medium">{t("k_f3ea6d345e")}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={11} className="p-8 text-center text-muted-foreground">
                  {t('k_26b5bd4947')}
                </td>
              </tr>
            ) : metrics.length === 0 ? (
              <tr>
                <td colSpan={11} className="p-8 text-center text-muted-foreground">
                  {loadError ? (
                    <span className="text-destructive">{t('k_ea6a8d0d43', { p0: loadError })}</span>
                  ) : hasFilters ? (
                    <span>{t("k_d2bce9e773")} <button
                      className="underline underline-offset-2 hover:text-foreground" onClick={resetFilters}>{t("k_4bbc4e5fd9")}</button></span>
                  ) : (
                    t('k_d48b229dac')
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
                        <span title={t("k_d39325b9c2")}><CheckCircle2 className="w-3.5 h-3.5 text-green-500" /></span>
                      )}
                      {m.is_active === 0 && (
                        <Badge variant="outline" className="text-[11px] font-normal text-muted-foreground">{t("k_6c7dcbb73a")}</Badge>
                      )}
                    </div>
                  </td>
                  <td className="p-3 text-muted-foreground">{m.display_name}</td>
                  <td className="p-3">
                    <Badge variant="outline">{metricCategoryLabel(m.category)}</Badge>
                  </td>
                  <td className="p-3">
                    <div className="flex flex-wrap gap-1">
                      {tagList(m.tags).length === 0
                        ? <span className="text-xs text-muted-foreground">-</span>
                        : tagList(m.tags).map((t) => (
                          <Badge key={t} variant="outline" className={`text-[11px] font-normal ${METRIC_TAG_COLORS[t] || ''}`}>
                            {t}
                          </Badge>
                        ))}
                    </div>
                  </td>
                  <td className="p-3">
                    <Badge variant="outline">{aggLabel(m.calculation_type)}</Badge>
                  </td>
                  <td className="p-3 text-muted-foreground">{m.unit || '-'}</td>
                  <td className="p-3">
                    <div className="flex flex-wrap gap-1 max-w-[180px]">
                      {aliasText(m.aliases).split(',').map((s) => s.trim()).filter(Boolean).map((a) => (
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
                      : <span className="text-xs text-amber-500">{t("k_46a9e40651")}</span>}
                  </td>
                  <td className="p-3 text-muted-foreground font-mono text-xs">{m.target_table || '-'}</td>
                  <td className="p-3 text-muted-foreground">{m.owner || '-'}</td>
                  <td className="p-3 text-right">
                    <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button variant="ghost" size="icon" className="h-8 w-8" title={t("k_6bff860500")}
                              onClick={() => handleOpenEdit(m)}>
                        <Edit className="w-4 h-4" />
                      </Button>
                      {/* 口径状态入口（选择/确认式，不靠手改库）：停用行要能恢复启用 */}
                      {m.is_active === 0 ? (
                        <Button variant="outline" size="sm" className="h-8 px-2 text-xs"
                                title={t("k_2419c9e212")}
                                onClick={() => setStatusTarget({ metric: m, next: 1 })}>
                          {t('k_d4e9ca3dd4')}
                        </Button>
                      ) : (
                        <Button variant="ghost" size="sm" className="h-8 px-2 text-xs"
                                title={t("k_98fc269472")}
                                onClick={() => setStatusTarget({ metric: m, next: 0 })}>
                          {t('k_5c56a88945')}
                        </Button>
                      )}
                      <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" title={t("k_7d28cf218b")}
                              onClick={() => setDeleteTarget(m)}>
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

      {/* Pagination —— 统一分页条（sticky 底部 + 半透明背景），全站唯一入口 */}
      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        busy={loading}
        onChange={(p: number) => setPage(p)}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
        hideWhenSingle
        showTotal={false}
        className={embedded ? 'mt-4 -mx-4 -mb-4 px-4' : 'shrink-0 mt-2'}
        leftText={t('k_2057a122ad', { p0: page, p1: Math.max(1, Math.ceil(total / pageSize)) })}
      />
        </TabsContent>

        <TabsContent value="dimensions" className={embedded ? 'space-y-4' : 'flex-1 min-h-0 overflow-auto'}>
          <DimensionsDictTab
            modelId={modelId}
            scope={modelId ? 'model' : undefined}
            facets={facets}
            facetsError={facetsError}
            onRetryFacets={loadFacets}
          />
        </TabsContent>
      </Tabs>

      {/* Create/Edit Metric Dialog —— 表单在 MetricFormDialog（本体对象「引用」页签复用同一份） */}
      {formOpen && (
        <MetricFormDialog
          metric={editMetric}
          modelId={modelId}
          objects={formObjectList}
          facets={facets}
          onClose={() => setFormOpen(false)}
          onSaved={() => { setFormOpen(false); loadMetrics(); }}
        />
      )}

      {/* 口径状态变更确认（双向）：启用会把口径重新广告给 ChatBI，
          停用会把它从取数里拿掉 —— 都影响线上答案，故不一点即生效。 */}
      <Dialog open={!!statusTarget} onOpenChange={(open) => { if (!open) setStatusTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t('k_f7240f58b6', { p0: statusTarget?.next === 1 ? t('k_d4e9ca3dd4') : t('k_5c56a88945'), p1: statusTarget?.metric.display_name || statusTarget?.metric.name })}
            </DialogTitle>
          </DialogHeader>
          {statusTarget?.next === 1 ? (
            <p className="text-sm text-muted-foreground">
              {t('k_8b52bac7d3')}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              {t('k_d4a2568ae4')}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setStatusTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant={statusTarget?.next === 1 ? 'default' : 'destructive'} disabled={statusBusy}
                    onClick={() => statusTarget && void applyStatus(statusTarget.metric, statusTarget.next)}>
              {statusBusy ? t('k_abe2c5d2b9') : (statusTarget?.next === 1 ? t('k_38f75a54c2') : t('k_6d433d967d'))}
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
              <Badge variant="outline">{metricCategoryLabel(detailMetric?.category)}</Badge>
              {tagList(detailMetric?.tags).map((t) => (
                <Badge key={t} variant="outline" className={METRIC_TAG_COLORS[t] || ''}>{t}</Badge>
              ))}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div><span className="text-muted-foreground">{t("k_2c2f77cdc8")}</span>{detailMetric?.name}</div>
              <div><span className="text-muted-foreground">{t("k_ec515e29b4")}</span>{aggLabel(detailMetric?.calculation_type)}</div>
              <div><span className="text-muted-foreground">{t("k_10664bc737")}</span>{detailMetric?.unit || '-'}</div>
              <div><span className="text-muted-foreground">{t("k_ae7aed7cd5")}</span>{detailMetric?.granularity || '-'}</div>
              <div><span className="text-muted-foreground">{t("k_41e72dae04")}</span><span className="font-mono text-xs">{detailMetric?.target_table || '-'}</span></div>
              <div><span className="text-muted-foreground">{t("k_a5b2040cb7")}</span><span className="font-mono text-xs">{detailMetric?.target_column || '-'}</span></div>
              {/* 口径状态可见：停用不能只在列表徽章里出现，详情里也要说清怎么恢复 */}
              <div>
                <span className="text-muted-foreground">{t("k_1ec29088f7")}</span>
                {detailMetric?.is_active === 0 ? t('k_f25648642c') : t('k_d4e9ca3dd4')}
              </div>
              {detailMetric?.expression && (
                <div className="col-span-2">
                  <span className="text-muted-foreground">{t("k_cd08ae80c5")}</span>
                  <code className="ml-1 px-2 py-0.5 bg-muted rounded text-xs">{detailMetric.expression}</code>
                </div>
              )}
              {detailMetric?.description && (
                <div className="col-span-2">
                  <span className="text-muted-foreground">{t("k_8390a0c003")}</span>{detailMetric.description}
                </div>
              )}
            </div>

            {/* Dimensions */}
            <div className="border-t pt-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium flex items-center gap-2">
                  <Layers className="w-4 h-4" />
                  {t('k_a6951b9ee4')}
                </h3>
                <Button variant="outline" size="sm" onClick={() => { setDimensionForm(emptyDimensionForm); setDimensionFormOpen(true); }}>
                  <Plus className="w-4 h-4 mr-1" />
                  {t('k_a7b6cac495')}
                </Button>
              </div>
              {dimensions.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">{t("k_fbb7cc4ffe")}</p>
              ) : (
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="text-left p-2 font-medium">{t("k_8be62bceaf")}</th>
                        <th className="text-left p-2 font-medium">{t("k_c10bbf5ddd")}</th>
                        <th className="text-left p-2 font-medium">{t("k_56dc7e14b9")}</th>
                        <th className="text-left p-2 font-medium">{t("k_e4e46c7235")}</th>
                        <th className="text-left p-2 font-medium">{t("k_412f54dc38")}</th>
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
            <DialogTitle>{t("k_a7b6cac495")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('k_f6ad17fca8')}</label>
              <Input
                placeholder={t('k_31d32c276f')}
                value={dimensionForm.name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("k_75ae6a8a7d")}</label>
              <Input
                placeholder={t('k_27c58bf1c9')}
                value={dimensionForm.display_name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, display_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('k_da0bcc29c5')}</label>
              <Input
                placeholder={t("k_2d172fa3a5")}
                value={dimensionForm.column_name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, column_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("k_b031dc9a85")}</label>
              <Select value={dimensionForm.data_type} onValueChange={(v) => setDimensionForm({ ...dimensionForm, data_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="string">{t("k_4dc9621a04")}</SelectItem>
                  <SelectItem value="int">{t("k_58306b30f4")}</SelectItem>
                  <SelectItem value="float">{t("k_82dbe08c5a")}</SelectItem>
                  <SelectItem value="date">{t("k_b6fed9af83")}</SelectItem>
                  <SelectItem value="datetime">{t("k_82d790005c")}</SelectItem>
                  <SelectItem value="boolean">{t("k_642a2c8a34")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("k_412f54dc38")}</label>
              <Input
                placeholder={t("k_d05ed0375d")}
                value={dimensionForm.description}
                onChange={(e) => setDimensionForm({ ...dimensionForm, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDimensionFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleAddDimension}>{t("k_94191ce210")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('k_a5e01dc275', { p0: deleteTarget?.display_name })}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t("k_3755f56f2f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
