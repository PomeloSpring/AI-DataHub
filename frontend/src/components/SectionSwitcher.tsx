import { useNavigate } from 'react-router-dom';
import { LayoutGrid, Folder, Database, Settings, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuTrigger, DropdownMenuLabel, DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAuthStore } from '../stores/authStore';
import { useWorkspaceStore } from '../stores/workspaceStore';

export type SectionId = 'workspace' | 'data' | 'system';

interface SectionSwitcherProps {
  /** Currently active section — used to highlight the matching item. */
  current: SectionId;
}

/**
 * Header-level module switcher.
 *
 * Provides a single dropdown button that navigates between the three top-level
 * sections of the app: workspace (工作空间), data platform (数据中台) and
 * system config (系统配置). Replaces the previous bottom-of-sidebar switcher
 * so users can jump between sections without reaching for the sidebar footer.
 */
export default function SectionSwitcher({ current }: SectionSwitcherProps) {
  const navigate = useNavigate();
  const { user } = useAuthStore();
  const { getDefaultWorkspaceId, currentWorkspaceId } = useWorkspaceStore();

  const goWorkspace = () => {
    const wsId = currentWorkspaceId || getDefaultWorkspaceId();
    navigate(`/ws/${wsId}/chat`);
  };
  const goData = () => navigate('/data');
  const goSystem = () => navigate('/system');

  const isAdmin = user?.role === 'admin';

  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label="切换模块">
              <LayoutGrid className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>切换模块</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
          模块切换
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={goWorkspace}
          className={`flex items-center gap-2 ${current === 'workspace' ? 'bg-accent' : ''}`}
        >
          <Folder className="h-4 w-4" />
          <span className="flex-1">工作空间</span>
          {current === 'workspace' && <Check className="h-4 w-4 text-primary" />}
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={goData}
          className={`flex items-center gap-2 ${current === 'data' ? 'bg-accent' : ''}`}
        >
          <Database className="h-4 w-4" />
          <span className="flex-1">数据中台</span>
          {current === 'data' && <Check className="h-4 w-4 text-primary" />}
        </DropdownMenuItem>
        {isAdmin && (
          <DropdownMenuItem
            onClick={goSystem}
            className={`flex items-center gap-2 ${current === 'system' ? 'bg-accent' : ''}`}
          >
            <Settings className="h-4 w-4" />
            <span className="flex-1">系统配置</span>
            {current === 'system' && <Check className="h-4 w-4 text-primary" />}
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
