/**
 * 品牌方块唯一渲染入口 —— 防"每个布局各画一套"回归
 * 历史缺陷：5 个布局 + 首页/文章页各写一遍内联片段（size-6/text-xs/text-[10px] 互不一致），
 * 品牌设置页预览还写着「CB」，与浏览器标签图标（写死 #3B82F6 的 vite.svg）三种画风。
 * 现在几何与字标都取自 lib/brand，与标签图标同源。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import BrandLogo from '../BrandLogo';
import { markFontSize, markRadius } from '@/lib/brand';

describe('BrandLogo', () => {
  it('未上传 logo：渲染主题主色方块，字标由应用名派生', () => {
    const { container } = render(<BrandLogo brand={{ app_name: 'AI-DataHub', logo_url: '', show_icon: true }} size={24} />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.textContent).toBe('AD');
    expect(el.className).toContain('bg-primary');
    expect(el.className).toContain('text-primary-foreground');
    expect(el.style.width).toBe('24px');
    expect(el.style.borderRadius).toBe(`${markRadius(24)}px`);
    expect(el.style.fontSize).toBe(`${markFontSize(24)}px`);
  });

  it('改名后字标跟着变（不再写死 AD/CB）', () => {
    const { container } = render(<BrandLogo brand={{ app_name: '数智平台' }} />);
    expect(container.textContent).toBe('数');
  });

  it('上传 logo：渲染图片而不是方块', () => {
    render(<BrandLogo brand={{ app_name: 'AI-DataHub', logo_url: 'data:image/png;base64,AA' }} size={28} />);
    const img = screen.getByRole('img', { name: '品牌图标' }) as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('data:image/png;base64,AA');
    expect(img.style.width).toBe('28px');
  });

  it('show_icon=false 时页内不渲染（标签图标照常，由 lib/brand 保证）', () => {
    const { container } = render(<BrandLogo brand={{ show_icon: false, logo_url: 'data:image/png;base64,AA' }} />);
    expect(container.firstChild).toBeNull();
  });

  it('品牌未加载时按默认品牌渲染，不出空白方块', () => {
    const { container } = render(<BrandLogo />);
    expect(container.textContent).toBe('AD');
  });

  it('onPrimary（主色底上的登录页左栏）：反色方块，不得再用主色底', () => {
    const { container } = render(<BrandLogo brand={{ app_name: 'AI-DataHub', logo_url: '' }} size={44} tone="onPrimary" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain('bg-primary-foreground');
    expect(el.className).toContain('text-primary');
    // 默认 tone 的那套（主色方块 + 主色前景字）落在主色渐变上会糊成一片
    expect(el.className).not.toContain('text-primary-foreground');
    expect(el.style.width).toBe('44px');
  });

  it('onPrimary 下的图片 logo 有白色承托（深色 logo 不致主色底上看不见）', () => {
    render(<BrandLogo brand={{ logo_url: 'data:image/png;base64,AA' }} size={44} tone="onPrimary" />);
    const img = screen.getByRole('img', { name: '品牌图标' });
    expect(img.className).toContain('bg-primary-foreground');
  });
});
