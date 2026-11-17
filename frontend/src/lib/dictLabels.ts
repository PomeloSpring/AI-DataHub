/**
 * 指标/维度字典筛选值的展示层。
 *
 * 值域一律来自后端 GET /metrics/filters（在全量字典上聚合的真实取值），本模块只做
 * “已知值给中文名、未知值原样显示”，**不得反过来把枚举写死成筛选项**：
 * 历史缺陷就是把 category 写成 basic/derived/composite 三选，而库里真实值域是
 * 可观测/客户/案例…，两边无交集 → 下拉选任何一项都查空，成了“能点但永远无结果”的死筛选。
 */

import { t } from '@/i18n/runtime';

/** 空值桶哨兵（未分类 / 未归属 / 未设置），与后端 metrics_service.UNSET_FACET 同值 */
export const DICT_UNSET = '__unset__';

export interface FacetValue {
  value: string;
  count: number;
}

/** 聚合方式表单候选（小写值提交，后端归一为大写存 agg_type）；
 *  展示名在渲染期经 aggLabel() 取，不在模块顶层冻结（切语言即时生效） */
export const AGG_TYPE_OPTIONS = ['sum', 'count', 'count_distinct', 'avg', 'max', 'min', 'none', 'custom'];

/** agg_type 展示名：库里历史上混过大小写，也出现过表单没提供的值（COUNT_DISTINCT/NONE）。
 *  值是枚举码（不翻译），展示文案走 ui 命名空间 key。 */
const AGG_LABEL_KEYS: Record<string, string> = {
  SUM: 'k_1609bae691',
  COUNT: 'k_37802b1dfc',
  AVG: 'k_33875c8660',
  MAX: 'k_01821997b7',
  MIN: 'k_c3d641f190',
  COUNT_DISTINCT: 'k_febab2bc51',
  NONE: 'k_8f29c443ff',
  CUSTOM: 'k_c493338e8c',
};

export function aggLabel(v?: string | null): string {
  const raw = String(v ?? '').trim();
  if (!raw) return t('k_fe2d26a257');
  const key = AGG_LABEL_KEYS[raw.toUpperCase()];
  return key ? t(key) : raw;
}

/** 指标口径标签候选（落 `tags` 列，与 category 的**业务域**是两个正交维度）。
 *  历史上这三档被当作 category 写库（basic/derived/composite），导致分类下拉里
 *  业务域（可观测/客户/案例…）与口径标签混杂，现拆列各管一头。 */
export const METRIC_KIND_TAG_OPTIONS = ['基础', '衍生', '复合'];

/** 指标分类展示名：category 值域一律来自后端 facet（业务域），原样显示 */
export function metricCategoryLabel(v?: string | null): string {
  const raw = String(v ?? '').trim();
  if (!raw || raw === DICT_UNSET) return t('k_b28f13ea3e');
  return raw;
}

export function dimensionCategoryLabel(v?: string | null): string {
  const raw = String(v ?? '').trim();
  if (!raw || raw === DICT_UNSET) return t('k_b28f13ea3e');
  return DIM_CATEGORY_KEYS[raw] ? t(DIM_CATEGORY_KEYS[raw]) : raw;
}

/** 已知维度分类码 → 文案 key（码值仍原样提交，只有展示翻译；未知值原样显示） */
const DIM_CATEGORY_KEYS: Record<string, string> = {
  基础属性: 'k_4bef8bfb50',
  时间: 'k_89b4aa6364',
  地理: 'k_8af663f981',
  状态枚举: 'k_3dd597f42a',
  业务分类: 'k_e50e8a3ff0',
  组织人员: 'k_9d3d4bfab5',
};

/** 归属对象展示名：name 为主，key 作次要信息；名字取不到就显示 key（是语义名，不编造也不显示裸 id） */
export function objectLabel(names: Record<string, string>, key?: string | null): string {
  const raw = String(key ?? '').trim();
  if (!raw || raw === DICT_UNSET) return t('k_46a9e40651');
  return names[raw] || raw;
}

/** 下拉选项文案: 展示名 + 该值在字典里的条数（计数来自后端 facet，不是当前页） */
export function withCount(label: string, count?: number): string {
  return typeof count === 'number' ? t('k_ba20101bb8', { p0: label, p1: count }) : label;
}
