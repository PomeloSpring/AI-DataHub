/**
 * 前置意图漏斗徽章 — 本地直答回合必须可见（no-silent-degradation）。
 *
 * 这一条回答没有调用模型，用户必须知道两件事：
 *   1. 为什么没有模型分析（判定层级与依据，hover 可查，排查误拦用）；
 *   2. 不认可这个判定时怎么救回来（「转深度分析」把原文重新下发执行层）。
 * 只在 `handledBy === 'intent_gate'` 时渲染；走模型的回合不出现任何多余标记。
 */
import { Button } from '@/components/ui/button';

/** 服务端 done 事件回带的判定快照（与 backend intent_gate.Decision 字段对齐）。 */
export interface GateSnapshot {
  mode?: string;
  layer?: string;
  label?: string;
  reason?: string;
  signals?: string[];
  latency_ms?: number;
  gate_error?: string;
}

/** 判定依据的中文可读化：把落库的层级/标签/信号拼成一句排查提示。 */
function tipText(gate?: GateSnapshot): string {
  if (!gate) return '本条由前置意图层直接回答，未调用模型。';
  const layer = gate.layer === 'L0' ? 'L0 形态规则'
    : gate.layer === 'L1' ? 'L1 词典/本体命中' : (gate.layer || '');
  const parts = [
    gate.label ? `判定=${gate.label}` : '',
    layer,
    gate.reason || '',
    gate.signals?.length ? `信号=${gate.signals.join(',')}` : '',
    gate.gate_error ? `网关异常已放行=${gate.gate_error}` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : '本条由前置意图层直接回答，未调用模型。';
}

export function IntentGateBadge({
  gate, onDeepAnalysis, deepAnalysisDisabled,
}: {
  gate?: GateSnapshot;
  onDeepAnalysis?: () => void;
  deepAnalysisDisabled?: boolean;
}) {
  return (
    <div className="mt-2 flex items-center gap-2 flex-wrap" title={tipText(gate)}>
      <span className="text-[11px] px-2 py-0.5 rounded-full border border-border/70 bg-muted/60 text-muted-foreground">
        未调用模型 · 前置意图层直答
      </span>
      {onDeepAnalysis && (
        <Button
          variant="ghost"
          size="sm"
          className="h-6 text-xs px-2"
          disabled={deepAnalysisDisabled}
          onClick={onDeepAnalysis}
        >
          转深度分析
        </Button>
      )}
    </div>
  );
}
