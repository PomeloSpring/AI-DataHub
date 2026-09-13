import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Terminal, Radar, Wrench, RefreshCw } from 'lucide-react';
import client from '@/api/client';

interface ExecutionLayer {
  id: number;
  name: string;
  display_name: string;
  description: string;
  layer_type: string;
  config: Record<string, any>;
  status: string;
  health_check_at?: string;
  last_test_status?: string;
  last_test_message?: string;
  // Phase 3: 注册与发现
  capabilities?: string[];
  tools?: { name: string; description?: string; type?: string }[];
  source?: string;
  last_heartbeat_at?: string;
  endpoint_url?: string;
  registered_at?: string;
}

interface DiscoveredCLI {
  name: string;
  path: string;
  version: string;
  capabilities: string[];
  default_command: string[];
}

const LAYER_TYPE_LABELS: Record<string, string> = {
  builtin: '内置执行层',
  cli: 'CLI 进程',
  docker: 'Docker 容器',
  remote: '远程 Agent',
};

/** 自注册层心跳超时窗口(秒),需与后端 EXEC_LAYER_HEARTBEAT_TIMEOUT 保持一致. */
const HEARTBEAT_TIMEOUT_SEC = 90;

/** 前端健康判定,镜像后端 _row_healthy:active + (非自注册 或 心跳在窗口内). */
function layerHealthy(l: ExecutionLayer): boolean {
  if (l.status !== 'active') return false;
  if (l.source !== 'self') return true;
  const ts = l.last_heartbeat_at ? new Date(l.last_heartbeat_at).getTime() : 0;
  return ts > 0 && Date.now() - ts <= HEARTBEAT_TIMEOUT_SEC * 1000;
}

