/**
 * 登录页品牌块 = 品牌设置的消费面 —— 回归锁定
 *
 * 历史缺陷：登录页把应用名与标语写死在页面里（图标还是一个写死的 Database 图标），
 * 管理员在「系统配置 → 品牌设置」改名 / 换图标 / 改标语，登录页一个字都不动，
 * 同一件品牌事实出现第二套真值源。本文件把口径钉死：登录页只渲染品牌快照。
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
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

const brand = (over: Partial<BrandSettings> = {}): BrandSettings => ({
  app_name: 'AI-DataHub', logo_url: '', show_icon: true, show_text: true,
  tagline: DEFAULT_TAGLINE, ...over,
});

const renderLogin = () => render(<MemoryRouter initialEntries={['/login']}><Login /></MemoryRouter>);

/** 品牌接口只被登录页拉一次，返回值即登录页应显示的品牌事实 */
const stubBrandFetch = (data: BrandSettings) => {
  vi.mocked(client.get).mockImplementation(((url: string) =>
    url === '/admin/brand' ? Promise.resolve({ data }) : Promise.resolve({ data: [] })) as never);
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useBrandStore.setState({ brand: brand(), loaded: false });
});
afterEach(() => { cleanup(); });

describe('登录页品牌块跟随品牌设置', () => {
  it('挂载即拉品牌设置（不靠页面里的写死文案撑首屏）', async () => {
    stubBrandFetch(brand());
    renderLogin();
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/admin/brand'));
  });

  it('改名后登录页标题区与页脚版权一起跟着变，旧硬编码名不再出现', async () => {
    stubBrandFetch(brand({ app_name: '数智中枢' }));
    renderLogin();
    await waitFor(() => expect(screen.getAllByText('数智中枢').length).toBeGreaterThan(0));
    expect(screen.queryByText(DEFAULT_APP_NAME)).not.toBeInTheDocument();
    // 页脚「© 年份 名称」同样取品牌名
    expect(screen.getByText(new RegExp(`© \\d{4} 数智中枢`))).toBeInTheDocument();
  });

  it('改标语后登录页副标题跟着变', async () => {
    stubBrandFetch(brand({ tagline: '让每一次提问都有可信答案' }));
    renderLogin();
    await waitFor(() => expect(screen.getByText('让每一次提问都有可信答案')).toBeInTheDocument());
    expect(screen.queryByText(DEFAULT_TAGLINE)).not.toBeInTheDocument();
  });

  it('标语被显式清空 = 不显示，不回落默认文案（与「还没加载」区分开）', async () => {
    stubBrandFetch(brand({ tagline: '' }));
    renderLogin();
    await waitFor(() => expect(screen.getAllByText(DEFAULT_APP_NAME).length).toBeGreaterThan(0));
    expect(screen.queryByText(DEFAULT_TAGLINE)).not.toBeInTheDocument();
  });

  it('上传图标后渲染图片而不是派生字标方块', async () => {
    stubBrandFetch(brand({ logo_url: 'data:image/png;base64,LOGO' }));
    renderLogin();
    await waitFor(() => expect(screen.getAllByRole('img', { name: '品牌图标' }).length).toBeGreaterThan(0));
    const img = screen.getAllByRole('img', { name: '品牌图标' })[0] as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('data:image/png;base64,LOGO');
  });

  it('show_text=false 只留图标标，名称与标语都不出现', async () => {
    stubBrandFetch(brand({ show_text: false }));
    renderLogin();
    await waitFor(() => expect(screen.getAllByText('AD').length).toBeGreaterThan(0));
    expect(screen.queryByText(DEFAULT_APP_NAME)).not.toBeInTheDocument();
    expect(screen.queryByText(DEFAULT_TAGLINE)).not.toBeInTheDocument();
  });

  it('show_icon=false 只留文字（未上传 logo 时不画方块）', async () => {
    stubBrandFetch(brand({ show_icon: false }));
    renderLogin();
    await waitFor(() => expect(screen.getAllByText(DEFAULT_APP_NAME).length).toBeGreaterThan(0));
    expect(screen.queryByRole('img', { name: '品牌图标' })).not.toBeInTheDocument();
    expect(screen.queryByText('AD')).not.toBeInTheDocument();
  });

  it('品牌接口失败时按默认品牌渲染（首屏不能空着），并留下可诊断告警', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(client.get).mockRejectedValue(new Error('network down') as never);
    renderLogin();
    await waitFor(() => expect(screen.getAllByText(DEFAULT_APP_NAME).length).toBeGreaterThan(0));
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('品牌设置读取失败'), expect.anything());
    warn.mockRestore();
  });
});
