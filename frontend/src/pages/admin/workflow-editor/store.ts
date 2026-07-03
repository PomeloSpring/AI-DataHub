import { create } from 'zustand';
import client from '@/api/client';
import type { WorkflowNode, WorkflowEdge, WorkflowConfig } from './types';
import { applyNodeChanges, applyEdgeChanges, type OnNodesChange, type OnEdgesChange, type OnConnect, addEdge } from '@xyflow/react';

interface WorkflowEditorState {
  workflows: WorkflowConfig[];
  currentWorkflowId: number | null;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  selectedNode: WorkflowNode | null;
  isDirty: boolean;
  isLoading: boolean;
  setWorkflows: (workflows: WorkflowConfig[]) => void;
  setCurrentWorkflow: (id: number | null) => void;
  setNodes: (nodes: WorkflowNode[]) => void;
  setEdges: (edges: WorkflowEdge[]) => void;
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;
  addNode: (node: WorkflowNode) => void;
  updateNodeData: (nodeId: string, data: Record<string, any>) => void;
  deleteNode: (nodeId: string) => void;
  setSelectedNode: (node: WorkflowNode | null) => void;
  setIsDirty: (dirty: boolean) => void;
  setIsLoading: (loading: boolean) => void;
  loadWorkflows: () => Promise<void>;
  loadWorkflow: (id: number) => Promise<void>;
  saveWorkflow: () => Promise<void>;
  createWorkflow: (name: string, description?: string) => Promise<number>;
  deleteWorkflow: (id: number) => Promise<void>;
}

