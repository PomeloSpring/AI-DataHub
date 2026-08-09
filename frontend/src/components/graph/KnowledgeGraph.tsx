import { useCallback, useEffect } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Edge,
  Node,
  MarkerType,
  Position,
  Handle,
  EdgeLabelRenderer,
  getBezierPath,
  useReactFlow,
  ReactFlowProvider,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Loader2, ZoomIn, ZoomOut, Maximize2, RefreshCw } from 'lucide-react';
import { useGraphStore } from '@/stores/graphStore';
import { applyDagreLayout, LayoutDirection } from './utils/layout';

// ── Custom Node Components ─────────────────────────────────────────────

function TableNode({ data }: { data: any }) {
  return (
    <div className="bg-card border-2 border-blue-500 rounded-lg shadow-lg min-w-[160px] max-w-[240px]">
      <Handle type="target" position={Position.Left} className="!bg-blue-500 !w-3 !h-3" />
      <Handle type="source" position={Position.Right} className="!bg-blue-500 !w-3 !h-3" />

      <div className="flex items-center gap-2 px-3 py-2 bg-blue-500/10 border-b rounded-t-lg">
        <div className="w-3 h-3 rounded bg-blue-500 shrink-0" />
        <span className="font-semibold text-sm truncate">{data.label}</span>
      </div>

      {data.comment && (
        <div className="px-3 py-1 text-xs text-muted-foreground border-b truncate">
          {data.comment}
        </div>
      )}

      {data.columns && data.columns.length > 0 && (
        <div className="px-3 py-2 space-y-0.5">
          {data.columns.slice(0, 5).map((col: string) => (
            <div key={col} className="text-xs font-mono text-muted-foreground truncate">
              {col}
            </div>
          ))}
          {data.columns.length > 5 && (
            <div className="text-xs text-muted-foreground italic">
              +{data.columns.length - 5} 更多...
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TermNode({ data }: { data: any }) {
  return (
    <div className="bg-card border-2 border-purple-500 rounded-lg shadow-lg min-w-[160px] max-w-[240px]">
      <Handle type="target" position={Position.Left} className="!bg-purple-500 !w-3 !h-3" />
      <Handle type="source" position={Position.Right} className="!bg-purple-500 !w-3 !h-3" />

      <div className="flex items-center gap-2 px-3 py-2 bg-purple-500/10 border-b rounded-t-lg">
        <div className="w-3 h-3 rounded bg-purple-500 shrink-0" />
        <span className="font-semibold text-sm truncate">{data.name_cn || data.label}</span>
      </div>

      {data.name_en && (
        <div className="px-3 py-1 text-xs text-muted-foreground border-b truncate">
          {data.name_en}
        </div>
      )}

      {data.description && (
        <div className="px-3 py-2 text-xs text-muted-foreground line-clamp-2">
          {data.description}
        </div>
      )}
    </div>
  );
}

function MetricNode({ data }: { data: any }) {
  return (
    <div className="bg-card border-2 border-orange-500 rounded-lg shadow-lg min-w-[160px] max-w-[240px]">
      <Handle type="target" position={Position.Left} className="!bg-orange-500 !w-3 !h-3" />
      <Handle type="source" position={Position.Right} className="!bg-orange-500 !w-3 !h-3" />

      <div className="flex items-center gap-2 px-3 py-2 bg-orange-500/10 border-b rounded-t-lg">
        <div className="w-3 h-3 rounded bg-orange-500 shrink-0" />
        <span className="font-semibold text-sm truncate">{data.name || data.label}</span>
      </div>

      {data.formula && (
        <div className="px-3 py-1 text-xs font-mono text-muted-foreground border-b truncate">
          {data.formula}
        </div>
      )}

      {data.unit && (
        <div className="px-3 py-2 text-xs text-muted-foreground">
          单位: {data.unit}
        </div>
      )}
    </div>
  );
}

function DimensionNode({ data }: { data: any }) {
  return (
    <div className="bg-card border-2 border-teal-500 rounded-lg shadow-lg min-w-[160px] max-w-[240px]">
      <Handle type="target" position={Position.Left} className="!bg-teal-500 !w-3 !h-3" />
      <Handle type="source" position={Position.Right} className="!bg-teal-500 !w-3 !h-3" />

      <div className="flex items-center gap-2 px-3 py-2 bg-teal-500/10 border-b rounded-t-lg">
        <div className="w-3 h-3 rounded bg-teal-500 shrink-0" />
        <span className="font-semibold text-sm truncate">{data.name || data.label}</span>
      </div>

      {data.level !== undefined && (
        <div className="px-3 py-1 text-xs text-muted-foreground border-b">
          层级: {data.level}
        </div>
      )}

      {data.description && (
        <div className="px-3 py-2 text-xs text-muted-foreground line-clamp-2">
          {data.description}
        </div>
      )}
    </div>
  );
}

function ColumnNode({ data }: { data: any }) {
  return (
    <div className="bg-card border-2 border-green-500 rounded-lg shadow-lg min-w-[140px] max-w-[200px]">
      <Handle type="target" position={Position.Left} className="!bg-green-500 !w-3 !h-3" />
      <Handle type="source" position={Position.Right} className="!bg-green-500 !w-3 !h-3" />

      <div className="flex items-center gap-2 px-3 py-2 bg-green-500/10 border-b rounded-t-lg">
        <div className="w-3 h-3 rounded bg-green-500 shrink-0" />
        <span className="font-semibold text-xs truncate">{data.name || data.label}</span>
      </div>

      {data.data_type && (
        <div className="px-3 py-1 text-xs font-mono text-muted-foreground border-b">
          {data.data_type}
        </div>
      )}

      {data.comment && (
        <div className="px-3 py-2 text-xs text-muted-foreground truncate">
          {data.comment}
        </div>
      )}
    </div>
  );
}

function DataSourceNode({ data }: { data: any }) {
  const statusColors: Record<string, string> = {
    active: 'bg-green-500',
    inactive: 'bg-gray-500',
    error: 'bg-red-500',
  };

  return (
    <div className="bg-card border-2 border-cyan-500 rounded-lg shadow-lg min-w-[180px] max-w-[260px]">
      <Handle type="target" position={Position.Left} className="!bg-cyan-500 !w-3 !h-3" />
      <Handle type="source" position={Position.Right} className="!bg-cyan-500 !w-3 !h-3" />

      <div className="flex items-center gap-2 px-3 py-2 bg-cyan-500/10 border-b rounded-t-lg">
        <div className="w-3 h-3 rounded bg-cyan-500 shrink-0" />
        <span className="font-semibold text-sm truncate">{data.name || data.label}</span>
        <div className={`ml-auto w-2 h-2 rounded-full ${statusColors[data.status] || 'bg-gray-500'}`} />
      </div>

      <div className="px-3 py-1 text-xs text-muted-foreground border-b">
        {data.ds_type?.toUpperCase()} {data.host && `• ${data.host}`}
      </div>

      {data.database_name && (
        <div className="px-3 py-1 text-xs font-mono text-muted-foreground border-b">
          {data.database_name}
        </div>
      )}

      {data.description && (
        <div className="px-3 py-2 text-xs text-muted-foreground line-clamp-2">
          {data.description}
        </div>
      )}
    </div>
  );
}

function ETLTaskNode({ data }: { data: any }) {
  const statusColors: Record<string, string> = {
    active: 'bg-green-500',
    paused: 'bg-yellow-500',
    error: 'bg-red-500',
    running: 'bg-blue-500 animate-pulse',
  };

  const typeLabels: Record<string, string> = {
    export: '导出',
    import: '导入',
    transform: '转换',
    sync: '同步',
  };

  return (
    <div className="bg-card border-2 border-amber-500 rounded-lg shadow-lg min-w-[180px] max-w-[260px]">
      <Handle type="target" position={Position.Left} className="!bg-amber-500 !w-3 !h-3" />
      <Handle type="source" position={Position.Right} className="!bg-amber-500 !w-3 !h-3" />

      <div className="flex items-center gap-2 px-3 py-2 bg-amber-500/10 border-b rounded-t-lg">
        <div className="w-3 h-3 rounded bg-amber-500 shrink-0" />
        <span className="font-semibold text-sm truncate">{data.name || data.label}</span>
        <div className={`ml-auto w-2 h-2 rounded-full ${statusColors[data.status] || 'bg-gray-500'}`} />
      </div>

      <div className="px-3 py-1 text-xs text-muted-foreground border-b flex items-center gap-2">
        <span>{typeLabels[data.task_type] || data.task_type}</span>
        {data.schedule && <span>• {data.schedule}</span>}
      </div>

      {data.source_tables && (
        <div className="px-3 py-1 text-xs text-muted-foreground border-b">
          源: {data.source_tables}
        </div>
      )}

      {data.target_tables && (
        <div className="px-3 py-1 text-xs text-muted-foreground border-b">
          目标: {data.target_tables}
        </div>
      )}

      {data.description && (
        <div className="px-3 py-2 text-xs text-muted-foreground line-clamp-2">
          {data.description}
        </div>
      )}
    </div>
  );
}

// ── Custom Edge Component ──────────────────────────────────────────────

function KnowledgeEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  markerEnd,
  style,
}: {
  id: string;
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  data: any;
  markerEnd: any;
  style: any;
}) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
  });

  const edgeColor = data?.color || 'hsl(var(--muted-foreground))';

  return (
    <>
      <path
        id={id}
        d={edgePath}
        markerEnd={markerEnd}
        style={{
          ...style,
          stroke: edgeColor,
          strokeWidth: 2,
        }}
      />
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            className="absolute bg-card border rounded px-2 py-0.5 shadow-sm pointer-events-none text-[10px] text-muted-foreground"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

// ── Node Type Map ──────────────────────────────────────────────────────

const nodeTypes: Record<string, React.ComponentType<any>> = {
  Table: TableNode,
  Column: ColumnNode,
  Term: TermNode,
  Metric: MetricNode,
  Dimension: DimensionNode,
  DataSource: DataSourceNode,
  ETLTask: ETLTaskNode,
};

const edgeTypes: Record<string, React.ComponentType<any>> = {
  knowledge: KnowledgeEdge,
};

// ── Edge Color Map ─────────────────────────────────────────────────────

const edgeColorMap: Record<string, string> = {
  HAS_COLUMN: '#22c55e',
  JOIN: '#3b82f6',
  MAPS_TO: '#a855f7',
  DEFINES: '#f97316',
  USES_DIMENSION: '#14b8a6',
  BELONGS_TO: '#14b8a6',
  DESCRIBES: '#6b7280',
  // Lineage relations
  PRODUCES: '#06b6d4',
  CONSUMES: '#06b6d4',
  FEEDS: '#06b6d4',
  TRANSFORMS: '#f59e0b',
  DEPENDS_ON: '#ef4444',
};

// ── Props ──────────────────────────────────────────────────────────────

interface KnowledgeGraphViewProps {
  graphType: string;
  viewMode: 'view' | 'edit' | 'ask';
  isLoading: boolean;
  onNodeSelect?: (node: any) => void;
  onRefresh?: () => void;
}

// ── Inner Component ────────────────────────────────────────────────────

function KnowledgeGraphInner({
  graphType,
  viewMode,
  isLoading,
  onNodeSelect,
}: KnowledgeGraphViewProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const reactFlow = useReactFlow();

  const { graphData } = useGraphStore();

  // ── Transform data to React Flow format ─────────────────────────────

  useEffect(() => {
    if (!graphData) return;

    const nodes = Array.isArray(graphData.nodes) ? graphData.nodes : [];
    const edges = Array.isArray(graphData.edges) ? graphData.edges : [];

    const flowNodes: Node[] = nodes.map((node) => ({
      id: node.id,
      type: node.label,
      position: { x: 0, y: 0 }, // Will be set by layout
      data: {
        ...node.properties,
        label: node.properties.name || node.properties.name_cn || node.id,
        nodeType: node.label,
      },
    }));

    const flowEdges: Edge[] = edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'knowledge',
      data: {
        label: edge.type,
        color: edgeColorMap[edge.type] || '#6b7280',
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
      },
    }));

    // Apply layout
    const direction: LayoutDirection = graphType === 'data-lineage' ? 'LR' : 'TB';
    const { nodes: layoutedNodes, edges: layoutedEdges } = applyDagreLayout(
      flowNodes,
      flowEdges,
      { direction, nodeWidth: 200, nodeHeight: 100 }
    );

    setNodes(layoutedNodes);
    setEdges(layoutedEdges);

    // Fit view after layout
    setTimeout(() => {
      reactFlow.fitView({ padding: 0.2 });
    }, 100);
  }, [graphData, graphType]);

  // ── Handle node click ───────────────────────────────────────────────

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onNodeSelect?.({
        id: node.id,
        label: node.data.nodeType || node.type,
        properties: node.data,
      });
    },
    [onNodeSelect]
  );

  // ── Handle pane click ───────────────────────────────────────────────

  const onPaneClick = useCallback(() => {
    onNodeSelect?.(null);
  }, [onNodeSelect]);

  // ── Toolbar actions ─────────────────────────────────────────────────

  const handleZoomIn = () => reactFlow.zoomIn();
  const handleZoomOut = () => reactFlow.zoomOut();
  const handleFitView = () => reactFlow.fitView({ padding: 0.2 });

  const handleReLayout = () => {
    const direction: LayoutDirection = graphType === 'data-lineage' ? 'LR' : 'TB';
    const { nodes: layoutedNodes, edges: layoutedEdges } = applyDagreLayout(
      nodes,
      edges,
      { direction, nodeWidth: 200, nodeHeight: 100 }
    );
    setNodes(layoutedNodes);
    setEdges(layoutedEdges);
    setTimeout(() => reactFlow.fitView({ padding: 0.2 }), 100);
  };

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <div className="w-full h-full relative">
      {/* Loading Overlay */}
      {isLoading && (
        <div className="absolute inset-0 bg-background/50 flex items-center justify-center z-50">
          <div className="flex items-center gap-2 bg-card px-4 py-2 rounded-lg shadow-lg">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">加载中...</span>
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="absolute top-4 right-4 z-40 flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={handleZoomIn} className="h-8 w-8 p-0">
          <ZoomIn className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="sm" onClick={handleZoomOut} className="h-8 w-8 p-0">
          <ZoomOut className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="sm" onClick={handleFitView} className="h-8 w-8 p-0">
          <Maximize2 className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="sm" onClick={handleReLayout} className="h-8 w-8 p-0">
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {/* Empty State */}
      {!isLoading && nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-center text-muted-foreground">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-muted flex items-center justify-center">
              <svg
                className="w-8 h-8"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"
                />
              </svg>
            </div>
            <p className="text-sm font-medium">暂无图谱数据</p>
            <p className="text-xs mt-1">请先同步元数据或选择其他图谱类型</p>
          </div>
        </div>
      )}

      {/* React Flow */}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        defaultEdgeOptions={{
          type: 'knowledge',
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        }}
        connectionLineStyle={{ strokeWidth: 2, stroke: 'hsl(var(--primary))' }}
        nodesDraggable={viewMode === 'edit'}
        nodesConnectable={viewMode === 'edit'}
        elementsSelectable={true}
      >
        <Background gap={20} size={1} />
        <Controls />
        <MiniMap
          nodeColor="hsl(var(--primary))"
          maskColor="hsl(var(--background) / 0.7)"
          className="!bg-card !border-border"
        />
      </ReactFlow>

      {/* Stats Badge */}
      <div className="absolute bottom-4 left-4 z-40">
        <Badge variant="outline" className="bg-card/80 backdrop-blur-sm">
          {nodes.length} 节点 · {edges.length} 关系
        </Badge>
      </div>
    </div>
  );
}

// ── Exported Wrapper ───────────────────────────────────────────────────

export default function KnowledgeGraphView(props: KnowledgeGraphViewProps) {
  return (
    <ReactFlowProvider>
      <KnowledgeGraphInner {...props} />
    </ReactFlowProvider>
  );
}
