import { useState, useCallback, useMemo, useEffect } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Node,
  Edge,
  MarkerType,
  Position,
  Handle,
  NodeProps,
  EdgeProps,
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useReactFlow,
  ReactFlowProvider,
  FitViewOptions,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import {
  Search, RefreshCw, Maximize2, X,
  Table2, Columns3, GitBranch, FileText, BarChart3,
  ArrowUpRight, ArrowDownRight, Activity, Loader2,
} from 'lucide-react';
import { lineageApi } from '@/api/lineage';
import { toast } from 'sonner';

// ── Types ──────────────────────────────────────────────────────────────

type LineageNodeType = 'table' | 'column' | 'etl_job' | 'report' | 'metric';

interface LineageNodeData {
  id: number;
  name: string;
  /** 带数据源维度的限定名（数据源.表 / 数据源.表.列） */
  node_id?: string;
  node_type: LineageNodeType;
  metadata?: Record<string, any>;
  description?: string;
}

interface LineageEdgeData {
  id: number;
  source_id: number;
  target_id: number;
  edge_type: string;
  metadata?: Record<string, any>;
}

interface LineageGraphResponse {
  nodes: LineageNodeData[];
  edges: LineageEdgeData[];
}

// ── Node color & icon mapping ──────────────────────────────────────────

const NODE_CONFIG: Record<LineageNodeType, { color: string; bg: string; border: string; icon: React.ComponentType<any> }> = {
  table:    { color: 'text-blue-600',   bg: 'bg-blue-50 dark:bg-blue-950',   border: 'border-blue-300 dark:border-blue-700',   icon: Table2 },
  column:   { color: 'text-green-600',  bg: 'bg-green-50 dark:bg-green-950',  border: 'border-green-300 dark:border-green-700',  icon: Columns3 },
  etl_job:  { color: 'text-orange-600', bg: 'bg-orange-50 dark:bg-orange-950', border: 'border-orange-300 dark:border-orange-700', icon: GitBranch },
  report:   { color: 'text-purple-600', bg: 'bg-purple-50 dark:bg-purple-950', border: 'border-purple-300 dark:border-purple-700', icon: FileText },
  metric:   { color: 'text-red-600',    bg: 'bg-red-50 dark:bg-red-950',     border: 'border-red-300 dark:border-red-700',     icon: BarChart3 },
};

const NODE_TYPE_LABELS: Record<LineageNodeType, string> = {
  table: 'Table',
  column: 'Column',
  etl_job: 'ETL Job',
  report: 'Report',
  metric: 'Metric',
};

const FIT_VIEW_OPTIONS: FitViewOptions = { padding: 0.2 };

// ── Custom Lineage Node ────────────────────────────────────────────────

