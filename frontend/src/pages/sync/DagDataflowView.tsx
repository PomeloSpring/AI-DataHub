import { useEffect, useMemo, useState } from 'react';
import {
  ReactFlow, Background, Controls,
  BaseEdge, EdgeLabelRenderer, getBezierPath,
  Handle, Position, MarkerType,
  type Node, type Edge, type NodeProps, type EdgeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { RefreshCw, X, FileSearch } from 'lucide-react';
import { dagApi, type DagGraph, type DataflowGraph, type DataflowNode } from '@/api/dag';

// ── 数据流视图：源表 → 转换 → 目标表（定义期静态解析 + 实际血缘验证） ──
// 画风遵循 graph-visualization.md：BaseEdge + 柔和贝塞尔 + fill:none、
// slate 细线、label 悬浮弧线中点；拓扑分层左→右（源左、目标右）。
// verified 边（实际血缘验证过）绿色；未验证保持灰（预期 vs 实际对照）。

const KIND_STYLE: Record<string, { border: string; head: string; icon: string; sub: string }> = {
  table: { border: 'border-blue-300', head: 'bg-blue-500/10', icon: 'bg-blue-500', sub: 'bg-blue-500/5' },
  transform: { border: 'border-amber-300', head: 'bg-amber-500/10', icon: 'bg-amber-500', sub: 'bg-amber-500/5' },
};

function DataflowNodeCard({ data, selected }: NodeProps) {
  const kind = (data as any).kind === 'transform' ? 'transform' : 'table';
  const st = KIND_STYLE[kind];
  return (
    <div className={`bg-card border rounded-lg shadow-sm min-w-[190px] max-w-[260px] cursor-pointer ${st.border} ${selected ? 'ring-2 ring-primary ring-offset-2' : ''}`}>
      <Handle type="target" position={Position.Left} className="!w-2 !h-2" />
      <div className={`flex items-center gap-2 px-3 py-2 border-b ${st.head}`}>
        <div className={`w-2.5 h-2.5 rounded-sm ${st.icon}`} />
        <span className="font-semibold text-xs truncate">{String((data as any).label || '')}</span>
        {kind === 'transform' && <Badge variant="outline" className="ml-auto text-[9px] px-1 py-0">转换</Badge>}
      </div>
      <div className={`px-3 py-1.5 text-[10px] text-muted-foreground ${st.sub}`}>
        {kind === 'table'
          ? ((data as any).roles || []).join(' / ') || '表'
          : (data as any).udfs?.length
            ? `UDF: ${(data as any).udfs.join(', ')}`
            : 'SQL 转换'}
      </div>
      <Handle type="source" position={Position.Right} className="!w-2 !h-2" />
    </div>
  );
}

function DataflowEdgeView({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, style, selected }: EdgeProps) {
  // graph-visualization.md：fill:none 必须显式（防黑色楔形）
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, curvature: 0.18,
  });
  const emphasized = Boolean(selected);
  const verified = Boolean(data?.verified);
  const baseColor = verified ? '#22c55e' : '#94a3b8';
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
          stroke: emphasized ? 'hsl(var(--primary))' : baseColor,
          strokeWidth: emphasized ? 1.8 : 1.2,
          strokeLinecap: 'round',
          opacity: emphasized ? 1 : 0.65,
        }}
      />
      {typeof data?.label === 'string' && (
        <EdgeLabelRenderer>
          <div
            className="absolute bg-card/95 border border-border/70 rounded-md px-2 py-0.5 shadow-sm pointer-events-none nodrag nopan text-[10px] whitespace-nowrap"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, zIndex: emphasized ? 20 : 1 }}
          >
            <span className={verified ? 'text-green-600' : 'text-muted-foreground'}>
              {String(data.label)}{verified ? ' ✓' : ''}
            </span>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { flowNode: DataflowNodeCard };
const edgeTypes = { flowEdge: DataflowEdgeView };

function layout(flow: DataflowGraph): { nodes: Node[]; edges: Edge[] } {
  // 拓扑分层：按流向深度分列（源在左、目标在右），同层纵向排布
  const depth: Record<string, number> = {};
  const upstream: Record<string, string[]> = {};
  for (const n of flow.nodes) { depth[n.id] = 0; upstream[n.id] = []; }
  for (const f of flow.flows) {
    if (upstream[f.to] && depth[f.from] !== undefined) upstream[f.to].push(f.from);
  }
  for (let i = 0; i < flow.nodes.length; i++) {
    for (const n of flow.nodes) {
      const deps = upstream[n.id] || [];
      depth[n.id] = deps.length ? Math.max(...deps.map(d => (depth[d] ?? 0) + 1)) : 0;
    }
  }
  const byDepth: Record<number, DataflowNode[]> = {};
  for (const n of flow.nodes) {
    const d = depth[n.id] ?? 0;
    (byDepth[d] = byDepth[d] || []).push(n);
  }
  const nodes: Node[] = [];
  Object.entries(byDepth).forEach(([d, list]) => {
    list.forEach((n, i) => {
      nodes.push({
        id: n.id,
        type: 'flowNode',
        position: { x: Number(d) * 320 + 40, y: i * 130 + 40 },
        data: { label: n.label, kind: n.kind, roles: n.roles, udfs: n.udfs, sql_digest: n.sql_digest },
      });
    });
  });
  const edges: Edge[] = flow.flows.map((f, i) => ({
    id: `flow-${i}`,
    source: f.from,
    target: f.to,
    type: 'flowEdge',
    data: { label: f.label, verified: f.verified },
    markerEnd: {
      type: MarkerType.ArrowClosed, width: 12, height: 12,
      color: f.verified ? '#22c55e' : '#94a3b8',
    },
  }));
  return { nodes, edges };
}

