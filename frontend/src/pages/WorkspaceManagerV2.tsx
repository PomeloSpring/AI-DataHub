import { useState, useEffect } from 'react';
import {
  Plus, Edit2, Trash2, Star, Database, Users, Settings,
  X, Folder, UserPlus, Server, Bot, Menu, Terminal,
  BookOpen, Workflow, Zap, Shield, UserMinus,
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

interface WorkspaceRole {
  id: number;
  name: string;
  display_name: string;
  description: string;
  is_system: number;
  in_workspace: boolean;
  member_count?: number;
}

interface RoleUser {
  id: number;
  username: string;
  email: string;
  role_scope: 'workspace' | 'global';
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
  { value: 'deep', label: '深度', desc: '平台内置 Agent，LLM 自主工具调用' },
  { value: 'agent', label: 'Agent', desc: '外部执行层（默认 Claude Agent SDK）' },
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
  const [saving, setSaving] = useState(false);

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

// ── Workspace Execution Layer Tab (binding mode) ────────────────────

const LAYER_TYPE_LABELS: Record<string, string> = {
  builtin: '内置',
  cli: 'CLI',
  docker: 'Docker',
  remote: '远程',
};

function WorkspaceExecutionLayerTab({ workspaceId }: { workspaceId: number }) {
  const [bound, setBound] = useState<any | null>(null);  // 每个工作空间只允许绑定一个执行层
  const [allLayers, setAllLayers] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toolCatalog, setToolCatalog] = useState<any[]>([]);

  const load = async () => {
    setLoading(true);
    try {
      const [wsRes, allRes, toolsRes] = await Promise.all([
        client.get(`/admin/execution-layers/workspaces/${workspaceId}`),
        client.get('/admin/execution-layers'),
        client.get('/admin/execution-layers/tools').catch(() => ({ data: [] })),
      ]);
      const wsItems = wsRes.data || [];
      setBound(wsItems[0] || null);
      setAllLayers(allRes.data || []);
      setToolCatalog(Array.isArray(toolsRes.data) ? toolsRes.data : []);
    } catch { toast.error('加载执行层失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const persist = async (binding: any | null) => {
    setSaving(true);
    try {
      await client.put(`/admin/execution-layers/workspaces/${workspaceId}`, {
        bindings: binding ? [{
          execution_layer_id: binding.id,
          is_default: true,
          priority: 0,
          allowed_tools: Array.isArray(binding.allowed_tools) ? binding.allowed_tools : [],
        }] : [],
      });
      toast.success('已更新');
      await load();
    } catch (e: any) { toast.error(e?.response?.data?.detail || '更新执行层绑定失败'); }
    finally { setSaving(false); }
  };

  // tools 权限:勾选/取消工具;未勾选任何项 = 不限制
  const handleToggleTool = (toolName: string) => {
    if (!bound) return;
    const cur: string[] = Array.isArray(bound.allowed_tools) ? bound.allowed_tools : [];
    let next: string[];
    if (cur.length === 0) next = [toolName];
    else if (cur.includes(toolName)) next = cur.filter(t => t !== toolName);
    else next = [...cur, toolName];
    persist({ ...bound, allowed_tools: next });
  };

  if (loading) return <div className="flex justify-center py-8"><Spinner size={24} /></div>;

  const allowed: string[] = Array.isArray(bound?.allowed_tools) ? bound.allowed_tools : [];
  const restricted = allowed.length > 0;

  return (
    <div className="space-y-3">
      <div className="text-sm text-muted-foreground">
        每个工作空间只能配置一个执行层，用于 Agent 任务派发；未绑定时回退到内置执行层。
      </div>

      {allLayers.map(l => {
        const isBound = bound?.id === l.id;
        return (
          <div key={l.id} className={`p-3 border rounded-lg space-y-2 ${isBound ? 'border-primary bg-primary/5' : ''}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 min-w-0">
                <input
                  type="radio"
                  name={`exec-layer-${workspaceId}`}
                  checked={isBound}
                  disabled={saving}
                  onChange={() => !isBound && persist(l)}
                  className="h-4 w-4 accent-primary flex-shrink-0"
                />
                <Terminal className="h-4 w-4 flex-shrink-0" />
                <span className="truncate">{l.display_name || l.name}</span>
                <Badge variant="outline" className="flex-shrink-0">
                  {LAYER_TYPE_LABELS[l.layer_type] || l.layer_type}
                </Badge>
                {isBound && (
                  <Badge variant="secondary" className="flex-shrink-0">
                    <Star className="h-3 w-3 mr-1" />当前执行层
                  </Badge>
                )}
                {l.status !== 'active' && (
                  <span className="text-xs text-destructive flex-shrink-0">不可用</span>
                )}
              </div>
              {isBound && l.layer_type !== 'builtin' && (
                <Button size="sm" variant="outline" disabled={saving} onClick={() => persist(null)}>
                  取消绑定
                </Button>
              )}
            </div>
            {isBound && toolCatalog.length > 0 && (
              <div className="space-y-1 pl-6">
                <div className="text-xs text-muted-foreground">
                  工具权限：{restricted ? `仅允许 ${allowed.length} 项` : '不限制（勾选后生效白名单）'}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {toolCatalog.map(t => {
                    const checked = !restricted || allowed.includes(t.name);
                    const active = restricted && allowed.includes(t.name);
                    return (
                      <button
                        key={t.name}
                        type="button"
                        title={t.description}
                        disabled={saving}
                        onClick={() => handleToggleTool(t.name)}
                        className={`px-2 py-0.5 rounded-full text-xs border transition-colors ${
                          active
                            ? 'bg-primary text-primary-foreground border-primary'
                            : checked
                              ? 'bg-muted text-foreground border-border'
                              : 'bg-transparent text-muted-foreground border-dashed border-border'
                        }`}
                      >
                        {t.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        );
      })}

      {allLayers.length === 0 && (
        <div className="text-sm text-muted-foreground text-center py-8">
          暂无执行层，请先在系统配置中创建
        </div>
      )}
    </div>
  );
}

// ── Workspace Roles Tab (RBAC) ─────────────────────────────────────

function WorkspaceRolesTab({ workspaceId }: { workspaceId: number }) {
  const [roles, setRoles] = useState<WorkspaceRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [memberRole, setMemberRole] = useState<WorkspaceRole | null>(null);
  const [roleUsers, setRoleUsers] = useState<RoleUser[]>([]);
  const [wsUsers, setWsUsers] = useState<WorkspaceUser[]>([]);

  const load = async () => {
    try {
      const { data } = await client.get(`/workspaces/${workspaceId}/roles`);
      setRoles(Array.isArray(data) ? data : []);
    } catch { toast.error('加载角色失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const openMembers = async (role: WorkspaceRole) => {
    setMemberRole(role);
    try {
      const [membersRes, usersRes] = await Promise.all([
        client.get(`/workspaces/${workspaceId}/roles/${role.id}/users`),
        client.get(`/workspaces/${workspaceId}/users`),
      ]);
      setRoleUsers(Array.isArray(membersRes.data) ? membersRes.data : []);
      setWsUsers(Array.isArray(usersRes.data) ? usersRes.data : []);
    } catch { toast.error('加载角色成员失败'); }
  };

  const refreshMembers = async (role: WorkspaceRole) => {
    try {
      const { data } = await client.get(`/workspaces/${workspaceId}/roles/${role.id}/users`);
      setRoleUsers(Array.isArray(data) ? data : []);
    } catch { /* ignore */ }
    load();  // 同步刷新成员计数
  };

  const handleGrant = async (roleId: number) => {
    try {
      await client.post(`/workspaces/${workspaceId}/roles`, { role_id: roleId });
      toast.success('角色已授权');
      load();
    } catch (e: any) { toast.error(e.response?.data?.detail || '授权失败'); }
  };

  const handleRevoke = async (role: WorkspaceRole) => {
    try {
      await client.delete(`/workspaces/${workspaceId}/roles/${role.id}`);
      toast.success(`已回收角色「${role.display_name}」`);
      load();
    } catch (e: any) { toast.error(e.response?.data?.detail || '回收失败'); }
  };

  const handleAssignUser = async (userId: number) => {
    if (!memberRole) return;
    try {
      await client.post(`/workspaces/${workspaceId}/roles/${memberRole.id}/users`, { user_id: userId });
      toast.success('已分配');
      refreshMembers(memberRole);
    } catch (e: any) { toast.error(e.response?.data?.detail || '分配失败'); }
  };

  const handleRemoveUser = async (userId: number) => {
    if (!memberRole) return;
    try {
      await client.delete(`/workspaces/${workspaceId}/roles/${memberRole.id}/users/${userId}`);
      toast.success('已取消');
      refreshMembers(memberRole);
    } catch (e: any) { toast.error(e.response?.data?.detail || '取消失败'); }
  };

  if (loading) return <div className="flex justify-center py-8"><Spinner size={24} /></div>;

  const granted = roles.filter(r => r.in_workspace);
  const available = roles.filter(r => !r.in_workspace);
  const assignedIds = new Set(roleUsers.map(u => u.id));

  return (
    <div className="space-y-4">
      <div className="text-sm text-muted-foreground">
        将角色授权给工作空间后，把成员分配到角色即可批量控制数据访问范围，无需逐用户配置。
      </div>

      {granted.map(r => (
        <div key={r.id} className="flex items-center justify-between p-3 border rounded-lg">
          <div className="flex items-center gap-2 min-w-0">
            <Shield className="h-4 w-4 flex-shrink-0" />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium truncate">{r.display_name}</span>
                <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{r.name}</code>
                {r.is_system ? <Badge variant="secondary">系统</Badge> : <Badge variant="outline">自定义</Badge>}
              </div>
              {r.description && <div className="text-xs text-muted-foreground truncate">{r.description}</div>}
            </div>
            <Badge variant="outline" className="flex-shrink-0">{r.member_count || 0} 名成员</Badge>
          </div>
          <div className="flex gap-1 flex-shrink-0">
            <Button size="sm" variant="outline" onClick={() => openMembers(r)}>
              <Users className="h-4 w-4 mr-1" />
              成员
            </Button>
            <Button size="sm" variant="outline" onClick={() => handleRevoke(r)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>
      ))}

      {available.length > 0 && (
        <>
          <div className="text-sm text-muted-foreground mt-4">可授权的角色：</div>
          {available.map(r => (
            <div key={r.id} className="flex items-center justify-between p-3 border rounded-lg border-dashed">
              <div className="flex items-center gap-2 min-w-0">
                <Shield className="h-4 w-4 flex-shrink-0" />
                <span className="truncate">{r.display_name}</span>
                <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{r.name}</code>
                {r.is_system ? <Badge variant="secondary">系统</Badge> : <Badge variant="outline">自定义</Badge>}
              </div>
              <Button size="sm" variant="outline" onClick={() => handleGrant(r.id)}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </>
      )}

      {granted.length === 0 && available.length === 0 && (
        <div className="text-sm text-muted-foreground text-center py-8">
          暂无角色，请先在角色权限页面中创建
        </div>
      )}

      {/* Role Members Dialog */}
      <Dialog open={!!memberRole} onOpenChange={() => setMemberRole(null)}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>角色成员 — {memberRole?.display_name}</DialogTitle>
            <DialogDescription>
              将工作空间成员分配到该角色，全局角色成员不可在此取消
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label className="mb-2 block">已分配成员 ({roleUsers.length})</Label>
              {roleUsers.length === 0 ? (
                <p className="text-sm text-muted-foreground py-2">暂无成员</p>
              ) : (
                <div className="space-y-1 max-h-[200px] overflow-auto">
                  {roleUsers.map(u => (
                    <div key={u.id} className="flex items-center justify-between border rounded px-3 py-1.5">
                      <div className="flex items-center gap-2">
                        <span className="text-sm">{u.username}</span>
                        {u.role_scope === 'global' && <Badge variant="secondary">全局角色</Badge>}
                      </div>
                      {u.role_scope !== 'global' && (
                        <Button variant="ghost" size="sm" onClick={() => handleRemoveUser(u.id)}>
                          <UserMinus className="h-3 w-3" />
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <Label className="mb-2 block">添加成员</Label>
              <div className="max-h-[200px] overflow-auto space-y-1">
                {wsUsers.filter(u => !assignedIds.has(u.id)).map(u => (
                  <div key={u.id} className="flex items-center justify-between border rounded px-3 py-1.5">
                    <span className="text-sm">{u.username}</span>
                    <Button variant="ghost" size="sm" onClick={() => handleAssignUser(u.id)}>
                      <UserPlus className="h-3 w-3" />
                    </Button>
                  </div>
                ))}
                {wsUsers.filter(u => !assignedIds.has(u.id)).length === 0 && (
                  <p className="text-sm text-muted-foreground py-2">没有可添加的成员</p>
                )}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setMemberRole(null)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Workspace Skills View (read-only) ──────────────────────────────

function WorkspaceSkillsView({ workspaceId }: { workspaceId: number }) {
  const [skills, setSkills] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    client.get(`/workspaces/${workspaceId}/skills`)
      .then(({ data }) => setSkills(data || []))
      .catch(() => toast.error('加载 Skills 失败'))
      .finally(() => setLoading(false));
  }, [workspaceId]);

  if (loading) return <div className="flex justify-center py-8"><Spinner size={24} /></div>;

  if (skills.length === 0) {
    return (
      <div className="text-sm text-muted-foreground text-center py-8">
        暂无 Skills 配置
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {skills.map((s: any) => (
        <div key={s.skill_key} className="flex items-center justify-between p-3 border rounded-lg">
          <div className="flex items-center gap-2 min-w-0">
            <Zap className="h-4 w-4 flex-shrink-0" />
            <span className="font-medium truncate">{s.skill_key}</span>
            <Badge variant={s.enabled ? 'default' : 'secondary'} className="flex-shrink-0">
              {s.enabled ? '已启用' : '已禁用'}
            </Badge>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Workspace Knowledge View (read-only) ───────────────────────────

function WorkspaceKnowledgeView({ workspaceId }: { workspaceId: number }) {
  const [knowledgeBases, setKnowledgeBases] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    client.get(`/knowledge-bases?workspace_id=${workspaceId}`)
      .then(({ data }) => setKnowledgeBases(data || []))
      .catch(() => toast.error('加载知识库失败'))
      .finally(() => setLoading(false));
  }, [workspaceId]);

  if (loading) return <div className="flex justify-center py-8"><Spinner size={24} /></div>;

  if (knowledgeBases.length === 0) {
    return (
      <div className="text-sm text-muted-foreground text-center py-8">
        暂未关联知识库
      </div>
    );
  }

  const KB_TYPE_LABELS: Record<string, string> = {
    local: '本地目录',
    vector_db: '向量数据库',
    cloud_rag: '云 RAG',
  };

  return (
    <div className="space-y-2">
      {knowledgeBases.map((kb: any) => (
        <div key={kb.id} className="flex items-center justify-between p-3 border rounded-lg">
          <div className="flex items-center gap-2 min-w-0">
            <Database className="h-4 w-4 flex-shrink-0" />
            <div className="min-w-0">
              <div className="font-medium truncate">{kb.name}</div>
              <div className="text-xs text-muted-foreground">
                {KB_TYPE_LABELS[kb.kb_type] || kb.kb_type} · {kb.document_count} 个文档
              </div>
            </div>
          </div>
          <Badge variant={kb.status === 'active' ? 'default' : 'secondary'}>
            {kb.status === 'active' ? '正常' : '停用'}
          </Badge>
        </div>
      ))}
    </div>
  );
}

// ── Workspace Workflow View (read-only) ────────────────────────────

function WorkspaceWorkflowView({ workspaceId }: { workspaceId: number }) {
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    client.get(`/workspaces/${workspaceId}/workflow-config`)
      .then(({ data }) => setConfig(data))
      .catch(() => toast.error('加载工作流配置失败'))
      .finally(() => setLoading(false));
  }, [workspaceId]);

  if (loading) return <div className="flex justify-center py-8"><Spinner size={24} /></div>;

  if (!config) {
    return (
      <div className="text-sm text-muted-foreground text-center py-8">
        暂无工作流配置
      </div>
    );
  }

  const PIPELINE_MODES: Record<string, string> = {
    quick: '快速',
    deep: '深度',
    agent: 'Agent',
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between p-3 border rounded-lg">
        <span className="font-medium">默认模式</span>
        <Badge>{PIPELINE_MODES[config.default_pipeline_mode] || config.default_pipeline_mode}</Badge>
      </div>
      <div className="p-3 border rounded-lg">
        <div className="font-medium mb-2">可用模式</div>
        <div className="flex flex-wrap gap-2">
          {(config.allowed_pipeline_modes || []).map((m: string) => (
            <Badge key={m} variant="outline">{PIPELINE_MODES[m] || m}</Badge>
          ))}
        </div>
      </div>
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
      <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
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
            <TabsTrigger value="roles">
              <Shield className="h-4 w-4 mr-1" />
              角色
            </TabsTrigger>
            <TabsTrigger value="datasources">
              <Database className="h-4 w-4 mr-1" />
              数据源 ({datasources.length})
            </TabsTrigger>
            <TabsTrigger value="skills">
              <Zap className="h-4 w-4 mr-1" />
              Skills
            </TabsTrigger>
            <TabsTrigger value="knowledge">
              <BookOpen className="h-4 w-4 mr-1" />
              知识库
            </TabsTrigger>
            <TabsTrigger value="mcp">
              <Server className="h-4 w-4 mr-1" />
              MCP 服务
            </TabsTrigger>
            <TabsTrigger value="agents">
              <Bot className="h-4 w-4 mr-1" />
              Agent
            </TabsTrigger>
            <TabsTrigger value="execution">
              <Terminal className="h-4 w-4 mr-1" />
              执行层
            </TabsTrigger>
            <TabsTrigger value="workflow">
              <Workflow className="h-4 w-4 mr-1" />
              工作流
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
              <DialogContent>
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

          <TabsContent value="roles" className="space-y-4">
            <WorkspaceRolesTab workspaceId={workspace.id} />
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

          <TabsContent value="skills" className="space-y-4">
            <WorkspaceSkillsView workspaceId={workspace.id} />
          </TabsContent>

          <TabsContent value="knowledge" className="space-y-4">
            <WorkspaceKnowledgeView workspaceId={workspace.id} />
          </TabsContent>

          <TabsContent value="mcp" className="space-y-4">
            <WorkspaceMCPTab workspaceId={workspace.id} />
          </TabsContent>

          <TabsContent value="agents" className="space-y-4">
            <WorkspaceAgentTab workspaceId={workspace.id} />
          </TabsContent>

          <TabsContent value="execution" className="space-y-4">
            <WorkspaceExecutionLayerTab workspaceId={workspace.id} />
          </TabsContent>

          <TabsContent value="workflow" className="space-y-4">
            <WorkspaceWorkflowView workspaceId={workspace.id} />
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
