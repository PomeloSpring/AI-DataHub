/**
 * 品牌组合标（图标 + 名称 + 标语）—— 登录页与品牌设置预览的唯一渲染入口。
 *
 * 历史缺陷：登录页把应用名与品牌标语两行文案直接写死在页面里，
 * 管理员在「系统配置 → 品牌设置」改名、换图标、改标语，登录页一个字都不动 ——
 * 同一件品牌事实出现第二套真值源（品牌口径只能来自 adh_brand_settings）。
 * 现在名称/标语/图标/显示开关全部取自品牌快照，方块渲染仍走 BrandLogo
 * （与浏览器标签图标同一组几何常量与主题令牌）。
 *
 * 显示开关与侧栏同口径：show_icon=false 只去方块，show_text=false 只去文字（含标语），
 * 两者由调用方（品牌设置页）保证至少留一项。
 */
import BrandLogo from './BrandLogo';
import { cn } from '@/lib/utils';
import { brandNameOf, taglineOf } from '@/lib/brand';

export interface BrandLockupProps {
  /** 品牌设置快照（app_name / logo_url / tagline / show_icon / show_text）；未加载时按默认品牌渲染 */
  brand?: {
    app_name?: string;
    logo_url?: string;
    tagline?: string;
    show_icon?: boolean;
    show_text?: boolean;
  };
  /** 方块边长 px */
  size?: number;
  /** onPrimary：主色底上的反色画法（登录页左栏），详见 BrandLogo */
  tone?: 'default' | 'onPrimary';
  /** 是否显示标语行（窄屏头部空间紧，只显名称时传 false） */
  showTagline?: boolean;
  nameClassName?: string;
  taglineClassName?: string;
  className?: string;
}

export default function BrandLockup({
  brand,
  size = 24,
  tone = 'default',
  showTagline = true,
  nameClassName,
  taglineClassName,
  className,
}: BrandLockupProps) {
  const showText = brand?.show_text !== false;
  const tagline = showTagline ? taglineOf(brand?.tagline) : '';
  return (
    <div className={cn('flex items-center gap-2.5 min-w-0', className)}>
      <BrandLogo brand={brand} size={size} tone={tone} />
      {showText && (
        <div className="min-w-0">
          <div className={cn('truncate text-base font-bold leading-tight tracking-tight', nameClassName)}>
            {brandNameOf(brand?.app_name)}
          </div>
          {tagline && (
            <div className={cn('truncate text-[11px] leading-tight opacity-70 mt-0.5', taglineClassName)}>
              {tagline}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
