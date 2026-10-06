import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
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
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  Plus, Edit, Trash2, Shield, Users, UserPlus, UserMinus,
  Database, Lock, Key, Bot, LayoutDashboard, ListChecks, ChevronDown, ChevronUp,
} from 'lucide-react';
import client from '@/api/client';
import { listRLSPolicies, type RLSPolicy } from '@/api/rls';

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

/** 权限码功能项（含只读接口绑定） */
interface PermItem {
  perm_code: string;
  label: string;
  description: string;
  api_pattern: string;
  api_method: string;
  /** AI 可调用级别（配置值）：none=不开放 / read=只读 / write=可读可写 */
  ai_access: 'none' | 'read' | 'write';
  /** 生效级别（被涉密硬上界收窄后的值），UI 应以它为准展示实际效果 */
  ai_effective: 'none' | 'read' | 'write';
  /** 涉密项：级别由代码层硬上界卡死，UI 置灰不可改 */
  ai_locked: boolean;
  /** 登记了才会生成 LLM 功能工具 */
  ai_action_key: string;
  /** 不开放/只读的原因，必须展示给用户 */
  ai_note: string;
}

/** 菜单分组（菜单 → 功能） */
interface MenuPermGroup {
  menu_key: string;
  label: string;
  section: string;
  module: string;
  module_label: string;
  sort: number;
  /** 菜单级 AI 默认值，仅供批量套用；生效级别以各功能项为准 */
  ai_access: 'none' | 'read' | 'write';
  permissions: PermItem[];
}

const AI_LEVEL_LABEL: Record<string, string> = {
  none: '不开放给 AI',
  read: '只读可见',
  write: '可读可写',
};

