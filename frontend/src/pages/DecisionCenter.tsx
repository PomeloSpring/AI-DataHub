import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { RefreshCw, ShieldAlert, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Empty } from '@/components/ui/empty';
import DecisionAdvicePanel from '@/components/decision/DecisionAdvicePanel';
import {
  decisionsApi, errorText, TREE_STATUS_LABELS, TRIGGER_LABELS, VERDICT_LABELS,
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

const VERDICT_HINT: Record<DecisionVerdict, string> = {
  hit: '按已发布口径判定为需要处置',
  no_hit: '本轮数据未触发任何建议',
  partial: '本轮取数不足以完整判定',
  skipped: '本次未评估，原因见详情',
};

export default function DecisionCenter() {
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
      toast.success(`已评估 ${data.evaluated} 棵树，命中 ${data.hit_count} 条`);
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
        <h2 className="mt-3 text-base font-medium">当前角色不可见决策建议</h2>
        <p className="mt-2 text-sm text-muted-foreground">{forbidden}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold">决策建议</h1>
          <p className="text-sm text-muted-foreground">
            按已发布的决策树判定「该做什么」；判定由确定性引擎完成，不随模型措辞变化
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setTab(tab === 'trees' ? 'history' : 'trees')}>
            {tab === 'trees' ? '看命中历史' : '看决策树清单'}
          </Button>
          <Button size="sm" onClick={evaluateAll} disabled={busy === 'all' || loading}>
            {busy === 'all' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            <span className="ml-1.5">重新评估全部</span>
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
        <section aria-label="评估详情" className="space-y-2">
          <h2 className="text-sm font-medium">评估详情</h2>
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
  if (!trees.length) {
    return (
      <Empty
        title="还没有已发布的决策树"
        description="决策树的配置与发布在「数据中台 → 决策建议」，由有配置权限的人在数据中台完成。" />
    );
  }
  return (
    <div className="overflow-hidden rounded-xl border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr className="text-left">
            <th className="px-3 py-2 font-medium">决策树</th>
            <th className="px-3 py-2 font-medium">状态</th>
            <th className="px-3 py-2 font-medium">适用站点</th>
            <th className="px-3 py-2 font-medium">最近判定</th>
            <th className="px-3 py-2 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          {trees.map(t => {
            const last = latest[t.tree_key];
            return (
              <tr key={t.tree_key} className="border-t border-border/60">
                <td className="px-3 py-2">
                  <div className="font-medium">{t.name}</div>
                  <div className="text-xs text-muted-foreground">{t.description || t.tree_key}</div>
                </td>
                <td className="px-3 py-2">
                  <span className="text-xs">{TREE_STATUS_LABELS[t.status]}</span>
                  <div className="text-xs text-muted-foreground">v{t.version}</div>
                </td>
                <td className="px-3 py-2 text-xs">
                  {t.apply_sites?.length
                    ? t.apply_sites.map(s => s.site_code).join('、')
                    : <span className="text-muted-foreground">按角色授权站点</span>}
                </td>
                <td className="px-3 py-2 text-xs">
                  {last ? (
                    <button type="button" className="text-left hover:text-primary" onClick={() => onInspect(last)}>
                      <div className="font-medium">{VERDICT_LABELS[last.verdict] || last.verdict}</div>
                      <div className="text-muted-foreground">{last.created_at} · {TRIGGER_LABELS[last.trigger_type] || last.trigger_type}</div>
                    </button>
                  ) : <span className="text-amber-700 dark:text-amber-400">从未评估（不代表无风险）</span>}
                </td>
                <td className="px-3 py-2 text-right">
                  <Button size="sm" variant="outline"
                    disabled={t.status !== 'active' || busy === t.tree_key}
                    title={t.status !== 'active' ? '草案/已下架的树不参与评估' : ''}
                    onClick={() => onEvaluate(t.tree_key)}>
                    {busy === t.tree_key ? '评估中…' : '评估'}
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
  if (!runs.length) {
    return <Empty title="还没有评估记录" description="点「重新评估全部」生成第一条。" />;
  }
  return (
    <div className="overflow-hidden rounded-xl border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr className="text-left">
            <th className="px-3 py-2 font-medium">时间</th>
            <th className="px-3 py-2 font-medium">决策树</th>
            <th className="px-3 py-2 font-medium">结论</th>
            <th className="px-3 py-2 font-medium">出口</th>
            <th className="px-3 py-2 font-medium">说明</th>
          </tr>
        </thead>
        <tbody>
          {runs.map(r => (
            <tr key={r.id} className="border-t border-border/60">
              <td className="px-3 py-2 text-xs whitespace-nowrap">{r.created_at}</td>
              <td className="px-3 py-2">
                <button type="button" className="text-left hover:text-primary" onClick={() => onInspect(r)}>
                  {r.tree_deleted ? `（已删除）${r.tree_key}` : r.tree_name}
                </button>
              </td>
              <td className="px-3 py-2 text-xs">{VERDICT_LABELS[r.verdict] || r.verdict}</td>
              <td className="px-3 py-2 text-xs">{TRIGGER_LABELS[r.trigger_type] || r.trigger_type}</td>
              <td className="px-3 py-2 text-xs text-muted-foreground">
                {r.verdict_reason || VERDICT_HINT[r.verdict] || ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
