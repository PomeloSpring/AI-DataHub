import client from './client';

export const lineageApi = {
  getTableLineage: (tableName: string, workspaceId: number, direction?: string) =>
    client.get(`/lineage/tables/${tableName}`, { params: { workspace_id: workspaceId, direction } }),
  getColumnLineage: (table: string, column: string, workspaceId: number) =>
    client.get(`/lineage/columns/${table}.${column}`, { params: { workspace_id: workspaceId } }),
  getGraph: (workspaceId: number, opts?: { datasource?: string; nodeTypes?: string[] }) =>
    client.get('/lineage/graph', {
      params: {
        workspace_id: workspaceId,
        // 空串 = 不过滤；数据源按业务名筛（含下游正向可达资产），节点类型逗号分隔
        datasource: opts?.datasource || undefined,
        node_types: opts?.nodeTypes?.length ? opts.nodeTypes.join(',') : undefined,
      },
    }),
  createNode: (data: any) => client.post('/lineage/nodes', data),
  createEdge: (data: any) => client.post('/lineage/edges', data),
  parseSql: (data: any) => client.post('/lineage/parse-sql', data),
  getImpact: (nodeId: number, workspaceId: number) =>
    client.get(`/lineage/impact/${nodeId}`, { params: { workspace_id: workspaceId } }),
};
