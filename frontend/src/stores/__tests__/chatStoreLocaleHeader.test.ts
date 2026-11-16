/**
 * Chat SSE 请求也带界面语言 —— 回归锁定
 *
 * 为什么单独锁：SSE 走 `fetch` 不经过 axios 的请求拦截器，语言必须在那两个发送入口
 * （`/api/pipeline/send/stream` 与 `/api/chat/send/stream`）显式带上。漏带 = 后端只能靠
 * 提问语言判定，英文界面下用中文提问依旧回中文，切换语言对回答毫无影响（配置面有、消费端没有）。
 * 两个 store（Chat 与 AS-BOT 端点）同一条口径，这里都覆盖。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useChatStore } from '../chatStore';
import { useAsBotStore } from '../asBotStore';
import { DEFAULT_LOCALE, setLocale } from '@/i18n';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
vi.mock('../../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

/** 截获 fetch 调用；流立即结束（只关心请求头带没带语言）。 */
function captureFetch() {
  const calls: Array<{ url: string; headers: Record<string, string> }> = [];
  vi.stubGlobal('fetch', vi.fn(async (url: any, init: any) => {
    calls.push({ url: String(url), headers: (init?.headers ?? {}) as Record<string, string> });
    return {
      ok: true,
      status: 200,
      body: { getReader: () => ({ read: async () => ({ done: true, value: undefined }), releaseLock: () => {} }) },
    };
  }));
  return calls;
}

describe('SSE 发送入口带 Accept-Language', () => {
  beforeEach(() => { localStorage.clear(); setLocale(DEFAULT_LOCALE); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('Chat 主链路（两个端点都要带上语言）', async () => {
    const calls = captureFetch();
    useChatStore.setState({ currentConvId: 41, messages: [], loading: false, stopping: false, abortController: null } as any);
    await useChatStore.getState().sendMessage('hello world');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['Accept-Language']).toBe('zh-CN');

    setLocale('en-US');
    useChatStore.setState({ currentConvId: 41, messages: [], loading: false, stopping: false, abortController: null } as any);
    await useChatStore.getState().sendMessage('how many orders last week');
    expect(calls[1].headers['Accept-Language']).toBe('en-US');
  });

  it('AS-BOT 端点同样带上语言', async () => {
    const calls = captureFetch();
    useAsBotStore.setState({ conversationId: 42, messages: [], loading: false, abortController: null } as any);
    await useAsBotStore.getState().sendMessage('hello');
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain('/api/pipeline/send/stream');
    expect(calls[0].headers['Accept-Language']).toBe('zh-CN');
    expect(calls[0].headers['Content-Type']).toBe('application/json');
  });
});
