import { ArrowDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

/**
 * 「回到底部」悬浮按钮 —— 上翻查看历史时的回程入口（Chat 与 AS-BOT 面板共用）。
 *
 * 为什么需要它：贴底判定修好后，用户在生成中上翻就不会再被拽回底部（这是对的），
 * 但聊天软件的另外半边是**给他一条明确的回程**：不滚回底部就一直看不到新内容，
 * 而"要不要回去"该由用户决定，不是由自动滚动替他决定。
 *
 * 只在离开底部时渲染（`atBottom` 来自 useStickToBottom，判定口径不在这写第二套）；
 * 生成中（loading）文案换成「有新内容」并给箭头一点脉冲，把"下面在长东西"这件事
 * 说出口——否则按钮只是普通滚动条提示，看不出内容还在变。
 *
 * 配色全部走主题变量（bg-card/border-border/text-foreground），不写裸 hex：
 * 九套主题下都要成立（见 .qoder/rules/ui-vis-library-style.md）。
 */
interface Props {
  /** 是否贴底：贴底就不渲染 */
  atBottom: boolean;
  /** 本轮是否还在生成（决定文案与脉冲） */
  loading?: boolean;
  /** 点击：滚到底部并恢复自动跟随（由调用方的 useStickToBottom 提供） */
  onScrollToBottom: () => void;
  className?: string;
}

export default function BackToBottomButton({ atBottom, loading = false, onScrollToBottom, className }: Props) {
  const { t } = useTranslation(['chat', 'common']);
  if (atBottom) return null;
  return (
    <div className={cn('pointer-events-none absolute inset-x-0 bottom-4 flex justify-end', className)}>
      <button
        type="button"
        onClick={onScrollToBottom}
        aria-label={loading ? t('chat:page.backToBottomNew') : t('chat:page.backToBottom')}
        className="pointer-events-auto inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full border border-border bg-card px-3 text-xs text-foreground shadow-md transition-colors duration-200 hover:bg-accent hover:text-accent-foreground"
      >
        <span>{loading ? t('chat:page.backToBottomNew') : t('chat:page.backToBottom')}</span>
        <ArrowDown className={cn('h-3.5 w-3.5', loading && 'animate-bounce')} />
      </button>
    </div>
  );
}
