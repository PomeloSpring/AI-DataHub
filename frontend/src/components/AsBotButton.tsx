import { Bot } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAsBotStore } from '../stores/asBotStore';

/**
 * AS-BOT header button — 在 SectionSwitcher 左侧显示.
 * 所有用户可见（继承角色 Waker 能力）。
 */
export default function AsBotButton() {
  const { panelOpen, togglePanel, pendingApprovalCount } = useAsBotStore();

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
          {pendingApprovalCount > 0 && (
            <Badge
              variant="destructive"
              className="absolute -top-1 -right-1 h-4 min-w-[16px] px-1 text-[10px] flex items-center justify-center"
            >
              {pendingApprovalCount}
            </Badge>
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>智能助手</TooltipContent>
    </Tooltip>
  );
}
