import client from './client';

export const metricsApi = {
  list: (params: any) => client.get('/metrics', { params }),
  create: (data: any) => client.post('/metrics', data),
  get: (id: number) => client.get(`/metrics/${id}`),
  update: (id: number, data: any) => client.put(`/metrics/${id}`, data),
  delete: (id: number) => client.delete(`/metrics/${id}`),
  getDimensions: (id: number) => client.get(`/metrics/${id}/dimensions`),
  addDimension: (id: number, data: any) => client.post(`/metrics/${id}/dimensions`, data),
};
