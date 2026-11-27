import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';

/**
 * 聊天类滚动面的「用户是否贴底」判定 —— 唯一实现处（工作空间 Chat 与 AS-BOT 面板共用）。
 *
 * 为什么需要它（口径由来，不是"脚本说明"）：
 * 聊天软件的惯例是**上翻查看历史时不要被打断**，只有用户自己发新问题才主动滚到底。
 * 原 Chat 实现有两处让它失效：
 * 1. 贴底监听 effect 依赖是 `[]`，而滚动容器（Radix ScrollArea）被 `hasTurnUI` 条件挂载 ——
 *    首屏没有消息时 `querySelector` 拿到 null 直接 return，**监听器永远没绑上**，
 *    判定恒为初始"贴底"，流式每帧都把用户从历史里拽回底部；
 * 2. 新消息分支只看 `messages.length` 增加就强制滚底，不区分是用户提问还是助手消息入列。
 * 所以这里把判定收成一个 hook，并强制调用方传 `rebindKey`（见下）。
 *
 * 幂等/边界：
 * - 两个阈值各管一件事且只在此处定义：NEAR_BOTTOM_PX 是高度抖动的松弛量，
 *   JUMP_BUTTON_PX 控回程按钮显隐；
 * - **方向优先**：非程序化的 scroll 事件里距底变大就是用户往上翻，任意距离立即脱钩
 *   （用户拍板 2026-11-27：上翻一点点就别再拽）；我们自己发起的瞬时滚动会先把紧随的
 *   那一个事件标成回声，避免"事件落地时内容又长了一截"被误判成上翻；
 * - 容器条件挂载时 `rebindKey` 必须跟着变，否则复现上面第 1 条缺陷；
 * - 程序化滚动（`scrollToBottom`）动画期间派发的中间态 scroll 事件**不代表用户意图**，
 *   在抑制窗口内一律按贴底处理，否则「回到底部」按钮会在平滑滚动途中闪一下。
 */

/** 距底部多少像素以内算"贴底"（沿用原实现口径） */
export const NEAR_BOTTOM_PX = 80;

/**
 * 离开底部多远才给「回到底部」按钮（用户拍板 2026-11-27：要距底部一段距离后才出现）。
 * 与贴底阈值分开是有意的：80px 是"还要不要自动跟随"的判定，轻微上翻就弹按钮会
 * 在长会话里反复闪现；按钮只在用户真的翻到上面去了才成为一个回程入口。
 */
export const JUMP_BUTTON_PX = 300;

/** 程序化滚动的抑制窗口（ms）：覆盖一次 smooth 动画的时长 */
const PROGRAMMATIC_SUPPRESS_MS = 700;

/** 距底变大多少像素算"用户在往上翻"（留 1px 防亚像素取整抖动） */
const SCROLL_UP_EPS = 1;

/** 距底部还有多少像素（0 = 正好贴底）；两个判定都从这一个量派生 */
export const gapFromBottom = (el: HTMLElement): number =>
  el.scrollHeight - el.scrollTop - el.clientHeight;

/** 纯几何判定，单独导出便于用例直接锁阈值边界 */
export const isNearBottom = (el: HTMLElement, threshold = NEAR_BOTTOM_PX): boolean =>
  gapFromBottom(el) < threshold;

export interface StickToBottom {
  /** 给自动滚动 effect 同步读（渲染时序敏感，不能只靠 state） */
  stickRef: MutableRefObject<boolean>;
  /** 是否贴底（跟随中） */
  atBottom: boolean;
  /** 是否已离开底部够远，该给出回程按钮 */
  showJumpButton: boolean;
  /** 主动滚到底部并恢复跟随（用户发新问题、点按钮都走这里） */
  scrollToBottom: (behavior?: ScrollBehavior) => void;
  /** 显式改写跟随判定（如 prepend 更早消息后要压住自动跟随） */
  setStick: (v: boolean) => void;
}

