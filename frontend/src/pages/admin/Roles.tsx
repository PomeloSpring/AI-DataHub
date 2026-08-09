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
import { toast } from 'sonner';
import { Plus, Edit, Trash2, RefreshCw, ChevronDown, ChevronRight, Shield } from 'lucide-react';
import { governanceApi, type Role, type RolePermission } from '@/api/governance';

// Default resource/action matrix for permission management
const DEFAULT_RESOURCES = [
  { resource: 'datasource', actions: ['view', 'create', 'edit', 'delete'] },
  { resource: 'agent', actions: ['view', 'create', 'edit', 'delete', 'execute'] },
  { resource: 'dashboard', actions: ['view', 'create', 'edit', 'delete'] },
  { resource: 'scheduled_task', actions: ['view', 'create', 'edit', 'delete', 'trigger'] },
  { resource: 'sync_task', actions: ['view', 'create', 'edit', 'delete', 'trigger'] },
  { resource: 'user', actions: ['view', 'create', 'edit', 'delete'] },
  { role: 'workspace', actions: ['view', 'create', 'edit', 'delete', 'manage_members'] },
  { resource: 'knowledge_base', actions: ['view', 'create', 'edit', 'delete'] },
  { resource: 'workflow', actions: ['view', 'create', 'edit', 'delete', 'execute'] },
  { resource: 'report', actions: ['view', 'create', 'edit', 'delete'] },
];

interface RoleForm {
  name: string;
  display_name: string;
  description: string;
}

const EMPTY_FORM: RoleForm = {
  name: '',
  display_name: '',
  description: '',
};

