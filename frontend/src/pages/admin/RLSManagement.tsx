import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import {
  Plus, Edit, Trash2, Shield, Eye, EyeOff, EyeClosed,
  User, FileText, Columns3,
} from 'lucide-react';
import {
  listRLSPolicies,
  createRLSPolicy,
  updateRLSPolicy,
  deleteRLSPolicy,
  getRLSColumnPolicies,
  setRLSColumnPolicies,
  listRLSAuditLogs,
  type RLSPolicy,
  type RLSColumnPolicy,
  type RLSAuditLog,
} from '@/api/rls';

interface PolicyFormData {
  name: string;
  description: string;
  datasource_id: number;
  table_name: string;
  policy_type: 'row' | 'column' | 'both';
  filter_type: 'condition' | 'user_attribute';
  filter_expr: string;
  user_attribute: string;
  is_active: boolean;
}

const DEFAULT_FORM: PolicyFormData = {
  name: '',
  description: '',
  datasource_id: 0,
  table_name: '',
  policy_type: 'both',
  filter_type: 'condition',
  filter_expr: '',
  user_attribute: '',
  is_active: true,
};

export default function RLSManagement() {
  const [policies, setPolicies] = useState<RLSPolicy[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editPolicy, setEditPolicy] = useState<RLSPolicy | null>(null);
  const [form, setForm] = useState<PolicyFormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<RLSPolicy | null>(null);

  // Column policy state
  const [columnPolicyTarget, setColumnPolicyTarget] = useState<RLSPolicy | null>(null);
  const [columnPolicies, setColumnPolicies] = useState<RLSColumnPolicy[]>([]);
  const [newColumnName, setNewColumnName] = useState('');
  const [newAccessType, setNewAccessType] = useState<'visible' | 'hidden' | 'masked'>('hidden');

  // Audit logs
  const [auditLogs, setAuditLogs] = useState<RLSAuditLog[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditPage, setAuditPage] = useState(1);

  // ── Load policies ────────────────────────────────────────────────

  const loadPolicies = useCallback(async () => {
    setLoading(true);
    try {
      // TODO: get workspace_id from context/store
      const data = await listRLSPolicies(0, undefined, undefined, page);
      setPolicies(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error('加载策略失败');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { loadPolicies(); }, [loadPolicies]);

  // ── Load audit logs ──────────────────────────────────────────────

  const loadAuditLogs = useCallback(async () => {
    try {
      const data = await listRLSAuditLogs(0, undefined, auditPage);
      setAuditLogs(data.items || []);
      setAuditTotal(data.total || 0);
    } catch {
      toast.error('加载审计日志失败');
    }
  }, [auditPage]);

  // ── Policy CRUD ──────────────────────────────────────────────────

  const openCreate = () => {
    setEditPolicy(null);
    setForm(DEFAULT_FORM);
    setFormOpen(true);
  };

  const openEdit = (policy: RLSPolicy) => {
    setEditPolicy(policy);
    setForm({
      name: policy.name,
      description: policy.description || '',
      datasource_id: policy.datasource_id,
      table_name: policy.table_name,
      policy_type: policy.policy_type,
      filter_type: policy.filter_type,
      filter_expr: policy.filter_expr || '',
      user_attribute: policy.user_attribute || '',
      is_active: !!policy.is_active,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name || !form.table_name) {
      toast.error('请填写策略名称和目标表名');
      return;
    }
    setSaving(true);
    try {
      if (editPolicy) {
        await updateRLSPolicy(editPolicy.id, {
          ...form,
          is_active: form.is_active ? 1 : 0,
        });
        toast.success('策略已更新');
      } else {
        await createRLSPolicy({
          ...form,
          workspace_id: 0, // TODO: get from context
          is_active: form.is_active ? 1 : 0,
        });
        toast.success('策略已创建');
      }
      setFormOpen(false);
      loadPolicies();
    } catch {
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteRLSPolicy(deleteTarget.id);
      toast.success('策略已删除');
      setDeleteTarget(null);
      loadPolicies();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (policy: RLSPolicy) => {
    try {
      await updateRLSPolicy(policy.id, { is_active: policy.is_active ? 0 : 1 });
      loadPolicies();
    } catch {
      toast.error('切换状态失败');
    }
  };

  // ── Column policy management ─────────────────────────────────────

  const openColumnPolicies = async (policy: RLSPolicy) => {
    setColumnPolicyTarget(policy);
    try {
      const cols = await getRLSColumnPolicies(policy.id);
      setColumnPolicies(cols);
    } catch {
      setColumnPolicies([]);
    }
  };

  const addColumnPolicy = async () => {
    if (!newColumnName || !columnPolicyTarget) return;
    const updated = [
      ...columnPolicies.map(c => ({
        column_name: c.column_name,
        access_type: c.access_type,
        mask_pattern: c.mask_pattern || '',
      })),
      { column_name: newColumnName, access_type: newAccessType, mask_pattern: '' },
    ];
    try {
      await setRLSColumnPolicies(columnPolicyTarget.id, updated);
      const cols = await getRLSColumnPolicies(columnPolicyTarget.id);
      setColumnPolicies(cols);
      setNewColumnName('');
      toast.success('列权限已添加');
    } catch {
      toast.error('添加失败');
    }
  };

  const removeColumnPolicy = async (columnName: string) => {
    if (!columnPolicyTarget) return;
    const updated = columnPolicies
      .filter(c => c.column_name !== columnName)
      .map(c => ({
        column_name: c.column_name,
        access_type: c.access_type,
        mask_pattern: c.mask_pattern || '',
      }));
    try {
      await setRLSColumnPolicies(columnPolicyTarget.id, updated);
      const cols = await getRLSColumnPolicies(columnPolicyTarget.id);
      setColumnPolicies(cols);
      toast.success('列权限已移除');
    } catch {
      toast.error('移除失败');
    }
  };

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
        <Shield className="h-6 w-6" />
        RLS 行级安全
      </h1>

      <Tabs defaultValue="policies">
        <TabsList>
          <TabsTrigger value="policies">
            <Shield className="h-4 w-4 mr-2" />
            策略管理
          </TabsTrigger>
          <TabsTrigger value="audit" onClick={() => loadAuditLogs()}>
            <FileText className="h-4 w-4 mr-2" />
            审计日志
          </TabsTrigger>
        </TabsList>

        {/* ── Policies Tab ──────────────────────────────────────── */}
        <TabsContent value="policies">
          <div className="flex justify-between items-center mb-4">
            <p className="text-sm text-muted-foreground">
              共 {total} 条策略
            </p>
            <Button onClick={openCreate} size="sm">
              <Plus className="h-4 w-4 mr-2" />
              新建策略
            </Button>
          </div>

          {loading ? (
            <div className="text-center py-8 text-muted-foreground">加载中...</div>
          ) : policies.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              暂无 RLS 策略，点击"新建策略"开始配置
            </div>
          ) : (
            <div className="space-y-3">
              {policies.map(policy => (
                <div
                  key={policy.id}
                  className="border rounded-lg p-4 flex items-start justify-between"
                >
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium">{policy.name}</span>
                      <Badge variant={policy.policy_type === 'both' ? 'default' : 'secondary'}>
                        {policy.policy_type === 'row' ? '行级' :
                         policy.policy_type === 'column' ? '列级' : '行+列'}
                      </Badge>
                      <Badge variant={policy.is_active ? 'default' : 'outline'}>
                        {policy.is_active ? '启用' : '禁用'}
                      </Badge>
                    </div>
                    <div className="text-sm text-muted-foreground space-y-1">
                      <p>表: <code>{policy.table_name}</code> | 数据源 ID: {policy.datasource_id}</p>
                      {policy.filter_expr && (
                        <p>过滤: <code className="bg-muted px-1 rounded">{policy.filter_expr}</code></p>
                      )}
                      {policy.description && <p>{policy.description}</p>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-4">
                    <Switch
                      checked={!!policy.is_active}
                      onCheckedChange={() => handleToggle(policy)}
                    />
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openColumnPolicies(policy)}
                      title="列权限"
                    >
                      <Columns3 className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openEdit(policy)}
                    >
                      <Edit className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setDeleteTarget(policy)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {total > 20 && (
            <div className="flex justify-center gap-2 mt-4">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage(p => p - 1)}
              >
                上一页
              </Button>
              <span className="py-1 px-3 text-sm">
                {page} / {Math.ceil(total / 20)}
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
        </TabsContent>

        {/* ── Audit Tab ─────────────────────────────────────────── */}
        <TabsContent value="audit">
          <div className="mb-4 text-sm text-muted-foreground">
            共 {auditTotal} 条审计记录
          </div>
          {auditLogs.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">暂无审计记录</div>
          ) : (
            <div className="space-y-2">
              {auditLogs.map(log => (
                <div key={log.id} className="border rounded p-3 text-sm">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant="outline">{log.action}</Badge>
                    <span className="text-muted-foreground">{log.created_at}</span>
                  </div>
                  {log.policy_name && <p>策略: {log.policy_name}</p>}
                  {log.table_name && <p>表: {log.table_name}</p>}
                  {log.filtered_sql && (
                    <details className="mt-1">
                      <summary className="cursor-pointer text-muted-foreground">查看 SQL</summary>
                      <pre className="mt-1 text-xs bg-muted p-2 rounded overflow-auto">
                        {log.filtered_sql}
                      </pre>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
          {auditTotal > 20 && (
            <div className="flex justify-center gap-2 mt-4">
              <Button
                variant="outline"
                size="sm"
                disabled={auditPage <= 1}
                onClick={() => setAuditPage(p => p - 1)}
              >
                上一页
              </Button>
              <span className="py-1 px-3 text-sm">
                {auditPage} / {Math.ceil(auditTotal / 20)}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={auditPage * 20 >= auditTotal}
                onClick={() => setAuditPage(p => p + 1)}
              >
                下一页
              </Button>
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* ── Create/Edit Dialog ──────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editPolicy ? '编辑策略' : '新建 RLS 策略'}</DialogTitle>
            <DialogDescription>
              配置行级和列级安全策略，控制用户对数据的访问权限
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>策略名称 *</Label>
              <Input
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="如: 仅看本区域数据"
              />
            </div>
            <div>
              <Label>目标表名 *</Label>
              <Input
                value={form.table_name}
                onChange={e => setForm(f => ({ ...f, table_name: e.target.value }))}
                placeholder="如: orders"
              />
            </div>
            <div>
              <Label>数据源 ID *</Label>
              <Input
                type="number"
                value={form.datasource_id || ''}
                onChange={e => setForm(f => ({ ...f, datasource_id: Number(e.target.value) }))}
              />
            </div>
            <div>
              <Label>策略类型</Label>
              <Select
                value={form.policy_type}
                onValueChange={(v: any) => setForm(f => ({ ...f, policy_type: v }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="both">行 + 列</SelectItem>
                  <SelectItem value="row">仅行级</SelectItem>
                  <SelectItem value="column">仅列级</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {(form.policy_type === 'row' || form.policy_type === 'both') && (
              <>
                <div>
                  <Label>过滤类型</Label>
                  <Select
                    value={form.filter_type}
                    onValueChange={(v: any) => setForm(f => ({ ...f, filter_type: v }))}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="condition">条件表达式</SelectItem>
                      <SelectItem value="user_attribute">用户属性</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>过滤表达式</Label>
                  <Textarea
                    value={form.filter_expr}
                    onChange={e => setForm(f => ({ ...f, filter_expr: e.target.value }))}
                    placeholder={'如: region = :user_region\n或: status = \'active\''}
                    rows={3}
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    使用 :user_xxx 引用用户属性值
                  </p>
                </div>
                {form.filter_type === 'user_attribute' && (
                  <div>
                    <Label>用户属性名</Label>
                    <Input
                      value={form.user_attribute}
                      onChange={e => setForm(f => ({ ...f, user_attribute: e.target.value }))}
                      placeholder="如: region"
                    />
                  </div>
                )}
              </>
            )}
            <div>
              <Label>描述</Label>
              <Textarea
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="策略说明..."
                rows={2}
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={form.is_active}
                onCheckedChange={v => setForm(f => ({ ...f, is_active: v }))}
              />
              <Label>启用策略</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Column Policy Dialog ────────────────────────────────── */}
      <Dialog open={!!columnPolicyTarget} onOpenChange={() => setColumnPolicyTarget(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>列权限管理 — {columnPolicyTarget?.name}</DialogTitle>
            <DialogDescription>
              为表 <code>{columnPolicyTarget?.table_name}</code> 配置列级访问控制
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {/* Add column */}
            <div className="flex gap-2">
              <Input
                placeholder="列名"
                value={newColumnName}
                onChange={e => setNewColumnName(e.target.value)}
                className="flex-1"
              />
              <Select value={newAccessType} onValueChange={(v: any) => setNewAccessType(v)}>
                <SelectTrigger className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="hidden">隐藏</SelectItem>
                  <SelectItem value="masked">脱敏</SelectItem>
                  <SelectItem value="visible">可见</SelectItem>
                </SelectContent>
              </Select>
              <Button onClick={addColumnPolicy} disabled={!newColumnName}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>

            {/* Column list */}
            {columnPolicies.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                暂无列级策略
              </p>
            ) : (
              <div className="space-y-2">
                {columnPolicies.map(col => (
                  <div
                    key={col.column_name}
                    className="flex items-center justify-between border rounded px-3 py-2"
                  >
                    <div className="flex items-center gap-2">
                      <code className="text-sm">{col.column_name}</code>
                      <Badge variant={col.access_type === 'hidden' ? 'destructive' :
                                     col.access_type === 'masked' ? 'secondary' : 'default'}>
                        {col.access_type === 'hidden' ? '隐藏' :
                         col.access_type === 'masked' ? '脱敏' : '可见'}
                      </Badge>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => removeColumnPolicy(col.column_name)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setColumnPolicyTarget(null)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除策略 "{deleteTarget?.name}" 吗？此操作不可撤销。
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
