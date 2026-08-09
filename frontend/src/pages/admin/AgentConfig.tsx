import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Bot } from 'lucide-react';
import client from '@/api/client';

interface Agent {
  id: number;
  name: string;
  display_name: string;
  description: string;
  agent_type: string;
  system_prompt: string;
  mcp_server_ids: string;
  datasource_ids: string;
  tools: string;
  config: string;
  route_patterns: string;
  is_active: number;
  is_default: number;
}

export default function AgentConfig({ workspaceId: propWorkspaceId, defaultWorkspaceId }: { workspaceId?: number; defaultWorkspaceId?: number } = {}) {
  const { workspaceId: paramWorkspaceId } = useParams();
  const workspaceId = propWorkspaceId ?? (paramWorkspaceId ? Number(paramWorkspaceId) : undefined);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<any>({ agent_type: 'custom', is_active: 1, is_default: 0, max_retries: 2, max_iterations: 10 });
  const [mcpServers, setMcpServers] = useState<any[]>([]);
  const [datasources, setDatasources] = useState<any[]>([]);

  const load = async () => {
    setLoading(true);
    try {
      const agentParams = workspaceId ? `?workspace_id=${workspaceId}` : '';
      const mcpParams = workspaceId ? `?workspace_id=${workspaceId}` : '';
      const [agentsRes, mcpRes, dsRes] = await Promise.all([
        client.get(`/admin/agents${agentParams}`),
        client.get(`/admin/mcp-servers${mcpParams}`),
        client.get('/datasources/'),
      ]);
      setAgents(Array.isArray(agentsRes.data) ? agentsRes.data : []);
      setMcpServers(Array.isArray(mcpRes.data) ? mcpRes.data : []);
      setDatasources(Array.isArray(dsRes.data) ? dsRes.data : []);
    } catch { toast.error('加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    if (!form.name) { toast.error('请输入 Agent 名称'); return; }
    let configObj: any = {};
    try {
      configObj = typeof form.config === 'string' ? JSON.parse(form.config || '{}') : (form.config || {});
    } catch { configObj = {}; }
    configObj.max_retries = form.max_retries ?? 2;
    configObj.max_iterations = form.max_iterations ?? 10;
    const wsId = workspaceId || defaultWorkspaceId;
    const submitForm = { ...form, config: JSON.stringify(configObj), ...(wsId ? { workspace_id: wsId } : {}) };
    try {
      if (editing) {
        await client.put(`/admin/agents/${editing.id}`, submitForm);
        toast.success('已更新');
      } else {
        await client.post('/admin/agents', submitForm);
        toast.success('已创建');
      }
      setFormOpen(false);
      setEditing(null);
      setForm({ agent_type: 'custom', is_active: 1, is_default: 0, max_retries: 2, max_iterations: 10 });
      load();
    } catch { toast.error('保存失败'); }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除？')) return;
    try {
      await client.delete(`/admin/agents/${id}`);
      toast.success('已删除');
      load();
    } catch { toast.error('删除失败'); }
  };

  const handleEdit = (a: Agent) => {
    let maxRetries = 2;
    let maxIterations = 10;
    try {
      const cfg = typeof a.config === 'string' ? JSON.parse(a.config || '{}') : (a.config || {});
      maxRetries = cfg.max_retries ?? 2;
      maxIterations = cfg.max_iterations ?? 10;
    } catch { /* use default */ }
    setEditing(a);
    setForm({ ...a, max_retries: maxRetries, max_iterations: maxIterations });
    setFormOpen(true);
  };

  return (
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agent 配置</h1>
          <p className="text-muted-foreground text-sm mt-1">配置 Agent，定义不同场景的推理逻辑和工具集</p>
        </div>
        <Button size="sm" onClick={() => { setEditing(null); setForm({ agent_type: 'custom', is_active: 1, is_default: 0 }); setFormOpen(true); }}>
          <Plus className="h-4 w-4 mr-1" /> 添加 Agent
        </Button>
      </div>

      {/* Form */}
      {formOpen && (
        <div className="border border-border rounded-xl p-4 space-y-4 bg-card">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Agent 名称 (英文标识)</Label>
              <Input value={form.name || ''} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="log_analysis" />
            </div>
            <div className="space-y-1.5">
              <Label>显示名称</Label>
              <Input value={form.display_name || ''} onChange={e => setForm({ ...form, display_name: e.target.value })} placeholder="日志分析" />
            </div>
          </div>
          {editing && (
            <div className="text-xs text-muted-foreground bg-blue-50 text-blue-700 px-3 py-2 rounded">
              💡 描述、系统提示词、路由正则已在 <code>config/agents/{editing.name}/</code> 目录下的文件中配置。此处仅管理运行时状态。
            </div>
          )}
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Input value={form.description || ''} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="Agent 描述" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>绑定 MCP 服务</Label>
              <Select value={form.mcp_server_ids || 'none'} onValueChange={v => setForm({ ...form, mcp_server_ids: v === 'none' ? '' : v })}>
                <SelectTrigger><SelectValue placeholder="选择 MCP 服务 (可选)" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不绑定</SelectItem>
                  {mcpServers.filter(s => s.is_active).map(s => (
                    <SelectItem key={s.id} value={String(s.id)}>{s.name} ({s.transport})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>绑定数据源</Label>
              <Select value={form.datasource_ids || 'none'} onValueChange={v => setForm({ ...form, datasource_ids: v === 'none' ? '' : v })}>
                <SelectTrigger><SelectValue placeholder="选择数据源 (可选)" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不绑定</SelectItem>
                  {datasources.map(ds => (
                    <SelectItem key={ds.id} value={String(ds.id)}>{ds.name} ({ds.db_type})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>最大重试次数</Label>
              <Input type="number" min={0} max={5} value={form.max_retries ?? 2}
                onChange={e => setForm({ ...form, max_retries: parseInt(e.target.value) || 0 })} placeholder="2" />
              <p className="text-xs text-muted-foreground">数据获取失败时的内部重试次数（0=不重试）</p>
            </div>
            <div className="space-y-1.5">
              <Label>最大迭代次数</Label>
              <Input type="number" min={1} max={50} value={form.max_iterations ?? 10}
                onChange={e => setForm({ ...form, max_iterations: parseInt(e.target.value) || 10 })} placeholder="10" />
              <p className="text-xs text-muted-foreground">工具调用的最大轮次（接近上限时会自动收尾）</p>
            </div>
            <div className="flex items-end">
              <div className="flex items-center gap-2">
                <Switch checked={form.is_active === 1} onCheckedChange={v => setForm({ ...form, is_active: v ? 1 : 0 })} />
                <span className="text-sm">启用</span>
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSave}>保存</Button>
            <Button size="sm" variant="ghost" onClick={() => { setFormOpen(false); setEditing(null); }}>取消</Button>
          </div>
        </div>
      )}

      {/* List */}
      <div className="border border-border rounded-xl divide-y divide-border">
        {agents.map(a => {
          const mcpName = a.mcp_server_ids ? mcpServers.find(s => String(s.id) === a.mcp_server_ids)?.name : '';
          const dsName = a.datasource_ids ? datasources.find(d => String(d.id) === a.datasource_ids)?.name : '';
          let maxRetries = 2;
          let maxIterations = 10;
          try {
            const cfg = typeof a.config === 'string' ? JSON.parse(a.config || '{}') : (a.config || {});
            maxRetries = cfg.max_retries ?? 2;
            maxIterations = cfg.max_iterations ?? 10;
          } catch {}
          return (
            <div key={a.id} className="flex items-center justify-between p-3 hover:bg-muted/30">
              <div className="space-y-0.5">
                <div className="font-medium text-sm">
                  {a.display_name || a.name}
                  <span className="ml-2 text-xs text-muted-foreground">({a.name})</span>
                  {a.agent_type === 'builtin' && <span className="ml-2 text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded">内置</span>}
                  {!a.is_active && <span className="ml-2 text-xs text-destructive">已禁用</span>}
                </div>
                <div className="text-xs text-muted-foreground">{a.description}</div>
                {(mcpName || dsName || maxRetries !== 2 || maxIterations !== 10) && (
                  <div className="flex gap-2 mt-1">
                    {mcpName && <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">MCP: {mcpName}</span>}
                    {dsName && <span className="text-xs bg-green-50 text-green-600 px-1.5 py-0.5 rounded">数据源: {dsName}</span>}
                    {maxRetries !== 2 && <span className="text-xs bg-orange-50 text-orange-600 px-1.5 py-0.5 rounded">重试: {maxRetries}次</span>}
                    {maxIterations !== 10 && <span className="text-xs bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded">迭代: {maxIterations}轮</span>}
                  </div>
                )}
              </div>
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => handleEdit(a)}><Pencil className="h-4 w-4" /></Button>
                {a.agent_type !== 'builtin' && <Button size="sm" variant="ghost" onClick={() => handleDelete(a.id)}><Trash2 className="h-4 w-4" /></Button>}
              </div>
            </div>
          );
        })}
        {!loading && agents.length === 0 && (
          <div className="text-sm text-muted-foreground text-center py-12">暂无 Agent，点击上方按钮添加</div>
        )}
      </div>
    </div>
  );
}
