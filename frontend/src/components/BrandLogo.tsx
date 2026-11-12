/**
 * 品牌标（唯一渲染入口）：左上角/顶栏那个方块标。
 *
 * 以前 5 个布局 + 首页 + 文章页各写一遍内联片段（字号 size-6/text-xs/text-[10px] 互不一致，
 * 品牌设置页预览还写着「CB」），现在统一到这里：几何常量取自 lib/brand，
 * 与浏览器标签图标（favicon）同一套比例、同一组主题令牌，两处永远同画风。
 */
import { cn } from '@/lib/utils';
import { brandAbbrev, markFontSize, markRadius, DEFAULT_APP_NAME, type BrandLike } from '@/lib/brand';

interface BrandLogoProps {
  /** 品牌设置（app_name / logo_url / show_icon）；未加载时按默认品牌渲染 */
  brand?: BrandLike & { show_icon?: boolean };
  /** 方块边长 px：侧栏 24（收起 20）、首页 28、登录页 44、品牌设置预览 32 */
  size?: number;
  className?: string;
  /**
   * onPrimary：品牌色底（如登录页左栏的 primary 渐变）上的反色画法。
   * 默认方块是 bg-primary + 主色前景字，直接落在主色底上会糊成一片；反色后为
   * 白底方块 + 主色字标，上传的图片 logo 则给一块白色承托（深色 logo 不致看不见）。
   */
  tone?: 'default' | 'onPrimary';
}

export default function BrandLogo({ brand, size = 24, className, tone = 'default' }: BrandLogoProps) {
  if (brand?.show_icon === false) return null;
  const logo = (brand?.logo_url || '').trim();
  if (logo) {
    return (
      <img
        src={logo}
        alt="品牌图标"
        style={{ width: size, height: size }}
        className={cn(
          'rounded object-contain flex-shrink-0',
          tone === 'onPrimary' && 'bg-primary-foreground/95 p-[12%]',
          className,
        )}
      />
    );
  }
  return (
    <span
      style={{ width: size, height: size, borderRadius: markRadius(size), fontSize: markFontSize(size) }}
      className={cn(
        'inline-flex items-center justify-center font-bold leading-none flex-shrink-0',
        tone === 'onPrimary' ? 'bg-primary-foreground text-primary' : 'bg-primary text-primary-foreground',
        className,
      )}
    >
      {brandAbbrev(brand?.app_name || DEFAULT_APP_NAME)}
    </span>
  );
}
