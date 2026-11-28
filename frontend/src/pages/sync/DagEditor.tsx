import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { t , t as tr } from '@/i18n/runtime';
import {
  ReactFlow, Background, Controls, MiniMap,
  BaseEdge, EdgeLabelRenderer, getBezierPath, MarkerType,
  Handle, Position, applyEdgeChanges, applyNodeChanges,
  type Node, type Edge, type NodeProps, type EdgeProps, type EdgeChange, type NodeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import AutoFitView from '@/components/graph/AutoFitView';
import { applyDagreLayout } from '@/components/graph/utils/layout';
import { toast } from '@/lib/toast';
import {
  Save, Play, FileCode2, Wand2, Plus, Trash2, ArrowLeft, Download,
} from 'lucide-react';
import {
  dagApi, type DagGraph, type DagNode, type DagWorkflow,
  type RunParams, type WatermarkItem,
} from '@/api/dag';
import { udfApi, type Udf } from '@/api/udf';
import { DatasourceSelect } from './DatasourceSelect';
import { DatasetSelect, DatasetSqlPreview, useMaterializableDatasets } from './DatasetSelect';
import { DataflowSqlPreview } from './DataflowSqlPreview';
import DagDataflowView from './DagDataflowView';
import { useTranslation } from 'react-i18next';

// ── 自定义节点 ──────────────────────────────────────────────────
const TYPE_LABEL: Record<string, string> = {
  materialize: tr('k_979c3cfe21'), sync: tr('k_e88ab5ba61'),
  sql_task: tr('k_c250192752'), control: tr('k_34e47d7e3e'),
};

// 节点配色向数据流视图看齐（graph-visualization.md §2 分类着色）：
// 物化=靛蓝、同步=蓝、SQL=琥珀、控制=slate；标题行带色块，视觉语言与 DagDataflowView 同源。
const NODE_STYLE: Record<string, { border: string; head: string; icon: string; sub: string }> = {
  materialize: { border: 'border-indigo-300', head: 'bg-indigo-500/10', icon: 'bg-indigo-500', sub: 'bg-indigo-500/5' },
  sync: { border: 'border-blue-300', head: 'bg-blue-500/10', icon: 'bg-blue-500', sub: 'bg-blue-500/5' },
  sql_task: { border: 'border-amber-300', head: 'bg-amber-500/10', icon: 'bg-amber-500', sub: 'bg-amber-500/5' },
  control: { border: 'border-slate-300', head: 'bg-slate-500/10', icon: 'bg-slate-400', sub: 'bg-slate-500/5' },
};

function DagTaskNode({ data, selected }: NodeProps) {
  const nodeType = String((data as any).nodeType || '');
  const st = NODE_STYLE[nodeType] || NODE_STYLE.control;
  const statusColor = (data as any).statusColor as string | undefined;
  const hint = String((data as any).hint || '');
  return (
    <div className={`bg-card border rounded-lg shadow-sm w-[300px] ${st.border} ${selected ? 'ring-2 ring-primary ring-offset-2' : ''}`}>
      <Handle type="target" position={Position.Left} className="!w-2 !h-2" />
      <div className={`flex items-center gap-2 px-3 py-2 border-b rounded-t-lg ${st.head}`}>
        <div className={`w-2.5 h-2.5 rounded-sm ${st.icon}`} />
        <span className="font-semibold text-xs truncate">{String((data as any).label || '')}</span>
        {statusColor && <span className={`ml-auto w-2 h-2 rounded-full ${statusColor}`} />}
      </div>
      <div className={`px-3 py-1.5 text-[10px] text-muted-foreground space-y-1 rounded-b-lg ${st.sub}`}>
        <Badge variant="outline" className="text-[9px] px-1 py-0">{TYPE_LABEL[nodeType] || nodeType}</Badge>
        {/* 目标表必须可读（含数据源）：截断成 "d..." 等于没说清落到哪张表 */}
        {hint && <div className="whitespace-normal break-all leading-snug" title={hint}>{hint}</div>}
      </div>
      <Handle type="source" position={Position.Right} className="!w-2 !h-2" />
    </div>
  );
}

// 连线遵循 graph-visualization.md §1：BaseEdge + 柔和贝塞尔（curvature 0.18）、
// 显式 fill:none（漏写会出现黑色楔形块）、slate 细线、label 悬浮弧线中点。
// virtual 边＝「并行执行」主干（无依赖时才有），虚线、无箭头、不可选不可删，仅视觉分组，
// 不进入 graph_json（写库只走 rfEdges）。
function DagEdgeView({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, style, selected }: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, curvature: 0.18,
  });
  const virtual = Boolean(data?.virtual);
  const emphasized = Boolean(selected) && !virtual;
  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        interactionWidth={virtual ? 0 : 16}
        style={{
          ...style,
          fill: 'none',
          stroke: emphasized ? 'hsl(var(--primary))' : virtual ? '#cbd5e1' : '#94a3b8',
          strokeWidth: emphasized ? 1.8 : virtual ? 1 : 1.2,
          strokeLinecap: 'round',
          strokeDasharray: virtual ? '5 5' : undefined,
          opacity: emphasized ? 1 : virtual ? 0.7 : 0.65,
        }}
      />
      {typeof data?.label === 'string' && (
        <EdgeLabelRenderer>
          <div
            className="absolute bg-card/95 border border-border/70 rounded-md px-2 py-0.5 shadow-sm pointer-events-none nodrag nopan text-[10px] whitespace-nowrap"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, zIndex: emphasized ? 20 : 1 }}
          >
            <span className={virtual ? 'text-slate-400' : 'text-muted-foreground'}>{String(data.label)}</span>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { dagTask: DagTaskNode };