function LineageNodeComponent({ data, selected }: NodeProps) {
  const nodeType = (data.nodeType as LineageNodeType) || 'table';
  const config = NODE_CONFIG[nodeType] || NODE_CONFIG.table;
  const Icon = config.icon;
  const label = typeof data.label === 'string' ? data.label : '';
  const description = typeof data.description === 'string' ? data.description : '';

  return (
    <div
      className={`
        rounded-lg border-2 shadow-md min-w-[160px] max-w-[240px] transition-all
        ${config.bg} ${config.border}
        ${selected ? 'ring-2 ring-primary ring-offset-2' : ''}
      `}
    >
      <Handle type="target" position={Position.Left} className="!bg-primary !w-2.5 !h-2.5" />
      <Handle type="source" position={Position.Right} className="!bg-primary !w-2.5 !h-2.5" />

      <div className={`flex items-center gap-2 px-3 py-2 border-b ${config.border}`}>
        <Icon className={`h-4 w-4 shrink-0 ${config.color}`} />
        <span className="font-semibold text-sm truncate">{label}</span>
      </div>

      <div className="px-3 py-1.5 flex items-center gap-1.5">
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
          {NODE_TYPE_LABELS[nodeType]}
        </Badge>
        {description && (
          <span className="text-[10px] text-muted-foreground truncate">
            {description}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Custom Edge with label ─────────────────────────────────────────────

function LineageEdge({
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
  // 与本体图谱（KnowledgeGraph）同款连线风格：柔和贝塞尔 + 细灰线 + 圆角，
  // fill:none 必须显式设置（SVG 开放曲线默认填充会出现黑色楔形块）
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.18,
  });

  const emphasized = Boolean(selected);
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
          fill: 'none',
          stroke: edgeColor,
          strokeWidth: emphasized ? 1.8 : 1.2,
          strokeLinecap: 'round',
          opacity: emphasized ? 1 : 0.65,
        }}
      />
      {typeof data?.edgeType === 'string' && (
        <EdgeLabelRenderer>
          <div
            className="absolute bg-card/95 border border-border/70 rounded-md px-2 py-0.5 shadow-sm pointer-events-none nodrag nopan text-[10px] text-muted-foreground whitespace-nowrap"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              zIndex: emphasized ? 20 : 1,
            }}
          >
            {String(data.edgeType)}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

// ── Node Detail Panel ──────────────────────────────────────────────────

function NodeDetailPanel({
  node,
  upstreamNodes,
  downstreamNodes,
  columnMappings,
  onClose,
  onImpactAnalysis,
}: {
  node: LineageNodeData;
  upstreamNodes: LineageNodeData[];
  downstreamNodes: LineageNodeData[];
  columnMappings: Array<{ from: string; to: string }>;
  onClose: () => void;
  onImpactAnalysis: (nodeId: number) => void;
}) {
  const config = NODE_CONFIG[node.node_type] || NODE_CONFIG.table;
  const Icon = config.icon;

  return (
    <div className="w-[320px] border-l bg-card flex flex-col h-full shrink-0">
      <div className="flex items-center justify-between p-4 border-b">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          <Icon className={`h-4 w-4 ${config.color}`} />
          Node Details
        </h3>
        <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {/* Basic Info */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{NODE_TYPE_LABELS[node.node_type]}</Badge>
              <span className="text-xs text-muted-foreground">ID: {node.id}</span>
            </div>
            <h4 className="font-medium text-base">{node.name}</h4>
            {node.description && (
              <p className="text-sm text-muted-foreground">{node.description}</p>
            )}
          </div>

          <Separator />

          {/* Metadata */}
          {node.metadata && Object.keys(node.metadata).length > 0 && (
            <div className="space-y-2">
              <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Metadata
              </h5>
              <div className="space-y-1">
                {Object.entries(node.metadata).map(([key, value]) => (
                  <div key={key} className="flex items-start gap-2 text-xs">
                    <span className="text-muted-foreground shrink-0 font-medium">{key}:</span>
                    <span className="font-mono break-all">
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <Separator />

          {/* Upstream Sources */}
          <div className="space-y-2">
            <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
              <ArrowUpRight className="h-3 w-3" />
              Upstream Sources ({upstreamNodes.length})
            </h5>
            {upstreamNodes.length > 0 ? (
              <div className="space-y-1">
                {upstreamNodes.map((n) => {
                  const nc = NODE_CONFIG[n.node_type] || NODE_CONFIG.table;
                  const NIcon = nc.icon;
                  return (
                    <div
                      key={n.id}
                      className="flex items-center gap-2 p-2 rounded border bg-muted/30 text-xs"
                    >
                      <NIcon className={`h-3 w-3 shrink-0 ${nc.color}`} />
                      <span className="font-medium truncate">{n.node_id || n.name}</span>
                      <Badge variant="outline" className="ml-auto text-[9px] px-1 py-0">
                        {NODE_TYPE_LABELS[n.node_type]}
                      </Badge>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground italic">No upstream sources</p>
            )}
          </div>

          <Separator />

          {/* Downstream Targets */}
          <div className="space-y-2">
            <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
              <ArrowDownRight className="h-3 w-3" />
              Downstream Targets ({downstreamNodes.length})
            </h5>
            {downstreamNodes.length > 0 ? (
              <div className="space-y-1">
                {downstreamNodes.map((n) => {
                  const nc = NODE_CONFIG[n.node_type] || NODE_CONFIG.table;
                  const NIcon = nc.icon;
                  return (
                    <div
                      key={n.id}
                      className="flex items-center gap-2 p-2 rounded border bg-muted/30 text-xs"
                    >
                      <NIcon className={`h-3 w-3 shrink-0 ${nc.color}`} />
                      <span className="font-medium truncate">{n.node_id || n.name}</span>
                      <Badge variant="outline" className="ml-auto text-[9px] px-1 py-0">
                        {NODE_TYPE_LABELS[n.node_type]}
                      </Badge>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground italic">No downstream targets</p>
            )}
          </div>

          {/* Column-level mappings (table nodes) */}
          {node.node_type === 'table' && (
            <>
              <Separator />
              <div className="space-y-2">
                <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  字段映射 ({columnMappings.length})
                </h5>
                {columnMappings.length > 0 ? (
                  <div className="space-y-1">
                    {columnMappings.map((m, i) => (
                      <div key={i} className="flex items-center gap-1.5 text-xs px-2 py-1 rounded border bg-muted/30">
                        <span className="font-mono truncate">{m.from}</span>
                        <ArrowDownRight className="h-3 w-3 shrink-0 text-muted-foreground" />
                        <span className="font-mono truncate">{m.to}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground italic">暂无字段级映射</p>
                )}
              </div>
            </>
          )}

          <Separator />

          {/* Impact Analysis */}
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => onImpactAnalysis(node.id)}
          >
            <Activity className="h-3.5 w-3.5 mr-2" />
            Impact Analysis
          </Button>
        </div>
      </ScrollArea>
    </div>
  );
}

// ── Main Component (inner) ─────────────────────────────────────────────

const nodeTypes = { lineageNode: LineageNodeComponent };
const edgeTypes = { lineageEdge: LineageEdge };

function LineageGraphInner() {
  const reactFlow = useReactFlow();

  // Data state
  const [rawNodes, setRawNodes] = useState<LineageNodeData[]>([]);
  const [rawEdges, setRawEdges] = useState<LineageEdgeData[]>([]);
  const [loading, setLoading] = useState(false);

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<string>('all');
  // 字段级血缘默认不展示（列节点会把图打碎）：默认仅表级主视图，
  // 字段映射在选中表节点的详情面板下钻；需要时可打开开关。
  const [showColumns, setShowColumns] = useState(false);
  const [selectedNode, setSelectedNode] = useState<LineageNodeData | null>(null);

  // ReactFlow state
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // ── Fetch data ──────────────────────────────────────────────────────

  const fetchGraph = useCallback(async () => {
    // 血缘为全局共享（工作空间概念已退役）：workspace 参数固定 0，
    // 后端按 IN (ws, 0) 兼容全局行；不再用工作空间作请求门禁。
    setLoading(true);
    try {
      const { data } = await lineageApi.getGraph(0);
      const graph = data as LineageGraphResponse;
      setRawNodes(graph.nodes || []);
      setRawEdges(graph.edges || []);
    } catch (err) {
      console.error('Failed to fetch lineage graph:', err);
      toast.error('Failed to load lineage graph');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  // ── Build node lookup ──────────────────────────────────────────────

  const nodeMap = useMemo(() => {
    const map = new Map<number, LineageNodeData>();
    for (const n of rawNodes) map.set(n.id, n);
    return map;
  }, [rawNodes]);

  // ── Upstream / Downstream for selected node ────────────────────────

  const { upstreamNodes, downstreamNodes } = useMemo(() => {
    if (!selectedNode) return { upstreamNodes: [], downstreamNodes: [] };
    const upstream: LineageNodeData[] = [];
    const downstream: LineageNodeData[] = [];
    for (const e of rawEdges) {
      if (e.target_id === selectedNode.id) {
        const src = nodeMap.get(e.source_id);
        if (src) upstream.push(src);
      }
      if (e.source_id === selectedNode.id) {
        const tgt = nodeMap.get(e.target_id);
        if (tgt) downstream.push(tgt);
      }
    }
    return { upstreamNodes: upstream, downstreamNodes: downstream };
  }, [selectedNode, rawEdges, nodeMap]);

  // ── Filtered data ─────────────────────────────────────────────────

  const filteredNodeIds = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    return new Set(
      rawNodes
        .filter((n) => {
          if (!showColumns && n.node_type === 'column') return false;
          if (filterType !== 'all' && n.node_type !== filterType) return false;
          const label = n.node_id || n.name || '';
          if (q && !label.toLowerCase().includes(q)) return false;
          return true;
        })
        .map((n) => n.id),
    );
  }, [rawNodes, searchQuery, filterType, showColumns]);

  // ── Layout: topological sort with layered placement ───────────────

  const buildLayout = useCallback(
    (nodeIds: Set<number>, edges: LineageEdgeData[]) => {
      const relevantEdges = edges.filter(
        (e) => nodeIds.has(e.source_id) && nodeIds.has(e.target_id),
      );

      // Build adjacency and in-degree
      const inDegree = new Map<number, number>();
      const adj = new Map<number, number[]>();
      for (const id of nodeIds) {
        inDegree.set(id, 0);
        adj.set(id, []);
      }
      for (const e of relevantEdges) {
        inDegree.set(e.target_id, (inDegree.get(e.target_id) || 0) + 1);
        adj.get(e.source_id)?.push(e.target_id);
      }

      // BFS layering (Kahn's algorithm)
      const layers: number[][] = [];
      const queue: number[] = [];
      for (const [id, deg] of inDegree) {
        if (deg === 0) queue.push(id);
      }

      const visited = new Set<number>();
      while (queue.length > 0) {
        const layer: number[] = [];
        const nextQueue: number[] = [];
        for (const id of queue) {
          if (visited.has(id)) continue;
          visited.add(id);
          layer.push(id);
          for (const child of adj.get(id) || []) {
            const newDeg = (inDegree.get(child) || 1) - 1;
            inDegree.set(child, newDeg);
            if (newDeg === 0) nextQueue.push(child);
          }
        }
        if (layer.length > 0) layers.push(layer);
        queue.length = 0;
        queue.push(...nextQueue);
      }

      // Handle nodes not reachable (cycles or disconnected)
      const unvisited = [...nodeIds].filter((id) => !visited.has(id));
      if (unvisited.length > 0) layers.push(unvisited);

      // Position nodes
      const NODE_W = 200;
      const NODE_H = 80;
      const GAP_X = 100;
      const GAP_Y = 60;
      const positions = new Map<number, { x: number; y: number }>();

      for (let col = 0; col < layers.length; col++) {
        const layer = layers[col];
        const totalHeight = layer.length * NODE_H + (layer.length - 1) * GAP_Y;
        const startY = -totalHeight / 2;
        for (let row = 0; row < layer.length; row++) {
          positions.set(layer[row], {
            x: col * (NODE_W + GAP_X),
            y: startY + row * (NODE_H + GAP_Y),
          });
        }
      }

      return positions;
    },
    [],
  );

  // ── Rebuild ReactFlow nodes & edges when data or filters change ────

  useEffect(() => {
    const positions = buildLayout(filteredNodeIds, rawEdges);

    const rfNodes: Node[] = rawNodes
      .filter((n) => filteredNodeIds.has(n.id))
      .map((n) => ({
        id: String(n.id),
        type: 'lineageNode',
        position: positions.get(n.id) || { x: 0, y: 0 },
        data: {
          label: n.node_id || n.name,
          nodeType: n.node_type,
          description: n.description || '',
          metadata: n.metadata || {},
        },
      }));

    const nodeIdSet = new Set([...filteredNodeIds].map(String));
    const rfEdges: Edge[] = rawEdges
      .filter((e) => nodeIdSet.has(String(e.source_id)) && nodeIdSet.has(String(e.target_id)))
      .map((e) => ({
        id: `edge-${e.id}`,
        source: String(e.source_id),
        target: String(e.target_id),
        type: 'lineageEdge',
        data: { edgeType: e.edge_type },
        markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: '#94a3b8' },
      }));

    setNodes(rfNodes);
    setEdges(rfEdges);
  }, [rawNodes, rawEdges, filteredNodeIds, buildLayout, setNodes, setEdges]);

  // ── Fit view after layout ─────────────────────────────────────────

  useEffect(() => {
    if (nodes.length > 0) {
      const timer = setTimeout(() => {
        reactFlow.fitView(FIT_VIEW_OPTIONS);
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [nodes.length, reactFlow]);

  // ── Handlers ──────────────────────────────────────────────────────

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const raw = nodeMap.get(Number(node.id));
      if (raw) setSelectedNode(raw);
    },
    [nodeMap],
  );

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
  }, []);

  const handleRefresh = useCallback(() => {
    setSelectedNode(null);
    fetchGraph();
  }, [fetchGraph]);

  const handleFitView = useCallback(() => {
    reactFlow.fitView(FIT_VIEW_OPTIONS);
  }, [reactFlow]);

  // ── Column-level mappings for selected table (detail panel drill-down) ──

  const columnMappings = useMemo(() => {
    if (!selectedNode || selectedNode.node_type !== 'table') return [];
    const prefix = (selectedNode.node_id || '') + '.';
    return rawEdges
      .map((e) => ({ src: nodeMap.get(e.source_id), tgt: nodeMap.get(e.target_id) }))
      .filter(({ src }) => src?.node_type === 'column' && (src.node_id || '').startsWith(prefix))
      .map(({ src, tgt }) => ({
        from: (src!.node_id || src!.name).split('.').slice(-2).join('.'),
        to: tgt ? (tgt.node_id || tgt.name).split('.').slice(-2).join('.') : '?',
      }));
  }, [selectedNode, rawEdges, nodeMap]);

  const handleImpactAnalysis = useCallback(
    async (nodeId: number) => {
      try {
        const { data } = await lineageApi.getImpact(nodeId, 0);
        toast.success(`Impact analysis complete. ${data?.affected_nodes?.length ?? 0} affected nodes found.`);
      } catch {
        toast.error('Failed to run impact analysis');
      }
    },
    [],
  );

  // ── Node type filter options ──────────────────────────────────────

  const nodeTypeOptions = useMemo(() => {
    const types = new Set(rawNodes.map((n) => n.node_type));
    return Array.from(types).sort();
  }, [rawNodes]);

  // ── Stats ─────────────────────────────────────────────────────────

  const stats = useMemo(() => {
    const typeCounts = new Map<string, number>();
    for (const n of rawNodes) {
      typeCounts.set(n.node_type, (typeCounts.get(n.node_type) || 0) + 1);
    }
    return { total: rawNodes.length, edges: rawEdges.length, typeCounts };
  }, [rawNodes, rawEdges]);

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full w-full">
      {/* Toolbar */}
      <div className="flex items-center gap-3 p-3 border-b bg-card shrink-0">
        <div className="flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-primary" />
          <h1 className="font-semibold text-base">Data Lineage</h1>
        </div>

        <Separator orientation="vertical" className="h-6" />

        {/* Search */}
        <div className="relative w-[220px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            placeholder="Search nodes..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-8 pl-8 text-xs"
          />
        </div>

        {/* Filter by type */}
        <Select value={filterType} onValueChange={setFilterType}>
          <SelectTrigger className="w-[140px] h-8 text-xs">
            <SelectValue placeholder="All Types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            {nodeTypeOptions.map((t) => (
              <SelectItem key={t} value={t}>
                {NODE_TYPE_LABELS[t as LineageNodeType] || t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Column-level lineage toggle */}
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer whitespace-nowrap">
          <Switch checked={showColumns} onCheckedChange={setShowColumns} />
          字段级血缘
        </label>

        <div className="flex-1" />

        {/* Stats */}
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>{stats.total} nodes</span>
          <span>{stats.edges} edges</span>
          {filterType !== 'all' && (
            <span className="text-primary">
              {filteredNodeIds.size} shown
            </span>
          )}
        </div>

        <Separator orientation="vertical" className="h-6" />

        {/* Actions */}
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={handleRefresh}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          )}
          Refresh
        </Button>

        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={handleFitView}
        >
          <Maximize2 className="h-3.5 w-3.5 mr-1.5" />
          Fit View
        </Button>
      </div>

      {/* Graph + Detail Panel */}
      <div className="flex flex-1 min-h-0">
        {/* ReactFlow Canvas */}
        <div className="flex-1 relative">
          {loading && nodes.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="h-8 w-8 animate-spin" />
                <span className="text-sm">Loading lineage graph...</span>
              </div>
            </div>
          ) : nodes.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center text-muted-foreground">
                <GitBranch className="h-12 w-12 mx-auto mb-3 opacity-30" />
                <p className="text-sm">No lineage data found</p>
                <p className="text-xs mt-1">
                  {searchQuery || filterType !== 'all'
                    ? 'Try adjusting your search or filters'
                    : 'Lineage nodes will appear here once configured'}
                </p>
              </div>
            </div>
          ) : null}

          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={handleNodeClick}
            onPaneClick={handlePaneClick}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={FIT_VIEW_OPTIONS}
            defaultEdgeOptions={{
              type: 'lineageEdge',
              markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
            }}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} size={1} />
            <Controls />
            <MiniMap
              nodeColor={(node) => {
                const nt = (node.data?.nodeType as LineageNodeType) || 'table';
                const colors: Record<LineageNodeType, string> = {
                  table: '#3b82f6',
                  column: '#22c55e',
                  etl_job: '#f97316',
                  report: '#a855f7',
                  metric: '#ef4444',
                };
                return colors[nt] || '#3b82f6';
              }}
              maskColor="hsl(var(--background) / 0.7)"
              className="!bg-card !border-border"
            />
          </ReactFlow>
        </div>

        {/* Detail Panel */}
        {selectedNode && (
          <NodeDetailPanel
            node={selectedNode}
            upstreamNodes={upstreamNodes}
            downstreamNodes={downstreamNodes}
            columnMappings={columnMappings}
            onClose={() => setSelectedNode(null)}
            onImpactAnalysis={handleImpactAnalysis}
          />
        )}
      </div>
    </div>
  );
}

// ── Exported wrapper with provider ─────────────────────────────────────

export default function LineageGraph() {
  return (
    <ReactFlowProvider>
      <LineageGraphInner />
    </ReactFlowProvider>
  );
}
