// 会话提问节点导航契约（长会话定位）：
// ① 只有一个提问时不给死开关；② 节点文本取自用户气泡正文（只带附件时也要可读，不出现空白节点）；
// ③ 悬停显示该问原文；④ 点击把唯一的 ScrollArea 视口滚到对应消息行并短暂高亮；
// ⑤ 滚动时高亮跟着走（scroll-spy），不能一直停在第一问。
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TurnNavigator from '../TurnNavigator';
import type { TurnNavItem } from '../TurnNavigator';

const rect = (top: number) => ({
  top, bottom: top + 100, left: 0, right: 800, width: 800, height: 100, x: 0, y: top,
  toJSON: () => ({}),
}) as DOMRect;

/** 假视口：只用到 getBoundingClientRect / scrollTop / scrollTo（jsdom 不实现 scrollTo） */
function makeViewport(scrollTo = vi.fn()) {
  const vp = document.createElement('div');
  const state = { scrollTop: 0 };
  vp.getBoundingClientRect = () => rect(0);
  Object.defineProperty(vp, 'scrollTop', {
    get: () => state.scrollTop,
    set: (v: number) => { state.scrollTop = v; },
    configurable: true,
  });
  Object.defineProperty(vp, 'scrollTo', { value: scrollTo, configurable: true });
  return { vp, state, scrollTo };
}

/** 假消息行锚点：位置 = 内容里的绝对偏移 - 当前滚动量（与真实布局同口径） */
function makeAnchor(contentTop: number, state: { scrollTop: number }) {
  const el = document.createElement('div');
  el.getBoundingClientRect = () => rect(contentTop - state.scrollTop);
  document.body.appendChild(el);
  return el;
}

const turns: TurnNavItem[] = [
  { index: 0, text: '帮我查一下11月3号设备表的同步日志' },
  { index: 2, text: '换成 180 天保留窗口口径再看一次' },
];

describe('TurnNavigator', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => window.setTimeout(() => cb(Date.now()), 0));
    vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = '';
  });

  it('只有一个提问时不渲染（没有回找需求，不放占位死开关）', () => {
    const { vp, state } = makeViewport();
    const anchors = { 0: makeAnchor(10, state) };
    render(
      <TurnNavigator
        turns={[turns[0]]}
        getAnchor={(i: number) => anchors[i as keyof typeof anchors] ?? null}
        getViewport={() => vp}
      />,
    );
    expect(screen.queryByLabelText('会话提问导航')).toBeNull();
  });

  it('每个提问一个节点，悬停显示该问原文', async () => {
    const { vp, state } = makeViewport();
    const anchors: Record<number, HTMLElement> = {
      0: makeAnchor(10, state),
      2: makeAnchor(1200, state),
    };
    render(
      <TurnNavigator turns={turns} getAnchor={(i: number) => anchors[i] ?? null} getViewport={() => vp} />,
    );
    const nodes = screen.getAllByRole('button', { name: /跳转到第/ });
    expect(nodes).toHaveLength(2);
    // aria-label 带问题摘要：读屏/悬浮都能知道这一格是哪一问
    expect(nodes[1].getAttribute('aria-label')).toContain('换成 180 天保留窗口口径再看一次');

    await userEvent.hover(nodes[1]);
    const card = await screen.findByRole('tooltip');
    expect(card.textContent).toContain('第 2 问');
    expect(card.textContent).toContain('换成 180 天保留窗口口径再看一次');
    expect(card.textContent).toContain('点击跳转到该问题');
    expect(nodes[1].getAttribute('data-hover')).toBe('true');

    await userEvent.unhover(nodes[1]);
    await waitFor(() => expect(screen.queryByRole('tooltip')).toBeNull());
  });

  it('点击节点滚动到对应消息行，并给该行加跳转高亮', async () => {
    const { vp, state, scrollTo } = makeViewport();
    const anchors: Record<number, HTMLElement> = {
      0: makeAnchor(10, state),
      2: makeAnchor(1200, state),
    };
    render(
      <TurnNavigator turns={turns} getAnchor={(i: number) => anchors[i] ?? null} getViewport={() => vp} />,
    );
    await userEvent.click(screen.getAllByRole('button', { name: /跳转到第 2 个/ })[0]);
    // 目标位置 = 锚点在内容中的偏移 - 顶部留白 12px（不让问题气泡贴死视口上沿）
    expect(scrollTo).toHaveBeenCalledWith({ top: 1200 - 12, behavior: 'smooth' });
    expect(anchors[2].classList.contains('chat-turn-flash')).toBe(true);
    expect(anchors[0].classList.contains('chat-turn-flash')).toBe(false);
  });

  it('滚动后高亮跟着当前提问走（不一直停在第一问）', async () => {
    const { vp, state } = makeViewport();
    const anchors: Record<number, HTMLElement> = {
      0: makeAnchor(10, state),
      2: makeAnchor(1200, state),
    };
    render(
      <TurnNavigator turns={turns} getAnchor={(i: number) => anchors[i] ?? null} getViewport={() => vp} />,
    );
    const nodes = screen.getAllByRole('button', { name: /跳转到第/ });
    await waitFor(() => expect(nodes[0].getAttribute('data-active')).toBe('true'));
    expect(nodes[1].getAttribute('data-active')).toBeNull();

    state.scrollTop = 1200;
    vp.dispatchEvent(new Event('scroll'));
    await waitFor(() => expect(nodes[1].getAttribute('data-active')).toBe('true'));
    await waitFor(() => expect(nodes[0].getAttribute('data-active')).toBeNull());
  });

  it('锚点缺失时点击不假装跳转（不抛错也不滚到别处）', async () => {
    const { vp, scrollTo } = makeViewport();
    render(
      <TurnNavigator turns={turns} getAnchor={() => null} getViewport={() => vp} />,
    );
    await userEvent.click(screen.getAllByRole('button', { name: /跳转到第/ })[0]);
    expect(scrollTo).not.toHaveBeenCalled();
  });
});
