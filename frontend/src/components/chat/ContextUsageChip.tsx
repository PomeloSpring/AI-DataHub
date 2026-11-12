// 聊天输入框右下角的上下文占用小徽标：收起态是一条微进度条（不写百分比数字），点开才出占用数值、明细与压缩入口。
//
// 为什么是"微进度条"而不是通栏进度条，也不是百分比文字：输入卡片是用户打字的注意力焦点，横贯一条进度条
// 加一块展开明细会把卡片撑高、抢走视线（历史版本就是这样，被要求改小）；而裸百分比数字太抽象，
// 一眼看不出"还剩多少余量"（历史版本是数字，被要求换成进度条）。收成右下角一小条，形状即比例。
//
// 为什么数字一律来自服务端：唯一权威口径是执行层 SDK 的 `get_context_usage()`
// （等价 CLI `/context` 视图，返回百分比而非 token 数）。前端不自造 token 估算、
// 不写第二套阈值，否则"显示条满了却没压缩"或"没满就压缩"都无从解释。
//
// 为什么"未测量"不渲染、而"采集失败"必须留着：新会话本来就没有上下文占用可展示，画一条虚线空槽
// 只会多一个没人看得懂的空控件（用户反馈：空的时候不显示）。但没测过≠测不出来：后者带服务端
// `error`（成功采集时会清空），仍必须显式存在，不能跟着一起藏掉（no-silent-degradation）。
// 同理，已测出 0% 是真实数据（空上下文），照常渲染。
//
// 自动压缩的触发时机归 CLI autoCompact（产品口径：交给模型/SDK 控制），本组件只把
// "正在压缩 / 已压缩"变成看得见的状态；手动压缩才是这里的动作。
import { useEffect, useRef, useState } from 'react';
import { Minimize2 } from 'lucide-react';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import type { CompactResult, ContextUsage } from '@/stores/chatStore';

interface Props {
  /** 最近一次采集值；null = 还没拉过（新会话/刚切换会话） */
  usage: ContextUsage | null;
  /** CLI autoCompact 正在进行（SSE context 事件驱动） */
  autoCompacting?: boolean;
  /** 手动压缩请求中 */
  compacting?: boolean;
  /** 任务进行中/正在停止：此时不允许压缩（服务端同样会用执行权拦） */
  blocked?: boolean;
  /** 被拦原因（写进按钮 title，不让用户对着灰按钮猜） */
  blockedReason?: string;
  /** 触发手动压缩；失败必须抛出服务端给的原因（本组件负责 toast） */
  onCompact: () => Promise<CompactResult | null>;
}

const pctText = (v?: number | null) => (typeof v === 'number' ? `${Math.round(v)}%` : '--%');

/** 占用色阶：<70% 常规、70~85% 提醒、>85% 紧张。 */
const barClass = (pct: number) => (pct > 85
  ? 'bg-red-500'
  : (pct >= 70 ? 'bg-amber-500' : 'bg-primary/70'));
