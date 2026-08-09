import client from './client';

export const qualityApi = {
  // Rules
  getRules: (workspaceId: number) => client.get('/quality/rules', { params: { workspace_id: workspaceId } }),
  createRule: (data: any) => client.post('/quality/rules', data),
  updateRule: (id: number, data: any) => client.put(`/quality/rules/${id}`, data),
  deleteRule: (id: number) => client.delete(`/quality/rules/${id}`),
  executeRule: (id: number) => client.post(`/quality/rules/${id}/execute`),
  executeAll: (workspaceId: number) => client.post('/quality/execute', null, { params: { workspace_id: workspaceId } }),

  // Results
  getResults: (params: any) => client.get('/quality/results', { params }),
  getReports: (workspaceId: number) => client.get('/quality/reports', { params: { workspace_id: workspaceId } }),
  generateReport: (workspaceId: number) => client.post('/quality/reports/generate', null, { params: { workspace_id: workspaceId } }),
  getDashboard: (workspaceId: number) => client.get('/quality/dashboard', { params: { workspace_id: workspaceId } }),
};
