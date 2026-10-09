import { useNavigate } from 'react-router-dom';
import { LayoutGrid, Folder, Database, Settings, Check, ChartNoAxesCombined, House } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuTrigger, DropdownMenuLabel, DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAuthStore } from '../stores/authStore';
import { useWorkspaceStore } from '../stores/workspaceStore';
import { isMenuPrefixAllowed } from '../stores/permissionStore';

export type SectionId = 'home' | 'workspace' | 'data' | 'system' | 'ask' | 'dashboards';

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
  const { getDefaultWorkspaceId } = useWorkspaceStore();

  const goWorkspace = () => {
    const wsId = getDefaultWorkspaceId();
    navigate(wsId ? `/ws/${wsId}/chat` : '/workspace');
  };
  const goData = () => navigate('/data');
  const goSystem = () => navigate('/system');

  const isAdmin = user?.role === 'admin';
  // 模块级一级入口按权限显隐: 模块内没有任何可见菜单就不提供入口。
  // 例外:「数据看板」是普通用户日常消费入口, 对所有登录用户恒可见(产品决策 2026-10),
  //  不参与权限码显隐 —— 路由 /dashboards 仅由 PrivateRoute 守卫, 看板内容级
  //  可见性由 adh_role_dashboard_access 在数据层裁决, 与入口显隐解耦。
  // 其余模块(工作空间/数据中台/系统配置)继续按权限码/菜单显隐, 不放宽。
  const showWorkspace = isMenuPrefixAllowed('workspace:');
  const showData = isMenuPrefixAllowed('data:');

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
        <DropdownMenuItem onClick={() => navigate('/')} className={`flex items-center gap-2 ${current === 'home' ? 'bg-accent' : ''}`}>
          <House className="h-4 w-4" /><span className="flex-1">首页</span>{current === 'home' && <Check className="h-4 w-4 text-primary" />}
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => navigate('/dashboards')}
          className={`flex items-center gap-2 ${current === 'dashboards' ? 'bg-accent' : ''}`}
        >
          <ChartNoAxesCombined className="h-4 w-4" />
          <span className="flex-1">数据看板</span>
          {current === 'dashboards' && <Check className="h-4 w-4 text-primary" />}
        </DropdownMenuItem>
        {showWorkspace && <DropdownMenuItem
          onClick={goWorkspace}
          className={`flex items-center gap-2 ${current === 'workspace' ? 'bg-accent' : ''}`}
        >
          <Folder className="h-4 w-4" />
          <span className="flex-1">工作空间</span>
          {current === 'workspace' && <Check className="h-4 w-4 text-primary" />}
        </DropdownMenuItem>}
        {showData && <DropdownMenuItem
          onClick={goData}
          className={`flex items-center gap-2 ${current === 'data' ? 'bg-accent' : ''}`}
        >
          <Database className="h-4 w-4" />
          <span className="flex-1">数据中台</span>
          {current === 'data' && <Check className="h-4 w-4 text-primary" />}
        </DropdownMenuItem>}
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
