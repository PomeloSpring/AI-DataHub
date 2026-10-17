/**
 * WaitingIndicator — 等待模型响应的人性化提示。
 *
 * 取代干巴巴的"等待模型响应..."+转圈:
 *  - 三点弹跳打字动画(而非机械 spinner);
 *  - 有明确阶段时显示阶段文案(思考/调用工具/组织回答);
 *  - 无明确阶段时轮换友好文案,让等待过程"有声音"。
 */

import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

export type WaitingStage = 'thinking' | 'tools' | 'composing' | 'idle';

const STAGE_LABELS: Record<Exclude<WaitingStage, 'idle'>, string> = {
  thinking: '正在深入思考',
  tools: '正在调用工具获取数据',
  composing: '正在组织回答',
};

// 无明确阶段时轮换的友好文案(让等待不那么干巴巴)
const IDLE_LABELS = [
  '正在理解你的问题',
  '正在查阅相关的业务语义',
  '正在梳理数据与逻辑',
  '答案马上就来',
];

interface Props {
  /** 阶段感知:thinking/tools/composing 显示阶段文案;idle 轮换友好文案 */
  stage?: WaitingStage;
  /** 显式文案(优先级最高,如停止中/后端阶段消息) */
  label?: string;
  size?: 'sm' | 'md';
  className?: string;
}

export default function WaitingIndicator({ stage = 'idle', label, size = 'md', className }: Props) {
  const [tick, setTick] = useState(0);

  // 仅无明确阶段且无显式文案时轮换
  useEffect(() => {
    if (stage !== 'idle' || label) return;
    const timer = setInterval(() => setTick(v => v + 1), 2800);
    return () => clearInterval(timer);
  }, [stage, label]);

  const text = label
    || (stage !== 'idle' ? STAGE_LABELS[stage] : '')
    || IDLE_LABELS[tick % IDLE_LABELS.length];

  return (
    <div className={cn('inline-flex items-center gap-2', className)}>
      <span className="flex items-center gap-1 text-primary" aria-hidden>
        <span className="typing-dot" />
        <span className="typing-dot" />
        <span className="typing-dot" />
      </span>
      <span role="status" className={size === 'sm' ? 'text-xs text-muted-foreground' : 'text-sm text-muted-foreground'}>
        {text}
      </span>
    </div>
  );
}
