import { Link } from 'react-router-dom';
import { Badge } from '@/components/ui/badge';
import {
  SEVERITY_LABELS, TREE_STATUS_LABELS, VERDICT_LABELS,
  type AdviceSeverity, type DecisionOutcomeView, type DecisionVerdict, type TreeStatus,
} from '@/api/decisions';

/**
 * 决策建议的统一渲染件（Chat 卡片 / 决策中心 / 看板控件共用一份）。
 *
 * 为什么必须共用：这三个出口的差别只在"什么时候算"，结论形状是同一个
 * `DecisionOutcomeView`（后端 `service.project_run` 是唯一投影口）。各写一套就会
 * 出现"Chat 里说没命中、页面里说命中"的双口径。
 *
 * 一条硬规矩：`partial` / `skipped` 必须把 reason 显示出来。把"没评估"渲染成
 * "没有风险"是这个功能最坏的失败方式（no-silent-degradation §1）。
 */

export type VerdictTone = 'hit' | 'warn' | 'muted' | 'neutral';

export const VERDICT_TONES: Record<DecisionVerdict, VerdictTone> = {
  hit: 'hit', partial: 'warn', skipped: 'warn', no_hit: 'neutral',
};

const TONE_CLASS: Record<VerdictTone, string> = {
  hit: 'border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400',
  warn: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400',
  muted: 'border-border bg-muted text-muted-foreground',
  neutral: 'border-border bg-muted/40 text-muted-foreground',
};

export const SEVERITY_CLASS: Record<AdviceSeverity, string> = {
  low: 'bg-slate-500/15 text-slate-600 dark:text-slate-300',
  medium: 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
  high: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  critical: 'bg-red-500/15 text-red-700 dark:text-red-300',
};

