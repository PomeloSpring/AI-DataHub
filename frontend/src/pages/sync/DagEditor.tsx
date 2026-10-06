import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ReactFlow, Background, Controls, MiniMap,
  Handle, Position, applyEdgeChanges, applyNodeChanges,
  type Node, type Edge, type NodeProps, type EdgeChange, type NodeChange,
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
import { toast } from 'sonner';
import {
  Save, Play, FileCode2, Wand2, Plus, Trash2, ArrowLeft, Download,
} from 'lucide-react';
import {
  dagApi, type DagGraph, type DagNode, type DagWorkflow,
} from '@/api/dag';
import { udfApi, type Udf } from '@/api/udf';
import { DatasourceSelect } from './DatasourceSelect';
import DagDataflowView from './DagDataflowView';

// ── 自定义节点 ──────────────────────────────────────────────────
const TYPE_LABEL: Record<string, string> = {
  sync: '同步', sql_task: 'SQL 任务', control: '控制',
};

function DagTaskNode({ data, selected }: NodeProps) {
  const statusColor = (data as any).statusColor as string | undefined;
  return (
    <div className={`bg-card border rounded-lg shadow-sm min-w-[170px] max-w-[240px] ${selected ? 'border-primary' : 'border-border'}`}>
      <Handle type="target" position={Position.Left} className="!w-2 !h-2" />
      <div className="flex items-center gap-2 px-3 py-2 border-b">
        <div className={`w-2.5 h-2.5 rounded-full ${statusColor || 'bg-slate-400'}`} />
        <span className="font-medium text-sm truncate">{String((data as any).label || '')}</span>
      </div>
      <div className="px-3 py-1.5 text-xs text-muted-foreground flex items-center gap-2">
        <Badge variant="outline" className="text-[10px]">{TYPE_LABEL[String((data as any).nodeType)] || String((data as any).nodeType)}</Badge>
        {String((data as any).hint || '') && <span className="truncate">{String((data as any).hint)}</span>}
      </div>
      <Handle type="source" position={Position.Right} className="!w-2 !h-2" />
    </div>
  );
}

const nodeTypes = { dagTask: DagTaskNode };

// ── graph_json ↔ ReactFlow 转换（简单分层布局） ─────────────────
function layoutGraph(graph: DagGraph): Node[] {
  const depth: Record<string, number> = {};
  const upstream: Record<string, string[]> = {};
  graph.nodes.forEach(n => { depth[n.key] = 0; upstream[n.key] = []; });
  graph.edges.forEach(e => {
    if (upstream[e.to] && depth[e.from] !== undefined) upstream[e.to].push(e.from);
  });
  // 拓扑松弛求深度
  for (let i = 0; i < graph.nodes.length; i++) {
    graph.nodes.forEach(n => {
      const deps = upstream[n.key] || [];
      depth[n.key] = deps.length ? Math.max(...deps.map(d => depth[d] + 1)) : 0;
    });
  }
  const byDepth: Record<number, string[]> = {};
  graph.nodes.forEach(n => {
    const d = depth[n.key] ?? 0;
    (byDepth[d] = byDepth[d] || []).push(n.key);
  });
  const nodeMap = Object.fromEntries(graph.nodes.map(n => [n.key, n]));
  const rfNodes: Node[] = [];
  Object.entries(byDepth).forEach(([d, keys]) => {
    keys.forEach((key, i) => {
      const n = nodeMap[key];
      rfNodes.push({
        id: key, type: 'dagTask', position: { x: Number(d) * 300 + 40, y: i * 140 + 40 },
        data: {
          label: n.name || key, nodeType: n.type, hint: nodeHint(n),
          config: n.config,
        },
      });
    });
  });
  return rfNodes;
}

