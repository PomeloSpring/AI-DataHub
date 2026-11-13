/**
 * 401 回登录页的重载循环护栏 —— 回归锁定
 *
 * 历史缺陷：响应拦截器在 token 刷新失败时无条件 `location.href = '/login'`。
 * 登录页自身发出的请求（fetchBrand，或陈旧 token 触发的 refreshUser）拿到 401 时，
 * 此刻已在 /login，再跳 /login = 整页重载 → 重新挂载又发同一请求 → 又 401 → 无限刷新。
 * 修复：已在 /login 时只清凭据、把错误抛回，绝不重载；其它页面才跳登录。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import client from '../client';

function stubLocation(pathname: string) {
  let assigned = '';
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      pathname,
      get href() { return assigned || `http://localhost${pathname}`; },
      set href(v: string) { assigned = v; },
    },
  });
  return () => assigned;
}

// 让所有请求都回 401（模拟后端拒绝），并保留 axios 错误形状供拦截器读取
function failAllWith401() {
  client.defaults.adapter = (config: any) => {
    const err: any = new Error('Request failed with status code 401');
    err.config = config;
    err.response = { status: 401, data: {}, config };
    return Promise.reject(err);
  };
}

describe('401 拦截器：登录页不再整页重载', () => {
  const realLocation = window.location;
  const realAdapter = client.defaults.adapter;

  beforeEach(() => {
    localStorage.clear();
    failAllWith401();
  });
  afterEach(() => {
    Object.defineProperty(window, 'location', { configurable: true, value: realLocation });
    client.defaults.adapter = realAdapter;
    vi.restoreAllMocks();
  });

  it('已在 /login：401 且刷新失败时不写 location.href（打断重载循环），仍清 token', async () => {
    localStorage.setItem('token', 'stale');
    const href = stubLocation('/login');
    await expect(client.get('/admin/brand')).rejects.toBeTruthy();
    expect(href()).toBe(''); // 从未被赋值 → 不触发整页跳转
    expect(localStorage.getItem('token')).toBeNull(); // 陈旧凭据被清掉
  });

  it('非登录页：401 且刷新失败时跳 /login', async () => {
    localStorage.setItem('token', 'expired');
    const href = stubLocation('/data/dashboards');
    await expect(client.get('/dashboards')).rejects.toBeTruthy();
    expect(href()).toBe('/login');
  });

  it('刷新请求本身 401 且在 /login：同样不重载', async () => {
    localStorage.setItem('refresh_token', 'r');
    const href = stubLocation('/login');
    await expect(client.post('/auth/refresh', { refresh_token: 'r' })).rejects.toBeTruthy();
    expect(href()).toBe('');
  });
});
