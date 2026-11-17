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
import { toast } from '@/lib/toast';
import { Play, X } from 'lucide-react';
import client from '@/api/client';
import {
  SUITE_LABEL, TRIGGER_LABEL, clusterKey, expectList, pct,
  type CapabilityMeta, type EvalCase, type EvalRun, type RunResult, type SuiteMeta,
} from './shared';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

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
  const { t } = useTranslation('ui');
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
        toast.success(data.note || t('k_8dce38692a'));
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
            + tr('k_c7fb3dc4f6', { p0: rep.verdict || '', p1: rep.floor_verdict || '' }),
          );
        } else {
          toast.success(t('k_ac782dbd7c'));
        }
      }
      onRefreshRuns();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_c777cf0ba9'));
    }
  };

  // async 运行进度轮询：到 done/failed 就停；超时明确告知而不是默默转圈
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(async () => {
      polls.current += 1;
      if (polls.current > POLL_MAX) {
        setPending(null);
        toast.error(t('k_edc7e1e614', { p0: Math.round(POLL_MAX * POLL_MS / 1000) }));
        return;
      }
      try {
        const { data } = await client.get('/eval/runs', { params: { limit: 20 } });
        const rows = expectList(data, tr('k_408cd702ca'));
        const mine = pending.runId
          ? rows.find((r: EvalRun) => r.id === pending.runId)
          : rows.find((r: EvalRun) => r.suite === pending.suite
                      && ['queued', 'running', 'done', 'failed'].includes(r.status));
        if (mine && ['done', 'failed'].includes(mine.status)) {
          setPending(null);
          onRefreshRuns();
          if (mine.status === 'failed') {
            toast.error(tr('k_541866badf', { p0: mine.id, p1: mine.error || tr('k_32d298d0a3') }));
          } else {
            toast.success(
              t('k_ace6968b40', { p0: mine.id, p1: SUITE_LABEL[mine.suite] || mine.suite })
              + `${mine.passed}/${mine.total}${mine.floor_ok ? '' : tr('k_e1f9b92e6a')}`,
            );
          }
        }
      } catch (e: any) {
        setPending(null);
        toast.error(e.message || e.response?.data?.detail || t('k_7b6e930c65'));
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [pending, onRefreshRuns]);

  const openDetail = async (run: EvalRun) => {
    setDetailLoading(true);
    try {
      const { data } = await client.get(`/eval/runs/${run.id}/results`);
      setDetail({ run, results: enrich(run.suite, data.items || []) });
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_d37e6080b7'));
    } finally {
      setDetailLoading(false);
    }
  };

  const markStatus = async (r: RunResult, status: 'active' | 'known_defect' | 'retired') => {
    const c = caseIndex.get(`${r.suite}|${r.case_key}`);
    if (!c) { toast.error(t('k_1b620ee5e2')); return; }
    if (status !== 'active' && !note.trim()) {
      toast.error(t('k_07f54d99d5'));
      return;
    }
    setBusy(true);
    try {
      await client.put(`/eval/cases/${c.id}`, {
        capability: c.capability, case_status: status, status_note: note.trim(),
      });
      toast.success(status === 'retired'
        ? t('k_f1848a866d')
        : status === 'known_defect' ? t('k_652c0d91e4') : t('k_87e49398c3'));
      setTriage(null); setNote('');
      onRefreshCases();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_bdf3369bc6'));
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
      toast.success(data.message || t('k_ade8754743'));
      setTriage(null); setNote('');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_b037f93515'));
    } finally {
      setBusy(false);
    }
  };

  // TS 不会把 useState 变量的收窄带进事件回调（它是可变绑定），所以先拷到局部
  // const 再分支 —— 也顺便避开四个嵌套三元。
  const triageFooter = () => {
    const tg = triage;
    if (!tg) return null;
    if (tg.kind === 'candidate') {
      return (
        <>
          <Button variant="outline" disabled={busy}
            onClick={() => markStatus(tg.r, 'known_defect')}>
            {t('k_0056ea8368')}
          </Button>
          <Button onClick={() => toCandidate(tg.r)} disabled={busy}>
            {busy ? t('k_cf978c0252') : t('k_b3becf1ab6')}
          </Button>
        </>
      );
    }
    return (
      <>
        <Button variant="outline" disabled={busy} onClick={() => markStatus(tg.r, 'retired')}>
          {t('k_c36d0a2985')}
        </Button>
        <Button disabled={busy} onClick={() => markStatus(tg.r, 'known_defect')}>
          {t('k_0ceecdd99d')}
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
          <Play className="h-3.5 w-3.5 mr-1" />{t('k_43785d9d8f')}
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
            {t('k_6e9427fa76', { p0: Math.round(polls.current * POLL_MS / 1000) })}</Badge>
        )}
      </div>

      <div className="border rounded-md overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-muted/40 text-left">
              <th className="p-2">{t("k_0c3acd446f")}</th>
              <th className="p-2">{t("k_0e8e7b44a1")}</th>
              <th className="p-2">{t("k_27eccf9785")}</th>
              <th className="p-2">{t("k_62e951a692")}</th>
              <th className="p-2">{t("k_dcc4233255")}</th>
              <th className="p-2">{t("k_8dc159502e")}</th>
              <th className="p-2">{t("k_a0d14adcf1")}</th>
              <th className="p-2">{t("k_4384c934af")}</th>
              <th className="p-2">{t("k_754a8a2e2d")}</th>
              <th className="p-2">{t("k_89d282d2fa")}</th>
              <th className="p-2 w-[80px]">{t("k_f3ea6d345e")}</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr><td colSpan={11} className="p-4 text-center text-muted-foreground">
                {t('k_920daa51ce')}
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
                      title={t('k_79edb84237', { p0: !!r.baseline_ok, p1: !!r.floor_ok })}>
                      {r.baseline_ok ? '' : t('k_c3cb48b69a')}{r.floor_ok ? '' : t('k_fed0373ed7', { p0: r.baseline_ok ? '' : '+ ' })}
                      {(r.baseline_ok && r.floor_ok) ? t('k_dcc4233255') : ''}
                    </Badge>
                  )}
                </td>
                <td className="p-2 text-muted-foreground text-[10px]">
                  {t('k_15275dcd9b', { p0: r.active_count ?? 0, p1: r.known_defect_count ?? 0, p2: r.retired_count ?? 0 })}
                </td>
                <td className="p-2 text-muted-foreground">{r.finished_at || '—'}</td>
                <td className="p-2 text-muted-foreground">{r.created_by || '—'}</td>
                <td className="p-2">
                  <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px]" onClick={() => openDetail(r)}>
                    {t('k_0a2c91cec6')}
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
              <b className="text-sm">{t('k_2fc8740c66', { p0: detail.run.id })}</b>
              <Badge variant="outline" className="text-[10px]">
                {SUITE_LABEL[detail.run.suite] || detail.run.suite}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {detail.run.passed}/{detail.run.total} · {pct(detail.run.accuracy)}
              </span>
              <div className="flex-1" />
              <Button variant="ghost" size="sm" className="h-7" onClick={() => setDetail(null)}>
                <X className="h-3.5 w-3.5 mr-1" />{t('k_6c14bd7f6f')}
              </Button>
            </div>

            {detailLoading && <div className="p-4 text-xs text-muted-foreground">{t("k_300ee3dee4")}</div>}

            <div className="p-4 space-y-4">
              {detail.results.some(r => !r.passed) && (
                <section className="space-y-2">
                  <h3 className="text-xs font-semibold">
                    {t('k_36c924539d', { p0: detail.results.filter(r => !r.passed).length, p1: clusters.length })}</h3>
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
                                {capabilities.find(c => c.key === r.capability)?.label || r.capability || t('k_19edabbb93')}
                              </span>
                              {r.case_status === 'known_defect' && (
                                <Badge variant="outline" className="text-[9px] text-amber-600">{t("k_118cea0e7c")}</Badge>
                              )}
                              <div className="flex-1" />
                              <Button size="sm" variant="outline" className="h-6 text-[11px]"
                                onClick={() => { setTriage({ kind: 'candidate', r }); setNote(''); }}>
                                {t('k_b3becf1ab6')}
                              </Button>
                              <Button size="sm" variant="outline" className="h-6 text-[11px]"
                                onClick={() => { setTriage({ kind: 'status', r }); setNote(r.reason || ''); }}>
                                {t('k_fdf8a35641')}
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
                <h3 className="text-xs font-semibold">{t('k_9281d52405', { p0: detail.results.filter(r => r.passed).length })}</h3>
                {detail.results.filter(r => r.passed).map(r => (
                  <div key={r.case_key} className="text-[11px] flex items-center gap-2 border-b pb-1">
                    <span className="font-mono text-[10px]">{r.case_key}</span>
                    <span className="text-muted-foreground truncate">{r.question}</span>
                    {r.score != null && <span className="text-muted-foreground">{t('k_fbb9501e89', { p0: r.score })}</span>}
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
                ? t('k_57d8782cf0', { p0: triage?.r.case_key })
                : t('k_48165c5443', { p0: triage?.r.case_key })}
            </DialogTitle>
          </DialogHeader>
          {triage && (
            <div className="space-y-2 text-sm">
              <div className="text-xs text-muted-foreground">
                {tr('k_1275eb8043')}{triage.r.question || t('k_88bb233e8a')}
              </div>
              {triage.r.reason && (
                <div className="text-xs text-destructive">{t('k_dd8653f437', { p0: triage.r.reason })}</div>
              )}
              {triage.kind === 'candidate' ? (
                <>
                  <Label className="block">{t("k_e03d438b86")}</Label>
                  <Textarea rows={2} value={note} onChange={e => setNote(e.target.value)}
                    placeholder={t('k_9d90351ef8')} />
                  <p className="text-[11px] text-muted-foreground">
                    {t('k_85f2cbab98')}
                  </p>
                </>
              ) : (
                <>
                  <Label className="block">{t("k_de9a5d4387")}</Label>
                  <Textarea rows={3} value={note} onChange={e => setNote(e.target.value)}
                    placeholder={t('k_1195daa372')} />
                  <p className="text-[11px] text-muted-foreground">
                    {t('k_931726a27c')}
                  </p>
                </>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setTriage(null); setNote(''); }}>{t("k_4d0b4688c7")}</Button>
            {triageFooter()}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
