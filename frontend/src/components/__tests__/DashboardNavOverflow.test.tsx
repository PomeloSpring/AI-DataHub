import { act, cleanup, createEvent, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { useDashboardNavigation } from '../DashboardNavigation';

/**
 * 同组看板切换条的**横向溢出**行为回归（与 DashboardNavigation.test.tsx 的
 * 看板路由/组合卡集成用例分开：那份测页面链路，这份只测切换条自身的溢出契约）。
 *
 * 背景：切换条把滚动条隐藏了（视觉护栏），溢出时既没有"还能往哪滚"的提示，
 * 点后半段的看板时选中态还会停在容器外——用户反馈"选到后面就看不见了"。
 * 四条契约：翻页按钮、选中项滚入可视区、纵向滚轮转横向滚动、布局变化后重算溢出。
 *
 * jsdom 没有布局引擎：scrollWidth/clientWidth/scrollLeft 恒为 0，
 * 因此在原型层按元素注入几何值（WeakMap 记账），不靠真实排版。
 */
const geom = new WeakMap<HTMLElement, { sw: number; cw: number; sl: number }>();

function setNavGeom(el: HTMLElement, sw: number, cw: number, sl = 0) {
  geom.set(el, { sw, cw, sl });
}

/** 把某颗 pill 摆到指定横向区间（组件算最小位移靠的就是这个 rect） */
function stubRect(el: HTMLElement | null, left: number, right: number) {
  if (!el) throw new Error('stubRect：目标 pill 不存在');
  el.getBoundingClientRect = () => ({ left, right, width: right - left, x: left, y: 0,
    top: 0, bottom: 40, height: 40, toJSON: () => ({}) } as DOMRect);
}

/** 把滚动容器自身的可视区定在 [0, cw) */
function stubNavBox(el: HTMLElement, cw: number) {
  el.getBoundingClientRect = () => ({ left: 0, right: cw, width: cw, x: 0, y: 0,
    top: 0, bottom: 40, height: 40, toJSON: () => ({}) } as DOMRect);
}

beforeAll(() => {
  const getter = (key: 'sw' | 'cw' | 'sl') => function (this: HTMLElement) {
    return geom.get(this)?.[key] ?? 0;
  };
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: getter('sw') });
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: getter('cw') });
  Object.defineProperty(HTMLElement.prototype, 'scrollLeft', {
    configurable: true,
    get: getter('sl'),
    set(this: HTMLElement, v: number) { const g = geom.get(this); if (g) g.sl = v; },
  });
});

// setup.ts 里 ResizeObserver 是不可 delete 的 defineProperty，所以只能存回来重新赋值
const originalRO = (globalThis as any).ResizeObserver;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  FakeRO.reset();
  (globalThis as any).ResizeObserver = originalRO;
});

/**
 * ResizeObserver 替身：jsdom 没有实现，组件按"无 RO 就退回 scroll/resize 事件"处理，
 * 于是这里显式装上并手动回调，钉住"布局变了必须重算"这条时机（否则箭头可能永远不出现）。
 */
class FakeRO {
  static callbacks: Array<() => void> = [];
  private cb: () => void;
  constructor(cb: () => void) { this.cb = cb; FakeRO.callbacks.push(cb); }
  observe() {} unobserve() {}
  disconnect() { FakeRO.callbacks = FakeRO.callbacks.filter(c => c !== this.cb); }
  static install() { (globalThis as any).ResizeObserver = FakeRO; }
  static fire() { FakeRO.callbacks.forEach(cb => cb()); }
  static reset() { FakeRO.callbacks = []; }
}

function Harness({ ids, currentId, onSelect }: {
  ids: number[]; currentId: number; onSelect?: (id: number) => void;
}) {
  const { tabs } = useDashboardNavigation(
    ids.map(id => ({ id, name: `看板${id}` })), currentId, onSelect);
  return <div>{tabs}</div>;
}

