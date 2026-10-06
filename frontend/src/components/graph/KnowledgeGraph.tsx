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
import { Loader2, ZoomIn, ZoomOut, Maximize2, RefreshCw, Network, Tags } from 'lucide-react';
import { useGraphStore } from '@/stores/graphStore';
import { applyDagreLayout, LayoutOptions } from './utils/layout';

const getLayoutOptions = (graphType: string): LayoutOptions => ({
  direction: graphType === 'data-lineage' ? 'LR' : 'TB',
  nodeWidth: 260,
  nodeHeight: 200,
  ranksep: 120,
  nodesep: 72,
  edgesep: 28,
});

// ── Custom Node Components ─────────────────────────────────────────────

/** 语义展开节点（行为/属性/规则/场景）：卡片 + 类型徽标，遵守 KnowledgeGraph 卡片基准。 */
function TaggedNode({ data }: { data: any }) {
  const cfg: Record<string, { color: string; badge: string }> = {
    Action: { color: '#10b981', badge: '行为' },
    Property: { color: '#22c55e', badge: '属性' },
    Rule: { color: '#f59e0b', badge: '规则' },
    Scenario: { color: '#8b5cf6', badge: '场景' },
  };
  const c = cfg[data.nodeType] || { color: '#64748b', badge: data.nodeType || '语义' };
  return (
    <div className="bg-card border rounded-lg shadow-sm min-w-[150px] max-w-[230px]" style={{ borderColor: c.color }}>
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!w-1.5 !h-1.5" style={{ background: c.color }} />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!w-1.5 !h-1.5" style={{ background: c.color }} />
      <div className="flex items-center gap-2 px-3 py-2 border-b rounded-t-lg" style={{ background: `${c.color}1a` }}>
        <div className="w-3 h-3 rounded shrink-0" style={{ background: c.color }} />
        <span className="font-semibold text-sm truncate">{data.label}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded border ml-auto shrink-0" style={{ color: c.color, borderColor: c.color }}>{c.badge}</span>
      </div>
      {data.comment && (
        <div className="px-3 py-2 text-xs text-muted-foreground line-clamp-2">{data.comment}</div>
      )}
    </div>
  );
}

function TableNode({ data }: { data: any }) {
  return (
    <div className="bg-card border border-blue-500 rounded-lg shadow-sm min-w-[160px] max-w-[240px]">
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!bg-blue-500 !w-1.5 !h-1.5" />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!bg-blue-500 !w-1.5 !h-1.5" />

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
    <div className="bg-card border border-purple-500 rounded-lg shadow-sm min-w-[160px] max-w-[240px]">
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!bg-purple-500 !w-1.5 !h-1.5" />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!bg-purple-500 !w-1.5 !h-1.5" />

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
    <div className="bg-card border border-orange-500 rounded-lg shadow-sm min-w-[160px] max-w-[240px]">
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!bg-orange-500 !w-1.5 !h-1.5" />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!bg-orange-500 !w-1.5 !h-1.5" />

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
    <div className="bg-card border border-teal-500 rounded-lg shadow-sm min-w-[160px] max-w-[240px]">
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!bg-teal-500 !w-1.5 !h-1.5" />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!bg-teal-500 !w-1.5 !h-1.5" />

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
    <div className="bg-card border border-green-500 rounded-lg shadow-sm min-w-[140px] max-w-[200px]">
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!bg-green-500 !w-1.5 !h-1.5" />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!bg-green-500 !w-1.5 !h-1.5" />

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
    <div className="bg-card border border-cyan-500 rounded-lg shadow-sm min-w-[180px] max-w-[260px]">
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!bg-cyan-500 !w-1.5 !h-1.5" />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!bg-cyan-500 !w-1.5 !h-1.5" />

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

/** 本体总览节点: 业务对象卡片(Palantir 式主语视图) —— 名字/key/别名/绑定态, 不展示物理细节 */
function ObjectNode({ data, selected }: { data: any; selected?: boolean }) {
  const sync = (data.binding?.sync_state || '').toLowerCase();
  const syncDot = sync === 'bound' ? 'bg-green-500'
    : sync === 'drifted' || sync === 'orphaned' ? 'bg-amber-500' : 'bg-gray-500';
  return (
    <div className={`w-[232px] h-[112px] bg-card border rounded-xl shadow-sm transition-shadow
      ${selected || data.focused ? 'border-indigo-400 ring-2 ring-indigo-400/20 shadow-md' : 'border-indigo-200/70 dark:border-indigo-400/30'}`}>
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!bg-indigo-400 !w-1.5 !h-1.5" />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!bg-indigo-400 !w-1.5 !h-1.5" />
      <div className="flex items-center gap-2.5 px-3 pt-3 pb-2">
        <div className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-500 shrink-0"><Network className="w-4 h-4" /></div>
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-sm truncate" title={data.label || data.object_key}>{data.label || data.object_key}</div>
          <div className="text-[10px] font-mono text-muted-foreground truncate">{data.object_key || '业务对象'}</div>
        </div>
        <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${syncDot}`} title={sync || '未绑定'} />
      </div>
      <div className="mx-3 border-t border-border/50 pt-2 text-[11px] leading-4 text-muted-foreground line-clamp-2">
        {data.comment || '点击查看对象详情与关联关系'}
      </div>
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
    <div className="bg-card border border-amber-500 rounded-lg shadow-sm min-w-[180px] max-w-[260px]">
      <Handle type="target" position={data.targetPosition ?? Position.Left} className="!bg-amber-500 !w-1.5 !h-1.5" />
      <Handle type="source" position={data.sourcePosition ?? Position.Right} className="!bg-amber-500 !w-1.5 !h-1.5" />

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

// ── Node Type Map ──────────────────────────────────────────────────────

const nodeTypes: Record<string, React.ComponentType<any>> = {
  Table: TableNode,
  Column: ColumnNode,
  Term: TermNode,
  Metric: MetricNode,
  Dimension: DimensionNode,
  DataSource: DataSourceNode,
  ETLTask: ETLTaskNode,
  Object: ObjectNode,
  // 语义展开（M2/M3/M4）：行为/属性/规则/场景
  Action: TaggedNode,
  Property: TaggedNode,
  Rule: TaggedNode,
  Scenario: TaggedNode,
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

    const flowNodes: Node[] = nodes.map((node) => ({
      id: node.id,
      type: node.label,
      ...(node.label === 'Object' ? { width: 232, height: 112 } : {}),
      position: { x: 0, y: 0 }, // Will be set by layout
      data: {
        ...node.properties,
        // label 键位修复: 后端 properties.label 为主展示名(此前误读 name 导致显示裸 IRI)
        label: node.properties.label || node.properties.name || node.properties.name_cn || node.id,
        nodeType: node.label,
      },
    }));

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

    // Apply layout
    const { nodes: layoutedNodes, edges: layoutedEdges } = applyDagreLayout(
      flowNodes,
      flowEdges,
      getLayoutOptions(graphType)
    );

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

  const handleZoomIn = () => reactFlow.zoomIn();
  const handleZoomOut = () => reactFlow.zoomOut();
  const handleFitView = () => reactFlow.fitView({ padding: 0.2 });

  const handleReLayout = () => {
    const { nodes: layoutedNodes, edges: layoutedEdges } = applyDagreLayout(
      nodes,
      edges,
      getLayoutOptions(graphType)
    );
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
          nodeColor="#a5b4fc"
          nodeBorderRadius={4}
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
