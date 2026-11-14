import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import { RefreshCw, ShieldAlert, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Empty } from '@/components/ui/empty';
import DecisionAdvicePanel from '@/components/decision/DecisionAdvicePanel';
import {
  decisionsApi, errorText, treeStatusLabel, triggerLabel, verdictLabel,
  type DecisionOutcomeView, type DecisionRunRow, type DecisionTreeRow, type DecisionVerdict,
} from '@/api/decisions';
import { toast } from '@/lib/toast';

/**
 * 工作空间「决策建议」消费页（路由 /ws/:workspaceId/decisions ↔ 菜单 workspace:decisions）。
 *
 * 这一页只做消费：看有哪些已发布的树、上次判成什么、按需手动重算。**编辑与发布在
 * 数据中台**（/data/decisions ↔ data:decisions），因为决策树的条件引用指标/维度字典，
 * 本质是口径资产（2026-11-12 拍板）。这里给编辑按钮就是第二个配置面。
 *
 * 无权限时不在前端猜：直接显示服务端 403 的裁决结果（权限口径唯一）。
 */

type Tab = 'trees' | 'history';

// 结论兑底说明只存 key，文案渲染期 t() 解析（服务端 verdict_reason 优先）
const VERDICT_HINT_KEYS: Record<DecisionVerdict, string> = {
  hit: 'decision.verdictHint.hit',
  no_hit: 'decision.verdictHint.no_hit',
  partial: 'decision.verdictHint.partial',
  skipped: 'decision.verdictHint.skipped',
};

