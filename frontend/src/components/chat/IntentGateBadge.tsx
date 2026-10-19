/**
 * 前置意图漏斗的**唯一**界面痕迹：一个「转深度分析」动作按钮。
 *
 * 聊天界面不显示"未调用模型/前置意图层直答"这类内部术语——对用户来说这就是一条正常回答，
 * 标注它只是噪声。不显示不等于不可审计：每一回合的处理方都落在
 * `adh_llm_traces.handled_by` 与 `adh_intent_intercepts`（管理端「LLM 交互可观测 →
 * 意图漏斗」页可查拦截率与误拦复盘），服务端判定链路始终可复盘。
 *
 * 保留的是**逃生门**：判错了（该走模型的被拦了），用户点一下就把原文重新下发执行层。
 * 只在 `handledBy === 'intent_gate'` 时渲染；走模型的回合不出现任何多余元素。
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

export function IntentGateBadge({
  onDeepAnalysis, deepAnalysisDisabled,
}: {
  gate?: GateSnapshot;
  onDeepAnalysis?: () => void;
  deepAnalysisDisabled?: boolean;
}) {
  if (!onDeepAnalysis) return null;
  return (
    <div className="mt-2 flex items-center gap-2">
      <Button
        variant="ghost"
        size="sm"
        className="h-6 text-xs px-2"
        disabled={deepAnalysisDisabled}
        onClick={onDeepAnalysis}
        title="让模型重新分析这条问题"
      >
        转深度分析
      </Button>
    </div>
  );
}
