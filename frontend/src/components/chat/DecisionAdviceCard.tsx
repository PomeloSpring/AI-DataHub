import { useState } from 'react';
import DecisionAdvicePanel from '@/components/decision/DecisionAdvicePanel';
import type { DecisionAdvicePayload } from '@/api/decisions';

/**
 * Chat 随分析结果出的决策建议卡。
 *
 * 卡片只是消费方：`advice` 来自服务端 done 事件的 `decision_advice`（会话回放时由
 * `chatStore.attachDecisionAdvice` 从实例表按 message_uuid 读回来），前端不判断
 * "这轮该不该给建议" —— 那是引擎的活，两处判断迟早飘开。
 *
 * 渲染规则（都是"不许把没评估演成没问题"的具体化）：
 * - `items` 空且无原因 → 整块不渲染（本轮与任何已发布树无关）；
 * - `skipped_reason` 非空 → 即使有 items 也要显示（例如部分取数结果被截断）；
 * - 默认只展开第一条建议，其余收在"再看 N 条"里：一次问数可能命中多棵树，
 *   全平铺会把分析结果本身挤出屏幕（一图一问题，ui-vis 同源纪律）；
 *   展开的那一条不折叠细节：命中路径与依据表就是这张卡的价值，抽掉它们
 *   就变成一句无法核对的结论。
 */
export default function DecisionAdviceCard({
  advice, onEvaluate,
}: {
  advice?: DecisionAdvicePayload;
  onEvaluate?: (treeKey: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const items = advice?.items || [];
  const reason = advice?.skipped_reason || '';
  if (!items.length && !reason) return null;

  const shown = expanded ? items : items.slice(0, 1);
  return (
    <section className="mt-3 space-y-2" aria-label="决策建议">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">决策建议</h3>
        <span className="text-xs text-muted-foreground">
          由已发布的决策树确定性判定，不是模型生成的措辞
        </span>
      </div>

      {shown.map((it, i) => (
        <DecisionAdvicePanel
          key={`${it.tree_key}-${i}`} advice={it}
          onEvaluate={onEvaluate && it.tree_key ? () => onEvaluate(it.tree_key!) : undefined}
        />
      ))}

      {reason && (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs"
          role="status">
          {reason}
        </p>
      )}

      {items.length > 1 && (
        <button type="button" className="text-xs text-primary hover:underline"
          onClick={() => setExpanded(e => !e)}>
          {expanded ? '收起' : `再看 ${items.length - 1} 棵树的判定`}
        </button>
      )}
    </section>
  );
}
