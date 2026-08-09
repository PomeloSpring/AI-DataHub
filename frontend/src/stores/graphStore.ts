import { create } from 'zustand';
import axios from 'axios';

// ── Types ──────────────────────────────────────────────────────────────

interface GraphNode {
  id: string;
  label: string;
  properties: Record<string, any>;
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  properties: Record<string, any>;
}

interface GraphStats {
  node_count: number;
  relationship_count: number;
  labels: string[];
  connected: boolean;
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: GraphStats;
}

interface SyncResult {
  success: boolean;
  tables: number;
  columns: number;
  terms: number;
  relations: number;
  message?: string;
}

interface FetchGraphOptions {
  graphType?: string;
  datasourceId?: number;
  nodeTypes?: string[];
  maxDepth?: number;
  centerNode?: string;
  search?: string;
  limit?: number;
}

interface GraphStore {
  // State
  graphData: GraphData | null;
  selectedNode: GraphNode | null;
  isLoading: boolean;
  error: string | null;

  // Computed
  nodes: GraphNode[];
  edges: GraphEdge[];

  // Actions
  fetchGraphData: (options?: FetchGraphOptions) => Promise<void>;
  searchNodes: (query: string, nodeTypes?: string[], limit?: number) => Promise<void>;
  getNodeDetail: (nodeId: string) => Promise<any>;
  syncGraph: (datasourceId?: number) => Promise<SyncResult>;

  // Node CRUD
  createNode: (nodeType: string, properties: Record<string, any>) => Promise<GraphNode | null>;
  updateNode: (nodeId: string, properties: Record<string, any>) => Promise<boolean>;
  deleteNode: (nodeId: string) => Promise<boolean>;

  // Relation CRUD
  createRelation: (
    sourceId: string,
    targetId: string,
    relationType: string,
    properties?: Record<string, any>
  ) => Promise<GraphEdge | null>;
  deleteRelation: (sourceId: string, relationType: string, targetId: string) => Promise<boolean>;

  // Lineage tracking
  getUpstream: (nodeId: string, maxDepth?: number) => Promise<GraphData>;
  getDownstream: (nodeId: string, maxDepth?: number) => Promise<GraphData>;
  getImpactAnalysis: (nodeId: string) => Promise<any>;

  // Selection
  setSelectedNode: (node: GraphNode | null) => void;
  clearSelection: () => void;

  // Reset
  reset: () => void;
}

// ── API Base URL ───────────────────────────────────────────────────────

const API_BASE = '/api/graph';

// ── Helper: Get auth header ────────────────────────────────────────────

