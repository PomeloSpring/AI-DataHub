import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
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
import { Plus, Trash2, RefreshCw, ScanSearch, Edit, Shield } from 'lucide-react';
import { governanceApi, type SensitiveField } from '@/api/governance';
import { useWorkspaceStore } from '@/stores/workspaceStore';

const LEVEL_MAP: Record<string, { label: string; color: string }> = {
  low: { label: '低', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  medium: { label: '中', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  high: { label: '高', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  critical: { label: '极高', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
};

const MASK_TYPE_OPTIONS = [
  { value: 'full_mask', label: '完全脱敏' },
  { value: 'partial_mask', label: '部分脱敏' },
  { value: 'hash', label: '哈希处理' },
  { value: 'encrypt', label: '加密存储' },
  { value: 'truncate', label: '截断处理' },
  { value: 'redact', label: '打码处理' },
];

interface FieldForm {
  datasource_id: string;
  table_name: string;
  column_name: string;
  sensitivity_level: string;
  mask_type: string;
}

const EMPTY_FORM: FieldForm = {
  datasource_id: '',
  table_name: '',
  column_name: '',
  sensitivity_level: '',
  mask_type: '',
};

export default function SensitiveData() {
  const [fields, setFields] = useState<SensitiveField[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editField, setEditField] = useState<SensitiveField | null>(null);
  const [form, setForm] = useState<FieldForm>(EMPTY_FORM);
  const [deleteTarget, setDeleteTarget] = useState<SensitiveField | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanDatasourceId, setScanDatasourceId] = useState('');
  const { getDefaultWorkspaceId } = useWorkspaceStore();

  const loadFields = useCallback(async () => {
    setLoading(true);
    try {
      const workspaceId = getDefaultWorkspaceId();
      const res = await governanceApi.getSensitiveFields({ workspace_id: workspaceId, page, size: 20 });
      setFields(res.data.items || res.data || []);
      setTotal(res.data.total || 0);
    } catch {
      toast.error('加载敏感字段失败');
    } finally {
      setLoading(false);
    }
  }, [page, getDefaultWorkspaceId]);

  useEffect(() => { loadFields(); }, [loadFields]);

  const openCreate = () => {
    setEditField(null);
    setForm(EMPTY_FORM);
    setFormOpen(true);
  };

  const openEdit = (field: SensitiveField) => {
    setEditField(field);
    setForm({
      datasource_id: String(field.datasource_id),
      table_name: field.table_name,
      column_name: field.column_name,
      sensitivity_level: field.sensitivity_level,
      mask_type: field.mask_type,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.datasource_id) { toast.error('请输入数据源 ID'); return; }
    if (!form.table_name.trim()) { toast.error('请输入表名'); return; }
    if (!form.column_name.trim()) { toast.error('请输入字段名'); return; }
    if (!form.sensitivity_level) { toast.error('请选择敏感级别'); return; }
    if (!form.mask_type) { toast.error('请选择脱敏方式'); return; }
    const payload = {
      datasource_id: Number(form.datasource_id),
      table_name: form.table_name,
      column_name: form.column_name,
      sensitivity_level: form.sensitivity_level,
      mask_type: form.mask_type,
      workspace_id: getDefaultWorkspaceId(),
    };
    try {
      if (editField) {
        await governanceApi.updateSensitiveField(editField.id, payload);
        toast.success('已更新');
      } else {
        await governanceApi.createSensitiveField(payload);
        toast.success('已创建');
      }
      setFormOpen(false);
      loadFields();
    } catch {
      toast.error('保存失败');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await governanceApi.deleteSensitiveField(deleteTarget.id);
      toast.success('已删除');
      setDeleteTarget(null);
      loadFields();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleScan = async () => {
    if (!scanDatasourceId) {
      toast.error('请输入数据源 ID');
      return;
    }
    setScanning(true);
    try {
      const workspaceId = getDefaultWorkspaceId();
      const res = await governanceApi.scanSensitiveFields(Number(scanDatasourceId), workspaceId);
      const count = res.data?.found ?? res.data?.count ?? 0;
      toast.success(`扫描完成，发现 ${count} 个敏感字段`);
      loadFields();
    } catch {
      toast.error('扫描失败');
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">敏感数据管理</h1>
          <p className="text-muted-foreground text-xs mt-1">管理敏感字段识别和脱敏策略</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadFields}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            添加字段
          </Button>
        </div>
      </div>

      {/* Scan Bar */}
      <div className="flex items-center gap-3 p-4 border rounded-lg bg-muted/20">
        <Shield className="w-5 h-5 text-muted-foreground" />
        <span className="text-sm font-medium">自动扫描</span>
        <Input
          className="w-[200px]"
          placeholder="数据源 ID"
          value={scanDatasourceId}
          onChange={e => setScanDatasourceId(e.target.value)}
        />
        <Button size="sm" variant="outline" onClick={handleScan} disabled={scanning}>
          <ScanSearch className="w-4 h-4 mr-1" />
          {scanning ? '扫描中...' : '扫描敏感字段'}
        </Button>
        <span className="text-xs text-muted-foreground">
          自动识别数据源中的敏感字段（手机号、身份证、邮箱等）
        </span>
      </div>

      {/* Fields Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">表名</th>
              <th className="text-left p-3 font-medium">字段名</th>
              <th className="text-left p-3 font-medium">敏感级别</th>
              <th className="text-left p-3 font-medium">脱敏方式</th>
              <th className="text-left p-3 font-medium">数据源 ID</th>
              <th className="text-left p-3 font-medium">创建时间</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="p-8 text-center text-muted-foreground">
                  加载中...
                </td>
              </tr>
            ) : fields.length === 0 ? (
              <tr>
                <td colSpan={7} className="p-8 text-center text-muted-foreground">
                  暂无敏感字段记录
                </td>
              </tr>
            ) : (
              fields.map(field => (
                <tr key={field.id} className="border-t hover:bg-muted/30">
                  <td className="p-3">
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{field.table_name}</code>
                  </td>
                  <td className="p-3">
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{field.column_name}</code>
                  </td>
                  <td className="p-3">
                    <Badge variant="outline" className={LEVEL_MAP[field.sensitivity_level]?.color}>
                      {LEVEL_MAP[field.sensitivity_level]?.label || field.sensitivity_level}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs">
                    {MASK_TYPE_OPTIONS.find(m => m.value === field.mask_type)?.label || field.mask_type}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {field.datasource_id}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {new Date(field.created_at).toLocaleString()}
                  </td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(field)}
                        title="编辑"
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(field)}
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
          className="max-w-lg"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{editField ? '编辑敏感字段' : '添加敏感字段'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">数据源 ID *</label>
              <Input
                className="mt-1"
                type="number"
                value={form.datasource_id}
                onChange={e => setForm(f => ({ ...f, datasource_id: e.target.value }))}
                placeholder="输入数据源 ID"
                disabled={!!editField}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">表名 *</label>
                <Input
                  className="mt-1"
                  value={form.table_name}
                  onChange={e => setForm(f => ({ ...f, table_name: e.target.value }))}
                  placeholder="如: user_info"
                  disabled={!!editField}
                />
              </div>
              <div>
                <label className="text-sm font-medium">字段名 *</label>
                <Input
                  className="mt-1"
                  value={form.column_name}
                  onChange={e => setForm(f => ({ ...f, column_name: e.target.value }))}
                  placeholder="如: phone_number"
                  disabled={!!editField}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">敏感级别 *</label>
                <Select
                  value={form.sensitivity_level}
                  onValueChange={v => setForm(f => ({ ...f, sensitivity_level: v }))}
                >
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="选择敏感级别" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="low">低</SelectItem>
                    <SelectItem value="medium">中</SelectItem>
                    <SelectItem value="high">高</SelectItem>
                    <SelectItem value="critical">极高</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium">脱敏方式 *</label>
                <Select
                  value={form.mask_type}
                  onValueChange={v => setForm(f => ({ ...f, mask_type: v }))}
                >
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="选择脱敏方式" />
                  </SelectTrigger>
                  <SelectContent>
                    {MASK_TYPE_OPTIONS.map(opt => (
                      <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave}>{editField ? '更新' : '添加'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除敏感字段「{deleteTarget?.table_name}.{deleteTarget?.column_name}」的脱敏配置吗？
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
