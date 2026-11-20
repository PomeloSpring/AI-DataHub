/**
 * 指标口径编辑弹窗（新建/编辑同一份表单）。
 *
 * 抽出来的理由：同一个口径有两条入口——「指标与维度」中心页，以及本体对象检查器的
 * 「引用」页签（建模时就地给对象补/改口径）。两处必须是**同一套字段、同一套校验、
 * 同一个写接口**（metricsApi.create/update），否则同一行口径在两个页面里改出来的
 * 结果会不一样，排查时根本说不清哪一处是权威。
 *
 * 词表：业务域分类/口径标签只能从「字典已在用的真实值」里选（facet），不得手输、
 * 不得写死枚举（历史缺陷：把 basic/derived/composite 当分类值域，与库内值域无交集，
 * 选什么都查空）。facet 未由调用方传入时本弹窗自行拉取，失败要显式提示（不静默空下拉）。
 */
import { useEffect, useMemo, useState } from 'react';
import { metricsApi } from '@/api/metrics';
import type { DictFilters } from '@/api/metrics';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CheckCircle2 } from 'lucide-react';
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
import { toast } from '@/lib/toast';
import {
  AGG_TYPE_OPTIONS,
  DICT_UNSET,
  METRIC_KIND_TAG_OPTIONS,
  aggLabel,
  aliasText,
  metricCategoryLabel,
  tagList,
} from '@/lib/dictLabels';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

/** adh_metrics 行的前端形状（列表/详情/编辑共用，字段与接口同口径） */
export interface MetricRecord {
  id: number;
  name: string;
  display_name: string;
  description?: string;
  /** 业务域分类(adh_metrics.category) */
  category?: string;
  /** 口径标签(adh_metrics.tags, JSON 数组): 基础/衍生/复合… 与业务域正交 */
  tags?: string[] | string | null;
  calculation_type: string;
  expression?: string;
  unit?: string;
  target_table?: string;
  target_column?: string;
  granularity?: string;
  owner?: string;
  bound_object_key?: string;
  aliases?: string | string[] | null;
  certified?: number;
  is_active?: number;
  created_at?: string;
  updated_at?: string;
}

interface MetricFormData {
  name: string;
  display_name: string;
  description: string;
  /** 业务域分类(落 category 列) */
  category: string;
  /** 口径标签(落 tags 列) */
  tags: string[];
  calculation_type: string;
  expression: string;
  unit: string;
  target_table: string;
  target_column: string;
  granularity: string;
  bound_object_key: string;
  /** 逗号分隔, 提交时拆为数组(后端存 JSON 列) */
  aliases: string;
  /** 口径状态(is_active): 1=启用参与取数, 0=停用/未发布 */
  is_active: number;
}

/** 三元组预览(只读): 所见 = 所写 = 图谱投影, 与 to_rdf/字典行为同一主语结构 */
function metricRdfPreview(f: MetricFormData): string {
  const subj = `adh:metric:${f.display_name || f.name || '?'}`;
  const lines: string[] = [`${subj} a adh:Metric .`];
  if (f.bound_object_key) lines.push(`${subj} adh:boundToObject adh:obj:${f.bound_object_key} .`);
  const als = aliasText(f.aliases).split(',').map((s) => s.trim()).filter(Boolean);
  als.forEach((a) => lines.push(`${subj} skos:altLabel "${a}" .`));
  if (f.calculation_type) lines.push(`${subj} adh:aggregation "${f.calculation_type.toUpperCase()}" .`);
  if (f.unit) lines.push(`${subj} adh:unit "${f.unit}" .`);
  if (f.description) lines.push(`${subj} rdfs:comment "${f.description}" .`);
  lines.push(tr('k_352b2a8289'));
  return lines.join('\n');
}

function emptyForm(boundObjectKey = ''): MetricFormData {
  return {
    name: '',
    display_name: '',
    description: '',
    category: '',
    tags: [],
    calculation_type: 'sum',
    expression: '',
    unit: '',
    target_table: '',
    target_column: '',
    granularity: '',
    bound_object_key: boundObjectKey,
    aliases: '',
    is_active: 1,
  };
}

function formOf(metric: MetricRecord): MetricFormData {
  return {
    name: metric.name,
    display_name: metric.display_name,
    description: metric.description || '',
    category: metric.category || '',
    tags: tagList(metric.tags),
    calculation_type: (metric.calculation_type || 'sum').toLowerCase(),
    expression: metric.expression || '',
    unit: metric.unit || '',
    target_table: metric.target_table || '',
    target_column: metric.target_column || '',
    granularity: metric.granularity || '',
    bound_object_key: metric.bound_object_key || '',
    aliases: aliasText(metric.aliases),
    is_active: metric.is_active === 0 ? 0 : 1,
  };
}

