import client from './client';

export const tagsApi = {
  // Categories
  getCategories: (workspaceId: number) => client.get('/tags/categories', { params: { workspace_id: workspaceId } }),
  createCategory: (data: any) => client.post('/tags/categories', data),

  // Tags
  list: (params: any) => client.get('/tags', { params }),
  create: (data: any) => client.post('/tags', data),
  update: (id: number, data: any) => client.put(`/tags/${id}`, data),
  delete: (id: number) => client.delete(`/tags/${id}`),

  // Values
  getValues: (tagId: number, params?: any) => client.get(`/tags/${tagId}/values`, { params }),
  setValue: (tagId: number, data: any) => client.post(`/tags/${tagId}/values`, data),

  // Query
  queryByTags: (conditions: any) => client.post('/tags/query', conditions),
};
