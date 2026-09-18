/**
 * ProcessTimeline — 按真实发生顺序穿插展示"思考"与"工具调用"。
 *
 * 数据源是 message.process(流式期间构建的有序片段):思考块与工具调用按
 * 到达顺序交替出现,还原模型的推理轨迹,而不是把"所有思考"和"所有工具"
 * 分成两坨。历史消息/无 process 时,回落到 thinking 块 + ToolCallTimeline
 * 的旧式分块展示,保证向后兼容。
 */

import { useMemo } from 'react';
import ThinkingBlock from './ThinkingBlock';
import ToolCallTimeline, { SingleToolItem } from './ToolCallTimeline';
import type { ProcessSegment, ToolCall, ProgressStage } from '../stores/chatStore';

interface Props {
  process?: ProcessSegment[];
  toolCalls?: ToolCall[];
  progressStages?: ProgressStage[];
  thinking?: string;
  isStreaming?: boolean;
}

export default function ProcessTimeline({
  process, toolCalls, progressStages, thinking, isStreaming = false,
}: Props) {
  const byId = useMemo(() => {
    const m = new Map<string, ToolCall>();
    (toolCalls || []).forEach(t => { if (t.tool_call_id) m.set(t.tool_call_id, t); });
    return m;
  }, [toolCalls]);

  const byStep = useMemo(() => {
    const m = new Map<number, ToolCall>();
    (toolCalls || []).forEach(t => { if (t.step != null) m.set(t.step, t); });
    return m;
  }, [toolCalls]);

  // 无有序时间线 → 回落到旧式分块展示(思考块 + 工具时间线)
  if (!process || process.length === 0) {
    return (
      <>
        {thinking && (
          <ThinkingBlock content={thinking} isStreaming={isStreaming} />
        )}
        <ToolCallTimeline
          toolCalls={toolCalls}
          progressStages={progressStages}
          isStreaming={isStreaming}
        />
      </>
    );
  }

  const lastIdx = process.length - 1;

  return (
    <div className="mb-3">
      {process.map((seg, i) => {
        if (seg.kind === 'thinking') {
          if (!seg.text) return null;
          return (
            <ThinkingBlock
              key={`t-${i}`}
              content={seg.text}
              isStreaming={isStreaming && i === lastIdx}
            />
          );
        }
        // tool 段:按 tool_call_id 优先、step 兜底定位到具体调用
        const tc =
          (seg.tool_call_id && byId.get(seg.tool_call_id)) ||
          (seg.step != null ? byStep.get(seg.step) : undefined);
        if (!tc) return null;
        return <SingleToolItem key={`c-${seg.tool_call_id || tc.step || i}`} tc={{ ...tc, _index: i }} />;
      })}
    </div>
  );
}
