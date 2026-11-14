import { Check, Languages } from 'lucide-react';
import { DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { useTranslation } from 'react-i18next';
import { LOCALES, setLocale, type Locale } from '@/i18n';

/**
 * 语言切换菜单选项的唯一实现处（顶栏 LanguageMenu 下拉内），与 ThemeMenuItems 同构。
 * 选项显示原生语言名（简体中文 / English）——不随当前语言翻译，
 * 否则切到英文后找不到"切回中文"的入口（语言选择器通行惯例）。
 */
export const LOCALE_NATIVE_NAMES: Record<Locale, string> = {
  'zh-CN': '简体中文',
  'en-US': 'English',
};

export default function LanguageMenuItems() {
  const { i18n, t } = useTranslation();
  return (
    <>
      <DropdownMenuLabel className="flex items-center gap-2 text-xs font-normal text-muted-foreground">
        <Languages className="h-3.5 w-3.5" />
        {t('language.label')}
      </DropdownMenuLabel>
      <DropdownMenuSeparator />
      {LOCALES.map(lng => (
        <DropdownMenuItem
          key={lng}
          onClick={() => setLocale(lng)}
          className={`flex items-center gap-3 ${i18n.language === lng ? 'bg-accent' : ''}`}
        >
          <div className="flex-1 min-w-0">
            <div className={`text-sm ${i18n.language === lng ? 'font-medium' : ''}`}>
              {LOCALE_NATIVE_NAMES[lng]}
            </div>
          </div>
          {i18n.language === lng && <Check className="h-4 w-4 text-primary" />}
        </DropdownMenuItem>
      ))}
    </>
  );
}