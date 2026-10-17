/**
 * BotAvatar — AS-BOT/助手头像(美化基准):渐变圆底 + 柔和光晕 + 可选"工作中"状态点。
 *
 * 全平台助手头像统一用它(Chat 消息流、AS-BOT 面板/头部/空态),不再各自画裸 Bot 图标圆底:
 *  - 渐变底(primary → indigo)+ 内描边高光 + 柔和投影;
 *  - active(流式输出中):外圈光晕呼吸 + 右下角绿色状态点(带 ping 扩散)。
 */

import { Bot } from 'lucide-react';
import { cn } from '@/lib/utils';

interface Props {
  /** 直径(px) */
  size?: number;
  /** 工作中(流式输出)时点亮光晕与状态点 */
  active?: boolean;
  className?: string;
}

export default function BotAvatar({ size = 32, active = false, className }: Props) {
  const iconSize = Math.round(size * 0.5);
  const dotSize = Math.max(8, Math.round(size * 0.26));
  return (
    <div
      className={cn('relative inline-flex shrink-0 items-center justify-center', className)}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {/* 外圈光晕:工作中呼吸扩散 */}
      <span
        className={cn('absolute -inset-1 rounded-full bot-avatar-glow', active && 'bot-avatar-glow-active')}
      />
      {/* 渐变圆底 + 内描边高光 */}
      <span className="absolute inset-0 rounded-full bg-gradient-to-br from-primary via-primary/90 to-indigo-500 dark:to-indigo-400 shadow-md ring-1 ring-white/25 flex items-center justify-center">
        <Bot className="text-white drop-shadow-sm" style={{ width: iconSize, height: iconSize }} />
      </span>
      {/* 状态点:工作中亮绿点(右下角,ping 扩散) */}
      {active && (
        <span
          className="absolute bottom-0 right-0 rounded-full bg-emerald-400 border-2 border-background bot-avatar-status"
          style={{ width: dotSize, height: dotSize }}
        />
      )}
    </div>
  );
}