export function useStickToBottom(
  getViewport: () => HTMLElement | null,
  /** 容器条件挂载时必须传（hasTurnUI / messages.length 之类），翻转即重绑监听 */
  rebindKey: unknown,
): StickToBottom {
  const stickRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const [showJumpButton, setShowJumpButton] = useState(false);
  const suppressUntilRef = useRef(0);
  /** 上一次看到的距底像素：方向判定的基线 */
  const lastGapRef = useRef(0);
  /** 我们自己发起的瞬时滚动即将派发的那一个事件（回声），不代表用户意图 */
  const echoRef = useRef(false);
  /** 当前已跟踪的容器：只对**新容器**做几何校准，同一容器重绑不得推翻方向判定 */
  const trackedElRef = useRef<HTMLElement | null>(null);

  /** showJump 传 undefined = 本次不改按钮显隐（交给几何/滞后的 scroll 事件定） */
  const apply = useCallback((stick: boolean, showJump?: boolean) => {
    stickRef.current = stick;
    setAtBottom(stick);
    if (showJump !== undefined) setShowJumpButton(showJump);
  }, []);

  /** 三个判定都按同一个 gap 算，方向基线也在这里更新（不分两处读容器，避免口径漂移） */
  const applyGap = useCallback((gap: number, stick: boolean) => {
    lastGapRef.current = gap;
    apply(stick, gap >= JUMP_BUTTON_PX);
  }, [apply]);

  useEffect(() => {
    const el = getViewport();
    if (!el) return;
    // 绑定时先同步一次几何：容器刚挂载时可能已经没有停在底部（切会话/回填），
    // 不能等第一个 scroll 事件才校准。但只对**新容器**做：同一容器重绑（调用方传的
    // getViewport 不稳定时每次渲染都会重跑）再按阈值算一遍，会把刚做出的"上翻脱钩"
    // 翻回去——阈值内的位置在几何上看起来就是贴底。
    if (trackedElRef.current !== el) {
      trackedElRef.current = el;
      applyGap(gapFromBottom(el), isNearBottom(el));
    }
    const onScroll = () => {
      const gap = gapFromBottom(el);
      // smooth 动画中间态、以及我们自己发起的瞬时滚动的回声：都不代表用户意图。
      // 回声必须单独识别：事件落地时可能又提交了一块内容（图表/表格撑高几百 px），
      // 那一下 gap 变大不是上翻，当成上翻就会把跟随静默卸掉。
      if (performance.now() < suppressUntilRef.current || echoRef.current) {
        echoRef.current = false;
        // 只刷新方向基线；两个判定都按"贴底、收起按钮"给，
        // 不能拿动画中间态的 gap 去算按钮显隐（否则按钮会在途中闪一下）。
        lastGapRef.current = gap;
        apply(true, false);
        return;
      }
      // 方向判定（用户拍板 2026-11-27）：距底变大就是用户在往上翻，任意距离立即脱钩，
      // 不等阈值——否则上翻几十像素仍会被下一帧拽回去；阈值只当高度抖动的松弛量用。
      if (gap > lastGapRef.current + SCROLL_UP_EPS) {
        applyGap(gap, false);
        return;
      }
      applyGap(gap, gap < NEAR_BOTTOM_PX);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [getViewport, rebindKey, applyGap]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    // 调用方意图就是"跟到底部"，判定先落下来（容器还没挂载时也要记住意图）
    apply(true, false);
    const el = getViewport();
    if (!el) return;
    lastGapRef.current = 0;   // 意图是到底，方向基线跟着归零
    if (behavior === 'smooth') {
      suppressUntilRef.current = performance.now() + PROGRAMMATIC_SUPPRESS_MS;
    } else if (gapFromBottom(el) > SCROLL_UP_EPS) {
      // 瞬时滚动只有真发生位移才派发事件，把它标成回声由下一个事件消费（不靠时间窗口：
      // 流式每几十毫秒就跟随一次，时间窗口会把真正的上翻也吞掉）。
      echoRef.current = true;
    }
    el.scrollTo({ top: el.scrollHeight, behavior });
  }, [getViewport, apply]);

  // 强制不跟随时不替用户决定按钮显隐（prepend 后紧跟的 scroll 事件会按几何算准）；
  // 强制跟随时等同于滚到底，按钮收起。
  const setStick = useCallback((v: boolean) => apply(v, v ? false : undefined), [apply]);

  return { stickRef, atBottom, showJumpButton, scrollToBottom, setStick };
}
