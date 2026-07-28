import { useState, useEffect } from 'react';
import {
  Plus, Edit2, Trash2, Star, Database, Users, Settings,
  Check, X, Save, Folder, UserPlus, Shield, Server, Bot, Menu,
  Wifi,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Spinner } from '@/components/ui/spinner';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import client from '../api/client';
import MenuEditorTab from '../components/MenuEditorTab';

// ── Types ──────────────────────────────────────────────────────────

interface Workspace {
  id: number;
  name: string;
  description: string;
  icon: string;
  color: string;
  owner_id: number;
  owner_name?: string;
  is_default: boolean;
  user_default: boolean;
  role: string;
  created_at: string;
}

interface WorkspaceUser {
  id: number;
  username: string;
  email: string;
  avatar: string;
  role: string;
  joined_at: string;
}

interface Datasource {
  id: number;
  name: string;
  db_type: string;
  is_primary: boolean;
}

// ── Main Component ─────────────────────────────────────────────────

export default function WorkspaceManagerV2() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(false);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<Workspace | null>(null);
  const [managingWorkspace, setManagingWorkspace] = useState<Workspace | null>(null);
  const [showDeleteDialog, setShowDeleteDialog] = useState<Workspace | null>(null);

  useEffect(() => {
    loadWorkspaces();
  }, []);

  const loadWorkspaces = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/workspaces');
      setWorkspaces(data || []);
    } catch (error) {
      console.error('Failed to load workspaces:', error);
      toast.error('加载工作空间失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSetDefault = async (ws: Workspace) => {
    try {
      await client.post(`/workspaces/${ws.id}/set-default`);
      toast.success(`已将 "${ws.name}" 设为默认工作空间`);
      loadWorkspaces();
    } catch (error) {
      toast.error('设置默认工作空间失败');
    }
  };

  const handleDelete = async () => {
    if (!showDeleteDialog) return;
    try {
      await client.delete(`/workspaces/${showDeleteDialog.id}`);
      toast.success(`已删除工作空间 "${showDeleteDialog.name}"`);
      setShowDeleteDialog(null);
      loadWorkspaces();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '删除失败');
    }
  };

  return (
    <div className="p-6 w-full">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">工作空间管理</h1>
          <p className="text-muted-foreground mt-1">
            管理您的工作空间，配置数据源、用户和权限
          </p>
        </div>
        <Button onClick={() => setShowCreateDialog(true)}>
          <Plus className="h-4 w-4 mr-2" />
          新建工作空间
        </Button>
      </div>

      {/* Workspace List */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 py-2">
        {workspaces.map((ws) => (
          <WorkspaceCard
            key={ws.id}
            workspace={ws}
            onEdit={() => setEditingWorkspace(ws)}
            onManage={() => setManagingWorkspace(ws)}
            onDelete={() => setShowDeleteDialog(ws)}
            onSetDefault={() => handleSetDefault(ws)}
          />
        ))}
      </div>

      {/* Create Dialog */}
      <CreateWorkspaceDialog
        open={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreated={() => {
          setShowCreateDialog(false);
          loadWorkspaces();
        }}
      />

      {/* Edit Dialog */}
      {editingWorkspace && (
        <EditWorkspaceDialog
          workspace={editingWorkspace}
          onClose={() => setEditingWorkspace(null)}
          onSaved={() => {
            setEditingWorkspace(null);
            loadWorkspaces();
          }}
        />
      )}

      {/* Manage Dialog */}
      {managingWorkspace && (
        <ManageWorkspaceDialog
          workspace={managingWorkspace}
          onClose={() => setManagingWorkspace(null)}
        />
      )}

      {/* Delete Confirmation */}
      <Dialog open={!!showDeleteDialog} onOpenChange={() => setShowDeleteDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除工作空间 "{showDeleteDialog?.name}" 吗？此操作不可撤销，
              该工作空间下的所有数据（仪表盘、对话、配置等）都将被删除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDeleteDialog(null)}>
              取消
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Workspace Card ─────────────────────────────────────────────────

function WorkspaceCard({
  workspace,
  onEdit,
  onManage,
  onDelete,
  onSetDefault,
}: {
  workspace: Workspace;
  onEdit: () => void;
  onManage: () => void;
  onDelete: () => void;
  onSetDefault: () => void;
}) {
  const roleLabels: Record<string, { label: string; variant: 'default' | 'secondary' | 'outline' }> = {
    owner: { label: '所有者', variant: 'default' },
    admin: { label: '管理员', variant: 'secondary' },
    member: { label: '成员', variant: 'outline' },
    viewer: { label: '查看者', variant: 'outline' },
  };

  const role = roleLabels[workspace.role] || roleLabels.member;

  return (
    <Card className={`relative min-w-0 ${workspace.user_default ? 'ring-2 ring-primary' : ''}`}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-2xl flex-shrink-0">{workspace.icon}</span>
            <div className="min-w-0">
              <CardTitle className="text-lg truncate">{workspace.name}</CardTitle>
              <div className="flex gap-2 mt-1 flex-wrap">
                <Badge variant={role.variant}>{role.label}</Badge>
                {workspace.user_default && (
                  <Badge variant="default" className="bg-primary">
                    <Star className="h-3 w-3 mr-1" />
                    默认
                  </Badge>
                )}
              </div>
            </div>
          </div>
        </div>
        <CardDescription className="mt-2">
          {workspace.description || '暂无描述'}
          {workspace.owner_name && (
            <span className="ml-2 text-xs">
              · 所有者:
              <a
                href="/admin"
                className="ml-1 text-primary hover:underline"
                onClick={(e) => { e.stopPropagation(); }}
              >
                {workspace.owner_name}
              </a>
            </span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={onManage}>
            <Settings className="h-3.5 w-3.5 mr-1" />
            管理
          </Button>
          <Button variant="outline" size="sm" onClick={onEdit}>
            <Edit2 className="h-3.5 w-3.5 mr-1" />
            编辑
          </Button>
          {!workspace.user_default && (
            <Button variant="outline" size="sm" onClick={onSetDefault}>
              <Star className="h-3.5 w-3.5 mr-1" />
              设为默认
            </Button>
          )}
          {workspace.role === 'owner' && !workspace.is_default && (
            <Button variant="outline" size="sm" onClick={onDelete}>
              <Trash2 className="h-3.5 w-3.5 mr-1" />
              删除
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Create Workspace Dialog ────────────────────────────────────────

function CreateWorkspaceDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState({
    name: '',
    description: '',
    icon: '📊',
    color: '#1890ff',
  });
  const [saving, setSaving] = useState(false);

  const handleCreate = async () => {
    if (!form.name.trim()) {
      toast.error('请输入工作空间名称');
      return;
    }
    setSaving(true);
    try {
      await client.post('/workspaces', form);
      toast.success('工作空间创建成功');
      onCreated();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '创建失败');
    } finally {
      setSaving(false);
    }
  };

  const icons = ['📊', '📋', '🔧', '🏠', '🚀', '💡', '🎯', '🌟'];

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建工作空间</DialogTitle>
          <DialogDescription>
            创建一个新的工作空间来组织您的数据和资源
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label>图标</Label>
            <div className="flex gap-2">
              {icons.map(icon => (
                <Button
                  key={icon}
                  variant={form.icon === icon ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setForm({ ...form, icon })}
                >
                  {icon}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>名称</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="输入工作空间名称"
            />
          </div>

          <div className="space-y-2">
            <Label>描述</Label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="输入工作空间描述（可选）"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={handleCreate} disabled={saving}>
            {saving ? '创建中...' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Edit Workspace Dialog ──────────────────────────────────────────

const RETRIEVAL_STRATEGIES = [
  { value: 'hybrid', label: '混合检索', desc: 'BM25+向量 RRF 融合（推荐）' },
  { value: 'full_table', label: '整表检索', desc: '返回命中表的全部字段' },
  { value: 'column_first', label: '字段优先', desc: '向量搜字段，只返回匹配字段' },
  { value: 'two_stage', label: '两阶段', desc: '先选表，再筛字段' },
  { value: 'bidirectional', label: '双向合并', desc: '表+字段双路召回，筛字段' },
  { value: 'graph', label: '图检索', desc: '关系遍历，只返回触及的字段' },
];

const PIPELINE_MODES = [
  { value: 'quick', label: '快速', desc: '简化 RAG 检索，响应快' },
  { value: 'deep', label: '深度', desc: '完整 RAG + Loop 自修复' },
  { value: 'agent', label: 'Agent', desc: 'LLM 自主决策，可调用 MCP 工具' },
];

function EditWorkspaceDialog({
  workspace,
  onClose,
  onSaved,
}: {
  workspace: Workspace;
  onClose: () => void;
  onSaved: () => void;
}) {
  const existingConfig = (workspace as any).config || {};
  const [form, setForm] = useState({
    name: workspace.name,
    description: workspace.description || '',
    icon: workspace.icon,
  });
  const [allowedStrategies, setAllowedStrategies] = useState<string[]>(
    existingConfig.allowed_retrieval_strategies || RETRIEVAL_STRATEGIES.map(s => s.value)
  );
  const [allowedModes, setAllowedModes] = useState<string[]>(
    existingConfig.allowed_pipeline_modes || PIPELINE_MODES.map(m => m.value)
  );
  const [sandboxId, setSandboxId] = useState<string>(
    existingConfig.sandbox_id ? String(existingConfig.sandbox_id) : 'none'
  );
  const [sandboxes, setSandboxes] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);

  // Load available sandboxes
  useEffect(() => {
    client.get('/sandbox/', { params: { size: 100 } })
      .then(({ data }) => setSandboxes(data.items || []))
      .catch(() => {});
  }, []);

  const toggleStrategy = (val: string) => {
    setAllowedStrategies(prev =>
      prev.includes(val) ? prev.filter(v => v !== val) : [...prev, val]
    );
  };

  const toggleMode = (val: string) => {
    setAllowedModes(prev =>
      prev.includes(val) ? prev.filter(v => v !== val) : [...prev, val]
    );
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await client.put(`/workspaces/${workspace.id}`, {
        ...form,
        config: {
          ...existingConfig,
          allowed_retrieval_strategies: allowedStrategies,
          allowed_pipeline_modes: allowedModes,
          sandbox_id: sandboxId === 'none' ? null : Number(sandboxId),
        },
      });
      toast.success('工作空间更新成功');
      onSaved();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '更新失败');
    } finally {
      setSaving(false);
    }
  };

  const icons = ['📊', '📋', '🔧', '🏠', '🚀', '💡', '🎯', '🌟'];

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>编辑工作空间</DialogTitle>
          <DialogDescription>
            修改 "{workspace.name}" 的配置
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label>图标</Label>
            <div className="flex gap-2">
              {icons.map(icon => (
                <Button
                  key={icon}
                  variant={form.icon === icon ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setForm({ ...form, icon })}
                >
                  {icon}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>名称</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>

          <div className="space-y-2">
            <Label>描述</Label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>

          {/* Pipeline Modes */}
          <div className="space-y-2">
            <Label>可用查询模式</Label>
            <p className="text-xs text-muted-foreground">选择此工作空间中用户可以使用的查询模式</p>
            <div className="flex flex-wrap gap-2">
              {PIPELINE_MODES.map(m => (
                <Button
                  key={m.value}
                  variant={allowedModes.includes(m.value) ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => toggleMode(m.value)}
                >
                  {m.label}
                </Button>
              ))}
            </div>
          </div>

          {/* Retrieval Strategies */}
          <div className="space-y-2">
            <Label>可用检索模式</Label>
            <p className="text-xs text-muted-foreground">选择此工作空间中快速/深度模式可用的检索策略</p>
            <div className="flex flex-wrap gap-2">
              {RETRIEVAL_STRATEGIES.map(s => (
                <Button
                  key={s.value}
                  variant={allowedStrategies.includes(s.value) ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => toggleStrategy(s.value)}
                >
                  {s.label}
                </Button>
              ))}
            </div>
          </div>

          {/* Sandbox Environment */}
          <div className="space-y-2">
            <Label>沙箱环境</Label>
            <p className="text-xs text-muted-foreground">Agent 模式下代码执行使用的沙箱环境（每个工作空间最多配置一个）</p>
            <Select value={sandboxId} onValueChange={setSandboxId}>
              <SelectTrigger>
                <SelectValue placeholder="选择沙箱环境" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">不配置（使用系统默认）</SelectItem>
                {sandboxes.map((sb: any) => (
                  <SelectItem key={sb.id} value={String(sb.id)}>
                    {sb.display_name || sb.name} ({sb.sandbox_type})
                    {sb.is_default ? ' ⭐' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Workspace MCP Tab (association mode, like datasources) ─────────

function WorkspaceMCPTab({ workspaceId }: { workspaceId: number }) {
  const [current, setCurrent] = useState<any[]>([]);
  const [available, setAvailable] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [wsRes, allRes] = await Promise.all([
        client.get(`/admin/mcp-servers?workspace_id=${workspaceId}`),
        client.get('/admin/mcp-servers'),
      ]);
      const wsItems = wsRes.data || [];
      const allItems = allRes.data || [];
      const wsIds = new Set(wsItems.map((s: any) => s.id));
      setCurrent(wsItems);
      // Available = system-level items (workspace_id=0) not already in this workspace
      // + items from other workspaces
      setAvailable(allItems.filter((s: any) => !wsIds.has(s.id)));
    } catch { toast.error('加载 MCP 服务失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleAdd = async (id: number) => {
    try {
      await client.put(`/admin/mcp-servers/${id}`, { workspace_id: workspaceId });
      toast.success('已添加');
      load();
    } catch { toast.error('添加失败'); }
  };

  const handleRemove = async (id: number) => {
    try {
      await client.put(`/admin/mcp-servers/${id}`, { workspace_id: 0 });
      toast.success('已移除');
      load();
    } catch { toast.error('移除失败'); }
  };

  const handleTest = async (id: number) => {
    setTesting(id);
    try {
      const { data } = await client.post(`/admin/mcp-servers/${id}/test`);
      if (data.success) toast.success(data.message);
      else toast.error(data.message);
    } catch { toast.error('测试失败'); }
    finally { setTesting(null); }
  };

  if (loading) return <div className="flex justify-center py-8"><Spinner size={24} /></div>;

  return (
    <div className="space-y-4">
      {current.map(s => (
        <div key={s.id} className="flex items-center justify-between p-3 border rounded-lg">
          <div className="flex items-center gap-2 min-w-0">
            <Server className="h-4 w-4 flex-shrink-0" />
            <span className="truncate">{s.name}</span>
            <Badge variant="outline" className="flex-shrink-0">{s.transport}</Badge>
            {!s.is_active && <span className="text-xs text-destructive flex-shrink-0">已禁用</span>}
          </div>
          <div className="flex gap-1 flex-shrink-0">
            <Button size="sm" variant="outline" onClick={() => handleTest(s.id)} disabled={testing === s.id}>
              {testing === s.id ? '...' : '测试'}
            </Button>
            <Button size="sm" variant="outline" onClick={() => handleRemove(s.id)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>
      ))}

      {available.length > 0 && (
        <>
          <div className="text-sm text-muted-foreground mt-4">可添加的 MCP 服务：</div>
          {available.map(s => (
            <div key={s.id} className="flex items-center justify-between p-3 border rounded-lg border-dashed">
              <div className="flex items-center gap-2 min-w-0">
                <Server className="h-4 w-4 flex-shrink-0" />
                <span className="truncate">{s.name}</span>
                <Badge variant="outline" className="flex-shrink-0">{s.transport}</Badge>
              </div>
              <Button size="sm" variant="outline" onClick={() => handleAdd(s.id)}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </>
      )}

      {current.length === 0 && available.length === 0 && (
        <div className="text-sm text-muted-foreground text-center py-8">
          暂无 MCP 服务，请先在系统配置中创建
        </div>
      )}
    </div>
  );
}

// ── Workspace Agent Tab (association mode) ─────────────────────────

function WorkspaceAgentTab({ workspaceId }: { workspaceId: number }) {
  const [current, setCurrent] = useState<any[]>([]);
  const [available, setAvailable] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [wsRes, allRes] = await Promise.all([
        client.get(`/admin/agents?workspace_id=${workspaceId}`),
        client.get('/admin/agents'),
      ]);
      const wsItems = wsRes.data || [];
      const allItems = allRes.data || [];
      const wsIds = new Set(wsItems.map((a: any) => a.id));
      setCurrent(wsItems);
      setAvailable(allItems.filter((a: any) => !wsIds.has(a.id)));
    } catch { toast.error('加载 Agent 失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleAdd = async (id: number) => {
    try {
      await client.put(`/admin/agents/${id}`, { workspace_id: workspaceId });
      toast.success('已添加');
      load();
    } catch { toast.error('添加失败'); }
  };

  const handleRemove = async (id: number) => {
    try {
      await client.put(`/admin/agents/${id}`, { workspace_id: 0 });
      toast.success('已移除');
      load();
    } catch { toast.error('移除失败'); }
  };

  if (loading) return <div className="flex justify-center py-8"><Spinner size={24} /></div>;

  return (
    <div className="space-y-4">
      {current.map(a => (
        <div key={a.id} className="flex items-center justify-between p-3 border rounded-lg">
          <div className="flex items-center gap-2 min-w-0">
            <Bot className="h-4 w-4 flex-shrink-0" />
            <span className="truncate">{a.display_name || a.name}</span>
            {a.agent_type === 'builtin' && <Badge variant="secondary" className="flex-shrink-0">内置</Badge>}
            {!a.is_active && <span className="text-xs text-destructive flex-shrink-0">已禁用</span>}
          </div>
          <Button size="sm" variant="outline" onClick={() => handleRemove(a.id)}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      ))}

      {available.length > 0 && (
        <>
          <div className="text-sm text-muted-foreground mt-4">可添加的 Agent：</div>
          {available.map(a => (
            <div key={a.id} className="flex items-center justify-between p-3 border rounded-lg border-dashed">
              <div className="flex items-center gap-2 min-w-0">
                <Bot className="h-4 w-4 flex-shrink-0" />
                <span className="truncate">{a.display_name || a.name}</span>
                {a.agent_type === 'builtin' && <Badge variant="secondary" className="flex-shrink-0">内置</Badge>}
              </div>
              <Button size="sm" variant="outline" onClick={() => handleAdd(a.id)}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </>
      )}

      {current.length === 0 && available.length === 0 && (
        <div className="text-sm text-muted-foreground text-center py-8">
          暂无 Agent，请先在系统配置中创建
        </div>
      )}
    </div>
  );
}

// ── Manage Workspace Dialog ────────────────────────────────────────

function ManageWorkspaceDialog({
  workspace,
  onClose,
}: {
  workspace: Workspace;
  onClose: () => void;
}) {
  const [users, setUsers] = useState<WorkspaceUser[]>([]);
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [allDatasources, setAllDatasources] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAddUser, setShowAddUser] = useState(false);
  const [newUserId, setNewUserId] = useState('');
  const [newUserRole, setNewUserRole] = useState('member');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [usersRes, dsRes, allDsRes] = await Promise.all([
        client.get(`/workspaces/${workspace.id}/users`),
        client.get(`/workspaces/${workspace.id}/datasources`),
        client.get('/datasources/'),
      ]);
      setUsers(usersRes.data || []);
      setDatasources(dsRes.data || []);
      setAllDatasources(allDsRes.data || []);
    } catch (error) {
      console.error('Failed to load data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAddUser = async () => {
    if (!newUserId) {
      toast.error('请输入用户ID');
      return;
    }
    try {
      await client.post(`/workspaces/${workspace.id}/users`, {
        user_id: Number(newUserId),
        role: newUserRole,
      });
      toast.success('用户已添加');
      setShowAddUser(false);
      setNewUserId('');
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '添加失败');
    }
  };

  const handleUpdateUserRole = async (userId: number, role: string) => {
    try {
      await client.put(`/workspaces/${workspace.id}/users/${userId}`, { role });
      toast.success('用户角色已更新');
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '更新失败');
    }
  };

  const handleRemoveUser = async (userId: number) => {
    try {
      await client.delete(`/workspaces/${workspace.id}/users/${userId}`);
      toast.success('用户已移除');
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '移除失败');
    }
  };

  const handleAddDatasource = async (dsId: number) => {
    try {
      await client.post(`/workspaces/${workspace.id}/datasources`, {
        datasource_id: dsId,
      });
      toast.success('数据源已添加');
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '添加失败');
    }
  };

  const handleRemoveDatasource = async (dsId: number) => {
    try {
      await client.delete(`/workspaces/${workspace.id}/datasources/${dsId}`);
      toast.success('数据源已移除');
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '移除失败');
    }
  };

  const roleLabels: Record<string, string> = {
    owner: '所有者',
    admin: '管理员',
    member: '成员',
    viewer: '查看者',
  };

  const availableDatasources = allDatasources.filter(
    ds => !datasources.some(d => d.id === ds.id)
  );

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent
        className="max-w-4xl max-h-[85vh] overflow-y-auto"
        onPointerDownOutside={(e) => {
          // Prevent Dialog from closing when clicking inside a Radix Select portal (role="listbox")
          const target = e.target as HTMLElement;
          if (target?.closest('[role="listbox"]') || target?.closest('[data-radix-select-viewport]')) {
            e.preventDefault();
          }
        }}
        onInteractOutside={(e) => {
          // Prevent Dialog from closing when interacting with a Radix Select portal
          const target = e.target as HTMLElement;
          if (target?.closest('[role="listbox"]') || target?.closest('[data-radix-select-viewport]')) {
            e.preventDefault();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>管理工作空间 - {workspace.name}</DialogTitle>
          <DialogDescription>
            管理工作空间的用户、数据源、MCP 服务和 Agent
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="users" className="py-4">
          <TabsList>
            <TabsTrigger value="users">
              <Users className="h-4 w-4 mr-1" />
              用户 ({users.length})
            </TabsTrigger>
            <TabsTrigger value="datasources">
              <Database className="h-4 w-4 mr-1" />
              数据源 ({datasources.length})
            </TabsTrigger>
            <TabsTrigger value="mcp">
              <Server className="h-4 w-4 mr-1" />
              MCP 服务
            </TabsTrigger>
            <TabsTrigger value="agents">
              <Bot className="h-4 w-4 mr-1" />
              Agent
            </TabsTrigger>
            <TabsTrigger value="menu">
              <Menu className="h-4 w-4 mr-1" />
              菜单管理
            </TabsTrigger>
          </TabsList>

          <TabsContent value="users" className="space-y-4">
            <div className="flex justify-end">
              <Button size="sm" onClick={() => setShowAddUser(true)}>
                <UserPlus className="h-4 w-4 mr-1" />
                添加用户
              </Button>
            </div>

            {users.map(user => (
              <div key={user.id} className="flex items-center justify-between p-3 border rounded-lg">
                <div className="flex items-center gap-3">
                  <Avatar>
                    <AvatarFallback>
                      {user.username?.charAt(0)?.toUpperCase() || 'U'}
                    </AvatarFallback>
                  </Avatar>
                  <div>
                    <div className="font-medium">{user.username}</div>
                    <div className="text-sm text-muted-foreground">{user.email}</div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Select
                    value={user.role}
                    onValueChange={(role) => handleUpdateUserRole(user.id, role)}
                    disabled={user.role === 'owner'}
                  >
                    <SelectTrigger className="w-[100px] h-8">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="admin">管理员</SelectItem>
                      <SelectItem value="member">成员</SelectItem>
                      <SelectItem value="viewer">查看者</SelectItem>
                    </SelectContent>
                  </Select>
                  {user.role !== 'owner' && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleRemoveUser(user.id)}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </div>
            ))}

            {/* Add User Dialog */}
            <Dialog open={showAddUser} onOpenChange={setShowAddUser}>
              <DialogContent
                onPointerDownOutside={(e) => {
                  const target = e.target as HTMLElement;
                  if (target?.closest('[role="listbox"]') || target?.closest('[data-radix-select-viewport]')) {
                    e.preventDefault();
                  }
                }}
                onInteractOutside={(e) => {
                  const target = e.target as HTMLElement;
                  if (target?.closest('[role="listbox"]') || target?.closest('[data-radix-select-viewport]')) {
                    e.preventDefault();
                  }
                }}
              >
                <DialogHeader>
                  <DialogTitle>添加用户</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-4">
                  <div className="space-y-2">
                    <Label>用户ID</Label>
                    <Input
                      value={newUserId}
                      onChange={(e) => setNewUserId(e.target.value)}
                      placeholder="输入用户ID"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>角色</Label>
                    <Select value={newUserRole} onValueChange={setNewUserRole}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="admin">管理员</SelectItem>
                        <SelectItem value="member">成员</SelectItem>
                        <SelectItem value="viewer">查看者</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setShowAddUser(false)}>
                    取消
                  </Button>
                  <Button onClick={handleAddUser}>添加</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </TabsContent>

          <TabsContent value="datasources" className="space-y-4">
            {/* Current datasources */}
            {datasources.map(ds => (
              <div key={ds.id} className="flex items-center justify-between p-3 border rounded-lg">
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4" />
                  <span>{ds.name}</span>
                  <Badge variant="outline">{ds.db_type}</Badge>
                  {ds.is_primary && <Badge>主数据源</Badge>}
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleRemoveDatasource(ds.id)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}

            {/* Available datasources */}
            {availableDatasources.length > 0 && (
              <>
                <div className="text-sm text-muted-foreground mt-4">
                  可添加的数据源：
                </div>
                {availableDatasources.map(ds => (
                  <div key={ds.id} className="flex items-center justify-between p-3 border rounded-lg border-dashed">
                    <div className="flex items-center gap-2">
                      <Database className="h-4 w-4" />
                      <span>{ds.name}</span>
                      <Badge variant="outline">{ds.db_type}</Badge>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleAddDatasource(ds.id)}
                    >
                      <Plus className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </>
            )}
          </TabsContent>

          <TabsContent value="mcp" className="space-y-4">
            <WorkspaceMCPTab workspaceId={workspace.id} />
          </TabsContent>

          <TabsContent value="agents" className="space-y-4">
            <WorkspaceAgentTab workspaceId={workspace.id} />
          </TabsContent>

          <TabsContent value="menu" className="space-y-4">
            <MenuEditorTab workspaceId={workspace.id} />
          </TabsContent>
        </Tabs>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
