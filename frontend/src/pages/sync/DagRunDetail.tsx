import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
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
import AutoFitView from '@/components/graph/AutoFitView';
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

// 节点类型显示为业务名（不把 materialize/sql_task 这类内部枚举直接抛给用户）
const NODE_TYPE_LABEL: Record<string, string> = {
  materialize: tr('k_979c3cfe21'),
  sync: tr('k_e88ab5ba61'),
  sql_task: tr('k_c250192752'),
  control: tr('k_34e47d7e3e'),
};

const WRITE_MODE_LABEL: Record<string, string> = {
  overwrite: tr('k_8cd94a9232'),
  append: tr('k_b64615865c'),
  partition_overwrite: tr('k_83dbc5deef'),
};

// 物化窗口（本次跑的是哪一段）：历史初始化 / 日常回看；续跑与跳过另行拼接
const WINDOW_MODE_LABEL: Record<string, string> = {
  history: tr('k_c1d6fbb6a9'),
  lookback: tr('k_fb1c24903f'),
};

// 分区条目是对象（date/target_partition/rows_written），直接 join 会渲染成
// [object Object]；必须按字段拼成可读行。
function fmtPartition(item: any): string {
  if (item == null) return '';
  if (typeof item !== 'object') return String(item);
  const rows = item.rows_written != null ? `${item.rows_written} ${tr('k_2a2b777ae4')}` : '';
  return [item.date, item.target_partition, rows].filter(Boolean).join(' · ');
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
  const location = useLocation();
  // 「返回」回到进入来源（运行记录/物化任务），没有来源（直接打开链接）时回运行记录：
  // 本页是“运行记录”的下钻页，返回物化任务页会丢掉用户刚看的那份历史。
  const backTo = (location.state as { from?: string } | null)?.from || '/data/sync/logs';
  const [run, setRun] = useState<DagRun | null>(null);
  const [nodeRuns, setNodeRuns] = useState<DagNodeRun[]>([]);
  const [graph, setGraph] = useState<{ nodes: any[]; edges: { from: string; to: string }[] }>({ nodes: [], edges: [] });
  const [errorDetail, setErrorDetail] = useState<DagNodeRun | null>(null);
  const [resultDetail, setResultDetail] = useState<DagNodeRun | null>(null);
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

  // 节点业务名：图里是 name，节点明细里是 key —— 两处必须能对上
  const nameByKey = useMemo(() => {
    const map: Record<string, string> = {};
    graph.nodes.forEach((n: any) => { map[n.key] = n.name || n.key; });
    return map;
  }, [graph]);

  // 已收尾的 run 里没有 node_run 行的节点 = 未执行（没有 attempt，不是“待运行”）
  const isTerminalRun = run != null && !['queued', 'running'].includes(run.status);

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
    // 运行过的节点必须在图上出现：编排在运行后被改过（节点改名/改 key）也不能
    // 让已有执行记录的节点凭空消失，否则图与节点明细就是两套事实。
    const known = new Set(graph.nodes.map((n: any) => n.key));
    const displayed: any[] = [
      ...graph.nodes,
      ...Array.from(new Set(nodeRuns.map(nr => nr.node_key)))
        .filter(k => !known.has(k))
        .map(k => ({ key: k, name: k })),
    ];
    const out: Node[] = [];
    const push = (n: any, x: number, y: number) => {
      const nr = latestStatus[n.key];
      const conf = nr ? (STATUS_MAP[nr.status] || null) : null;
      out.push({
        id: n.key, type: 'runNode', position: { x, y },
        data: {
          label: n.name || n.key,
          statusDot: conf?.dot || 'bg-slate-400',
          statusLabel: conf?.label
            || (isTerminalRun ? tr('k_c3f05bd3a3') : tr('k_dcebd71155')),
          attempt: nr?.attempt || 1,
        },
      });
    };
    if (graph.edges.length === 0) {
      // 无依赖边（如 5 站 × 3 档的独立物化节点）：铺成网格，
      // 堆成一列 时视口里只看得到前几个，看起来像“节点丢了”。
      // 间距收紧到卡片实际尺寸附近：5 行铺太开会被迫缩到 0.35，文字就看不清了。
      const cols = 3;
      displayed.forEach((n, i) =>
        push(n, (i % cols) * 260 + 40, Math.floor(i / cols) * 125 + 40));
      return out;
    }
    const byDepth: Record<number, any[]> = {};
    displayed.forEach((n: any) => {
      const d = depth[n.key] ?? 0;
      (byDepth[d] = byDepth[d] || []).push(n);
    });
    Object.entries(byDepth).forEach(([d, nodes]) => {
      nodes.forEach((n: any, i: number) => push(n, Number(d) * 300 + 40, i * 140 + 40));
    });
    return out;
  }, [graph, latestStatus, nodeRuns, isTerminalRun]);

  const rfEdges: Edge[] = useMemo(
    () => graph.edges.map((e, i) => ({ id: `e-${i}`, source: e.from, target: e.to })),
    [graph]);

  // 节点明细必须与统计/图同口径：编排里的每个节点都占一行，
  // 没有 node_run 行（中止/失败 run 里还没轮到）的显示「未执行」，
  // 否则 15 个节点只列 10 行，用户看到的就是“表丢了 5 个节点”。
  const tableRows = useMemo(() => {
    const byKey = new Map<string, DagNodeRun>();
    Object.entries(latestStatus).forEach(([k, v]) => byKey.set(k, v));
    const rows = (graph.nodes as any[]).map((n: any) => ({
      key: String(n.key),
      name: n.name || n.key,
      nodeType: n.type || '',
      nodeRun: byKey.get(String(n.key)) || null,
    }));
    const known = new Set(rows.map(r => r.key));
    // 编排里已删、但本 run 真的执行过的节点：依旧列出，不丢历史
    byKey.forEach((nr, k) => {
      if (!known.has(k)) {
        rows.push({ key: k, name: nameByKey[k] || k, nodeType: nr.node_type, nodeRun: nr });
      }
    });
    return rows;
  }, [graph, latestStatus, nameByKey]);

  const stats = run?.stats_json || {};
  // 被运维中止的 run 有 cancel 节点：不展示这一列就变成「成功 5 / 失败 0 / 跳过 0」的 10 个黑洞
  const hasCancelled = Number(stats.nodes_cancelled || 0) > 0;
  // 中止/失败收尾的 run 里没来得及创建节点的部分：也算“未执行”，不隐形成黑洞
  const executedNodes = Number(stats.nodes_success || 0) + Number(stats.nodes_failed || 0)
    + Number(stats.nodes_skipped || 0) + Number(stats.nodes_cancelled || 0);
  const notRunNodes = Math.max(0, Number(stats.nodes_total || graph.nodes.length) - executedNodes);
  const showNotRun = notRunNodes > 0 && run != null && !['queued', 'running'].includes(run.status);
  const statCols = 5 + (hasCancelled ? 1 : 0) + (showNotRun ? 1 : 0);
  const statGridCls = statCols >= 7 ? 'md:grid-cols-7'
    : statCols === 6 ? 'md:grid-cols-6' : 'md:grid-cols-5';

  // 本次跑的是哪一段（历史初始化 / 续跑 / 日常回看 / 本次跳过）
  const resultWindow = useMemo(() => {
    const win: any = resultDetail?.result_json?.window || {};
    const mode = String(win.mode || '');
    if (mode === 'skipped') {
      return [tr('k_834e17e160'), win.reason].filter(Boolean).join(' · ');
    }
    if (mode === 'resume') {
      return [tr('k_d1c4797bb7', { p0: win.resumed_from || '—' }), win.end].filter(Boolean).join(' ~ ');
    }
    if (mode === 'history' || mode === 'lookback') {
      return [
        WINDOW_MODE_LABEL[mode],
        [win.start, win.end].filter(Boolean).join(' ~ '),
        win.cells != null ? tr('k_afeddadd94', { p0: win.cells }) : '',
      ].filter(Boolean).join(' · ');
    }
    return '';
  }, [resultDetail]);

  // 分区条目是对象（date/target_partition/rows_written），必须逐条格式化后才可读
  const resultPartitions = useMemo(() => {
    const parts = resultDetail?.result_json?.partitions;
    return Array.isArray(parts) ? parts : [];
  }, [resultDetail]);

  return (
    <div className="p-6 space-y-4 h-full flex flex-col">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => navigate(backTo)}>
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
                navigate(`/data/sync/dag-runs/${data.run_id}`, { state: { from: backTo } });
              } catch (error: any) {
                toast.error(error?.response?.data?.detail || tr('k_c17c7b13e3'));
              }
            }}>
              <RotateCcw className="w-4 h-4 mr-1" />{tr('k_73aa205577')}</Button>
          )}
        </div>
      </div>

      {/* 统计卡：与节点表同源（节点终态反算，stats_json 为空也真实） */}
      <div className={`grid grid-cols-2 gap-3 ${statGridCls}`}>
        {[
          [tr('k_e840cd6f1e'), tr('k_17a35523f8', { p0: stats.nodes_success ?? 0, p1: stats.nodes_total ?? graph.nodes.length })],
          [tr('k_3e3c8068bb'), String(stats.nodes_failed ?? 0)],
          [tr('k_92636e8c8f'), String(stats.nodes_skipped ?? 0)],
          ...(hasCancelled ? [[tr('k_c6ae34696e'), String(stats.nodes_cancelled ?? 0)]] : []),
          ...(showNotRun ? [[tr('k_c3f05bd3a3'), String(notRunNodes)]] : []),
          [tr('k_3634f29fb1'), String(stats.rows_read ?? 0)],
          [tr('k_44b75fa528'), String(stats.rows_written ?? 0)],
        ].map(([label, value]) => (
          <div key={label} className="border rounded-lg p-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="text-lg font-semibold">{value}</p>
          </div>
        ))}
      </div>

      {/* DAG 图（运行状态 / 数据流双视图）：给固定高度 —— 5 站 × 3 档在矮画布里
          只能缩到 0.35（文字不可读），且画布高度随表格渲染抖动会影响适配。
          shrink-0 必须显式：作为 flex 子项默认会被下面的节点表挤到 50px（画布塌成 0）。 */}
      <Tabs defaultValue="run" className="h-[460px] shrink-0 flex flex-col">
        <TabsList className="self-start">
          <TabsTrigger value="run">{t("k_2a4080ad9f")}</TabsTrigger>
          <TabsTrigger value="flow">{t("k_b2047f645e")}</TabsTrigger>
        </TabsList>
        <TabsContent value="run" className="flex-1 min-h-0 mt-2 data-[state=active]:flex">
          <div className="border rounded-lg flex-1 min-h-0 w-full">
            <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} fitView
              nodesDraggable={false}>
              {/* 节点异步到达且需先测量：量完再适配，否则 5 站 × 3 档会被裁掉一端 */}
              <AutoFitView key={runId ? `${runId}:${rfNodes.length}` : rfNodes.length} />
              <Background />
              <Controls />
            </ReactFlow>
          </div>
        </TabsContent>
        <TabsContent value="flow" className="flex-1 min-h-0 mt-2 data-[state=active]:flex">
          {/* 容器必须有确定高度：ReactFlow 用的是 height:100%，父层只给 min-h 时
              百分比高度解析为 0，画布变成 306×0 的空白（浏览器实测 0 高）。 */}
          <div className="border rounded-lg flex-1 min-h-0 w-full">
            <DagDataflowView workflowId={run?.workflow_id} />
          </div>
        </TabsContent>
      </Tabs>

      {/* 节点明细：必须 shrink-0 —— 作为 flex 子项默认可被压缩，15 行会被父容器裁掉，
          只剩前 3 行（看起来像“只有中国站点的节点”） */}
      <div className="border rounded-lg overflow-hidden shrink-0">
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
            {tableRows.length === 0 ? (
              <TableRow><TableCell colSpan={9} className="text-center p-6 text-muted-foreground">{t("k_db576e80fb")}</TableCell></TableRow>
            ) : tableRows.map(row => {
              const nr = row.nodeRun;
              return (
                <TableRow key={row.key} className={nr ? '' : 'text-muted-foreground'}>
                  <TableCell className="font-medium">
                    {row.name}
                    {row.name !== row.key && (
                      <span className="block text-[11px] font-normal text-muted-foreground">{row.key}</span>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{NODE_TYPE_LABEL[row.nodeType] || row.nodeType || '—'}</TableCell>
                  <TableCell>{nr ? <StatusBadge status={nr.status} /> : (
                    <Badge variant="outline" className="bg-gray-500/10 text-gray-500 border-gray-500/20">
                      {tr('k_c3f05bd3a3')}</Badge>
                  )}</TableCell>
                  <TableCell>{nr ? nr.attempt : '—'}</TableCell>
                  <TableCell>{nr ? nr.rows_read : '—'}</TableCell>
                  <TableCell>{nr ? nr.rows_written : '—'}</TableCell>
                  <TableCell>{nr && nr.elapsed_ms != null ? `${nr.elapsed_ms}ms` : '—'}</TableCell>
                  <TableCell className="text-xs">{fmtTime(nr?.finished_at)}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      {nr?.result_json && Object.keys(nr.result_json).length > 0 && (
                        <Button variant="ghost" size="sm" onClick={() => setResultDetail(nr)}>{tr('k_7388cb5ee9')}</Button>
                      )}
                      {nr?.error_message && (
                        <Button variant="ghost" size="sm" className="text-red-500" onClick={() => setErrorDetail(nr)}>{t("k_8b0f6c2374")}</Button>
                      )}
                      {!nr?.error_message && !(nr?.result_json && Object.keys(nr.result_json).length > 0) && '—'}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* 物化结果（平台自产摘要：分区 / 对账 / 写入策略，不含数据行与生成 SQL） */}
      <Dialog open={!!resultDetail} onOpenChange={() => setResultDetail(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{tr('k_7388cb5ee9')}{' '}{resultDetail?.node_key}</DialogTitle>
          </DialogHeader>
          {resultDetail?.result_json && (
            <div className="space-y-2 text-sm">
              {[
                [tr('k_fc2b4735c3'), resultDetail.result_json.dataset || '—'],
                [tr('k_442be9c497'), resultDetail.result_json.target || '—'],
                [tr('k_244d6de40f'),
                  WRITE_MODE_LABEL[String(resultDetail.result_json.write_mode)]
                  || resultDetail.result_json.write_mode || '—'],
                [tr('k_26f34f61c5'), resultWindow || '—'],
                [tr('k_7e1ed047d6'), resultDetail.result_json.strategy || '—'],
                [tr('k_b7ef6de9b3'), resultDetail.result_json.budget?.max_rows != null
                  ? String(resultDetail.result_json.budget.max_rows) : '—'],
              ].map(([label, value]) => (
                <div key={String(label)} className="flex gap-2">
                  <span className="text-muted-foreground shrink-0">{label}</span>
                  <span className="break-all">{value}</span>
                </div>
              ))}
              <div className="space-y-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-muted-foreground">{tr('k_66c260d88c')}</span>
                  {resultPartitions.length > 0 && (
                    <span className="text-xs text-muted-foreground">
                      {tr('k_afeddadd94', { p0: resultPartitions.length })}
                    </span>
                  )}
                </div>
                {resultPartitions.length === 0 ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  <div className="max-h-44 overflow-auto rounded-md border divide-y text-xs">
                    {resultPartitions.map((item: any, i: number) => (
                      <div key={i} className="px-2 py-1 flex items-center justify-between gap-2">
                        <span className="truncate" title={fmtPartition(item)}>
                          {fmtPartition(item) || '—'}</span>
                        {item?.reconciled === false && (
                          <Badge variant="outline"
                            className="shrink-0 bg-yellow-500/10 text-yellow-500 border-yellow-500/20">
                            {tr('k_13c008c635')}
                          </Badge>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex gap-2 items-center">
                <span className="text-muted-foreground">{tr('k_53d880a916')}</span>
                <Badge variant="outline" className={resultDetail.result_json.reconciled
                  ? 'bg-green-500/10 text-green-500 border-green-500/20'
                  : 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20'}>
                  {resultDetail.result_json.reconciled ? tr('k_53d880a916') : tr('k_13c008c635')}
                </Badge>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

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