export default function RoleManagement() {
  const navigate = useNavigate();
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

  // 数据安全策略绑定（勾选才生效）
  const [rlsPolicies, setRlsPolicies] = useState<RLSPolicy[]>([]);
  const [roleRlsPolicies, setRoleRlsPolicies] = useState<Set<number>>(new Set());

  // 看板可见范围（看板不按工作空间归属, 平铺勾选; 可见性由角色授权 fail-closed 裁决）
  const [dashboardCatalog, setDashboardCatalog] = useState<Array<{ id: number; name: string; status: string }>>([]);
  const [roleDashboards, setRoleDashboards] = useState<number[]>([]);

  // 菜单与功能权限（权限码模型）
  const [permGroups, setPermGroups] = useState<MenuPermGroup[]>([]);
  const [standalonePerms, setStandalonePerms] = useState<PermItem[]>([]);
  const [rolePerms, setRolePerms] = useState<Set<string>>(new Set());
  const [expandedPerms, setExpandedPerms] = useState<Set<string>>(new Set());
  // 菜单分组展开/收起(长列表浏览用): key=menu_key, '__standalone__'=通用功能
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const toggleGroupCollapse = (key: string) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };
  const setAllCollapsed = (collapsed: boolean) => {
    const keys = [...permGroups.map(g => g.menu_key), '__standalone__'];
    setCollapsedGroups(collapsed ? new Set(keys) : new Set());
  };

  // User assignment
  const [userTarget, setUserTarget] = useState<Role | null>(null);
  const [allUsers, setAllUsers] = useState<any[]>([]);
  const [roleUsers, setRoleUsers] = useState<Array<{ user_id: number; username: string }>>([]);

  const loadRoles = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/roles/');
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
        await client.put(`/roles/${editRole.id}`, { display_name: formDisplayName, description: formDesc });
        toast.success('已更新');
      } else {
        await client.post('/roles/', { name: formName, display_name: formDisplayName, description: formDesc });
        toast.success(`角色已创建，已同步创建「${formDisplayName || formName}」的 AS-BOT，可前往 AS-BOT 配置页编辑`, {
          action: {
            label: '去编辑',
            onClick: () => navigate('/system/as-bots'),
          },
        });
      }
      setFormOpen(false); loadRoles();
    } catch (e: any) { toast.error(e.response?.data?.detail || '保存失败'); }
    finally { setSaving(false); }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/roles/${deleteTarget.id}`);
      toast.success('已删除'); setDeleteTarget(null); loadRoles();
    } catch (e: any) { toast.error(e.response?.data?.detail || '删除失败'); }
  };

  // ── Permission Config ────────────────────────────────────────────

  const openPermissions = async (role: Role) => {
    setPermTarget(role);
    setPermTab('datasources');
    // 分块独立加载: 单块失败显式报错, 不连坐其它页签
    const load = async (fn: () => Promise<void>, errMsg: string) => {
      try { await fn(); } catch { toast.error(errMsg); }
    };
    await load(async () => {
      const { data } = await client.get('/datasources/');
      setAllDatasources(Array.isArray(data) ? data : []);
    }, '加载数据源列表失败');
    await load(async () => {
      const { data } = await client.get(`/roles/${role.id}/datasources`);
      setDsAccess(data || []);
    }, '加载数据源权限失败');
    await load(async () => {
      const data = await listRLSPolicies(0, undefined, undefined, 1, 100);
      setRlsPolicies(data.items || []);
      const { data: bound } = await client.get(`/roles/${role.id}/rls-policies`);
      setRoleRlsPolicies(new Set(bound.policy_ids || []));
    }, '加载数据安全策略失败');
    await load(async () => {
      const { data } = await client.get('/roles/dashboard-catalog');
      setDashboardCatalog(Array.isArray(data) ? data : []);
      const { data: grants } = await client.get(`/roles/${role.id}/dashboards`);
      setRoleDashboards((grants || []).map((r: any) => r.dashboard_id));
    }, '加载看板可见范围失败');
    await load(async () => {
      const { data: reg } = await client.get('/roles/perm-registry');
      setPermGroups(reg.menu_groups || []);
      setStandalonePerms(reg.standalone || []);
      const { data: permData } = await client.get(`/roles/${role.id}/permissions`);
      setRolePerms(new Set(permData.permissions || []));
    }, '加载功能权限失败');
  };

  const saveDatasourceAccess = async (dsIds: number[]) => {
    if (!permTarget) return;
    try {
      await client.put(`/roles/${permTarget.id}/datasources`, { datasource_ids: dsIds });
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

  const toggleRlsPolicy = (policyId: number) => {
    if (!permTarget) return;
    const next = roleRlsPolicies.has(policyId)
      ? [...roleRlsPolicies].filter(id => id !== policyId)
      : [...roleRlsPolicies, policyId];
    client.put(`/roles/${permTarget.id}/rls-policies`, { policy_ids: next })
      .then(() => { setRoleRlsPolicies(new Set(next)); toast.success('数据安全策略绑定已保存'); })
      .catch(() => toast.error('保存失败'));
  };

  // ── Dashboard Visibility ──────────────────────────────────────

  const toggleDashboard = (dashId: number) => {
    if (!permTarget) return;
    const next = roleDashboards.includes(dashId)
      ? roleDashboards.filter(id => id !== dashId)
      : [...roleDashboards, dashId];
    client.put(`/roles/${permTarget.id}/dashboards`, { dashboard_ids: next })
      .then(() => { setRoleDashboards(next); toast.success('看板可见范围已保存'); })
      .catch(() => toast.error('保存失败'));
  };

  // ── Menu & Function Permissions (权限码) ────────────────────

  const togglePerm = (code: string) => {
    setRolePerms(prev => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code); else next.add(code);
      return next;
    });
  };

  const toggleMenuPerms = (codes: string[]) => {
    setRolePerms(prev => {
      const next = new Set(prev);
      const allOn = codes.length > 0 && codes.every(c => next.has(c));
      codes.forEach(c => { if (allOn) next.delete(c); else next.add(c); });
      return next;
    });
  };

  const toggleApiExpand = (code: string) => {
    setExpandedPerms(prev => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code); else next.add(code);
      return next;
    });
  };

  /** 设置功能项的 AI 可调用级别。
   *  涉密项后端会以 409 硬拒（不静默降级），这里把原因原样透出给用户。 */
  const handleAiAccess = async (code: string, level: 'none' | 'read' | 'write') => {
    const patch = (perm: PermItem, res: any): PermItem => ({
      ...perm,
      ai_access: (res.ai_access ?? level) as PermItem['ai_access'],
      ai_effective: (res.ai_effective ?? level) as PermItem['ai_effective'],
      ai_note: res.ai_note ?? perm.ai_note,
    });
    try {
      const { data } = await client.put(
        `/roles/perm-registry/${encodeURIComponent(code)}/ai-access`,
        { ai_access: level },
      );
      setPermGroups(prev => prev.map(g => ({
        ...g,
        permissions: g.permissions.map(p => (p.perm_code === code ? patch(p, data) : p)),
      })));
      setStandalonePerms(prev => prev.map(p => (p.perm_code === code ? patch(p, data) : p)));
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '设置 AI 可调用级别失败');
    }
  };

  const saveFunctionPerms = async () => {
    if (!permTarget) return;
    try {
      await client.put(`/roles/${permTarget.id}/permissions`, { permissions: [...rolePerms] });
      toast.success('功能权限已保存');
    } catch { toast.error('保存失败'); }
  };

  // ── User Assignment ──────────────────────────────────────────────

  const openUsers = async (role: Role) => {
    setUserTarget(role);
    try {
      const { data: roleData } = await client.get(`/roles/${role.id}`);
      setRoleUsers(roleData.users || []);
      const { data: usersData } = await client.get('/users/', { params: { size: 200 } });
      setAllUsers(usersData.items || usersData || []);
    } catch { setRoleUsers([]); }
  };

  const handleAssignUser = async (userId: number) => {
    if (!userTarget) return;
    try {
      await client.post(`/roles/${userTarget.id}/users`, { user_id: userId, workspace_id: 0 });
      toast.success('已分配');
      const { data } = await client.get(`/roles/${userTarget.id}`);
      setRoleUsers(data.users || []);
    } catch { toast.error('分配失败'); }
  };

  const handleRemoveUser = async (userId: number) => {
    if (!userTarget) return;
    try {
      await client.delete(`/roles/${userTarget.id}/users/${userId}`, { params: { workspace_id: 0 } });
      toast.success('已移除');
      const { data } = await client.get(`/roles/${userTarget.id}`);
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
                  <Button variant="outline" size="sm" onClick={() => navigate('/system/as-bots')} title="编辑 AS-BOT 配置">
                    <Bot className="h-4 w-4" />
                  </Button>
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
            <DialogDescription>配置该角色的数据访问范围与功能权限</DialogDescription>
          </DialogHeader>
          <Tabs value={permTab} onValueChange={setPermTab}>
            <TabsList className="grid grid-cols-4 w-full">
              <TabsTrigger value="datasources"><Database className="h-3.5 w-3.5 mr-1" />数据源</TabsTrigger>
              <TabsTrigger value="policies"><Lock className="h-3.5 w-3.5 mr-1" />数据安全策略</TabsTrigger>
              <TabsTrigger value="dashboards"><LayoutDashboard className="h-3.5 w-3.5 mr-1" />看板</TabsTrigger>
              <TabsTrigger value="functions"><ListChecks className="h-3.5 w-3.5 mr-1" />菜单与功能</TabsTrigger>
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

            {/* 数据安全策略绑定（勾选才生效; 策略在 数据安全策略 页维护） */}
            <TabsContent value="policies" className="space-y-3">
              <p className="text-sm text-muted-foreground">勾选该角色生效的数据安全策略。策略在「数据安全策略」页维护（行/列/属性约束），此处仅选择绑定；勾选的策略对该角色生效，未勾选不生效。</p>
              {rlsPolicies.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">暂无策略，请先在「数据安全策略」页配置</p>
              ) : (
                <div className="space-y-1">
                  {rlsPolicies.map(p => (
                    <div key={p.id} className="flex items-center justify-between gap-2 border rounded px-3 py-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <Lock className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                          <span className="text-sm font-medium truncate">{p.name}</span>
                          <Badge variant="outline" className="text-xs flex-shrink-0">
                            {p.policy_type === 'row' ? '行级' : p.policy_type === 'column' ? '列级' : '行+列'}
                          </Badge>
                          {!p.is_active && <Badge variant="secondary" className="text-xs flex-shrink-0">已停用</Badge>}
                        </div>
                        <div className="pl-6 text-xs text-muted-foreground truncate">
                          表 <code>{p.table_name}</code>
                          {p.filter_expr && <> · <code>{p.filter_expr}</code></>}
                        </div>
                      </div>
                      <Switch
                        checked={roleRlsPolicies.has(p.id)}
                        onCheckedChange={() => toggleRlsPolicy(p.id)}
                      />
                    </div>
                  ))}
                </div>
              )}
            </TabsContent>

            {/* Dashboard Visibility */}
            <TabsContent value="dashboards" className="space-y-3">
              <p className="text-sm text-muted-foreground">勾选该角色可查看的看板。未勾选任何看板 = 除 admin 外不可见（fail-closed）</p>
              {dashboardCatalog.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">暂无看板</p>
              ) : (
                <div className="grid grid-cols-2 gap-1.5">
                  {dashboardCatalog.map(d => (
                    <div key={d.id} className="flex items-center justify-between gap-2 border rounded px-2 py-1.5">
                      <div className="flex items-center gap-2 min-w-0">
                        <LayoutDashboard className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                        <span className="text-xs truncate">{d.name}</span>
                        {d.status !== 'enabled' && <Badge variant="outline" className="text-[10px] flex-shrink-0">未启用</Badge>}
                      </div>
                      <Switch
                        checked={roleDashboards.includes(d.id)}
                        onCheckedChange={() => toggleDashboard(d.id)}
                      />
                    </div>
                  ))}
                </div>
              )}
            </TabsContent>

            {/* 菜单与功能（权限码，菜单→功能分组，接口只读） */}
            <TabsContent value="functions" className="space-y-3">
              <p className="text-sm text-muted-foreground">按「菜单 → 功能」分组配置该角色可用的功能权限；展开功能可查看其绑定的接口（只读，由权限注册表维护）。未配置任何功能 = 该角色不可用（fail-closed），admin 不受限。</p>
              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setAllCollapsed(false)}>全部展开</Button>
                <Button variant="outline" size="sm" onClick={() => setAllCollapsed(true)}>全部收起</Button>
              </div>
              {permGroups.length === 0 && standalonePerms.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">权限注册表为空</p>
              ) : (
                <div className="space-y-4">
                  {['system', 'data', 'workspace'].map(mod => {
                    const groups = permGroups.filter(g => g.module === mod);
                    if (groups.length === 0) return null;
                    return (
                      <div key={mod}>
                        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                          {mod === 'system' ? '系统配置' : mod === 'data' ? '数据中台' : '工作空间'}
                        </h4>
                        <div className="space-y-2">
                          {groups.map(group => (
                            <PermGroupCard
                              key={group.menu_key}
                              group={group}
                              checked={rolePerms}
                              expanded={expandedPerms}
                              collapsed={collapsedGroups.has(group.menu_key)}
                              onToggleGroup={toggleMenuPerms}
                              onTogglePerm={togglePerm}
                              onToggleApi={toggleApiExpand}
                              onAiAccess={handleAiAccess}
                              onToggleCollapse={() => toggleGroupCollapse(group.menu_key)}
                            />
                          ))}
                        </div>
                      </div>
                    );
                  })}
                  {standalonePerms.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2 flex items-center justify-between">
                        <span>通用功能</span>
                        <Button
                          variant="ghost" size="sm" className="h-6 w-6 p-0"
                          aria-label={collapsedGroups.has('__standalone__') ? '展开 通用功能' : '收起 通用功能'}
                          onClick={() => toggleGroupCollapse('__standalone__')}
                        >
                          {collapsedGroups.has('__standalone__') ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
                        </Button>
                      </h4>
                      {!collapsedGroups.has('__standalone__') && (
                        <div className="border rounded p-2 space-y-1">
                        {standalonePerms.map(p => (
                          <PermRow
                            key={p.perm_code}
                            perm={p}
                            checked={rolePerms.has(p.perm_code)}
                            expanded={expandedPerms.has(p.perm_code)}
                            onToggle={() => togglePerm(p.perm_code)}
                            onToggleApi={() => toggleApiExpand(p.perm_code)}
                            onAiAccess={handleAiAccess}
                          />
                        ))}
                        </div>
                      )}
                    </div>
                  )}
                  <Button size="sm" onClick={saveFunctionPerms}>保存功能权限</Button>
                </div>
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

/** 功能项行：权限码勾选 + AI 可调用级别选择 + 只读接口绑定展开 */
function PermRow({ perm, checked, expanded, onToggle, onToggleApi, onAiAccess }: {
  perm: PermItem;
  checked: boolean;
  expanded: boolean;
  onToggle: () => void;
  onToggleApi: () => void;
  onAiAccess: (code: string, level: 'none' | 'read' | 'write') => void;
}) {
  const patterns = (perm.api_pattern || '').split(',').map(s => s.trim()).filter(Boolean);
  const effective = perm.ai_effective || perm.ai_access || 'none';
  const configured = perm.ai_access || 'none';
  // 生效值被硬上界压下来时要让用户看见，否则会以为改了就生效
  const capped = effective !== configured;
  return (
    <div className="border rounded px-2 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-2 min-w-0 cursor-pointer">
          <input type="checkbox" className="h-3.5 w-3.5 flex-shrink-0" checked={checked} onChange={onToggle} />
          <span className="text-xs font-medium truncate">{perm.label}</span>
          <code className="text-[10px] text-muted-foreground flex-shrink-0" title={perm.perm_code}>{perm.perm_code}</code>
        </label>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <Select
            value={configured}
            disabled={perm.ai_locked}
            onValueChange={(v: 'none' | 'read' | 'write') => onAiAccess(perm.perm_code, v)}
          >
            <SelectTrigger
              className="h-6 w-[110px] text-[10px]"
              title={perm.ai_locked
                ? `该功能涉敏，AI 级别已锁死为「${AI_LEVEL_LABEL[effective]}」。${perm.ai_note || ''}`
                : `AI 可调用级别：${AI_LEVEL_LABEL[effective]}`}
            >
              <SelectValue placeholder="AI 级别" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">不开放给 AI</SelectItem>
              <SelectItem value="read">只读可见</SelectItem>
              <SelectItem value="write">可读可写</SelectItem>
            </SelectContent>
          </Select>
          <Badge
            variant={effective === 'none' ? 'outline' : effective === 'read' ? 'secondary' : 'default'}
            className="text-[9px] flex-shrink-0"
            title={perm.ai_action_key ? `LLM 功能工具：${perm.ai_action_key}` : '未登记 LLM 功能工具，仅在 UI 上标记级别'}
          >
            {perm.ai_action_key ? 'AI 工具' : '未接 AI'}
          </Badge>
          {patterns.length > 0 && (
            <Button variant="ghost" size="sm" className="h-6 px-2 text-[10px] flex-shrink-0" onClick={onToggleApi}>
              {expanded ? '收起接口' : `接口(${patterns.length})`}
            </Button>
          )}
        </div>
      </div>
      {perm.description && <p className="text-[10px] text-muted-foreground pl-6">{perm.description}</p>}
      {(capped || perm.ai_note) && (
        <p className={`text-[10px] pl-6 ${capped ? 'text-amber-600' : 'text-muted-foreground'}`}>
          {capped && <>已按涉密边界收窄：「{AI_LEVEL_LABEL[configured]}」→「{AI_LEVEL_LABEL[effective]}」。{perm.ai_note}</>}
          {!capped && perm.ai_note}
        </p>
      )}
      {expanded && patterns.length > 0 && (
        <div className="mt-1 ml-6 space-y-0.5">
          {patterns.map((pat, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <Badge variant="outline" className="text-[9px] flex-shrink-0">{perm.api_method || '*'}</Badge>
              <code className="text-[10px] text-muted-foreground">{pat}</code>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 菜单卡片：组头勾选整组（含半选态）+ 功能项列表（支持展开/收起，方便浏览长列表） */
function PermGroupCard({ group, checked, expanded, collapsed, onToggleGroup, onTogglePerm, onToggleApi, onToggleCollapse, onAiAccess }: {
  group: MenuPermGroup;
  checked: Set<string>;
  expanded: Set<string>;
  collapsed: boolean;
  onToggleGroup: (codes: string[]) => void;
  onTogglePerm: (code: string) => void;
  onToggleApi: (code: string) => void;
  onToggleCollapse: () => void;
  onAiAccess: (code: string, level: 'none' | 'read' | 'write') => void;
}) {
  const codes = group.permissions.map(p => p.perm_code);
  const checkedCount = codes.filter(c => checked.has(c)).length;
  const all = codes.length > 0 && checkedCount === codes.length;
  const partial = checkedCount > 0 && !all;
  return (
    <div className="border rounded">
      <div className="flex items-center justify-between px-2 py-1.5 bg-muted/30">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            className="h-3.5 w-3.5"
            checked={all}
            ref={el => { if (el) el.indeterminate = partial; }}
            onChange={() => onToggleGroup(codes)}
          />
          <span className="text-xs font-medium">{group.label}</span>
          <span className="text-[10px] text-muted-foreground">{checkedCount}/{codes.length}</span>
        </label>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground">{group.section}</span>
          <Button
            variant="ghost" size="sm" className="h-6 w-6 p-0"
            aria-label={collapsed ? `展开 ${group.label}` : `收起 ${group.label}`}
            onClick={onToggleCollapse}
          >
            {collapsed ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
          </Button>
        </div>
      </div>
      {!collapsed && (
        <div className="p-2 space-y-1">
          {group.permissions.map(p => (
            <PermRow
              key={p.perm_code}
              perm={p}
              checked={checked.has(p.perm_code)}
              expanded={expanded.has(p.perm_code)}
              onToggle={() => onTogglePerm(p.perm_code)}
              onToggleApi={() => onToggleApi(p.perm_code)}
              onAiAccess={onAiAccess}
            />
          ))}
        </div>
      )}
    </div>
  );
}
