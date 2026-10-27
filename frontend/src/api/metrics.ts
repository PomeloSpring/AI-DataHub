import client from './client';
import type { FacetValue } from '@/lib/dictLabels';

/** 字典筛选词表的一个分桶(计数在全量字典上聚合, 与列表接口同口径) */
export interface DictFacetBucket {
  total: number;
  active: number;
  inactive: number;
  certified: number;
  draft: number;
  unbound_objects: number;
  with_target_table: number;
  without_target_table: number;
  with_target_column: number;
  with_value_labels?: number;
  without_value_labels?: number;
  categories: FacetValue[];
  agg_types?: FacetValue[];
  /** 口径标签分桶（基础/衍生/复合…，来自 tags 列；与 categories 的业务域正交） */
  tags?: FacetValue[];
}

/** 归属对象候选(key + 业务名 + 挂接资产数) */
export interface DictObjectFacet {
  key: string;
  display_name: string;
  metric_count: number;
  dimension_count: number;
}

export interface DictFilters {
  metrics: DictFacetBucket;
  dimensions: DictFacetBucket;
  objects: DictObjectFacet[];
}

/** 指标列表筛选入参(值域来自 filters 接口, 前端不写死枚举) */
export interface MetricListQuery {
  page?: number;
  size?: number;
  search?: string;
  /** 业务域分类（值域来自 filters 的 categories） */
  category?: string;
  /** 口径标签（值域来自 filters 的 tags） */
  tags?: string;
  agg_type?: string;
  certified?: string;
  bound_object_key?: string;
  has_target_table?: string;
  is_active?: string;
  model_id?: number;
  scope?: 'model' | 'unbound';
}

export const metricsApi = {
  list: (params?: MetricListQuery) => client.get('/metrics', { params }),
  create: (data: any) => client.post('/metrics', data),
  get: (id: number) => client.get(`/metrics/${id}`),
  update: (id: number, data: any) => client.put(`/metrics/${id}`, data),
  delete: (id: number) => client.delete(`/metrics/${id}`),
  deleteDimension: (id: number) => client.delete(`/metrics/dimensions/${id}`),
  getDimensions: (id: number) => client.get(`/metrics/${id}/dimensions`),
  addDimension: (id: number, data: any) => client.post(`/metrics/${id}/dimensions`, data),
  // 全局维度字典(adh_dimensions) — 指标中心「维度字典」Tab 唯一编辑入口
  // 模型工作区复用同一入口: 传 { model_id, scope: 'model' } 或 { scope: 'unbound' } 筛选
  listAllDimensions: (params?: { model_id?: number; scope?: 'model' | 'unbound' }) =>
    client.get('/metrics/dimensions', { params }),
  updateDimension: (id: number, data: any) => client.put(`/metrics/dimensions/${id}`, data),
  // 新建全局维度字典行(与 updateDimension 对称) —— 必填 display_name + bound_object_key
  createDimension: (data: any) => client.post('/metrics/dimensions', data),
  // 字典筛选词表: 指标/维度两个 Tab 共用一次拉取(选项必须来自全量集聚合, 见 lib/dictLabels)
  filters: (params?: { model_id?: number; scope?: 'model' | 'unbound' }) =>
    client.get('/metrics/filters', { params }),
  // 未归属资产池(指标/维度/术语/回流候选 + 计数徽章)
  assetPool: () => client.get('/metrics/asset-pool'),
};
