import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { Plus, Pencil, Trash2 } from 'lucide-react';
import client from '@/api/client';

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
  /** 平台内置服务：后端不返回 url/command/args/env，只给业务可读信息与就绪状态 */
  builtin?: boolean;
  label?: string;
  state?: string;
  status_message?: string;
  /** 内置服务已开放的数据范围数量（只给数量，范围清单属部署配置） */
  open_view_count?: number;
}

const BUILTIN_STATE_BADGE: Record<string, { text: string; cls: string }> = {
  ready: { text: '就绪', cls: 'text-green-600' },
  incomplete: { text: '配置不完整', cls: 'text-amber-600' },
  disabled: { text: '未启用', cls: 'text-muted-foreground' },
  // 库里有内置行、代码里没有实现（服务已退役或迁移漏登记）：属配置漂移，必须显式可见
  unknown: { text: '未登记实现', cls: 'text-destructive' },
}

const builtinBadge = (s: MCPServer) =>
  BUILTIN_STATE_BADGE[s.state || ''] || { text: s.state || '未知', cls: 'text-muted-foreground' };

const EMPTY_FORM: Record<string, any> = { transport: 'sse', is_active: 1 };

export default function MCPConfig() {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<MCPServer | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<any>({ ...EMPTY_FORM });
  const [testing, setTesting] = useState<number | null>(null);
  // 工具清单的展开态：集合里存**当前展开**的服务 id，内置与外部一致默认收起。
  // 清单是明细信息，不该在页面首屏平铺；要看点了再展开。
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set());
  const [discoveredToolsList, setDiscoveredToolsList] = useState<any[]>([]);
  const [selectedToolNames, setSelectedToolNames] = useState<Set<string>>(new Set());

  const load = async () => {
    setLoading(true);
    try {
      // 内置服务与外部服务同一列表（同一配置入口），内置行由后端剥光配置字段
      const { data } = await client.get('/admin/mcp-servers');
      setServers(Array.isArray(data) ? data : []);
    } catch { toast.error('加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  /** 关闭表单弹窗并复位全部编辑态：发现工具清单/勾选集也必须清，否则上一次编辑的服务
   *  工具清单会串到新建表单里（表现为新建时显示别人的工具勾选，保存写错 tools_config）。 */
  const closeForm = () => {
    setFormOpen(false);
    setEditing(null);
    setForm({ ...EMPTY_FORM });
    setDiscoveredToolsList([]);
    setSelectedToolNames(new Set());
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ ...EMPTY_FORM });
    setDiscoveredToolsList([]);
    setSelectedToolNames(new Set());
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name) { toast.error('请输入服务名称'); return; }
    let toolsConfigStr = form.tools_config || '';
    if (discoveredToolsList.length > 0) {
      const selected = discoveredToolsList.filter(t => selectedToolNames.has(t.name));
      toolsConfigStr = JSON.stringify(selected.map(t => ({ name: t.name, description: t.description || '' })));
    }
    const submitData = { ...form, tools_config: toolsConfigStr };
    delete submitData.workspace_id;
    delete submitData.workspace_ids;
    try {
      if (editing) {
        await client.put(`/admin/mcp-servers/${editing.id}`, submitData);
        toast.success('已更新');
      } else {
        await client.post('/admin/mcp-servers', submitData);
        toast.success('已创建');
      }
      closeForm();
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
    let discovered: any[] = [];
    try { discovered = JSON.parse(s.discovered_tools || '[]'); } catch {}
    if (!Array.isArray(discovered)) discovered = [];
    setDiscoveredToolsList(discovered);
    let selectedNames: string[] = [];
    try {
      const cfg = JSON.parse(s.tools_config || '[]');
      if (Array.isArray(cfg) && cfg.length > 0) {
        selectedNames = cfg.map((t: any) => typeof t === 'string' ? t : t.name).filter(Boolean);
      }
    } catch {}
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
        // 测通了就把清单展开给用户看：这是用户显式点「测试连接」后的反馈，
        // 不是首屏默认态（默认仍收起）
        setExpandedTools(prev => { const next = new Set(prev); next.add(id); return next; });
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
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">MCP 服务</h1>
          <p className="text-muted-foreground text-sm mt-1">注册 MCP 服务，为 Agent 提供工具能力</p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={openCreate}>
            <Plus className="h-4 w-4 mr-1" /> 添加服务
          </Button>
        </div>
      </div>

      {/* Form —— 新建/编辑统一走弹窗（不再内联顶开列表；工具清单等长内容靠 DialogContent 限高滚动） */}
      <Dialog open={formOpen} onOpenChange={(open) => { if (!open) closeForm(); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑 MCP 服务' : '添加 MCP 服务'}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>服务名称</Label>
              <Input value={form.name || ''} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="filesystem" />
            </div>
            <div className="space-y-1.5">
              <Label>传输方式</Label>
              <Select value={form.transport} onValueChange={v => setForm({ ...form, transport: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="sse">SSE (远程服务)</SelectItem>
                  <SelectItem value="streamable_http">Streamable HTTP (远程服务)</SelectItem>
                  <SelectItem value="stdio">stdio (本地进程)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Input value={form.description || ''} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="文件系统访问服务" />
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
                  <Input value={form.args || ''} onChange={e => setForm({ ...form, args: e.target.value })} placeholder="-y,@modelcontextprotocol/server-filesystem" />
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
          {discoveredToolsList.length > 0 ? (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>可用工具（勾选需要启用的工具）</Label>
                <span className="text-xs text-muted-foreground">
                  已选 {selectedToolNames.size} / {discoveredToolsList.length}
                </span>
              </div>
              <div className="border rounded-md p-3 max-h-60 overflow-y-auto space-y-1.5 bg-background">
                <div className="flex gap-3 mb-2 pb-2 border-b">
                  <button type="button" className="text-xs text-primary hover:underline"
                    onClick={() => setSelectedToolNames(new Set(discoveredToolsList.map(t => t.name)))}>全选</button>
                  <button type="button" className="text-xs text-muted-foreground hover:underline"
                    onClick={() => setSelectedToolNames(new Set())}>全不选</button>
                </div>
                {discoveredToolsList.map((t: any, i: number) => (
                  <label key={i} className="flex items-start gap-2 cursor-pointer group">
                    <input type="checkbox" className="mt-0.5 rounded"
                      checked={selectedToolNames.has(t.name)}
                      onChange={(e) => {
                        setSelectedToolNames(prev => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(t.name); else next.delete(t.name);
                          return next;
                        });
                      }} />
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-mono group-hover:text-primary transition-colors">{t.name}</span>
                      {t.description && <span className="ml-2 text-xs text-muted-foreground">{t.description}</span>}
                    </div>
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">未勾选的工具不会暴露给 LLM。留空 = 全部可用。</p>
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label>Tools 配置 (JSON)</Label>
              <Textarea value={form.tools_config || ''} onChange={e => setForm({ ...form, tools_config: e.target.value })} placeholder='[{"name":"query_sql","description":"执行SQL查询"}]' rows={3} />
              <p className="text-xs text-muted-foreground">先测试连通性以自动发现工具，或手动输入 JSON 配置。</p>
            </div>
          )}
          <div className="flex items-center gap-2">
            <Switch checked={form.is_active === 1} onCheckedChange={v => setForm({ ...form, is_active: v ? 1 : 0 })} />
            <span className="text-sm">启用</span>
          </div>
          <DialogFooter>
            <Button size="sm" variant="ghost" onClick={closeForm}>取消</Button>
            <Button size="sm" onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* List */}
      <div className="border border-border rounded-xl divide-y divide-border">
        {servers.map(s => (
          <div key={s.id} className="p-3 hover:bg-muted/50 transition-colors">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <div className="font-medium text-sm">
                  {s.label || s.name}
                  {s.builtin ? (
                    <>
                      <span className="ml-2 text-xs text-muted-foreground">平台内置</span>
                      <span className={`ml-2 text-xs ${builtinBadge(s).cls}`}>{builtinBadge(s).text}</span>
                    </>
                  ) : (
                    <>
                      <span className="ml-2 text-xs text-muted-foreground">({s.transport})</span>
                      {!s.is_active && <span className="ml-2 text-xs text-destructive">已禁用</span>}
                      {s.last_test_status === 'success' && <span className="ml-2 text-xs text-green-600">✅ 已连接</span>}
                      {s.last_test_status === 'failed' && <span className="ml-2 text-xs text-destructive">❌ 连接失败</span>}
                    </>
                  )}
                </div>
                {/* 只显业务描述：旧写法在描述为空时回退显示 url/command，会把连接地址当名称摊开 */}
                <div className="text-xs text-muted-foreground max-w-3xl">{s.description || '（无描述）'}</div>
                {s.builtin && s.status_message && (
                  <div className="text-xs text-amber-600 mt-1">{s.status_message}</div>
                )}
                {/* 内置服务给数量不给名单：管理员要能判断“配了没配”，但页面读不到配置本身 */}
                {s.builtin && typeof s.open_view_count === 'number' && (
                  <div className="text-xs text-muted-foreground mt-1">
                    已开放 {s.open_view_count} 个数据范围
                  </div>
                )}
              </div>
              <div className="flex gap-1">
                <Button size="sm" variant="outline" onClick={() => handleTest(s.id)} disabled={testing === s.id}>
                  {testing === s.id ? '测试中...' : '测试连接'}
                </Button>
                {/* 内置服务不给编辑/删除入口：能力定义与连接配置均由系统与部署侧托管 */}
                {!s.builtin && (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => handleEdit(s)}><Pencil className="h-4 w-4" /></Button>
                    <Button size="sm" variant="ghost" onClick={() => handleDelete(s.id)}><Trash2 className="h-4 w-4" /></Button>
                  </>
                )}
              </div>
            </div>
            {(() => {
              const toolsRaw = s.discovered_tools || s.tools_config;
              if (!toolsRaw) return null;
              try {
                const tools = typeof toolsRaw === 'string' ? JSON.parse(toolsRaw) : toolsRaw;
                if (!Array.isArray(tools) || tools.length === 0) return null;
                // 工具清单默认收起（内置/外部同口径）：数量已在按钮上给出，
                // 明细点了才展开，避免首屏被工具描述打散。展开态可反复切换。
                const isExpanded = expandedTools.has(s.id);
                return (
                  <div className="mt-3 pl-4 border-l-2 border-muted">
                    <button className="text-xs font-medium text-muted-foreground hover:text-foreground transition-colors cursor-pointer flex items-center gap-1"
                      onClick={() => setExpandedTools(prev => { const next = new Set(prev); if (next.has(s.id)) next.delete(s.id); else next.add(s.id); return next; })}>
                      {s.builtin ? '提供工具' : '已发现工具'} ({tools.length})
                      <span className="text-[10px]">{isExpanded ? '▲' : '▼'}</span>
                    </button>
                    {isExpanded && (
                      <div className="mt-2 space-y-1 max-h-60 overflow-y-auto">
                        {tools.map((t: any, i: number) => (
                          <div key={i} className="text-xs">
                            <span className="font-mono text-primary">{t.name}</span>
                            {t.description && <span className="ml-2 text-muted-foreground">{t.description}</span>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              } catch { return null; }
            })()}
            {s.builtin && (
              <div className="mt-2 text-[11px] text-muted-foreground">
                连接地址、凭据与访问范围由部署配置托管，不在本页面显示或修改；启用范围请在 AS-BOT「引用 MCP 服务」中绑定并逐工具授权。
              </div>
            )}
          </div>
        ))}
        {!loading && servers.length === 0 && (
          <div className="text-sm text-muted-foreground text-center py-12">暂无 MCP 服务，点击上方按钮添加</div>
        )}
      </div>
    </div>
  );
}
