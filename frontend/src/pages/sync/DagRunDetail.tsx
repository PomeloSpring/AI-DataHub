import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ReactFlow, Background, Controls, Handle, Position,
  type Node, type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { toast } from '@/lib/toast';
import { ArrowLeft, RefreshCw, Square, RotateCcw } from 'lucide-react';
import {
  dagApi, type DagRun, type DagNodeRun,
} from '@/api/dag';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import DagDataflowView from './DagDataflowView';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

const STATUS_MAP: Record<string, { label: string; className: string; dot: string }> = {
  queued: { label: tr('k_e5ac1d2029'), className: 'bg-blue-500/10 text-blue-500 border-blue-500/20', dot: 'bg-blue-500' },
  running: { label: tr('k_5942497005'), className: 'bg-blue-500/10 text-blue-500 border-blue-500/20', dot: 'bg-blue-500 animate-pulse' },
  success: { label: tr('k_51991a5d11'), className: 'bg-green-500/10 text-green-500 border-green-500/20', dot: 'bg-green-500' },
  failed: { label: tr('k_3e3c8068bb'), className: 'bg-red-500/10 text-red-500 border-red-500/20', dot: 'bg-red-500' },
  partial: { label: tr('k_2f2b7e05e3'), className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20', dot: 'bg-yellow-500' },
  skipped: { label: tr('k_92636e8c8f'), className: 'bg-gray-500/10 text-gray-500 border-gray-500/20', dot: 'bg-gray-400' },
  timeout: { label: tr('k_ff06c243d7'), className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20', dot: 'bg-yellow-500' },
  cancelled: { label: tr('k_a5ffdc95ee'), className: 'bg-gray-500/10 text-gray-500 border-gray-500/20', dot: 'bg-gray-400' },
};

function StatusBadge({ status }: { status: string | null }) {
  const conf = STATUS_MAP[status || ''] || { label: status || '—', className: 'bg-muted text-muted-foreground', dot: 'bg-slate-400' };
  return <Badge variant="outline" className={conf.className}>{conf.label}</Badge>;
}

function fmtTime(value?: string | null): string {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '—';
}

function RunNode({ data }: { data: any }) {
  return (
    <div className="bg-card border rounded-lg shadow-sm min-w-[170px] max-w-[240px]">
      <Handle type="target" position={Position.Left} className="!w-2 !h-2" />
      <div className="flex items-center gap-2 px-3 py-2 border-b">
        <div className={`w-2.5 h-2.5 rounded-full ${data.statusDot || 'bg-slate-400'}`} />
        <span className="font-medium text-sm truncate">{data.label}</span>
      </div>
      <div className="px-3 py-1.5 text-xs text-muted-foreground">
        {data.statusLabel}{data.attempt > 1 ? tr('k_c174f06ed5', { p0: data.attempt }) : ''}
      </div>
      <Handle type="source" position={Position.Right} className="!w-2 !h-2" />
    </div>
  );
}

const nodeTypes = { runNode: RunNode };

export default function DagRunDetail() {
  const { t } = useTranslation('ui');
  const { runId } = useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState<DagRun | null>(null);
  const [nodeRuns, setNodeRuns] = useState<DagNodeRun[]>([]);
  const [graph, setGraph] = useState<{ nodes: any[]; edges: { from: string; to: string }[] }>({ nodes: [], edges: [] });
  const [errorDetail, setErrorDetail] = useState<DagNodeRun | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = async () => {
    if (!runId) return;
    try {
      const { data } = await dagApi.getRun(Number(runId));
      setRun(data.run);
      setNodeRuns(data.nodes || []);
      const wf = await dagApi.getWorkflow(data.run.workflow_id);
      setGraph(wf.data.graph_json || { nodes: [], edges: [] });
      if (!['queued', 'running'].includes(data.run.status)) setAutoRefresh(false);
    } catch {
      toast.error(tr('k_33ca15579d'));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh, runId]);

  // 最新 attempt 的节点状态
  const latestStatus = useMemo(() => {
    const map: Record<string, DagNodeRun> = {};
    nodeRuns.forEach(nr => {
      const prev = map[nr.node_key];
      if (!prev || nr.attempt >= prev.attempt) map[nr.node_key] = nr;
    });
    return map;
  }, [nodeRuns]);

  const rfNodes: Node[] = useMemo(() => {
    const depth: Record<string, number> = {};
    const upstream: Record<string, string[]> = {};
    graph.nodes.forEach((n: any) => { depth[n.key] = 0; upstream[n.key] = []; });
    graph.edges.forEach(e => {
      if (upstream[e.to] && depth[e.from] !== undefined) upstream[e.to].push(e.from);
    });
    for (let i = 0; i < graph.nodes.length; i++) {
      graph.nodes.forEach((n: any) => {
        const deps = upstream[n.key] || [];
        depth[n.key] = deps.length ? Math.max(...deps.map(d => depth[d] + 1)) : 0;
      });
    }
    const byDepth: Record<number, any[]> = {};
    graph.nodes.forEach((n: any) => {
      const d = depth[n.key] ?? 0;
      (byDepth[d] = byDepth[d] || []).push(n);
    });
    const out: Node[] = [];
    Object.entries(byDepth).forEach(([d, nodes]) => {
      nodes.forEach((n: any, i: number) => {
        const nr = latestStatus[n.key];
        const conf = STATUS_MAP[nr?.status || ''] || STATUS_MAP.queued;
        out.push({
          id: n.key, type: 'runNode',
          position: { x: Number(d) * 300 + 40, y: i * 140 + 40 },
          data: {
            label: n.name || n.key,
            statusDot: conf.dot,
            statusLabel: nr ? conf.label : tr('k_dcebd71155'),
            attempt: nr?.attempt || 1,
          },
        });
      });
    });
    return out;
  }, [graph, latestStatus]);

  const rfEdges: Edge[] = useMemo(
    () => graph.edges.map((e, i) => ({ id: `e-${i}`, source: e.from, target: e.to })),
    [graph]);

  const stats = run?.stats_json || {};

  return (
    <div className="p-6 space-y-4 h-full flex flex-col">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => navigate('/data/sync/dag')}>
              <ArrowLeft className="w-4 h-4 mr-1" />{tr('k_11d0241540')}</Button>
            <h1 className="text-xl font-bold">{t("k_a939e999ca")}</h1>
            {run && <StatusBadge status={run.status} />}
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            {run?.workflow_name || ''} {tr('k_64b778ac54')}{' '}{run?.trigger_type || '—'} {tr('k_214fc2da49')}{' '}{fmtTime(run?.started_at)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} />
            {tr('k_5f518347ba')}</label>
          <Button variant="outline" size="sm" onClick={load}><RefreshCw className="w-4 h-4 mr-1" />{t("k_38108eaa1d")}</Button>
          {run && ['queued', 'running'].includes(run.status) && (
            <Button variant="outline" size="sm" onClick={async () => {
              await dagApi.stopRun(run.id); toast.success(tr('k_2530fb3f66')); load();
            }}>
              <Square className="w-4 h-4 mr-1" />{tr('k_095e938e2a')}</Button>
          )}
          {run && !['queued', 'running'].includes(run.status) && (
            <Button variant="outline" size="sm" onClick={async () => {
              try {
                const { data } = await dagApi.retryRun(run.id);
                toast.success(tr('k_0eea91ef85'));
                navigate(`/data/sync/dag-runs/${data.run_id}`);
              } catch (error: any) {
                toast.error(error?.response?.data?.detail || tr('k_c17c7b13e3'));
              }
            }}>
              <RotateCcw className="w-4 h-4 mr-1" />{tr('k_73aa205577')}</Button>
          )}
        </div>
      </div>

      {/* 统计卡 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          [tr('k_e840cd6f1e'), tr('k_17a35523f8', { p0: stats.nodes_success ?? 0, p1: stats.nodes_total ?? graph.nodes.length })],
          [tr('k_3e3c8068bb'), String(stats.nodes_failed ?? 0)],
          [tr('k_92636e8c8f'), String(stats.nodes_skipped ?? 0)],
          [tr('k_3634f29fb1'), String(stats.rows_read ?? 0)],
          [tr('k_44b75fa528'), String(stats.rows_written ?? 0)],
        ].map(([label, value]) => (
          <div key={label} className="border rounded-lg p-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="text-lg font-semibold">{value}</p>
          </div>
        ))}
      </div>

      {/* DAG 图（运行状态 / 数据流双视图） */}
      <Tabs defaultValue="run" className="flex-1 min-h-[380px] flex flex-col">
        <TabsList className="self-start">
          <TabsTrigger value="run">{t("k_2a4080ad9f")}</TabsTrigger>
          <TabsTrigger value="flow">{t("k_b2047f645e")}</TabsTrigger>
        </TabsList>
        <TabsContent value="run" className="flex-1 min-h-[320px] mt-2 data-[state=active]:flex">
          <div className="border rounded-lg flex-1 min-h-[320px] w-full">
            <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} fitView nodesDraggable={false}>
              <Background />
              <Controls />
            </ReactFlow>
          </div>
        </TabsContent>
        <TabsContent value="flow" className="flex-1 min-h-[320px] mt-2">
          <div className="border rounded-lg min-h-[320px]">
            <DagDataflowView workflowId={run?.workflow_id} />
          </div>
        </TabsContent>
      </Tabs>

      {/* 节点明细 */}
      <div className="border rounded-lg overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("k_e840cd6f1e")}</TableHead>
              <TableHead>{t("k_e4e46c7235")}</TableHead>
              <TableHead>{t("k_62e951a692")}</TableHead>
              <TableHead>{t("k_31d1e44656")}</TableHead>
              <TableHead>{t("k_b6b97ac60d")}</TableHead>
              <TableHead>{t("k_9e93bdccb2")}</TableHead>
              <TableHead>{t("k_a9704e1997")}</TableHead>
              <TableHead>{t("k_76b9880829")}</TableHead>
              <TableHead className="text-right">{t("k_0a2c91cec6")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {nodeRuns.length === 0 ? (
              <TableRow><TableCell colSpan={9} className="text-center p-6 text-muted-foreground">{t("k_db576e80fb")}</TableCell></TableRow>
            ) : nodeRuns.map(nr => (
              <TableRow key={nr.id}>
                <TableCell className="font-medium">{nr.node_key}</TableCell>
                <TableCell className="text-muted-foreground">{nr.node_type}</TableCell>
                <TableCell><StatusBadge status={nr.status} /></TableCell>
                <TableCell>{nr.attempt}</TableCell>
                <TableCell>{nr.rows_read}</TableCell>
                <TableCell>{nr.rows_written}</TableCell>
                <TableCell>{nr.elapsed_ms != null ? `${nr.elapsed_ms}ms` : '—'}</TableCell>
                <TableCell className="text-xs">{fmtTime(nr.finished_at)}</TableCell>
                <TableCell className="text-right">
                  {nr.error_message ? (
                    <Button variant="ghost" size="sm" className="text-red-500" onClick={() => setErrorDetail(nr)}>{t("k_8b0f6c2374")}</Button>
                  ) : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={!!errorDetail} onOpenChange={() => setErrorDetail(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{tr('k_8f182b117d')}{' '}{errorDetail?.node_key}</DialogTitle></DialogHeader>
          <div className="space-y-2 text-sm">
            <div className="flex gap-2 items-center">
              <Badge variant="outline">{errorDetail?.error_code || 'ERROR'}</Badge>
              <span className="text-xs text-muted-foreground">{tr('k_32575a9cd0', { p0: errorDetail?.attempt })}</span>
            </div>
            <p className="text-muted-foreground whitespace-pre-wrap">{errorDetail?.error_message}</p>
            <p className="text-xs text-muted-foreground">{t("k_4253c252a3")}</p>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
