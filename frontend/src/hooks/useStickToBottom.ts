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
 * - 阈值 `NEAR_BOTTOM_PX` 只在这一处定义（沿用原口径 80px，不新造配置项）；
 * - 容器条件挂载时 `rebindKey` 必须跟着变，否则复现上面第 1 条缺陷；
 * - 程序化滚动（`scrollToBottom`）动画期间派发的中间态 scroll 事件**不代表用户意图**，
 *   在抑制窗口内一律按贴底处理，否则「回到底部」按钮会在平滑滚动途中闪一下。
 */

/** 距底部多少像素以内算"贴底"（沿用原实现口径） */
export const NEAR_BOTTOM_PX = 80;

/** 程序化滚动的抑制窗口（ms）：覆盖一次 smooth 动画的时长 */
const PROGRAMMATIC_SUPPRESS_MS = 700;

/** 纯几何判定，单独导出便于用例直接锁阈值边界 */
export const isNearBottom = (el: HTMLElement, threshold = NEAR_BOTTOM_PX): boolean =>
  el.scrollHeight - el.scrollTop - el.clientHeight < threshold;

export interface StickToBottom {
  /** 给自动滚动 effect 同步读（渲染时序敏感，不能只靠 state） */
  stickRef: MutableRefObject<boolean>;
  /** 给「回到底部」按钮渲染 */
  atBottom: boolean;
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
  const suppressUntilRef = useRef(0);

  const apply = useCallback((stick: boolean) => {
    stickRef.current = stick;
    setAtBottom(stick);
  }, []);

  useEffect(() => {
    const el = getViewport();
    if (!el) return;
    // 绑定时先同步一次几何：容器刚挂载时可能已经没有停在底部（切会话/回填），
    // 不能等第一个 scroll 事件才校准。
    apply(isNearBottom(el));
    const onScroll = () => {
      if (performance.now() < suppressUntilRef.current) {
        apply(true);
        return;
      }
      apply(isNearBottom(el));
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [getViewport, rebindKey, apply]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    // 调用方意图就是"跟到底部"，判定先落下来（容器还没挂载时也要记住意图）
    apply(true);
    const el = getViewport();
    if (!el) return;
    // 只给 smooth 开抑制窗口：瞬时滚动落点就是最终几何，派发的事件本来就是真值。
    if (behavior === 'smooth') {
      suppressUntilRef.current = performance.now() + PROGRAMMATIC_SUPPRESS_MS;
    }
    el.scrollTo({ top: el.scrollHeight, behavior });
  }, [getViewport, apply]);

  return { stickRef, atBottom, scrollToBottom, setStick: apply };
}
