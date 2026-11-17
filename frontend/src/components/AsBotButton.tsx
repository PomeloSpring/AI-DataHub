import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import BotAvatar from './chat/BotAvatar';
import { useAsBotStore } from '../stores/asBotStore';
import { useTranslation } from 'react-i18next';

/**
 * AS-BOT header button — 在 SectionSwitcher 左侧显示.
 * 所有用户可见（继承角色 AS-BOT 能力）。
 */
export default function AsBotButton() {
  const { t } = useTranslation('ui');
  const { panelOpen, togglePanel } = useAsBotStore();

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={`h-9 w-9 p-0 relative ${panelOpen ? 'bg-primary/10 text-primary' : ''}`}
          onClick={togglePanel}
          aria-label={t("k_7c11c1b2ad")}
        >
          <BotAvatar size={22} active={panelOpen} />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{t("k_7c11c1b2ad")}</TooltipContent>
    </Tooltip>
  );
}
