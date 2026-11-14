import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import {
  Plus,
  Play,
  History,
  Edit,
  Trash2,
  RefreshCw,
  Copy,
} from 'lucide-react';
import {
  listScheduledTasks,
  deleteScheduledTask,
  toggleScheduledTask,
  triggerScheduledTask,
  type ScheduledTask,
} from '@/api/scheduledTask';
import ScheduledTaskForm from './ScheduledTaskForm';
import ScheduledTaskLogs from './ScheduledTaskLogs';
import i18n from '@/i18n';

// 状态/触发方式只存 key 与色板，文案渲染期 t() 解析（模块顶层写死文案会冻结语言）
const STATUS_MAP: Record<string, { labelKey: string; color: string }> = {
  success: { labelKey: 'success', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  partial: { labelKey: 'partial', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  queued: { labelKey: 'queued', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  cancelled: { labelKey: 'cancelled', color: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
  failed: { labelKey: 'failed', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  running: { labelKey: 'running', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  timeout: { labelKey: 'timeout', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
};

const TRIGGER_TYPE_MAP: Record<string, { labelKey: string; color: string }> = {
  cron: { labelKey: 'cron', color: 'bg-sky-500/10 text-sky-500 border-sky-500/20' },
  webhook: { labelKey: 'webhook', color: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' },
  both: { labelKey: 'both', color: 'bg-indigo-500/10 text-indigo-500 border-indigo-500/20' },
};

const statusLabel = (s: string): string =>
  STATUS_MAP[s] ? i18n.t(`workspace:schedule.status.${STATUS_MAP[s].labelKey}` as 'workspace:schedule.status.success') : s;
const triggerLabel = (s: string): string =>
  TRIGGER_TYPE_MAP[s] ? i18n.t(`workspace:schedule.trigger.${TRIGGER_TYPE_MAP[s].labelKey}` as 'workspace:schedule.trigger.cron') : s;

// 执行身份固定为「按属主角色继承」：任务不持有 AS-BOT 与数据源，
// 每次执行按创建者的角色实时解析能力与可查范围（把内部 key 显示出来既不可读也无意义）。
const SOURCE_BADGE_COLOR = 'bg-blue-500/10 text-blue-500 border-blue-500/20';

export default function ScheduledTasks() {
  const { t } = useTranslation(['workspace', 'common', 'menu']);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledTask | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editTask, setEditTask] = useState<ScheduledTask | null>(null);
  const [logsTaskId, setLogsTaskId] = useState<number | null>(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      // 系统管理页面：不按 workspace 过滤，显示所有任务
      const res = await listScheduledTasks({ page, size: pageSize });
      setTasks(res.items);
      setTotal(res.total);
    } catch {
      toast.error(i18n.t('workspace:schedule.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize]);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  const handleToggle = async (task: ScheduledTask) => {
    try {
      await toggleScheduledTask(task.id, !task.is_active);
      toast.success(task.is_active ? t('schedule.disabled') : t('schedule.enabled'));
      loadTasks();
    } catch {
      toast.error(t('common:state.operationFailed'));
    }
  };

  const handleTrigger = async (task: ScheduledTask) => {
    try {
      await triggerScheduledTask(task.id);
      toast.success(t('schedule.queuedToast'));
      setLogsTaskId(task.id);
    } catch {
      toast.error(t('schedule.triggerFailed'));
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteScheduledTask(deleteTarget.id);
      toast.success(t('schedule.deleted'));
      setDeleteTarget(null);
      loadTasks();
    } catch {
      toast.error(t('common:state.deleteFailed'));
    }
  };

  const handleFormClose = (refresh?: boolean) => {
    setFormOpen(false);
    setEditTask(null);
    if (refresh) loadTasks();
  };

  // If viewing logs, show logs page
  if (logsTaskId) {
    return (
      <ScheduledTaskLogs
        taskId={logsTaskId}
        onBack={() => setLogsTaskId(null)}
      />
    );
  }

  return (
    <div className="flex flex-col h-full gap-4 p-6">
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-2xl font-bold">{t('menu:workspace.scheduled')}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t('schedule.subtitle')}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadTasks}>
            <RefreshCw className="w-4 h-4 mr-1" />
            {t('common:action.refresh')}
          </Button>
          <Button size="sm" onClick={() => { setEditTask(null); setFormOpen(true); }}>
            <Plus className="w-4 h-4 mr-1" />
            {t('schedule.newTask')}
          </Button>
        </div>
      </div>

      {/* Task List：仅此区滚动（页头/分页固定，参考表&字段） */}
      <div className="flex-1 min-h-0 overflow-auto border rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">{t('schedule.col.name')}</th>
              <th className="text-left p-3 font-medium">{t('schedule.col.identity')}</th>
              <th className="text-left p-3 font-medium">{t('schedule.col.trigger')}</th>
              <th className="text-left p-3 font-medium">{t('common:label.status')}</th>
              <th className="text-left p-3 font-medium">{t('schedule.col.lastRun')}</th>
              <th className="text-left p-3 font-medium">{t('schedule.col.runCount')}</th>
              <th className="text-center p-3 font-medium">{t('schedule.col.enabled')}</th>
              <th className="text-right p-3 font-medium">{t('common:label.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-muted-foreground">
                  {t('common:state.loading')}
                </td>
              </tr>
            ) : tasks.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-muted-foreground">
                  {t('schedule.empty')}
                </td>
              </tr>
            ) : (
              tasks.map(task => (
                <tr key={task.id} className="border-t hover:bg-muted/30">
                  <td className="p-3">
                    <div className="font-medium">{task.name}</div>
                    {task.description && (
                      <div className="text-xs text-muted-foreground mt-0.5 truncate max-w-[300px]">
                        {task.description}
                      </div>
                    )}
                  </td>
                  <td className="p-3">
                    <Badge variant="outline" className={SOURCE_BADGE_COLOR} title={t('schedule.identityHint')}>
                      {t('schedule.identityBadge')}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <div className="flex flex-col gap-1">
                      <Badge variant="outline" className={TRIGGER_TYPE_MAP[task.trigger_type || 'cron']?.color + ' w-fit'}>
                        {triggerLabel(task.trigger_type || 'cron')}
                      </Badge>
                      {task.cron_expression && (
                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded w-fit">
                          {task.cron_expression}
                        </code>
                      )}
                    </div>
                  </td>
                  <td className="p-3">
                    {task.ownership_status === 'unclaimed' ? (
                      <Badge variant="outline" className="text-orange-500">{t('schedule.unclaimed')}</Badge>
                    ) : task.last_status ? (
                      <Badge variant="outline" className={STATUS_MAP[task.last_status]?.color}>
                        {statusLabel(task.last_status)}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">{t('schedule.neverRun')}</span>
                    )}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {task.last_run_at ? new Date(task.last_run_at).toLocaleString(i18n.language) : '-'}
                  </td>
                  <td className="p-3 text-center">{task.run_count}</td>
                  <td className="p-3 text-center">
                    <Switch
                      checked={task.is_active}
                      onCheckedChange={() => handleToggle(task)}
                    />
                  </td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      {task.trigger_type !== 'cron' && task.webhook_token && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            navigator.clipboard.writeText(`${window.location.origin}/api/webhook/tasks/${task.id}/${task.webhook_token}`);
                            toast.success(t('schedule.webhookCopied'));
                          }}
                          title={t('schedule.webhookCopyTitle')}
                        >
                          <Copy className="w-4 h-4" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleTrigger(task)}
                        disabled={task.ownership_status === 'unclaimed'}
                        title={t('schedule.triggerTitle')}
                      >
                        <Play className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setLogsTaskId(task.id)}
                        title={t('schedule.logsTitle')}
                      >
                        <History className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => { setEditTask(task); setFormOpen(true); }}
                        title={t('common:action.edit')}
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(task)}
                        title={t('common:action.delete')}
                      >
                        <Trash2 className="w-4 h-4 text-destructive" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination —— 统一分页条：固定底栏 */}
      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        onChange={(p: number) => setPage(p)}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
        hideWhenSingle
        className="shrink-0 mt-2"
      />

      {/* Create/Edit Form Dialog — only close via X/cancel/ESC, not by clicking outside */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent
          className="max-w-2xl max-h-[90vh] overflow-y-auto overflow-x-hidden"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <ScheduledTaskForm
            task={editTask}
            onClose={handleFormClose}
          />
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('schedule.deleteTitle')}</DialogTitle>
            <DialogDescription>
              {t('schedule.deleteDesc', { name: deleteTarget?.name })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t('common:action.cancel')}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t('common:action.delete')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
