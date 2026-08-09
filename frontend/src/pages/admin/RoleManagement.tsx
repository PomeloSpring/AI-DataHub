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
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import {
  Plus, Edit, Trash2, Shield, Users, Settings, UserPlus, UserMinus,
  Database, Table, Columns3, Key,
} from 'lucide-react';
import client from '@/api/client';

interface Role {
  id: number;
  name: string;
  display_name: string;
  description: string;
  is_system: number;
  is_active: number;
  users?: Array<{ user_id: number; username: string }>;
}

interface DatasourceAccess {
  datasource_id: number;
  datasource_name: string;
  db_type: string;
}

interface TableAccess {
  datasource_id: number;
  table_name: string;
  access_type: string;
}

interface ColumnAccess {
  datasource_id: number;
  table_name: string;
  column_name: string;
  access_type: string;
  mask_pattern: string;
}

export default function RoleManagement() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editRole, setEditRole] = useState<Role | null>(null);
  const [formName, setFormName] = useState('');
  const [formDisplayName, setFormDisplayName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Role | null>(null);

  // Permission config state
  const [permTarget, setPermTarget] = useState<Role | null>(null);
  const [permTab, setPermTab] = useState('datasources');

  // Datasource access
  const [dsAccess, setDsAccess] = useState<DatasourceAccess[]>([]);
  const [allDatasources, setAllDatasources] = useState<any[]>([]);

  // Table access
  const [tableAccess, setTableAccess] = useState<TableAccess[]>([]);
  const [newTableDsId, setNewTableDsId] = useState(0);
  const [newTableName, setNewTableName] = useState('');

  // Column access
  const [colAccess, setColAccess] = useState<ColumnAccess[]>([]);
  const [newColDsId, setNewColDsId] = useState(0);
  const [newColTable, setNewColTable] = useState('');
  const [newColName, setNewColName] = useState('');
  const [newColType, setNewColType] = useState('hidden');

  // Attributes
  const [attrs, setAttrs] = useState<Record<string, string>>({});
  const [newAttrKey, setNewAttrKey] = useState('');
  const [newAttrValue, setNewAttrValue] = useState('');

  // User assignment
  const [userTarget, setUserTarget] = useState<Role | null>(null);
  const [allUsers, setAllUsers] = useState<any[]>([]);
  const [roleUsers, setRoleUsers] = useState<Array<{ user_id: number; username: string }>>([]);

  const loadRoles = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/admin/roles');
      setRoles(Array.isArray(data) ? data : []);
    } catch {
      toast.error('加载角色失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadRoles(); }, [loadRoles]);

  // ── Role CRUD ────────────────────────────────────────────────────

  const openCreate = () => {
    setEditRole(null);
    setFormName(''); setFormDisplayName(''); setFormDesc('');
    setFormOpen(true);
  };

  const openEdit = (role: Role) => {
    setEditRole(role);
    setFormName(role.name); setFormDisplayName(role.display_name); setFormDesc(role.description || '');
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!formName || !formDisplayName) { toast.error('请填写标识和名称'); return; }
    setSaving(true);
    try {
      if (editRole) {
        await client.put(`/admin/roles/${editRole.id}`, { display_name: formDisplayName, description: formDesc });
        toast.success('已更新');
      } else {
        await client.post('/admin/roles', { name: formName, display_name: formDisplayName, description: formDesc });
        toast.success('已创建');
      }
      setFormOpen(false); loadRoles();
    } catch (e: any) { toast.error(e.response?.data?.detail || '保存失败'); }
    finally { setSaving(false); }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/admin/roles/${deleteTarget.id}`);
      toast.success('已删除'); setDeleteTarget(null); loadRoles();
    } catch (e: any) { toast.error(e.response?.data?.detail || '删除失败'); }
  };

  // ── Permission Config ────────────────────────────────────────────

  const openPermissions = async (role: Role) => {
    setPermTarget(role);
    setPermTab('datasources');
    try {
      // Load datasources
      const { data: dsData } = await client.get('/datasources/');
      setAllDatasources(Array.isArray(dsData) ? dsData : []);
      // Load role permissions
      const { data: dsAccess } = await client.get(`/admin/roles/${role.id}/datasources`);
      setDsAccess(dsAccess || []);
      const { data: tAccess } = await client.get(`/admin/roles/${role.id}/tables`);
      setTableAccess(tAccess || []);
      const { data: cAccess } = await client.get(`/admin/roles/${role.id}/columns`);
      setColAccess(cAccess || []);
      const { data: attrData } = await client.get(`/admin/roles/${role.id}/attributes`, { params: { workspace_id: 0 } });
      setAttrs(attrData || {});
    } catch { /* ignore */ }
  };

  const saveDatasourceAccess = async (dsIds: number[]) => {
    if (!permTarget) return;
    try {
      await client.put(`/admin/roles/${permTarget.id}/datasources`, { datasource_ids: dsIds });
      setDsAccess(dsIds.map(id => {
        const ds = allDatasources.find((d: any) => d.id === id);
        return { datasource_id: id, datasource_name: ds?.name || '', db_type: ds?.db_type || '' };
      }));
      toast.success('数据源权限已保存');
    } catch { toast.error('保存失败'); }
  };

  const toggleDsAccess = (dsId: number) => {
    const current = dsAccess.map(d => d.datasource_id);
    if (current.includes(dsId)) {
      saveDatasourceAccess(current.filter(id => id !== dsId));
    } else {
      saveDatasourceAccess([...current, dsId]);
    }
  };

  const saveTableAccess = async () => {
    if (!permTarget) return;
    try {
      await client.put(`/admin/roles/${permTarget.id}/tables`, {
        tables: tableAccess.map(t => ({ datasource_id: t.datasource_id, table_name: t.table_name, access_type: t.access_type }))
      });
      toast.success('表权限已保存');
    } catch { toast.error('保存失败'); }
  };

  const addTableAccess = () => {
    if (!newTableName) { toast.error('请输入表名'); return; }
    setTableAccess(prev => [...prev, { datasource_id: newTableDsId, table_name: newTableName, access_type: 'read' }]);
    setNewTableName('');
  };

  const removeTableAccess = (idx: number) => {
    setTableAccess(prev => prev.filter((_, i) => i !== idx));
  };

  const saveColumnAccess = async () => {
    if (!permTarget) return;
    try {
      await client.put(`/admin/roles/${permTarget.id}/columns`, {
        columns: colAccess.map(c => ({
          datasource_id: c.datasource_id, table_name: c.table_name,
          column_name: c.column_name, access_type: c.access_type, mask_pattern: c.mask_pattern
        }))
      });
      toast.success('列权限已保存');
    } catch { toast.error('保存失败'); }
  };

  const addColumnAccess = () => {
    if (!newColTable || !newColName) { toast.error('请输入表名和列名'); return; }
    setColAccess(prev => [...prev, {
      datasource_id: newColDsId, table_name: newColTable,
      column_name: newColName, access_type: newColType, mask_pattern: ''
    }]);
    setNewColName('');
  };

  const removeColumnAccess = (idx: number) => {
    setColAccess(prev => prev.filter((_, i) => i !== idx));
  };

  const saveAttributes = async () => {
    if (!permTarget) return;
    try {
      await client.put(`/admin/roles/${permTarget.id}/attributes`, { workspace_id: 0, attributes: attrs });
      toast.success('属性已保存');
    } catch { toast.error('保存失败'); }
  };

  // ── User Assignment ──────────────────────────────────────────────

  const openUsers = async (role: Role) => {
    setUserTarget(role);
    try {
      const { data: roleData } = await client.get(`/admin/roles/${role.id}`);
      setRoleUsers(roleData.users || []);
      const { data: usersData } = await client.get('/admin/users', { params: { size: 9999 } });
      setAllUsers(usersData.items || usersData || []);
    } catch { setRoleUsers([]); }
  };

  const handleAssignUser = async (userId: number) => {
    if (!userTarget) return;
    try {
      await client.post(`/admin/roles/${userTarget.id}/users`, { user_id: userId, workspace_id: 0 });
      toast.success('已分配');
      const { data } = await client.get(`/admin/roles/${userTarget.id}`);
      setRoleUsers(data.users || []);
    } catch { toast.error('分配失败'); }
  };

  const handleRemoveUser = async (userId: number) => {
    if (!userTarget) return;
    try {
      await client.delete(`/admin/roles/${userTarget.id}/users/${userId}`, { params: { workspace_id: 0 } });
      toast.success('已移除');
      const { data } = await client.get(`/admin/roles/${userTarget.id}`);
      setRoleUsers(data.users || []);
    } catch { toast.error('移除失败'); }
  };

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
        <Shield className="h-6 w-6" />
        角色权限
      </h1>

      <div className="flex justify-between items-center mb-4">
        <p className="text-sm text-muted-foreground">共 {roles.length} 个角色 · 系统角色不可删除</p>
        <Button onClick={openCreate} size="sm"><Plus className="h-4 w-4 mr-2" />新建角色</Button>
      </div>

      {loading ? (
        <div className="text-center py-8 text-muted-foreground">加载中...</div>
      ) : roles.length === 0 ? (
        <div className="text-center py-8 text-muted-foreground">暂无角色</div>
      ) : (
        <div className="space-y-3">
          {roles.map(role => (
            <div key={role.id} className="border rounded-lg p-4">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium">{role.display_name}</span>
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{role.name}</code>
                    {role.is_system ? <Badge variant="secondary">系统</Badge> : <Badge variant="outline">自定义</Badge>}
                  </div>
                  {role.description && <p className="text-sm text-muted-foreground">{role.description}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => openPermissions(role)} title="权限配置">
                    <Key className="h-4 w-4" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => openUsers(role)} title="用户分配">
                    <Users className="h-4 w-4" />
                  </Button>
                  {!role.is_system && (
                    <>
                      <Button variant="outline" size="sm" onClick={() => openEdit(role)}><Edit className="h-4 w-4" /></Button>
                      <Button variant="outline" size="sm" onClick={() => setDeleteTarget(role)}><Trash2 className="h-4 w-4" /></Button>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Permission Config Dialog ────────────────────────────── */}
      <Dialog open={!!permTarget} onOpenChange={() => setPermTarget(null)}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-auto">
          <DialogHeader>
            <DialogTitle>权限配置 — {permTarget?.display_name}</DialogTitle>
            <DialogDescription>配置该角色的数据访问范围</DialogDescription>
          </DialogHeader>
          <Tabs value={permTab} onValueChange={setPermTab}>
            <TabsList className="grid grid-cols-4 w-full">
              <TabsTrigger value="datasources"><Database className="h-4 w-4 mr-1" />数据源</TabsTrigger>
              <TabsTrigger value="tables"><Table className="h-4 w-4 mr-1" />表</TabsTrigger>
              <TabsTrigger value="columns"><Columns3 className="h-4 w-4 mr-1" />列</TabsTrigger>
              <TabsTrigger value="attributes"><Settings className="h-4 w-4 mr-1" />属性</TabsTrigger>
            </TabsList>

            {/* Datasource Access */}
            <TabsContent value="datasources" className="space-y-3">
              <p className="text-sm text-muted-foreground">勾选该角色可访问的数据源（不勾选=不限制）</p>
              {allDatasources.map((ds: any) => (
                <div key={ds.id} className="flex items-center justify-between border rounded px-3 py-2">
                  <div className="flex items-center gap-2">
                    <Database className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">{ds.name}</span>
                    <Badge variant="outline" className="text-xs">{ds.db_type}</Badge>
                  </div>
                  <Switch
                    checked={dsAccess.some(d => d.datasource_id === ds.id)}
                    onCheckedChange={() => toggleDsAccess(ds.id)}
                  />
                </div>
              ))}
              {allDatasources.length === 0 && <p className="text-sm text-muted-foreground py-4 text-center">暂无数据源</p>}
            </TabsContent>

            {/* Table Access */}
            <TabsContent value="tables" className="space-y-3">
              <p className="text-sm text-muted-foreground">限制该角色只能访问指定的表（不配置=不限制）</p>
              <div className="flex gap-2">
                <select className="border rounded px-2 py-1 text-sm" value={newTableDsId} onChange={e => setNewTableDsId(Number(e.target.value))}>
                  <option value={0}>全部数据源</option>
                  {allDatasources.map((ds: any) => <option key={ds.id} value={ds.id}>{ds.name}</option>)}
                </select>
                <Input placeholder="表名" value={newTableName} onChange={e => setNewTableName(e.target.value)} className="flex-1" />
                <Button size="sm" onClick={addTableAccess} disabled={!newTableName}><Plus className="h-4 w-4" /></Button>
              </div>
              {tableAccess.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">暂无限制，可访问所有表</p>
              ) : (
                <div className="space-y-1">
                  {tableAccess.map((t, i) => (
                    <div key={i} className="flex items-center justify-between border rounded px-3 py-1.5">
                      <div className="flex items-center gap-2">
                        <Table className="h-3 w-3 text-muted-foreground" />
                        <code className="text-sm">{t.table_name}</code>
                        {t.datasource_id > 0 && <Badge variant="outline" className="text-xs">DS:{t.datasource_id}</Badge>}
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => removeTableAccess(i)}><Trash2 className="h-3 w-3" /></Button>
                    </div>
                  ))}
                </div>
              )}
              {tableAccess.length > 0 && (
                <Button size="sm" onClick={saveTableAccess}>保存表权限</Button>
              )}
            </TabsContent>

            {/* Column Access */}
            <TabsContent value="columns" className="space-y-3">
              <p className="text-sm text-muted-foreground">控制列的可见性和脱敏（不配置=不限制）</p>
              <div className="flex gap-2 flex-wrap">
                <select className="border rounded px-2 py-1 text-sm" value={newColDsId} onChange={e => setNewColDsId(Number(e.target.value))}>
                  <option value={0}>全部</option>
                  {allDatasources.map((ds: any) => <option key={ds.id} value={ds.id}>{ds.name}</option>)}
                </select>
                <Input placeholder="表名" value={newColTable} onChange={e => setNewColTable(e.target.value)} className="w-32" />
                <Input placeholder="列名" value={newColName} onChange={e => setNewColName(e.target.value)} className="w-32" />
                <select className="border rounded px-2 py-1 text-sm" value={newColType} onChange={e => setNewColType(e.target.value)}>
                  <option value="hidden">隐藏</option>
                  <option value="masked">脱敏</option>
                  <option value="visible">可见</option>
                </select>
                <Button size="sm" onClick={addColumnAccess} disabled={!newColTable || !newColName}><Plus className="h-4 w-4" /></Button>
              </div>
              {colAccess.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">暂无列级限制</p>
              ) : (
                <div className="space-y-1">
                  {colAccess.map((c, i) => (
                    <div key={i} className="flex items-center justify-between border rounded px-3 py-1.5">
                      <div className="flex items-center gap-2">
                        <Columns3 className="h-3 w-3 text-muted-foreground" />
                        <code className="text-sm">{c.table_name}.{c.column_name}</code>
                        <Badge variant={c.access_type === 'hidden' ? 'destructive' : c.access_type === 'masked' ? 'secondary' : 'default'} className="text-xs">
                          {c.access_type === 'hidden' ? '隐藏' : c.access_type === 'masked' ? '脱敏' : '可见'}
                        </Badge>
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => removeColumnAccess(i)}><Trash2 className="h-3 w-3" /></Button>
                    </div>
                  ))}
                </div>
              )}
              {colAccess.length > 0 && (
                <Button size="sm" onClick={saveColumnAccess}>保存列权限</Button>
              )}
            </TabsContent>

            {/* Attributes */}
            <TabsContent value="attributes" className="space-y-3">
              <p className="text-sm text-muted-foreground">配置数据范围属性，RLS 策略中的 :user_xxx 会替换为对应值</p>
              <div className="flex gap-2">
                <Input placeholder="属性名，如 region" value={newAttrKey} onChange={e => setNewAttrKey(e.target.value)} className="flex-1" />
                <Input placeholder="属性值，如 cn" value={newAttrValue} onChange={e => setNewAttrValue(e.target.value)} className="flex-1" />
                <Button size="sm" onClick={() => { if (newAttrKey) { setAttrs(p => ({ ...p, [newAttrKey]: newAttrValue })); setNewAttrKey(''); setNewAttrValue(''); } }} disabled={!newAttrKey}>
                  <Plus className="h-4 w-4" />
                </Button>
              </div>
              {Object.keys(attrs).length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">暂无属性</p>
              ) : (
                <div className="space-y-1">
                  {Object.entries(attrs).map(([k, v]) => (
                    <div key={k} className="flex items-center justify-between border rounded px-3 py-1.5">
                      <div className="flex items-center gap-2">
                        <code className="text-sm font-medium">{k}</code>
                        <span className="text-muted-foreground">=</span>
                        <code className="text-sm">{v}</code>
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => { const n = { ...attrs }; delete n[k]; setAttrs(n); }}>
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
              {Object.keys(attrs).length > 0 && (
                <Button size="sm" onClick={saveAttributes}>保存属性</Button>
              )}
            </TabsContent>
          </Tabs>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPermTarget(null)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Create/Edit Dialog ──────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editRole ? '编辑角色' : '新建角色'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div><Label>角色标识 *</Label><Input value={formName} onChange={e => setFormName(e.target.value)} placeholder="如 sales_cn" disabled={!!editRole} /></div>
            <div><Label>显示名称 *</Label><Input value={formDisplayName} onChange={e => setFormDisplayName(e.target.value)} placeholder="如 中国区销售" /></div>
            <div><Label>描述</Label><Textarea value={formDesc} onChange={e => setFormDesc(e.target.value)} rows={2} /></div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>{saving ? '保存中...' : '保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── User Assignment Dialog ──────────────────────────────── */}
      <Dialog open={!!userTarget} onOpenChange={() => setUserTarget(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>用户分配 — {userTarget?.display_name}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label className="mb-2 block">已分配用户 ({roleUsers.length})</Label>
              {roleUsers.length === 0 ? <p className="text-sm text-muted-foreground py-2">暂无用户</p> : (
                <div className="space-y-1 max-h-[200px] overflow-auto">
                  {roleUsers.map(u => (
                    <div key={u.user_id} className="flex items-center justify-between border rounded px-3 py-1.5">
                      <span className="text-sm">{u.username}</span>
                      <Button variant="ghost" size="sm" onClick={() => handleRemoveUser(u.user_id)}><UserMinus className="h-3 w-3" /></Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <Label className="mb-2 block">添加用户</Label>
              <div className="max-h-[200px] overflow-auto space-y-1">
                {allUsers.filter(u => !roleUsers.some(r => r.user_id === u.id)).map(u => (
                  <div key={u.id} className="flex items-center justify-between border rounded px-3 py-1.5">
                    <span className="text-sm">{u.username}</span>
                    <Button variant="ghost" size="sm" onClick={() => handleAssignUser(u.id)}><UserPlus className="h-3 w-3" /></Button>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setUserTarget(null)}>关闭</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>确认删除</DialogTitle></DialogHeader>
          <p>确定要删除角色 "{deleteTarget?.display_name}" 吗？</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
