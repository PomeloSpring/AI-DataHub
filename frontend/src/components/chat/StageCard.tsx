/**
 * StageCard / SlideCollapse — chat 回答输出的阶段卡片与滑动展开基础件。
 *
 * 统一"阶段性内容"的卡片语言:
 *  - StageCard: 图标 + 标题 + 状态 + 摘要 + 右侧动作的卡片外壳, 可折叠;
 *  - SlideCollapse: 平滑滑动展开/收起容器(CSS grid-rows 过渡, 不用 JS 测高),
 *    用于思考/工具调用/检索详情等阶段性内容的"滑动输出"展示;
 *  - animate-stage-in: 阶段卡片滑入入场(流式过程中逐个阶段滑入)。
 */

import { useState, type ReactNode } from 'react';
import { ChevronDown, Loader2, CheckCircle2, AlertCircle, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

// ── 滑动展开/收起容器 ──────────────────────────────────────────

export function SlideCollapse({
  open,
  children,
  className,
}: {
  open: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('slide-collapse', open && 'slide-collapse-open', className)}>
      <div className="slide-collapse-inner">{children}</div>
    </div>
  );
}

// ── 阶段卡片外壳 ───────────────────────────────────────────────

export type StageStatus = 'running' | 'done' | 'error' | 'info';

const TONE_STYLES: Record<string, { card: string; icon: string }> = {
  default: { card: 'border-border bg-card', icon: 'text-primary bg-primary/10' },
  purple: {
    card: 'border-purple-200 dark:border-purple-800/40 bg-purple-50/50 dark:bg-purple-950/20',
    icon: 'text-purple-500 bg-purple-500/10',
  },
  green: { card: 'border-green-200 dark:border-green-900/40 bg-green-50/40 dark:bg-green-950/20', icon: 'text-green-600 bg-green-500/10' },
  destructive: {
    card: 'border-destructive/40 bg-destructive/5',
    icon: 'text-destructive bg-destructive/10',
  },
};

function StatusIcon({ status }: { status?: StageStatus }) {
  if (status === 'running') return <Loader2 className="h-3 w-3 animate-spin text-primary" />;
  if (status === 'done') return <CheckCircle2 className="h-3 w-3 text-green-500" />;
  if (status === 'error') return <AlertCircle className="h-3 w-3 text-destructive" />;
  return null;
}

export interface StageCardProps {
  icon?: LucideIcon;
  title: string;
  status?: StageStatus;
  /** 标题行右侧的摘要文字(截断显示) */
  summary?: ReactNode;
  /** 标题行右侧的徽标/chips(在摘要之前) */
  badge?: ReactNode;
  /** 标题行最右的自定义动作按钮(如"全屏查看"), 不参与折叠切换 */
  extra?: ReactNode;
  tone?: keyof typeof TONE_STYLES;
  /** 可折叠时点击标题行滑动展开/收起 */
  collapsible?: boolean;
  defaultOpen?: boolean;
  /** 受控展开态(与 defaultOpen 二选一) */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 阶段卡片滑入入场动画 */
  animate?: boolean;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
}

export default function StageCard({
  icon: Icon,
  title,
  status,
  summary,
  badge,
  extra,
  tone = 'default',
  collapsible = false,
  defaultOpen = false,
  open: openProp,
  onOpenChange,
  animate = true,
  className,
  bodyClassName,
  children,
}: StageCardProps) {
  const [selfOpen, setSelfOpen] = useState(defaultOpen);
  const open = openProp !== undefined ? openProp : selfOpen;
  const toneStyle = TONE_STYLES[tone] || TONE_STYLES.default;

  const toggle = () => {
    const next = !open;
    if (openProp === undefined) setSelfOpen(next);
    onOpenChange?.(next);
  };

  return (
    <div
      className={cn(
        'rounded-xl border shadow-sm overflow-hidden',
        toneStyle.card,
        animate && 'animate-stage-in',
        className,
      )}
    >
      <div
        className={cn(
          'flex items-center gap-2 px-3 py-2',
          collapsible && 'cursor-pointer select-none hover:bg-muted/40 transition-colors',
        )}
        onClick={collapsible ? toggle : undefined}
      >
        {Icon && (
          <span className={cn('w-6 h-6 rounded-md flex items-center justify-center shrink-0', toneStyle.icon)}>
            <Icon className="h-3.5 w-3.5" />
          </span>
        )}
        <span className="text-xs font-medium text-foreground shrink-0">{title}</span>
        {status && <StatusIcon status={status} />}
        {badge}
        {summary !== undefined && summary !== null && (
          <span className="text-[11px] text-muted-foreground truncate flex-1 min-w-0">{summary}</span>
        )}
        {!summary && <span className="flex-1 min-w-0" />}
        {extra && (
          <span className="shrink-0 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
            {extra}
          </span>
        )}
        {collapsible && (
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 text-muted-foreground shrink-0 transition-transform duration-300',
              open && 'rotate-180',
            )}
          />
        )}
      </div>
      {children !== undefined && children !== null && (
        <SlideCollapse open={open || !collapsible}>
          <div className={cn('px-3 pb-3', bodyClassName)}>{children}</div>
        </SlideCollapse>
      )}
    </div>
  );
}
