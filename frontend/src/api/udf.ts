import client from './client';

export interface UdfParam {
  name: string;
  type?: string;
}

export interface Udf {
  id: number;
  name: string;
  udf_kind: string;
  expression: string;
  params: UdfParam[];
  return_type: string;
  description?: string;
  is_active: number | boolean;
  is_current: number | boolean;
  version: number;
  usage_count: number;
  created_at?: string;
  updated_at?: string;
}

export const udfApi = {
  list: (includeHistory = false) =>
    client.get('/udfs', { params: { include_history: includeHistory } }),
  create: (data: { name: string; expression: string; params: UdfParam[]; return_type?: string; description?: string }) =>
    client.post('/udfs', data),
  update: (name: string, data: Partial<Udf>) => client.put(`/udfs/${name}`, data),
  toggle: (name: string, isActive: boolean) =>
    client.post(`/udfs/${name}/toggle`, { is_active: isActive }),
  remove: (name: string) => client.delete(`/udfs/${name}`),
  dependents: (name: string) => client.get(`/udfs/${name}/dependents`),
};
