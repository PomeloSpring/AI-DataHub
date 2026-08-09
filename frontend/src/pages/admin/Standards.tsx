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
import { Plus, Edit, Trash2, RefreshCw } from 'lucide-react';
import { governanceApi, type DataStandard } from '@/api/governance';
import { useWorkspaceStore } from '@/stores/workspaceStore';

const TYPE_MAP: Record<string, { label: string; color: string }> = {
  naming: { label: '命名规范', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  encoding: { label: '编码规范', color: 'bg-purple-500/10 text-purple-500 border-purple-500/20' },
  measurement: { label: '度量规范', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  format: { label: '格式规范', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
};

interface StandardForm {
  name: string;
  standard_type: string;
  description: string;
  rule_config: string;
}

const EMPTY_FORM: StandardForm = {
  name: '',
  standard_type: '',
  description: '',
  rule_config: '{}',
};

export default function Standards() {
  const [standards, setStandards] = useState<DataStandard[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editStandard, setEditStandard] = useState<DataStandard | null>(null);
  const [form, setForm] = useState<StandardForm>(EMPTY_FORM);
  const [deleteTarget, setDeleteTarget] = useState<DataStandard | null>(null);
  const [jsonError, setJsonError] = useState('');
  const { getDefaultWorkspaceId } = useWorkspaceStore();

  const loadStandards = useCallback(async () => {
    setLoading(true);
    try {
      const workspaceId = getDefaultWorkspaceId();
      const res = await governanceApi.listStandards({ workspace_id: workspaceId, page, size: 20 });
      setStandards(res.data.items || res.data || []);
      setTotal(res.data.total || 0);
    } catch {
      toast.error('加载数据标准失败');
    } finally {
      setLoading(false);
    }
  }, [page, getDefaultWorkspaceId]);

  useEffect(() => { loadStandards(); }, [loadStandards]);

  const openCreate = () => {
    setEditStandard(null);
    setForm(EMPTY_FORM);
    setJsonError('');
    setFormOpen(true);
  };

  const openEdit = (standard: DataStandard) => {
    setEditStandard(standard);
    setForm({
      name: standard.name,
      standard_type: standard.standard_type,
      description: standard.description || '',
      rule_config: JSON.stringify(standard.rule_config || {}, null, 2),
    });
    setJsonError('');
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error('请输入标准名称'); return; }
    if (!form.standard_type) { toast.error('请选择标准类型'); return; }
    let parsedConfig: Record<string, any>;
    try {
      parsedConfig = JSON.parse(form.rule_config);
      setJsonError('');
    } catch {
      setJsonError('规则配置 JSON 格式错误');
      return;
    }
    const payload = {
      name: form.name,
      standard_type: form.standard_type,
      description: form.description,
      rule_config: parsedConfig,
      workspace_id: getDefaultWorkspaceId(),
    };
    try {
      if (editStandard) {
        await governanceApi.updateStandard(editStandard.id, payload);
        toast.success('已更新');
      } else {
        await governanceApi.createStandard(payload);
        toast.success('已创建');
      }
      setFormOpen(false);
      loadStandards();
    } catch {
      toast.error('保存失败');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await governanceApi.deleteStandard(deleteTarget.id);
      toast.success('已删除');
      setDeleteTarget(null);
      loadStandards();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (standard: DataStandard) => {
    try {
      // Use update to toggle is_active
      await governanceApi.updateStandard(standard.id, { is_active: !standard.is_active });
      toast.success(standard.is_active ? '已禁用' : '已启用');
      loadStandards();
    } catch {
      toast.error('操作失败');
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">数据标准</h1>
          <p className="text-muted-foreground text-sm mt-1">管理命名、编码、度量、格式等数据标准规范</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadStandards}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            新建标准
          </Button>
        </div>
      </div>

      {/* Standards Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">标准名称</th>
              <th className="text-left p-3 font-medium">类型</th>
              <th className="text-left p-3 font-medium">描述</th>
              <th className="text-center p-3 font-medium">启用</th>
              <th className="text-left p-3 font-medium">创建时间</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="p-8 text-center text-muted-foreground">
                  加载中...
                </td>
              </tr>
            ) : standards.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-8 text-center text-muted-foreground">
                  暂无数据标准
                </td>
              </tr>
            ) : (
              standards.map(standard => (
                <tr key={standard.id} className="border-t hover:bg-muted/30">
                  <td className="p-3 font-medium">{standard.name}</td>
                  <td className="p-3">
                    <Badge variant="outline" className={TYPE_MAP[standard.standard_type]?.color}>
                      {TYPE_MAP[standard.standard_type]?.label || standard.standard_type}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs text-muted-foreground truncate max-w-[300px]">
                    {standard.description || '-'}
                  </td>
                  <td className="p-3 text-center">
                    <Switch
                      checked={standard.is_active}
                      onCheckedChange={() => handleToggle(standard)}
                    />
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {new Date(standard.created_at).toLocaleString()}
                  </td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(standard)}
                        title="编辑"
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(standard)}
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
          className="max-w-xl max-h-[90vh] overflow-y-auto"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{editStandard ? '编辑数据标准' : '新建数据标准'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">标准名称 *</label>
              <Input
                className="mt-1"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="输入标准名称"
              />
            </div>
            <div>
              <label className="text-sm font-medium">标准类型 *</label>
              <Select
                value={form.standard_type}
                onValueChange={v => setForm(f => ({ ...f, standard_type: v }))}
              >
                <SelectTrigger className="mt-1">
                  <SelectValue placeholder="选择标准类型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="naming">命名规范</SelectItem>
                  <SelectItem value="encoding">编码规范</SelectItem>
                  <SelectItem value="measurement">度量规范</SelectItem>
                  <SelectItem value="format">格式规范</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-sm font-medium">描述</label>
              <Input
                className="mt-1"
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="标准描述（可选）"
              />
            </div>
            <div>
              <label className="text-sm font-medium">规则配置 (JSON)</label>
              <textarea
                className="mt-1 w-full min-h-[120px] rounded-md border border-input bg-background px-3 py-2 text-sm font-mono"
                value={form.rule_config}
                onChange={e => setForm(f => ({ ...f, rule_config: e.target.value }))}
                placeholder='{"pattern": "^[a-z][a-z0-9_]*$", "max_length": 64}'
              />
              {jsonError && (
                <p className="text-sm text-red-500 mt-1">{jsonError}</p>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave}>{editStandard ? '更新' : '创建'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除数据标准「{deleteTarget?.name}」吗？
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
