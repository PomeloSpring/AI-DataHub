import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Terminal, Radar } from 'lucide-react';
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

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      const { data } = await client.get('/admin/execution-layers/discover');
      const found: DiscoveredCLI[] = Array.isArray(data) ? data : [];
      setDiscovered(found);
      if (found.length === 0) {
        toast.info('未发现已安装的 CLI 工具(opencode / qoder)');
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
      if (['qoder', 'opencode'].includes(form.cli_name) && form.mode === 'sdk') config.mode = 'sdk';
      if (form.env_text) {
        try {
          config.env = JSON.parse(form.env_text);
        } catch { toast.error('环境变量不是合法 JSON'); return; }
      }
    }

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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Terminal className="h-5 w-5" /> 执行层管理
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            配置 AI 任务的执行后端:内置 Agent、本地 CLI(opencode / qoder)等
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
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="cli-opencode" />
            </div>
            <div className="space-y-1.5">
              <Label>显示名称</Label>
              <Input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} placeholder="OpenCode CLI" />
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
              <div className={`grid ${['qoder', 'opencode'].includes(form.cli_name) ? 'grid-cols-3' : 'grid-cols-2'} gap-4`}>
                <div className="space-y-1.5">
                  <Label>CLI 类型</Label>
                  <Select value={form.cli_name} onValueChange={v => { setForm({ ...form, cli_name: v, model: '', mode: 'process' }); loadModels(v); }}>
                    <SelectTrigger><SelectValue placeholder="选择 CLI" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="opencode">OpenCode</SelectItem>
                      <SelectItem value="qoder">Qoder CLI</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {['qoder', 'opencode'].includes(form.cli_name) && (
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
                  {form.cli_name === 'opencode' && (
                    <p className="text-xs text-muted-foreground">
                      候选模型自动来自系统「模型中心」配置,凭据随执行自动注入
                    </p>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>可执行文件路径(留空自动查找)</Label>
                  <Input value={form.cli_path} onChange={e => setForm({ ...form, cli_path: e.target.value })} placeholder="/usr/local/bin/opencode" />
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
        {layers.map(l => (
          <div key={l.id} className="p-3 hover:bg-muted/50 transition-colors">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <div className="font-medium text-sm">
                  {l.display_name || l.name}
                  <span className="ml-2 text-xs text-muted-foreground">({LAYER_TYPE_LABELS[l.layer_type] || l.layer_type})</span>
                  {l.status !== 'active' && <span className="ml-2 text-xs text-destructive">已停用</span>}
                  {l.last_test_status === 'success' && <span className="ml-2 text-xs text-green-600">✅ 连通</span>}
                  {l.last_test_status === 'failed' && <span className="ml-2 text-xs text-destructive">❌ 不可用</span>}
                </div>
                <div className="text-xs text-muted-foreground">
                  {l.layer_type === 'cli' && l.config?.cli_name && (
                    <span className="font-mono mr-2">{l.config.cli_path || l.config.cli_name}</span>
                  )}
                  {l.layer_type === 'cli' && l.config?.model && (
                    <Badge variant="outline" className="mr-2">模型: {l.config.model}</Badge>
                  )}
                  {l.description}
                  {l.health_check_at && <span className="ml-2">上次检测: {l.health_check_at}</span>}
                </div>
                {l.last_test_message && (
                  <div className="text-xs text-muted-foreground truncate max-w-xl">{l.last_test_message}</div>
                )}
              </div>
              <div className="flex gap-1">
                <Button size="sm" variant="outline" onClick={() => handleTest(l)} disabled={testing === l.id}>
                  {testing === l.id ? '检测中...' : '测试'}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => handleEdit(l)}><Pencil className="h-4 w-4" /></Button>
                {l.layer_type !== 'builtin' && (
                  <Button size="sm" variant="ghost" onClick={() => handleDelete(l)}><Trash2 className="h-4 w-4" /></Button>
                )}
              </div>
            </div>
          </div>
        ))}
        {!loading && layers.length === 0 && (
          <div className="text-sm text-muted-foreground text-center py-12">
            暂无执行层,请先执行迁移 SQL 或点击「发现本机 CLI」添加
          </div>
        )}
      </div>
    </div>
  );
}
