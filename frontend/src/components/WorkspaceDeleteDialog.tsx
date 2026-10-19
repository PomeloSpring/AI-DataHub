import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import client from '@/api/client';
import { formatBytes } from '@/api/monitoring';

/**
 * 工作空间删除确认框（下拉切换器与管理员统管页共用）。
 *
 * 打开时先拉影响清单（GET /workspaces/{id}/delete-impact）再让人确认——
 * 不做"点了就删"的静默级联；后端对默认工作站一律 409，这里同样不把删除
 * 入口摆给默认空间（见 WorkspaceMenuItem / WorkspaceManagerV2）。
 */

export interface DeletableWorkspace {
  id: number;
  name: string;
}

/** 与后端 workspace_deletion.get_delete_impact 对齐 */
export interface WorkspaceDeleteImpact {
  workspace_id: number;
  workspace_name: string;
  is_default: number;
  deletable: boolean;
  disk_usage_bytes: number;
  conversations: number;
  dashboards: number;
  scheduled_tasks: number;
  dag_workflows: number;
  saved_queries: number;
}

const IMPACT_LABELS: { key: keyof WorkspaceDeleteImpact; label: string }[] = [
  { key: 'conversations', label: '会话' },
  { key: 'dashboards', label: '数据看板' },
  { key: 'scheduled_tasks', label: '定时任务' },
  { key: 'dag_workflows', label: '工作流' },
  { key: 'saved_queries', label: '保存查询/数据集' },
];

export function WorkspaceDeleteDialog({ workspace, open, onOpenChange, onDeleted }: {
  workspace: DeletableWorkspace | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 删除成功后的回调（父级刷新清单/跳转），抛错不影响提示 */
  onDeleted?: (workspaceId: number) => void | Promise<void>;
}) {
  const [impact, setImpact] = useState<WorkspaceDeleteImpact | null>(null);
  const [loading, setLoading] = useState(false);
  const [ack, setAck] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!open || !workspace) {
      setImpact(null);
      setAck(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    client.get<WorkspaceDeleteImpact>(`/workspaces/${workspace.id}/delete-impact`)
      .then(({ data }) => { if (!cancelled) setImpact(data); })
      .catch((e: any) => {
        toast.error(e.response?.data?.detail || '获取删除影响清单失败，请重试');
        if (!cancelled) onOpenChange(false);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, workspace?.id]);

  const handleDelete = async () => {
    if (!workspace) return;
    setDeleting(true);
    try {
      await client.delete(`/workspaces/${workspace.id}`);
      toast.success(`工作空间「${workspace.name}」已删除`);
      const deletedId = workspace.id;
      onOpenChange(false);
      await onDeleted?.(deletedId);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  const rows = impact ? IMPACT_LABELS.filter(item => Number(impact[item.key]) > 0) : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>删除工作空间</DialogTitle>
          <DialogDescription>
            将永久删除「{workspace?.name}」及其中的工作站内容，此操作不可恢复。
            已归档到资产清单的文件跟随账号保留，不受影响。
          </DialogDescription>
        </DialogHeader>
        <div className="text-sm space-y-2">
          {loading ? (
            <p className="text-muted-foreground">正在统计影响内容…</p>
          ) : impact ? (
            rows.length === 0
              ? <p className="text-muted-foreground">该空间内暂无会话、看板与任务。</p>
              : (
                <ul className="space-y-1">
                  {rows.map(item => (
                    <li key={item.key} className="flex justify-between gap-4">
                      <span>{item.label}</span>
                      <span className="font-medium">{Number(impact[item.key])} 项</span>
                    </li>
                  ))}
                </ul>
              )
          ) : null}
          {impact && impact.disk_usage_bytes > 0 && (
            <p className="text-xs text-muted-foreground">
              同时清理会话工作区文件 {formatBytes(impact.disk_usage_bytes)}
            </p>
          )}
          <label className="flex items-start gap-2 pt-1 text-sm cursor-pointer">
            <input
              type="checkbox" aria-label="确认永久删除工作空间" checked={ack}
              onChange={e => setAck(e.target.checked)}
              className="mt-0.5 accent-destructive"
            />
            <span>我已知晓上述内容将被永久删除且无法恢复</span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button
            variant="destructive" onClick={handleDelete}
            disabled={deleting || !ack || loading || !impact}
          >
            {deleting ? '删除中...' : '永久删除'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
