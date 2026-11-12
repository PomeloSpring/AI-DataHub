/**
 * 会话提问节点导航（长会话定位）
 *
 * 会话变长后，回找之前某个问题只能靠滚动。这里在正文右侧留白处竖一排刻度：
 * 鼠标悬停显示那一轮的提问原文，点击把视口滚到对应消息并短暂高亮。
 *
 * 数据来源只有已渲染的消息清单（提问文本 = 用户气泡正文），**不额外请求接口、
 * 不调模型**；跳转靠消息行的 DOM 锚点，滚动口径与页面唯一的 ScrollArea viewport 一致
 * （不在前端另造一套滚动容器）。
 *
 * 只有一个提问时不渲染：单问题会话没有"回找"需求，摆一排刻度只是视觉噪音。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

export interface TurnNavItem {
  /** 消息在 messages 里的下标（跳转锚点的主键，不是轮次序号） */
  index: number;
  /** 提问正文（已压成单行的可读文本） */
  text: string;
}

interface Props {
  turns: TurnNavItem[];
  /** 取某条消息行的 DOM（锚点缺失时该节点不响应跳转，不假装能跳） */
  getAnchor: (index: number) => HTMLElement | null;
  /** 取滚动视口（Radix ScrollArea 的 viewport） */
  getViewport: () => HTMLElement | null;
}

/** 悬停浮卡里最多展示多少字（再长会撑爆卡片，正文区自己能看到完整内容） */
const HOVER_MAX_CHARS = 400;

/** 已滚过视口顶部多少像素算"这一轮已经看过"（scroll-spy 判定阈值） */
const ACTIVE_OFFSET_PX = 96;

/** 跳转后的高亮时长（与 globals.css 的 .chat-turn-flash 动画时长一致） */
const FLASH_MS = 1400;

/** 顶部留白：不让目标消息贴死视口上沿 */
const JUMP_TOP_GAP = 12;

/**
 * 滚到某条消息并短暂高亮 —— 提问节点与侧栏条目「定位」共用这一份实现。
 *
 * 只问页面唯一的 ScrollArea viewport 要 scrollTo，不在前端另造第二套滚动容器；
 * 高亮走 DOM class（消息列里有大量图表卡，不值得为它整列 React 重渲染）。
 */
export function jumpToTurnAnchor(
  index: number,
  getAnchor: (index: number) => HTMLElement | null,
  getViewport: () => HTMLElement | null,
): boolean {
  const vp = getViewport();
  const el = getAnchor(index);
  if (!vp || !el) return false;
  const target = el.getBoundingClientRect().top - vp.getBoundingClientRect().top + vp.scrollTop;
  vp.scrollTo({ top: Math.max(0, target - JUMP_TOP_GAP), behavior: 'smooth' });
  el.classList.remove('chat-turn-flash');
  el.classList.add('chat-turn-flash');
  window.setTimeout(() => el.classList.remove('chat-turn-flash'), FLASH_MS);
  return true;
}

function shortLabel(text: string, max = 24): string {
  const one = (text || '').replace(/\s+/g, ' ').trim();
  return one.length > max ? `${one.slice(0, max)}…` : one;
}

export default function TurnNavigator({ turns, getAnchor, getViewport }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const [active, setActive] = useState<number>(-1);
  const rafRef = useRef<number | null>(null);

  /** 当前视口顶部之上（含阈值内）最后一条提问 = 高亮节点 */
  const computeActive = useCallback(() => {
    const vp = getViewport();
    if (!vp) return;
    const top = vp.getBoundingClientRect().top;
    let last = -1;
    turns.forEach((t, i) => {
      const el = getAnchor(t.index);
      if (!el) return;
      if (el.getBoundingClientRect().top - top <= ACTIVE_OFFSET_PX) last = i;
    });
    setActive(prev => (prev === last ? prev : last));
  }, [getAnchor, getViewport, turns]);

  useEffect(() => {
    const vp = getViewport();
    if (!vp) return;
    const schedule = () => {
      if (rafRef.current !== null) return;
      rafRef.current = window.requestAnimationFrame(() => {
        rafRef.current = null;
        computeActive();
      });
    };
    schedule();
    vp.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    return () => {
      vp.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      if (rafRef.current !== null) window.cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [computeActive, getViewport, turns.length]);

  const jump = (item: TurnNavItem) => {
    jumpToTurnAnchor(item.index, getAnchor, getViewport);
  };

  // 提问很多时收敛密度，否则长会话的刻度列会高过可视区
  const dense = turns.length > 18;
  const shown = useMemo(
    () => turns.map((t, i) => ({ ...t, no: i + 1 })),
    [turns],
  );

  if (shown.length < 2) return null;

  return (
    <div
      className="pointer-events-none absolute right-3 top-1/2 z-10 -translate-y-1/2"
      data-turn-navigator
      aria-label="会话提问导航"
    >
      <div
        className={cn(
          'pointer-events-auto flex max-h-[68vh] flex-col items-end overflow-y-auto',
          '[&::-webkit-scrollbar]:hidden [scrollbar-width:none]',
          dense ? 'gap-1.5' : 'gap-2.5',
        )}
      >
        {shown.map(t => {
          const isActive = shown[active]?.index === t.index;
          const isHover = hover === t.index;
          return (
            <div key={t.index} className="relative flex justify-end">
              {isHover && (
                <div
                  className="absolute bottom-0 right-full mr-2 w-[320px] rounded-lg border border-border bg-popover p-3 text-xs shadow-lg"
                  role="tooltip"
                >
                  <div className="mb-1 flex items-center gap-2">
                    <span className="font-medium text-foreground">第 {t.no} 问</span>
                    <span className="text-[10px] text-muted-foreground">点击跳转到该问题</span>
                  </div>
                  <p className="whitespace-pre-wrap break-words text-muted-foreground">
                    {t.text.length > HOVER_MAX_CHARS ? `${t.text.slice(0, HOVER_MAX_CHARS)}…` : t.text}
                  </p>
                </div>
              )}
              <button
                type="button"
                aria-label={`跳转到第 ${t.no} 个问题：${shortLabel(t.text)}`}
                title={shortLabel(t.text, 60)}
                // 当前所在轮 / 悬停态显式标出来：样式是 bg-primary + 加宽，
                // 用例按数据属性断言，不去子串匹配 tailwind class（hover:bg-primary/60 会误命中）
                data-active={isActive ? 'true' : undefined}
                data-hover={isHover ? 'true' : undefined}
                onClick={() => jump(t)}
                onMouseEnter={() => setHover(t.index)}
                onMouseLeave={() => setHover(prev => (prev === t.index ? null : prev))}
                onFocus={() => setHover(t.index)}
                onBlur={() => setHover(prev => (prev === t.index ? null : prev))}
                className={cn(
                  'rounded-full transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                  dense ? 'h-[3px]' : 'h-[4px]',
                  isActive || isHover
                    ? 'w-8 bg-primary'
                    : cn('bg-border hover:bg-primary/60', dense ? 'w-4' : 'w-5'),
                )}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
