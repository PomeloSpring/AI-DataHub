/**
 * WaitingIndicator — 等待期间的占位提示（智能问数 / AS-BOT 共用）。
 *
 * 默认只说一句**当前确实成立**的事：「正在等待模型响应」。
 * 这里原先每 2.8 秒轮换四句文案（正在查阅相关的业务语义 / 正在梳理数据与逻辑 / 答案马上就来 …），
 * 问题不在"不够活泼"，而在于每一句都像对本次处理的**事实描述**：知识问答、看板设计、闲聊
 * 根本没在查库取数，画面却写着"获取数据""梳理数据"，用户会照着这句话理解后端在做什么；
 * 「答案马上就来」更是拿不出依据的承诺。给不出依据的文案比一句朴素的等待提示更糟
 * （与「不许把失败伪装成看起来能用」同一取向）。
 *
 * 规则：
 *  - 没有阶段信号 → 只用 DEFAULT_LABEL，不轮换、不预测；
 *  - 有阶段信号（stage）→ 说阶段，但措辞不预设结果形态：「正在调用工具」而不是
 *    「正在调用工具获取数据」——工具可能是知识库检索、SQL 预检，也可能真是取数；
 *  - 调用方给了明确文案（label，例如后端进度消息、"正在停止上一轮…"）→ 优先级最高，原样显示。
 *
 * 三点动画保留：它表达"还在处理"，是状态而不是承诺。
 */

import { cn } from '@/lib/utils';

export type WaitingStage = 'thinking' | 'tools' | 'composing' | 'idle';

const STAGE_LABELS: Record<Exclude<WaitingStage, 'idle'>, string> = {
  thinking: '正在思考',
  tools: '正在调用工具',
  composing: '正在组织回答',
};

/** 还没有阶段信号时的唯一文案。不要往这里加"轮换池"：加一句就是一次无依据的预测 */
const DEFAULT_LABEL = '正在等待模型响应';

interface Props {
  /** 阶段感知：thinking/tools/composing 显示阶段文案；idle 显示通用等待文案 */
  stage?: WaitingStage;
  /** 显式文案（优先级最高，如停止中/后端阶段消息） */
  label?: string;
  size?: 'sm' | 'md';
  className?: string;
}

export default function WaitingIndicator({ stage = 'idle', label, size = 'md', className }: Props) {
  const text = label || (stage !== 'idle' ? STAGE_LABELS[stage] : DEFAULT_LABEL);

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
