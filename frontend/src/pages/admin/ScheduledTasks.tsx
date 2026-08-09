import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  Plus,
  Play,
  History,
  Edit,
  Trash2,
  Clock,
  Bell,
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

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  success: { label: '成功', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  failed: { label: '失败', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  running: { label: '运行中', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  timeout: { label: '超时', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
};

const TASK_TYPE_MAP: Record<string, { label: string; color: string }> = {
  query: { label: 'SQL', color: 'bg-purple-500/10 text-purple-500 border-purple-500/20' },
  agent: { label: 'Agent', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
};

const TRIGGER_TYPE_MAP: Record<string, { label: string; color: string }> = {
  cron: { label: '定时', color: 'bg-sky-500/10 text-sky-500 border-sky-500/20' },
  webhook: { label: 'Webhook', color: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' },
  both: { label: '定时+Webhook', color: 'bg-indigo-500/10 text-indigo-500 border-indigo-500/20' },
};

function getSourceBadge(task: ScheduledTask): { label: string; color: string } {
  const cfg = task.task_config || {} as any;
  if (cfg.agent_name) return { label: 'Agent', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' };
  if (cfg.mcp_server_id) return { label: 'MCP', color: 'bg-teal-500/10 text-teal-500 border-teal-500/20' };
  return { label: '数据源', color: 'bg-gray-500/10 text-gray-500 border-gray-500/20' };
}

export default function ScheduledTasks() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledTask | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editTask, setEditTask] = useState<ScheduledTask | null>(null);
  const [logsTaskId, setLogsTaskId] = useState<number | null>(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      // 系统管理页面：不按 workspace 过滤，显示所有任务
      const res = await listScheduledTasks({ page, size: 20 });
      setTasks(res.items);
      setTotal(res.total);
    } catch {
      toast.error('加载任务调度失败');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  const handleToggle = async (task: ScheduledTask) => {
    try {
      await toggleScheduledTask(task.id, !task.is_active);
      toast.success(task.is_active ? '已禁用' : '已启用');
      loadTasks();
    } catch {
      toast.error('操作失败');
    }
  };

  const handleTrigger = async (task: ScheduledTask) => {
    try {
      await triggerScheduledTask(task.id);
      toast.success('已发送到执行队列');
    } catch {
      toast.error('触发失败');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteScheduledTask(deleteTarget.id);
      toast.success('已删除');
      setDeleteTarget(null);
      loadTasks();
    } catch {
      toast.error('删除失败');
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
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">任务调度</h1>
          <p className="text-muted-foreground text-sm mt-1">配置定时执行的 SQL 或 Agent 任务</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadTasks}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button size="sm" onClick={() => { setEditTask(null); setFormOpen(true); }}>
            <Plus className="w-4 h-4 mr-1" />
            新建任务
          </Button>
        </div>
      </div>

      {/* Task List */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">任务名称</th>
              <th className="text-left p-3 font-medium">类型</th>
              <th className="text-left p-3 font-medium">执行来源</th>
              <th className="text-left p-3 font-medium">触发方式</th>
              <th className="text-left p-3 font-medium">状态</th>
              <th className="text-left p-3 font-medium">上次执行</th>
              <th className="text-left p-3 font-medium">执行次数</th>
              <th className="text-center p-3 font-medium">启用</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-muted-foreground">
                  加载中...
                </td>
              </tr>
            ) : tasks.length === 0 ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-muted-foreground">
                  暂无任务调度
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
                    <Badge variant="outline" className={TASK_TYPE_MAP[task.task_type]?.color}>
                      {TASK_TYPE_MAP[task.task_type]?.label || task.task_type}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <Badge variant="outline" className={getSourceBadge(task).color}>
                      {getSourceBadge(task).label}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <div className="flex flex-col gap-1">
                      <Badge variant="outline" className={TRIGGER_TYPE_MAP[task.trigger_type || 'cron']?.color + ' w-fit'}>
                        {TRIGGER_TYPE_MAP[task.trigger_type || 'cron']?.label || task.trigger_type}
                      </Badge>
                      {task.cron_expression && (
                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded w-fit">
                          {task.cron_expression}
                        </code>
                      )}
                    </div>
                  </td>
                  <td className="p-3">
                    {task.last_status ? (
                      <Badge variant="outline" className={STATUS_MAP[task.last_status]?.color}>
                        {STATUS_MAP[task.last_status]?.label || task.last_status}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">未执行</span>
                    )}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {task.last_run_at ? new Date(task.last_run_at).toLocaleString() : '-'}
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
                            toast.success('已复制 Webhook URL');
                          }}
                          title="复制 Webhook URL"
                        >
                          <Copy className="w-4 h-4" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleTrigger(task)}
                        title="手动触发"
                      >
                        <Play className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setLogsTaskId(task.id)}
                        title="执行历史"
                      >
                        <History className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => { setEditTask(task); setFormOpen(true); }}
                        title="编辑"
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(task)}
                        title="删除"
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

      {/* Pagination */}
      {total > 20 && (
        <div className="flex justify-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage(p => p - 1)}
          >
            上一页
          </Button>
          <span className="text-sm text-muted-foreground leading-9">
            第 {page} 页 / 共 {Math.ceil(total / 20)} 页
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page * 20 >= total}
            onClick={() => setPage(p => p + 1)}
          >
            下一页
          </Button>
        </div>
      )}

      {/* Create/Edit Form Dialog — only close via X/cancel/ESC, not by clicking outside */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent
          className="max-w-3xl max-h-[90vh] overflow-y-auto overflow-x-hidden"
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
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除任务调度「{deleteTarget?.name}」吗？相关的执行历史也会被删除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
