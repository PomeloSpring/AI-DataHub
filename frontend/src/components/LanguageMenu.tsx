import { Languages } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import LanguageMenuItems from './LanguageMenuItems';

/**
 * 语言切换的唯一实现处（顶栏常驻 + 登录页右上角复用同一组件）。
 * 用户拍板 2026-11-14：从用户下拉移到顶栏，与主题按钮并排，四套布局同口径；
 * 2026-11-27：去掉鼠标悬浮提示 —— 按钮 aria-label 与下拉首行标签都已写明「语言」，
 * 悬浮浮层是重复噪音（主题按钮的 Tooltip 保留，它没有等价的可见文字）。
 * 选项复用 LanguageMenuItems（原生语言名，不随当前语言翻译）。
 */
export default function LanguageMenu() {
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label={t('language.label')}>
          <Languages className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <LanguageMenuItems />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}