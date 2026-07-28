import type { Node, Edge } from '@xyflow/react';

// ── Node Types ──────────────────────────────────────────────────────

export type NodeType = 'start' | 'end' | 'step' | 'condition' | 'parallel' | 'merge' | 'agent' | 'mcp_tool';

export interface NodeConfig {
  step_type: string;
  step_name: string;
  max_rounds?: number;
  is_enabled: boolean;
  prompt_key?: string;
  config?: Record<string, any>;
  condition_expr?: string;
}

// Extended ReactFlow node with our custom data
export interface WorkflowNode extends Node {
  data: NodeConfig & {
    label: string;
    nodeType: NodeType;
    status?: 'idle' | 'running' | 'success' | 'error';
  };
}

// ── Edge Types ──────────────────────────────────────────────────────

export type EdgeType = 'normal' | 'conditional' | 'error';

export interface WorkflowEdge extends Edge {
  data?: {
    edgeType: EdgeType;
    conditionExpr?: string;
    label?: string;
  };
}

// ── Workflow Config ─────────────────────────────────────────────────

export interface WorkflowStepConfig {
  id?: number;
  workflow_id?: number;
  step_type: string;
  step_name: string;
  step_order: number;
  max_rounds: number;
  is_enabled: boolean;
  prompt_key?: string;
  config?: Record<string, any>;
  position_x?: number;
  position_y?: number;
  dependencies?: string;
  node_type?: NodeType;
}

export interface WorkflowEdgeConfig {
  id?: number;
  workflow_id?: number;
  source_step_id: number;
  target_step_id: number;
  edge_type: EdgeType;
  condition_expr?: string;
  label?: string;
}

export interface WorkflowConfig {
  id: number;
  name: string;
  description?: string;
  is_active: boolean;
  is_default: boolean;
  workflow_type: 'linear' | 'dag';
  dag_config?: string;
  created_at: string;
  updated_at: string;
  created_by?: string;
  steps: WorkflowStepConfig[];
  edges?: WorkflowEdgeConfig[];
}

// ── Node Type Definitions ───────────────────────────────────────────

export interface NodeTypeInfo {
  type: NodeType;
  label: string;
  description: string;
  icon: string;
  color: string;
  bgColor: string;
  borderColor: string;
  inputs: string[];
  outputs: string[];
}

export const NODE_TYPE_DEFINITIONS: NodeTypeInfo[] = [
  {
    type: 'start',
    label: '开始',
    description: '工作流入口',
    icon: 'Play',
    color: 'text-green-500',
    bgColor: 'bg-green-500/10',
    borderColor: 'border-green-500/30',
    inputs: [],
    outputs: ['question', 'datasource_id'],
  },
  {
    type: 'end',
    label: '结束',
    description: '工作流出口',
    icon: 'StopCircle',
    color: 'text-red-500',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    inputs: ['result'],
    outputs: [],
  },
  {
    type: 'step',
    label: '处理步骤',
    description: '通用处理节点',
    icon: 'Box',
    color: 'text-blue-500',
    bgColor: 'bg-blue-500/10',
    borderColor: 'border-blue-500/30',
    inputs: ['input'],
    outputs: ['output'],
  },
  {
    type: 'condition',
    label: '条件判断',
    description: '根据条件分支',
    icon: 'GitBranch',
    color: 'text-yellow-500',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/30',
    inputs: ['input'],
    outputs: ['true', 'false'],
  },
  {
    type: 'parallel',
    label: '并行执行',
    description: '并行执行多路',
    icon: 'GitMerge',
    color: 'text-purple-500',
    bgColor: 'bg-purple-500/10',
    borderColor: 'border-purple-500/30',
    inputs: ['input'],
    outputs: ['branch_1', 'branch_2', 'branch_3'],
  },
  {
    type: 'merge',
    label: '合并',
    description: '合并多路结果',
    icon: 'Merge',
    color: 'text-cyan-500',
    bgColor: 'bg-cyan-500/10',
    borderColor: 'border-cyan-500/30',
    inputs: ['input_1', 'input_2', 'input_3'],
    outputs: ['output'],
  },
  {
    type: 'agent',
    label: 'Agent调用',
    description: '调用外部Agent',
    icon: 'Bot',
    color: 'text-violet-500',
    bgColor: 'bg-violet-500/10',
    borderColor: 'border-violet-500/30',
    inputs: ['input'],
    outputs: ['output'],
  },
  {
    type: 'mcp_tool',
    label: 'MCP工具',
    description: '调用MCP工具',
    icon: 'Wrench',
    color: 'text-orange-500',
    bgColor: 'bg-orange-500/10',
    borderColor: 'border-orange-500/30',
    inputs: ['params'],
    outputs: ['result'],
  },
];

// ── Step Type Options ───────────────────────────────────────────────

export const STEP_TYPE_OPTIONS = [
  { value: 'metadata_retrieval', label: '元数据检索', description: '从 RAG 知识库检索表结构' },
  { value: 'llm_analysis', label: 'LLM意图分析', description: '分析用户意图和元数据需求' },
  { value: 'metadata_supplement', label: '元数据补充', description: '补充缺失的表/字段信息' },
  { value: 'sql_generation', label: 'SQL生成', description: '基于元数据生成SQL' },
  { value: 'sql_execution', label: 'SQL执行', description: '执行SQL查询' },
  { value: 'result_analysis', label: '结果分析', description: '分析查询结果' },
  { value: 'agent_call', label: 'Agent调用', description: '调用外部Agent' },
  { value: 'mcp_tool', label: 'MCP工具', description: '调用MCP工具' },
  { value: 'llm_call', label: '通用LLM调用', description: '通用LLM调用' },
  { value: 'transform', label: '数据转换', description: '数据格式转换' },
  { value: 'condition', label: '条件判断', description: '根据条件分支' },
  { value: 'parallel', label: '并行执行', description: '并行执行多路' },
  { value: 'merge', label: '合并', description: '合并多路结果' },
];
