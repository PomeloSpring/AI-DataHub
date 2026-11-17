import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { t , t as tr } from '@/i18n/runtime';
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
import { toast } from '@/lib/toast';
import {
  Plus, Edit, Trash2, Shield, Users, UserPlus, UserMinus,
  Database, Lock, Key, Bot, LayoutDashboard, ListChecks, ChevronDown, ChevronUp, Layers,
} from 'lucide-react';
import client from '@/api/client';
import { listRLSPolicies, type RLSPolicy } from '@/api/rls';
import { useTranslation } from 'react-i18next';

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
  /**
   * AI 接入通道（后端 perm_link.ai_channel 派生，前端不另造判断）：
   * function_action=已登记功能工具 / perm_gate=执行期按权限码直执行把关 / none=无消费方。
   * 「AI 可调用级别」只有前两类真实生效；none 时必须显式告知“配置暂不生效”，
   * 不得让下拉框看起来能开通能力（空承诺）。
   */
  ai_channel?: 'function_action' | 'perm_gate' | 'none';
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

/**
 * 模块分组展示顺序与兼容名册（与一级模块切换器 SectionSwitcher 同口径）。
 *
 * 历史缺陷：角色页「菜单与功能」曾写死 `['system','data','workspace']`，
 * 内容中心（module='content'）整块菜单在角色配置页渲染不出来 —— 新菜单登记了也配不了、
 * 管不了（menu-permission-sync 规则里的“配了没用/该配的看不到”空承诺）。
 * 现在分组以接口返回的 module 全集为准（本表只管顺序与显示名兼容），
 * 未登记的模块排最后但不丢。
 */
const MODULE_ORDER = ['system', 'data', 'workspace', 'content'];
/** 模块分组显示名回退：与顶栏模块切换同一份菜单文案（切语言即时生效） */
const MODULE_LABEL_KEY: Record<string, string> = {
  system: 'switcher.system', data: 'switcher.data', workspace: 'switcher.workspace', content: 'switcher.content',
};

const AI_LEVEL_LABEL: Record<string, string> = {
  none: t('k_292a251e16'),
  read: t('k_7a33d9210f'),
  write: t('k_079280e097'),
};

/** 接入通道徽标文案 —— 与后端 perm_link.ai_channel 三值一一对应 */
const AI_CHANNEL_LABEL: Record<string, string> = {
  function_action: t('k_7a3c4d37bf'),
  perm_gate: t('k_fd392e283d'),
  none: t('k_8f5c4bfd26'),
};

