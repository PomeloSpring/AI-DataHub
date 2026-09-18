import { useState, useEffect, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Bot, Star, Lock, Eye } from 'lucide-react';
import client from '@/api/client';
import { useAuthStore } from '../../stores/authStore';

// ── 常量:与后端 sdk_tools 工具组 / tool_catalog 标准工具 / 图表类型对齐 ──
// 工具权限采用"逐个工具"粒度:每个进程内 MCP server 的工具可单独勾选。
const MCP_TOOL_GROUPS: { group: string; label: string; desc: string; tools: { name: string; label: string }[] }[] = [
  {
    group: 'catalog', label: '元数据检索', desc: '数据目录类工具(物理表/列/数据源,对 LLM 相对敏感)',
    tools: [
      { name: 'search_metadata', label: '元数据搜索' },
      { name: 'get_table_schema', label: '表结构' },
      { name: 'list_datasources', label: '数据源列表' },
    ],
  },
  {
    group: 'semantic', label: '业务语义', desc: '统一语义层主路(发现对象/指标/知识 + 声明式查询)',
    tools: [
      { name: 'knowledge_search', label: '知识检索' },
      { name: 'get_metrics', label: '指标' },
      { name: 'get_glossary', label: '术语' },
      { name: 'query_by_tags', label: '标签查询' },
      { name: 'run_semantic_query', label: '语义查询' },
    ],
  },
  {
    group: 'query', label: 'SQL 执行', desc: 'execute_sql 受控旁路(默认不建议开启)',
    tools: [{ name: 'execute_sql', label: '执行 SQL' }],
  },
  {
    group: 'ontology', label: '本体模型', desc: '本体建模与元数据管理工具(AS-BOT 专用)',
    tools: [
      { name: 'search_ontology', label: '搜索本体' },
      { name: 'get_ontology_model', label: '模型详情' },
      { name: 'list_ontology_models', label: '模型列表' },
      { name: 'get_metadata_summary', label: '元数据摘要' },
      { name: 'generate_ontology_draft', label: '生成草案*' },
      { name: 'save_ontology_model', label: '保存模型*' },
      { name: 'activate_ontology_model', label: '激活模型*' },
      { name: 'import_ontology_yaml', label: '导入YAML*' },
    ],
  },
  {
    group: 'screen', label: '可视化大屏', desc: '生成/管理数据大屏 + 字模库取用(取数走语义意图 + 治理护城河)',
    tools: [
      { name: 'create_data_screen', label: '创建大屏*' },
      { name: 'get_data_screen', label: '查看大屏' },
      { name: 'update_data_screen_chart', label: '更新图表*' },
      { name: 'list_vis_components', label: '字模列表' },
      { name: 'get_vis_component', label: '字模详情' },
      { name: 'save_vis_component', label: '存为字模*' },
    ],
  },
];
const MCP_GROUP_TOOLS: Record<string, string[]> = Object.fromEntries(
  MCP_TOOL_GROUPS.map((g) => [g.group, g.tools.map((t) => t.name)]),
);
const STANDARD_TOOLS = [
  { name: 'read', label: '读取' }, { name: 'write', label: '写入' }, { name: 'edit', label: '编辑' },
  { name: 'glob', label: '文件检索' }, { name: 'grep', label: '内容搜索' }, { name: 'bash', label: '命令执行' },
  { name: 'webfetch', label: '网页抓取' }, { name: 'task', label: '子任务' },
];
const CATEGORIES = ['nl2sql', 'analysis', 'chart', 'report', 'custom'];

interface Waker {
  id: number;
  waker_key: string;
  name: string;
  display_name: string;
  description: string;
  category: string;
  system_prompt: string;
  persona: { responsibility?: string; style?: string; boundary?: string };
  tools: { groups: string[]; standard: string[]; mcp: Record<string, string[]> };
  mcp_server_ids: number[];
  datasource_ids: number[];
  knowledge_base_ids: number[];
  skills: string[];
  models: string[];
  chart_enabled: boolean;
  permission_mode: string;
  is_active: boolean;
  is_builtin?: number;
  workspace_id?: number;
  is_default?: number;
}

