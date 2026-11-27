import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { isNearBottom, NEAR_BOTTOM_PX, useStickToBottom } from '../useStickToBottom';

/**
 * 贴底判定契约（hooks/useStickToBottom 是唯一实现处，Chat 与 AS-BOT 面板共用）。
 *
 * 三条都是本次缺陷的直接形态，旧实现在此必红：
 * 1. 滚动容器是**条件挂载**的（Chat 的 ScrollArea 只在 hasTurnUI 时存在）：容器出现后
 *    监听必须重绑并立刻按几何校准。原实现 effect 依赖为 `[]`，首屏容器不存在就永久没绑上，
 *    判定恒为"贴底"，流式每帧把上翻查看历史的用户拽回底部。
 * 2. 阈值只在 NEAR_BOTTOM_PX 一处定义（80px 内算贴底）。
 * 3. smooth 滚动的中间态 scroll 事件不代表用户意图，抑制窗口内不得把「回到底部」按钮抖出来；
 *    窗口过后必须恢复按几何判定（否则用户上翻会被永久忽略）。
 */

interface Geo { scrollHeight: number; clientHeight: number; scrollTop?: number }

/** jsdom 没有布局：几何全靠注入（scrollHeight/clientHeight/scrollTop 都是只读或零值，
 * 必须按 TurnNavigator.test.tsx 同口径用 defineProperty 接到一个可变 state 上） */
function makeViewport({ scrollHeight, clientHeight, scrollTop = 0 }: Geo) {
  const el = document.createElement('div');
  const geo = { scrollTop };
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => clientHeight });
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => geo.scrollTop,
    set: (v: number) => { geo.scrollTop = v; },
  });
  el.scrollTo = vi.fn((opts?: { top?: number }) => {
    if (typeof opts?.top === 'number') geo.scrollTop = opts.top;
  }) as never;
  return el;
}

/** 模拟用户把视口滚到 top 并派发 scroll 事件 */
function userScroll(el: HTMLElement, top: number) {
  act(() => {
    el.scrollTop = top;
    el.dispatchEvent(new Event('scroll'));
  });
}

afterEach(() => { vi.restoreAllMocks(); });

describe('isNearBottom 阈值', () => {
  it('距底 < 80px 才算贴底，边界值 80 本身不算', () => {
    const el = makeViewport({ scrollHeight: 1000, clientHeight: 400 });
    expect(isNearBottom(el)).toBe(false);            // 距底 600
    el.scrollTop = 1000 - 400 - NEAR_BOTTOM_PX;      // 距底正好 80
    expect(isNearBottom(el)).toBe(false);
    el.scrollTop += 1;                               // 距底 79
    expect(isNearBottom(el)).toBe(true);
  });
});

describe('容器条件挂载后的监听重绑', () => {
  it('rebindKey 翻真即重绑，并按当前几何立刻校准（不等第一个 scroll 事件）', () => {
    let box: HTMLElement | null = null;
    const { result, rerender } = renderHook(
      ({ key }: { key: boolean }) => useStickToBottom(() => box, key),
      { initialProps: { key: false } },
    );
    // 容器还没挂载：判定保持初始"贴底"，但绝不能把它当成已核实的事实
    expect(result.current.atBottom).toBe(true);

    // 容器出现且此刻停在中间（历史缺陷就在这：旧代码不再重跑，这里会一直是 true）
    box = makeViewport({ scrollHeight: 1000, clientHeight: 400, scrollTop: 0 });
    rerender({ key: true });
    expect(result.current.atBottom).toBe(false);
    expect(result.current.stickRef.current).toBe(false);

    // 之后按真实滚动事件走：滚回底部恢复跟随，再上翻又断开
    userScroll(box, 560);
    expect(result.current.atBottom).toBe(true);
    userScroll(box, 0);
    expect(result.current.atBottom).toBe(false);
  });
});

describe('scrollToBottom 与程序化滚动抑制窗口', () => {
  it('滚到容器底部并恢复跟随判定', () => {
    const box = makeViewport({ scrollHeight: 1000, clientHeight: 400, scrollTop: 0 });
    const { result } = renderHook(() => useStickToBottom(() => box, true));
    act(() => { result.current.setStick(false); });
    act(() => { result.current.scrollToBottom('auto'); });
    expect(box.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'auto' });
    // 真实容器里瞬时滚动会直接落到底部，判定随之为贴底（滞后的 scroll 事件同值）
    expect(box.scrollTop).toBe(1000);
    expect(result.current.atBottom).toBe(true);
    expect(result.current.stickRef.current).toBe(true);
  });

  it('smooth 动画中间态不得把按钮抖出来；窗口过后按几何判定', () => {
    let now = 1000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const box = makeViewport({ scrollHeight: 1000, clientHeight: 400, scrollTop: 0 });
    const { result } = renderHook(() => useStickToBottom(() => box, true));

    act(() => { result.current.scrollToBottom('smooth'); });
    // 动画进行到一半（距底 600px）：这是程序化中间态，不是用户意图
    userScroll(box, 0);
    expect(result.current.atBottom).toBe(true);

    // 窗口过后同样的几何就按事实判定（否则用户上翻会被永久忽略）
    now += 800;
    userScroll(box, 0);
    expect(result.current.atBottom).toBe(false);
  });
});
