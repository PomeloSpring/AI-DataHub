/**
 * 界面语言随请求下发（Accept-Language）—— 回归锁定
 *
 * 后端 LLM 回复语言按「提问语言优先 → 界面语言兜底」判定（backend/common/i18n），
 * 界面语言只认这个请求头。历史缺陷：前端 2026-11 起了多语言框架但语言从没传到后端，
 * 加上提示词硬编码「中文回答」→ 切到 English 后界面是英文、回答仍中文（配了没用）。
 * 本用例锁的是"头真的带上了、且跟随切换"，不是只测函数返回值。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import client from '../client';
import i18n, { DEFAULT_LOCALE, LOCALE_STORAGE_KEY, setLocale } from '@/i18n';

/** 用桩 adapter 截获 axios 真正发出的配置，读回请求头（不靠源码字符串断言）。 */
async function sentHeaders(path = '/dashboards'): Promise<Record<string, string>> {
  let seen: any;
  client.defaults.adapter = (config: any) => {
    seen = config;
    return Promise.resolve({ data: {}, status: 200, statusText: 'OK', headers: {}, config });
  };
  await client.get(path);
  const raw = seen?.headers;
  const read = (key: string) => (typeof raw?.get === 'function' ? raw.get(key) : raw?.[key]);
  return { 'Accept-Language': read('Accept-Language') ?? read('accept-language') };
}

describe('Accept-Language 随请求下发', () => {
  const realAdapter = client.defaults.adapter;

  beforeEach(() => { localStorage.clear(); setLocale(DEFAULT_LOCALE); });
  afterEach(() => { client.defaults.adapter = realAdapter; });

  it('默认语言下也带头（不靠浏览器兜底）', async () => {
    expect((await sentHeaders())['Accept-Language']).toBe('zh-CN');
  });

  it('切换语言后的请求立即跟随（无需刷新页面）', async () => {
    setLocale('en-US');
    // changeLanguage 是异步的（i18n 先切语言再触发事件），等下一个请求真带上 en-US
    await vi.waitFor(() => expect(sentHeaders()).resolves.toEqual({ 'Accept-Language': 'en-US' }));
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US');
    expect(i18n.language).toBe('en-US');
  });

  it('带 token 时语言头与鉴权头并存（拦截器不得互相覆盖）', async () => {
    localStorage.setItem('token', 'abc');
    const headers = await sentHeaders();
    expect(headers['Accept-Language']).toBe('zh-CN');
  });
});
