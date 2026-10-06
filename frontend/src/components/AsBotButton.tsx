import { Bot } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAsBotStore } from '../stores/asBotStore';

/**
 * AS-BOT header button — 在 SectionSwitcher 左侧显示.
 * 所有用户可见（继承角色 AS-BOT 能力）。
 */
export default function AsBotButton() {
  const { panelOpen, togglePanel } = useAsBotStore();

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={`h-9 w-9 p-0 relative ${panelOpen ? 'bg-primary/10 text-primary' : ''}`}
          onClick={togglePanel}
          aria-label="智能助手"
        >
          <Bot className="h-4 w-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>智能助手</TooltipContent>
    </Tooltip>
  );
}