export default function Roles() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editRole, setEditRole] = useState<Role | null>(null);
  const [form, setForm] = useState<RoleForm>(EMPTY_FORM);
  const [deleteTarget, setDeleteTarget] = useState<Role | null>(null);
  const [expandedRoleId, setExpandedRoleId] = useState<number | null>(null);
  const [permissions, setPermissions] = useState<Record<number, RolePermission[]>>({});
  const [permLoading, setPermLoading] = useState<number | null>(null);

  const loadRoles = useCallback(async () => {
    setLoading(true);
    try {
      const res = await governanceApi.listRoles();
      setRoles(res.data.items || res.data || []);
    } catch {
      toast.error('加载角色列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadRoles(); }, [loadRoles]);

  const loadPermissions = async (roleId: number) => {
    setPermLoading(roleId);
    try {
      const res = await governanceApi.listPermissions(roleId);
      setPermissions(prev => ({ ...prev, [roleId]: res.data || [] }));
    } catch {
      toast.error('加载权限失败');
    } finally {
      setPermLoading(null);
    }
  };

  const toggleExpand = async (roleId: number) => {
    if (expandedRoleId === roleId) {
      setExpandedRoleId(null);
      return;
    }
    setExpandedRoleId(roleId);
    if (!permissions[roleId]) {
      await loadPermissions(roleId);
    }
  };

  const openCreate = () => {
    setEditRole(null);
    setForm(EMPTY_FORM);
    setFormOpen(true);
  };

  const openEdit = (role: Role) => {
    setEditRole(role);
    setForm({
      name: role.name,
      display_name: role.display_name,
      description: role.description || '',
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error('请输入角色标识'); return; }
    if (!form.display_name.trim()) { toast.error('请输入角色名称'); return; }
    try {
      if (editRole) {
        await governanceApi.updateRole(editRole.id, form);
        toast.success('已更新');
      } else {
        await governanceApi.createRole(form);
        toast.success('已创建');
      }
      setFormOpen(false);
      loadRoles();
    } catch {
      toast.error('保存失败');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await governanceApi.deleteRole(deleteTarget.id);
      toast.success('已删除');
      setDeleteTarget(null);
      loadRoles();
    } catch {
      toast.error('删除失败');
    }
  };

  const hasPermission = (roleId: number, resource: string, action: string): boolean => {
    const perms = permissions[roleId] || [];
    return perms.some(p => p.resource === resource && p.action === action);
  };

  const handlePermToggle = async (role: Role, resource: string, action: string, checked: boolean) => {
    const currentPerms = permissions[role.id] || [];
    let newPerms: { resource: string; action: string }[];
    if (checked) {
      newPerms = [...currentPerms.map(p => ({ resource: p.resource, action: p.action })), { resource, action }];
    } else {
      newPerms = currentPerms
        .filter(p => !(p.resource === resource && p.action === action))
        .map(p => ({ resource: p.resource, action: p.action }));
    }
    try {
      await governanceApi.updatePermissions(role.id, newPerms);
      setPermissions(prev => ({
        ...prev,
        [role.id]: newPerms.map((p, i) => ({ id: i, role_id: role.id, ...p })),
      }));
    } catch {
      toast.error('更新权限失败');
    }
  };

  const resources = DEFAULT_RESOURCES as any[];

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">角色管理</h1>
          <p className="text-muted-foreground text-sm mt-1">管理系统角色和权限配置</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadRoles}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            新建角色
          </Button>
        </div>
      </div>

      {/* Roles List */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium w-8"></th>
              <th className="text-left p-3 font-medium">角色标识</th>
              <th className="text-left p-3 font-medium">角色名称</th>
              <th className="text-left p-3 font-medium">描述</th>
              <th className="text-center p-3 font-medium">系统角色</th>
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
            ) : roles.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-8 text-center text-muted-foreground">
                  暂无角色
                </td>
              </tr>
            ) : (
              roles.map(role => (
                <>
                  <tr
                    key={role.id}
                    className="border-t hover:bg-muted/30 cursor-pointer"
                    onClick={() => toggleExpand(role.id)}
                  >
                    <td className="p-3">
                      {expandedRoleId === role.id ? (
                        <ChevronDown className="w-4 h-4" />
                      ) : (
                        <ChevronRight className="w-4 h-4" />
                      )}
                    </td>
                    <td className="p-3">
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{role.name}</code>
                    </td>
                    <td className="p-3 font-medium">{role.display_name}</td>
                    <td className="p-3 text-muted-foreground text-xs truncate max-w-[300px]">
                      {role.description || '-'}
                    </td>
                    <td className="p-3 text-center">
                      {role.is_system ? (
                        <Badge variant="outline" className="bg-blue-500/10 text-blue-500 border-blue-500/20">
                          系统
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="bg-gray-500/10 text-gray-500 border-gray-500/20">
                          自定义
                        </Badge>
                      )}
                    </td>
                    <td className="p-3">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => { e.stopPropagation(); openEdit(role); }}
                          title="编辑"
                        >
                          <Edit className="w-4 h-4" />
                        </Button>
                        {!role.is_system && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={(e) => { e.stopPropagation(); setDeleteTarget(role); }}
                            title="删除"
                          >
                            <Trash2 className="w-4 h-4 text-destructive" />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {/* Expanded Permission Matrix */}
                  {expandedRoleId === role.id && (
                    <tr key={`${role.id}-perms`}>
                      <td colSpan={6} className="p-0">
                        <div className="bg-muted/20 p-4 border-t">
                          {permLoading === role.id ? (
                            <div className="text-center text-muted-foreground py-4">加载权限中...</div>
                          ) : (
                            <div className="space-y-1">
                              <div className="flex items-center gap-2 mb-3">
                                <Shield className="w-4 h-4 text-muted-foreground" />
                                <span className="text-sm font-medium">权限矩阵</span>
                              </div>
                              <div className="overflow-x-auto">
                                <table className="w-full text-xs">
                                  <thead>
                                    <tr className="border-b">
                                      <th className="text-left p-2 font-medium">资源</th>
                                      {['查看', '创建', '编辑', '删除', '执行', '管理成员'].map(label => (
                                        <th key={label} className="text-center p-2 font-medium">{label}</th>
                                      ))}
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {resources.map(res => (
                                      <tr key={res.resource} className="border-b last:border-0">
                                        <td className="p-2 font-medium">{res.resource}</td>
                                        {['view', 'create', 'edit', 'delete', 'execute', 'manage_members'].map(action => {
                                          const supported = res.actions.includes(action);
                                          if (!supported) {
                                            return (
                                              <td key={action} className="p-2 text-center text-muted-foreground">
                                                -
                                              </td>
                                            );
                                          }
                                          return (
                                            <td key={action} className="p-2 text-center">
                                              <input
                                                type="checkbox"
                                                checked={hasPermission(role.id, res.resource, action)}
                                                onChange={e => handlePermToggle(role, res.resource, action, e.target.checked)}
                                                className="cursor-pointer"
                                              />
                                            </td>
                                          );
                                        })}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent
          className="max-w-lg"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{editRole ? '编辑角色' : '新建角色'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">角色标识 *</label>
              <Input
                className="mt-1"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="如: data_analyst"
                disabled={editRole?.is_system}
              />
            </div>
            <div>
              <label className="text-sm font-medium">角色名称 *</label>
              <Input
                className="mt-1"
                value={form.display_name}
                onChange={e => setForm(f => ({ ...f, display_name: e.target.value }))}
                placeholder="如: 数据分析师"
              />
            </div>
            <div>
              <label className="text-sm font-medium">描述</label>
              <Input
                className="mt-1"
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="角色描述（可选）"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave}>{editRole ? '更新' : '创建'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除角色「{deleteTarget?.display_name}」吗？已分配该角色的用户将失去相关权限。
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
