import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  BaseEdge,
  EdgeProps,
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
import { Loader2, ZoomIn, ZoomOut, Maximize2, RefreshCw, Tags } from 'lucide-react';
import { useGraphStore } from '@/stores/graphStore';
import { applyDagreLayout, applyForceLayout, LayoutOptions } from './utils/layout';

// ── 图数据库风格基准：分类着色圆形节点 + 顶部图例 + 力导向布局 ──────────
// 参考图数据库可视化惯例：节点为实心圆、按类型着色、标签压在圆内；
// 图面只放关系主干，明细一律下钻侧栏（ui-resource-display/graph 规范）。

const getLayoutOptions = (graphType: string): LayoutOptions => ({
  direction: graphType === 'data-lineage' ? 'LR' : 'TB',
  nodeWidth: 120,
  nodeHeight: 120,
  ranksep: 120,
  nodesep: 72,
  edgesep: 28,
});

// ── 节点类型配置（全平台统一配色，图例同源） ─────────────────────────

export const NODE_CONFIG: Record<string, { color: string; label: string }> = {
  Object: { color: '#6366f1', label: '业务对象' },
  Table: { color: '#3b82f6', label: '表' },
  Column: { color: '#22c55e', label: '字段' },
  Term: { color: '#a855f7', label: '业务术语' },
  Metric: { color: '#f97316', label: '指标' },
  Dimension: { color: '#14b8a6', label: '维度' },
  DataSource: { color: '#06b6d4', label: '数据源' },
  ETLTask: { color: '#f59e0b', label: '同步任务' },
  Action: { color: '#10b981', label: '行为' },
  Property: { color: '#22c55e', label: '属性' },
  Rule: { color: '#f59e0b', label: '规则' },
  Scenario: { color: '#8b5cf6', label: '场景' },
};

/** 圆节点尺寸随标签长度收敛（参考图：圆的大小托住标签）。 */
export function circleSize(label: string): number {
  const chars = Array.from(String(label || '')).length;
  return Math.max(64, Math.min(150, 44 + chars * 13));
}

// ── 圆形节点（统一渲染，类型只决定颜色） ─────────────────────────────

function CircleNode({ data, selected }: { data: any; selected?: boolean }) {
  const cfg = NODE_CONFIG[data.nodeType] || { color: '#64748b', label: data.nodeType || '节点' };
  const label = String(data.label || cfg.label);
  const chars = Array.from(label).length;
  const size = data.size || circleSize(label);
  const fontSize = chars <= 3 ? 13 : chars <= 5 ? 12 : chars <= 8 ? 11 : 10;
  return (
    <div
      className="relative flex items-center justify-center rounded-full text-white shadow-sm select-none transition-shadow"
      style={{
        width: size,
        height: size,
        background: cfg.color,
        outline: selected || data.focused ? '2px solid hsl(var(--primary))' : 'none',
        outlineOffset: 2,
      }}
      title={data.comment || label}
    >
      <Handle type="target" position={data.targetPosition ?? Position.Top} className="!w-1.5 !h-1.5 !opacity-0" />
      <Handle type="source" position={data.sourcePosition ?? Position.Bottom} className="!w-1.5 !h-1.5 !opacity-0" />
      <span className="font-semibold text-center leading-tight px-1.5 line-clamp-2" style={{ fontSize }}>
        {label}
      </span>
      {data.statusDot && (
        <span
          className="absolute right-0 bottom-0 w-2.5 h-2.5 rounded-full border-2 border-background"
          style={{ background: data.statusDot }}
          title={data.statusText || ''}
        />
      )}
    </div>
  );
}

// ── Custom Edge Component（护栏：BaseEdge + 显式 fill:none） ─────────

function KnowledgeEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
  style,
  selected,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.18,
  });

  const emphasized = Boolean(data?.emphasized || selected);
  const edgeColor = emphasized ? 'hsl(var(--primary))' : String(data?.color || '#94a3b8');

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        interactionWidth={16}
        style={{
          ...style,
          // SVG 开放曲线也会默认填充；必须显式禁用，避免出现黑色楔形块。
          fill: 'none',
          stroke: edgeColor,
          strokeWidth: emphasized ? 1.8 : 1.2,
          strokeLinecap: 'round',
          opacity: data?.muted ? 0.12 : emphasized ? 1 : 0.65,
        }}
      />
      {Boolean(data?.label) && (data?.showLabel || emphasized) && (
        <EdgeLabelRenderer>
          <div
            className="absolute bg-card/95 border border-border/70 rounded-md px-2 py-1 shadow-sm pointer-events-none text-[11px] text-foreground whitespace-nowrap"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              zIndex: emphasized ? 20 : 1,
            }}
          >
            {String(data?.label)}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

// ── Node Type Map（全部映射到圆节点，类型即配色） ─────────────────────

