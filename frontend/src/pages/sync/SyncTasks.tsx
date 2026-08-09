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
import { Plus, Play, History, Edit, Trash2, RefreshCw } from 'lucide-react';
import { syncApi, type SyncTask } from '@/api/sync';

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  success: { label: '成功', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  failed: { label: '失败', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  running: { label: '运行中', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
};

const SYNC_MODE_MAP: Record<string, { label: string; color: string }> = {
  full: { label: '全量', color: 'bg-purple-500/10 text-purple-500 border-purple-500/20' },
  incremental: { label: '增量', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  cdc: { label: 'CDC', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
};

const SOURCE_TYPE_OPTIONS = [
  { value: 'mysql', label: 'MySQL' },
  { value: 'doris', label: 'Apache Doris' },
  { value: 'elasticsearch', label: 'Elasticsearch' },
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'api', label: 'API' },
];

const TARGET_TYPE_OPTIONS = [
  { value: 'mysql', label: 'MySQL' },
  { value: 'doris', label: 'Apache Doris' },
  { value: 'elasticsearch', label: 'Elasticsearch' },
  { value: 'postgresql', label: 'PostgreSQL' },
];

const EMPTY_FORM = {
  name: '',
  description: '',
  source_type: '',
  source_config: '{}',
  target_type: '',
  target_config: '{}',
  sync_mode: 'full' as const,
  schedule_cron: '',
};

export default function SyncTasks() {
  const [tasks, setTasks] = useState<SyncTask[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editTask, setEditTask] = useState<SyncTask | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [deleteTarget, setDeleteTarget] = useState<SyncTask | null>(null);
  const [jsonError, setJsonError] = useState('');
  const [viewLogsTaskId, setViewLogsTaskId] = useState<number | null>(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await syncApi.list({ page, size: 20 });
      setTasks(res.data.items || res.data);
      setTotal(res.data.total || 0);
    } catch {
      toast.error('加载同步任务失败');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  const openCreate = () => {
    setEditTask(null);
    setForm(EMPTY_FORM);
    setJsonError('');
    setFormOpen(true);
  };

  const openEdit = (task: SyncTask) => {
    setEditTask(task);
    setForm({
      name: task.name,
      description: task.description || '',
      source_type: task.source_type,
      source_config: JSON.stringify(task.source_config || {}, null, 2),
      target_type: task.target_type,
      target_config: JSON.stringify(task.target_config || {}, null, 2),
      sync_mode: task.sync_mode,
      schedule_cron: task.schedule_cron || '',
    });
    setJsonError('');
    setFormOpen(true);
  };

  const validateForm = (): boolean => {
    if (!form.name.trim()) { toast.error('请输入任务名称'); return false; }
    if (!form.source_type) { toast.error('请选择来源类型'); return false; }
    if (!form.target_type) { toast.error('请选择目标类型'); return false; }
    if (!form.sync_mode) { toast.error('请选择同步模式'); return false; }
    try {
      JSON.parse(form.source_config);
    } catch {
      setJsonError('来源配置 JSON 格式错误');
      return false;
    }
    try {
      JSON.parse(form.target_config);
    } catch {
      setJsonError('目标配置 JSON 格式错误');
      return false;
    }
    setJsonError('');
    return true;
  };

  const handleSave = async () => {
    if (!validateForm()) return;
    const payload = {
      name: form.name,
      description: form.description,
      source_type: form.source_type,
      source_config: JSON.parse(form.source_config),
      target_type: form.target_type,
      target_config: JSON.parse(form.target_config),
      sync_mode: form.sync_mode,
      schedule_cron: form.schedule_cron,
    };
    try {
      if (editTask) {
        await syncApi.update(editTask.id, payload);
        toast.success('已更新');
      } else {
        await syncApi.create(payload);
        toast.success('已创建');
      }
      setFormOpen(false);
      loadTasks();
    } catch {
      toast.error('保存失败');
    }
  };

  const handleToggle = async (task: SyncTask) => {
    try {
      await syncApi.toggle(task.id, !task.is_active);
      toast.success(task.is_active ? '已禁用' : '已启用');
      loadTasks();
    } catch {
      toast.error('操作失败');
    }
  };

  const handleRun = async (task: SyncTask) => {
    try {
      await syncApi.run(task.id);
      toast.success('已发送到执行队列');
    } catch {
      toast.error('触发失败');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await syncApi.delete(deleteTarget.id);
      toast.success('已删除');
      setDeleteTarget(null);
      loadTasks();
    } catch {
      toast.error('删除失败');
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">同步任务</h1>
          <p className="text-muted-foreground text-sm mt-1">管理数据同步任务和调度</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadTasks}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            新建任务
          </Button>
        </div>
      </div>

      {/* Tasks Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">任务名称</th>
              <th className="text-left p-3 font-medium">来源类型</th>
              <th className="text-left p-3 font-medium">目标类型</th>
              <th className="text-left p-3 font-medium">同步模式</th>
              <th className="text-left p-3 font-medium">Cron</th>
              <th className="text-left p-3 font-medium">上次状态</th>
              <th className="text-left p-3 font-medium">上次执行</th>
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
                  暂无同步任务
                </td>
              </tr>
            ) : (
              tasks.map(task => (
                <tr key={task.id} className="border-t hover:bg-muted/30">
                  <td className="p-3">
                    <div className="font-medium">{task.name}</div>
                    {task.description && (
                      <div className="text-xs text-muted-foreground mt-0.5 truncate max-w-[250px]">
                        {task.description}
                      </div>
                    )}
                  </td>
                  <td className="p-3">
                    <Badge variant="outline" className="bg-gray-500/10 text-gray-500 border-gray-500/20">
                      {task.source_type.toUpperCase()}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <Badge variant="outline" className="bg-gray-500/10 text-gray-500 border-gray-500/20">
                      {task.target_type.toUpperCase()}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <Badge variant="outline" className={SYNC_MODE_MAP[task.sync_mode]?.color}>
                      {SYNC_MODE_MAP[task.sync_mode]?.label || task.sync_mode}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
                      {task.schedule_cron || '-'}
                    </code>
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
                  <td className="p-3 text-center">
                    <Switch
                      checked={task.is_active}
                      onCheckedChange={() => handleToggle(task)}
                    />
                  </td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleRun(task)}
                        title="手动执行"
                      >
                        <Play className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setViewLogsTaskId(task.id)}
                        title="执行日志"
                      >
                        <History className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(task)}
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

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent
          className="max-w-2xl max-h-[90vh] overflow-y-auto"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{editTask ? '编辑同步任务' : '新建同步任务'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">任务名称 *</label>
              <Input
                className="mt-1"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="输入任务名称"
              />
            </div>
            <div>
              <label className="text-sm font-medium">描述</label>
              <Input
                className="mt-1"
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="任务描述（可选）"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">来源类型 *</label>
                <Select
                  value={form.source_type}
                  onValueChange={v => setForm(f => ({ ...f, source_type: v }))}
                >
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="选择来源类型" />
                  </SelectTrigger>
                  <SelectContent>
                    {SOURCE_TYPE_OPTIONS.map(opt => (
                      <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium">目标类型 *</label>
                <Select
                  value={form.target_type}
                  onValueChange={v => setForm(f => ({ ...f, target_type: v }))}
                >
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="选择目标类型" />
                  </SelectTrigger>
                  <SelectContent>
                    {TARGET_TYPE_OPTIONS.map(opt => (
                      <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">来源配置 (JSON)</label>
                <textarea
                  className="mt-1 w-full min-h-[100px] rounded-md border border-input bg-background px-3 py-2 text-sm font-mono"
                  value={form.source_config}
                  onChange={e => setForm(f => ({ ...f, source_config: e.target.value }))}
                  placeholder='{"host": "localhost", "port": 3306, "database": "db"}'
                />
              </div>
              <div>
                <label className="text-sm font-medium">目标配置 (JSON)</label>
                <textarea
                  className="mt-1 w-full min-h-[100px] rounded-md border border-input bg-background px-3 py-2 text-sm font-mono"
                  value={form.target_config}
                  onChange={e => setForm(f => ({ ...f, target_config: e.target.value }))}
                  placeholder='{"host": "localhost", "port": 9030, "database": "db"}'
                />
              </div>
            </div>
            {jsonError && (
              <p className="text-sm text-red-500">{jsonError}</p>
            )}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">同步模式 *</label>
                <Select
                  value={form.sync_mode}
                  onValueChange={v => setForm(f => ({ ...f, sync_mode: v as any }))}
                >
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="选择同步模式" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="full">全量同步</SelectItem>
                    <SelectItem value="incremental">增量同步</SelectItem>
                    <SelectItem value="cdc">CDC 变更捕获</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium">调度 Cron</label>
                <Input
                  className="mt-1"
                  value={form.schedule_cron}
                  onChange={e => setForm(f => ({ ...f, schedule_cron: e.target.value }))}
                  placeholder="0 */6 * * *"
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave}>{editTask ? '更新' : '创建'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除同步任务「{deleteTarget?.name}」吗？相关执行日志也会被删除。
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
