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
/** 距底 80px 以内算贴底（阈值真值源 hooks/useStickToBottom.NEAR_BOTTOM_PX） */
const AT_BOTTOM_TOP = SCROLL_HEIGHT - CLIENT_HEIGHT;

/** 会话正文里始终存在的标记（用它认出“消息区那个” viewport） */
const CONV_MARKER = '你好';

const scrollTo = vi.fn();

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

/**
 * 注入几何并把用户置到指定位置（jsdom 无布局，同 TurnNavigator.test.tsx 口径：
 * scrollTop 接到可变 state 上，再派发 scroll 事件让监听器读到事实）。
 */
function placeUser(vp: HTMLElement, atBottom: boolean) {
  const geo = { scrollTop: atBottom ? AT_BOTTOM_TOP : 0 };
  Object.defineProperty(vp, 'scrollHeight', { configurable: true, get: () => SCROLL_HEIGHT });
  Object.defineProperty(vp, 'clientHeight', { configurable: true, get: () => CLIENT_HEIGHT });
  Object.defineProperty(vp, 'scrollTop', {
    configurable: true,
    get: () => geo.scrollTop,
    set: (v: number) => { geo.scrollTop = v; },
  });
  act(() => { vp.dispatchEvent(new Event('scroll')); });
}

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

  it('非生成态文案是「回到底部」，贴底时不渲染', () => {
    act(() => { useChatStore.setState({ loading: false } as never); });
    mount();
    expect(screen.queryByRole('button', { name: '回到底部' })).not.toBeInTheDocument();
    placeUser(getViewport(), false);
    expect(screen.getByRole('button', { name: '回到底部' })).toBeInTheDocument();
  });
});
