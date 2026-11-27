/**
 * AS-BOT 面板滚动跟随口径（回归锁定）
 *
 * 与工作空间 Chat 同一口径（判定唯一实现处 hooks/useStickToBottom）：
 * 上翻查看历史/后台 3s 轮询刷新转录时**不把人拽回底部**，只有自己发出新提问才主动跳底。
 *
 * 历史缺陷：这里原来是无条件 `scrollRef.current.scrollTop = scrollRef.current.scrollHeight`
 * （每次 messages 变化都跳底），本文件同时用源码级护栏钉住它不许回来。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import AsBotPanel from '../AsBotPanel';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useAsBotStore } from '../../../stores/asBotStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: [] }), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('../../MarkdownWithCharts', () => ({ default: () => null }));
vi.mock('../../ProcessTimeline', () => ({ default: () => null }));
vi.mock('../../ScreenPreviewEntry', () => ({ TurnScreenPreview: () => null, default: () => null }));

const SCROLL_HEIGHT = 2000;
const CLIENT_HEIGHT = 600;
const AT_BOTTOM_TOP = SCROLL_HEIGHT - CLIENT_HEIGHT;

const scrollTo = vi.fn();

/** 面板头部用了 Tooltip，真应用里由 main.tsx 全局提供 TooltipProvider，用例同口径包一层 */
const mountPanel = () => render(
  <TooltipProvider><MemoryRouter><AsBotPanel /></MemoryRouter></TooltipProvider>,
);

/** 取消息区那个滚动容器（页面上不止一处 overflow-y-auto，按内容认）。
 * 标记用**用户提问**原文：助手正文走 MarkdownWithCharts（本用例桩成 null），
 * 用户气泡是直接渲染的 <p>，只有它一定在 DOM 里。 */
const MSG_MARKER = '帮我看下系统能力';

function getScrollBox(): HTMLElement {
  const boxes = [...document.querySelectorAll('div.overflow-y-auto')] as HTMLElement[];
  const box = boxes.find(b => b.textContent?.includes(MSG_MARKER));
  expect(box, '面板必须渲染消息滚动容器').toBeTruthy();
  return box!;
}

/** 注入几何并把用户置到指定位置（jsdom 无布局，同 TurnNavigator.test.tsx 口径） */
function placeUser(box: HTMLElement, atBottom: boolean) {
  const geo = { scrollTop: atBottom ? AT_BOTTOM_TOP : 0 };
  Object.defineProperty(box, 'scrollHeight', { configurable: true, get: () => SCROLL_HEIGHT });
  Object.defineProperty(box, 'clientHeight', { configurable: true, get: () => CLIENT_HEIGHT });
  Object.defineProperty(box, 'scrollTop', {
    configurable: true,
    get: () => geo.scrollTop,
    set: (v: number) => { geo.scrollTop = v; },
  });
  act(() => { box.dispatchEvent(new Event('scroll')); });
}

function setMessages(messages: any[]) {
  act(() => { useAsBotStore.setState({ messages } as never); });
}

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollTo = scrollTo as never;
  useAsBotStore.setState({
    panelOpen: true, conversationId: 5, loading: true, stopping: false,
    messages: [{ role: 'user', content: '帮我看下系统能力' }, { role: 'assistant', content: '在的，正在整理' }],
  } as never);
});
afterEach(() => { cleanup(); });

describe('AS-BOT 面板滚动跟随', () => {
  it('上翻后轮询刷新转录（同长度改正文）不滚到底部', () => {
    mountPanel();
    placeUser(getScrollBox(), false);
    scrollTo.mockClear();
    setMessages([
      { role: 'user', content: '帮我看下系统能力' },
      { role: 'assistant', content: '在的，正在整理：本体建模、指标查询、看板生成' },
    ]);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('上翻后自己发出新提问 → 平滑滚到底并恢复跟随', () => {
    mountPanel();
    placeUser(getScrollBox(), false);
    scrollTo.mockClear();
    setMessages([
      { role: 'user', content: '帮我看下系统能力' },
      { role: 'assistant', content: '在的，正在整理' },
      { role: 'user', content: '再列一下已有本体' },
    ]);
    expect(scrollTo).toHaveBeenCalledWith({ top: SCROLL_HEIGHT, behavior: 'smooth' });
  });

  it('贴底时转录增长照常跟随', () => {
    mountPanel();
    placeUser(getScrollBox(), true);
    scrollTo.mockClear();
    setMessages([
      { role: 'user', content: '帮我看下系统能力' },
      { role: 'assistant', content: '在的，正在整理结果' },
    ]);
    expect(scrollTo).toHaveBeenCalledWith({ top: SCROLL_HEIGHT, behavior: 'auto' });
  });

  it('上翻时给出「有新内容」回程按钮', () => {
    mountPanel();
    expect(screen.queryByRole('button', { name: '有新内容' })).not.toBeInTheDocument();
    placeUser(getScrollBox(), false);
    expect(screen.getByRole('button', { name: '有新内容' })).toBeInTheDocument();
  });

  it('源码护栏：不得回到无条件 scrollTop = scrollHeight，判定必须走 useStickToBottom', () => {
    const src = readFileSync(resolve(process.cwd(), 'src/components/asbot/AsBotPanel.tsx'), 'utf-8');
    expect(src).not.toMatch(/scrollTop\s*=\s*\w+\.scrollHeight/);
    expect(src).toContain('useStickToBottom');
  });
});
