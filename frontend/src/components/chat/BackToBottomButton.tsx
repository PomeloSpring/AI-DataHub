import { ArrowDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/**
 * 「回到底部」悬浮按钮 —— 上翻查看历史时的回程入口（Chat 与 AS-BOT 面板共用）。
 *
 * 为什么需要它：贴底判定修好后，用户在生成中上翻就不会再被拽回底部（这是对的），
 * 但聊天软件的另外半边是**给他一条明确的回程**：不滚回底部就一直看不到新内容，
 * 而"要不要回去"该由用户决定，不是由自动滚动替他决定。
 *
 * 形态（用户拍板 2026-11-27）：只有箭头、不写可见文字（文案只进 aria-label/title），
 * 圆形主题色底——直接复用 `Button` 默认变体（bg-primary / hover:bg-primary/90），
 * 与输入区发送钮同一颗主题色、同为圆形，不在这里另写一份颜色口径；
 * 出现条件不是"离开底部"而是"离开底部够远"（阈值 JUMP_BUTTON_PX
 * 在 hooks/useStickToBottom 一处定义），轻微上翻不弹按钮，免得长会话里反复闪现。
 *
 * 生成中（loading）箭头带一点脉冲，把"下面还在长东西"说出口。
 */
interface Props {
  /** 是否该出现（离开底部够远；判定口径不在这里写第二套） */
  show: boolean;
  /** 本轮是否还在生成（决定脉冲与无障碍名） */
  loading?: boolean;
  /** 点击：滚到底部并恢复自动跟随（由调用方的 useStickToBottom 提供） */
  onScrollToBottom: () => void;
  className?: string;
}

export default function BackToBottomButton({ show, loading = false, onScrollToBottom, className }: Props) {
  const { t } = useTranslation(['chat', 'common']);
  if (!show) return null;
  const label = loading ? t('chat:page.backToBottomNew') : t('chat:page.backToBottom');
  return (
    <div className={cn('pointer-events-none absolute inset-x-0 bottom-4 flex justify-end', className)}>
      <Button
        type="button"
        size="icon"
        onClick={onScrollToBottom}
        aria-label={label}
        title={label}
        className="pointer-events-auto h-9 w-9 shrink-0 rounded-full shadow-md"
      >
        <ArrowDown className={cn('h-4 w-4', loading && 'animate-bounce')} />
      </Button>
    </div>
  );
}