/** 相对时间(用于展示最近一次心跳). */
function relTime(iso?: string): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diff < 60) return `${diff}秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  return `${Math.floor(diff / 86400)}天前`;
}

const EMPTY_FORM = {
  name: '',
  display_name: '',
  description: '',
  layer_type: 'cli',
  status: 'active',
  cli_name: '',
  cli_path: '',
  env_text: '',
  timeout: 300,
  model: '',
  mode: 'process',
  allowed_tools: [] as string[],
  dirs_text: '',
};

export default function ExecutionLayers() {
  const [layers, setLayers] = useState<ExecutionLayer[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ExecutionLayer | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<any>({ ...EMPTY_FORM });
  const [testing, setTesting] = useState<number | null>(null);
  const [discovered, setDiscovered] = useState<DiscoveredCLI[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [toolsLoading, setToolsLoading] = useState<number | null>(null);
  const [toolsSource, setToolsSource] = useState<Record<number, string>>({});
  // 标准工具目录(供 allowed_tools chips 候选);与执行层实测工具合并
  const [toolCatalog, setToolCatalog] = useState<{ name: string; label?: string; description?: string }[]>([]);

  const loadModels = async (cliName: string, layerId?: number) => {
    if (!cliName) { setModelOptions([]); return; }
    setLoadingModels(true);
    try {
      const qs = layerId ? `layer_id=${layerId}` : `cli_name=${cliName}`;
      const { data } = await client.get(`/admin/execution-layers/models?${qs}`);
      setModelOptions(Array.isArray(data?.models) ? data.models : []);
    } catch {
      // 查询失败不阻断表单,留空即可(使用 CLI 默认模型)
      setModelOptions([]);
    }
    finally { setLoadingModels(false); }
  };

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/admin/execution-layers');
      setLayers(Array.isArray(data) ? data : []);
    } catch {
      // 服务未启动/未迁移时不报错,展示空状态
      setLayers([]);
    }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  // 加载标准工具目录(一次);失败不阻断表单
  useEffect(() => {
    client.get('/admin/execution-layers/tools')
      .then(({ data }) => setToolCatalog(Array.isArray(data) ? data : []))
      .catch(() => setToolCatalog([]));
  }, []);

  // 实时状态:每 30s 刷新执行层列表(心跳 / 健康 / 工具目录随自注册更新)
  useEffect(() => {
    const timer = setInterval(() => { load(); }, 30000);
    return () => clearInterval(timer);
  }, []);

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      const { data } = await client.get('/admin/execution-layers/discover');
      const found: DiscoveredCLI[] = Array.isArray(data) ? data : [];
      setDiscovered(found);
      if (found.length === 0) {
        toast.info('未发现已安装的 CLI 工具(qoder)');
      } else {
        toast.success(`发现 ${found.length} 个 CLI 工具`);
      }
    } catch {
      setDiscovered([]);
      toast.info('CLI 发现不可用(后端服务未启动或未更新)');
    }
    finally { setDiscovering(false); }
  };

  const applyDiscovered = (c: DiscoveredCLI) => {
    setFormOpen(true);
    setEditing(null);
    setForm({
      ...EMPTY_FORM,
      name: `cli-${c.name}`,
      display_name: `${c.name} CLI`,
      description: c.version ? `版本: ${c.version}` : '',
      cli_name: c.name,
      cli_path: c.path,
    });
  };

  const handleSave = async () => {
    if (!form.name) { toast.error('请输入执行层名称'); return; }
    if (form.layer_type === 'cli' && !form.cli_name) { toast.error('请选择 CLI 类型'); return; }

    const config: Record<string, any> = {};
    if (form.layer_type === 'cli') {
      config.cli_name = form.cli_name;
      if (form.cli_path) config.cli_path = form.cli_path;
      if (form.timeout) config.timeout = Number(form.timeout);
      if (form.model) config.model = form.model;
      if (form.cli_name === 'qoder' && form.mode === 'sdk') config.mode = 'sdk';
      if (form.env_text) {
        try {
          config.env = JSON.parse(form.env_text);
        } catch { toast.error('环境变量不是合法 JSON'); return; }
      }
    }

    // 执行层定义级权限白名单(对所有类型生效)
    if (form.allowed_tools?.length) config.allowed_tools = form.allowed_tools;
    const dirs = (form.dirs_text || '').split('\n').map((s: string) => s.trim()).filter(Boolean);
    if (dirs.length) config.allowed_dirs = dirs;

    const payload = {
      name: form.name,
      display_name: form.display_name,
      description: form.description,
      layer_type: form.layer_type,
      status: form.status,
      config,
    };
    try {
      if (editing) {
        await client.put(`/admin/execution-layers/${editing.id}`, payload);
        toast.success('已更新');
      } else {
        await client.post('/admin/execution-layers', payload);
        toast.success('已创建');
      }
      setFormOpen(false);
      setEditing(null);
      setForm({ ...EMPTY_FORM });
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  };

  const handleEdit = (l: ExecutionLayer) => {
    setEditing(l);
    setForm({
      name: l.name,
      display_name: l.display_name || '',
      description: l.description || '',
      layer_type: l.layer_type,
      status: l.status,
      cli_name: l.config?.cli_name || '',
      cli_path: l.config?.cli_path || '',
      env_text: l.config?.env ? JSON.stringify(l.config.env) : '',
      timeout: l.config?.timeout || 300,
      model: l.config?.model || '',
      mode: l.config?.mode === 'sdk' ? 'sdk' : 'process',
      allowed_tools: Array.isArray(l.config?.allowed_tools) ? l.config.allowed_tools : [],
      dirs_text: Array.isArray(l.config?.allowed_dirs) ? l.config.allowed_dirs.join('\n') : '',
    });
    setFormOpen(true);
    if (l.layer_type === 'cli' && l.config?.cli_name) {
      loadModels(l.config.cli_name, l.id);
    }
  };

  const handleDelete = async (l: ExecutionLayer) => {
    if (l.layer_type === 'builtin') { toast.error('内置执行层不可删除'); return; }
    if (!confirm(`确定删除执行层「${l.display_name || l.name}」？`)) return;
    try {
      await client.delete(`/admin/execution-layers/${l.id}`);
      toast.success('已删除');
      load();
    } catch (e: any) { toast.error(e.response?.data?.detail || '删除失败'); }
  };

  const handleTest = async (l: ExecutionLayer) => {
    setTesting(l.id);
    try {
      const { data } = await client.post(`/admin/execution-layers/${l.id}/test`);
      if (data.success) toast.success(`连通正常: ${data.message || ''}`);
      else toast.error(`检测失败: ${data.message || '未知错误'}`);
      load();
    } catch (e: any) { toast.error(e.response?.data?.detail || '测试失败'); }
    finally { setTesting(null); }
  };

  const handleRefreshTools = async (l: ExecutionLayer) => {
    setToolsLoading(l.id);
    setExpanded(l.id);
    try {
      const { data } = await client.get(`/admin/execution-layers/${l.id}/tools`);
      const tools = Array.isArray(data?.tools) ? data.tools : [];
      setLayers(prev => prev.map(x => (x.id === l.id ? { ...x, tools } : x)));
      const src = data?.source === 'cached' ? 'cached' : 'live';
      setToolsSource(prev => ({ ...prev, [l.id]: src }));
      if (src === 'cached') toast.info(`适配器实询失败,已展示缓存目录 (${tools.length})`);
      else toast.success(`已刷新工具目录 (${tools.length})`);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '刷新工具失败');
    } finally { setToolsLoading(null); }
  };

  // allowed_tools chips 候选 = 标准工具目录 ∪ 该层实测工具 ∪ 已选项
  const toolCandidateMap = new Map<string, { name: string; label?: string }>();
  toolCatalog.forEach(t => toolCandidateMap.set(t.name, { name: t.name, label: t.label }));
  (editing?.tools || []).forEach(t => { if (!toolCandidateMap.has(t.name)) toolCandidateMap.set(t.name, { name: t.name }); });
  (form.allowed_tools || []).forEach((n: string) => { if (!toolCandidateMap.has(n)) toolCandidateMap.set(n, { name: n }); });
  const toolCandidates = Array.from(toolCandidateMap.values());

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Terminal className="h-5 w-5" /> 执行层管理
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            配置 AI 任务的执行后端:内置 Agent、本地 CLI(qoder)等
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleDiscover} disabled={discovering}>
            <Radar className="h-4 w-4 mr-1" /> {discovering ? '扫描中...' : '发现本机 CLI'}
          </Button>
          <Button size="sm" onClick={() => { setEditing(null); setForm({ ...EMPTY_FORM }); setFormOpen(!formOpen); }}>
            <Plus className="h-4 w-4 mr-1" /> 新建执行层
          </Button>
        </div>
      </div>

      {/* Discovered CLIs */}
      {discovered.length > 0 && (
        <div className="border border-border rounded-xl p-3 space-y-2">
          <div className="text-sm font-medium">本机发现的 CLI 工具</div>
          {discovered.map(c => (
            <div key={c.name} className="flex items-center justify-between text-sm">
              <div>
                <span className="font-mono">{c.name}</span>
                <span className="ml-2 text-xs text-muted-foreground">{c.path}</span>
                {c.version && <span className="ml-2 text-xs text-muted-foreground">({c.version})</span>}
              </div>
              <Button size="sm" variant="outline" onClick={() => applyDiscovered(c)}>添加为执行层</Button>
            </div>
          ))}
        </div>
      )}

      {/* Form */}
      {formOpen && (
        <div className="border border-border rounded-xl p-4 space-y-3">
          <div className="text-sm font-medium">{editing ? '编辑执行层' : '新建执行层'}</div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>名称(唯一标识)</Label>
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="cli-qoder" />
            </div>
            <div className="space-y-1.5">
              <Label>显示名称</Label>
              <Input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} placeholder="Qoder CLI" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>执行层类型</Label>
              <Select value={form.layer_type} onValueChange={v => setForm({ ...form, layer_type: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="cli">CLI 进程</SelectItem>
                  <SelectItem value="builtin">内置执行层</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>描述</Label>
              <Input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="用于深度数据分析的本地 CLI" />
            </div>
          </div>
          {form.layer_type === 'cli' && (
            <>
              <div className={`grid ${form.cli_name === 'qoder' ? 'grid-cols-3' : 'grid-cols-2'} gap-4`}>
                <div className="space-y-1.5">
                  <Label>CLI 类型</Label>
                  <Select value={form.cli_name} onValueChange={v => { setForm({ ...form, cli_name: v, model: '', mode: 'process' }); loadModels(v); }}>
                    <SelectTrigger><SelectValue placeholder="选择 CLI" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="qoder">Qoder CLI</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {form.cli_name === 'qoder' && (
                  <div className="space-y-1.5">
                    <Label>调用方式</Label>
                    <Select value={form.mode} onValueChange={v => setForm({ ...form, mode: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="process">子进程(命令模板)</SelectItem>
                        <SelectItem value="sdk">SDK(支持 MCP/Agent 注入)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}
                <div className="space-y-1.5">
                  <Label>模型(不选则用 CLI 默认)</Label>
                  <Select value={form.model || 'auto'} onValueChange={v => setForm({ ...form, model: v === 'auto' ? '' : v })}>
                    <SelectTrigger>
                      <SelectValue placeholder={loadingModels ? '加载中...' : 'Auto(默认)'} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">Auto(默认)</SelectItem>
                      {modelOptions.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>可执行文件路径(留空自动查找)</Label>
                  <Input value={form.cli_path} onChange={e => setForm({ ...form, cli_path: e.target.value })} placeholder="/usr/local/bin/qodercli" />
                </div>
                <div className="space-y-1.5">
                  <Label>超时(秒)</Label>
                  <Input type="number" value={form.timeout} onChange={e => setForm({ ...form, timeout: e.target.value })} />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>附加环境变量 (JSON,可选)</Label>
                <Textarea value={form.env_text} onChange={e => setForm({ ...form, env_text: e.target.value })} rows={2} placeholder='{"OPENAI_API_KEY": "..."}' />
              </div>
            </>
          )}
          {/* 权限白名单:可用工具 + 可执行目录(执行层定义级) */}
          <div className="space-y-1.5">
            <Label>可用工具(空=不限制)</Label>
            <div className="flex flex-wrap gap-1.5">
              {toolCandidates.map(t => {
                const active = (form.allowed_tools || []).includes(t.name);
                return (
                  <button
                    key={t.name}
                    type="button"
                    title={t.label || t.name}
                    onClick={() => setForm({
                      ...form,
                      allowed_tools: active
                        ? (form.allowed_tools || []).filter((n: string) => n !== t.name)
                        : [...(form.allowed_tools || []), t.name],
                    })}
                    className={`px-2 py-0.5 rounded-full text-xs border transition-colors ${active ? 'bg-primary text-primary-foreground border-primary' : 'bg-muted text-muted-foreground border-border hover:bg-muted/80'}`}
                  >
                    {t.label || t.name}
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-muted-foreground">候选 = 标准工具目录 ∪ 该执行层实测工具;最终生效 = 层级 ∩ 工作空间绑定</p>
          </div>
          <div className="space-y-1.5">
            <Label>可执行目录(每行一个绝对路径,空=回退工作空间目录)</Label>
            <Textarea
              value={form.dirs_text}
              onChange={e => setForm({ ...form, dirs_text: e.target.value })}
              rows={2}
              placeholder={'/data/workspaces/ws_100\n/mnt/shared'}
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={form.status === 'active'} onCheckedChange={v => setForm({ ...form, status: v ? 'active' : 'inactive' })} />
            <span className="text-sm">启用</span>
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSave}>保存</Button>
            <Button size="sm" variant="ghost" onClick={() => { setFormOpen(false); setEditing(null); }}>取消</Button>
          </div>
        </div>
      )}

      {/* List */}
      <div className="border border-border rounded-xl divide-y divide-border">
        {layers.map(l => {
          const healthy = layerHealthy(l);
          const toolCount = l.tools?.length ?? 0;
          return (
          <div key={l.id} className="p-3 hover:bg-muted/50 transition-colors">
            <div className="flex items-center justify-between gap-2">
              <div className="space-y-0.5 min-w-0 flex-1">
                <div className="font-medium text-sm">
                  {l.display_name || l.name}
                  <span className="ml-2 text-xs text-muted-foreground">({LAYER_TYPE_LABELS[l.layer_type] || l.layer_type})</span>
                  {l.source === 'self' && <Badge variant="secondary" className="ml-2 text-[10px] font-normal">自注册</Badge>}
                  {l.status !== 'active'
                    ? <span className="ml-2 text-xs text-destructive">● 已停用</span>
                    : healthy
                      ? <span className="ml-2 text-xs text-green-600">● 在线</span>
                      : <span className="ml-2 text-xs text-amber-600">● 心跳超时</span>}
                  {l.source !== 'self' && l.status === 'active' && l.last_test_status === 'success' && <span className="ml-2 text-xs text-green-600">✅ 连通</span>}
                  {l.source !== 'self' && l.status === 'active' && l.last_test_status === 'failed' && <span className="ml-2 text-xs text-destructive">❌ 不可用</span>}
                </div>
                <div className="text-xs text-muted-foreground">
                  {l.layer_type === 'cli' && l.config?.cli_name && (
                    <span className="font-mono mr-2">{l.config.cli_path || l.config.cli_name}</span>
                  )}
                  {l.layer_type === 'cli' && l.config?.model && (
                    <Badge variant="outline" className="mr-2">模型: {l.config.model}</Badge>
                  )}
                  {Array.isArray(l.config?.allowed_tools) && l.config.allowed_tools.length > 0 && (
                    <Badge variant="outline" className="mr-2">工具白名单: {l.config.allowed_tools.length}</Badge>
                  )}
                  {Array.isArray(l.config?.allowed_dirs) && l.config.allowed_dirs.length > 0 && (
                    <Badge variant="outline" className="mr-2">目录: {l.config.allowed_dirs.length}</Badge>
                  )}
                  {l.source === 'self' && (
                    <span className="mr-2">心跳: {l.last_heartbeat_at ? relTime(l.last_heartbeat_at) : '从未'}</span>
                  )}
                  {l.description}
                  {l.health_check_at && <span className="ml-2">上次检测: {l.health_check_at}</span>}
                </div>
                {l.capabilities && l.capabilities.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {l.capabilities.map(c => (
                      <Badge key={c} variant="outline" className="text-[10px] px-1.5 py-0 font-normal">{c}</Badge>
                    ))}
                  </div>
                )}
                {l.last_test_message && (
                  <div className="text-xs text-muted-foreground truncate max-w-xl">{l.last_test_message}</div>
                )}
              </div>
              <div className="flex gap-1 shrink-0">
                <Button size="sm" variant="outline" onClick={() => handleTest(l)} disabled={testing === l.id}>
                  {testing === l.id ? '检测中...' : '测试'}
                </Button>
                <Button
                  size="sm"
                  variant={expanded === l.id ? 'secondary' : 'ghost'}
                  onClick={() => setExpanded(expanded === l.id ? null : l.id)}
                  title="工具目录"
                >
                  <Wrench className="h-4 w-4" />
                  {toolCount > 0 && <span className="ml-1 text-xs">{toolCount}</span>}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => handleEdit(l)}><Pencil className="h-4 w-4" /></Button>
                {l.layer_type !== 'builtin' && (
                  <Button size="sm" variant="ghost" onClick={() => handleDelete(l)}><Trash2 className="h-4 w-4" /></Button>
                )}
              </div>
            </div>
            {expanded === l.id && (
              <div className="mt-2 border-t border-border pt-2 text-xs">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-medium text-muted-foreground">
                    工具目录 {toolCount > 0 ? `(${toolCount})` : ''}
                    {toolsSource[l.id] && <span className="ml-2 font-normal">· {toolsSource[l.id] === 'live' ? '实时查询' : '缓存快照'}</span>}
                  </span>
                  <Button size="sm" variant="ghost" onClick={() => handleRefreshTools(l)} disabled={toolsLoading === l.id}>
                    <RefreshCw className={`h-3.5 w-3.5 mr-1 ${toolsLoading === l.id ? 'animate-spin' : ''}`} />
                    {toolsLoading === l.id ? '刷新中...' : '刷新工具'}
                  </Button>
                </div>
                {toolCount > 0 ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
                    {l.tools!.map((t, i) => (
                      <div key={`${t.name}-${i}`} className="flex items-center gap-1.5 min-w-0">
                        <Badge variant="secondary" className="text-[10px] px-1 py-0 font-normal shrink-0">{t.type || 'tool'}</Badge>
                        <span className="font-mono truncate">{t.name}</span>
                        {t.description && <span className="text-muted-foreground truncate">— {t.description}</span>}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-muted-foreground">暂无工具目录,点击「刷新工具」实询适配器</div>
                )}
              </div>
            )}
          </div>
          );
        })}
        {!loading && layers.length === 0 && (
          <div className="text-sm text-muted-foreground text-center py-12">
            暂无执行层,请先执行迁移 SQL 或点击「发现本机 CLI」添加
          </div>
        )}
      </div>
    </div>
  );
}
