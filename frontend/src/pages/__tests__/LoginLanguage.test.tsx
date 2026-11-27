/**
 * 登录页语言切换（用户拍板 2026-11-27：登录页也要放一份）—— 回归锁定
 *
 * 锁三件事：
 * 1. 登录页有语言入口，且复用顶栏同一个 LanguageMenu（选项仍是原生语言名，不另写一套）；
 * 2. 切换必须**立即**反映到登录页自身文案，含左侧特性清单 —— 隐患是清单写成模块级常量
 *    会在 import 时冻结成首屏语言，切完仍显示旧文案（「存了不生效」= 缺陷）；
 * 3. 语言按钮不得有鼠标悬浮浮层：aria-label 与下拉首行标签都已写明「语言」，浮层是重复噪音。
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import i18n, { LOCALE_STORAGE_KEY } from '@/i18n';
import Login from '../Login';
import { DEFAULT_APP_NAME, DEFAULT_TAGLINE } from '@/lib/brand';
import { useBrandStore, type BrandSettings } from '@/stores/brandStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const brand: BrandSettings = {
  app_name: DEFAULT_APP_NAME, logo_url: '', show_icon: true, show_text: true,
  tagline: DEFAULT_TAGLINE,
};

const renderLogin = () => render(<MemoryRouter initialEntries={['/login']}><Login /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useBrandStore.setState({ brand, loaded: true });
  vi.mocked(client.get).mockImplementation(((url: string) =>
    url === '/admin/brand' ? Promise.resolve({ data: brand }) : Promise.resolve({ data: [] })) as never);
});
afterEach(() => {
  cleanup();
  void i18n.changeLanguage('zh-CN');
});

describe('登录页语言切换入口', () => {
  it('登录页渲染语言按钮，下拉是原生语言名（与顶栏同组件、同选项）', async () => {
    renderLogin();
    await userEvent.click(await screen.findByRole('button', { name: '语言' }));
    expect(await screen.findByText('简体中文')).toBeInTheDocument();
    expect(screen.getByText('English')).toBeInTheDocument();
  });

  it('在登录页切到 English：写持久化 + 页面文案立即变英文（特性清单不得冻结在首屏语言）', async () => {
    renderLogin();
    await userEvent.click(await screen.findByRole('button', { name: '语言' }));
    await userEvent.click(await screen.findByText('English'));

    await waitFor(() => expect(i18n.language).toBe('en-US'));
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US');

    // 表单区与品牌叙事区同时换语言（切完还留中文 = 该文案冻结在 import 时）
    expect(await screen.findByText('Welcome back')).toBeInTheDocument();
    expect(screen.queryByText('欢迎回来')).not.toBeInTheDocument();
    expect(screen.getByText('Natural-language query')).toBeInTheDocument();
    expect(screen.queryByText('自然语言查询')).not.toBeInTheDocument();
    // 按钮 aria-label 随语言，回到顶栏同一套语言包 key
    expect(screen.getByRole('button', { name: 'Language' })).toBeInTheDocument();
  });

  it('语言按钮不弹鼠标悬浮提示（aria-label 已写明「语言」，浮层是重复噪音）', async () => {
    renderLogin();
    const trigger = await screen.findByRole('button', { name: '语言' });
    await userEvent.hover(trigger);
    await new Promise((r) => setTimeout(r, 120));
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });
});
