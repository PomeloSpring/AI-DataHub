/**
 * 聊天滚动跟随口径（回归锁定）
 *
 * 期望行为（聊天软件惯例，用户拍板 2026-11-27）：向上滚动查看以前的内容时，
 * 即使还在生成也**不要**主动滚到底部；只有自己发出新问题才主动滚到底。
 *
 * 历史缺陷两处（口径由来见 hooks/useStickToBottom 头注）：
 * 1. 贴底监听 effect 依赖为 `[]`，而 ScrollArea 是条件挂载的 → 监听器永远没绑上，
 *    判定恒为"贴底"，流式每帧把人拽回底部；
 * 2. 新消息分支只看 messages.length 增加就强制跳底，助手消息入列也算"新消息"。
 * 本文件按用户可见行为钉死这几条。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Chat from '../Chat';
import { useChatStore } from '../../stores/chatStore';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}));
// 本用例只关心滚动行为，桩掉重型渲染依赖保持轻量（与 ChatStopButton.test.tsx 同一清单）
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }));
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }));
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }));
vi.mock('../../components/ChartPicker', () => ({ default: () => null }));
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }));

const user = (content: string) => ({ role: 'user', content });
const ai = (content: string) => ({ role: 'assistant', content });

/** 滚动几何（注入值，与下方 defineProperty 对应） */
const SCROLL_HEIGHT = 2000;
const CLIENT_HEIGHT = 600;

/** 会话正文里始终存在的标记（用它认出“消息区那个” viewport） */
const CONV_MARKER = '你好';

const scrollTo = vi.fn(function (this: HTMLElement, opts?: { top?: number; behavior?: string }) {
  // 桩成真实浏览器行为：把 scrollTop 落到目标并派发回声事件。
  // 回声必须成对消费，否则 hook 会把下一次用户滚动当成回声吞掉。
  const geo = (this as unknown as { __geo?: { scrollTop: number } }).__geo;
  if (!geo || typeof opts?.top !== 'number') return;
  geo.scrollTop = Math.min(opts.top, SCROLL_HEIGHT - CLIENT_HEIGHT);
  this.dispatchEvent(new Event('scroll'));
});

/**
 * 取消息区的 ScrollArea viewport。
 * 页面上不止一个 ScrollArea（会话列表侧栏也用），document 首个匹配是侧栏那个——
 * 拿错元素等于给另一个容器注入几何，判定永远读不到（历史上就这么误判过）。
 */
function getViewport(): HTMLElement {
  const boxes = [...document.querySelectorAll('[data-radix-scroll-area-viewport]')] as HTMLElement[];
  const vp = boxes.find(b => b.textContent?.includes(CONV_MARKER));
  expect(vp, '消息区必须渲染 ScrollArea viewport').toBeTruthy();
  return vp!;
}

/** 给元素注入几何（只做一次，把 scrollTop 接到可变 state 上） */
function geoOf(box: HTMLElement): { scrollTop: number } {
  const held = (box as unknown as { __geo?: { scrollTop: number } }).__geo;
  if (held) return held;
  const geo = { scrollTop: 0 };
  Object.defineProperty(box, 'scrollHeight', { configurable: true, get: () => SCROLL_HEIGHT });
  Object.defineProperty(box, 'clientHeight', { configurable: true, get: () => CLIENT_HEIGHT });
  Object.defineProperty(box, 'scrollTop', {
    configurable: true,
    get: () => geo.scrollTop,
    set: (v: number) => { geo.scrollTop = v; },
  });
  (box as unknown as { __geo: typeof geo }).__geo = geo;
  return geo;
}

/** 把用户放到“距底部 gap 像素”的位置（jsdom 无布局，同 TurnNavigator.test.tsx 口径） */
function placeGap(box: HTMLElement, gap: number) {
  const geo = geoOf(box);
  act(() => {
    geo.scrollTop = SCROLL_HEIGHT - CLIENT_HEIGHT - gap;
    box.dispatchEvent(new Event('scroll'));
  });
}

const placeUser = (box: HTMLElement, atBottom: boolean) => placeGap(box, atBottom ? 0 : 1400);

function mount() {
  render(<MemoryRouter><Chat /></MemoryRouter>);
  // 挂载那一帧走的是"打开会话 → 瞬时定位到底部"分支，与本文件断言无关，清掉记录
  scrollTo.mockClear();
}

/** 改 store 里的消息（流式增长=同长度改正文；追加=变长度） */
function pushMessages(messages: any[]) {
  act(() => { useChatStore.setState({ messages } as never); });
}

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollTo = scrollTo as never;
  useChatStore.setState({
    messages: [user('你好'), ai('在的，我是助手')],
    currentConvId: 1, loading: true, stopping: false,
  } as never);
});
afterEach(() => { cleanup(); });

