import { Languages } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import LanguageMenuItems from './LanguageMenuItems';

/**
 * 顶栏语言切换（用户拍板 2026-11-14：从用户下拉移到顶栏，与主题按钮并排，
 * 四套布局同口径）。与主题选择器同构：图标按钮 + Tooltip + 下拉；
 * 选项复用 LanguageMenuItems（原生语言名，不随当前语言翻译）。
 */
export default function LanguageMenu() {
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label={t('language.label')}>
              <Languages className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>{t('language.label')}</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-48">
        <LanguageMenuItems />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}