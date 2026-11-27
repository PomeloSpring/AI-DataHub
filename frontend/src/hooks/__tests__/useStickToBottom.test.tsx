import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { isNearBottom, JUMP_BUTTON_PX, NEAR_BOTTOM_PX, useStickToBottom } from '../useStickToBottom';

/**
 * 贴底判定契约（hooks/useStickToBottom 是唯一实现处，Chat 与 AS-BOT 面板共用）。
 *
 * 三条都是本次缺陷的直接形态，旧实现在此必红：
 * 1. 滚动容器是**条件挂载**的（Chat 的 ScrollArea 只在 hasTurnUI 时存在）：容器出现后
 *    监听必须重绑并立刻按几何校准。原实现 effect 依赖为 `[]`，首屏容器不存在就永久没绑上，
 *    判定恒为"贴底"，流式每帧把上翻查看历史的用户拽回底部。
 * 2. 两个阈值各管一件事且只在此处定义：80px 内算贴底（要不要自动跟随），
 *    离开超过 240px 才给回程按钮（用户拍板：轻微上翻不得弹按钮）；
 * 3. smooth 滚动的中间态 scroll 事件不代表用户意图，抑制窗口内不得把「回到底部」按钮抖出来；
 *    窗口过后必须恢复按几何判定（否则用户上翻会被永久忽略）。
 */

interface Geo { scrollHeight: number; clientHeight: number; scrollTop?: number }

/** jsdom 没有布局：几何全靠注入（scrollHeight/clientHeight/scrollTop 都是只读或零值，
 * 必须按 TurnNavigator.test.tsx 同口径用 defineProperty 接到一个可变 state 上） */
interface GeoState { scrollTop: number; scrollHeight: number; clientHeight: number }

function makeViewport({ scrollHeight, clientHeight, scrollTop = 0 }: Geo): HTMLElement {
  const el = document.createElement('div');
  const geo: GeoState = { scrollTop, scrollHeight, clientHeight };
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => geo.scrollHeight });
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => geo.clientHeight });
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => geo.scrollTop,
    set: (v: number) => { geo.scrollTop = v; },
  });
  // 真实浏览器会把 scrollTop 夹到 scrollHeight - clientHeight，stub 同口径
  el.scrollTo = vi.fn((opts?: { top?: number }) => {
    if (typeof opts?.top === 'number') {
      geo.scrollTop = Math.min(opts.top, geo.scrollHeight - geo.clientHeight);
    }
  }) as never;
  (el as unknown as { __geo: GeoState }).__geo = geo;
  return el;
}

/** 模拟内容又长了一截（图表/表格提交）：scrollTop 不变，所以距底变大 */
function grow(el: HTMLElement, by: number) {
  (el as unknown as { __geo: GeoState }).__geo.scrollHeight += by;
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

  it('按钮阈值比贴底阈值宽：轻微上翻不跟随也不弹按钮', () => {
    expect(JUMP_BUTTON_PX).toBeGreaterThan(NEAR_BOTTOM_PX);
    const box = makeViewport({ scrollHeight: 1000, clientHeight: 400, scrollTop: 0 });
    const { result } = renderHook(() => useStickToBottom(() => box, true));
    // 距底 150px：超出贴底阈值（不再自动跟随），但还没到按钮阈值
    userScroll(box, 1000 - 400 - 150);
    expect(result.current.atBottom).toBe(false);
    expect(result.current.showJumpButton).toBe(false);
    // 距底 300px：真的翻上去了，才给回程入口
    userScroll(box, 1000 - 400 - 300);
    expect(result.current.showJumpButton).toBe(true);
  });
});

describe('方向优先：上翻任意距离即脱钩', () => {
  it('距底从 20px 变 30px（仍在 80px 阈值内）也算上翻，立即停止跟随', () => {
    const box = makeViewport({ scrollHeight: 1000, clientHeight: 400, scrollTop: 580 });
    const { result } = renderHook(() => useStickToBottom(() => box, true));
    expect(result.current.atBottom).toBe(true);      // 距底 20px，本来在跟随

    userScroll(box, 570);                             // 距底 30px：只翻了 10px
    expect(result.current.stickRef.current).toBe(false);
    expect(result.current.atBottom).toBe(false);
  });

  it('跟随自己的回声不算上翻：事件落地时内容已撑高 200px 仍保持跟随', () => {
    const box = makeViewport({ scrollHeight: 1000, clientHeight: 400, scrollTop: 550 });
    const { result } = renderHook(() => useStickToBottom(() => box, true));
    expect(result.current.atBottom).toBe(true);      // 距底 50px（阈值内）

    act(() => { result.current.scrollToBottom('auto'); });
    // 事件还没派发，又提交了一块内容：scrollTop 没动 → 距底从 0 变成 200
    grow(box, 200);
    userScroll(box, 600);
    expect(result.current.atBottom).toBe(true);      // 回声，不是用户上翻

    // 基线已刷新：之后用户真的往上翻，仍然立即脱钩
    userScroll(box, 500);
    expect(result.current.atBottom).toBe(false);
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
    expect(result.current.showJumpButton).toBe(true);

    // 之后按真实滚动事件走：滚回底部恢复跟随（按钮同时收起），再上翻又断开
    userScroll(box, 560);
    expect(result.current.atBottom).toBe(true);
    expect(result.current.showJumpButton).toBe(false);
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
    // 真实容器里瞬时滚动会直接落到底部（scrollTop 被夹到 scrollHeight - clientHeight），
    // 判定随之为贴底（滞后的 scroll 事件同值）
    expect(box.scrollTop).toBe(600);
    expect(result.current.atBottom).toBe(true);
    expect(result.current.stickRef.current).toBe(true);
  });

  it('smooth 动画中间态不得把按钮抖出来；窗口过后按几何判定', () => {
    let now = 1000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const box = makeViewport({ scrollHeight: 1000, clientHeight: 400, scrollTop: 0 });
    const { result } = renderHook(() => useStickToBottom(() => box, true));

    act(() => { result.current.scrollToBottom('smooth'); });
    expect(result.current.showJumpButton).toBe(false);
    // 动画进行到一半（距底 600px）：这是程序化中间态，不是用户意图
    userScroll(box, 0);
    expect(result.current.atBottom).toBe(true);
    expect(result.current.showJumpButton).toBe(false);

    // 窗口过后同样的几何就按事实判定（否则用户上翻会被永久忽略）
    now += 800;
    userScroll(box, 0);
    expect(result.current.atBottom).toBe(false);
    expect(result.current.showJumpButton).toBe(true);
  });
});