const circleOf = (type: string) => (props: any) => (
  <CircleNode {...props} data={{ ...props.data, nodeType: props.data?.nodeType || type }} />
);

const nodeTypes: Record<string, React.ComponentType<any>> = {
  Table: circleOf('Table'),
  Column: circleOf('Column'),
  Term: circleOf('Term'),
  Metric: circleOf('Metric'),
  Dimension: circleOf('Dimension'),
  DataSource: circleOf('DataSource'),
  ETLTask: circleOf('ETLTask'),
  Object: circleOf('Object'),
  Action: circleOf('Action'),
  Property: circleOf('Property'),
  Rule: circleOf('Rule'),
  Scenario: circleOf('Scenario'),
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
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [showLabels, setShowLabels] = useState(false);

  const { graphData } = useGraphStore();
  const focusedId = hoveredNodeId ?? selectedNodeId;
  const relatedIds = useMemo(() => {
    const ids = new Set<string>();
    if (focusedId) {
      ids.add(focusedId);
      edges.forEach((edge) => {
        if (edge.source === focusedId || edge.target === focusedId) {
          ids.add(edge.source);
          ids.add(edge.target);
        }
      });
    }
    return ids;
  }, [focusedId, edges]);
  const visibleNodes = useMemo(() => nodes.map((node) => ({
    ...node,
    data: { ...node.data, focused: node.id === focusedId },
    style: { ...node.style, opacity: focusedId && !relatedIds.has(node.id) ? 0.3 : 1, transition: 'opacity 150ms' },
  })), [nodes, focusedId, relatedIds]);
  const visibleEdges = useMemo(() => edges.map((edge) => {
    const emphasized = edge.id === hoveredEdgeId || edge.selected ||
      Boolean(focusedId && (edge.source === focusedId || edge.target === focusedId));
    return {
      ...edge,
      zIndex: emphasized ? 1 : 0,
      data: { ...edge.data, emphasized, muted: Boolean(focusedId && !emphasized), showLabel: showLabels },
    };
  }), [edges, focusedId, hoveredEdgeId, showLabels]);

  // 图例（参考图顶栏样式）：按节点类型取色，与 NODE_CONFIG 同源
  const legendItems = useMemo(() => {
    const seen = new Map<string, string>();
    (nodes as any[]).forEach((node) => {
      const t = node.data?.nodeType || node.type || '';
      if (t && !seen.has(t)) seen.set(t, NODE_CONFIG[t]?.label || t);
    });
    return [...seen.entries()].map(([type, label]) => ({
      type, label, color: NODE_CONFIG[type]?.color || '#64748b',
    }));
  }, [nodes]);

  // ── Transform data to React Flow format ─────────────────────────────

  useEffect(() => {
    setSelectedNodeId(null);
    setHoveredNodeId(null);
    setHoveredEdgeId(null);
    if (!graphData) {
      setNodes([]);
      setEdges([]);
      return;
    }

    const nodes = Array.isArray(graphData.nodes) ? graphData.nodes : [];
    const edges = Array.isArray(graphData.edges) ? graphData.edges : [];

    const flowNodes: Node[] = nodes.map((node) => {
      // label 键位修复: 后端 properties.label 为主展示名(此前误读 name 导致显示裸 IRI)
      const label = node.properties.label || node.properties.name || node.properties.name_cn || node.id;
      const size = circleSize(label);
      const status = String(node.properties.status || node.properties.binding?.sync_state || '').toLowerCase();
      const statusDot = ['active', 'bound', 'running'].includes(status) ? '#22c55e'
        : ['error', 'drifted', 'orphaned'].includes(status) ? '#f59e0b'
        : status ? '#94a3b8' : '';
      return {
        id: node.id,
        type: node.label,
        width: size,
        height: size,
        position: { x: 0, y: 0 }, // Will be set by layout
        data: {
          ...node.properties,
          label,
          size,
          nodeType: node.label,
          statusDot,
          statusText: status,
        },
      };
    });

    const flowEdges: Edge[] = edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'knowledge',
      data: {
        // 总览视图: 边上标 Link 名字与基数(谓词字典即图例)
        label: edge.properties?.cardinality
          ? `${edge.type} · ${edge.properties.cardinality}`
          : edge.type,
        color: edgeColorMap[edge.type] || (graphType === 'ontology-overview' ? '#a5b4fc' : '#94a3b8'),
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 12,
        height: 12,
        color: edgeColorMap[edge.type] || (graphType === 'ontology-overview' ? '#a5b4fc' : '#94a3b8'),
      },
    }));

    // 布局: 血缘保留 LR 分层(方向语义); 其余图谱走力导向(图数据库观感)
    const { nodes: layoutedNodes, edges: layoutedEdges } = layoutGraph(flowNodes, flowEdges);

    setNodes(layoutedNodes);
    setEdges(layoutedEdges);

    // Fit view after layout
    const timer = setTimeout(() => {
      reactFlow.fitView({ padding: 0.15, maxZoom: 1 });
    }, 100);
    return () => clearTimeout(timer);
  }, [graphData, graphType]);

  // ── Handle node click ───────────────────────────────────────────────

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setSelectedNodeId(node.id);
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
    setSelectedNodeId(null);
    setHoveredNodeId(null);
    setHoveredEdgeId(null);
    onNodeSelect?.(null);
  }, [onNodeSelect]);

  // ── Toolbar actions ─────────────────────────────────────────────────

  const layoutGraph = (inNodes: Node[], inEdges: Edge[]) =>
    graphType === 'data-lineage'
      ? applyDagreLayout(inNodes, inEdges, getLayoutOptions(graphType))
      : applyForceLayout(inNodes, inEdges);

  const handleZoomIn = () => reactFlow.zoomIn();
  const handleZoomOut = () => reactFlow.zoomOut();
  const handleFitView = () => reactFlow.fitView({ padding: 0.2 });

  const handleReLayout = () => {
    const { nodes: layoutedNodes, edges: layoutedEdges } = layoutGraph(nodes, edges);
    setNodes(layoutedNodes);
    setEdges(layoutedEdges);
    setTimeout(() => reactFlow.fitView({ padding: 0.2 }), 100);
  };

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <div className="w-full h-full relative bg-muted/10">
      {/* Loading Overlay */}
      {isLoading && (
        <div className="absolute inset-0 bg-background/50 flex items-center justify-center z-50">
          <div className="flex items-center gap-2 bg-card px-4 py-2 rounded-lg shadow-lg">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">加载中...</span>
          </div>
        </div>
      )}

      {/* 图例（分类着色，参考图顶栏） */}
      {legendItems.length > 0 && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 px-3 py-1.5 rounded-full border bg-card/95 shadow-sm max-w-[80%] flex-wrap justify-center">
          {legendItems.map((item) => (
            <span key={item.type} className="flex items-center gap-1.5 text-[11px] text-muted-foreground whitespace-nowrap">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: item.color }} />
              {item.label}
            </span>
          ))}
        </div>
      )}

      {/* Toolbar */}
      <div className="absolute top-3 right-3 z-40 flex items-center gap-1 p-1 rounded-xl border bg-card/95 shadow-sm">
        <Button variant={showLabels ? 'secondary' : 'ghost'} size="sm" className="h-8 text-xs"
          aria-label="显示全部关系名" aria-pressed={showLabels} onClick={() => setShowLabels((value) => !value)}>
          <Tags className="h-3.5 w-3.5 mr-1" />关系名称
        </Button>
        <span className="h-4 border-l mx-1" />
        <Button variant="ghost" size="sm" onClick={handleZoomIn} aria-label="放大图谱" className="h-8 w-8 p-0">
          <ZoomIn className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="sm" onClick={handleZoomOut} aria-label="缩小图谱" className="h-8 w-8 p-0">
          <ZoomOut className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="sm" onClick={handleFitView} aria-label="适应画布" className="h-8 w-8 p-0">
          <Maximize2 className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="sm" onClick={handleReLayout} aria-label="重新布局" className="h-8 w-8 p-0">
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
        nodes={visibleNodes}
        edges={visibleEdges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onNodeMouseEnter={(_, node) => setHoveredNodeId(node.id)}
        onNodeMouseLeave={() => setHoveredNodeId(null)}
        onEdgeMouseEnter={(_, edge) => setHoveredEdgeId(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdgeId(null)}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        defaultEdgeOptions={{
          type: 'knowledge',
          markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: '#94a3b8' },
        }}
        connectionLineStyle={{ strokeWidth: 2, stroke: 'hsl(var(--primary))' }}
        nodesDraggable={viewMode === 'edit'}
        nodesConnectable={viewMode === 'edit'}
        elementsSelectable={true}
      >
        <Background gap={24} size={0.6} color="hsl(var(--muted-foreground) / 0.2)" />
        <MiniMap
          nodeColor={(node: any) => NODE_CONFIG[node.data?.nodeType]?.color || '#a5b4fc'}
          nodeBorderRadius={32}
          maskColor="hsl(var(--background) / 0.7)"
          className="!bg-card/80 !border !border-border/60 !rounded-lg !shadow-none"
          style={{ width: 140, height: 90 }}
        />
      </ReactFlow>

      {/* Stats Badge */}
      <div className="absolute bottom-4 left-4 z-40">
        <Badge variant="outline" className="bg-card/80 backdrop-blur-sm">
          {nodes.length} 节点 · {edges.length} 关系
          <span className="ml-2 font-normal text-muted-foreground">{focusedId ? '已聚焦关联 · 点击空白恢复' : '悬停或点击节点查看关系'}</span>
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
