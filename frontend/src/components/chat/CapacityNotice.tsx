// 并发排队与模型繁忙的可见提示条（挂在输入卡片上方，与 `/` 面板同一锚位）。
//
// 为什么必须单独有它：过去所有失败都归成一句"执行未完成"，用户分不清是自己问法问题、
// 平台在排队，还是提供商在拒；而排队时屏幕上只有一个转圈，既不说明位置也不说明还要等多久。
// 位置/预计等待/重试次数都由服务端给出实况（闸门有真实队列，退避采纳提供商的 retry_after），
// 前端不编倒计时冒充精确剩余时间。
import { useEffect, useState } from 'react';
import { AlertCircle, Hourglass, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui';
import type { CapacityState } from '@/stores/chatStore';

interface Props {
  state: CapacityState;
  /** 排队中允许"取消排队"（本质是断掉本轮请求）；回合已结束时不可点 */
  onCancel?: () => void;
  /** 重试耗尽后的两个出口：重试本轮 / 切换模型（复用既有模型选择器，不新造入口） */
  onRetry?: () => void;
  onSwitchModel?: () => void;
}

export function CapacityNotice({ state, onCancel, onRetry, onSwitchModel }: Props) {
  const { t } = useTranslation('chat');
  const [left, setLeft] = useState(0);
  const waiting = state.phase === 'retrying' ? Math.max(0, Math.round(state.wait_s || 0)) : 0;

  // 每次重试事件都带新的等待秒数（服务端采纳提供商 retry_after），按次重置而不是猜
  useEffect(() => {
    setLeft(waiting);
    if (!waiting) return;
    const timer = window.setInterval(() => setLeft(v => (v > 0 ? v - 1 : 0)), 1000);
    return () => window.clearInterval(timer);
  }, [waiting, state.attempt]);

  if (state.phase === 'queued') {
    const estimate = typeof state.estimated_wait_s === 'number'
      ? Math.max(1, Math.round(state.estimated_wait_s)) : null;
    return (
      <div
        role="status"
        className="mb-2 flex items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
      >
        <Hourglass className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1">
          {t('capacity.queued', {
            position: state.position || 1,
            estimate: estimate ? t('capacity.queuedEstimate', { seconds: estimate }) : t('capacity.queuedNoEstimate'),
          })}
        </span>
        {onCancel && (
          <Button variant="ghost" size="sm" className="h-6 shrink-0 px-2 text-xs" onClick={onCancel}>
            {t('capacity.cancelQueue')}
          </Button>
        )}
      </div>
    );
  }

  if (state.phase === 'retrying') {
    return (
      <div
        role="status"
        className="mb-2 flex items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
      >
        <RefreshCw className="h-3.5 w-3.5 shrink-0 animate-spin" />
        <span className="min-w-0 flex-1">
          {t('capacity.retrying', {
            reason: state.reason || t('capacity.modelBusy'),
            attempt: state.attempt || 1,
            max: state.max_attempts || state.attempt || 1,
            wait: left > 0 ? t('capacity.retryWait', { seconds: left }) : t('capacity.retryReconnect'),
          })}
        </span>
        {onCancel && (
          <Button variant="ghost" size="sm" className="h-6 shrink-0 px-2 text-xs" onClick={onCancel}>
            {t('capacity.stopWaiting')}
          </Button>
        )}
      </div>
    );
  }

  if (state.phase === 'exhausted') {
    return (
      <div
        role="alert"
        className="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs"
      >
        <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
        <span className="min-w-0 flex-1 text-muted-foreground">
          {t('capacity.exhausted', { reason: state.reason ? t('capacity.exhaustedReason', { reason: state.reason }) : '' })}
        </span>
        {onRetry && (
          <Button variant="outline" size="sm" className="h-7 shrink-0 px-2.5 text-xs" onClick={onRetry}>
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            {t('capacity.retryTurn')}
          </Button>
        )}
        {onSwitchModel && (
          <Button variant="outline" size="sm" className="h-7 shrink-0 px-2.5 text-xs" onClick={onSwitchModel}>
            {t('capacity.switchModel')}
          </Button>
        )}
      </div>
    );
  }

  return null;
}
