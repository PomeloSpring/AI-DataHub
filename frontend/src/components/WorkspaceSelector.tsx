import React, { useEffect, useState } from 'react';
import { Folder, Settings } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { listWorkspaces, Workspace } from '../api/workspace';

interface WorkspaceSelectorProps {
  value?: number;
  onChange?: (workspaceId: number, workspace: Workspace) => void;
  className?: string;
  showManage?: boolean;
  onManage?: () => void;
}

const WORKSPACE_TYPE_LABELS: Record<string, { label: string; variant: 'default' | 'secondary' | 'outline' }> = {
  data_analysis: { label: '数据分析', variant: 'default' },
  log_analysis: { label: '日志分析', variant: 'secondary' },
  ops: { label: '综合运维', variant: 'outline' },
  custom: { label: '自定义', variant: 'outline' },
};

const WorkspaceSelector: React.FC<WorkspaceSelectorProps> = ({
  value,
  onChange,
  className,
  showManage = true,
  onManage,
}) => {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | undefined>(value);

  useEffect(() => {
    loadWorkspaces();
  }, []);

  useEffect(() => {
    if (value !== undefined) {
      setSelectedId(value);
    }
  }, [value]);

  const loadWorkspaces = async () => {
    setLoading(true);
    try {
      const data = await listWorkspaces();
      setWorkspaces(data);

      // If no value provided, select default workspace
      if (!value && data.length > 0) {
        const defaultWs = data.find((w) => w.is_default) || data[0];
        setSelectedId(defaultWs.id);
        onChange?.(defaultWs.id, defaultWs);
      }
    } catch (error) {
      console.error('Failed to load workspaces:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleChange = (wsIdStr: string) => {
    const wsId = Number(wsIdStr);
    if (wsId === -1) {
      // Manage workspaces
      onManage?.();
      return;
    }

    setSelectedId(wsId);
    const workspace = workspaces.find((w) => w.id === wsId);
    if (workspace) {
      onChange?.(wsId, workspace);
    }
  };

  return (
    <Select
      value={selectedId ? String(selectedId) : undefined}
      onValueChange={handleChange}
      disabled={loading}
    >
      <SelectTrigger className={className || 'w-[200px] h-8'}>
        <Folder className="h-3.5 w-3.5 mr-1.5" />
        <SelectValue placeholder={loading ? '加载中...' : '选择工作空间'} />
      </SelectTrigger>
      <SelectContent>
        {workspaces.map((ws) => {
          const typeInfo = WORKSPACE_TYPE_LABELS[ws.workspace_type] || WORKSPACE_TYPE_LABELS.custom;
          return (
            <SelectItem key={ws.id} value={String(ws.id)}>
              <div className="flex items-center justify-between w-full">
                <span>
                  <span className="mr-2">{ws.icon}</span>
                  {ws.name}
                </span>
                <Badge variant={typeInfo.variant} className="ml-2 text-xs">
                  {typeInfo.label}
                </Badge>
              </div>
            </SelectItem>
          );
        })}
        {showManage && (
          <SelectItem value="-1">
            <div className="text-primary">
              <Settings className="h-3.5 w-3.5 inline mr-1" />
              管理工作空间
            </div>
          </SelectItem>
        )}
      </SelectContent>
    </Select>
  );
};

export default WorkspaceSelector;