describe('看板切换条 — 横向溢出', () => {
  it('只有一个看板时不渲染切换条（也没有多余的翻页按钮）', () => {
    render(<Harness ids={[1]} currentId={1} />);
    expect(screen.queryByRole('navigation', { name: '同组看板' })).toBeNull();
    expect(screen.queryByRole('button', { name: '向后翻看板' })).toBeNull();
  });

  it('未溢出时不渲染翻页按钮（不留点了没反应的死开关）', () => {
    render(<Harness ids={[1, 2]} currentId={1} />);
    expect(screen.queryByRole('button', { name: '向后翻看板' })).toBeNull();
  });

  it('溢出时常驻左右按钮：未滚动只能往后翻，滚到底只能往前翻', () => {
    const { container } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 0);
    fireEvent.scroll(nav);

    expect(screen.getByRole('button', { name: '向前翻看板' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '向后翻看板' })).toBeEnabled();

    setNavGeom(nav, 1200, 400, 800);      // 已滚到最右（800 = 1200-400）
    fireEvent.scroll(nav);
    expect(screen.getByRole('button', { name: '向前翻看板' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '向后翻看板' })).toBeDisabled();
  });

  it('点翻页按钮只滚容器（按可视宽度 60% 步进），不动整页', () => {
    const { container } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 0);
    const spy = vi.fn();
    (nav as any).scrollBy = spy;
    fireEvent.scroll(nav);

    fireEvent.click(screen.getByRole('button', { name: '向后翻看板' }));
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ left: 240 }));  // 400×0.6
  });

  it('切换看板时把选中项滚进可视区（点后半段不再"选中了却看不见"）', () => {
    const { container, rerender } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 0);
    const spy = vi.fn();
    (nav as any).scrollBy = spy;
    // 容器可视区 0~400；看板3 的 pill 落在 500~640（完全在右侧视野外）
    // rect 按**看板名**定位而不是取当时的 aria-current：rerender 后选中项才换成看板3
    stubNavBox(nav, 400);
    stubRect(screen.getByRole('button', { name: '看板3' }), 500, 640);

    spy.mockClear();
    rerender(<Harness ids={[1, 2, 3]} currentId={3} />);
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ left: 252 }));  // 640-(400-12)
  });

  it('选中项已在可视区内时不做任何滚动（不"每次切换都大幅滑一下"）', () => {
    const { container, rerender } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 0);
    const spy = vi.fn();
    (nav as any).scrollBy = spy;
    stubNavBox(nav, 400);
    stubRect(screen.getByRole('button', { name: '看板3' }), 100, 240);   // 已在 0~400 内

    spy.mockClear();
    rerender(<Harness ids={[1, 2, 3]} currentId={3} />);
    expect(spy).not.toHaveBeenCalled();
  });

  it('点击 pill 仍走 onSelect（溢出改造没有动选择链路）', () => {
    const onSelect = vi.fn();
    render(<Harness ids={[1, 2]} currentId={1} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: '看板2' }));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  // ── 滚轮：隐藏滚动条 + 只能横滚的容器，鼠标滚轮默认什么都不做 ──────────────
  // fireEvent.wheel 只回“是否未被取消”，拿 defaultPrevented 必须自己造事件再 dispatch
  it('纵向滚轮在溢出条上转成横向滚动（不让后半段看板只能靠拖）', () => {
    const { container } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 0);

    const evt = createEvent.wheel(nav, { deltaY: 120 });
    fireEvent(nav, evt);
    expect(evt.defaultPrevented).toBe(true);   // 接管了，页面不再跟着纵向滚
    expect(nav.scrollLeft).toBe(120);
  });

  it('滚到端点不再拦截：放行给页面继续纵向滚（不做滚不动的死胡同）', () => {
    const { container } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 800);           // 已到底（800 = 1200-400）

    const evt = createEvent.wheel(nav, { deltaY: 120 });
    fireEvent(nav, evt);
    expect(evt.defaultPrevented).toBe(false);
    expect(nav.scrollLeft).toBe(800);
  });

  it('未溢出时不接管滚轮（切换条不该抢走整页的纵向滚动）', () => {
    const { container } = render(<Harness ids={[1, 2]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 400, 400, 0);

    const evt = createEvent.wheel(nav, { deltaY: 120 });
    fireEvent(nav, evt);
    expect(evt.defaultPrevented).toBe(false);
    expect(nav.scrollLeft).toBe(0);
  });

  it('已有横向输入或 ctrl 缩放手势时不抢滚轮（交回浏览器原生行为）', () => {
    const { container } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 0);

    const evt = createEvent.wheel(nav, { deltaX: 200, deltaY: 20 });
    fireEvent(nav, evt);
    expect(evt.defaultPrevented).toBe(false);
    expect(nav.scrollLeft).toBe(0);
  });

  it('line 模式滚轮按行换算成像素（Firefox deltaY 不是像素，直接累加会几乎滚不动）', () => {
    const { container } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 0);

    fireEvent.wheel(nav, { deltaY: 3, deltaMode: 1 });
    expect(nav.scrollLeft).toBe(48);            // 3 行 × 16px
  });

  // ── 布局变化后重算（真实浏览器里 scrollWidth 常晚于首帧才稳定）──────────────
  it('ResizeObserver 回调后补出翻页按钮：首帧测成"没溢出"不会把看板永久藏起来', () => {
    FakeRO.install();
    const { container } = render(<Harness ids={[1, 2, 3]} currentId={1} />);
    const nav = container.querySelector('nav') as HTMLElement;
    expect(screen.queryByRole('button', { name: '向后翻看板' })).toBeNull();

    // 字体加载完/pill 变宽：几何此时才溢出
    setNavGeom(nav, 1200, 400, 0);
    act(() => { FakeRO.fire(); });
    expect(screen.getByRole('button', { name: '向后翻看板' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '向前翻看板' })).toBeDisabled();
  });

  it('窗口变化后选中 pill 仍被带回可视区（不只在切换那一下才滚）', () => {
    FakeRO.install();
    const { container } = render(<Harness ids={[1, 2, 3]} currentId={3} />);
    const nav = container.querySelector('nav') as HTMLElement;
    setNavGeom(nav, 1200, 400, 0);
    const spy = vi.fn();
    (nav as any).scrollBy = spy;
    stubNavBox(nav, 400);
    stubRect(screen.getByRole('button', { name: '看板3' }), -200, -60);   // 整颗在左侧视野外

    spy.mockClear();
    act(() => { FakeRO.fire(); });
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ left: -212 }));  // -200-(0+12)
  });
});