describe('生成中上翻查看历史：不被拽回底部', () => {
  it('流式正文继续增长时不滚动', () => {
    mount();
    placeUser(getViewport(), false);
    scrollTo.mockClear();
    pushMessages([user('你好'), ai('在的，我是助手，正在为你查询本季度的销售数据与同比情况')]);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('思考/工具过程行增长同样不滚动（同一判定，不分事件种类）', () => {
    mount();
    placeUser(getViewport(), false);
    scrollTo.mockClear();
    pushMessages([user('你好'), { role: 'assistant', content: '在的，我是助手', process: [{ step: 1, text: '检索语义层' }] }]);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('助手消息入列（本轮开始生成时那条占位）不把上翻的人拽下去', () => {
    mount();
    pushMessages([user('你好')]);
    placeUser(getViewport(), false);
    scrollTo.mockClear();
    pushMessages([user('你好'), ai('')]);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('轻微上翻（距底 150px）不弹回程按钮，但也不再自动跟随', () => {
    mount();
    placeGap(getViewport(), 150);
    expect(screen.queryByRole('button', { name: '有新内容' })).not.toBeInTheDocument();
    scrollTo.mockClear();
    pushMessages([user('你好'), ai('在的，我是助手，正在为你查询本季度的销售数据')]);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('只翻 20px（仍在 80px 阈值内）也算上翻：方向优先，立即停止跟随', () => {
    mount();
    const vp = getViewport();
    placeGap(vp, 100);
    placeGap(vp, 40);        // 往下滚到距底 40px：重新挂上跟随
    scrollTo.mockClear();
    pushMessages([user('你好'), ai('在的，我是助手，结果快出来了')]);
    expect(scrollTo).toHaveBeenCalled();      // 对照组：阈值内确实会跟随

    placeGap(vp, 60);        // 往上翻 20px（仍在 80px 阈值内）
    scrollTo.mockClear();
    pushMessages([user('你好'), ai('在的，我是助手，结果快出来了，已关联三个数据源')]);
    expect(scrollTo).not.toHaveBeenCalled();
  });
});

describe('自己发出新问题：主动滚到底', () => {
  it('上翻后追加 role=user 的新提问 → 平滑滚到底并恢复跟随', () => {
    mount();
    placeUser(getViewport(), false);
    scrollTo.mockClear();
    pushMessages([user('你好'), ai('在的，我是助手'), user('那查一下本季度销售额')]);
    expect(scrollTo).toHaveBeenCalledWith({ top: SCROLL_HEIGHT, behavior: 'smooth' });

    // 恢复跟随后，本轮流式内容继续增长要照常跟随
    scrollTo.mockClear();
    pushMessages([user('你好'), ai('在的，我是助手'), user('那查一下本季度销售额'), ai('正在查询')]);
    expect(scrollTo).toHaveBeenCalled();
  });

  it('本来就贴底时流式增长照常跟随（跟随能力没有因修复而回退）', () => {
    mount();
    placeUser(getViewport(), true);
    scrollTo.mockClear();
    pushMessages([user('你好'), ai('在的，我是助手，结果已出')]);
    expect(scrollTo).toHaveBeenCalledWith({ top: SCROLL_HEIGHT, behavior: 'auto' });
  });
});

describe('回到底部按钮：回程交给用户按', () => {
  it('上翻后出现按钮，点击即滚到底、按钮消失并恢复跟随', () => {
    mount();
    placeUser(getViewport(), false);
    // loading 中：文案换成「有新内容」，说清下面还在长东西
    const btn = screen.getByRole('button', { name: '有新内容' });
    scrollTo.mockClear();
    fireEvent.click(btn);
    expect(scrollTo).toHaveBeenCalledWith({ top: SCROLL_HEIGHT, behavior: 'smooth' });
    expect(screen.queryByRole('button', { name: '有新内容' })).not.toBeInTheDocument();

    scrollTo.mockClear();
    pushMessages([user('你好'), ai('在的，我是助手，正在汇总结果')]);
    expect(scrollTo).toHaveBeenCalled();
  });

  it('非生成态也有回程按钮（只有箭头，文案只进无障碍名），贴底时不渲染', () => {
    act(() => { useChatStore.setState({ loading: false } as never); });
    mount();
    expect(screen.queryByRole('button', { name: '回到底部' })).not.toBeInTheDocument();
    placeUser(getViewport(), false);
    const btn = screen.getByRole('button', { name: '回到底部' });
    expect(btn).toBeInTheDocument();
    // 不写可见文字：按钮里只有一个箭头图标，没任何文本节点
    expect(btn.textContent).toBe('');
    expect(btn.querySelector('svg')).toBeTruthy();
    // 形态（用户拍板）：圆形主题色底，与发送钮同一个 Button 默认变体
    expect(btn.className).toContain('rounded-full');
    expect(btn.className).toContain('bg-primary');
  });
});
