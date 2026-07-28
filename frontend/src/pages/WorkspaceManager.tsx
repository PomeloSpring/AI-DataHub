import { useState, useEffect } from 'react';
import {
  Plus, Edit2, Trash2, Star, Database, Server, Bot,
  Check, Zap, Workflow,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import client from '../api/client';
import {
  listWorkspaces, createWorkspace, updateWorkspace, deleteWorkspace, setDefaultWorkspace,
  Workspace, WorkspaceCreateRequest,
} from '../api/workspace';

// ── Types ──────────────────────────────────────────────────────────

interface Datasource {
  id: number;
  name: string;
  db_type: string;
  is_default: boolean;
}

interface MCPServer {
  id: number;
  name: string;
  description: string;
  is_active: boolean;
}

interface Agent {
  name: string;
  display_name: string;
  description: string;
  is_active: boolean;
}

// ── Workspace Templates ────────────────────────────────────────────

const WORKSPACE_TEMPLATES: Record<string, {
  label: string;
  icon: string;
  description: string;
  allowed_modes: string;
  default_mode: string;
  retrieval_strategy: string;
  default_agents: string[];
}> = {
  data_analysis: {
    label: '数据分析',
    icon: '📊',
    description: '用于业务数据查询和统计分析',
    allowed_modes: 'quick,deep,agent',
    default_mode: 'quick',
    retrieval_strategy: 'full_table',
    default_agents: ['data_analysis_agent'],
  },
  log_analysis: {
    label: '日志分析',
    icon: '📋',
    description: '用于ES日志查询和链路追踪',
    allowed_modes: 'agent',
    default_mode: 'agent',
    retrieval_strategy: 'full_table',
    default_agents: ['log_analysis'],
  },
  ops: {
    label: '综合运维',
    icon: '🔧',
    description: '包含数据源和日志服务，支持混合查询',
    allowed_modes: 'agent',
    default_mode: 'agent',
    retrieval_strategy: 'full_table',
    default_agents: ['data_analysis_agent', 'log_analysis'],
  },
};

// ── Main Component ─────────────────────────────────────────────────

export default function WorkspaceManager() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [, setLoading] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<Workspace | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState<Workspace | null>(null);

  // Available resources
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [wsData, dsData, mcpData] = await Promise.all([
        listWorkspaces(),
        client.get('/datasources/').then(r => r.data),
        client.get('/chat/mcp-tools').then(r => r.data.servers || []),
      ]);
      setWorkspaces(wsData);
      setDatasources(dsData);
      setMcpServers(mcpData);

      // Load agents
      try {
        const agentRes = await client.get('/admin/agents');
        setAgents(agentRes.data || []);
      } catch {
        // Agents endpoint might not exist yet
        setAgents([]);
      }
    } catch (error) {
      console.error('Failed to load data:', error);
      toast.error('加载数据失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSetDefault = async (ws: Workspace) => {
    try {
      await setDefaultWorkspace(ws.id);
      toast.success(`已将 "${ws.name}" 设为默认工作空间`);
      loadData();
    } catch (error) {
      toast.error('设置默认工作空间失败');
    }
  };

  const handleDelete = async () => {
    if (!showDeleteDialog) return;
    try {
      await deleteWorkspace(showDeleteDialog.id);
      toast.success(`已删除工作空间 "${showDeleteDialog.name}"`);
      setShowDeleteDialog(null);
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '删除失败');
    }
  };

  return (
    <div className="container mx-auto p-6 max-w-6xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">工作空间管理</h1>
          <p className="text-muted-foreground mt-1">
            管理您的工作空间，配置数据源、MCP服务和Agent
          </p>
        </div>
        <Button onClick={() => setShowCreateDialog(true)}>
          <Plus className="h-4 w-4 mr-2" />
          新建工作空间
        </Button>
      </div>

      {/* Workspace List */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {workspaces.map((ws) => (
          <WorkspaceCard
            key={ws.id}
            workspace={ws}
            onEdit={() => setEditingWorkspace(ws)}
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
          loadData();
        }}
        datasources={datasources}
        mcpServers={mcpServers}
        agents={agents}
      />

      {/* Edit Dialog */}
      {editingWorkspace && (
        <EditWorkspaceDialog
          workspace={editingWorkspace}
          onClose={() => setEditingWorkspace(null)}
          onSaved={() => {
            setEditingWorkspace(null);
            loadData();
          }}
          datasources={datasources}
          mcpServers={mcpServers}
          agents={agents}
        />
      )}

      {/* Delete Confirmation */}
      <Dialog open={!!showDeleteDialog} onOpenChange={() => setShowDeleteDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除工作空间 "{showDeleteDialog?.name}" 吗？此操作不可撤销。
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
  onDelete,
  onSetDefault,
}: {
  workspace: Workspace;
  onEdit: () => void;
  onDelete: () => void;
  onSetDefault: () => void;
}) {
  const typeInfo: Record<string, { label: string; color: string }> = {
    data_analysis: { label: '数据分析', color: 'bg-blue-100 text-blue-800' },
    log_analysis: { label: '日志分析', color: 'bg-green-100 text-green-800' },
    ops: { label: '综合运维', color: 'bg-orange-100 text-orange-800' },
    custom: { label: '自定义', color: 'bg-gray-100 text-gray-800' },
  };

  const type = typeInfo[workspace.workspace_type] || typeInfo.custom;
  const modes = (workspace.allowed_modes || '').split(',');

  return (
    <Card className={`relative ${workspace.is_default ? 'ring-2 ring-primary' : ''}`}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <span className="text-2xl">{workspace.icon}</span>
            <div>
              <CardTitle className="text-lg">{workspace.name}</CardTitle>
              <Badge className={`mt-1 ${type.color}`}>{type.label}</Badge>
            </div>
          </div>
          {workspace.is_default && (
            <Badge variant="default" className="bg-primary">
              <Star className="h-3 w-3 mr-1" />
              默认
            </Badge>
          )}
        </div>
        <CardDescription className="mt-2">
          {workspace.description || '暂无描述'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {/* Stats */}
          <div className="flex gap-4 text-sm text-muted-foreground">
            <span className="flex items-center gap-1">
              <Database className="h-3.5 w-3.5" />
              {workspace.datasource_count || 0} 数据源
            </span>
            <span className="flex items-center gap-1">
              <Server className="h-3.5 w-3.5" />
              {workspace.mcp_server_count || 0} MCP服务
            </span>
          </div>

          {/* Allowed Modes */}
          <div className="flex flex-wrap gap-1">
            {modes.includes('quick') && (
              <Badge variant="outline" className="text-xs">
                <Zap className="h-3 w-3 mr-1" />快速
              </Badge>
            )}
            {modes.includes('deep') && (
              <Badge variant="outline" className="text-xs">
                <Workflow className="h-3 w-3 mr-1" />深度
              </Badge>
            )}
            {modes.includes('agent') && (
              <Badge variant="outline" className="text-xs">
                <Bot className="h-3 w-3 mr-1" />Agent
              </Badge>
            )}
          </div>

          {/* Actions */}
          <div className="flex gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={onEdit}>
              <Edit2 className="h-3.5 w-3.5 mr-1" />
              编辑
            </Button>
            {!workspace.is_default && (
              <Button variant="outline" size="sm" onClick={onSetDefault}>
                <Star className="h-3.5 w-3.5 mr-1" />
                设为默认
              </Button>
            )}
            {!workspace.is_default && (
              <Button variant="outline" size="sm" onClick={onDelete}>
                <Trash2 className="h-3.5 w-3.5 mr-1" />
                删除
              </Button>
            )}
          </div>
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
  datasources,
  mcpServers,
  agents,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  datasources: Datasource[];
  mcpServers: MCPServer[];
  agents: Agent[];
}) {
  const [step, setStep] = useState<'template' | 'config'>('template');
  const [, setSelectedTemplate] = useState<string>('');
  const [form, setForm] = useState<WorkspaceCreateRequest>({
    name: '',
    description: '',
    workspace_type: 'custom',
    allowed_modes: 'quick,deep,agent',
    default_mode: 'quick',
    retrieval_strategy: 'full_table',
    datasource_ids: [],
    mcp_server_ids: [],
    agent_names: [],
  });
  const [saving, setSaving] = useState(false);

  const handleTemplateSelect = (templateKey: string) => {
    setSelectedTemplate(templateKey);
    const template = WORKSPACE_TEMPLATES[templateKey];
    if (template) {
      setForm({
        ...form,
        name: template.label,
        description: template.description,
        workspace_type: templateKey,
        allowed_modes: template.allowed_modes,
        default_mode: template.default_mode,
        retrieval_strategy: template.retrieval_strategy,
        agent_names: template.default_agents,
      });
    }
    setStep('config');
  };

  const handleCreate = async () => {
    if (!form.name.trim()) {
      toast.error('请输入工作空间名称');
      return;
    }
    setSaving(true);
    try {
      await createWorkspace(form);
      toast.success('工作空间创建成功');
      onCreated();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '创建失败');
    } finally {
      setSaving(false);
    }
  };

  const toggleDatasource = (dsId: number) => {
    const current = form.datasource_ids || [];
    setForm({
      ...form,
      datasource_ids: current.includes(dsId)
        ? current.filter(id => id !== dsId)
        : [...current, dsId],
    });
  };

  const toggleMcpServer = (mcpId: number) => {
    const current = form.mcp_server_ids || [];
    setForm({
      ...form,
      mcp_server_ids: current.includes(mcpId)
        ? current.filter(id => id !== mcpId)
        : [...current, mcpId],
    });
  };

  const toggleAgent = (agentName: string) => {
    const current = form.agent_names || [];
    setForm({
      ...form,
      agent_names: current.includes(agentName)
        ? current.filter(name => name !== agentName)
        : [...current, agentName],
    });
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>新建工作空间</DialogTitle>
          <DialogDescription>
            选择模板快速创建，或自定义配置
          </DialogDescription>
        </DialogHeader>

        {step === 'template' ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 py-4">
            {Object.entries(WORKSPACE_TEMPLATES).map(([key, template]) => (
              <Card
                key={key}
                className="cursor-pointer hover:ring-2 hover:ring-primary transition-all"
                onClick={() => handleTemplateSelect(key)}
              >
                <CardHeader className="text-center pb-2">
                  <span className="text-4xl">{template.icon}</span>
                  <CardTitle className="text-lg mt-2">{template.label}</CardTitle>
                </CardHeader>
                <CardContent>
                  <CardDescription className="text-center">
                    {template.description}
                  </CardDescription>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <Tabs defaultValue="basic" className="py-4">
            <TabsList>
              <TabsTrigger value="basic">基本信息</TabsTrigger>
              <TabsTrigger value="datasources">数据源</TabsTrigger>
              <TabsTrigger value="mcp">MCP服务</TabsTrigger>
              <TabsTrigger value="agents">Agent</TabsTrigger>
            </TabsList>

            <TabsContent value="basic" className="space-y-4">
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
                  placeholder="输入工作空间描述"
                />
              </div>
              <div className="space-y-2">
                <Label>允许的模式</Label>
                <div className="flex gap-2">
                  {['quick', 'deep', 'agent'].map(mode => (
                    <Badge
                      key={mode}
                      variant={(form.allowed_modes || '').includes(mode) ? 'default' : 'outline'}
                      className="cursor-pointer"
                      onClick={() => {
                        const modes = (form.allowed_modes || '').split(',').filter(Boolean);
                        const newModes = modes.includes(mode)
                          ? modes.filter(m => m !== mode)
                          : [...modes, mode];
                        setForm({ ...form, allowed_modes: newModes.join(',') });
                      }}
                    >
                      {mode === 'quick' && <Zap className="h-3 w-3 mr-1" />}
                      {mode === 'deep' && <Workflow className="h-3 w-3 mr-1" />}
                      {mode === 'agent' && <Bot className="h-3 w-3 mr-1" />}
                      {mode}
                    </Badge>
                  ))}
                </div>
              </div>
              <div className="space-y-2">
                <Label>默认模式</Label>
                <Select
                  value={form.default_mode}
                  onValueChange={(v) => setForm({ ...form, default_mode: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(form.allowed_modes || '').split(',').filter(Boolean).map(mode => (
                      <SelectItem key={mode} value={mode}>{mode}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </TabsContent>

            <TabsContent value="datasources" className="space-y-4">
              <p className="text-sm text-muted-foreground">
                选择此工作空间可用的数据源
              </p>
              {datasources.map(ds => (
                <div
                  key={ds.id}
                  className="flex items-center justify-between p-3 border rounded-lg cursor-pointer hover:bg-accent"
                  onClick={() => toggleDatasource(ds.id)}
                >
                  <div className="flex items-center gap-2">
                    <Database className="h-4 w-4" />
                    <span>{ds.name}</span>
                    <Badge variant="outline">{ds.db_type}</Badge>
                  </div>
                  {(form.datasource_ids || []).includes(ds.id) && (
                    <Check className="h-4 w-4 text-primary" />
                  )}
                </div>
              ))}
            </TabsContent>

            <TabsContent value="mcp" className="space-y-4">
              <p className="text-sm text-muted-foreground">
                选择此工作空间可用的MCP服务
              </p>
              {mcpServers.map(mcp => (
                <div
                  key={mcp.id}
                  className="flex items-center justify-between p-3 border rounded-lg cursor-pointer hover:bg-accent"
                  onClick={() => toggleMcpServer(mcp.id)}
                >
                  <div className="flex items-center gap-2">
                    <Server className="h-4 w-4" />
                    <span>{mcp.name}</span>
                    <span className="text-sm text-muted-foreground">{mcp.description}</span>
                  </div>
                  {(form.mcp_server_ids || []).includes(mcp.id) && (
                    <Check className="h-4 w-4 text-primary" />
                  )}
                </div>
              ))}
            </TabsContent>

            <TabsContent value="agents" className="space-y-4">
              <p className="text-sm text-muted-foreground">
                选择此工作空间可用的Agent
              </p>
              {agents.map(agent => (
                <div
                  key={agent.name}
                  className="flex items-center justify-between p-3 border rounded-lg cursor-pointer hover:bg-accent"
                  onClick={() => toggleAgent(agent.name)}
                >
                  <div className="flex items-center gap-2">
                    <Bot className="h-4 w-4" />
                    <span>{agent.display_name || agent.name}</span>
                    <span className="text-sm text-muted-foreground">{agent.description}</span>
                  </div>
                  {(form.agent_names || []).includes(agent.name) && (
                    <Check className="h-4 w-4 text-primary" />
                  )}
                </div>
              ))}
            </TabsContent>
          </Tabs>
        )}

        <DialogFooter>
          {step === 'config' && (
            <Button variant="outline" onClick={() => setStep('template')}>
              返回
            </Button>
          )}
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          {step === 'config' && (
            <Button onClick={handleCreate} disabled={saving}>
              {saving ? '创建中...' : '创建'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Edit Workspace Dialog ──────────────────────────────────────────

function EditWorkspaceDialog({
  workspace,
  onClose,
  onSaved,
  datasources,
  mcpServers,
  agents,
}: {
  workspace: Workspace;
  onClose: () => void;
  onSaved: () => void;
  datasources: Datasource[];
  mcpServers: MCPServer[];
  agents: Agent[];
}) {
  const [form, setForm] = useState({
    name: workspace.name,
    description: workspace.description || '',
    allowed_modes: workspace.allowed_modes || 'quick,deep,agent',
    default_mode: workspace.default_mode || 'quick',
    retrieval_strategy: workspace.retrieval_strategy || 'full_table',
    datasource_ids: (workspace.datasources || []).map(d => d.id),
    mcp_server_ids: (workspace.mcp_servers || []).map(m => m.id),
    agent_names: (workspace.agents || []).map(a => a.name),
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateWorkspace(workspace.id, form);
      toast.success('工作空间更新成功');
      onSaved();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '更新失败');
    } finally {
      setSaving(false);
    }
  };

  const toggleDatasource = (dsId: number) => {
    setForm({
      ...form,
      datasource_ids: form.datasource_ids.includes(dsId)
        ? form.datasource_ids.filter(id => id !== dsId)
        : [...form.datasource_ids, dsId],
    });
  };

  const toggleMcpServer = (mcpId: number) => {
    setForm({
      ...form,
      mcp_server_ids: form.mcp_server_ids.includes(mcpId)
        ? form.mcp_server_ids.filter(id => id !== mcpId)
        : [...form.mcp_server_ids, mcpId],
    });
  };

  const toggleAgent = (agentName: string) => {
    setForm({
      ...form,
      agent_names: form.agent_names.includes(agentName)
        ? form.agent_names.filter(name => name !== agentName)
        : [...form.agent_names, agentName],
    });
  };

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>编辑工作空间</DialogTitle>
          <DialogDescription>
            修改 "{workspace.name}" 的配置
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="basic" className="py-4">
          <TabsList>
            <TabsTrigger value="basic">基本信息</TabsTrigger>
            <TabsTrigger value="datasources">数据源</TabsTrigger>
            <TabsTrigger value="mcp">MCP服务</TabsTrigger>
            <TabsTrigger value="agents">Agent</TabsTrigger>
          </TabsList>

          <TabsContent value="basic" className="space-y-4">
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
            <div className="space-y-2">
              <Label>允许的模式</Label>
              <div className="flex gap-2">
                {['quick', 'deep', 'agent'].map(mode => (
                  <Badge
                    key={mode}
                    variant={form.allowed_modes.includes(mode) ? 'default' : 'outline'}
                    className="cursor-pointer"
                    onClick={() => {
                      const modes = form.allowed_modes.split(',').filter(Boolean);
                      const newModes = modes.includes(mode)
                        ? modes.filter(m => m !== mode)
                        : [...modes, mode];
                      setForm({ ...form, allowed_modes: newModes.join(',') });
                    }}
                  >
                    {mode}
                  </Badge>
                ))}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="datasources" className="space-y-4">
            {datasources.map(ds => (
              <div
                key={ds.id}
                className="flex items-center justify-between p-3 border rounded-lg cursor-pointer hover:bg-accent"
                onClick={() => toggleDatasource(ds.id)}
              >
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4" />
                  <span>{ds.name}</span>
                </div>
                {form.datasource_ids.includes(ds.id) && (
                  <Check className="h-4 w-4 text-primary" />
                )}
              </div>
            ))}
          </TabsContent>

          <TabsContent value="mcp" className="space-y-4">
            {mcpServers.map(mcp => (
              <div
                key={mcp.id}
                className="flex items-center justify-between p-3 border rounded-lg cursor-pointer hover:bg-accent"
                onClick={() => toggleMcpServer(mcp.id)}
              >
                <div className="flex items-center gap-2">
                  <Server className="h-4 w-4" />
                  <span>{mcp.name}</span>
                </div>
                {form.mcp_server_ids.includes(mcp.id) && (
                  <Check className="h-4 w-4 text-primary" />
                )}
              </div>
            ))}
          </TabsContent>

          <TabsContent value="agents" className="space-y-4">
            {agents.map(agent => (
              <div
                key={agent.name}
                className="flex items-center justify-between p-3 border rounded-lg cursor-pointer hover:bg-accent"
                onClick={() => toggleAgent(agent.name)}
              >
                <div className="flex items-center gap-2">
                  <Bot className="h-4 w-4" />
                  <span>{agent.display_name || agent.name}</span>
                </div>
                {form.agent_names.includes(agent.name) && (
                  <Check className="h-4 w-4 text-primary" />
                )}
              </div>
            ))}
          </TabsContent>
        </Tabs>

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