const edgeTypes = { dagEdge: DagEdgeView };

// ── graph_json ↔ ReactFlow 转换（依赖分层 / 并行分行） ───────────
// 节点是固定宽度 300px 的卡片：间距必须大于卡片本身，否则会「贴在一块」
// （旧口径 x 步长 300 = 卡片最大宽度 320，右边缘直接压到下一个节点上）。
const NODE_W = 300;
const NODE_H = 116;
const COL_GAP = 130;
const ROW_GAP = 90;

const DEP_EDGE_LABEL = tr('k_b8d206aab6');
const PARALLEL_LABEL = tr('k_90fa6a26c9');

function makeEdge(id: string, source: string, target: string, label?: string): Edge {
  return {
    id, source, target, type: 'dagEdge',
    data: label ? { label } : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: '#94a3b8' },
  };
}

function layoutGraph(graph: DagGraph): { nodes: Node[]; edges: Edge[] } {
  const rfNodes: Node[] = graph.nodes.map(n => ({
    id: n.key, type: 'dagTask', position: { x: 0, y: 0 },
    data: {
      label: n.name || n.key, nodeType: n.type, hint: nodeHint(n),
      config: n.config,
    },
  }));
  const depEdges = graph.edges.map((e, i) =>
    makeEdge(`e-${i}`, e.from, e.to, DEP_EDGE_LABEL));

  if (depEdges.length > 0) {
    // 有依赖：dagre 分层（左→右，上游在左），间距传给布局器而不是事后手算
    const laid = applyDagreLayout(rfNodes, depEdges, {
      direction: 'LR', nodeWidth: NODE_W, nodeHeight: NODE_H,
      ranksep: COL_GAP + 40, nodesep: ROW_GAP + 30,
    });
    return { nodes: laid.nodes, edges: depEdges };
  }

  // 无依赖（如日/月/年三档并行物化）：**按列堆叠**而不是横排。
  // 画布被左右面板夹在中间（可用宽 ~800px），三个 300px 卡横排必被 fitView 缩到
  // 0.49——字小到看不清（实测），那和“贴在一起”一样不能用。竖排一列则
  // 宽度只占一张卡，fitView 能保持 1.0，同列相邻节点由 buildGuideEdges 画
  // 虚线「并行执行」主干（仅视觉，不进 graph_json）。
  // 节点多时按 ~4 行一列折行，保持列内是一条并行链（不要行主序：那会把链剪断）。
  const n = graph.nodes.length;
  const cols = n <= 4 ? 1 : Math.max(2, Math.ceil(n / 4));
  const rows = Math.max(1, Math.ceil(n / cols));
  rfNodes.forEach((node, i) => {
    node.position = {
      x: Math.floor(i / rows) * (NODE_W + COL_GAP) + 40,
      y: (i % rows) * (NODE_H + ROW_GAP) + 40,
    };
  });
  return { nodes: rfNodes, edges: [] };
}

// 无依赖编排的可视主干：按**列**分组，列内相邻节点连虚线（并行语义，无箭头、不可点不可删）。
function buildGuideEdges(nodes: Node[]): Edge[] {
  if (nodes.length < 2) return [];
  const cols = new Map<number, Node[]>();
  nodes.forEach(n => {
    const key = Math.round(n.position.x / 40);
    const list = cols.get(key);
    if (list) list.push(n); else cols.set(key, [n]);
  });
  const guides: Edge[] = [];
  cols.forEach(list => {
    list.sort((a, b) => a.position.y - b.position.y);
    for (let i = 0; i < list.length - 1; i += 1) {
      const a = list[i];
      const b = list[i + 1];
      guides.push({
        id: `guide-${a.id}-${b.id}`,
        source: a.id, target: b.id, type: 'dagEdge',
        selectable: false, deletable: false, focusable: false,
        data: { virtual: true, label: i === 0 ? PARALLEL_LABEL : undefined },
      });
    }
  });
  return guides;
}

const GRAIN_LABEL: Record<string, string> = {
  day: tr('k_c6da7c8547'), month: tr('k_2b726f75b2'), year: tr('k_bb608c6e04'),
};

