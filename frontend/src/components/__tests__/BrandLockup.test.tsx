/**
 * BrandLockup（图标 + 名称 + 标语）—— 品牌设置消费面的唯一组合入口
 * 锁定两件事：① 文案全部来自品牌快照（页面不再自带第二套）；
 * ② 显示开关与标语"显式清空"的语义（'' = 不显示，undefined = 还没加载才回落默认）。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import BrandLockup from '../BrandLockup';
import { DEFAULT_APP_NAME, DEFAULT_TAGLINE } from '@/lib/brand';

describe('BrandLockup', () => {
  it('默认渲染：方块字标 + 名称 + 标语', () => {
    const { container } = render(<BrandLockup brand={{ app_name: 'AI-DataHub', logo_url: '', tagline: DEFAULT_TAGLINE }} />);
    expect(container.textContent).toContain('AD');
    expect(screen.getByText(DEFAULT_APP_NAME)).toBeInTheDocument();
    expect(screen.getByText(DEFAULT_TAGLINE)).toBeInTheDocument();
  });

  it('品牌还没拉到（brand 缺省）也按默认品牌渲染，不出空白块', () => {
    const { container } = render(<BrandLockup />);
    expect(container.textContent).toContain('AD');
    expect(screen.getByText(DEFAULT_APP_NAME)).toBeInTheDocument();
  });

  it('改名后名称、标语、字标三处一起跟着变', () => {
    render(<BrandLockup brand={{ app_name: '数智中枢', tagline: '让提问有可信答案' }} />);
    expect(screen.getByText('数智中枢')).toBeInTheDocument();
    expect(screen.getByText('让提问有可信答案')).toBeInTheDocument();
    expect(screen.getByText('数')).toBeInTheDocument();
  });

  it('tagline 显式清空 = 不显示标语行（不回落默认文案）', () => {
    render(<BrandLockup brand={{ app_name: 'AI-DataHub', tagline: '' }} />);
    expect(screen.queryByText(DEFAULT_TAGLINE)).not.toBeInTheDocument();
    expect(screen.getByText(DEFAULT_APP_NAME)).toBeInTheDocument();
  });

  it('showTagline=false 只显图标与名称（窄屏头部）', () => {
    render(<BrandLockup brand={{ tagline: DEFAULT_TAGLINE }} showTagline={false} />);
    expect(screen.queryByText(DEFAULT_TAGLINE)).not.toBeInTheDocument();
    expect(screen.getByText(DEFAULT_APP_NAME)).toBeInTheDocument();
  });

  it('show_text=false 只剩方块；show_icon=false 只剩文字（与侧栏同口径）', () => {
    const textOff = render(<BrandLockup brand={{ show_text: false, app_name: 'AI-DataHub', tagline: DEFAULT_TAGLINE }} />);
    expect(textOff.container.textContent).toBe('AD');
    textOff.unmount();

    const iconOff = render(<BrandLockup brand={{ show_icon: false, app_name: 'AI-DataHub', tagline: DEFAULT_TAGLINE }} />);
    expect(iconOff.container.querySelector('span')).toBeNull();
    expect(iconOff.container.textContent).toBe(`AI-DataHub${DEFAULT_TAGLINE}`);
  });

  it('上传 logo 时渲染图片，标语行仍在（图片不吞掉文字）', () => {
    render(<BrandLockup brand={{ app_name: 'AI-DataHub', logo_url: 'data:image/svg+xml,AAA', tagline: DEFAULT_TAGLINE }} />);
    expect(screen.getByRole('img', { name: '品牌图标' })).toBeInTheDocument();
    expect(screen.getByText(DEFAULT_TAGLINE)).toBeInTheDocument();
  });

  it('onPrimary 把反色画法透传给方块（主色底上的登录页左栏）', () => {
    const { container } = render(<BrandLockup brand={{ app_name: 'AI-DataHub' }} tone="onPrimary" />);
    expect((container.firstElementChild!.firstElementChild as HTMLElement).className)
      .toContain('bg-primary-foreground');
  });
});