export default function DagDataflowView({
  workflowId, graph,
}: {
  /** 已保存工作流：按 ID 拉数据流（含血缘验证）；编辑中：直接传 graph_json 解析 */
  workflowId?: number;
  graph?: DagGraph;
}) {
  const [flow, setFlow] = useState<DataflowGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<DataflowNode | null>(null);
  const [plan, setPlan] = useState<string[] | null>(null);
  const [planLoading, setPlanLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      // 编辑中优先用画布实时解析（graph）；已保存/运行页按 workflowId 拉服务端解析+血缘验证
      const useLocal = graph && graph.nodes.length > 0;
      const { data } = useLocal
        ? await dagApi.dataflow(graph!)
        : await dagApi.workflowDataflow(workflowId!, true);
      setFlow(data as DataflowGraph);
    } catch (e: any) {
      setError(e?.response?.data?.detail || '数据流解析失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if ((graph && graph.nodes.length) || workflowId) load();
    else setFlow(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, JSON.stringify(graph || {})]);

  const rf = useMemo(() => (flow ? layout(flow) : { nodes: [], edges: [] }), [flow]);
  const hasVerified = flow?.flows.some(f => f.verified);

  const runExplain = async (node: DataflowNode) => {
    setPlanLoading(true);
    setPlan(null);
    try {
      const { data } = await dagApi.explain(node.sql_digest || '', node.datasource || '');
      setPlan(data.plan || []);
    } catch (e: any) {
      setPlan([`执行计划获取失败：${e?.response?.data?.detail || e?.message || '未知错误'}`]);
    } finally {
      setPlanLoading(false);
    }
  };

  return (
    <div className="h-full min-h-[380px] relative">
      <ReactFlow
        nodes={rf.nodes}
        edges={rf.edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        nodesDraggable
        onNodeClick={(_, n) => {
          const src = flow?.nodes.find(x => x.id === n.id) || null;
          setSelected(src);
          setPlan(null);
        }}
      >
        <Background gap={20} size={1} />
        <Controls />
      </ReactFlow>

      <div className="absolute top-2 right-2 flex items-center gap-2">
        {hasVerified && (
          <span className="text-[10px] text-green-600 bg-card/90 border rounded-md px-2 py-1">
            ✓ 绿线 = 实际血缘已验证
          </span>
        )}
        <button
          className="text-xs text-muted-foreground flex items-center gap-1 bg-card/90 border rounded-md px-2 py-1"
          onClick={load}
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />刷新
        </button>
      </div>

      {/* 节点详情（转换节点可看执行计划） */}
      {selected && (
        <div className="absolute top-2 left-2 w-[320px] bg-card border rounded-lg shadow-md p-3 space-y-2">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-[9px]">{selected.kind === 'transform' ? '转换' : '表'}</Badge>
            <span className="font-medium text-xs truncate">{selected.label}</span>
            <button className="ml-auto" onClick={() => setSelected(null)}><X className="w-3.5 h-3.5" /></button>
          </div>
          {selected.kind === 'transform' ? (
            <div className="space-y-1.5">
              {selected.udfs && selected.udfs.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {selected.udfs.map(u => <Badge key={u} variant="secondary" className="text-[9px]">UDF {u}</Badge>)}
                </div>
              )}
              {selected.sql_digest && (
                <pre className="text-[10px] text-muted-foreground bg-muted rounded p-2 whitespace-pre-wrap break-all max-h-[120px] overflow-y-auto">
                  {selected.sql_digest}
                </pre>
              )}
              <Button size="sm" variant="outline" className="w-full text-xs h-7"
                onClick={() => runExplain(selected)} disabled={planLoading}>
                <FileSearch className="w-3 h-3 mr-1" />
                {planLoading ? '获取中...' : '查看执行计划'}
              </Button>
              {plan && (
                <pre className="text-[10px] text-muted-foreground bg-muted rounded p-2 whitespace-pre-wrap max-h-[180px] overflow-y-auto">
                  {plan.join('\n')}
                </pre>
              )}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">
              角色：{(selected.roles || []).join(' / ') || '表'}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="absolute bottom-2 left-2 text-xs text-red-500 bg-card/90 border rounded-md px-2 py-1">{error}</div>
      )}
    </div>
  );
}
