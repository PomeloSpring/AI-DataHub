import client from './client';

export interface QualityRuleQuery {
  workspace_id?: number;
  /** 规则名称/描述模糊搜索 */
  q?: string;
  rule_type?: string;
  severity?: string;
  /** 目标表模糊匹配 */
  target_table?: string;
  page?: number;
  page_size?: number;
}

export const qualityApi = {
  // Rules
  // 传 page_size 走服务端真分页（{items,total,page,page_size}）；不传回全量数组
  getRules: (params: QualityRuleQuery) => client.get('/quality/rules', { params }),
  createRule: (data: any) => client.post('/quality/rules', data),
  updateRule: (id: number, data: any) => client.put(`/quality/rules/${id}`, data),
  deleteRule: (id: number) => client.delete(`/quality/rules/${id}`),
  executeRule: (id: number) => client.post(`/quality/rules/${id}/execute`),
  executeAll: (workspaceId: number) => client.post('/quality/execute', null, { params: { workspace_id: workspaceId } }),

  // Results
  getResults: (params: any) => client.get('/quality/results', { params }),
  // 删除问题记录：批量逐条回报（部分失败带失败项与原因），不静默吞错
  deleteResultsBatch: (ids: number[]) => client.post('/quality/results/batch-delete', { ids }),
  // 失败样本要实时重跑该规则（persist=false），按查看者权限脱敏后返回
  getRuleSamples: (id: number) => client.get(`/quality/rules/${id}/samples`),
  getReports: (workspaceId: number) => client.get('/quality/reports', { params: { workspace_id: workspaceId } }),
  deleteReport: (id: number) => client.delete(`/quality/reports/${id}`),
  generateReport: (workspaceId: number) => client.post('/quality/reports/generate', null, { params: { workspace_id: workspaceId } }),
  getDashboard: (workspaceId: number) => client.get('/quality/dashboard', { params: { workspace_id: workspaceId } }),
};
