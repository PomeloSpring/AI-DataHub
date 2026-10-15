/**
 * 评测运行面板：触发 / 进度轮询 / 结果抽屉 / 失败聚类 / 就地处置。
 *
 * 三处针对旧版缺陷的修正：
 *  1. 旧版 async 触发后只提示"已投递，请刷新"，用户得手动点刷新才知道跑完没。
 *     现在轮询运行状态，queued→running→done 推进可见（不假报完成）。
 *  2. 旧版逐用例结果与运行列表挤在一屏，点开一条就往下撑一大块。改右侧抽屉。
 *  3. 旧版失败逐条平铺，看不出"10 条同一原因"其实是一类问题。按原因前缀聚类，
 *     并在每条失败上就地给「转进化候选 / 标已知缺陷 / 退役」——把闭环接上，
 *     而不是让人换个页面再手抄一遍问句。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Play, X } from 'lucide-react';
import client from '@/api/client';
import {
  SUITE_LABEL, TRIGGER_LABEL, clusterKey, expectList, pct,
  type CapabilityMeta, type EvalCase, type EvalRun, type RunResult, type SuiteMeta,
} from './shared';

interface Props {
  suites: SuiteMeta[];
  capabilities: CapabilityMeta[];
  runs: EvalRun[];
  cases: EvalCase[];
  onRefreshRuns: () => void;
  onRefreshCases: () => void;
}

const POLL_MS = 2000;
const POLL_MAX = 60;   // 最多约 2 分钟；超了就停下来说明情况，不无限转圈假装在跑

export default function EvalRunsPanel({
  suites, capabilities, runs, cases, onRefreshRuns, onRefreshCases,
}: Props) {
  const [detail, setDetail] = useState<{ run: EvalRun; results: RunResult[] } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [pending, setPending] = useState<{ runId: number; suite: string } | null>(null);
  const [triage, setTriage] = useState<{ kind: 'candidate' | 'status'; r: RunResult } | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const polls = useRef(0);

  // 用例索引：结果表按设计不冗余存 capability/问句（单一事实源在用例表），
  // 这里在内存里 join 出来供失败处置使用。
  const caseIndex = useMemo(() => {
    const m = new Map<string, EvalCase>();
    for (const c of cases) m.set(`${c.suite}|${c.case_key}`, c);
    return m;
  }, [cases]);

  const enrich = useCallback((suite: string, rows: RunResult[]): RunResult[] =>
    rows.map(r => {
      const c = caseIndex.get(`${suite}|${r.case_key}`);
      return c
        ? { ...r, capability: c.capability, question: c.question, case_status: c.case_status }
        : r;
    }), [caseIndex]);

  const trigger = async (suite: string, mode: 'sync' | 'async') => {
    try {
      const { data } = await client.post('/eval/runs', { suite, mode, trigger_type: 'manual' });
      if (mode === 'async') {
        toast.success(data.note || '已投递评测任务');
        // 投递后自动轮询，不再要求用户手动刷新来确认有没有跑起来
        const latest = (data.run_id ? Number(data.run_id) : 0);
        setPending({ runId: latest, suite });
        polls.current = 0;
      } else {
        const rep = data?.report?.suites ? data.report.suites[suite] : data?.report;
        if (rep) {
          const ok = rep.release_ok !== false;
          toast[ok ? 'success' : 'error'](
            `${SUITE_LABEL[rep.suite] || rep.suite}：${rep.passed}/${rep.total} · `
            + `相对 ${rep.verdict || ''} · 绝对 ${rep.floor_verdict || ''}`,
          );
        } else {
          toast.success('评测完成');
        }
      }
      onRefreshRuns();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '触发评测失败');
    }
  };

  // async 运行进度轮询：到 done/failed 就停；超时明确告知而不是默默转圈
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(async () => {
      polls.current += 1;
      if (polls.current > POLL_MAX) {
        setPending(null);
        toast.error(`评测仍在进行或队列阻塞（已等待 ${Math.round(POLL_MAX * POLL_MS / 1000)}s），`
          + '请查看 celery-worker 日志');
        return;
      }
      try {
        const { data } = await client.get('/eval/runs', { params: { limit: 20 } });
        const rows = expectList(data, '评测运行记录');
        const mine = pending.runId
          ? rows.find((r: EvalRun) => r.id === pending.runId)
          : rows.find((r: EvalRun) => r.suite === pending.suite
                      && ['queued', 'running', 'done', 'failed'].includes(r.status));
        if (mine && ['done', 'failed'].includes(mine.status)) {
          setPending(null);
          onRefreshRuns();
          if (mine.status === 'failed') {
            toast.error(`运行 #${mine.id} 失败：${mine.error || '未给出原因'}`);
          } else {
            toast.success(
              `运行 #${mine.id}（${SUITE_LABEL[mine.suite] || mine.suite}）完成：`
              + `${mine.passed}/${mine.total}${mine.floor_ok ? '' : ' · 低于绝对下限'}`,
            );
          }
        }
      } catch (e: any) {
        setPending(null);
        toast.error(e.message || e.response?.data?.detail || '轮询运行状态失败');
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [pending, onRefreshRuns]);

  const openDetail = async (run: EvalRun) => {
    setDetailLoading(true);
    try {
      const { data } = await client.get(`/eval/runs/${run.id}/results`);
      setDetail({ run, results: enrich(run.suite, data.items || []) });
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '加载结果失败');
    } finally {
      setDetailLoading(false);
    }
  };

  const markStatus = async (r: RunResult, status: 'active' | 'known_defect' | 'retired') => {
    const c = caseIndex.get(`${r.suite}|${r.case_key}`);
    if (!c) { toast.error('该结果对应的用例已不在用例表，无法改状态'); return; }
    if (status !== 'active' && !note.trim()) {
      toast.error('标为退役/已知缺陷必须写原因');
      return;
    }
    setBusy(true);
    try {
      await client.put(`/eval/cases/${c.id}`, {
        capability: c.capability, case_status: status, status_note: note.trim(),
      });
      toast.success(status === 'retired'
        ? '已退役（不计分母，条数与原因在概览/门禁处常驻可见）'
        : status === 'known_defect' ? '已标为已知缺陷（仍计入分母，逼着修）' : '已恢复计分');
      setTriage(null); setNote('');
      onRefreshCases();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '状态更新失败');
    } finally {
      setBusy(false);
    }
  };

  const toCandidate = async (r: RunResult) => {
    const c = caseIndex.get(`${r.suite}|${r.case_key}`);
    setBusy(true);
    try {
      const dsName = String(((c?.payload || {}) as any).datasource_name || '');
      const { data } = await client.post('/evolution/candidates/from-eval-failure', {
        suite: r.suite, case_key: r.case_key, question: c?.question || r.case_key,
        capability: c?.capability || '', datasource_name: dsName,
        term: note.trim(), reason: r.reason || '',
      });
      toast.success(data.message || '已转入进化中心');
      setTriage(null); setNote('');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '转候选失败');
    } finally {
      setBusy(false);
    }
  };

  // TS 不会把 useState 变量的收窄带进事件回调（它是可变绑定），所以先拷到局部
  // const 再分支 —— 也顺便避开四个嵌套三元。
  const triageFooter = () => {
    const t = triage;
    if (!t) return null;
    if (t.kind === 'candidate') {
      return (
        <>
          <Button variant="outline" disabled={busy}
            onClick={() => markStatus(t.r, 'known_defect')}>
            同时标为已知缺陷
          </Button>
          <Button onClick={() => toCandidate(t.r)} disabled={busy}>
            {busy ? '处理中…' : '转候选'}
          </Button>
        </>
      );
    }
    return (
      <>
        <Button variant="outline" disabled={busy} onClick={() => markStatus(t.r, 'retired')}>
          退役
        </Button>
        <Button disabled={busy} onClick={() => markStatus(t.r, 'known_defect')}>
          标为已知缺陷
        </Button>
      </>
    );
  };

  const clusters = useMemo(() => {
    if (!detail) return [];
    const fails = detail.results.filter(r => !r.passed);
    const m = new Map<string, RunResult[]>();
    for (const r of fails) {
      const k = clusterKey(r.reason);
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(r);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [detail]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => trigger('all', 'async')}>
          <Play className="h-3.5 w-3.5 mr-1" />跑全部（后台）
        </Button>
        <div className="w-px h-5 bg-border" />
        {suites.map(s => (
          <Button key={s.suite} size="sm"
            variant={s.suite === 'compile' || s.suite === 'semantic' ? 'default' : 'outline'}
            title={s.scope_note}
            onClick={() => trigger(s.suite, (s.suite === 'compile' || s.suite === 'semantic') ? 'sync' : 'async')}>
            {s.label}
          </Button>
        ))}
        {pending && (
          <Badge variant="outline" className="text-[10px] animate-pulse">
            运行中… 已等 {Math.round(polls.current * POLL_MS / 1000)}s
          </Badge>
        )}
      </div>

      <div className="border rounded-md overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-muted/40 text-left">
              <th className="p-2">运行</th>
              <th className="p-2">评测层</th>
              <th className="p-2">触发</th>
              <th className="p-2">状态</th>
              <th className="p-2">通过</th>
              <th className="p-2">正确率</th>
              <th className="p-2">门禁</th>
              <th className="p-2">分母构成</th>
              <th className="p-2">完成时间</th>
              <th className="p-2">发起人</th>
              <th className="p-2 w-[80px]">操作</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr><td colSpan={11} className="p-4 text-center text-muted-foreground">
                暂无运行记录
              </td></tr>
            )}
            {runs.map(r => (
              <tr key={r.id} className="border-t">
                <td className="p-2 text-muted-foreground" title={r.run_key}>#{r.id}</td>
                <td className="p-2">{SUITE_LABEL[r.suite] || r.suite}</td>
                <td className="p-2 text-muted-foreground">{TRIGGER_LABEL[r.trigger_type] || r.trigger_type}</td>
                <td className="p-2">
                  <Badge variant={r.status === 'done' ? 'default'
                    : r.status === 'failed' ? 'destructive' : 'outline'} className="text-[9px]">
                    {r.status}
                  </Badge>
                </td>
                <td className="p-2">{r.passed}/{r.total}</td>
                <td className="p-2">{pct(r.accuracy)}</td>
                <td className="p-2">
                  {r.status !== 'done' ? '—' : (
                    <Badge variant={r.floor_ok && r.baseline_ok ? 'secondary' : 'destructive'}
                      className="text-[9px]"
                      title={`相对未回退=${!!r.baseline_ok}；达绝对下限=${!!r.floor_ok}`}>
                      {r.baseline_ok ? '' : '回退'}{r.floor_ok ? '' : `${r.baseline_ok ? '' : '+ '}低于下限`}
                      {(r.baseline_ok && r.floor_ok) ? '通过' : ''}
                    </Badge>
                  )}
                </td>
                <td className="p-2 text-muted-foreground text-[10px]">
                  活跃 {r.active_count ?? 0} · 缺陷 {r.known_defect_count ?? 0} · 退役 {r.retired_count ?? 0}
                </td>
                <td className="p-2 text-muted-foreground">{r.finished_at || '—'}</td>
                <td className="p-2 text-muted-foreground">{r.created_by || '—'}</td>
                <td className="p-2">
                  <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px]" onClick={() => openDetail(r)}>
                    结果
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 结果抽屉：不往下撑列表 */}
      {detail && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/40" onClick={() => setDetail(null)} />
          <div className="relative w-full max-w-2xl h-full bg-background border-l overflow-y-auto">
            <div className="sticky top-0 bg-background border-b px-4 py-3 flex items-center gap-2 z-10">
              <b className="text-sm">运行 #{detail.run.id}</b>
              <Badge variant="outline" className="text-[10px]">
                {SUITE_LABEL[detail.run.suite] || detail.run.suite}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {detail.run.passed}/{detail.run.total} · {pct(detail.run.accuracy)}
              </span>
              <div className="flex-1" />
              <Button variant="ghost" size="sm" className="h-7" onClick={() => setDetail(null)}>
                <X className="h-3.5 w-3.5 mr-1" />关闭
              </Button>
            </div>

            {detailLoading && <div className="p-4 text-xs text-muted-foreground">加载中…</div>}

            <div className="p-4 space-y-4">
              {detail.results.some(r => !r.passed) && (
                <section className="space-y-2">
                  <h3 className="text-xs font-semibold">
                    失败原因聚类（{detail.results.filter(r => !r.passed).length} 条 / {clusters.length} 类）
                  </h3>
                  {clusters.map(([key, list]) => (
                    <div key={key} className="border rounded-md">
                      <div className="px-2.5 py-1.5 bg-muted/30 flex items-center gap-2">
                        <Badge variant="destructive" className="text-[10px]">{list.length}</Badge>
                        <span className="text-xs font-medium">{key}</span>
                      </div>
                      <div>
                        {list.map(r => (
                          <div key={r.case_key} className="px-2.5 py-2 border-t text-xs space-y-1">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-mono text-[10px]" title={r.question}>
                                {r.case_key}
                              </span>
                              <span className="text-[10px] text-muted-foreground">
                                {capabilities.find(c => c.key === r.capability)?.label || r.capability || '契约未登记'}
                              </span>
                              {r.case_status === 'known_defect' && (
                                <Badge variant="outline" className="text-[9px] text-amber-600">已登记缺陷</Badge>
                              )}
                              <div className="flex-1" />
                              <Button size="sm" variant="outline" className="h-6 text-[11px]"
                                onClick={() => { setTriage({ kind: 'candidate', r }); setNote(''); }}>
                                转候选
                              </Button>
                              <Button size="sm" variant="outline" className="h-6 text-[11px]"
                                onClick={() => { setTriage({ kind: 'status', r }); setNote(r.reason || ''); }}>
                                标缺陷/退役
                              </Button>
                            </div>
                            <div className="text-muted-foreground">{r.question}</div>
                            {r.reason && <div className="text-destructive">{r.reason}</div>}
                            {Object.keys(r.sources || {}).length > 0 && (
                              <div className="text-[10px] text-muted-foreground">
                                {Object.entries(r.sources).map(([k, v]) => `${k}=${v}`).join(' · ')}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </section>
              )}

              <section className="space-y-1.5">
                <h3 className="text-xs font-semibold">通过项（{detail.results.filter(r => r.passed).length}）</h3>
                {detail.results.filter(r => r.passed).map(r => (
                  <div key={r.case_key} className="text-[11px] flex items-center gap-2 border-b pb-1">
                    <span className="font-mono text-[10px]">{r.case_key}</span>
                    <span className="text-muted-foreground truncate">{r.question}</span>
                    {r.score != null && <span className="text-muted-foreground">评分 {r.score}</span>}
                    <span className="ml-auto text-muted-foreground">{r.duration_ms}ms</span>
                  </div>
                ))}
              </section>
            </div>
          </div>
        </div>
      )}

      <Dialog open={!!triage} onOpenChange={(o) => { if (!o) { setTriage(null); setNote(''); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {triage?.kind === 'candidate'
                ? `转进化候选 — ${triage?.r.case_key}`
                : `变更用例状态 — ${triage?.r.case_key}`}
            </DialogTitle>
          </DialogHeader>
          {triage && (
            <div className="space-y-2 text-sm">
              <div className="text-xs text-muted-foreground">
                问句：{triage.r.question || '（用例已不在用例表）'}
              </div>
              {triage.r.reason && (
                <div className="text-xs text-destructive">失败原因：{triage.r.reason}</div>
              )}
              {triage.kind === 'candidate' ? (
                <>
                  <Label className="block">业务词（留空则用整条问句；不要填物理表/列名）</Label>
                  <Textarea rows={2} value={note} onChange={e => setNote(e.target.value)}
                    placeholder="例：企业省份 / activation_rate —— 只写业务说法" />
                  <p className="text-[11px] text-muted-foreground">
                    转候选是人工判断"这条失败反映语义资产缺口"。归纳作业不会自动扫失败：
                    拿失败文案分词猜只会造伪候选。
                  </p>
                </>
              ) : (
                <>
                  <Label className="block">原因（退役/已知缺陷必填）</Label>
                  <Textarea rows={3} value={note} onChange={e => setNote(e.target.value)}
                    placeholder="例：实测 rag_source=ontology_traversal:no_seed，关键词未被抽成图种子" />
                  <p className="text-[11px] text-muted-foreground">
                    已知缺陷继续计入分母并拖累门禁下限（逼着修）；退役不计分母但条数与原因
                    在概览常驻可见 —— 都不允许静默消失。
                  </p>
                </>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setTriage(null); setNote(''); }}>取消</Button>
            {triageFooter()}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
