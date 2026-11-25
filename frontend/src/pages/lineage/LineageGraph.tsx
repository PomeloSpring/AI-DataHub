import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Node,
  Edge,
  NodeProps,
  EdgeProps,
  MarkerType,
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
  Search, RefreshCw, Maximize2, X, GitBranch, Crosshair,
  ArrowUpRight, ArrowDownRight, Activity, Loader2,
} from 'lucide-react';
import { lineageApi } from '@/api/lineage';
import { toast } from '@/lib/toast';
import {
  CircleNode,
  NODE_CONFIG as GRAPH_NODE_CONFIG,
  compactNodeSize,
  hubSizeOf,
} from '@/components/graph/KnowledgeGraph';
import { applyDagreLayout } from '@/components/graph/utils/layout';
import {
  DENSE_THRESHOLD,
  EDGE_LABEL_LIMIT,
  EDGE_LABELS,
  FOCUS_HOPS,
  FOCUS_MAX_NODES,
  buildLegend,
  computeSearchHits,
  computeVisibleIds,
  displayLabel,
  expandNeighbors,
  isHubType,
  pickFocusSeed,
  typeMeta,
  type LineageEdgeData,
  type LineageNodeData,
} from './lineageView';

// ── 口径 ───────────────────────────────────────────────────────────────
// 节点标识统一为「数据源业务名.表[.列]」；资产层另有产品/本体对象/指标/维度/
// 数据集/图表。图面走 KnowledgeGraph 基准（圆形分类着色 + 顶部图例 + LR 分层），
// 明细一律下钻右侧栏（图可视化规范 §2/§3）。

interface LineageGraphResponse {
  nodes: LineageNodeData[];
  edges: LineageEdgeData[];
  datasources?: string[];
}

const FIT_VIEW_OPTIONS: FitViewOptions = { padding: 0.2 };

// ── 节点：直接复用图谱基准圆节点 ───────────────────────────────────────

function LineageNodeComponent({ data, selected }: NodeProps) {
  return <CircleNode data={data} selected={selected} />;
}

// ── 边：与 KnowledgeGraph 同款贝塞尔细灰线 ─────────────────────────────

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
  const edgeColor = emphasized ? 'hsl(var(--primary))' : '#94a3b8';

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
      {Boolean(data?.showLabel) && typeof data?.edgeType === 'string' && (
        <EdgeLabelRenderer>
          <div
            className="absolute bg-card/95 border border-border/70 rounded-md px-1.5 py-0.5 shadow-sm pointer-events-none nodrag nopan text-[10px] text-muted-foreground whitespace-nowrap"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              zIndex: 1,
            }}
          >
            {String(data.edgeType)}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

// ── 右侧详情：图面克制，明细下钻 ───────────────────────────────────────

