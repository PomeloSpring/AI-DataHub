import { useEffect } from 'react';
import { Bot } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAsBotStore } from '../stores/asBotStore';
import { useAuthStore } from '../stores/authStore';

/**
 * AS-BOT header button — 在 SectionSwitcher 左侧显示.
 * 仅当用户角色有 AS-BOT 访问权限时渲染.
 */
export default function AsBotButton() {
  const { panelOpen, togglePanel, pendingApprovalCount, loadPermissions, permissions, permissionsLoaded } = useAsBotStore();
  const { user } = useAuthStore();

  useEffect(() => {
    if (!permissionsLoaded) {
      loadPermissions();
    }
  }, [permissionsLoaded, loadPermissions]);

  // 权限检查: admin 始终可用, 其他角色需 can_access
  const canAccess = permissions?.can_access ?? (user?.role === 'admin');

  // 未加载完权限前不渲染, 避免闪烁
  if (!permissionsLoaded) return null;
  if (!canAccess) return null;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={`h-9 w-9 p-0 relative ${panelOpen ? 'bg-primary/10 text-primary' : ''}`}
          onClick={togglePanel}
          aria-label="AS-BOT 系统助手"
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
      <TooltipContent>AS-BOT 系统助手</TooltipContent>
    </Tooltip>
  );
}
