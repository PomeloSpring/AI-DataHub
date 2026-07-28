import { useState, useEffect } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Server, Bot, Store, Wrench, Copy } from 'lucide-react';
import client from '@/api/client';
import { skillApi, Skill } from '@/api/skill';
import MCPMarket from './MCPMarket';

interface MCPServer {
  id: number;
  name: string;
  description: string;
  transport: string;
  url: string;
  command: string;
  args: string;
  tools_config: string;
  discovered_tools?: string;
  is_active: number;
  datasource_id: number;
  last_test_at?: string;
  last_test_status?: string;
  last_test_message?: string;
}

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

export default function MCPAgentConfig() {
  return (
    <div className="h-full overflow-auto">
      <Tabs defaultValue="mcp-servers" className="h-full">
        <div className="mb-6">
          <h1 className="text-2xl font-bold mb-4">MCP / Agent</h1>
          <TabsList>
            <TabsTrigger value="mcp-servers">
              <Server className="h-4 w-4 mr-2" />
              MCP 服务
            </TabsTrigger>
            <TabsTrigger value="agents">
              <Bot className="h-4 w-4 mr-2" />
              Agent 管理
            </TabsTrigger>
            <TabsTrigger value="skills">
              <Wrench className="h-4 w-4 mr-2" />
              Skills
            </TabsTrigger>
            <TabsTrigger value="market">
              <Store className="h-4 w-4 mr-2" />
              服务市场
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="mcp-servers" className="mt-0">
          <MCPServersTab />
        </TabsContent>
        <TabsContent value="agents" className="mt-0">
          <AgentsTab />
        </TabsContent>
        <TabsContent value="skills" className="mt-0">
          <SkillsTab />
        </TabsContent>
        <TabsContent value="market" className="mt-0">
          <MCPMarket />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── MCP Servers Tab ─────────────────────────────────────────────────

export function MCPServersTab({ workspaceId, defaultWorkspaceId }: { workspaceId?: number; defaultWorkspaceId?: number } = {}) {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<MCPServer | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<any>({ transport: 'sse', is_active: 1 });
  const [testing, setTesting] = useState<number | null>(null);
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set());
  // Tools selection state for the edit form
  const [discoveredToolsList, setDiscoveredToolsList] = useState<any[]>([]);
  const [selectedToolNames, setSelectedToolNames] = useState<Set<string>>(new Set());

  const load = async () => {
    setLoading(true);
    try {
      const params = workspaceId ? `?workspace_id=${workspaceId}` : '';
      const { data } = await client.get(`/admin/mcp-servers${params}`);
      setServers(data);
    } catch { toast.error('加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    if (!form.name) { toast.error('请输入服务名称'); return; }
    const wsId = workspaceId || defaultWorkspaceId;
    // Build tools_config from selected tools
    let toolsConfigStr = form.tools_config || '';
    if (discoveredToolsList.length > 0) {
      // Multi-select mode: serialize selected tool names as JSON
      const selected = discoveredToolsList.filter(t => selectedToolNames.has(t.name));
      toolsConfigStr = JSON.stringify(selected.map(t => ({ name: t.name, description: t.description || '' })));
    }
    const submitData = { ...form, tools_config: toolsConfigStr, ...(wsId ? { workspace_id: wsId } : {}) };
    try {
      if (editing) {
        await client.put(`/admin/mcp-servers/${editing.id}`, submitData);
        toast.success('已更新');
      } else {
        await client.post('/admin/mcp-servers', submitData);
        toast.success('已创建');
      }
      setFormOpen(false);
      setEditing(null);
      setForm({ transport: 'sse', is_active: 1 });
      load();
    } catch { toast.error('保存失败'); }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除？')) return;
    try {
      await client.delete(`/admin/mcp-servers/${id}`);
      toast.success('已删除');
      load();
    } catch { toast.error('删除失败'); }
  };

  const handleEdit = (s: MCPServer) => {
    setEditing(s);
    setForm({ ...s });
    setFormOpen(true);
    // Parse discovered_tools for the multi-select
    let discovered: any[] = [];
    try { discovered = JSON.parse(s.discovered_tools || '[]'); } catch {}
    if (!Array.isArray(discovered)) discovered = [];
    setDiscoveredToolsList(discovered);
    // Parse tools_config to know which are selected
    let selectedNames: string[] = [];
    try {
      const cfg = JSON.parse(s.tools_config || '[]');
      if (Array.isArray(cfg) && cfg.length > 0) {
        selectedNames = cfg.map((t: any) => typeof t === 'string' ? t : t.name).filter(Boolean);
      }
    } catch {}
    // If tools_config is empty/invalid and discovered_tools exists, select all by default
    if (selectedNames.length === 0 && discovered.length > 0) {
      selectedNames = discovered.map((t: any) => t.name);
    }
    setSelectedToolNames(new Set(selectedNames));
  };

  const handleTest = async (id: number) => {
    setTesting(id);
    try {
      const { data } = await client.post(`/admin/mcp-servers/${id}/test`);
      if (data.success) {
        toast.success(data.message);
        load();
        // Ensure tools section is expanded after test
        setExpandedTools(prev => new Set(prev).add(id));
      } else {
        toast.error(data.message);
      }
    } catch {
      toast.error('测试失败');
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">注册 MCP 服务，为 Agent 提供工具能力</p>
        <Button size="sm" onClick={() => { setEditing(null); setForm({ transport: 'sse', is_active: 1 }); setFormOpen(true); }}>
          <Plus className="h-4 w-4 mr-1" /> 添加服务
        </Button>
      </div>

      {/* Form */}
      {formOpen && (
        <div className="border rounded-lg p-4 space-y-4 bg-muted/30">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>服务名称</Label>
              <Input value={form.name || ''} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="elasticsearch" />
            </div>
            <div className="space-y-1.5">
              <Label>传输方式</Label>
              <Select value={form.transport} onValueChange={v => setForm({ ...form, transport: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="streamable_http">Streamable HTTP (远程服务)</SelectItem>
                  <SelectItem value="stdio">stdio (本地进程)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Input value={form.description || ''} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="Elasticsearch 日志查询服务" />
          </div>
          {form.transport === 'sse' || form.transport === 'streamable_http' ? (
            <div className="space-y-1.5">
              <Label>服务地址</Label>
              <Input value={form.url || ''} onChange={e => setForm({ ...form, url: e.target.value })} placeholder="http://localhost:3001/mcp" />
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>命令</Label>
                  <Input value={form.command || ''} onChange={e => setForm({ ...form, command: e.target.value })} placeholder="npx" />
                </div>
                <div className="space-y-1.5">
                  <Label>参数 (逗号分隔)</Label>
                  <Input value={form.args || ''} onChange={e => setForm({ ...form, args: e.target.value })} placeholder="-y,@anthropic/mcp-server-elasticsearch" />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>环境变量 (JSON 或 KEY=VALUE 格式)</Label>
                <Textarea
                  value={form.env || ''}
                  onChange={e => setForm({ ...form, env: e.target.value })}
                  rows={2}
                  placeholder={'{"ES_URL": "http://localhost:9200", "ES_AUTH": "user:password"}'}
                />
                <p className="text-xs text-muted-foreground">stdio 模式下传递给子进程的环境变量</p>
              </div>
            </>
          )}
          {/* Tools config: multi-select when discovered_tools exists, else raw JSON */}
          {discoveredToolsList.length > 0 ? (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>可用工具（勾选需要启用的工具）</Label>
                <span className="text-xs text-muted-foreground">
                  已选 {selectedToolNames.size} / {discoveredToolsList.length}
                </span>
              </div>
              <div className="border rounded-md p-3 max-h-60 overflow-y-auto space-y-1.5 bg-background">
                {/* Select all / deselect all */}
                <div className="flex gap-3 mb-2 pb-2 border-b">
                  <button
                    type="button"
                    className="text-xs text-primary hover:underline"
                    onClick={() => setSelectedToolNames(new Set(discoveredToolsList.map(t => t.name)))}
                  >
                    全选
                  </button>
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:underline"
                    onClick={() => setSelectedToolNames(new Set())}
                  >
                    全不选
                  </button>
                </div>
                {discoveredToolsList.map((t: any, i: number) => (
                  <label key={i} className="flex items-start gap-2 cursor-pointer group">
                    <input
                      type="checkbox"
                      className="mt-0.5 rounded"
                      checked={selectedToolNames.has(t.name)}
                      onChange={(e) => {
                        setSelectedToolNames(prev => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(t.name); else next.delete(t.name);
                          return next;
                        });
                      }}
                    />
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-mono group-hover:text-primary transition-colors">{t.name}</span>
                      {t.description && (
                        <span className="ml-2 text-xs text-muted-foreground">{t.description}</span>
                      )}
                    </div>
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                未勾选的工具不会暴露给 LLM。留空 = 全部可用。
              </p>
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label>Tools 配置 (JSON)</Label>
              <Textarea value={form.tools_config || ''} onChange={e => setForm({ ...form, tools_config: e.target.value })} placeholder='[{"name":"query_sql","description":"执行SQL查询"}]' rows={3} />
              <p className="text-xs text-muted-foreground">
                先测试连通性以自动发现工具，或手动输入 JSON 配置。
              </p>
            </div>
          )}
          <div className="flex items-center gap-2">
            <Switch checked={form.is_active === 1} onCheckedChange={v => setForm({ ...form, is_active: v ? 1 : 0 })} />
            <span className="text-sm">启用</span>
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSave}>保存</Button>
            <Button size="sm" variant="ghost" onClick={() => { setFormOpen(false); setEditing(null); }}>取消</Button>
          </div>
        </div>
      )}

      {/* List */}
      <div className="border rounded-lg divide-y">
        {servers.map(s => (
          <div key={s.id} className="p-3 hover:bg-muted/30">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <div className="font-medium text-sm">
                  {s.name}
                  <span className="ml-2 text-xs text-muted-foreground">({s.transport})</span>
                  {!s.is_active && <span className="ml-2 text-xs text-destructive">已禁用</span>}
                  {s.last_test_status === 'success' && (
                    <span className="ml-2 text-xs text-green-600">✅ 已连接</span>
                  )}
                  {s.last_test_status === 'failed' && (
                    <span className="ml-2 text-xs text-destructive">❌ 连接失败</span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">{s.description || s.url || s.command}</div>
              </div>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => handleTest(s.id)}
                  disabled={testing === s.id}
                >
                  {testing === s.id ? '测试中...' : '测试连接'}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => handleEdit(s)}>
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button size="sm" variant="ghost" onClick={() => handleDelete(s.id)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </div>
            {/* Tools list — always visible when discovered_tools exists, expanded by default */}
            {(() => {
              // Prefer discovered_tools, fallback to tools_config
              const toolsRaw = s.discovered_tools || s.tools_config;
              if (!toolsRaw) return null;
              try {
                const tools = JSON.parse(toolsRaw);
                if (!Array.isArray(tools) || tools.length === 0) return null;
                const isExpanded = expandedTools.has(s.id);
                return (
                  <div className="mt-3 pl-4 border-l-2 border-muted">
                    <button
                      className="text-xs font-medium text-muted-foreground hover:text-foreground transition-colors cursor-pointer flex items-center gap-1"
                      onClick={() => setExpandedTools(prev => {
                        const next = new Set(prev);
                        if (next.has(s.id)) next.delete(s.id); else next.add(s.id);
                        return next;
                      })}
                    >
                      已发现工具 ({tools.length})
                      <span className="text-[10px]">{isExpanded ? '▲' : '▼'}</span>
                    </button>
                    {isExpanded && (
                      <div className="mt-2 space-y-1 max-h-60 overflow-y-auto">
                        {tools.map((t: any, i: number) => (
                          <div key={i} className="text-xs">
                            <span className="font-mono text-primary">{t.name}</span>
                            {t.description && (
                              <span className="ml-2 text-muted-foreground">{t.description}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              } catch { return null; }
            })()}
          </div>
        ))}
        {!loading && servers.length === 0 && (
          <div className="text-sm text-muted-foreground text-center py-12">
            暂无 MCP 服务，点击上方按钮添加
          </div>
        )}
      </div>
    </div>
  );
}

// ── Agents Tab ──────────────────────────────────────────────────────

export function AgentsTab({ workspaceId, defaultWorkspaceId }: { workspaceId?: number; defaultWorkspaceId?: number } = {}) {
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
      setAgents(agentsRes.data);
      setMcpServers(mcpRes.data);
      setDatasources(dsRes.data);
    } catch { toast.error('加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    if (!form.name) { toast.error('请输入 Agent 名称'); return; }
    // Serialize max_retries into config JSON
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
    // Parse config JSON
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
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">配置 Agent，定义不同场景的推理逻辑和工具集</p>
        <Button size="sm" onClick={() => { setEditing(null); setForm({ agent_type: 'custom', is_active: 1, is_default: 0 }); setFormOpen(true); }}>
          <Plus className="h-4 w-4 mr-1" /> 添加 Agent
        </Button>
      </div>

      {/* Form */}
      {formOpen && (
        <div className="border rounded-lg p-4 space-y-4 bg-muted/30">
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
              💡 描述、系统提示词、路由正则已在 <code>prompts/agents/{editing.name}/</code> 目录下的文件中配置。此处仅管理运行时状态。
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>绑定 MCP 服务</Label>
              <Select
                value={form.mcp_server_ids || 'none'}
                onValueChange={v => setForm({ ...form, mcp_server_ids: v === 'none' ? '' : v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择 MCP 服务 (可选)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不绑定</SelectItem>
                  {mcpServers.filter(s => s.is_active).map(s => (
                    <SelectItem key={s.id} value={String(s.id)}>
                      {s.name} ({s.transport})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>绑定数据源</Label>
              <Select
                value={form.datasource_ids || 'none'}
                onValueChange={v => setForm({ ...form, datasource_ids: v === 'none' ? '' : v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择数据源 (可选)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不绑定</SelectItem>
                  {datasources.map(ds => (
                    <SelectItem key={ds.id} value={String(ds.id)}>
                      {ds.name} ({ds.db_type})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>最大重试次数</Label>
              <Input
                type="number"
                min={0}
                max={5}
                value={form.max_retries ?? 2}
                onChange={e => setForm({ ...form, max_retries: parseInt(e.target.value) || 0 })}
                placeholder="2"
              />
              <p className="text-xs text-muted-foreground">数据获取失败时的内部重试次数（0=不重试）</p>
            </div>
            <div className="space-y-1.5">
              <Label>最大迭代次数</Label>
              <Input
                type="number"
                min={1}
                max={50}
                value={form.max_iterations ?? 10}
                onChange={e => setForm({ ...form, max_iterations: parseInt(e.target.value) || 10 })}
                placeholder="10"
              />
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
      <div className="border rounded-lg divide-y">
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

// ── Skills Tab ─────────────────────────────────────────────────────

const SKILL_CATEGORIES = [
  { value: 'analysis', label: '分析技能' },
  { value: 'nl2sql', label: 'NL2SQL' },
  { value: 'chart', label: '图表' },
  { value: 'correction', label: '纠错' },
  { value: 'prediction', label: '预测' },
  { value: 'other', label: '其他' },
];

export function SkillsTab({ workspaceId }: { workspaceId?: number } = {}) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<any>({ category: 'analysis', is_active: 1, source_type: 'user' });
  const [viewPrompt, setViewPrompt] = useState<Skill | null>(null);
  const [promptContent, setPromptContent] = useState('');
  const [filterCategory, setFilterCategory] = useState<string>('all');

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await skillApi.list();
      setSkills(data);
    } catch { toast.error('加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    if (!form.name) { toast.error('请输入 Skill 名称'); return; }
    const submitForm = { ...form, ...(workspaceId ? { workspace_id: workspaceId } : {}) };
    try {
      if (editing) {
        await skillApi.update(editing.id!, submitForm);
        toast.success('已更新');
      } else {
        await skillApi.create(submitForm);
        toast.success('已创建');
      }
      setFormOpen(false);
      setEditing(null);
      setForm({ category: 'analysis', is_active: 1, source_type: 'user' });
      load();
    } catch { toast.error('保存失败'); }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除？')) return;
    try {
      await skillApi.delete(id);
      toast.success('已删除');
      load();
    } catch { toast.error('删除失败'); }
  };

  const handleEdit = (s: Skill) => {
    setEditing(s);
    setForm({ ...s });
    setFormOpen(true);
  };

  const handleCopy = async (name: string) => {
    try {
      const { data } = await skillApi.copy(name, workspaceId);
      if (data.success) {
        toast.success(`已创建副本: ${data.name}`);
        load();
      }
    } catch { toast.error('拷贝失败'); }
  };

  const handleViewPrompt = async (s: Skill) => {
    try {
      const { data } = await skillApi.get(s.name);
      setViewPrompt(data);
      setPromptContent(data.system_prompt || '');
    } catch { toast.error('加载提示词失败'); }
  };

  const filtered = filterCategory === 'all' ? skills : skills.filter(s => s.category === filterCategory);

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <p className="text-sm text-muted-foreground">管理分析技能（Skill），Agent 可动态加载专业分析提示词</p>
          <Select value={filterCategory} onValueChange={setFilterCategory}>
            <SelectTrigger className="w-32 h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部分类</SelectItem>
              {SKILL_CATEGORIES.map(c => (
                <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" onClick={() => { setEditing(null); setForm({ category: 'analysis', is_active: 1, source_type: 'user' }); setFormOpen(true); }}>
          <Plus className="h-4 w-4 mr-1" /> 新建 Skill
        </Button>
      </div>

      {/* Form */}
      {formOpen && (
        <div className="border rounded-lg p-4 space-y-4 bg-muted/30">
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-1.5">
              <Label>Skill 名称 (英文标识)</Label>
              <Input value={form.name || ''} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="my_custom_trend" disabled={!!editing && editing.source_type === 'system'} />
            </div>
            <div className="space-y-1.5">
              <Label>显示名称</Label>
              <Input value={form.display_name || ''} onChange={e => setForm({ ...form, display_name: e.target.value })} placeholder="自定义趋势分析" />
            </div>
            <div className="space-y-1.5">
              <Label>分类</Label>
              <Select value={form.category || 'analysis'} onValueChange={v => setForm({ ...form, category: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SKILL_CATEGORIES.map(c => (
                    <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Input value={form.description || ''} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="描述这个 Skill 的用途" />
          </div>
          <div className="space-y-1.5">
            <Label>System Prompt（提示词内容）</Label>
            <Textarea
              value={form.system_prompt || ''}
              onChange={e => setForm({ ...form, system_prompt: e.target.value })}
              rows={15}
              placeholder="输入分析提示词，指导 Agent 如何执行分析..."
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              提示词定义了分析方法论、执行流程、输出格式。Agent 在执行分析前会加载此内容。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={form.is_active === 1} onCheckedChange={v => setForm({ ...form, is_active: v ? 1 : 0 })} />
            <span className="text-sm">启用</span>
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSave}>保存</Button>
            <Button size="sm" variant="ghost" onClick={() => { setFormOpen(false); setEditing(null); }}>取消</Button>
          </div>
        </div>
      )}

      {/* Prompt Viewer Modal */}
      {viewPrompt && (
        <div className="border rounded-lg p-4 space-y-3 bg-muted/30">
          <div className="flex items-center justify-between">
            <div>
              <span className="font-medium text-sm">{viewPrompt.display_name || viewPrompt.name}</span>
              <span className="ml-2 text-xs text-muted-foreground">提示词预览</span>
            </div>
            <Button size="sm" variant="ghost" onClick={() => { setViewPrompt(null); setPromptContent(''); }}>关闭</Button>
          </div>
          <pre className="text-xs whitespace-pre-wrap bg-background p-4 rounded-md max-h-96 overflow-y-auto border">
            {promptContent || '（无内容）'}
          </pre>
        </div>
      )}

      {/* List */}
      <div className="border rounded-lg divide-y">
        {filtered.map(s => (
          <div key={s.id || s.name} className="p-3 hover:bg-muted/30">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5 flex-1 min-w-0">
                <div className="font-medium text-sm">
                  {s.display_name || s.name}
                  <span className="ml-2 text-xs text-muted-foreground">({s.name})</span>
                  {s.source_type === 'system' && (
                    <span className="ml-2 text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">系统</span>
                  )}
                  {s.source_type === 'user' && (
                    <span className="ml-2 text-xs bg-green-50 text-green-600 px-1.5 py-0.5 rounded">自定义</span>
                  )}
                  {!s.is_active && <span className="ml-2 text-xs text-destructive">已禁用</span>}
                </div>
                <div className="text-xs text-muted-foreground">{s.description}</div>
                {s.source_skill && (
                  <div className="text-xs text-muted-foreground">拷贝自: {s.source_skill}</div>
                )}
              </div>
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => handleViewPrompt(s)} title="查看提示词">
                  <Wrench className="h-4 w-4" />
                </Button>
                {s.source_type === 'system' ? (
                  <Button size="sm" variant="ghost" onClick={() => handleCopy(s.name)} title="拷贝为自定义副本">
                    <Copy className="h-4 w-4" />
                  </Button>
                ) : (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => handleEdit(s)} title="编辑">
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => s.id && handleDelete(s.id)} title="删除">
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
        {!loading && filtered.length === 0 && (
          <div className="text-sm text-muted-foreground text-center py-12">
            {filterCategory === 'all' ? '暂无 Skill' : `暂无 ${SKILL_CATEGORIES.find(c => c.value === filterCategory)?.label || filterCategory} 类型的 Skill`}
          </div>
        )}
      </div>
    </div>
  );
}