function NodeDetailPanel({
  node,
  upstreamNodes,
  downstreamNodes,
  columnMappings,
  onClose,
  onSelectNode,
  onImpactAnalysis,
}: {
  node: LineageNodeData;
  upstreamNodes: LineageNodeData[];
  downstreamNodes: LineageNodeData[];
  columnMappings: Array<{ from: string; to: string }>;
  onClose: () => void;
  onSelectNode: (node: LineageNodeData) => void;
  onImpactAnalysis: (nodeId: number) => void;
}) {
  const meta = typeMeta(node.node_type);
  const cfg = GRAPH_NODE_CONFIG[meta.key];
  const qualified = node.node_id && node.node_id !== node.name ? node.node_id : '';

  const renderRelation = (list: LineageNodeData[], emptyText: string) => (
    <div className="space-y-1">
      {list.length > 0 ? list.map((n) => {
        const m = typeMeta(n.node_type);
        const c = GRAPH_NODE_CONFIG[m.key];
        return (
          <button
            key={n.id}
            type="button"
            onClick={() => onSelectNode(n)}
            className="w-full flex items-center gap-2 p-2 rounded border bg-muted/30 text-xs text-left hover:bg-muted/60"
          >
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: c?.color || '#94a3b8' }} />
            <span className="font-medium truncate">{displayLabel(n) || n.name}</span>
            <Badge variant="outline" className="ml-auto text-[9px] px-1 py-0 shrink-0">{m.label}</Badge>
          </button>
        );
      }) : <p className="text-xs text-muted-foreground italic">{emptyText}</p>}
    </div>
  );

  return (
    <div className="w-[330px] border-l bg-card flex flex-col h-full shrink-0">
      <div className="flex items-center justify-between p-4 border-b">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: cfg?.color || '#64748b' }} />
          节点详情
        </h3>
        <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{meta.label}</Badge>
              {node.datasource_name && (
                <Badge variant="outline" className="text-[10px]">{node.datasource_name}</Badge>
              )}
            </div>
            <h4 className="font-medium text-base break-all">{displayLabel(node) || node.name}</h4>
            {qualified && (
              <p className="text-[11px] text-muted-foreground font-mono break-all" title="限定名">
                {qualified}
              </p>
            )}
          </div>

          {node.metadata && Object.keys(node.metadata).length > 0 && (
            <>
              <Separator />
              <div className="space-y-2">
                <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">属性</h5>
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
            </>
          )}

          <Separator />

          <div className="space-y-2">
            <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
              <ArrowUpRight className="h-3 w-3" />
              上游来源（{upstreamNodes.length}）
            </h5>
            {renderRelation(upstreamNodes, '无上游来源')}
          </div>

          <Separator />

          <div className="space-y-2">
            <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
              <ArrowDownRight className="h-3 w-3" />
              下游去向（{downstreamNodes.length}）
            </h5>
            {renderRelation(downstreamNodes, '无下游去向')}
          </div>

          {node.node_type === 'table' && (
            <>
              <Separator />
              <div className="space-y-2">
                <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  字段映射（{columnMappings.length}）
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
                  <p className="text-xs text-muted-foreground italic">暂无字段级映射（该表未从 SQL 解析到字段血缘）</p>
                )}
              </div>
            </>
          )}

          <Separator />

          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => onImpactAnalysis(node.id)}
          >
            <Activity className="h-3.5 w-3.5 mr-2" />
            影响分析
          </Button>
        </div>
      </ScrollArea>
    </div>
  );
}

// ── 主组件 ─────────────────────────────────────────────────────────────

const nodeTypes = { lineageNode: LineageNodeComponent };
const edgeTypes = { lineageEdge: LineageEdge };