function getAuthHeader(): Record<string, string> {
  const token = localStorage.getItem('token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Store ──────────────────────────────────────────────────────────────

export const useGraphStore = create<GraphStore>((set, get) => ({
  // Initial state
  graphData: null,
  selectedNode: null,
  isLoading: false,
  error: null,

  // Computed
  get nodes() {
    return get().graphData?.nodes || [];
  },
  get edges() {
    return get().graphData?.edges || [];
  },

  // Fetch graph data
  fetchGraphData: async (options = {}) => {
    set({ isLoading: true, error: null });

    try {
      const params = new URLSearchParams();

      if (options.graphType) params.append('graph_type', options.graphType);
      if (options.datasourceId) params.append('datasource_id', options.datasourceId.toString());
      if (options.nodeTypes) {
        options.nodeTypes.forEach((t) => params.append('node_types', t));
      }
      if (options.maxDepth) params.append('max_depth', options.maxDepth.toString());
      if (options.centerNode) params.append('center_node', options.centerNode);
      if (options.search) params.append('search', options.search);
      if (options.limit) params.append('limit', options.limit.toString());

      const response = await axios.get(`${API_BASE}/query?${params.toString()}`, {
        headers: getAuthHeader(),
      });

      set({
        graphData: response.data,
        isLoading: false,
      });
    } catch (error: any) {
      console.error('Failed to fetch graph data:', error);
      set({
        error: error.response?.data?.detail || '获取图谱数据失败',
        isLoading: false,
      });
    }
  },

  // Search nodes
  searchNodes: async (query, nodeTypes, limit = 20) => {
    set({ isLoading: true, error: null });

    try {
      const params = new URLSearchParams();
      params.append('query', query);
      if (nodeTypes) {
        nodeTypes.forEach((t) => params.append('node_types', t));
      }
      params.append('limit', limit.toString());

      const response = await axios.get(`${API_BASE}/search?${params.toString()}`, {
        headers: getAuthHeader(),
      });

      // Transform search results to graph data format
      const nodes: GraphNode[] = response.data;
      set({
        graphData: {
          nodes,
          edges: [],
          stats: {
            node_count: nodes.length,
            relationship_count: 0,
            labels: [...new Set(nodes.map((n) => n.label))] as string[],
            connected: true,
          },
        },
        isLoading: false,
      });
    } catch (error: any) {
      console.error('Failed to search nodes:', error);
      set({
        error: error.response?.data?.detail || '搜索节点失败',
        isLoading: false,
      });
    }
  },

  // Get node detail
  getNodeDetail: async (nodeId) => {
    try {
      const response = await axios.get(`${API_BASE}/node/${encodeURIComponent(nodeId)}`, {
        headers: getAuthHeader(),
      });
      return response.data;
    } catch (error: any) {
      console.error('Failed to get node detail:', error);
      return null;
    }
  },

  // Sync graph
  syncGraph: async (datasourceId = 0) => {
    set({ isLoading: true, error: null });

    try {
      const response = await axios.post(
        `${API_BASE}/sync?datasource_id=${datasourceId}`,
        {},
        { headers: getAuthHeader() }
      );

      set({ isLoading: false });
      return response.data;
    } catch (error: any) {
      console.error('Failed to sync graph:', error);
      set({
        error: error.response?.data?.detail || '同步图谱失败',
        isLoading: false,
      });
      return {
        success: false,
        tables: 0,
        columns: 0,
        terms: 0,
        relations: 0,
        message: error.response?.data?.detail || '同步图谱失败',
      };
    }
  },

  // Create node
  createNode: async (nodeType, properties) => {
    try {
      const response = await axios.post(
        `${API_BASE}/nodes`,
        { node_type: nodeType, properties },
        { headers: getAuthHeader() }
      );

      // Refresh graph data
      await get().fetchGraphData();

      return response.data;
    } catch (error: any) {
      console.error('Failed to create node:', error);
      return null;
    }
  },

  // Update node
  updateNode: async (nodeId, properties) => {
    try {
      await axios.put(
        `${API_BASE}/nodes/${encodeURIComponent(nodeId)}`,
        { properties },
        { headers: getAuthHeader() }
      );

      // Refresh graph data
      await get().fetchGraphData();

      return true;
    } catch (error: any) {
      console.error('Failed to update node:', error);
      return false;
    }
  },

  // Delete node
  deleteNode: async (nodeId) => {
    try {
      await axios.delete(`${API_BASE}/nodes/${encodeURIComponent(nodeId)}`, {
        headers: getAuthHeader(),
      });

      // Refresh graph data
      await get().fetchGraphData();

      return true;
    } catch (error: any) {
      console.error('Failed to delete node:', error);
      return false;
    }
  },

  // Create relation
  createRelation: async (sourceId, targetId, relationType, properties) => {
    try {
      const response = await axios.post(
        `${API_BASE}/relations`,
        {
          source_id: sourceId,
          target_id: targetId,
          relation_type: relationType,
          properties,
        },
        { headers: getAuthHeader() }
      );

      // Refresh graph data
      await get().fetchGraphData();

      return response.data;
    } catch (error: any) {
      console.error('Failed to create relation:', error);
      return null;
    }
  },

  // Delete relation
  deleteRelation: async (sourceId, relationType, targetId) => {
    try {
      await axios.delete(
        `${API_BASE}/relations/${encodeURIComponent(sourceId)}/${relationType}/${encodeURIComponent(targetId)}`,
        { headers: getAuthHeader() }
      );

      // Refresh graph data
      await get().fetchGraphData();

      return true;
    } catch (error: any) {
      console.error('Failed to delete relation:', error);
      return false;
    }
  },

  // Get upstream dependencies
  getUpstream: async (nodeId, maxDepth = 3) => {
    set({ isLoading: true, error: null });

    try {
      const response = await axios.get(
        `${API_BASE}/lineage/upstream/${encodeURIComponent(nodeId)}?max_depth=${maxDepth}`,
        { headers: getAuthHeader() }
      );

      set({ isLoading: false });
      return response.data;
    } catch (error: any) {
      console.error('Failed to get upstream:', error);
      set({
        error: error.response?.data?.detail || '获取上游依赖失败',
        isLoading: false,
      });
      return { nodes: [], edges: [], stats: { node_count: 0, relationship_count: 0, labels: [], connected: false } };
    }
  },

  // Get downstream impact
  getDownstream: async (nodeId, maxDepth = 3) => {
    set({ isLoading: true, error: null });

    try {
      const response = await axios.get(
        `${API_BASE}/lineage/downstream/${encodeURIComponent(nodeId)}?max_depth=${maxDepth}`,
        { headers: getAuthHeader() }
      );

      set({ isLoading: false });
      return response.data;
    } catch (error: any) {
      console.error('Failed to get downstream:', error);
      set({
        error: error.response?.data?.detail || '获取下游影响失败',
        isLoading: false,
      });
      return { nodes: [], edges: [], stats: { node_count: 0, relationship_count: 0, labels: [], connected: false } };
    }
  },

  // Get impact analysis
  getImpactAnalysis: async (nodeId) => {
    set({ isLoading: true, error: null });

    try {
      const response = await axios.get(
        `${API_BASE}/lineage/impact/${encodeURIComponent(nodeId)}`,
        { headers: getAuthHeader() }
      );

      set({ isLoading: false });
      return response.data;
    } catch (error: any) {
      console.error('Failed to get impact analysis:', error);
      set({
        error: error.response?.data?.detail || '影响分析失败',
        isLoading: false,
      });
      return null;
    }
  },

  // Set selected node
  setSelectedNode: (node) => {
    set({ selectedNode: node });
  },

  // Clear selection
  clearSelection: () => {
    set({ selectedNode: null });
  },

  // Reset store
  reset: () => {
    set({
      graphData: null,
      selectedNode: null,
      isLoading: false,
      error: null,
    });
  },
}));
