import { useMemo } from 'react';
import {
  Background, Edge, Handle, MarkerType, Node, NodeProps, Position, ReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Network } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useVisConfig } from '@/lib/visStyle';
import { applyDagreLayout } from '@/components/graph/utils/layout';

export interface GraphNodeInput {
  id: string;
  label?: string;
  /** 节点分组/类型,决定配色轮转 */
  type?: string;
}

export interface GraphEdgeInput {
  source: string;
  target: string;
  label?: string;
}

interface Props {
  title?: string;
  nodes: GraphNodeInput[];
  edges: GraphEdgeInput[];
  height?: number;
}

// 字模缺失时的内置默认(与 seed 迁移 vis_analysis_migration.sql 同值);
// direction/nodeWidth/nodeHeight/ranksep/nodesep 为可视化旋钮(字模 style_config 可调)
const DEFAULT_GRAPH = {
  colorScheme: ['hsl(var(--chart-1))', 'hsl(var(--chart-2))', 'hsl(var(--chart-3))'],
  nodeBg: 'hsl(var(--card))',
  nodeBorder: 'hsl(var(--border))',
  edgeColor: 'hsl(var(--muted-foreground))',
  labelColor: 'hsl(var(--foreground))',
  direction: 'LR',
  nodeWidth: 170,
  nodeHeight: 44,
  ranksep: 90,
  nodesep: 36,
};

/** 轻量节点:左侧类型色条 + 标签,边挂在左右两个 Handle 上。 */
interface SimpleData {
  id?: string;
  label?: string;
  color: string;
  bg: string;
  border: string;
  labelColor: string;
}

function SimpleNode(props: NodeProps) {
  const d = props.data as unknown as SimpleData;
  return (
    <div
      className="rounded-md border px-2.5 py-1.5 text-xs shadow-sm"
      style={{ background: d.bg, borderColor: d.border }}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !opacity-0" />
      <div className="flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: d.color }} />
        <span className="max-w-[160px] truncate font-medium" style={{ color: d.labelColor }} title={String(d.label ?? '')}>
          {String(d.label ?? d.id ?? '')}
        </span>
      </div>
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !opacity-0" />
    </div>
  );
}

const NODE_TYPES = { simple: SimpleNode };

/**
 * 关系图谱卡片:```graph 围栏 JSON 的画布渲染(ReactFlow + dagre 分层布局)。
 * 节点/边配色优先取字模库 cs_graph_relation(chart_style),缺失回落内置默认;
 * 按 type 轮转色板,悬停/缩放交互保留。
 */
export default function GraphCard({ title, nodes, edges, height = 360 }: Props) {
  const cfg = useVisConfig('cs_graph_relation', DEFAULT_GRAPH);

  const typeOrder = useMemo(() => [...new Set(nodes.map(n => n.type || ''))], [nodes]);
  const colorOf = (type?: string) =>
    cfg.colorScheme[Math.max(0, typeOrder.indexOf(type || '')) % cfg.colorScheme.length];

  const { layoutNodes, layoutEdges } = useMemo(() => {
    const rfNodes: Node[] = nodes.map(n => ({
      id: n.id,
      type: 'simple',
      position: { x: 0, y: 0 },
      data: {
        id: n.id,
        label: n.label || n.id,
        color: colorOf(n.type),
        bg: cfg.nodeBg,
        border: cfg.nodeBorder,
        labelColor: cfg.labelColor,
      },
    }));
    const rfEdges: Edge[] = edges
      .filter(e => nodes.some(n => n.id === e.source) && nodes.some(n => n.id === e.target))
      .map((e, i) => ({
        id: `e-${e.source}-${e.target}-${i}`,
        source: e.source,
        target: e.target,
        label: e.label || undefined,
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: cfg.edgeColor },
        style: { stroke: cfg.edgeColor, strokeWidth: 1.2 },
      }));
    if (!rfNodes.length) return { layoutNodes: rfNodes, layoutEdges: rfEdges };
    const laid = applyDagreLayout(rfNodes, rfEdges, {
      direction: cfg.direction === 'TB' ? 'TB' : 'LR',
      nodeWidth: cfg.nodeWidth, nodeHeight: cfg.nodeHeight,
      ranksep: cfg.ranksep, nodesep: cfg.nodesep,
    });
    return { layoutNodes: laid.nodes, layoutEdges: laid.edges };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, cfg, typeOrder]);

  return (
    <div className="my-3 overflow-hidden rounded-lg border bg-card">
      <div className="flex items-center gap-2 border-b bg-muted/50 px-3 py-2">
        <Network className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold">{title || '关系图谱'}</span>
        <Badge variant="outline" className="text-[10px] font-normal">
          {nodes.length} 节点 · {edges.length} 边
        </Badge>
      </div>
      {nodes.length === 0 ? (
        <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">暂无图数据</div>
      ) : (
        <div style={{ height }} className="bg-background/40">
          <ReactFlow
            nodes={layoutNodes}
            edges={layoutEdges}
            nodeTypes={NODE_TYPES}
            fitView
            minZoom={0.2}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={18} color={cfg.edgeColor} style={{ opacity: 0.15 }} />
          </ReactFlow>
        </div>
      )}
    </div>
  );
}
