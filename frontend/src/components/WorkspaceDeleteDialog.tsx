import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/toast';
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
 * 不做"点了就删"的静默级联；后端对默认工作空间一律 409，这里同样不把删除
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

/** 影响清单行：只存 key，文案在渲染期经 t() 取（模块顶层常量会冻结语言，切语言不刷新） */
const IMPACT_KEYS: { key: keyof WorkspaceDeleteImpact; labelKey: 'delete.impact.conversations' | 'delete.impact.dashboards' | 'delete.impact.scheduledTasks' | 'delete.impact.dagWorkflows' | 'delete.impact.savedQueries' }[] = [
  { key: 'conversations', labelKey: 'delete.impact.conversations' },
  { key: 'dashboards', labelKey: 'delete.impact.dashboards' },
  { key: 'scheduled_tasks', labelKey: 'delete.impact.scheduledTasks' },
  { key: 'dag_workflows', labelKey: 'delete.impact.dagWorkflows' },
  { key: 'saved_queries', labelKey: 'delete.impact.savedQueries' },
];

export function WorkspaceDeleteDialog({ workspace, open, onOpenChange, onDeleted }: {
  workspace: DeletableWorkspace | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 删除成功后的回调（父级刷新清单/跳转），抛错不影响提示 */
  onDeleted?: (workspaceId: number) => void | Promise<void>;
}) {
  const { t } = useTranslation(['workspace', 'common']);
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
        toast.error(e.response?.data?.detail || t('delete.impactFailed'));
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
      toast.success(t('delete.deleted', { name: workspace.name }));
      const deletedId = workspace.id;
      onOpenChange(false);
      await onDeleted?.(deletedId);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('common:state.deleteFailed'));
    } finally {
      setDeleting(false);
    }
  };

  const rows = impact ? IMPACT_KEYS.filter(item => Number(impact[item.key]) > 0) : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('delete.title')}</DialogTitle>
          <DialogDescription>
            {t('delete.description', { name: workspace?.name ?? '' })}
          </DialogDescription>
        </DialogHeader>
        <div className="text-sm space-y-2">
          {loading ? (
            <p className="text-muted-foreground">{t('delete.impactLoading')}</p>
          ) : impact ? (
            rows.length === 0
              ? <p className="text-muted-foreground">{t('delete.impactEmpty')}</p>
              : (
                <ul className="space-y-1">
                  {rows.map(item => (
                    <li key={item.key} className="flex justify-between gap-4">
                      <span>{t(item.labelKey)}</span>
                      <span className="font-medium">{t('delete.itemCount', { count: Number(impact[item.key]) })}</span>
                    </li>
                  ))}
                </ul>
              )
          ) : null}
          {impact && impact.disk_usage_bytes > 0 && (
            <p className="text-xs text-muted-foreground">
              {t('delete.diskCleanup', { size: formatBytes(impact.disk_usage_bytes) })}
            </p>
          )}
          <label className="flex items-start gap-2 pt-1 text-sm cursor-pointer">
            <input
              type="checkbox" aria-label={t('delete.ackAria')} checked={ack}
              onChange={e => setAck(e.target.checked)}
              className="mt-0.5 accent-destructive"
            />
            <span>{t('delete.ackLabel')}</span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('common:action.cancel')}</Button>
          <Button
            variant="destructive" onClick={handleDelete}
            disabled={deleting || !ack || loading || !impact}
          >
            {deleting ? t('delete.deleting') : t('delete.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
