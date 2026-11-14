import { useNavigate } from 'react-router-dom';
import { LayoutGrid, Folder, Database, Settings, Check, House, Newspaper } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuTrigger, DropdownMenuLabel, DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAuthStore } from '../stores/authStore';
import { useWorkspaceStore } from '../stores/workspaceStore';
import { useMenuAccess } from '../hooks/useMenuAccess';
import { workspaceLandingPath } from './WorkspaceRoute';
import { TOUR } from '@/lib/onboarding/guides';

export type SectionId = 'home' | 'content' | 'workspace' | 'data' | 'system' | 'ask';

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
    // 工作空间落点口径与登录落地同源（默认页 = 智能问数）
    navigate(workspaceLandingPath(getDefaultWorkspaceId()));
  };
  const goContent = () => navigate('/content');
  const goData = () => navigate('/data');
  const goSystem = () => navigate('/system');

  const isAdmin = user?.role === 'admin';
  // 模块级一级入口按权限显隐: 模块内没有任何可见菜单就不提供入口。
  // 「数据看板」不再是独立模块——已收进工作空间侧栏(日常消费入口, 对所有登录用户恒可见,
  //  不参与权限码显隐; 看板内容级可见性由 adh_role_dashboard_access 在数据层裁决)。
  // 订阅 store 且未就绪时不给入口: 否则会把无权限模块先列出来再抽走(闪现)
  const menuAccess = useMenuAccess();
  const showWorkspace = menuAccess.ready && menuAccess.prefixAllowed('workspace:');
  const showData = menuAccess.ready && menuAccess.prefixAllowed('data:');
  // 内容中心: 只做内容创作/审核/发布, 入口按权限码反查(无任何 content: 菜单可看就不给
  // 入口), 与其它模块同口径——不在这里硬编码"恒可见"。资讯门户是另一个模块。
  const showContent = menuAccess.ready && menuAccess.prefixAllowed('content:');

  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label="切换模块" data-tour={TOUR.headerModuleSwitcher}>
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
        {showContent && <DropdownMenuItem
          onClick={goContent}
          className={`flex items-center gap-2 ${current === 'content' ? 'bg-accent' : ''}`}
        >
          <Newspaper className="h-4 w-4" />
          <span className="flex-1">内容中心</span>
          {current === 'content' && <Check className="h-4 w-4 text-primary" />}
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