export default function RoleManagement() {
  const { t } = useTranslation('ui');
  // 模块分组显示名走 menu 语言包（动态 key，签名放宽）
  const tMenu = useTranslation('menu').t as unknown as (key: string) => string;
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
  // 批量勾选（全选/反选）在途态：一次 PUT 整份清单，避免逐条请求把列表刷屏
  const [bulkSaving, setBulkSaving] = useState<'datasources' | 'datasets' | 'dashboards' | 'policies' | null>(null);

  // Datasource access
  const [dsAccess, setDsAccess] = useState<DatasourceAccess[]>([]);
  const [allDatasources, setAllDatasources] = useState<any[]>([]);

  // 数据安全策略绑定（勾选才生效）
  const [rlsPolicies, setRlsPolicies] = useState<RLSPolicy[]>([]);
  const [roleRlsPolicies, setRoleRlsPolicies] = useState<Set<number>>(new Set());

  // 看板可见范围（看板不按工作空间归属, 平铺勾选; 可见性由角色授权 fail-closed 裁决）
  const [dashboardCatalog, setDashboardCatalog] = useState<Array<{ id: number; name: string; status: string }>>([]);
  const [roleDashboards, setRoleDashboards] = useState<number[]>([]);

  // 数据集可见范围（同看板口径: 角色授权 fail-closed, admin 不受限）
  const [datasetCatalog, setDatasetCatalog] = useState<Array<{ id: number; name: string }>>([]);
  const [roleDatasets, setRoleDatasets] = useState<number[]>([]);

  // 菜单与功能权限（权限码模型）
  const [permGroups, setPermGroups] = useState<MenuPermGroup[]>([]);
  const [standalonePerms, setStandalonePerms] = useState<PermItem[]>([]);
  const [rolePerms, setRolePerms] = useState<Set<string>>(new Set());
  const [expandedPerms, setExpandedPerms] = useState<Set<string>>(new Set());
  // 菜单分组展开/收起(长列表浏览用): key=menu_key, '__standalone__'=通用功能
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  // 模块分桶从接口数据里推，不写死枚举（见 MODULE_ORDER 处注释）；
  // 显示名优先用服务端 module_label（业务名），缺失才回退本地表/原值。
  const permModules = useMemo(() => {
    const seen = new Map<string, string>();
    for (const g of permGroups) {
      const key = g.module || 'other';
      const labelKey = MODULE_LABEL_KEY[key];
      if (!seen.has(key)) seen.set(key, g.module_label || (labelKey ? tMenu(labelKey) : key));
    }
    const rank = (m: string) => {
      const i = MODULE_ORDER.indexOf(m);
      return i < 0 ? MODULE_ORDER.length : i;
    };
    return [...seen.entries()]
      .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
      .map(([value, label]) => ({ value, label }));
  }, [permGroups, tMenu]);
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
      toast.error(t('k_a32f3b2a85'));
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
    if (!formName || !formDisplayName) { toast.error(t('k_8f7d256f87')); return; }
    setSaving(true);
    try {
      if (editRole) {
        await client.put(`/roles/${editRole.id}`, { display_name: formDisplayName, description: formDesc });
        toast.success(t('k_1b92092d63'));
      } else {
        await client.post('/roles/', { name: formName, display_name: formDisplayName, description: formDesc });
        toast.success(t('k_b53fcc07c7', { p0: formDisplayName || formName }), {
          action: {
            label: t('k_eee0335136'),
            onClick: () => navigate('/system/as-bots'),
          },
        });
      }
      setFormOpen(false); loadRoles();
    } catch (e: any) { toast.error(e.response?.data?.detail || t('k_6de920b4e4')); }
    finally { setSaving(false); }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/roles/${deleteTarget.id}`);
      toast.success(t('k_5cc232620c')); setDeleteTarget(null); loadRoles();
    } catch (e: any) { toast.error(e.response?.data?.detail || t('k_acf0664a54')); }
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
    }, tr('k_c5ef0f3bfd'));
    await load(async () => {
      const { data } = await client.get(`/roles/${role.id}/datasources`);
      setDsAccess(data || []);
    }, tr('k_d398d22d01'));
    await load(async () => {
      const data = await listRLSPolicies(0, undefined, undefined, 1, 100);
      setRlsPolicies(data.items || []);
      const { data: bound } = await client.get(`/roles/${role.id}/rls-policies`);
      setRoleRlsPolicies(new Set(bound.policy_ids || []));
    }, tr('k_b0eb2d2dc9'));
    await load(async () => {
      const { data } = await client.get('/roles/dashboard-catalog');
      setDashboardCatalog(Array.isArray(data) ? data : []);
      const { data: grants } = await client.get(`/roles/${role.id}/dashboards`);
      setRoleDashboards((grants || []).map((r: any) => r.dashboard_id));
    }, tr('k_5757e3e6c8'));
    await load(async () => {
      const { data } = await client.get('/roles/dataset-catalog');
      setDatasetCatalog(Array.isArray(data) ? data : []);
      const { data: grants } = await client.get(`/roles/${role.id}/datasets`);
      setRoleDatasets((grants || []).map((r: any) => r.dataset_id));
    }, tr('k_657e38a431'));
    await load(async () => {
      const { data: reg } = await client.get('/roles/perm-registry');
      setPermGroups(reg.menu_groups || []);
      setStandalonePerms(reg.standalone || []);
      const { data: permData } = await client.get(`/roles/${role.id}/permissions`);
      setRolePerms(new Set(permData.permissions || []));
    }, tr('k_5e489875f5'));
  };

  /** 授权结果回写本地态：名称从数据源清单取（显示 name，不显示裸 id） */
  const applyDsAccess = (dsIds: number[]) => setDsAccess(dsIds.map(id => {
    const ds = allDatasources.find((d: any) => d.id === id);
    return { datasource_id: id, datasource_name: ds?.name || '', db_type: ds?.db_type || '' };
  }));

  const saveDatasourceAccess = async (dsIds: number[]) => {
    if (!permTarget) return;
    try {
      await client.put(`/roles/${permTarget.id}/datasources`, { datasource_ids: dsIds });
      applyDsAccess(dsIds);
      toast.success(t('k_d8befd9e8c'));
    } catch { toast.error(t('k_6de920b4e4')); }
  };

  /**
   * 批量授权统一出口：一次 PUT 整份清单。后端这几个 PUT 本就是全量替换语义
   * （看板/数据集「空列表=全部不可见」，数据源与 RLS 绑定同理），
   * 所以「全选/反选」天然是整份提交；逐条 toggle 会发 N 个请求、列表逐条闪。
   * 写库失败只报错、不回写本地勾选态——否则界面显示「已授权」而库里没变，属静默掩盖。
   */
  const bulkSave = async (
    kind: 'datasources' | 'datasets' | 'dashboards' | 'policies',
    ids: number[],
    okMsg: string,
  ): Promise<boolean> => {
    if (!permTarget) return false;
    const bodyKey = {
      datasources: 'datasource_ids',
      datasets: 'dataset_ids',
      dashboards: 'dashboard_ids',
      policies: 'policy_ids',
    }[kind];
    // 策略绑定的接口路径是 rls-policies（其余与清单名同名）
    const path = kind === 'policies' ? 'rls-policies' : kind;
    setBulkSaving(kind);
    try {
      await client.put(`/roles/${permTarget.id}/${path}`, { [bodyKey]: ids });
      toast.success(okMsg);
      return true;
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
      return false;
    } finally {
      setBulkSaving(null);
    }
  };

  /** 清单页签顶部的「已选 n/N + 全选 + 反选」工具条（四个勾选清单同口径） */
  const renderBulkActions = (
    kind: 'datasources' | 'datasets' | 'dashboards' | 'policies',
    allIds: number[],
    selectedIds: number[],
    onApply: (ids: number[]) => void,
    okMsg: string,
  ) => {
    if (allIds.length === 0) return null;
    const busy = bulkSaving === kind;
    const allOn = allIds.every(id => selectedIds.includes(id));
    // 按清单顺序落库，避免「反选」后顺序乱跳
    const run = async (ids: number[]) => {
      const ordered = allIds.filter(id => ids.includes(id));
      if (await bulkSave(kind, ordered, okMsg)) onApply(ordered);
    };
    return (
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {t('k_3ae55abc41', { p0: selectedIds.length, p1: allIds.length })}
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline" size="sm" className="h-7 px-2 text-xs" disabled={busy}
            onClick={() => run(allOn ? [] : allIds)}
          >
            {allOn ? t('k_a04144e4d7') : t('k_3e44b2a933')}
          </Button>
          <Button
            variant="outline" size="sm" className="h-7 px-2 text-xs" disabled={busy}
            onClick={() => run(allIds.filter(id => !selectedIds.includes(id)))}
          >
            {t('k_aab57a3547')}
          </Button>
        </div>
      </div>
    );
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
      .then(() => { setRoleRlsPolicies(new Set(next)); toast.success(t('k_2f86df5c33')); })
      .catch(() => toast.error(t('k_6de920b4e4')));
  };

  // ── Dashboard Visibility ──────────────────────────────────────

  const toggleDashboard = (dashId: number) => {
    if (!permTarget) return;
    const next = roleDashboards.includes(dashId)
      ? roleDashboards.filter(id => id !== dashId)
      : [...roleDashboards, dashId];
    client.put(`/roles/${permTarget.id}/dashboards`, { dashboard_ids: next })
      .then(() => { setRoleDashboards(next); toast.success(t('k_afe877a5e7')); })
      .catch(() => toast.error(t('k_6de920b4e4')));
  };

  // ── Dataset Access ──────────────────────────────────────

  const toggleDataset = (dsId: number) => {
    if (!permTarget) return;
    const next = roleDatasets.includes(dsId)
      ? roleDatasets.filter(id => id !== dsId)
      : [...roleDatasets, dsId];
    client.put(`/roles/${permTarget.id}/datasets`, { dataset_ids: next })
      .then(() => { setRoleDatasets(next); toast.success(t('k_7163377e5a')); })
      .catch(() => toast.error(t('k_6de920b4e4')));
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

  /** 全量权限码（菜单分组 + 通用功能），供整批全选/反选 */
  const allPermCodes = useMemo(() => [
    ...permGroups.flatMap(g => g.permissions.map(p => p.perm_code)),
    ...standalonePerms.map(p => p.perm_code),
  ], [permGroups, standalonePerms]);
  const permsAllOn = allPermCodes.length > 0 && allPermCodes.every(c => rolePerms.has(c));

  const toggleAllPerms = () => {
    const allOn = allPermCodes.length > 0 && allPermCodes.every(c => rolePerms.has(c));
    setRolePerms(prev => {
      const next = new Set(prev);
      allPermCodes.forEach(c => { if (allOn) next.delete(c); else next.add(c); });
      return next;
    });
  };

  const invertPerms = () => {
    setRolePerms(prev => {
      const next = new Set(prev);
      allPermCodes.forEach(c => { if (next.has(c)) next.delete(c); else next.add(c); });
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
      ai_channel: (res.ai_channel ?? perm.ai_channel) as PermItem['ai_channel'],
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
      toast.error(e.response?.data?.detail || t('k_0b0e9bde50'));
    }
  };

  const saveFunctionPerms = async () => {
    if (!permTarget) return;
    try {
      await client.put(`/roles/${permTarget.id}/permissions`, { permissions: [...rolePerms] });
      toast.success(t('k_6bc122782a'));
    } catch { toast.error(t('k_6de920b4e4')); }
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
      toast.success(t('k_fbfa6c18bf'));
      const { data } = await client.get(`/roles/${userTarget.id}`);
      setRoleUsers(data.users || []);
    } catch { toast.error(t('k_b05fa6dc7e')); }
  };

  const handleRemoveUser = async (userId: number) => {
    if (!userTarget) return;
    try {
      await client.delete(`/roles/${userTarget.id}/users/${userId}`, { params: { workspace_id: 0 } });
      toast.success(t('k_16edb1665d'));
      const { data } = await client.get(`/roles/${userTarget.id}`);
      setRoleUsers(data.users || []);
    } catch { toast.error(t('k_6a6b0524c7')); }
  };

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
        <Shield className="h-6 w-6" />
        {t('k_86a1be00f6')}
      </h1>

      <div className="flex justify-between items-center mb-4">
        <p className="text-sm text-muted-foreground">{t('k_59102a8c0f', { p0: roles.length })}</p>
        <Button onClick={openCreate} size="sm"><Plus className="h-4 w-4 mr-2" />{t("k_7a0524c3a7")}</Button>
      </div>

      {loading ? (
        <div className="text-center py-8 text-muted-foreground">{t('k_26b5bd4947')}</div>
      ) : roles.length === 0 ? (
        <div className="text-center py-8 text-muted-foreground">{t("k_6103376362")}</div>
      ) : (
        <div className="space-y-3">
          {roles.map(role => (
            <div key={role.id} className="border rounded-lg p-4">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium">{role.display_name}</span>
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{role.name}</code>
                    {role.is_system ? <Badge variant="secondary">{t("k_1a1f6dff78")}</Badge> : <Badge variant="outline">{t("k_c493338e8c")}</Badge>}
                  </div>
                  {role.description && <p className="text-sm text-muted-foreground">{role.description}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => navigate('/system/as-bots')} title={t("k_1f45e50d4b")}>
                    <Bot className="h-4 w-4" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => openPermissions(role)} title={t("k_f9f59a9827")}>
                    <Key className="h-4 w-4" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => openUsers(role)} title={t("k_edede123a2")}>
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
            <DialogTitle>{t('k_247fd8f28a', { p0: permTarget?.display_name })}</DialogTitle>
            <DialogDescription>{t("k_4eeea0280e")}</DialogDescription>
          </DialogHeader>
          <Tabs value={permTab} onValueChange={setPermTab}>
            <TabsList className="grid grid-cols-5 w-full">
              <TabsTrigger value="datasources"><Database className="h-3.5 w-3.5 mr-1" />{t("k_a3ccf702c5")}</TabsTrigger>
              <TabsTrigger value="datasets"><Layers className="h-3.5 w-3.5 mr-1" />{t("k_fc2b4735c3")}</TabsTrigger>
              <TabsTrigger value="policies"><Lock className="h-3.5 w-3.5 mr-1" />{t("k_e284beb40c")}</TabsTrigger>
              <TabsTrigger value="dashboards"><LayoutDashboard className="h-3.5 w-3.5 mr-1" />{t("k_9dc1a19f2c")}</TabsTrigger>
              <TabsTrigger value="functions"><ListChecks className="h-3.5 w-3.5 mr-1" />{t("k_eb0509962a")}</TabsTrigger>
            </TabsList>

            {/* Datasource Access */}
            <TabsContent value="datasources" className="space-y-3">
              <p className="text-sm text-muted-foreground">{t('k_6b7cc53a33')}</p>
              {renderBulkActions(
                'datasources',
                allDatasources.map((d: any) => d.id),
                dsAccess.map(d => d.datasource_id),
                applyDsAccess,
                t('k_d8befd9e8c'),
              )}
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
              {allDatasources.length === 0 && <p className="text-sm text-muted-foreground py-4 text-center">{t("k_bb6750d7bc")}</p>}
            </TabsContent>

            {/* Dataset Access */}
            <TabsContent value="datasets" className="space-y-3">
              <p className="text-sm text-muted-foreground">{t('k_4a44b46b78')}</p>
              {renderBulkActions(
                'datasets',
                datasetCatalog.map(d => d.id),
                roleDatasets,
                setRoleDatasets,
                t('k_7163377e5a'),
              )}
              {datasetCatalog.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">{t("k_c46e035dbe")}</p>
              ) : (
                <div className="grid grid-cols-2 gap-1.5">
                  {datasetCatalog.map(ds => (
                    <div key={ds.id} className="flex items-center justify-between gap-2 border rounded px-2 py-1.5">
                      <div className="flex items-center gap-2 min-w-0">
                        <Layers className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                        <span className="text-xs truncate">{ds.name}</span>
                      </div>
                      <Switch
                        checked={roleDatasets.includes(ds.id)}
                        onCheckedChange={() => toggleDataset(ds.id)}
                      />
                    </div>
                  ))}
                </div>
              )}
            </TabsContent>

            {/* 数据安全策略绑定（勾选才生效; 策略在 数据安全策略 页维护） */}
            <TabsContent value="policies" className="space-y-3">
              <p className="text-sm text-muted-foreground">{t("k_9c3cf07be5")}</p>
              {renderBulkActions(
                'policies',
                rlsPolicies.map(p => p.id),
                [...roleRlsPolicies],
                ids => setRoleRlsPolicies(new Set(ids)),
                t('k_2f86df5c33'),
              )}
              {rlsPolicies.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">{t("k_8dfd345a8c")}</p>
              ) : (
                <div className="space-y-1">
                  {rlsPolicies.map(p => (
                    <div key={p.id} className="flex items-center justify-between gap-2 border rounded px-3 py-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <Lock className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                          <span className="text-sm font-medium truncate">{p.name}</span>
                          <Badge variant="outline" className="text-xs flex-shrink-0">
                            {p.policy_type === 'row' ? t('k_aff627e54b') : p.policy_type === 'column' ? t('k_a8b880d21f') : p.policy_type === 'table' ? t('k_90fa0ea225') : t('k_8f64c985d2')}
                          </Badge>
                          {!p.is_active && <Badge variant="secondary" className="text-xs flex-shrink-0">{t("k_6c7dcbb73a")}</Badge>}
                        </div>
                        <div className="pl-6 text-xs text-muted-foreground truncate">
                          {tr('k_6a954390e3')}{' '}<code>{p.table_name}</code>
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
              <p className="text-sm text-muted-foreground">{t('k_9d000fb4e3')}</p>
              {renderBulkActions(
                'dashboards',
                dashboardCatalog.map(d => d.id),
                roleDashboards,
                setRoleDashboards,
                t('k_afe877a5e7'),
              )}
              {dashboardCatalog.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">{t("k_49734c90cc")}</p>
              ) : (
                <div className="grid grid-cols-2 gap-1.5">
                  {dashboardCatalog.map(d => (
                    <div key={d.id} className="flex items-center justify-between gap-2 border rounded px-2 py-1.5">
                      <div className="flex items-center gap-2 min-w-0">
                        <LayoutDashboard className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                        <span className="text-xs truncate">{d.name}</span>
                        {d.status !== 'enabled' && <Badge variant="outline" className="text-[10px] flex-shrink-0">{t("k_8bb38ef00c")}</Badge>}
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
              <p className="text-sm text-muted-foreground">{t('k_763aa11abf')}</p>
              {/* 两个控件的归属必须说清（历史陷阱：级别下拉长在角色页里，写的却是全局注册表，
                  改 A 角色会连带改变 B 角色的 AI 能力）。徽标只回答“这个级别有没有消费方”：
                  AI 工具=已登记功能工具，权限码把关=执行期按码直执行，两者级别均真实生效；
                  未接入=无消费方，配了不生效（已在行内显式提示）。 */}
              <p className="text-xs text-muted-foreground">
                {t('k_9ff6fa85e4')}
              </p>
              <div className="flex flex-wrap justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setAllCollapsed(false)}>{t("k_66c98ab6d8")}</Button>
                <Button variant="outline" size="sm" onClick={() => setAllCollapsed(true)}>{t("k_3f7e768840")}</Button>
                {/* 整批勾选权限码（本地态，点「保存功能权限」才落库） */}
                <Button variant="outline" size="sm" onClick={toggleAllPerms}>
                  {permsAllOn ? t('k_a04144e4d7') : t('k_3e44b2a933')}
                </Button>
                <Button variant="outline" size="sm" onClick={invertPerms}>{t('k_aab57a3547')}</Button>
              </div>
              {permGroups.length === 0 && standalonePerms.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">{t("k_0e4016d665")}</p>
              ) : (
                <div className="space-y-4">
                  {permModules.map(({ value: mod, label: modLabel }) => {
                    const groups = permGroups.filter(g => (g.module || 'other') === mod);
                    if (groups.length === 0) return null;
                    return (
                      <div key={mod}>
                        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                          {modLabel}
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
                        <span>{t("k_dd8bb81826")}</span>
                        <Button
                          variant="ghost" size="sm" className="h-6 w-6 p-0"
                          aria-label={collapsedGroups.has('__standalone__') ? t('k_a823b50769') : t('k_2bc6a1121d')}
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
                  <Button size="sm" onClick={saveFunctionPerms}>{t("k_0a4cf60d5a")}</Button>
                </div>
              )}
            </TabsContent>
          </Tabs>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPermTarget(null)}>{t("k_6c14bd7f6f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Create/Edit Dialog ──────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editRole ? t('k_ac775e9a51') : t('k_7a0524c3a7')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div><Label>{t('k_939050e252')}</Label><Input value={formName} onChange={e => setFormName(e.target.value)} placeholder={t('k_cb86f05180')} disabled={!!editRole} /></div>
            <div><Label>{t('k_f75459f6f0')}</Label><Input value={formDisplayName} onChange={e => setFormDisplayName(e.target.value)} placeholder={t("k_47ebb1f096")} /></div>
            <div><Label>{t("k_412f54dc38")}</Label><Textarea value={formDesc} onChange={e => setFormDesc(e.target.value)} rows={2} /></div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave} disabled={saving}>{saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── User Assignment Dialog ──────────────────────────────── */}
      <Dialog open={!!userTarget} onOpenChange={() => setUserTarget(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('k_2f59ad94d2', { p0: userTarget?.display_name })}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label className="mb-2 block">{t('k_7842852c84', { p0: roleUsers.length })}</Label>
              {roleUsers.length === 0 ? <p className="text-sm text-muted-foreground py-2">{t("k_3d268bdd39")}</p> : (
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
              <Label className="mb-2 block">{t("k_4f965d4969")}</Label>
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
          <DialogFooter><Button variant="outline" onClick={() => setUserTarget(null)}>{t("k_6c14bd7f6f")}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("k_3c06abe116")}</DialogTitle></DialogHeader>
          <p>{t('k_0633eb1bd4', { p0: deleteTarget?.display_name })}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t("k_3755f56f2f")}</Button>
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
  const { t } = useTranslation('ui');
  const patterns = (perm.api_pattern || '').split(',').map(s => s.trim()).filter(Boolean);
  const effective = perm.ai_effective || perm.ai_access || 'none';
  const configured = perm.ai_access || 'none';
  // 生效值被硬上界压下来时要让用户看见，否则会以为改了就生效
  const capped = effective !== configured;
  // 接入通道：后端派生（ai_action_key 登记 / require_write_perm 消费点），前端不另判。
  // 旧后端未回该字段时按同一口径回推，不静默当“已接入”。
  const channel = perm.ai_channel || (perm.ai_action_key ? 'function_action' : 'none');
  const channelTitle = channel === 'function_action'
    ? tr('k_8413c7714a', { p0: perm.ai_action_key })
    : channel === 'perm_gate'
      ? tr('k_40b3f56d06')
      : tr('k_790df09ab0');
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
                ? t('k_8e7919f2f0', { p0: AI_LEVEL_LABEL[effective], p1: perm.ai_note || '' })
                : t('k_020d85e451', { p0: AI_LEVEL_LABEL[effective] })}
            >
              <SelectValue placeholder={t("k_deb7157586")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">{t("k_292a251e16")}</SelectItem>
              <SelectItem value="read">{t("k_7a33d9210f")}</SelectItem>
              <SelectItem value="write">{t("k_079280e097")}</SelectItem>
            </SelectContent>
          </Select>
          <Badge
            variant={channel === 'function_action' ? 'default' : channel === 'perm_gate' ? 'secondary' : 'outline'}
            className="text-[9px] flex-shrink-0"
            title={channelTitle}
          >
            {AI_CHANNEL_LABEL[channel]}
          </Badge>
          {patterns.length > 0 && (
            <Button variant="ghost" size="sm" className="h-6 px-2 text-[10px] flex-shrink-0" onClick={onToggleApi}>
              {expanded ? t('k_4ca113a4f6') : t('k_eecd0676ec', { p0: patterns.length })}
            </Button>
          )}
        </div>
      </div>
      {perm.description && <p className="text-[10px] text-muted-foreground pl-6">{perm.description}</p>}
      {(capped || perm.ai_note) && (
        <p className={`text-[10px] pl-6 ${capped ? 'text-amber-600' : 'text-muted-foreground'}`}>
          {capped && <>{t('k_2c2d65c4b4', { p0: AI_LEVEL_LABEL[configured], p1: AI_LEVEL_LABEL[effective], p2: perm.ai_note })}</>}
          {!capped && perm.ai_note}
        </p>
      )}
      {/* 无消费方时显式提醒（no-silent-degradation §1）：不得让人以为选了级别 AI 就能用。
          角色级开关是左侧勾选框（收回权限码 = 该码下所有 AI 动作带原因不可用）。 */}
      {channel === 'none' && (
        <p className="text-[10px] pl-6 text-amber-600">
          {t('k_ee3147d034', { p0: tr('k_f1eafb83c8', { p0: AI_LEVEL_LABEL[effective] }) })}</p>
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
  const { t } = useTranslation('ui');
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
            aria-label={collapsed ? t('k_791821ac8d', { p0: group.label }) : t('k_422a0b52e4', { p0: group.label })}
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