export default function DecisionCenter() {
  const { t } = useTranslation(['workspace', 'common']);
  const { workspaceId } = useParams();
  const ws = Number(workspaceId || 0);
  const [tab, setTab] = useState<Tab>('trees');
  const [trees, setTrees] = useState<DecisionTreeRow[]>([]);
  const [runs, setRuns] = useState<DecisionRunRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState('');
  const [busy, setBusy] = useState('');
  const [detail, setDetail] = useState<DecisionOutcomeView | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [t, r] = await Promise.all([
        decisionsApi.listTrees({ workspace_id: ws }),
        decisionsApi.listRuns({ workspace_id: ws, size: 100 }),
      ]);
      setTrees(t.data.items || []);
      setRuns(r.data.items || []);
      setForbidden('');
    } catch (err: any) {
      if (err?.response?.status === 403) setForbidden(errorText(err));
      else toast.error(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [ws]);

  useEffect(() => { load(); }, [load]);

  // 每棵树的最近一次实例（清单的「最近命中」列）：按 created_at 倒序的首条，
  // 没有实例就是「从未评估」，绝不能显示成「没有风险」。
  const latestByTree = useMemo(() => {
    const map: Record<string, DecisionRunRow> = {};
    for (const r of runs) if (!map[r.tree_key]) map[r.tree_key] = r;
    return map;
  }, [runs]);

  const runToOutcome = (r: DecisionRunRow): DecisionOutcomeView => ({
    tree_key: r.tree_key, tree_name: r.tree_name, tree_version: r.tree_version,
    verdict: r.verdict, reason: r.verdict_reason, hit_path: r.hit_path || [],
    advice: r.advice_snapshot || [], evidence: r.evidence || [],
    site_code: r.site_code, created_at: r.created_at, run_id: r.id,
  });

  const evaluateAll = async () => {
    setBusy('all');
    try {
      const { data } = await decisionsApi.evaluateAll(ws);
      toast.success(t('decision.evaluatedToast', { trees: data.evaluated, hits: data.hit_count }));
      (data.errors || []).forEach((e: any) => e?.reason && toast.error(e.reason));
      await load();
    } catch (err: any) {
      toast.error(errorText(err));
    } finally {
      setBusy('');
    }
  };

  const evaluateOne = async (treeKey: string) => {
    setBusy(treeKey);
    try {
      const { data } = await decisionsApi.evaluateOne(treeKey, ws);
      setDetail(data);
      await load();
    } catch (err: any) {
      toast.error(errorText(err));
    } finally {
      setBusy('');
    }
  };

  if (forbidden) {
    return (
      <div className="mx-auto mt-16 max-w-lg rounded-xl border border-border bg-card p-6 text-center">
        <ShieldAlert className="mx-auto h-8 w-8 text-amber-500" />
        <h2 className="mt-3 text-base font-medium">{t('decision.forbiddenTitle')}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{forbidden}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold">{t('decision.title')}</h1>
          <p className="text-sm text-muted-foreground">
            {t('decision.subtitle')}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setTab(tab === 'trees' ? 'history' : 'trees')}>
            {tab === 'trees' ? t('decision.viewHistory') : t('decision.viewTrees')}
          </Button>
          <Button size="sm" onClick={evaluateAll} disabled={busy === 'all' || loading}>
            {busy === 'all' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            <span className="ml-1.5">{t('decision.evaluateAll')}</span>
          </Button>
        </div>
      </header>

      {loading ? (
        <div className="space-y-2">{[0, 1, 2].map(i => <div key={i} className="h-16 animate-pulse rounded-lg bg-muted/50" />)}</div>
      ) : tab === 'trees' ? (
        <TreeTable
          trees={trees} latest={latestByTree} busy={busy}
          onEvaluate={evaluateOne}
          onInspect={(r) => setDetail(runToOutcome(r))}
        />
      ) : (
        <HistoryTable runs={runs} onInspect={(r) => setDetail(runToOutcome(r))} />
      )}

      {detail && (
        <section aria-label={t('decision.detailTitle')} className="space-y-2">
          <h2 className="text-sm font-medium">{t('decision.detailTitle')}</h2>
          <DecisionAdvicePanel
            advice={detail}
            onEvaluate={detail.tree_key ? () => evaluateOne(detail.tree_key!) : undefined}
            evaluating={busy === detail.tree_key}
          />
        </section>
      )}
    </div>
  );
}

function TreeTable({
  trees, latest, busy, onEvaluate, onInspect,
}: {
  trees: DecisionTreeRow[];
  latest: Record<string, DecisionRunRow>;
  busy: string;
  onEvaluate: (treeKey: string) => void;
  onInspect: (run: DecisionRunRow) => void;
}) {
  const { t } = useTranslation(['workspace', 'common']);
  if (!trees.length) {
    return (
      <Empty
        title={t('decision.treesEmpty')}
        description={t('decision.treesEmptyDesc')} />
    );
  }
  return (
    <div className="overflow-hidden rounded-xl border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr className="text-left">
            <th className="px-3 py-2 font-medium">{t('decision.col.tree')}</th>
            <th className="px-3 py-2 font-medium">{t('common:label.status')}</th>
            <th className="px-3 py-2 font-medium">{t('decision.col.sites')}</th>
            <th className="px-3 py-2 font-medium">{t('decision.col.lastVerdict')}</th>
            <th className="px-3 py-2 text-right font-medium">{t('common:label.actions')}</th>
          </tr>
        </thead>
        <tbody>
          {trees.map(tr => {
            const last = latest[tr.tree_key];
            return (
              <tr key={tr.tree_key} className="border-t border-border/60">
                <td className="px-3 py-2">
                  <div className="font-medium">{tr.name}</div>
                  <div className="text-xs text-muted-foreground">{tr.description || tr.tree_key}</div>
                </td>
                <td className="px-3 py-2">
                  <span className="text-xs">{treeStatusLabel(tr.status)}</span>
                  <div className="text-xs text-muted-foreground">v{tr.version}</div>
                </td>
                <td className="px-3 py-2 text-xs">
                  {tr.apply_sites?.length
                    ? tr.apply_sites.map(s => s.site_code).join(t('decision.siteSeparator'))
                    : <span className="text-muted-foreground">{t('decision.sitesByRole')}</span>}
                </td>
                <td className="px-3 py-2 text-xs">
                  {last ? (
                    <button type="button" className="text-left hover:text-primary" onClick={() => onInspect(last)}>
                      <div className="font-medium">{verdictLabel(last.verdict)}</div>
                      <div className="text-muted-foreground">{last.created_at} · {triggerLabel(last.trigger_type)}</div>
                    </button>
                  ) : <span className="text-amber-700 dark:text-amber-400">{t('decision.neverEvaluated')}</span>}
                </td>
                <td className="px-3 py-2 text-right">
                  <Button size="sm" variant="outline"
                    disabled={tr.status !== 'active' || busy === tr.tree_key}
                    title={tr.status !== 'active' ? t('decision.inactiveTitle') : ''}
                    onClick={() => onEvaluate(tr.tree_key)}>
                    {busy === tr.tree_key ? t('decision.evaluating') : t('decision.evaluate')}
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function HistoryTable({ runs, onInspect }: { runs: DecisionRunRow[]; onInspect: (r: DecisionRunRow) => void }) {
  const { t } = useTranslation(['workspace']);
  if (!runs.length) {
    return <Empty title={t('decision.historyEmpty')} description={t('decision.historyEmptyDesc')} />;
  }
  return (
    <div className="overflow-hidden rounded-xl border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr className="text-left">
            <th className="px-3 py-2 font-medium">{t('decision.hcol.time')}</th>
            <th className="px-3 py-2 font-medium">{t('decision.hcol.tree')}</th>
            <th className="px-3 py-2 font-medium">{t('decision.hcol.verdict')}</th>
            <th className="px-3 py-2 font-medium">{t('decision.hcol.trigger')}</th>
            <th className="px-3 py-2 font-medium">{t('decision.hcol.reason')}</th>
          </tr>
        </thead>
        <tbody>
          {runs.map(r => (
            <tr key={r.id} className="border-t border-border/60">
              <td className="px-3 py-2 text-xs whitespace-nowrap">{r.created_at}</td>
              <td className="px-3 py-2">
                <button type="button" className="text-left hover:text-primary" onClick={() => onInspect(r)}>
                  {r.tree_deleted ? t('decision.deletedTree', { key: r.tree_key }) : r.tree_name}
                </button>
              </td>
              <td className="px-3 py-2 text-xs">{verdictLabel(r.verdict)}</td>
              <td className="px-3 py-2 text-xs">{triggerLabel(r.trigger_type)}</td>
              <td className="px-3 py-2 text-xs text-muted-foreground">
                {r.verdict_reason || (VERDICT_HINT_KEYS[r.verdict] ? t(VERDICT_HINT_KEYS[r.verdict] as 'workspace:decision.verdictHint.hit') : '')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
