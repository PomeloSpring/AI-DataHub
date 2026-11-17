import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { t , t as tr } from '@/i18n/runtime';
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
import { toast } from '@/lib/toast';
import {
  Save, Play, FileCode2, Wand2, Plus, Trash2, ArrowLeft, Download,
} from 'lucide-react';
import {
  dagApi, type DagGraph, type DagNode, type DagWorkflow,
} from '@/api/dag';
import { udfApi, type Udf } from '@/api/udf';
import { DatasourceSelect } from './DatasourceSelect';
import DagDataflowView from './DagDataflowView';
import { useTranslation } from 'react-i18next';

// ── 自定义节点 ──────────────────────────────────────────────────
const TYPE_LABEL: Record<string, string> = {
  sync: tr('k_e88ab5ba61'), sql_task: tr('k_c250192752'), control: tr('k_34e47d7e3e'),
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

// ── 节点配置面板 ────────────────────────────────────────────────
function NodeConfigPanel({
  node, udfs, onChange,
}: { node: DagNode; udfs: Udf[]; onChange: (config: any) => void }) {
  const config = node.config || {};
  const set = (patch: any) => onChange({ ...config, ...patch });

  return (
    <div className="space-y-3 text-sm">
      <div>
        <label className="text-xs font-medium text-muted-foreground">{t("k_3de90b4272")}</label>
        <Input className="mt-1" value={node.name || ''} onChange={e => onChange({ ...config, __name: e.target.value })} />
      </div>

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
      }).catch(() => toast.error(tr('k_8ba34adb23')));
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

  const handleRun = async () => {
    if (isNew) { toast.error(tr('k_f76664781d')); return; }
    try {
      await dagApi.updateWorkflow(Number(workflowId), {
        name, cron_expression: cron || null,
        graph_json: toGraph(rfNodes, rfEdges, graph), version,
      });
      const { data } = await dagApi.runWorkflow(Number(workflowId));
      toast.success(tr('k_e2917cf924'));
      navigate(`/data/sync/dag-runs/${data.run_id}`);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || tr('k_02b526816d'));
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
      toast.success(tr('k_a454942a37', { p0: data.node_count, p1: data.edge_count }));
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || tr('k_b4077309f9'));
    }
  };

  const handleExportSql = async () => {
    try {
      const { data } = await dagApi.dagToSql(toGraph(rfNodes, rfEdges, graph));
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
          <Button variant="outline" size="sm" onClick={handleExportSql}>
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
                <p className="text-sm font-medium">{t("k_7eddbd9247")}</p>
                <Button variant="ghost" size="sm" onClick={removeSelected}>
                  <Trash2 className="w-3.5 h-3.5 text-red-500" />
                </Button>
              </div>
              <NodeConfigPanel node={selectedNode} udfs={udfs} onChange={updateSelectedConfig} />
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

      {/* 导出 SQL 对话框 */}
      <Dialog open={exportOpen} onOpenChange={setExportOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{t("k_6a2ce32a7a")}</DialogTitle></DialogHeader>
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