export const useWorkflowEditorStore = create<WorkflowEditorState>((set, get) => ({
  workflows: [],
  currentWorkflowId: null,
  nodes: [],
  edges: [],
  selectedNode: null,
  isDirty: false,
  isLoading: false,
  setWorkflows: (workflows) => set({ workflows }),
  setCurrentWorkflow: (id) => set({ currentWorkflowId: id }),
  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),
  onNodesChange: (changes) => {
    // Check if any change is a position change (drag)
    const hasPositionChange = changes.some(c => c.type === 'position' && c.dragging === false);
    set({
      nodes: applyNodeChanges(changes, get().nodes) as WorkflowNode[],
      ...(hasPositionChange ? { isDirty: true } : {}),
    });
  },
  onEdgesChange: (changes) => {
    // Check if any edge is being removed
    const hasRemove = changes.some(c => c.type === 'remove');
    set({
      edges: applyEdgeChanges(changes, get().edges) as WorkflowEdge[],
      ...(hasRemove ? { isDirty: true } : {}),
    });
  },
  onConnect: (connection) => {
    const newEdge: WorkflowEdge = {
      ...connection,
      id: `e-${connection.source}-${connection.target}`,
      type: 'smoothstep',
      animated: true,
      data: { edgeType: 'normal' },
    };
    set({ edges: addEdge(newEdge, get().edges) as WorkflowEdge[], isDirty: true });
  },
  addNode: (node) => {
    set({ nodes: [...get().nodes, node], isDirty: true });
  },
  updateNodeData: (nodeId, data) => {
    set({
      nodes: get().nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n
      ),
      isDirty: true,
    });
  },
  deleteNode: (nodeId) => {
    set({
      nodes: get().nodes.filter((n) => n.id !== nodeId),
      edges: get().edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
      selectedNode: get().selectedNode?.id === nodeId ? null : get().selectedNode,
      isDirty: true,
    });
  },
  setSelectedNode: (node) => set({ selectedNode: node }),
  setIsDirty: (dirty) => set({ isDirty: dirty }),
  setIsLoading: (loading) => set({ isLoading: loading }),
  loadWorkflows: async () => {
    set({ isLoading: true });
    try {
      const { data } = await client.get('/admin/workflows', { params: { size: 200 } });
      set({ workflows: data.items || [] });
    } catch (e) {
      console.error('Failed to load workflows:', e);
    } finally {
      set({ isLoading: false });
    }
  },
  loadWorkflow: async (id) => {
    set({ isLoading: true });
    try {
      const { data: wf } = await client.get(`/admin/workflows/${id}`);

      // Sort steps by step_order
      const sortedSteps = [...(wf.steps || [])].sort((a: any, b: any) => a.step_order - b.step_order);

      // Generate positions if not set (auto layout for linear workflows)
      const hasPositions = sortedSteps.some((s: any) => s.position_x !== 0 || s.position_y !== 0);

      // Auto layout: vertical with good spacing
      const NODE_WIDTH = 180;
      const NODE_HEIGHT = 80;
      const VERTICAL_GAP = 40;
      const HORIZONTAL_CENTER = 300;

      const nodes: WorkflowNode[] = sortedSteps.map((step: any, index: number) => ({
        id: String(step.id),
        type: step.node_type || 'step',
        position: hasPositions
          ? { x: step.position_x || 0, y: step.position_y || 0 }
          : { x: HORIZONTAL_CENTER, y: index * (NODE_HEIGHT + VERTICAL_GAP) + 50 },
        data: {
          label: step.step_name,
          nodeType: step.node_type || 'step',
          step_type: step.step_type,
          step_name: step.step_name,
          max_rounds: step.max_rounds,
          is_enabled: step.is_enabled,
          prompt_key: step.prompt_key,
          config: step.config,
          status: 'idle',
        },
      }));

      // Generate edges from DB or auto-generate for linear workflows
      let edges: WorkflowEdge[] = (wf.edges || []).map((edge: any) => ({
        id: `e-${edge.source_step_id}-${edge.target_step_id}`,
        source: String(edge.source_step_id),
        target: String(edge.target_step_id),
        type: 'smoothstep',
        animated: true,
        label: edge.label,
        data: { edgeType: edge.edge_type || 'normal', conditionExpr: edge.condition_expr, label: edge.label },
      }));

      // If no edges stored, auto-generate linear connections based on step_order
      if (edges.length === 0 && sortedSteps.length > 1) {
        for (let i = 0; i < sortedSteps.length - 1; i++) {
          edges.push({
            id: `e-${sortedSteps[i].id}-${sortedSteps[i + 1].id}`,
            source: String(sortedSteps[i].id),
            target: String(sortedSteps[i + 1].id),
            type: 'smoothstep',
            animated: true,
            data: { edgeType: 'normal' },
          });
        }
      }

      set({ nodes, edges, currentWorkflowId: id, isDirty: false });
    } catch (e) {
      console.error('Failed to load workflow:', e);
    } finally {
      set({ isLoading: false });
    }
  },
  saveWorkflow: async () => {
    const { currentWorkflowId, nodes, edges } = get();
    if (!currentWorkflowId) return;
    set({ isLoading: true });
    try {
      const steps = nodes.map((node, index) => ({
        id: parseInt(node.id) || undefined,
        step_type: node.data.step_type || node.type,
        step_name: node.data.step_name || node.data.label,
        step_order: index,
        max_rounds: node.data.max_rounds || 1,
        is_enabled: node.data.is_enabled !== false,
        prompt_key: node.data.prompt_key,
        config: node.data.config,
        position_x: node.position.x,
        position_y: node.position.y,
        dependencies: JSON.stringify([]),
        node_type: node.type,
      }));
      const edgeList = edges.map((edge) => ({
        source_step_id: parseInt(edge.source),
        target_step_id: parseInt(edge.target),
        edge_type: edge.data?.edgeType || 'normal',
        condition_expr: edge.data?.conditionExpr,
        label: edge.label as string,
      }));
      await client.put(`/admin/workflows/${currentWorkflowId}`, { steps, edges: edgeList });
      set({ isDirty: false });
    } catch (e) {
      console.error('Failed to save workflow:', e);
    } finally {
      set({ isLoading: false });
    }
  },
  createWorkflow: async (name, description = '') => {
    set({ isLoading: true });
    try {
      const { data } = await client.post('/admin/workflows', {
        name, description, workflow_type: 'dag', is_active: true, steps: [],
      });
      await get().loadWorkflows();
      return data.id;
    } catch (e) {
      console.error('Failed to create workflow:', e);
      return 0;
    } finally {
      set({ isLoading: false });
    }
  },
  deleteWorkflow: async (id) => {
    set({ isLoading: true });
    try {
      await client.delete(`/admin/workflows/${id}`);
      if (get().currentWorkflowId === id) {
        set({ currentWorkflowId: null, nodes: [], edges: [], isDirty: false });
      }
      await get().loadWorkflows();
    } catch (e) {
      console.error('Failed to delete workflow:', e);
    } finally {
      set({ isLoading: false });
    }
  },
}));