const textClass = (pct: number) => (pct > 85
  ? 'text-red-600 dark:text-red-400'
  : (pct >= 70 ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'));
/** 只有"采集失败"才会以空槽形态出现：虚线 + 红色，与 0% 的实心轨道可区分，也与"没测过不渲染"不同。 */
const trackClass = (measured: boolean) => (measured
  ? 'bg-muted-foreground/15'
  : 'border border-dashed border-red-500/60 bg-transparent');

export function ContextUsageChip({
  usage, autoCompacting = false, compacting = false, blocked = false, blockedReason, onCompact,
}: Props) {
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState('');
  const wrapRef = useRef<HTMLDivElement>(null);
  const summaryTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (summaryTimer.current) window.clearTimeout(summaryTimer.current);
  }, []);

  const measured = !!usage?.available && typeof usage.used_percentage === 'number';
  const pct = measured ? (usage!.used_percentage as number) : null;
  const threshold = usage?.threshold_percentage ?? null;
  const busy = autoCompacting || compacting;
  const categories = usage?.categories || [];
  /** 采集/读取失败事实（服务端 `error`，成功采集即清空）："显示不出来"而不是"还没测过"。 */
  const failed = !!usage?.error;
  const failText = usage?.error || usage?.reason || '';

  const disabledReason = blocked
    ? (blockedReason || '本轮任务进行中，等它结束或先停止后再压缩')
    : (compacting ? '正在压缩，请稍候…'
      : (autoCompacting ? '模型正在自动压缩上下文，请稍候…'
        : (!measured ? (usage?.reason || '尚未测量，先发一轮消息后再试') : '')));
  const disabled = !!disabledReason;

  // 点徽标外面或按 Esc 就收起：展开态是"临时看一眼"，常驻就把小徽标又变回一大块
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const showSummary = (text: string) => {
    setSummary(text);
    if (summaryTimer.current) window.clearTimeout(summaryTimer.current);
    // 提示是"这一次压缩"的回执，常驻会变成过期事实，8s 后自行退场
    summaryTimer.current = window.setTimeout(() => setSummary(''), 8000);
  };

  const handleCompact = async () => {
    if (disabled) return;
    try {
      const result = await onCompact();
      if (result) {
        showSummary(`已压缩，占用从 ${pctText(result.before_percentage)} 降到 `
          + `${pctText(result.after_percentage)}（第 ${result.compact_count} 次）`);
      }
    } catch (e: any) {
      // 失败必须显式：服务端 detail 优先（409/502 都带可读原因），不回退成"压缩成功"
      toast.error(e?.response?.data?.detail || e?.message || '压缩失败，占用未变化');
    }
  };

  // 新会话/尚未测量：没有任何占用可说，连控件也不占位（不是一条虚线空槽）；
  // 但压缩进行中或采集失败不属于这一档，前者要看得见状态，后者要看得见报错。
  if (!measured && !failed && !busy) return null;

  return (
    <div ref={wrapRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-label={busy ? '上下文压缩中' : (measured ? `上下文占用 ${pctText(pct)}` : '上下文占用采集失败')}
        title={busy ? '上下文压缩中…'
          : (measured ? `上下文占用 ${pctText(pct)}（点击查看明细与压缩）`
            : (failText || '上下文采集失败（点击查看原因）'))}
        className={cn(
          'flex h-6 w-[68px] items-center rounded-md px-2 transition-colors hover:bg-muted/70',
          busy ? 'text-muted-foreground' : (measured ? textClass(pct!) : 'text-red-600 dark:text-red-400'),
        )}
      >
        {/* 收起态：一整条微进度条（宽即余量），不展示百分比数字；采集失败时是红色虚线空槽，不画成 0% 的实心轨道 */}
        <span className={cn('relative block h-1.5 w-full overflow-hidden rounded-full', trackClass(measured))}>
          {busy && <span className="absolute inset-y-0 left-0 w-1/3 animate-pulse rounded-full bg-primary/50" />}
          {!busy && measured && (
            <span
              className={cn('absolute inset-y-0 left-0 rounded-full transition-all', barClass(pct!))}
              style={{ width: `${Math.min(100, Math.max(4, pct!))}%` }}
            />
          )}
        </span>
      </button>

      {open && (
        <div className="absolute bottom-full right-0 z-30 mb-2 w-[260px] rounded-xl border bg-card p-3 shadow-lg">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-xs font-semibold">上下文窗口</span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {busy ? '压缩中…' : pctText(pct)}
            </span>
          </div>

          <span
            className="relative mt-2 block h-1.5 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={measured ? Math.round(pct!) : undefined}
          >
            {busy && (
              <span className="absolute inset-y-0 left-0 w-1/3 animate-pulse rounded-full bg-primary/50" />
            )}
            {!busy && measured && (
              <span
                className={cn('absolute inset-y-0 left-0 rounded-full transition-all', barClass(pct!))}
                style={{ width: `${Math.min(100, Math.max(1, pct!))}%` }}
              />
            )}
            {!busy && measured && typeof threshold === 'number' && threshold > 0 && (
              <span
                aria-hidden
                title={`自动压缩阈值 ${pctText(threshold)}`}
                className="absolute inset-y-[-2px] w-px bg-foreground/40"
                style={{ left: `${Math.min(100, threshold)}%` }}
              />
            )}
          </span>

          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            {measured
              ? (failed
                // 有旧值 + 本次采集失败：不能只当没事发生，也不能拿旧值冒充当前值
                ? <span className="text-red-600 dark:text-red-400">最近一次采集失败：{failText}（上方是上次成功的值）</span>
                : '接近阈值时模型会自动压缩早期内容；手动压缩立即生效，聊天记录不受影响。')
              : (failText || '尚未测量：发送一轮消息后开始测量')}
          </p>

          {summary && <p className="mt-1.5 text-[11px] text-primary">{summary}</p>}

          {measured && categories.length > 0 && (
            <ul className="mt-2 space-y-1">
              {categories.map(c => (
                <li key={c.type} className="flex items-center gap-1.5 text-[11px]">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-foreground/60" />
                  <span className="min-w-0 flex-1 truncate text-muted-foreground" title={c.label}>
                    {c.label}
                  </span>
                  <span className="shrink-0 tabular-nums">{pctText(c.percentage)}</span>
                </li>
              ))}
            </ul>
          )}
          {measured && categories.length === 0 && (
            <p className="mt-2 text-[11px] text-muted-foreground">Harness 未返回分类明细（仅有总占用）</p>
          )}

          <p className="mt-2 flex flex-wrap gap-x-1.5 text-[11px] text-muted-foreground">
            <span>
              自动压缩：{usage?.auto_compact_enabled ? '已开启' : '未开启'}
              {/* 阈值归到“自动压缩”这一句里：它是自动压缩的属性，挂在原因后面会把
                  未测量那句说明拼成半句，用户反而看不出哪句是重点 */}
              {typeof threshold === 'number' && `（阈值 ${pctText(threshold)}）`}
            </span>
            {typeof usage?.compact_count === 'number' && (
              <span>· 已手动压缩 {usage.compact_count} 次</span>
            )}
            {usage?.model && <span>· {usage.model}</span>}
          </p>

          <button
            type="button"
            onClick={handleCompact}
            disabled={disabled}
            title={disabled ? disabledReason : '压缩本会话上下文（会先归纳早期内容，聊天记录不受影响）'}
            className={cn(
              'mt-2.5 flex h-8 w-full items-center justify-center gap-1.5 rounded-lg border text-xs transition-colors',
              disabled
                ? 'cursor-not-allowed border-border/40 text-muted-foreground/50'
                : 'border-border/70 text-foreground hover:bg-muted',
            )}
          >
            {compacting ? <Spinner className="h-3.5 w-3.5" /> : <Minimize2 className="h-3.5 w-3.5" />}
            压缩上下文
          </button>
        </div>
      )}
    </div>
  );
}