export function VerdictBadge({ verdict }: { verdict: DecisionVerdict }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${TONE_CLASS[VERDICT_TONES[verdict] || 'muted']}`}>
      {VERDICT_LABELS[verdict] || verdict}
    </span>
  );
}

export function StatusBadge({ status }: { status: TreeStatus }) {
  const tone = status === 'active'
    ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
    : status === 'draft'
      ? 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
      : 'bg-slate-500/15 text-slate-600 dark:text-slate-300';
  return <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ${tone}`}>{TREE_STATUS_LABELS[status] || status}</span>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const key = (SEVERITY_LABELS as Record<string, string>)[severity] ? severity as AdviceSeverity : 'medium';
  return <span className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium ${SEVERITY_CLASS[key]}`}>{SEVERITY_LABELS[key]}</span>;
}

/** 命中路径面包屑：节点 label 序列（引擎按 label 给，物理名在这里根本没有机会出现） */
export function HitPathCrumbs({ path }: { path: string[] }) {
  if (!path?.length) return null;
  return (
    <ol className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground" aria-label="命中路径">
      {path.map((p, i) => (
        <li key={`${p}-${i}`} className="flex items-center gap-1">
          {i > 0 && <span aria-hidden>→</span>}
          <span className={i === path.length - 1 ? 'font-medium text-foreground' : ''}>{p}</span>
        </li>
      ))}
    </ol>
  );
}

/** 依据表：指标业务名 + 实测值 vs 阈值（后端已脱敏，这里不加任何物理层兜底渲染） */
export function EvidenceTable({ evidence }: { evidence: DecisionOutcomeView['evidence'] }) {
  if (!evidence?.length) return null;
  return (
    <table className="w-full text-xs" aria-label="判定依据">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="py-1 pr-3 font-medium">指标</th>
          <th className="py-1 pr-3 font-medium">判定</th>
          <th className="py-1 pr-3 font-medium">阈值</th>
          <th className="py-1 font-medium">实测值</th>
        </tr>
      </thead>
      <tbody>
        {evidence.map((e, i) => (
          <tr key={`${e.metric}-${i}`} className="border-t border-border/60">
            <td className="py-1 pr-3">{e.metric}</td>
            <td className="py-1 pr-3 tabular-nums">{e.comparator}</td>
            <td className="py-1 pr-3 tabular-nums">
              {formatValue(e.threshold)}{e.unit ? ` ${e.unit}` : ''}
            </td>
            <td className={`py-1 tabular-nums ${e.judged === false ? 'text-amber-700 dark:text-amber-400' : 'font-medium'}`}>
              {e.judged === false ? '未判定' : formatValue(e.measured)}
              {e.unit && e.judged !== false ? ` ${e.unit}` : ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function formatValue(v: any): string {
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) return v.map(formatValue).join(' ~ ');
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(6)));
  return String(v);
}

/** 建议正文 + 知识出处（kb_refs 指向内容中心已发布文章的 slug） */
export function AdviceList({ advice }: { advice: DecisionOutcomeView['advice'] }) {
  if (!advice?.length) return null;
  return (
    <ul className="space-y-2">
      {advice.map((a, i) => (
        <li key={`${a.advice_key}-${i}`} className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-start gap-2">
            <SeverityBadge severity={a.severity} />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{a.action || '（建议文案缺失）'}</div>
              {a.rationale && <div className="mt-1 text-xs text-muted-foreground">{a.rationale}</div>}
              {a.note && <div className="mt-1 text-xs text-amber-700 dark:text-amber-400">{a.note}</div>}
            </div>
          </div>
          {!!a.kb_refs?.length && (
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              {a.kb_refs.map((ref, j) => {
                const slug = typeof ref === 'string' ? ref : String((ref as any)?.slug || '');
                if (!slug) return null;
                return (
                  <Link key={`${slug}-${j}`} to={`/content/preview/${slug}`}
                    className="text-primary hover:underline">
                    依据知识：{slug}
                  </Link>
                );
              })}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * 一条完整的建议结论。
 * `onEvaluate` 存在时渲染「完整评估」按钮：turn 模式下缺指标（partial）必须由人
 * 显式发起补取数，不能由卡片自己静默再打一次库（那会把首答拖慢还没人说清）。
 */
export default function DecisionAdvicePanel({
  advice, onEvaluate, evaluating, compact,
}: {
  advice: DecisionOutcomeView;
  onEvaluate?: () => void;
  evaluating?: boolean;
  compact?: boolean;
}) {
  const verdict = advice.verdict;
  // 口径说明在 hit 上也要显示：看板控件带着日期区间/下拉算出的建议，如果只在
  // 非 hit 时才显 reason，人就会把“已改口径的结论”当全量结论读（双口径）。
  // 非 hit 时 reason 里已带上同一批说明（服务端落实例时附进去的），只显一条不重复。
  const scopeNotes = advice.scope_notes || [];
  const showReason = verdict !== 'hit' && !!advice.reason;
  return (
    <div className={`space-y-3 rounded-xl border ${TONE_CLASS[VERDICT_TONES[verdict] || 'muted'].split(' ').filter(c => c.startsWith('border-')).join(' ') || 'border-border'} bg-card p-3`}>
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge verdict={verdict} />
        <span className="text-sm font-medium">{advice.tree_name || advice.tree_key}</span>
        <span className="text-xs text-muted-foreground">v{advice.tree_version}</span>
        {advice.site_code && <Badge variant="outline" className="text-xs">{advice.site_code}</Badge>}
        {!!onEvaluate && (
          <button type="button" onClick={onEvaluate} disabled={evaluating}
            className="ml-auto h-7 rounded-md border border-border px-2 text-xs hover:bg-muted disabled:opacity-60">
            {evaluating ? '评估中…' : verdict === 'partial' ? '完整评估' : '重新评估'}
          </button>
        )}
      </div>

      {!compact && <HitPathCrumbs path={advice.hit_path || []} />}

      {!!scopeNotes.length && verdict === 'hit' && (
        <p className="text-xs leading-relaxed text-muted-foreground" role="status">
          {scopeNotes.join('；')}
        </p>
      )}

      {showReason && (
        <p className="text-xs leading-relaxed" role="status">
          {advice.reason}
        </p>
      )}

      {!!advice.missing_metrics?.length && (
        <p className="text-xs text-muted-foreground">
          本树还需指标：{advice.missing_metrics.join('、')}（本轮未取到，未做完整判定）
        </p>
      )}

      {!!advice.unresolved?.length && (
        <p className="text-xs text-muted-foreground">
          {advice.unresolved.map((u, i) => (
            <span key={i} className="mr-2">
              「{u.term}」无法确认{u.candidates?.length ? `，候选：${u.candidates.join('、')}` : ''}
            </span>
          ))}
        </p>
      )}

      <AdviceList advice={advice.advice || []} />
      {!compact && <EvidenceTable evidence={advice.evidence || []} />}
    </div>
  );
}
