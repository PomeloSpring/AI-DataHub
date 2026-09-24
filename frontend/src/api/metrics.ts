import client from './client';

export const metricsApi = {
  list: (params: any) => client.get('/metrics', { params }),
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
  // 未归属资产池(指标/维度/术语/回流候选 + 计数徽章)
  assetPool: () => client.get('/metrics/asset-pool'),
};