function materializeTarget(config: any): string {
  const table = [config.target_datasource, config.target_table].filter(Boolean).join('.');
  return table || '—';
}

function nodeHint(n: DagNode): string {
  if (n.type === 'materialize') {
    const grain = GRAIN_LABEL[String(n.config.partition?.grain || 'day')] || '';
    const source = n.config.dataset || '—';
    return `${source}${grain ? ` · ${grain}` : ''} → ${materializeTarget(n.config)}`;
  }
  if (n.type === 'sync') return `${n.config.source_table || ''} → ${n.config.target_table || ''}`;
  if (n.type === 'sql_task') return n.config.target?.table ? `→ ${n.config.target.table}` : tr('k_711363c424');
  return n.config.action === 'fail' ? tr('k_ff24cedd52') : tr('k_dcc4233255');
}

function toGraph(rfNodes: Node[], rfEdges: Edge[], existing: DagGraph): DagGraph {
  const nodeMap = Object.fromEntries(existing.nodes.map(n => [n.key, n]));
  return {
    nodes: rfNodes.map(rf => {
      const prev = nodeMap[rf.id];
      return {
        key: rf.id,
        name: String((rf.data as any).label || rf.id),
        type: (prev?.type || 'sql_task') as DagNode['type'],
        config: prev?.config || {},
      };
    }),
    edges: rfEdges.map(e => ({ from: e.source, to: e.target })),
  };
}