const emptyWaker = (): Waker => ({
  id: 0, waker_key: '', name: '', display_name: '', description: '', category: 'custom',
  system_prompt: '', persona: {},
  tools: { groups: ['semantic'], standard: ['read', 'grep'], mcp: { semantic: ['knowledge_search', 'run_semantic_query'] } },
  mcp_server_ids: [], datasource_ids: [], knowledge_base_ids: [], skills: [], models: [], chart_enabled: true,
  permission_mode: 'inherit', is_active: true, workspace_id: 0,
});

export default function WakerManager() {
  const [wakers, setWakers] = useState<Waker[]>([]);
  const [mcpServers, setMcpServers] = useState<any[]>([]);
  const [datasources, setDatasources] = useState<any[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<any[]>([]);
  const [skillOptions, setSkillOptions] = useState<any[]>([]);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Waker | null>(null);
  const [form, setForm] = useState<Waker>(emptyWaker());

  // 绑定 tab 状态
  const [workspaces, setWorkspaces] = useState<any[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [bindWsId, setBindWsId] = useState<number>(0);
  const [bindRoleId, setBindRoleId] = useState<number>(0);
  const [wsBindings, setWsBindings] = useState<any[]>([]);
  const [roleBindings, setRoleBindings] = useState<number[]>([]);

  const load = async () => {
    setLoading(true);
    try {
      const [wRes, mcpRes, dsRes, kbRes, wsRes, roleRes, skillRes] = await Promise.all([
        client.get('/admin/wakers/'),
        client.get('/admin/mcp-servers'),
        client.get('/datasources/'),
        client.get('/knowledge-bases'),
        client.get('/workspaces'),
        client.get('/admin/wakers/roles'),
        client.get('/admin/skills'),
      ]);
      setWakers(Array.isArray(wRes.data) ? wRes.data.map(normalizeWaker) : []);
      setMcpServers(Array.isArray(mcpRes.data) ? mcpRes.data : []);
      setDatasources(Array.isArray(dsRes.data) ? dsRes.data : []);
      setKnowledgeBases(Array.isArray(kbRes.data) ? kbRes.data.filter((k: any) => k.status === 'active') : []);
      setWorkspaces(Array.isArray(wsRes.data) ? wsRes.data : []);
      setRoles(Array.isArray(roleRes.data) ? roleRes.data : []);
      setSkillOptions(Array.isArray(skillRes.data) ? skillRes.data : []);
    } catch {
      toast.error('加载失败');
    } finally {
      setLoading(false);
    }
    // 模型候选单独非阻塞加载(需先定位实际 qoder 执行层再按 layer_id 取模型,可能较慢)
    loadModelOptions();
  };

  // 可用模型候选:来自已配置的 qoder(cli)执行层的 list_models。
  // 不能直接用 cli_name=qoder 查(会因缺少层配置/路径/令牌返回空),须用 layer_id。
  const loadModelOptions = async () => {
    try {
      const { data: layers } = await client.get('/admin/execution-layers');
      const arr = Array.isArray(layers) ? layers : [];
      const cli =
        arr.find((x: any) => x.layer_type === 'cli' && ((x.config || {}).cli_name === 'qoder')) ||
        arr.find((x: any) => x.layer_type === 'cli');
      if (!cli) { setModelOptions([]); return; }
      const { data } = await client.get(`/admin/execution-layers/models?layer_id=${cli.id}`);
      setModelOptions(Array.isArray(data?.models) ? data.models : []);
    } catch {
      setModelOptions([]);
    }
  };
  useEffect(() => { load(); }, []);

  const mcpName = (id: number) => mcpServers.find((s) => s.id === id)?.name || `#${id}`;
  const dsName = (id: number) => datasources.find((d) => d.id === id)?.name || `#${id}`;
  const kbName = (id: number) => knowledgeBases.find((k) => k.id === id)?.name || `#${id}`;

  const openCreate = () => { setEditing(null); setForm(emptyWaker()); setFormOpen(true); };
  const openEdit = (w: Waker) => { setEditing(w); setForm(JSON.parse(JSON.stringify(w))); setFormOpen(true); };

  const toggleArr = (arr: (string | number)[], val: string | number) =>
    arr.includes(val) ? arr.filter((x) => x !== val) : [...arr, val];

  // 逐工具 MCP 勾选
  const setMcp = (group: string, names: string[]) =>
    setForm((f) => ({ ...f, tools: { ...f.tools, mcp: { ...(f.tools?.mcp || {}), [group]: names } } }));
  const toggleMcpTool = (group: string, tool: string) => {
    const cur = form.tools?.mcp?.[group] || [];
    setMcp(group, cur.includes(tool) ? cur.filter((x) => x !== tool) : [...cur, tool]);
  };
  const toggleMcpGroup = (group: string, all: string[]) => {
    const cur = form.tools?.mcp?.[group] || [];
    setMcp(group, cur.length === all.length ? [] : [...all]);
  };

  const handleSave = async () => {
    if (!form.name.trim() && !form.waker_key.trim()) { toast.error('请填写名称或标识'); return; }
    const key = (form.waker_key || form.name).trim();
    // 从 mcp 逐工具勾选反向派生 groups(有≥一个工具的组),保持向后兼容与列表展示
    const mcpSel = form.tools?.mcp || {};
    const derivedGroups = Object.keys(mcpSel).filter((g) => (mcpSel[g] || []).length > 0);
    const payload = {
      ...form,
      waker_key: key,
      name: form.name.trim() || key,
      display_name: form.display_name || form.name,
      tools: { ...form.tools, groups: derivedGroups },
      chart_enabled: !!form.chart_enabled,
      is_active: !!form.is_active,
    };
    try {
      if (editing) {
        await client.put(`/admin/wakers/${editing.id}`, payload);
        toast.success('已更新');
      } else {
        await client.post('/admin/wakers/', payload);
        toast.success('已创建');
      }
      setFormOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    }
  };

  const handleDelete = async (w: Waker) => {
    if (w.is_builtin) { toast.error('内置 Waker 不可删除'); return; }
    if (!confirm(`确定删除 Waker "${w.display_name || w.name}"?`)) return;
    try {
      await client.delete(`/admin/wakers/${w.id}`);
      toast.success('已删除');
      load();
    } catch { toast.error('删除失败'); }
  };

  // ── 工作空间绑定 ──
  const loadWsBinding = async (wsId: number) => {
    setBindWsId(wsId);
    if (!wsId) { setWsBindings([]); return; }
    try {
      const { data } = await client.get(`/admin/wakers/workspace/${wsId}/wakers`);
      setWsBindings(Array.isArray(data) ? data : []);
    } catch { setWsBindings([]); }
  };
  const saveWsBinding = async () => {
    try {
      const bindings = wsBindings.map((b) => ({ waker_id: b.waker_id, is_default: !!b.is_default, sort: b.sort || 0 }));
      await client.put(`/admin/wakers/workspace/${bindWsId}/wakers`, { bindings });
      toast.success('工作空间绑定已保存');
    } catch { toast.error('保存失败'); }
  };
  const toggleWsWaker = (wakerId: number, add: boolean) => {
    setWsBindings((prev) => (add
      ? [...prev, { waker_id: wakerId, is_default: prev.length === 0, sort: prev.length }]
      : prev.filter((b) => b.waker_id !== wakerId)));
  };

  // ── 角色绑定 ──
  const loadRoleBinding = async (roleId: number) => {
    setBindRoleId(roleId);
    if (!roleId) { setRoleBindings([]); return; }
    try {
      const { data } = await client.get(`/admin/wakers/role/${roleId}/wakers`);
      setRoleBindings(data?.waker_ids || []);
    } catch { setRoleBindings([]); }
  };
  const saveRoleBinding = async () => {
    try {
      await client.put(`/admin/wakers/role/${bindRoleId}/wakers`, { waker_ids: roleBindings });
      toast.success('角色绑定已保存');
    } catch { toast.error('保存失败'); }
  };

  const { user } = useAuthStore();
  const isAdmin = user?.role === 'admin';
  const boundWakerIds = useMemo(() => new Set(wsBindings.map((b) => b.waker_id)), [wsBindings]);
  const roleWakerSet = useMemo(() => new Set(roleBindings), [roleBindings]);
  const isReadOnly = !!(editing?.is_builtin && !isAdmin);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Bot className="h-6 w-6 text-primary" />Waker 配置</h1>
          <p className="text-muted-foreground text-sm mt-1">角色化智能体的统一定义入口(替代分散的 Agent / Skills 配置)</p>
        </div>
        <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4 mr-1" /> 添加 Waker</Button>
      </div>

      <Tabs defaultValue="wakers">
        <TabsList>
          <TabsTrigger value="wakers">Waker 定义</TabsTrigger>
          <TabsTrigger value="workspace">工作空间绑定</TabsTrigger>
          <TabsTrigger value="role">角色绑定</TabsTrigger>
        </TabsList>

        {/* ── Waker 列表 ── */}
        <TabsContent value="wakers" className="mt-4">
          <div className="border border-border rounded-xl divide-y divide-border">
            {wakers.map((w) => (
              <div key={w.id} className="flex items-center justify-between p-3 hover:bg-muted/30">
                <div className="space-y-0.5 min-w-0">
                  <div className="font-medium text-sm flex items-center gap-2 flex-wrap">
                    {w.display_name || w.name}
                    <span className="text-xs text-muted-foreground">({w.name})</span>
                    {!!w.is_builtin && <Badge variant="secondary" className="text-xs"><Lock className="h-2.5 w-2.5 mr-0.5 inline" />系统内置</Badge>}
                    {w.chart_enabled && <Badge variant="outline" className="text-xs">图表</Badge>}
                    {!w.is_active && <span className="text-xs text-destructive">已禁用</span>}
                  </div>
                  <div className="text-xs text-muted-foreground truncate">{w.description}</div>
                  <div className="flex gap-1.5 mt-1 flex-wrap">
                    {Object.entries(w.tools?.mcp || {}).flatMap(([g, names]) =>
                      (names || []).map((t) => (
                        <span key={`${g}.${t}`} className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">{g}·{t}</span>
                      ))
                    )}
                    {(w.tools?.standard || []).map((t) => (
                      <span key={t} className="text-xs bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">{t}</span>
                    ))}
                    {w.mcp_server_ids?.map((id) => (
                      <span key={`m${id}`} className="text-xs bg-green-50 text-green-600 px-1.5 py-0.5 rounded">MCP: {mcpName(id)}</span>
                    ))}
                    {w.datasource_ids?.map((id) => (
                      <span key={`d${id}`} className="text-xs bg-amber-50 text-amber-600 px-1.5 py-0.5 rounded">数据源: {dsName(id)}</span>
                    ))}
                    {w.knowledge_base_ids?.map((id) => (
                      <span key={`k${id}`} className="text-xs bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded">知识库: {kbName(id)}</span>
                    ))}
                    {(w.skills || []).map((sk) => (
                      <span key={`s${sk}`} className="text-xs bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded">技能: {sk}</span>
                    ))}
                  </div>
                </div>
                <div className="flex gap-1 flex-shrink-0">
                  {(w.is_builtin && !isAdmin) ? (
                    <Button size="sm" variant="ghost" onClick={() => openEdit(w)} title="查看配置(不可编辑)"><Eye className="h-4 w-4" /></Button>
                  ) : (
                    <Button size="sm" variant="ghost" onClick={() => openEdit(w)}><Pencil className="h-4 w-4" /></Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => handleDelete(w)} disabled={!!w.is_builtin}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
            ))}
            {!loading && wakers.length === 0 && (
              <div className="text-sm text-muted-foreground text-center py-12">暂无 Waker,点击上方按钮添加</div>
            )}
          </div>
        </TabsContent>

        {/* ── 工作空间绑定 ── */}
        <TabsContent value="workspace" className="mt-4 space-y-4">
          <div className="flex items-center gap-3">
            <Label className="shrink-0">工作空间</Label>
            <Select value={String(bindWsId)} onValueChange={(v) => loadWsBinding(Number(v))}>
              <SelectTrigger className="w-[260px]"><SelectValue placeholder="选择工作空间" /></SelectTrigger>
              <SelectContent>
                {workspaces.map((ws) => (<SelectItem key={ws.id} value={String(ws.id)}>{ws.name}</SelectItem>))}
              </SelectContent>
            </Select>
            {bindWsId > 0 && <Button size="sm" onClick={saveWsBinding}>保存绑定</Button>}
          </div>
          {bindWsId > 0 ? (
            <div className="border border-border rounded-xl divide-y divide-border">
              {wakers.map((w) => {
                const bound = boundWakerIds.has(w.id);
                const binding = wsBindings.find((b) => b.waker_id === w.id);
                return (
                  <div key={w.id} className="flex items-center justify-between p-3">
                    <div className="text-sm">{w.display_name || w.name}<span className="text-xs text-muted-foreground ml-2">({w.name})</span></div>
                    <div className="flex items-center gap-4">
                      {bound && (
                        <button
                          className="flex items-center gap-1 text-xs"
                          onClick={() => setWsBindings((prev) => prev.map((b) => ({
                            ...b, is_default: b.waker_id === w.id ? !b.is_default : false,
                          })))}
                        >
                          <Star className={`h-4 w-4 ${binding?.is_default ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground'}`} />
                          默认
                        </button>
                      )}
                      <Switch checked={bound} onCheckedChange={(v) => toggleWsWaker(w.id, v)} />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">请选择工作空间以配置其可用 Waker。</p>
          )}
        </TabsContent>

        {/* ── 角色绑定 ── */}
        <TabsContent value="role" className="mt-4 space-y-4">
          <div className="flex items-center gap-3">
            <Label className="shrink-0">角色</Label>
            <Select value={String(bindRoleId)} onValueChange={(v) => loadRoleBinding(Number(v))}>
              <SelectTrigger className="w-[260px]"><SelectValue placeholder="选择角色" /></SelectTrigger>
              <SelectContent>
                {roles.map((r) => (<SelectItem key={r.id} value={String(r.id)}>{r.display_name || r.name}</SelectItem>))}
              </SelectContent>
            </Select>
            {bindRoleId > 0 && <Button size="sm" onClick={saveRoleBinding}>保存绑定</Button>}
          </div>
          {bindRoleId > 0 ? (
            <div className="flex flex-wrap gap-2">
              {wakers.map((w) => {
                const active = roleWakerSet.has(w.id);
                return (
                  <Badge
                    key={w.id}
                    variant={active ? 'default' : 'outline'}
                    className="cursor-pointer"
                    onClick={() => setRoleBindings((prev) => toggleArr(prev, w.id) as number[])}
                  >
                    {w.display_name || w.name}
                  </Badge>
                );
              })}
              {wakers.length === 0 && <p className="text-sm text-muted-foreground">暂无 Waker</p>}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">请选择角色以配置其可用 Waker。</p>
          )}
        </TabsContent>
      </Tabs>

      {/* ── 编辑对话框 ── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {editing ? (isReadOnly ? '查看 Waker 配置' : '编辑 Waker') : '新建 Waker'}
              {isReadOnly && <Badge variant="secondary" className="text-xs"><Lock className="h-3 w-3 mr-1" />系统内置 · 不可编辑</Badge>}
            </DialogTitle>
            <DialogDescription>{isReadOnly ? '该 Waker 为系统内置配置，仅支持查看' : '定义角色化智能体的人格、工具、技能与资源引用'}</DialogDescription>
          </DialogHeader>

          <div className={`space-y-4 ${isReadOnly ? 'pointer-events-none opacity-90' : ''}`}>
            <div className="grid grid-cols-2 gap-4">
              <Field label="标识 (waker_key)">
                <Input value={form.waker_key} disabled={!!editing} onChange={(e) => setForm({ ...form, waker_key: e.target.value })} placeholder="data_analyst" />
              </Field>
              <Field label="名称">
                <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="data_analyst" />
              </Field>
              <Field label="显示名称">
                <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="数据分析师" />
              </Field>
              <Field label="分类">
                <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{CATEGORIES.map((c) => (<SelectItem key={c} value={c}>{c}</SelectItem>))}</SelectContent>
                </Select>
              </Field>
            </div>

            <Field label="描述">
              <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="这个 Waker 的职责概述" />
            </Field>

            <Field label="系统提示词 (角色设定)">
              <Textarea rows={3} value={form.system_prompt} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} placeholder="你是..." />
            </Field>

            <div className="grid grid-cols-3 gap-4">
              <Field label="职责"><Textarea rows={2} value={form.persona?.responsibility || ''} onChange={(e) => setForm({ ...form, persona: { ...form.persona, responsibility: e.target.value } })} /></Field>
              <Field label="风格"><Textarea rows={2} value={form.persona?.style || ''} onChange={(e) => setForm({ ...form, persona: { ...form.persona, style: e.target.value } })} /></Field>
              <Field label="边界"><Textarea rows={2} value={form.persona?.boundary || ''} onChange={(e) => setForm({ ...form, persona: { ...form.persona, boundary: e.target.value } })} /></Field>
            </div>

            <Field label="工具权限 (逐工具粒度 — 未勾选的工具不会注册，LLM 无从调用)">
              <div className="space-y-3">
                {MCP_TOOL_GROUPS.map((g) => {
                  const sel = form.tools?.mcp?.[g.group] || [];
                  const all = g.tools.map((t) => t.name);
                  const allOn = sel.length === all.length;
                  return (
                    <div key={g.group} className="border border-border rounded-lg p-2.5">
                      <button
                        type="button"
                        className="flex items-center gap-2 text-sm font-medium w-full text-left"
                        onClick={() => toggleMcpGroup(g.group, all)}
                        title={g.desc}
                      >
                        <span className={`inline-block w-3.5 h-3.5 rounded border ${allOn ? 'bg-primary border-primary' : 'border-muted-foreground/40'}`} />
                        {g.label}
                        <span className="text-xs text-muted-foreground">({sel.length}/{all.length})</span>
                      </button>
                      <div className="flex flex-wrap gap-2 mt-2 pl-5">
                        {g.tools.map((t) => {
                          const active = sel.includes(t.name);
                          return (
                            <Badge key={t.name} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                              title={t.name}
                              onClick={() => toggleMcpTool(g.group, t.name)}>
                              {t.label}
                            </Badge>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Field>

            <Field label="标准工具白名单 (文件/命令等内置工具)">
              <div className="flex flex-wrap gap-2">
                {STANDARD_TOOLS.map((t) => {
                  const active = (form.tools?.standard || []).includes(t.name);
                  return (
                    <Badge key={t.name} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                      onClick={() => setForm({ ...form, tools: { ...form.tools, standard: toggleArr(form.tools?.standard || [], t.name) as string[] } })}>
                      {t.label}
                    </Badge>
                  );
                })}
              </div>
            </Field>

            <div className="grid grid-cols-2 gap-4">
              <Field label="引用 MCP 服务">
                <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                  {mcpServers.filter((s) => s.is_active).map((s) => {
                    const active = (form.mcp_server_ids || []).includes(s.id);
                    return (
                      <Badge key={s.id} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                        onClick={() => setForm({ ...form, mcp_server_ids: toggleArr(form.mcp_server_ids || [], s.id) as number[] })}>
                        {s.name}
                      </Badge>
                    );
                  })}
                  {mcpServers.filter((s) => s.is_active).length === 0 && <span className="text-xs text-muted-foreground">无可用 MCP</span>}
                </div>
              </Field>
              <Field label="引用数据源">
                <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                  {datasources.map((ds) => {
                    const active = (form.datasource_ids || []).includes(ds.id);
                    return (
                      <Badge key={ds.id} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                        onClick={() => setForm({ ...form, datasource_ids: toggleArr(form.datasource_ids || [], ds.id) as number[] })}>
                        {ds.name}
                      </Badge>
                    );
                  })}
                  {datasources.length === 0 && <span className="text-xs text-muted-foreground">无数据源</span>}
                </div>
              </Field>
              <Field label="引用知识库">
                <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                  {knowledgeBases.map((kb) => {
                    const active = (form.knowledge_base_ids || []).includes(kb.id);
                    return (
                      <Badge key={kb.id} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                        title={kb.description}
                        onClick={() => setForm({ ...form, knowledge_base_ids: toggleArr(form.knowledge_base_ids || [], kb.id) as number[] })}>
                        {kb.name}
                      </Badge>
                    );
                  })}
                  {knowledgeBases.length === 0 && <span className="text-xs text-muted-foreground">无可用知识库</span>}
                </div>
              </Field>
            </div>

            {/* 技能勾选绑定 */}
            <Field label="引用技能 (Skills)">
              <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                {skillOptions.map((sk) => {
                  const active = (form.skills || []).includes(sk.name);
                  return (
                    <Badge key={sk.name} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                      title={`${sk.description || ''}${sk.is_builtin ? '（内置）' : ''}`}
                      onClick={() => setForm({ ...form, skills: toggleArr(form.skills || [], sk.name) as string[] })}>
                      {sk.display_name || sk.name}{sk.is_builtin ? ' ·内置' : ''}
                    </Badge>
                  );
                })}
                {skillOptions.length === 0 && <span className="text-xs text-muted-foreground">无可用技能，请先在“技能配置”中创建</span>}
              </div>
            </Field>

            {/* 可用模型（Chat 端可切换；仅一个/留空时不展示选择框） */}
            <Field label="可用模型 (Chat 对话页可在其中切换；留空=使用执行层默认模型；仅选 1 个时不展示下拉)">
              <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                {(() => {
                  const opts = Array.from(new Set([...modelOptions, ...(form.models || [])]));
                  return opts.map((m) => {
                    const active = (form.models || []).includes(m);
                    return (
                      <Badge key={m} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                        onClick={() => setForm({ ...form, models: toggleArr(form.models || [], m) as string[] })}>
                        {m}
                      </Badge>
                    );
                  });
                })()}
                {modelOptions.length === 0 && (form.models || []).length === 0 && (
                  <span className="text-xs text-muted-foreground">未获取到执行层模型列表（将使用执行层默认模型）</span>
                )}
              </div>
            </Field>

            <div className="grid grid-cols-3 gap-4 items-end">
              <Field label="权限模式">
                <Select value={form.permission_mode} onValueChange={(v) => setForm({ ...form, permission_mode: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="inherit">继承用户角色</SelectItem>
                    <SelectItem value="restrict">严格限制</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <div className="flex items-center gap-2">
                <Switch checked={!!form.chart_enabled} onCheckedChange={(v) => setForm({ ...form, chart_enabled: v })} />
                <span className="text-sm">注入图表契约</span>
              </div>
              <div className="flex items-center gap-2">
                <Switch checked={!!form.is_active} onCheckedChange={(v) => setForm({ ...form, is_active: v })} />
                <span className="text-sm">启用</span>
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setFormOpen(false)}>{isReadOnly ? '关闭' : '取消'}</Button>
            {!isReadOnly && <Button onClick={handleSave}>保存</Button>}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

// DB 行 → 前端表单结构(persona/tools/mcp_server_ids/skills 兜底为对象/数组)
function normalizeWaker(r: any): Waker {
  const tools = typeof r.tools === 'string' ? JSON.parse(r.tools || '{}') : (r.tools || {});
  const groups: string[] = Array.isArray(tools) ? tools : (tools.groups || []);
  const standard: string[] = Array.isArray(tools) ? [] : (tools.standard || []);
  // 优先用已配置的逐工具 mcp;旧数据无 mcp 时按 groups 展开全部工具(向后兼容)
  let mcp: Record<string, string[]> = (!Array.isArray(tools) && tools.mcp) ? tools.mcp : {};
  if (!mcp || Object.keys(mcp).length === 0) {
    mcp = Object.fromEntries(groups.filter((g) => MCP_GROUP_TOOLS[g]).map((g) => [g, [...MCP_GROUP_TOOLS[g]]]));
  }
  return {
    ...r,
    persona: typeof r.persona === 'string' ? JSON.parse(r.persona || '{}') : (r.persona || {}),
    tools: { groups, standard, mcp },
    mcp_server_ids: parseArr(r.mcp_server_ids),
    datasource_ids: parseArr(r.datasource_ids),
    knowledge_base_ids: parseArr(r.knowledge_base_ids),
    skills: parseArr(r.skills)
      .map((s: any) => (typeof s === 'string' ? s : String(s?.name || s?.key || '')))
      .filter(Boolean),
    models: parseArr(r.models).map((m: any) => String(m)).filter(Boolean),
    chart_enabled: !!r.chart_enabled,
    is_active: !!r.is_active,
  };
}

function parseArr(v: any): any[] {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string') { try { return JSON.parse(v) || []; } catch { return []; } }
  return [];
}
