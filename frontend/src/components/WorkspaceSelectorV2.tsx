import { useState, useEffect } from 'react';
import { Folder, Plus, Settings, Check, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Badge } from '@/components/ui/badge';
import { useNavigate } from 'react-router-dom';
import client from '../api/client';

interface Workspace {
  id: number;
  name: string;
  description: string;
  icon: string;
  color: string;
  is_default: boolean;
  user_default: boolean;
  role: string;
}

interface WorkspaceSelectorV2Props {
  className?: string;
}

export default function WorkspaceSelectorV2({ className }: WorkspaceSelectorV2Props) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [currentWorkspace, setCurrentWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    loadWorkspaces();
  }, []);

  const loadWorkspaces = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/workspaces');
      setWorkspaces(data || []);

      // Load current workspace from localStorage
      const savedWsId = localStorage.getItem('currentWorkspace');
      if (savedWsId && data) {
        const ws = data.find((w: Workspace) => w.id === Number(savedWsId));
        if (ws) {
          setCurrentWorkspace(ws);
          return;
        }
      }

      // Default to user's default workspace
      if (data && data.length > 0) {
        const defaultWs = data.find((w: Workspace) => w.user_default) || data[0];
        setCurrentWorkspace(defaultWs);
        localStorage.setItem('currentWorkspace', String(defaultWs.id));
      }
    } catch (error) {
      console.error('Failed to load workspaces:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSwitchWorkspace = (ws: Workspace) => {
    setCurrentWorkspace(ws);
    localStorage.setItem('currentWorkspace', String(ws.id));
    // Reload page to refresh all data
    window.location.reload();
  };

  const handleManageWorkspaces = () => {
    navigate('/workspaces');
  };

  if (!currentWorkspace) {
    return (
      <Button variant="outline" size="sm" className={className} disabled>
        <Folder className="h-4 w-4 mr-1.5" />
        加载中...
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className={`${className} min-w-[180px] justify-between`}>
          <div className="flex items-center gap-1.5 truncate">
            <span className="text-base">{currentWorkspace.icon}</span>
            <span className="truncate">{currentWorkspace.name}</span>
          </div>
          <ChevronDown className="h-4 w-4 ml-1.5 shrink-0 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {workspaces.map((ws) => (
          <DropdownMenuItem
            key={ws.id}
            onClick={() => handleSwitchWorkspace(ws)}
            className="flex items-center gap-2"
          >
            <span className="text-base">{ws.icon}</span>
            <div className="flex-1 min-w-0">
              <div className="truncate">{ws.name}</div>
              {ws.description && (
                <div className="text-xs text-muted-foreground truncate">{ws.description}</div>
              )}
            </div>
            {ws.id === currentWorkspace.id && (
              <Check className="h-4 w-4 shrink-0 text-primary" />
            )}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={handleManageWorkspaces}>
          <Settings className="h-4 w-4 mr-2" />
          管理工作空间
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// Export hook for other components to use
export function useCurrentWorkspace() {
  const [workspaceId, setWorkspaceId] = useState<number>(() => {
    const saved = localStorage.getItem('currentWorkspace');
    return saved ? Number(saved) : 0;
  });

  useEffect(() => {
    const handleStorageChange = () => {
      const saved = localStorage.getItem('currentWorkspace');
      setWorkspaceId(saved ? Number(saved) : 0);
    };

    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, []);

  return workspaceId;
}