export default function MetricFormDialog({
  metric = null,
  modelId,
  objects,
  facets,
  defaultObjectKey = '',
  onClose,
  onSaved,
}: {
  /** 传入 = 编辑该行；缺省/null = 新建 */
  metric?: MetricRecord | null;
  /** 模型作用域（拉分类/标签/对象候选；缺省 = 全局字典） */
  modelId?: number;
  /** 归属对象候选（模型工作区传本模型对象；缺省退回 facet 里的全量对象） */
  objects?: { key: string; display_name: string }[];
  /** 分类/标签候选词表。未传（undefined）时本弹窗自行拉取；调用方已有词表就传入免得重复请求 */
  facets?: DictFilters | null;
  /** 新建时的默认归属对象（对象检查器里新建 = 给这个对象挂口径） */
  defaultObjectKey?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation('ui');
  const [formData, setFormData] = useState<MetricFormData>(
    () => (metric ? formOf(metric) : emptyForm(defaultObjectKey)));
  const [saving, setSaving] = useState(false);

  // 调用方没备词表时自行拉（对象「引用」页签复用本弹窗，不该被迫先拉一遍 facet）
  const needsFacets = facets === undefined;
  const [ownFacets, setOwnFacets] = useState<DictFilters | null>(null);
  useEffect(() => {
    if (!needsFacets) return;
    let cancelled = false;
    metricsApi.filters(modelId ? { model_id: modelId, scope: 'model' } : undefined)
      .then(({ data }) => { if (!cancelled) setOwnFacets(data ?? null); })
      .catch((e: any) => {
        if (cancelled) return;
        // 降级要显式：词表缺失 → 分类/标签下拉为空，必须让人知道原因，而不是以为“字典里没这些值”
        setOwnFacets(null);
        toast.error(t('k_e6385b26bf', {
          p0: String(e?.response?.data?.detail || e?.message || t('k_974e748456')),
        }));
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsFacets, modelId]);
  const source = needsFacets ? ownFacets : facets;

  // 新建/编辑表单的「归属对象」候选 = **全量**对象（含尚无指标的）。
  // 不可复用列表筛选的 objectOptions —— 它按 metric_count>0 过滤，新建时目标对象往往尚未挂指标。
  const formObjectList = useMemo(
    () => (objects?.length ? objects : (source?.objects || []).map((o) => ({ key: o.key, display_name: o.display_name }))),
    [objects, source]);
  // 「业务域分类」候选 = 字典已有真实域(+当前值兜底)。不得再拼基础/衍生/复合 ——
  // 那是口径标签(tags 列)，与业务域是两个正交维度，混进同一候选会把两个维度写进一列。
  const categoryFormOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const f of source?.metrics?.categories || []) {
      if (f.value !== DICT_UNSET) seen.set(f.value, metricCategoryLabel(f.value));
    }
    const current = (formData.category || '').trim();
    if (current && !seen.has(current)) seen.set(current, metricCategoryLabel(current));
    return [...seen.entries()];
  }, [source, formData.category]);
  // 「口径标签」候选 = 固定三档 + 字典已有标签(+当前已选)，只能选不能手输
  const tagFormOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const tag of METRIC_KIND_TAG_OPTIONS) seen.set(tag, tag);
    for (const f of source?.metrics?.tags || []) {
      if (f.value !== DICT_UNSET) seen.set(f.value, f.value);
    }
    for (const tag of formData.tags) if (tag) seen.set(tag, tag);
    return [...seen.keys()];
  }, [source, formData.tags]);

  const handleSave = async () => {
    if (!formData.name || !formData.display_name) {
      toast.error(t('k_336d5cec6d'));
      return;
    }
    setSaving(true);
    try {
      const payload: any = {
        ...formData,
        aliases: formData.aliases.split(',').map((s) => s.trim()).filter(Boolean),
      };
      if (metric) {
        await metricsApi.update(metric.id, payload);
        toast.success(t('k_f5e88fe8dc'));
      } else {
        await metricsApi.create(payload);
        toast.success(t('k_45eda4ad25'));
      }
      onSaved();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{metric ? t('k_6bff860500') : t('k_e4f7015bed')}</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-4 py-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_fbf757538f')}</label>
            <Input
              placeholder={t('k_50fecbf25f')}
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_f75459f6f0')}</label>
            <Input
              placeholder={t('k_5139413eed')}
              value={formData.display_name}
              onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
            />
          </div>
          <div className="col-span-2 space-y-2">
            <label className="text-sm font-medium">{t('k_412f54dc38')}</label>
            <Input
              placeholder={t('k_c0c4a42ea4')}
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_79b96b9bed')}</label>
            {/* 候选 = 字典已有真实域(facet)，否则存量域(可观测/客户…) 在编辑弹窗里
                渲染成空白，看起来像“类型被清空了”。口径标签(基础/衍生/复合)不在本列。 */}
            <Select value={formData.category} onValueChange={(v) => setFormData({ ...formData, category: v })}>
              <SelectTrigger aria-label={t('k_c695bf5821')}>
                <SelectValue placeholder={categoryFormOptions.length ? t('k_af4e7e5c7c') : t('k_111736cc91')} />
              </SelectTrigger>
              <SelectContent>
                {categoryFormOptions.map(([value, label]) => (
                  <SelectItem key={value} value={value}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {/* 口径标签与业务域是两个正交维度（落 tags 列）；候选只能选不能手输。
              fieldset/legend 原生就是一个具名选项组（比 div[role=group] 更可访） */}
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">{t('k_bcfb3e80e0')}</legend>
            <div className="flex flex-wrap gap-1.5 pt-1">
              {tagFormOptions.map((tag) => {
                const on = formData.tags.includes(tag);
                return (
                  <Button key={tag} type="button" size="sm" variant={on ? 'default' : 'outline'}
                    aria-pressed={on}
                    className="h-7 px-2 text-xs"
                    onClick={() => setFormData({
                      ...formData,
                      tags: on ? formData.tags.filter((x) => x !== tag) : [...formData.tags, tag],
                    })}>
                    {on && <CheckCircle2 className="w-3 h-3 mr-1" />}{tag}
                  </Button>
                );
              })}
            </div>
          </fieldset>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_e13f097b9b')}</label>
            <Select value={formData.calculation_type} onValueChange={(v) => setFormData({ ...formData, calculation_type: v })}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {AGG_TYPE_OPTIONS.map((agg) => (
                  <SelectItem key={agg} value={agg}>{aggLabel(agg)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="col-span-2 space-y-2">
            <label className="text-sm font-medium">{t('k_80a343d78c')}</label>
            <Input
              placeholder={t('k_a5ad2b377c')}
              value={formData.expression}
              onChange={(e) => setFormData({ ...formData, expression: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_8ec8c8c712')}</label>
            <Input
              placeholder={t('k_da50dded17')}
              value={formData.unit}
              onChange={(e) => setFormData({ ...formData, unit: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_de2e02c66e')}</label>
            <Input
              placeholder={t('k_378825f36d')}
              value={formData.aliases}
              onChange={(e) => setFormData({ ...formData, aliases: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_baf051c677')}</label>
            <Input
              placeholder={t('k_0443e61e41')}
              value={formData.granularity}
              onChange={(e) => setFormData({ ...formData, granularity: e.target.value })}
            />
          </div>
          {/* 归属对象: 引用选择器（不手填 key，见 ui-resource-display §2）。
              嵌入模型工作区时限本模型对象（不产生悬空/跨模型挂接）；独立页用全量对象。 */}
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_8251ec3d2b')}</label>
            <Select
              value={formData.bound_object_key || undefined}
              onValueChange={(v) => setFormData({ ...formData, bound_object_key: v })}
            >
              <SelectTrigger>
                <SelectValue placeholder={formObjectList.length ? t('k_02ff67e28e') : t('k_e95a129d4d')} />
              </SelectTrigger>
              <SelectContent>
                {formObjectList.map((o) => (
                  <SelectItem key={o.key} value={o.key}>{o.display_name || o.key} ({o.key})</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_442be9c497')}</label>
            <Input
              placeholder={t('k_8f4f44b3e6')}
              value={formData.target_table}
              onChange={(e) => setFormData({ ...formData, target_table: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('k_38556b027f')}</label>
            <Input
              placeholder={t('k_3c6ca65fb9')}
              value={formData.target_column}
              onChange={(e) => setFormData({ ...formData, target_column: e.target.value })}
            />
          </div>
          {/* 口径状态（is_active）：停用与 AS-BOT 「未发布」同一语义 —— 行保留、不参与检索与取数。 */}
          <div className="col-span-2 grid grid-cols-2 gap-4 items-end">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('k_b9dc554215')}</label>
              <Select value={String(formData.is_active)}
                onValueChange={(v) => setFormData({ ...formData, is_active: Number(v) })}>
                <SelectTrigger aria-label={t('k_b9dc554215')}><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="1">{t('k_d7b43aab71')}</SelectItem>
                  <SelectItem value="0">{t('k_062c1e7b42')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {formData.is_active === 0 && (
              <p className="text-xs text-amber-600 pb-2">
                {t('k_a0196ad69c')}
              </p>
            )}
          </div>
        </div>
        {/* 三元组预览: 所见=所写=图谱投影（限高内部滚动，不得把表单撑出视口） */}
        <div className="mt-2">
          <label className="text-sm font-medium text-muted-foreground">{t('k_65b25bea2b')}</label>
          <pre className="mt-1 p-3 max-h-40 overflow-auto rounded-md bg-muted/50 border text-[11px] font-mono whitespace-pre-wrap leading-relaxed">
            {metricRdfPreview(formData)}
          </pre>
        </div>
        {/* 长表单弹窗：操作条吸底，滚动到底部之前也能取消/保存 */}
        <DialogFooter className="sticky bottom-0 -mx-5 -mb-5 px-5 pb-5 pt-3 bg-background border-t border-border/60">
          <Button variant="outline" onClick={onClose}>{t('k_4d0b4688c7')}</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}