function nodeHint(n: DagNode): string {
  if (n.type === 'sync') return `${n.config.source_table || ''} → ${n.config.target_table || ''}`;
  if (n.type === 'sql_task') return n.config.target?.table ? `→ ${n.config.target.table}` : '查询';
  return n.config.action === 'fail' ? '显式失败' : '通过';
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

// ── 节点配置面板 ────────────────────────────────────────────────
function NodeConfigPanel({
  node, udfs, onChange,
}: { node: DagNode; udfs: Udf[]; onChange: (config: any) => void }) {
  const config = node.config || {};
  const set = (patch: any) => onChange({ ...config, ...patch });

  return (
    <div className="space-y-3 text-sm">
      <div>
        <label className="text-xs font-medium text-muted-foreground">节点名称</label>
        <Input className="mt-1" value={node.name || ''} onChange={e => onChange({ ...config, __name: e.target.value })} />
      </div>

      {node.type === 'sync' && (
        <>
          <div>
            <label className="text-xs font-medium text-muted-foreground">源数据源 *</label>
            <div className="mt-1"><DatasourceSelect value={config.source_datasource} onChange={v => set({ source_datasource: v })} /></div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">源表 *</label>
            <Input className="mt-1" value={config.source_table || ''} onChange={e => set({ source_table: e.target.value })} />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">目标数据源 *</label>
            <div className="mt-1"><DatasourceSelect value={config.target_datasource} onChange={v => set({ target_datasource: v })} /></div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">目标表 *</label>
            <Input className="mt-1" value={config.target_table || ''} onChange={e => set({ target_table: e.target.value })} />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">同步模式</label>
            <Select value={config.sync_mode || 'full'} onValueChange={v => set({ sync_mode: v })}>
              <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="full">全量</SelectItem>
                <SelectItem value="incremental">增量</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {config.sync_mode === 'incremental' && (
            <div>
              <label className="text-xs font-medium text-muted-foreground">增量列 *</label>
              <Input className="mt-1" value={config.incremental_column || ''} onChange={e => set({ incremental_column: e.target.value })} />
            </div>
          )}
          <div>
            <label className="text-xs font-medium text-muted-foreground">写入模式</label>
            <Select value={config.write_mode || 'append'} onValueChange={v => set({ write_mode: v })}>
              <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="append">追加</SelectItem>
                <SelectItem value="overwrite">覆盖（全量刷新）</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </>
      )}

      {node.type === 'sql_task' && (
        <>
          <div>
            <label className="text-xs font-medium text-muted-foreground">主数据源 *</label>
            <div className="mt-1"><DatasourceSelect value={config.source_datasource} onChange={v => set({ source_datasource: v })} /></div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              SQL *（跨源用 ds.db.table 三段式限定名）
            </label>
            <Textarea className="mt-1 font-mono min-h-[140px]" value={config.sql || ''}
              onChange={e => set({ sql: e.target.value })}
              placeholder={'SELECT ... FROM 源库表 s\nJOIN 另一数据源.db.table t ON ...\nLIMIT 1000'} />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">UDF 引用</label>
            <div className="mt-1 flex flex-wrap gap-1">
              {udfs.length === 0 && <span className="text-xs text-muted-foreground">暂无 UDF</span>}
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
            <label className="text-xs font-medium text-muted-foreground">写入目标（可选）</label>
            <div className="mt-1"><DatasourceSelect allowEmpty value={config.target?.datasource}
              onChange={v => set({ target: v ? { ...(config.target || { table: '' }), datasource: v } : undefined })} /></div>
            {config.target?.datasource && (
              <>
                <Input className="mt-1" placeholder="目标表名" value={config.target?.table || ''}
                  onChange={e => set({ target: { ...config.target!, table: e.target.value } })} />
                <Select value={config.target?.write_mode || 'append'}
                  onValueChange={v => set({ target: { ...config.target!, write_mode: v } })}>
                  <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="append">追加</SelectItem>
                    <SelectItem value="overwrite">覆盖</SelectItem>
                  </SelectContent>
                </Select>
              </>
            )}
          </div>
        </>
      )}

      {node.type === 'control' && (
        <div>
          <label className="text-xs font-medium text-muted-foreground">动作</label>
          <Select value={config.action || 'pass'} onValueChange={v => set({ action: v })}>
            <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="pass">通过</SelectItem>
              <SelectItem value="fail">显式失败</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}

// ── 主页面 ──────────────────────────────────────────────────────
export default function DagEditor() {
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

  useEffect(() => {
    udfApi.list().then(({ data }) => setUdfs(data.items || [])).catch(() => setUdfs([]));
    if (!isNew && workflowId) {
      dagApi.getWorkflow(Number(workflowId)).then(({ data }) => {
        const wf = data as DagWorkflow;
        setName(wf.name); setCron(wf.cron_expression || ''); setVersion(wf.version);
        const g = wf.graph_json || { nodes: [], edges: [] };
        setGraph(g);
        setRfNodes(layoutGraph(g));
        setRfEdges(g.edges.map((e, i) => ({ id: `e-${i}`, source: e.from, target: e.to })));
      }).catch(() => toast.error('工作流加载失败'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setRfNodes(nds => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => setRfEdges(eds => applyEdgeChanges(changes, eds)), []);

  const selectedNode = useMemo(
    () => graph.nodes.find(n => n.key === selectedKey) || null, [graph, selectedKey]);

  const addNode = (type: DagNode['type']) => {
    const key = `${type}_${graph.nodes.length + 1}_${Date.now() % 1000}`;
    const node: DagNode = {
      key, name: `${TYPE_LABEL[type]} ${graph.nodes.length + 1}`, type,
      config: type === 'control' ? { action: 'pass' } : {},
    };
    const nextGraph = { ...graph, nodes: [...graph.nodes, node] };
    setGraph(nextGraph);
    setRfNodes(layoutGraph(nextGraph));
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
    setRfNodes(layoutGraph(nextGraph));
    setRfEdges(nextGraph.edges.map((e, i) => ({ id: `e-${i}`, source: e.from, target: e.to })));
    setSelectedKey(null);
  };

  // ReactFlow 画布连线 → graph edges（拖拽连线时同步）
  const syncEdges = (eds: Edge[]) => {
    setRfEdges(eds);
    setGraph(g => ({ ...g, edges: eds.map(e => ({ from: e.source, to: e.target })) }));
  };

  const handleSave = async () => {
    if (!name.trim()) { toast.error('请填写工作流名称'); return; }
    const finalGraph = toGraph(rfNodes, rfEdges, graph);
    setSaving(true);
    try {
      if (isNew) {
        const { data } = await dagApi.createWorkflow({
          name, cron_expression: cron || null, graph_json: finalGraph,
        });
        toast.success('工作流已创建');
        navigate(`/data/sync/dag/${data.id}`);
      } else {
        await dagApi.updateWorkflow(Number(workflowId), {
          name, cron_expression: cron || null, graph_json: finalGraph, version,
        });
        const { data } = await dagApi.getWorkflow(Number(workflowId));
        setVersion(data.version);
        toast.success('已保存');
      }
      setGraph(finalGraph);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    if (isNew) { toast.error('请先保存工作流'); return; }
    try {
      await dagApi.updateWorkflow(Number(workflowId), {
        name, cron_expression: cron || null,
        graph_json: toGraph(rfNodes, rfEdges, graph), version,
      });
      const { data } = await dagApi.runWorkflow(Number(workflowId));
      toast.success('已进入执行队列');
      navigate(`/data/sync/dag-runs/${data.run_id}`);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '触发失败');
    }
  };

  const handleSqlToDag = async () => {
    try {
      const { data } = await dagApi.sqlToDag(sqlScript, sqlDatasource);
      const g = data.graph_json as DagGraph;
      setGraph(g);
      setRfNodes(layoutGraph(g));
      setRfEdges(g.edges.map((e, i) => ({ id: `e-${i}`, source: e.from, target: e.to })));
      setSqlDlgOpen(false);
      toast.success(`已生成 ${data.node_count} 个节点 / ${data.edge_count} 条依赖`);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || 'SQL 解析失败');
    }
  };

  const handleExportSql = async () => {
    try {
      const { data } = await dagApi.dagToSql(toGraph(rfNodes, rfEdges, graph));
      setExportSql(data.sql);
      setExportOpen(true);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || '导出失败');
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 p-4 border-b">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => navigate('/data/sync/dag')}>
            <ArrowLeft className="w-4 h-4 mr-1" />返回
          </Button>
          <Input className="w-[220px]" placeholder="工作流名称 *" value={name} onChange={e => setName(e.target.value)} />
          <Input className="w-[160px]" placeholder="Cron（可选）" value={cron} onChange={e => setCron(e.target.value)} />
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setSqlDlgOpen(true)}>
            <Wand2 className="w-4 h-4 mr-1" />从 SQL 生成
          </Button>
          <Button variant="outline" size="sm" onClick={handleExportSql}>
            <Download className="w-4 h-4 mr-1" />导出 SQL
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            <Save className="w-4 h-4 mr-1" />保存
          </Button>
          <Button size="sm" onClick={handleRun} disabled={isNew}>
            <Play className="w-4 h-4 mr-1" />运行
          </Button>
        </div>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left: node palette */}
        <div className="w-[180px] border-r p-3 space-y-2">
          <p className="text-xs font-medium text-muted-foreground">节点</p>
          <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => addNode('sync')}>
            <Plus className="w-3.5 h-3.5 mr-1" />同步节点
          </Button>
          <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => addNode('sql_task')}>
            <FileCode2 className="w-3.5 h-3.5 mr-1" />SQL 任务
          </Button>
          <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => addNode('control')}>
            <Plus className="w-3.5 h-3.5 mr-1" />控制节点
          </Button>
          <p className="text-xs text-muted-foreground pt-2">
            拖拽节点右侧锚点到另一节点左侧锚点定义依赖。
          </p>
        </div>

        {/* Canvas: 编排图（控制流） / 数据流（源→转换→目标）双视图 */}
        <Tabs defaultValue="design" className="flex-1 flex flex-col min-h-0">
          <TabsList className="mx-3 mt-2 self-start">
            <TabsTrigger value="design">编排图</TabsTrigger>
            <TabsTrigger value="dataflow">数据流</TabsTrigger>
          </TabsList>
          <TabsContent value="design" className="flex-1 min-h-0 mt-2 data-[state=active]:flex">
            <div className="flex-1 min-h-0 w-full">
              <ReactFlow
                nodes={rfNodes}
                edges={rfEdges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={(conn) => {
                  if (conn.source && conn.target) {
                    syncEdges([...rfEdges, { id: `e-${Date.now()}`, source: conn.source, target: conn.target }]);
                  }
                }}
                onNodeClick={(_, node) => setSelectedKey(node.id)}
                onEdgeClick={(_, edge) => syncEdges(rfEdges.filter(e => e.id !== edge.id))}
                fitView
              >
                <Background />
                <Controls />
                <MiniMap />
              </ReactFlow>
            </div>
          </TabsContent>
          <TabsContent value="dataflow" className="flex-1 min-h-0 mt-2">
            <DagDataflowView
              workflowId={isNew ? undefined : Number(workflowId)}
              graph={toGraph(rfNodes, rfEdges, graph)}
            />
          </TabsContent>
        </Tabs>

        {/* Right: config panel */}
        <div className="w-[300px] border-l p-3 overflow-y-auto">
          {selectedNode ? (
            <>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-medium">节点配置</p>
                <Button variant="ghost" size="sm" onClick={removeSelected}>
                  <Trash2 className="w-3.5 h-3.5 text-red-500" />
                </Button>
              </div>
              <NodeConfigPanel node={selectedNode} udfs={udfs} onChange={updateSelectedConfig} />
            </>
          ) : (
            <p className="text-sm text-muted-foreground">点击画布中的节点进行配置。</p>
          )}
        </div>
      </div>

      {/* SQL → DAG 对话框 */}
      <Dialog open={sqlDlgOpen} onOpenChange={setSqlDlgOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>从 SQL 生成 DAG</DialogTitle></DialogHeader>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              粘贴多语句 SQL 脚本（支持 SELECT / INSERT INTO...SELECT / CREATE TABLE AS...SELECT）。
              将按表产出/消费依赖自动拆解为节点，生成后可在画布继续编辑。
            </p>
            <DatasourceSelect value={sqlDatasource} onChange={setSqlDatasource} placeholder="主数据源（未限定表所属源）" />
            <Textarea className="font-mono min-h-[220px]" value={sqlScript} onChange={e => setSqlScript(e.target.value)}
              placeholder={'CREATE TABLE agg AS SELECT ...;\nINSERT INTO doris_ds.dwh.agg SELECT ... FROM agg;'} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSqlDlgOpen(false)}>取消</Button>
            <Button onClick={handleSqlToDag} disabled={!sqlScript.trim() || !sqlDatasource}>解析生成</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 导出 SQL 对话框 */}
      <Dialog open={exportOpen} onOpenChange={setExportOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>导出 SQL（人审阅用）</DialogTitle></DialogHeader>
          <Textarea className="font-mono min-h-[260px]" readOnly value={exportSql} />
          <DialogFooter>
            <Button variant="outline" onClick={() => navigator.clipboard.writeText(exportSql).then(() => toast.success('已复制'))}>
              复制
            </Button>
            <Button onClick={() => setExportOpen(false)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