function LineageGraphInner() {
  const reactFlow = useReactFlow();

  const [rawNodes, setRawNodes] = useState<LineageNodeData[]>([]);
  const [rawEdges, setRawEdges] = useState<LineageEdgeData[]>([]);
  const [datasources, setDatasources] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const [searchQuery, setSearchQuery] = useState('');
  const [datasource, setDatasource] = useState<string>('all');
  // 类型显隐走图例点选（与知识图谱一致），不再用单一下拉（一次只能看一个类型）
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  // 字段级默认不展示（列节点会把图打碎）；字段映射在选中表节点的详情面板下钻
  const [showColumns, setShowColumns] = useState(false);
  const [selectedNode, setSelectedNode] = useState<LineageNodeData | null>(null);
  // 聚焦根：血缘一档动辄上百个节点，全图 fitView 只能压到亚像素点阵（不可读）。
  // 首屏自动聚焦到「连接最多的表」的上下游子图，点任意节点改为聚焦该节点。
  const [focusId, setFocusId] = useState<number | null>(null);
  const [autoFocusPending, setAutoFocusPending] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const [lodFar, setLodFar] = useState(false);
  // 重排：不重新取数，只重跑布局（图可视化规范 §4 的常驻工具之一）
  const [layoutEpoch, setLayoutEpoch] = useState(0);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // ── 取数：数据源筛选下推到服务端（含下游正向可达资产） ─────────────

  const fetchGraph = useCallback(async () => {
    setLoading(true);
    try {
      // 血缘为全局共享（工作空间概念已退役）：固定 0，后端按 IN (ws, 0) 兼容全局行
      const { data } = await lineageApi.getGraph(0, {
        datasource: datasource === 'all' ? '' : datasource,
      });
      const graph = data as LineageGraphResponse;
      setRawNodes(graph.nodes || []);
      setRawEdges(graph.edges || []);
      // datasources 由后端按全量节点返回：筛选后仍能切回其他源
      if (graph.datasources?.length) setDatasources(graph.datasources);
    } catch (err) {
      console.error('Failed to fetch lineage graph:', err);
      toast.error('血缘数据加载失败');
      setRawNodes([]);
      setRawEdges([]);
    } finally {
      setLoading(false);
    }
  }, [datasource]);

  useEffect(() => {
    setSelectedNode(null);
    fetchGraph();
  }, [fetchGraph]);

  // 数据换了（换源/刷新）：旧聚焦根可能已不在新图里，先作废再重选。
  // 不能放在 fetchGraph 同一个 effect 里：那个 effect 先于新数据到达执行，会拿旧数据选种子
  //（历史缺陷：提示条显示「已聚焦「」、图退回全源）。
  useEffect(() => {
    setFocusId(null);
    setAutoFocusPending(true);
  }, [rawNodes]);

  const nodeMap = useMemo(() => {
    const map = new Map<number, LineageNodeData>();
    for (const n of rawNodes) map.set(n.id, n);
    return map;
  }, [rawNodes]);

  // ── 可见节点：字段开关 + 类型显隐 + 搜索（命中节点带 1 跳邻居） ────

  const searchHits = useMemo(
    () => computeSearchHits(rawNodes, searchQuery),
    [rawNodes, searchQuery],
  );

  const visibleIds = useMemo(
    () => computeVisibleIds({ nodes: rawNodes, edges: rawEdges, hiddenTypes, showColumns, searchHits }),
    [rawNodes, rawEdges, hiddenTypes, showColumns, searchHits],
  );

  const baseVisibleIds = visibleIds;

  // 首屏 / 换源后自动聚焦一张有代表性的表（提示条可一键「查看全图」）
  useEffect(() => {
    if (!autoFocusPending || rawNodes.length === 0) return;
    setFocusId(pickFocusSeed(rawNodes, rawEdges));
    setAutoFocusPending(false);
  }, [autoFocusPending, rawNodes, rawEdges]);

  // 搜索以命中为准（聚焦让位）；否则只画聚焦根 ±FOCUS_HOPS 跳。
  // 扇出巨表 2 跳仍超过上限时退回 1 跳（宁少勿糊，不是静默截断：提示条会显示聚焦到哪张表）
  const focusedVisibleIds = useMemo(() => {
    if (searchHits.size > 0 || focusId == null) return baseVisibleIds;
    let ids = expandNeighbors(baseVisibleIds, rawEdges, focusId, FOCUS_HOPS);
    if (ids.size > FOCUS_MAX_NODES) {
      ids = expandNeighbors(baseVisibleIds, rawEdges, focusId, 1);
    }
    return ids;
  }, [baseVisibleIds, rawEdges, focusId, searchHits]);

  const visibleNodes = useMemo(
    () => rawNodes.filter((n) => focusedVisibleIds.has(n.id)),
    [rawNodes, focusedVisibleIds],
  );

  const dense = visibleNodes.length > DENSE_THRESHOLD;

  // ── 布局 + ReactFlow 元素（LR 拓扑分层，与血缘方向语义一致） ────────

  useEffect(() => {
    const visibleEdges = rawEdges.filter(
      (e) => focusedVisibleIds.has(e.source_id) && focusedVisibleIds.has(e.target_id),
    );
    const showEdgeLabel = visibleEdges.length <= EDGE_LABEL_LIMIT;

    const rfNodes: Node[] = visibleNodes.map((n) => {
      const meta = typeMeta(n.node_type);
      const label = displayLabel(n) || n.name || '';
      // 主干（表/数据产品/本体对象）走 hub：圆点 + 圆外名称胶囊（按 1/zoom 反向缩放，缩远可读）；
      // 明细（指标/维度/数据集/图表）走小圆点 + 缩放分级出标签（与 KnowledgeGraph 同款 LOD）。
      const hub = isHubType(n.node_type);
      const size = hub ? hubSizeOf(label) : compactNodeSize(meta.key, label);
      return {
        id: String(n.id),
        type: 'lineageNode',
        width: size,
        // hub 名称胶囊在圆下方（约 20px），布局高度补上，避免同档节点压住名字
        height: hub ? size + 20 : size,
        position: { x: 0, y: 0 },
        data: {
          nodeType: meta.key,
          label,
          size,
          compact: true,
          role: hub ? 'hub' : 'leaf',
          focused: searchHits.has(n.id) || n.id === focusId,
        },
      };
    });

    const rfEdges: Edge[] = visibleEdges.map((e) => ({
      id: `edge-${e.id}`,
      source: String(e.source_id),
      target: String(e.target_id),
      type: 'lineageEdge',
      data: { edgeType: EDGE_LABELS[e.edge_type] || e.edge_type, showLabel: showEdgeLabel },
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: '#94a3b8' },
    }));

    const laid = applyDagreLayout(rfNodes, rfEdges, {
      direction: 'LR',
      nodeWidth: 40,
      nodeHeight: 40,
      ranksep: dense ? 110 : 150,
      nodesep: dense ? 26 : 40,
      edgesep: 12,
    });

    // 分层布局横向上只有几列（表→产品→对象→数据集→图表），纵向却几十行；
    // 直接 fitView 会把图挤成中间一条竖带、两侧大片空白。
    // 只把 x 坐标按横纵比拉开（不动 y、不改左→右的分层语义），让聚焦视图铺满画布。
    const xs = laid.nodes.map((n) => n.position.x);
    const ys = laid.nodes.map((n) => n.position.y);
    const width = Math.max(...xs) - Math.min(...xs) || 1;
    const height = Math.max(...ys) - Math.min(...ys) || 1;
    const ratio = width / height;
    // 只对聚焦视图（未进紧凑总览）拉伸：全图拉伸会把连线拉成横跨全屏的长弧，总览更难读。
    const stretch = !dense && ratio < 1.2
      ? Math.min(3, Math.max(1, 1.6 / Math.max(ratio, 0.05)))
      : 1;
    const positioned = stretch > 1.05
      ? laid.nodes.map((n) => ({ ...n, position: { x: n.position.x * stretch, y: n.position.y } }))
      : laid.nodes;

    setNodes(positioned);
    setEdges(laid.edges);
  }, [visibleNodes, rawEdges, focusedVisibleIds, dense, searchHits, focusId, layoutEpoch, setNodes, setEdges]);

  useEffect(() => {
    if (nodes.length > 0) {
      // 与基准组件（KnowledgeGraph）同口径：千级图整图 fitView 会把缩放压到 0.05
      //（亚像素点阵，标题不可读），初始视图停在可读下限 0.35；要看全貌点「适应画布」。
      const timer = setTimeout(
        () => reactFlow.fitView({ padding: 0.12, minZoom: 0.35, maxZoom: 1 }),
        100,
      );
      return () => clearTimeout(timer);
    }
  }, [nodes.length, reactFlow]);

  // ── 交互 ──────────────────────────────────────────────────────────

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const raw = nodeMap.get(Number(node.id));
      if (!raw) return;
      setSelectedNode(raw);
      // 点谁聚焦谁（±FOCUS_HOPS 跳）：全图缩到不可读时靠这一下把局部放大看清
      setFocusId(raw.id);
    },
    [nodeMap],
  );

  const handleRefresh = useCallback(() => {
    setSelectedNode(null);
    setFocusId(null);
    setAutoFocusPending(true);
    fetchGraph();
  }, [fetchGraph]);

  const handleFitView = useCallback(() => {
    reactFlow.fitView(FIT_VIEW_OPTIONS);
  }, [reactFlow]);

  // 缩放分级：缩远（<0.55）隐明细标签，避免整图糊成小字；
  // hub 名称按 1/zoom 反向缩放，全景下仍是 11px 屏幕字号（与 KnowledgeGraph 同源）。
  const handleViewportMove = useCallback((_: unknown, viewport: { zoom: number }) => {
    const far = viewport.zoom < 0.55;
    setLodFar((prev) => (prev === far ? prev : far));
    containerRef.current?.style.setProperty(
      '--kg-inv-zoom',
      String(Math.min(8, 1 / Math.max(0.05, viewport.zoom))),
    );
  }, []);

  const focusLabel = useMemo(() => {
    if (focusId == null) return '';
    const n = nodeMap.get(focusId);
    return n ? displayLabel(n) || n.name : '';
  }, [focusId, nodeMap]);

  const { upstreamNodes, downstreamNodes } = useMemo(() => {
    if (!selectedNode) return { upstreamNodes: [] as LineageNodeData[], downstreamNodes: [] as LineageNodeData[] };
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

  // 字段映射：列节点 id = 数据源.表.列，按选中表的限定名前缀取
  const columnMappings = useMemo(() => {
    if (!selectedNode || selectedNode.node_type !== 'table') return [];
    const qualified = selectedNode.node_id || '';
    if (!qualified) return [];
    const prefix = qualified + '.';
    return rawEdges
      .map((e) => ({ src: nodeMap.get(e.source_id), tgt: nodeMap.get(e.target_id) }))
      .filter(({ src }) => src?.node_type === 'column' && (src.node_id || '').startsWith(prefix))
      .map(({ src, tgt }) => ({
        from: (src!.node_id || src!.name).split('.').slice(-2).join('.'),
        to: tgt ? (tgt.node_id || tgt.name).split('.').slice(-2).join('.') : '?',
      }));
  }, [selectedNode, rawEdges, nodeMap]);

  const handleImpactAnalysis = useCallback(async (nodeId: number) => {
    try {
      const { data } = await lineageApi.getImpact(nodeId, 0);
      const count = (data as { affected_nodes?: unknown[] })?.affected_nodes?.length ?? 0;
      toast.success(`影响分析完成，共 ${count} 个下游节点受影响`);
    } catch {
      toast.error('影响分析失败');
    }
  }, []);

  // ── 图例（按图中实际出现的类型渲染色块 + 业务名 + 数量，点击显隐） ──

  const legendItems = useMemo(() => buildLegend(rawNodes).map((item) => {
    const meta = typeMeta(item.type);
    return { ...item, color: GRAPH_NODE_CONFIG[meta.key]?.color || '#64748b' };
  }), [rawNodes]);

  const toggleType = useCallback((type: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  const stats = useMemo(
    () => ({ total: rawNodes.length, edges: rawEdges.length, shown: visibleNodes.length }),
    [rawNodes.length, rawEdges.length, visibleNodes.length],
  );

  // ── 空态文案：区分「没数据」「该源无血缘」「筛选无命中」 ────────────

  const emptyHint = useMemo(() => {
    if (rawNodes.length === 0) {
      return datasource !== 'all'
        ? { title: '该数据源暂无可视血缘', desc: '该源的表还没有接入血缘，或尚未执行血缘重建' }
        : { title: '暂无血缘数据', desc: '请执行血缘重建（后台 /lineage/rebuild）后刷新' };
    }
    if (focusedVisibleIds.size === 0) {
      return { title: '当前筛选无匹配节点', desc: '调整搜索词、数据源或点击图例恢复类型显示' };
    }
    return null;
  }, [rawNodes.length, datasource, focusedVisibleIds.size]);

  return (
    <div
      ref={containerRef}
      className={`flex flex-col h-full w-full ${lodFar ? 'kg-lod-far' : ''}`}
    >
      {/* 工具栏 */}
      <div className="flex items-center gap-3 p-3 border-b bg-card shrink-0 flex-wrap">
        <div className="flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-primary" />
          <h1 className="font-semibold text-base">数据血缘</h1>
        </div>

        <Separator orientation="vertical" className="h-6" />

        {/* 数据源筛选：显示业务名，值也是业务名（不出现 id） */}
        <Select value={datasource} onValueChange={setDatasource}>
          <SelectTrigger className="w-[180px] h-8 text-xs">
            <SelectValue placeholder="全部数据源" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部数据源</SelectItem>
            {datasources.map((ds) => (
              <SelectItem key={ds} value={ds}>{ds}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="relative w-[220px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            placeholder="搜索表 / 指标 / 图表..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-8 pl-8 text-xs"
          />
        </div>

        <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer whitespace-nowrap">
          <Switch checked={showColumns} onCheckedChange={setShowColumns} />
          字段级血缘
        </label>

        <div className="flex-1" />

        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>{stats.total} 个节点</span>
          <span>{stats.edges} 条关系</span>
          {stats.shown !== stats.total && <span className="text-primary">显示 {stats.shown}</span>}
        </div>

        <Separator orientation="vertical" className="h-6" />

        <Button variant="outline" size="sm" className="h-8 text-xs" onClick={handleRefresh} disabled={loading}>
          {loading
            ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            : <RefreshCw className="h-3.5 w-3.5 mr-1.5" />}
          刷新
        </Button>
        <Button variant="outline" size="sm" className="h-8 text-xs" onClick={handleFitView}>
          <Maximize2 className="h-3.5 w-3.5 mr-1.5" />
          适应画布
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={() => setLayoutEpoch((n) => n + 1)}
        >
          <GitBranch className="h-3.5 w-3.5 mr-1.5" />
          重排
        </Button>
      </div>

      {/* 聚焦说明：默认不是全图（全图一档几百节点，fitView 后是亚像素点阵），
          这里是显式的“当前在看哪条链”，而不是静默截断数据。 */}
      {rawNodes.length > 0 && searchHits.size === 0 && (
        <div className="flex items-center gap-2 px-3 py-1 border-b bg-muted/40 text-xs text-muted-foreground shrink-0">
          {focusId != null ? (
            <>
              <Crosshair className="h-3.5 w-3.5 text-primary shrink-0" />
              <span className="truncate">
                已聚焦「<span className="font-medium text-foreground">{focusLabel}</span>」上下游 {FOCUS_HOPS} 跳
              </span>
              <span className="text-muted-foreground/60 hidden sm:inline">
                · 点击任意节点改聚焦 · 全图共 {stats.total} 个节点
              </span>
              <Button
                variant="link"
                size="sm"
                className="h-6 px-1 text-xs"
                onClick={() => setFocusId(null)}
              >
                查看全图
              </Button>
            </>
          ) : (
            <>
              <Crosshair className="h-3.5 w-3.5 shrink-0" />
              <span>全图总览（{stats.total} 个节点）· 图面不可读时请选择数据源、搜索或点击节点聚焦</span>
            </>
          )}
        </div>
      )}

      {/* 图 + 详情 */}
      <div className="flex flex-1 min-h-0">
        <div className="flex-1 relative">
          {/* 图例：点击切换类型显隐（与知识图谱同款） */}
          {legendItems.length > 0 && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 px-3 py-1.5 rounded-full border bg-card/95 shadow-sm max-w-[80%] flex-wrap justify-center">
              {legendItems.map((item) => {
                const hidden = hiddenTypes.has(item.type);
                return (
                  <button
                    key={item.type}
                    type="button"
                    onClick={() => toggleType(item.type)}
                    title={hidden ? '点击显示该类型' : '点击隐藏该类型'}
                    aria-pressed={!hidden}
                    className={`flex items-center gap-1.5 text-[11px] whitespace-nowrap rounded-full px-1.5 py-0.5 transition-colors hover:bg-muted/60 ${
                      hidden ? 'text-muted-foreground/50 line-through' : 'text-muted-foreground'
                    }`}
                  >
                    <span className="w-2.5 h-2.5 rounded-sm" style={{ background: item.color, opacity: hidden ? 0.3 : 1 }} />
                    {item.label}
                    <span className="text-muted-foreground/60">{item.count}</span>
                  </button>
                );
              })}
            </div>
          )}

          {emptyHint && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="text-center text-muted-foreground max-w-sm px-6">
                {loading
                  ? <Loader2 className="h-8 w-8 mx-auto mb-3 animate-spin" />
                  : <GitBranch className="h-12 w-12 mx-auto mb-3 opacity-30" />}
                <p className="text-sm font-medium">{emptyHint.title}</p>
                <p className="text-xs mt-1 leading-relaxed">{emptyHint.desc}</p>
              </div>
            </div>
          )}

          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={handleNodeClick}
            onPaneClick={() => setSelectedNode(null)}
            onMove={handleViewportMove}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={FIT_VIEW_OPTIONS}
            defaultEdgeOptions={{ type: 'lineageEdge' }}
            proOptions={{ hideAttribution: true }}
            minZoom={0.05}
          >
            <Background gap={20} size={1} />
            <Controls />
            <MiniMap
              nodeColor={(node) => GRAPH_NODE_CONFIG[(node.data as any)?.nodeType]?.color || '#a5b4fc'}
              maskColor="hsl(var(--background) / 0.7)"
              className="!bg-card !border-border rounded"
            />
          </ReactFlow>
        </div>

        {selectedNode && (
          <NodeDetailPanel
            node={selectedNode}
            upstreamNodes={upstreamNodes}
            downstreamNodes={downstreamNodes}
            columnMappings={columnMappings}
            onClose={() => setSelectedNode(null)}
            onSelectNode={setSelectedNode}
            onImpactAnalysis={handleImpactAnalysis}
          />
        )}
      </div>
    </div>
  );
}

export default function LineageGraph() {
  return (
    <ReactFlowProvider>
      <LineageGraphInner />
    </ReactFlowProvider>
  );
}