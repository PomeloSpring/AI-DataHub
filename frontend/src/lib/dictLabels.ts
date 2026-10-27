/**
 * 指标/维度字典筛选值的展示层。
 *
 * 值域一律来自后端 GET /metrics/filters（在全量字典上聚合的真实取值），本模块只做
 * “已知值给中文名、未知值原样显示”，**不得反过来把枚举写死成筛选项**：
 * 历史缺陷就是把 category 写成 basic/derived/composite 三选，而库里真实值域是
 * 可观测/客户/案例…，两边无交集 → 下拉选任何一项都查空，成了“能点但永远无结果”的死筛选。
 */

/** 空值桶哨兵（未分类 / 未归属 / 未设置），与后端 metrics_service.UNSET_FACET 同值 */
export const DICT_UNSET = '__unset__';

export interface FacetValue {
  value: string;
  count: number;
}

/** 聚合方式表单候选（小写值提交，后端归一为大写存 agg_type） */
export const AGG_TYPE_OPTIONS = [
  { value: 'sum', label: '求和' },
  { value: 'count', label: '计数' },
  { value: 'count_distinct', label: '去重计数' },
  { value: 'avg', label: '平均' },
  { value: 'max', label: '最大值' },
  { value: 'min', label: '最小值' },
  { value: 'none', label: '无聚合（直接表达式）' },
  { value: 'custom', label: '自定义' },
];

/** agg_type 展示名：库里历史上混过大小写，也出现过表单没提供的值（COUNT_DISTINCT/NONE） */
const AGG_LABELS: Record<string, string> = {
  SUM: '求和',
  COUNT: '计数',
  AVG: '平均',
  MAX: '最大值',
  MIN: '最小值',
  COUNT_DISTINCT: '去重计数',
  NONE: '无聚合',
  CUSTOM: '自定义',
};

export function aggLabel(v?: string | null): string {
  const raw = String(v ?? '').trim();
  if (!raw) return '未设置';
  return AGG_LABELS[raw.toUpperCase()] || raw;
}

/** 指标口径标签候选（落 `tags` 列，与 category 的**业务域**是两个正交维度）。
 *  历史上这三档被当作 category 写库（basic/derived/composite），导致分类下拉里
 *  业务域（可观测/客户/案例…）与口径标签混杂，现拆列各管一头。 */
export const METRIC_KIND_TAG_OPTIONS = ['基础', '衍生', '复合'];

/** 指标分类展示名：category 值域一律来自后端 facet（业务域），原样显示 */
export function metricCategoryLabel(v?: string | null): string {
  const raw = String(v ?? '').trim();
  if (!raw || raw === DICT_UNSET) return '未分类';
  return raw;
}

export function dimensionCategoryLabel(v?: string | null): string {
  const raw = String(v ?? '').trim();
  if (!raw || raw === DICT_UNSET) return '未分类';
  return raw;
}

/** 归属对象展示名：name 为主，key 作次要信息；名字取不到就显示 key（是语义名，不编造也不显示裸 id） */
export function objectLabel(names: Record<string, string>, key?: string | null): string {
  const raw = String(key ?? '').trim();
  if (!raw) return '未归属';
  if (raw === DICT_UNSET) return '未归属';
  return names[raw] || raw;
}

/** 下拉选项文案: 展示名 + 该值在字典里的条数（计数来自后端 facet，不是当前页） */
export function withCount(label: string, count?: number): string {
  return typeof count === 'number' ? `${label}（${count}）` : label;
}