// ── 物化节点配置（数据集 → 已登记目标表；分区/预算/批量） ──────────
function MaterializeConfigPanel({ nodeKey, nodeName, config, set, watermark, watermarkReady }: {
  nodeKey: string; nodeName?: string;
  config: any; set: (patch: any) => void;
  watermark?: WatermarkItem; watermarkReady: boolean;
}) {
  const datasets = useMaterializableDatasets();
  const params = datasets.find(d => d.name === config.dataset)?.params || [];
  const partition = config.partition || {};
  const budget = config.budget || {};
  const setPartition = (patch: any) => set({ partition: { ...partition, ...patch } });
  const setBudget = (patch: any) => set({ budget: { ...budget, ...patch } });
  const toNum = (v: string) => (v.trim() && Number.isFinite(Number(v)) ? Number(v) : undefined);

  return (
    <>
      <div>
        <label className="text-xs font-medium text-muted-foreground">{t("k_fc2b4735c3")}</label>
        <div className="mt-1">
          <DatasetSelect value={config.dataset} datasets={datasets}
            onChange={v => set({ dataset: v, partition: { ...partition, date_arg: '' } })} />
        </div>
        {/* 就地看数据集 SQL + 跳详情（免切页核对）；正文来自详情接口，按选中项懒加载 */}
        <DatasetSqlPreview datasetName={config.dataset} datasets={datasets} />
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">{t("k_6daeb016b3")}</label>
        <div className="mt-1"><DatasourceSelect value={config.target_datasource} onChange={v => set({ target_datasource: v })} /></div>
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">{t("k_442be9c497")}</label>
        <Input className="mt-1" value={config.target_table || ''} onChange={e => set({ target_table: e.target.value })} />
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">{t("k_244d6de40f")}</label>
        <Select value={config.write_mode || 'partition_overwrite'}
          onValueChange={v => set({
            write_mode: v,
            partition: v === 'partition_overwrite' ? partition : undefined,
          })}>
          <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="partition_overwrite">{t("k_83dbc5deef")}</SelectItem>
            <SelectItem value="overwrite">{t("k_8cd94a9232")}</SelectItem>
            <SelectItem value="append">{t("k_b64615865c")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {config.write_mode === 'partition_overwrite' && (
        <div className="border rounded-lg p-2 space-y-2">
          <div className="text-xs font-medium">{tr('k_de6418afb3')}</div>
          <div>
            <label className="text-xs text-muted-foreground">{t('k_1c03da519c')}</label>
            <Select value={partition.grain || 'day'} onValueChange={v => setPartition({ grain: v })}>
              <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="day">{t('k_c6da7c8547')}</SelectItem>
                <SelectItem value="month">{t('k_2b726f75b2')}</SelectItem>
                <SelectItem value="year">{t('k_bb608c6e04')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">{tr('k_6ee8f215b6')}</label>
            <Select value={partition.date_arg || ''} onValueChange={v => setPartition({ date_arg: v })}>
              <SelectTrigger className="mt-1"><SelectValue placeholder={tr('k_f4bf86975d')} /></SelectTrigger>
              <SelectContent>
                {params.length === 0 && <SelectItem value="__none__" disabled>{tr('k_73fc81271d')}</SelectItem>}
                {params.map(p => (
                  <SelectItem key={p.key} value={p.key}>{p.label || p.key}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">{tr('k_a4d4228a94')}</label>
            <Input className="mt-1" value={partition.column || ''} onChange={e => setPartition({ column: e.target.value })} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">{tr('k_94ec865db6')}</label>
            <Input className="mt-1" placeholder="p{yyyyMMdd}" value={partition.target_partition || ''}
              onChange={e => setPartition({ target_partition: e.target.value })} />
            <p className="text-[10px] text-muted-foreground mt-1">{tr('k_aa9be99c63')}</p>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">{tr('k_56f5690c96')}</label>
            <Input className="mt-1" type="number" min={1} max={366} placeholder="1"
              value={partition.lookback_days ?? ''}
              onChange={e => setPartition({ lookback_days: toNum(e.target.value) })} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">{t('k_62479c48c9')}</label>
            {/* 未加载到水位线时不给“暂无记录”的假结论（加载失败与真没跑过要能区分） */}
            <div className="mt-1 text-xs">
              {watermarkReady
                ? (watermark?.position || t('k_b9db8476ee'))
                : '…'}
            </div>
            {watermark?.updated_at && (
              <p className="text-[10px] text-muted-foreground mt-0.5">{watermark.updated_at}</p>
            )}
          </div>
        </div>
      )}

      <div className="border rounded-lg p-2 space-y-2">
        <div className="text-xs font-medium">{tr('k_e2462800c6')}</div>
        <div>
          <label className="text-xs text-muted-foreground">{tr('k_b7ef6de9b3')}</label>
          <Input className="mt-1" type="number" min={1} value={budget.max_rows ?? ''}
            onChange={e => setBudget({ max_rows: toNum(e.target.value) })} />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{tr('k_10225ebef2')}</label>
          <Input className="mt-1" type="number" min={1} value={budget.max_seconds ?? ''}
            onChange={e => setBudget({ max_seconds: toNum(e.target.value) })} />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{tr('k_3d2f6e5259')}</label>
          <Input className="mt-1" type="number" min={1} max={20000} value={config.batch_size ?? ''}
            onChange={e => set({ batch_size: toNum(e.target.value) })} />
        </div>
        <p className="text-[10px] text-muted-foreground">{tr('k_56634acc0a')}</p>
      </div>

      {/* 数据流 SQL：与「导出 SQL」同一条编译链，就地看当前配置将执行的完整 SQL */}
      <DataflowSqlPreview nodeKey={nodeKey} nodeName={nodeName} config={config} />
    </>
  );
}

// ── 节点配置面板 ────────────────────────────────────────────────
function NodeConfigPanel({
  node, udfs, onChange, watermark, watermarkReady,
}: {
  node: DagNode; udfs: Udf[]; onChange: (config: any) => void;
  watermark?: WatermarkItem; watermarkReady: boolean;
}) {
  const config = node.config || {};
  const set = (patch: any) => onChange({ ...config, ...patch });

  return (
    <div className="space-y-3 text-sm">
      <div>
        <label className="text-xs font-medium text-muted-foreground">{t("k_3de90b4272")}</label>
        <Input className="mt-1" value={node.name || ''} onChange={e => onChange({ ...config, __name: e.target.value })} />
      </div>

      {node.type === 'materialize' && (
        <MaterializeConfigPanel nodeKey={node.key} nodeName={node.name}
          config={config} set={set}
          watermark={watermark} watermarkReady={watermarkReady} />
      )}

      {node.type === 'sync' && (
        <>
          <div>
            <label className="text-xs font-medium text-muted-foreground">{tr('k_6abfc28824')}</label>
            <div className="mt-1"><DatasourceSelect value={config.source_datasource} onChange={v => set({ source_datasource: v })} /></div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">{tr('k_9bd9553623')}</label>
            <Input className="mt-1" value={config.source_table || ''} onChange={e => set({ source_table: e.target.value })} />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">{tr('k_ca1e476393')}</label>
            <div className="mt-1"><DatasourceSelect value={config.target_datasource} onChange={v => set({ target_datasource: v })} /></div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">{tr('k_c7c8c8f4b0')}</label>
            <Input className="mt-1" value={config.target_table || ''} onChange={e => set({ target_table: e.target.value })} />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">{t("k_c15748bd6f")}</label>
            <Select value={config.sync_mode || 'full'} onValueChange={v => set({ sync_mode: v })}>
              <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="full">{t("k_07a5db8b5e")}</SelectItem>
                <SelectItem value="incremental">{t("k_6078bac61a")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {config.sync_mode === 'incremental' && (
            <div>
              <label className="text-xs font-medium text-muted-foreground">{tr('k_9c885540a5')}</label>
              <Input className="mt-1" value={config.incremental_column || ''} onChange={e => set({ incremental_column: e.target.value })} />
            </div>
          )}
          <div>
            <label className="text-xs font-medium text-muted-foreground">{t("k_244d6de40f")}</label>
            <Select value={config.write_mode || 'append'} onValueChange={v => set({ write_mode: v })}>
              <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="append">{t("k_aec344de80")}</SelectItem>
                <SelectItem value="overwrite">{t("k_80598cde3b")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </>
      )}

      {node.type === 'sql_task' && (
        <>
          <div>
            <label className="text-xs font-medium text-muted-foreground">{tr('k_766b3c92d7')}</label>
            <div className="mt-1"><DatasourceSelect value={config.source_datasource} onChange={v => set({ source_datasource: v })} /></div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              {tr('k_f78f6984db')}</label>
            <Textarea className="mt-1 font-mono min-h-[140px]" value={config.sql || ''}
              onChange={e => set({ sql: e.target.value })}
              placeholder={tr('k_11bfb5be5f')} />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">{t("k_7714352a81")}</label>
            <div className="mt-1 flex flex-wrap gap-1">
              {udfs.length === 0 && <span className="text-xs text-muted-foreground">{t("k_50d596f2ee")}</span>}
              {udfs.map(u => {
                const ref = `${u.name}:${u.version}`;
                const active = (config.udf_refs || []).includes(ref) || (config.udf_refs || []).includes(u.name);
                return (
                  <Badge key={ref} variant={active ? 'default' : 'outline'}
                    className="cursor-pointer text-[10px]"
                    onClick={() => {
                      const refs = new Set(config.udf_refs || []);
                      if (active) { refs.delete(ref); refs.delete(u.name); }
                      else refs.add(ref);
                      set({ udf_refs: [...refs] });
                    }}>
                    {u.name}
                  </Badge>
                );
              })}
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">{t("k_eff2016531")}</label>
            <div className="mt-1"><DatasourceSelect allowEmpty value={config.target?.datasource}
              onChange={v => set({ target: v ? { ...(config.target || { table: '' }), datasource: v } : undefined })} /></div>
            {config.target?.datasource && (
              <>
                <Input className="mt-1" placeholder={t("k_451035fee3")} value={config.target?.table || ''}
                  onChange={e => set({ target: { ...config.target!, table: e.target.value } })} />
                <Select value={config.target?.write_mode || 'append'}
                  onValueChange={v => set({ target: { ...config.target!, write_mode: v } })}>
                  <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="append">{t("k_aec344de80")}</SelectItem>
                    <SelectItem value="overwrite">{t("k_a26056298a")}</SelectItem>
                  </SelectContent>
                </Select>
              </>
            )}
          </div>
        </>
      )}

      {node.type === 'control' && (
        <div>
          <label className="text-xs font-medium text-muted-foreground">{t("k_d9d9827827")}</label>
          <Select value={config.action || 'pass'} onValueChange={v => set({ action: v })}>
            <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="pass">{t("k_dcc4233255")}</SelectItem>
              <SelectItem value="fail">{t("k_ff24cedd52")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}

// ── 主页面 ──────────────────────────────────────────────────────
export default function DagEditor() {
  const { t } = useTranslation('ui');
  const { workflowId } = useParams();
  const navigate = useNavigate();
  const isNew = !workflowId || workflowId === 'new';

  const [rfNodes, setRfNodes] = useState<Node[]>([]);
  const [rfEdges, setRfEdges] = useState<Edge[]>([]);
  const [graph, setGraph] = useState<DagGraph>({ nodes: [], edges: [] });
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [cron, setCron] = useState('');
  const [version, setVersion] = useState<number | undefined>();
  const [udfs, setUdfs] = useState<Udf[]>([]);
  const [saving, setSaving] = useState(false);

  const [sqlDlgOpen, setSqlDlgOpen] = useState(false);
  const [sqlScript, setSqlScript] = useState('');
  const [sqlDatasource, setSqlDatasource] = useState('');
  const [exportOpen, setExportOpen] = useState(false);
  const [exportSql, setExportSql] = useState('');
  // 导出 SQL 的窗口覆盖（留空 = 按节点配置的默认窗口）；与「运行」共用同一套窗口参数
  const [exportStart, setExportStart] = useState('');
  const [exportEnd, setExportEnd] = useState('');
  const [exportLoading, setExportLoading] = useState(false);
  // 节点「上次执行成功的位置」：null = 未加载到（不冒充“暂无记录”）
  const [watermarks, setWatermarks] = useState<Record<string, WatermarkItem> | null>(null);
  const [runDlgOpen, setRunDlgOpen] = useState(false);
  const [runStart, setRunStart] = useState('');
  const [runEnd, setRunEnd] = useState('');
  // 本次参与的粒度：默认全部；取消的档位本次跳过（不静默：结果里报 skipped + 原因）
  const [runGrains, setRunGrains] = useState<string[]>(['day', 'month', 'year']);
  const [runSubmitting, setRunSubmitting] = useState(false);

  useEffect(() => {
    udfApi.list().then(({ data }) => setUdfs(data.items || [])).catch(() => setUdfs([]));
    if (!isNew && workflowId) {
      dagApi.getWorkflow(Number(workflowId)).then(({ data }) => {
        const wf = data as DagWorkflow;
        setName(wf.name); setCron(wf.cron_expression || ''); setVersion(wf.version);
        const g = wf.graph_json || { nodes: [], edges: [] };
        setGraph(g);
        const lay = layoutGraph(g);
        setRfNodes(lay.nodes);
        setRfEdges(lay.edges);
      }).catch(() => toast.error(tr('k_8ba34adb23')));
      dagApi.listWatermarks(Number(workflowId))
        .then(({ data }) => setWatermarks(data.items || {}))
        .catch(() => setWatermarks(null));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  // 画布节点是异步加载的：fitView 只在挂载时算一次，加载完成后得重新适配，
  // 否则右侧面板外的节点看不见（5 站 × 3 档 = 15 个节点时尤其明显）。
  // 适配时机交给 AutoFitView（等节点量完尺寸再 fit，否则按 0 尺寸算出错误缩放）。

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setRfNodes(nds => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => setRfEdges(eds => applyEdgeChanges(changes, eds)), []);

  const selectedNode = useMemo(
    () => graph.nodes.find(n => n.key === selectedKey) || null, [graph, selectedKey]);

  // 无依赖编排的可视主干（虚线，仅视觉分组；真实依赖只看 graph.edges，不与 rfEdges 混用）
  const guideEdges = useMemo(
    () => (graph.edges.length === 0 ? buildGuideEdges(rfNodes) : []),
    [rfNodes, graph.edges.length]);

  const addNode = (type: DagNode['type']) => {
    const key = `${type}_${graph.nodes.length + 1}_${Date.now() % 1000}`;
    const node: DagNode = {
      key, name: `${TYPE_LABEL[type]} ${graph.nodes.length + 1}`, type,
      config: type === 'control' ? { action: 'pass' } : {},
    };
    const nextGraph = { ...graph, nodes: [...graph.nodes, node] };
    setGraph(nextGraph);
    const lay = layoutGraph(nextGraph);
    setRfNodes(lay.nodes);
    setRfEdges(lay.edges);
    setSelectedKey(key);
  };

  const updateSelectedConfig = (config: any) => {
    if (!selectedKey) return;
    const { __name, ...rest } = config;
    setGraph(g => ({
      ...g,
      nodes: g.nodes.map(n => n.key === selectedKey
        ? { ...n, name: __name !== undefined ? __name : n.name, config: rest } : n),
    }));
  };

  const removeSelected = () => {
    if (!selectedKey) return;
    const nextGraph = {
      nodes: graph.nodes.filter(n => n.key !== selectedKey),
      edges: graph.edges.filter(e => e.from !== selectedKey && e.to !== selectedKey),
    };
    setGraph(nextGraph);
    const lay = layoutGraph(nextGraph);
    setRfNodes(lay.nodes);
    setRfEdges(lay.edges);
    setSelectedKey(null);
  };

  // ReactFlow 画布连线 → graph edges（拖拽连线时同步）
  const syncEdges = (eds: Edge[]) => {
    setRfEdges(eds);
    setGraph(g => ({ ...g, edges: eds.map(e => ({ from: e.source, to: e.target })) }));
  };

  const handleSave = async () => {
    if (!name.trim()) { toast.error(tr('k_84b7d3c4cf')); return; }
    const finalGraph = toGraph(rfNodes, rfEdges, graph);
    setSaving(true);
    try {
      if (isNew) {
        const { data } = await dagApi.createWorkflow({
          name, cron_expression: cron || null, graph_json: finalGraph,
        });
        toast.success(tr('k_84265975db'));
        navigate(`/data/sync/dag/${data.id}`);
      } else {
        await dagApi.updateWorkflow(Number(workflowId), {
          name, cron_expression: cron || null, graph_json: finalGraph, version,
        });
        const { data } = await dagApi.getWorkflow(Number(workflowId));
        setVersion(data.version);
        toast.success(tr('k_f8dfedcd8a'));
      }
      setGraph(finalGraph);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || tr('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  const handleRun = () => {
    if (isNew) { toast.error(tr('k_f76664781d')); return; }
    setRunDlgOpen(true);
  };

  // 确认运行：先保存当前编排，再带窗口参数触发（历史初始化与日常调度共用同一份图）
  const submitRun = async () => {
    if (!workflowId) return;
    setRunSubmitting(true);
    try {
      await dagApi.updateWorkflow(Number(workflowId), {
        name, cron_expression: cron || null,
        graph_json: toGraph(rfNodes, rfEdges, graph), version,
      });
      const params: RunParams = {
        start_date: runStart || undefined,
        end_date: runEnd || undefined,
        // 三档全选 = 日常口径，不必落库；一旦收窄就必须显式传（空选会被后端拒）
        grains: runGrains.length < 3
          ? (runGrains as RunParams['grains'])
          : undefined,
      };
      const { data } = await dagApi.runWorkflow(Number(workflowId), params);
      toast.success(tr('k_e2917cf924'));
      setRunDlgOpen(false);
      navigate(`/data/sync/dag-runs/${data.run_id}`, { state: { from: `/data/sync/dag/${workflowId}` } });
    } catch (error: any) {
      // 后端会拒未知参数/错日期/无可承载参数的分区节点，原因原样展示
      toast.error(error?.response?.data?.detail || tr('k_02b526816d'));
    } finally {
      setRunSubmitting(false);
    }
  };

  const handleSqlToDag = async () => {
    try {
      const { data } = await dagApi.sqlToDag(sqlScript, sqlDatasource);
      const g = data.graph_json as DagGraph;
      setGraph(g);
      const lay = layoutGraph(g);
      setRfNodes(lay.nodes);
      setRfEdges(lay.edges);
      setSqlDlgOpen(false);
      toast.success(tr('k_a454942a37', { p0: data.node_count, p1: data.edge_count }));
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || tr('k_b4077309f9'));
    }
  };

  const handleExportSql = async (start = exportStart, end = exportEnd) => {
    try {
      const runParams: RunParams = {
        start_date: start || undefined,
        end_date: end || undefined,
      };
      const { data } = await dagApi.dagToSql(toGraph(rfNodes, rfEdges, graph), runParams);
      setExportSql(data.sql);
      setExportOpen(true);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || tr('k_dd51ab5026'));
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 p-4 border-b">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => navigate('/data/sync/dag')}>
            <ArrowLeft className="w-4 h-4 mr-1" />{tr('k_11d0241540')}</Button>
          <Input className="w-[220px]" placeholder={tr('k_9e71b468f0')} value={name} onChange={e => setName(e.target.value)} />
          <Input className="w-[160px]" placeholder={t("k_4a7239c74a")} value={cron} onChange={e => setCron(e.target.value)} />
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setSqlDlgOpen(true)}>
            <Wand2 className="w-4 h-4 mr-1" />{tr('k_751997c519')}</Button>
          <Button variant="outline" size="sm" onClick={() => handleExportSql()}>
            <Download className="w-4 h-4 mr-1" />{tr('k_379d7d22cc')}</Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            <Save className="w-4 h-4 mr-1" />{tr('k_fadf24dbc5')}</Button>
          <Button size="sm" onClick={handleRun} disabled={isNew}>
            <Play className="w-4 h-4 mr-1" />{tr('k_0c3acd446f')}</Button>
        </div>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left: node palette */}
        <div className="w-[180px] border-r p-3 space-y-2">
          <p className="text-xs font-medium text-muted-foreground">{t("k_e840cd6f1e")}</p>
          <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => addNode('materialize')}>
            <Plus className="w-3.5 h-3.5 mr-1" />{tr('k_979c3cfe21')}</Button>
          <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => addNode('sync')}>
            <Plus className="w-3.5 h-3.5 mr-1" />{tr('k_95753ea661')}</Button>
          <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => addNode('sql_task')}>
            <FileCode2 className="w-3.5 h-3.5 mr-1" />{tr('k_c250192752')}</Button>
          <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => addNode('control')}>
            <Plus className="w-3.5 h-3.5 mr-1" />{tr('k_ab40a96f7c')}</Button>
          <p className="text-xs text-muted-foreground pt-2">
            {tr('k_c76ebae125')}</p>
        </div>

        {/* Canvas: 编排图（控制流） / 数据流（源→转换→目标）双视图 */}
        <Tabs defaultValue="design" className="flex-1 flex flex-col min-h-0">
          <TabsList className="mx-3 mt-2 self-start">
            <TabsTrigger value="design">{t("k_6bc3ff93aa")}</TabsTrigger>
            <TabsTrigger value="dataflow">{t("k_b2047f645e")}</TabsTrigger>
          </TabsList>
          <TabsContent value="design" className="flex-1 min-h-0 mt-2 data-[state=active]:flex">
            <div className="flex-1 min-h-0 w-full relative">
              <ReactFlow
                nodes={rfNodes}
                edges={[...rfEdges, ...guideEdges]}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={(conn) => {
                  if (conn.source && conn.target) {
                    syncEdges([...rfEdges, makeEdge(`e-${Date.now()}`, conn.source, conn.target, DEP_EDGE_LABEL)]);
                  }
                }}
                onNodeClick={(_, node) => setSelectedKey(node.id)}
                onEdgeClick={(_, edge) => {
                  // 虚线主干是视觉分组，不是真依赖：点了不当删除
                  if (edge.data?.virtual) return;
                  syncEdges(rfEdges.filter(e => e.id !== edge.id));
                }}
                fitView
              >
                <AutoFitView key={`${workflowId}:${rfNodes.length}:${rfEdges.length}`} />
                <Background gap={20} size={1} />
                <Controls />
                <MiniMap />
              </ReactFlow>
              {guideEdges.length > 0 && (
                <div className="absolute bottom-2 left-2 text-[10px] text-muted-foreground bg-card/90 border rounded-md px-2 py-1 pointer-events-none">
                  {tr('k_618d4de44c')}
                </div>
              )}
            </div>
          </TabsContent>
          <TabsContent value="dataflow" className="flex-1 min-h-0 mt-2 data-[state=active]:flex">
            {/* 同 DagRunDetail：父层无确定高度时 ReactFlow 的 height:100% 会算成 0 高 */}
            <div className="flex-1 min-h-[320px] w-full">
              <DagDataflowView
                workflowId={isNew ? undefined : Number(workflowId)}
                graph={toGraph(rfNodes, rfEdges, graph)}
              />
            </div>
          </TabsContent>
        </Tabs>

        {/* Right: config panel */}
        <div className="w-[300px] border-l p-3 overflow-y-auto">
          {selectedNode ? (
            <>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-medium">{t("k_7eddbd9247")}</p>
                <Button variant="ghost" size="sm" onClick={removeSelected}>
                  <Trash2 className="w-3.5 h-3.5 text-red-500" />
                </Button>
              </div>
              <NodeConfigPanel node={selectedNode} udfs={udfs} onChange={updateSelectedConfig}
                watermark={watermarks ? watermarks[selectedNode.key] : undefined}
                watermarkReady={watermarks !== null} />
            </>
          ) : (
            <p className="text-sm text-muted-foreground">{t("k_a1b7e2b306")}</p>
          )}
        </div>
      </div>

      {/* SQL → DAG 对话框 */}
      <Dialog open={sqlDlgOpen} onOpenChange={setSqlDlgOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{t("k_4118acbda2")}</DialogTitle></DialogHeader>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              {tr('k_4371fe3fda')}</p>
            <DatasourceSelect value={sqlDatasource} onChange={setSqlDatasource} placeholder={t("k_4de3d6c8a3")} />
            <Textarea className="font-mono min-h-[220px]" value={sqlScript} onChange={e => setSqlScript(e.target.value)}
              placeholder={'CREATE TABLE agg AS SELECT ...;\nINSERT INTO doris_ds.dwh.agg SELECT ... FROM agg;'} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSqlDlgOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSqlToDag} disabled={!sqlScript.trim() || !sqlDatasource}>{t("k_1417f615a6")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 运行参数（历史初始化窗口 / 续跑位置） */}
      <Dialog open={runDlgOpen} onOpenChange={setRunDlgOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{t('k_9bbb81216e')}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">{t('k_55eb14c082')}</p>
            <div>
              <label className="text-xs font-medium text-muted-foreground">{t('k_23434f8e64')}</label>
              <Input className="mt-1" type="date" value={runStart} onChange={e => setRunStart(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">{t('k_1d468be946')}</label>
              <Input className="mt-1" type="date" value={runEnd} onChange={e => setRunEnd(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">{t('k_ffd8320e16')}</label>
              <div className="mt-1 flex flex-wrap gap-1">
                {(['day', 'month', 'year'] as const).map(g => {
                  const active = runGrains.includes(g);
                  return (
                    <Badge key={g} variant={active ? 'default' : 'outline'}
                      className="cursor-pointer text-[10px]"
                      onClick={() => setRunGrains(prev => active
                        ? prev.filter(x => x !== g) : [...prev, g])}>
                      {GRAIN_LABEL[g]}
                    </Badge>
                  );
                })}
              </div>
              <p className="text-[10px] text-muted-foreground mt-1">{t('k_1800ba3d6d')}</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRunDlgOpen(false)}>{t('k_4d0b4688c7')}</Button>
            <Button onClick={submitRun} disabled={runSubmitting}>
              <Play className="w-4 h-4 mr-1" />{tr('k_0c3acd446f')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 导出 SQL 对话框：窗口可覆盖（导出的是真实可执行的 INSERT + SELECT，不只是注释） */}
      <Dialog open={exportOpen} onOpenChange={setExportOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{t("k_6a2ce32a7a")}</DialogTitle></DialogHeader>
          <p className="text-[11px] text-muted-foreground">{tr('k_a256d4623d')}</p>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="text-xs font-medium text-muted-foreground">{t('k_23434f8e64')}</label>
              <Input className="mt-1" type="date" value={exportStart} onChange={e => setExportStart(e.target.value)} />
            </div>
            <div className="flex-1">
              <label className="text-xs font-medium text-muted-foreground">{t('k_1d468be946')}</label>
              <Input className="mt-1" type="date" value={exportEnd} onChange={e => setExportEnd(e.target.value)} />
            </div>
            <Button variant="outline" disabled={exportLoading}
              onClick={async () => {
                setExportLoading(true);
                try { await handleExportSql(); } finally { setExportLoading(false); }
              }}>
              {tr('k_a7c23201a2')}</Button>
          </div>
          <Textarea className="font-mono min-h-[260px]" readOnly value={exportSql} />
          <DialogFooter>
            <Button variant="outline" onClick={() => navigator.clipboard.writeText(exportSql).then(() => toast.success(tr('k_52e6abbe5c')))}>
              {tr('k_79d3abe929')}</Button>
            <Button onClick={() => setExportOpen(false)}>{t("k_6c14bd7f6f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
