import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import {
  Plus, Pencil, Trash2, Server, Wifi, WifiOff, Star, Loader2, Search,
  Monitor, Globe, Cloud, ChevronDown, ChevronUp, Terminal, CheckCircle, XCircle, Clock,
} from 'lucide-react';
import client from '@/api/client';

// ── Types ──────────────────────────────────────────────────────────

interface SandboxEnvironment {
  id: number;
  name: string;
  sandbox_type: string;
  display_name: string;
  description: string;
  config: Record<string, any>;
  resource_info: Record<string, any>;
  status: string;
  is_default: boolean;
  is_active: boolean;
  last_heartbeat: string | null;
  created_at: string;
  updated_at: string;
}

interface SandboxType {
  key: string;
  label: string;
  description: string;
  config_schema: Record<string, ConfigField>;
}

interface ConfigField {
  type: string;
  label: string;
  default?: any;
  required?: boolean;
  placeholder?: string;
  options?: string[];
  show_if?: Record<string, any>;
}

interface ExecutionLog {
  id: number;
  sandbox_id: number;
  sandbox_name: string;
  sandbox_type: string;
  code: string;
  requirements: string[];
  success: boolean;
  stdout: string;
  stderr: string;
  result: string;
  error: string;
  elapsed_ms: number;
  conversation_id: number;
  user_id: number;
  created_at: string;
}

// ── Type Icons ─────────────────────────────────────────────────────

const TYPE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  local: Monitor,
  ssh: Globe,
  fc: Cloud,
};

const STATUS_COLORS: Record<string, string> = {
  ready: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  busy: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  offline: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400',
  error: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  unknown: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400',
};

const STATUS_LABELS: Record<string, string> = {
  ready: '就绪',
  busy: '忙碌',
  offline: '离线',
  error: '异常',
  unknown: '未知',
};

// ── Dynamic Config Form ────────────────────────────────────────────

function ConfigForm({
  sandboxType,
  configSchema,
  values,
  onChange,
}: {
  sandboxType: string;
  configSchema: Record<string, ConfigField>;
  values: Record<string, any>;
  onChange: (values: Record<string, any>) => void;
}) {
  const handleChange = (key: string, value: any) => {
    onChange({ ...values, [key]: value });
  };

  // Check if a field should be visible based on show_if conditions
  const isVisible = (field: ConfigField): boolean => {
    if (!field.show_if) return true;
    return Object.entries(field.show_if).every(([k, v]) => values[k] === v);
  };

  return (
    <div className="space-y-3">
      {Object.entries(configSchema).map(([key, field]) => {
        if (!isVisible(field)) return null;
        const value = values[key] ?? field.default ?? '';

        return (
          <div key={key} className="space-y-1.5">
            <Label className="flex items-center gap-1">
              {field.label}
              {field.required && <span className="text-red-500">*</span>}
            </Label>
            {field.type === 'boolean' ? (
              <div className="flex items-center gap-2">
                <Switch
                  checked={!!value}
                  onCheckedChange={(checked) => handleChange(key, checked)}
                />
                <span className="text-sm text-muted-foreground">{value ? '是' : '否'}</span>
              </div>
            ) : field.type === 'select' ? (
              <Select value={String(value)} onValueChange={(v) => handleChange(key, v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(field.options || []).map((opt) => (
                    <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : field.type === 'password' ? (
              <Input
                type="password"
                value={String(value)}
                onChange={(e) => handleChange(key, e.target.value)}
                placeholder={field.placeholder}
              />
            ) : field.type === 'number' ? (
              <Input
                type="number"
                value={String(value)}
                onChange={(e) => handleChange(key, Number(e.target.value))}
                placeholder={field.placeholder}
              />
            ) : (
              <Input
                value={String(value)}
                onChange={(e) => handleChange(key, e.target.value)}
                placeholder={field.placeholder}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Resource Info Display ──────────────────────────────────────────

function ResourceInfo({ info }: { info: Record<string, any> }) {
  if (!info || Object.keys(info).length === 0) return null;
  const parts: string[] = [];
  if (info.cpu) parts.push(`${info.cpu} CPU`);
  if (info.memory_gb) parts.push(`${info.memory_gb} GB`);
  if (info.gpu) parts.push(`GPU: ${info.gpu}`);
  if (info.gpu_count) parts.push(`×${info.gpu_count}`);
  if (info.host) parts.push(`@ ${info.host}`);
  if (parts.length === 0) return null;
  return <span className="text-xs text-muted-foreground">{parts.join(' · ')}</span>;
}

// ── Main Component ─────────────────────────────────────────────────

export default function SandboxManagement() {
  const [sandboxes, setSandboxes] = useState<SandboxEnvironment[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('');
  const [loading, setLoading] = useState(true);
  const [sandboxTypes, setSandboxTypes] = useState<SandboxType[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<SandboxEnvironment | null>(null);
  const [form, setForm] = useState<any>({ sandbox_type: 'local', config: {}, is_active: true, is_default: false });
  const [testing, setTesting] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [configExpanded, setConfigExpanded] = useState(true);
  const [deleteConfirm, setDeleteConfirm] = useState<SandboxEnvironment | null>(null);
  const [activeTab, setActiveTab] = useState('sandboxes');
  const [logSandboxFilter, setLogSandboxFilter] = useState<number>(0);
  const [logSandboxName, setLogSandboxName] = useState<string>('');

  // Load sandbox types
  useEffect(() => {
    client.get('/sandbox/types').then(({ data }) => setSandboxTypes(Array.isArray(data) ? data : [])).catch(() => {});
  }, []);

  // Load sandboxes
  const load = async (p?: number) => {
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size: 50 };
      if (search) params.search = search;
      if (filterType) params.sandbox_type = filterType;
      const { data } = await client.get('/sandbox/', { params });
      setSandboxes(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error('加载沙箱列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  // Get config schema for current type
  const getConfigSchema = (type: string): Record<string, ConfigField> => {
    const t = sandboxTypes.find((st) => st.key === type);
    return t?.config_schema || {};
  };

  // Open create dialog
  const openCreate = () => {
    setEditing(null);
    const defaultConfig: Record<string, any> = {};
    const schema = getConfigSchema('local');
    Object.entries(schema).forEach(([key, field]) => {
      if (field.default !== undefined) defaultConfig[key] = field.default;
    });
    setForm({ sandbox_type: 'local', config: defaultConfig, is_active: true, is_default: false });
    setConfigExpanded(true);
    setFormOpen(true);
  };

  // Open edit dialog
  const openEdit = (sb: SandboxEnvironment) => {
    setEditing(sb);
    setForm({
      sandbox_type: sb.sandbox_type,
      name: sb.name,
      display_name: sb.display_name,
      description: sb.description,
      config: { ...sb.config },
      is_active: sb.is_active,
      is_default: sb.is_default,
    });
    setConfigExpanded(true);
    setFormOpen(true);
  };

  // Handle type change in form
  const handleTypeChange = (type: string) => {
    const defaultConfig: Record<string, any> = {};
    const schema = getConfigSchema(type);
    Object.entries(schema).forEach(([key, field]) => {
      if (field.default !== undefined) defaultConfig[key] = field.default;
    });
    setForm({ ...form, sandbox_type: type, config: defaultConfig });
  };

  // Save sandbox
  const handleSave = async () => {
    if (!form.name?.trim()) {
      toast.error('请输入沙箱名称');
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await client.put(`/sandbox/${editing.id}`, {
          display_name: form.display_name,
          description: form.description,
          config: form.config,
          is_active: form.is_active,
          is_default: form.is_default,
        });
        toast.success('已更新');
      } else {
        await client.post('/sandbox/', {
          name: form.name,
          sandbox_type: form.sandbox_type,
          display_name: form.display_name || '',
          description: form.description || '',
          config: form.config || {},
          is_active: form.is_active,
          is_default: form.is_default,
        });
        toast.success('已创建');
      }
      setFormOpen(false);
      setEditing(null);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  // Delete sandbox
  const handleDelete = async (sb: SandboxEnvironment) => {
    try {
      await client.delete(`/sandbox/${sb.id}`);
      toast.success('已删除');
      setDeleteConfirm(null);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  // Test connection
  const handleTest = async (id: number) => {
    setTesting(id);
    try {
      const { data } = await client.post(`/sandbox/${id}/test`);
      if (data.success) {
        toast.success(data.message);
      } else {
        toast.error(data.message);
      }
      load();
    } catch {
      toast.error('测试失败');
    } finally {
      setTesting(null);
    }
  };

  // Set as default
  const handleSetDefault = async (id: number) => {
    try {
      await client.put(`/sandbox/${id}/default`);
      toast.success('已设为默认');
      load();
    } catch {
      toast.error('设置失败');
    }
  };

  const currentSchema = getConfigSchema(form.sandbox_type);

  // ── Execution Logs State ──────────────────────────────────────
  const [logs, setLogs] = useState<ExecutionLog[]>([]);
  const [logsTotal, setLogsTotal] = useState(0);
  const [logsPage, setLogsPage] = useState(1);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logDetail, setLogDetail] = useState<ExecutionLog | null>(null);

  const loadLogs = async (p?: number, sandboxId?: number) => {
    setLogsLoading(true);
    try {
      const params: any = { page: p ?? logsPage, size: 50 };
      const sid = sandboxId ?? logSandboxFilter;
      if (sid) params.sandbox_id = sid;
      const { data } = await client.get('/sandbox/logs', { params });
      setLogs(data.items || []);
      setLogsTotal(data.total || 0);
    } catch {
      toast.error('加载执行日志失败');
    } finally {
      setLogsLoading(false);
    }
  };

  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6">沙箱环境</h1>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="mb-4">
          <TabsTrigger value="sandboxes">
            <Server className="h-4 w-4 mr-1" /> 沙箱管理
          </TabsTrigger>
          <TabsTrigger value="logs" onClick={() => loadLogs()}>
            <Terminal className="h-4 w-4 mr-1" /> 执行日志
          </TabsTrigger>
        </TabsList>

        <TabsContent value="sandboxes">
          {/* Toolbar */}
          <div className="flex items-center justify-between mb-4 gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="搜索名称..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && load(1)}
              className="pl-9 w-[200px]"
            />
          </div>
          <Select value={filterType || 'all'} onValueChange={(v) => { setFilterType(v === 'all' ? '' : v); }}>
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="全部类型" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部类型</SelectItem>
              {sandboxTypes.map((t) => (
                <SelectItem key={t.key} value={t.key}>{t.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={() => load(1)}>筛选</Button>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1" /> 添加沙箱
        </Button>
      </div>

      {/* List */}
      <div className="rounded-lg border bg-card">
        {loading ? (
          <div className="flex items-center justify-center py-16 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中...
          </div>
        ) : sandboxes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
            <Server className="h-12 w-12 mb-3 opacity-30" />
            <p className="text-sm">暂无沙箱环境</p>
            <p className="text-xs mt-1">点击「添加沙箱」创建第一个执行环境</p>
          </div>
        ) : (
          <div className="overflow-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">名称</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">类型</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">状态</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">资源</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">默认</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">启用</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">最后测试</th>
                  <th className="h-10 px-4 text-right text-sm font-medium text-muted-foreground">操作</th>
                </tr>
              </thead>
              <tbody>
                {sandboxes.map((sb) => {
                  const TypeIcon = TYPE_ICONS[sb.sandbox_type] || Server;
                  return (
                    <tr key={sb.id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <TypeIcon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                          <div>
                            <div className="font-medium text-sm">{sb.display_name || sb.name}</div>
                            {sb.display_name && (
                              <div className="text-xs text-muted-foreground">{sb.name}</div>
                            )}
                            {sb.description && (
                              <div className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{sb.description}</div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="outline" className="text-xs">
                          {sandboxTypes.find((t) => t.key === sb.sandbox_type)?.label || sb.sandbox_type}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="secondary" className={`text-xs ${STATUS_COLORS[sb.status] || STATUS_COLORS.unknown}`}>
                          {sb.status === 'ready' && <Wifi className="h-3 w-3 mr-1" />}
                          {sb.status === 'error' && <WifiOff className="h-3 w-3 mr-1" />}
                          {STATUS_LABELS[sb.status] || sb.status}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        <ResourceInfo info={sb.resource_info} />
                      </td>
                      <td className="px-4 py-3">
                        {sb.is_default && (
                          <Star className="h-4 w-4 text-yellow-500 fill-yellow-500" />
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={sb.is_active ? 'default' : 'secondary'} className="text-xs">
                          {sb.is_active ? '启用' : '禁用'}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {sb.last_heartbeat || '-'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleTest(sb.id)}
                            disabled={testing === sb.id}
                            title="测试连接"
                          >
                            {testing === sb.id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Wifi className="h-4 w-4" />
                            )}
                          </Button>
                          {!sb.is_default && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleSetDefault(sb.id)}
                              title="设为默认"
                            >
                              <Star className="h-4 w-4" />
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => openEdit(sb)}
                            title="编辑"
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setLogSandboxFilter(sb.id);
                              setLogSandboxName(sb.display_name || sb.name);
                              setActiveTab('logs');
                              loadLogs(1, sb.id);
                            }}
                            title="执行日志"
                          >
                            <Terminal className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setDeleteConfirm(sb)}
                            title="删除"
                            className="text-destructive hover:text-destructive"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {total > 50 && (
          <div className="flex items-center justify-between px-4 py-3 border-t">
            <span className="text-sm text-muted-foreground">共 {total} 项</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(page - 1); load(page - 1); }}>
                上一页
              </Button>
              <span className="text-sm">{page}</span>
              <Button variant="outline" size="sm" disabled={page * 50 >= total} onClick={() => { setPage(page + 1); load(page + 1); }}>
                下一页
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Create / Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-[600px] max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑沙箱' : '添加沙箱'}</DialogTitle>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {/* Basic Info */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>
                  名称 <span className="text-red-500">*</span>
                </Label>
                <Input
                  value={form.name || ''}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="如 gpu-server, local-docker"
                  disabled={!!editing}
                />
                <p className="text-xs text-muted-foreground">唯一标识，创建后不可修改</p>
              </div>
              <div className="space-y-1.5">
                <Label>沙箱类型</Label>
                <Select
                  value={form.sandbox_type}
                  onValueChange={handleTypeChange}
                  disabled={!!editing}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {sandboxTypes.map((t) => (
                      <SelectItem key={t.key} value={t.key}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>显示名称</Label>
              <Input
                value={form.display_name || ''}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder="如 GPU 计算服务器、本地开发沙箱"
              />
            </div>

            <div className="space-y-1.5">
              <Label>描述</Label>
              <Textarea
                value={form.description || ''}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                rows={2}
                placeholder="沙箱用途说明"
              />
            </div>

            {/* Switches */}
            <div className="flex items-center gap-6">
              <div className="flex items-center gap-2">
                <Switch
                  checked={!!form.is_active}
                  onCheckedChange={(checked) => setForm({ ...form, is_active: checked })}
                />
                <Label>启用</Label>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={!!form.is_default}
                  onCheckedChange={(checked) => setForm({ ...form, is_default: checked })}
                />
                <Label>设为默认</Label>
              </div>
            </div>

            {/* Config Section */}
            <div className="border rounded-lg">
              <button
                type="button"
                className="flex items-center justify-between w-full px-4 py-3 text-sm font-medium hover:bg-muted/50 transition-colors"
                onClick={() => setConfigExpanded(!configExpanded)}
              >
                <span>
                  连接配置
                  <span className="text-muted-foreground font-normal ml-2">
                    ({sandboxTypes.find((t) => t.key === form.sandbox_type)?.label || form.sandbox_type})
                  </span>
                </span>
                {configExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </button>
              {configExpanded && (
                <div className="px-4 pb-4 border-t">
                  <div className="pt-3">
                    <ConfigForm
                      sandboxType={form.sandbox_type}
                      configSchema={currentSchema}
                      values={form.config || {}}
                      onChange={(config) => setForm({ ...form, config })}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
              {editing ? '保存' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={!!deleteConfirm} onOpenChange={() => setDeleteConfirm(null)}>
        <DialogContent className="max-w-[400px]">
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要删除沙箱 <strong>{deleteConfirm?.display_name || deleteConfirm?.name}</strong> 吗？此操作不可撤销。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteConfirm(null)}>取消</Button>
            <Button variant="destructive" onClick={() => deleteConfirm && handleDelete(deleteConfirm)}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

        </TabsContent>

        {/* ── Execution Logs Tab ──────────────────────────────────── */}
        <TabsContent value="logs">
          {/* Filter header */}
          <div className="flex items-center justify-between mb-4 gap-4 flex-wrap">
            <div className="flex items-center gap-2">
              {logSandboxFilter ? (
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">
                    沙箱: {logSandboxName}
                  </Badge>
                  <Button variant="ghost" size="sm" onClick={() => {
                    setLogSandboxFilter(0);
                    setLogSandboxName('');
                    loadLogs(1, 0);
                  }}>
                    清除筛选
                  </Button>
                </div>
              ) : (
                <span className="text-sm text-muted-foreground">全部沙箱执行记录</span>
              )}
            </div>
            <Button variant="outline" size="sm" onClick={() => loadLogs()}>
              刷新
            </Button>
          </div>
          <div className="rounded-lg border bg-card">
            {logsLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中...
              </div>
            ) : logs.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Terminal className="h-12 w-12 mb-3 opacity-30" />
                <p className="text-sm">暂无执行日志</p>
              </div>
            ) : (
              <div className="overflow-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b bg-muted/50">
                      <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">沙箱</th>
                      <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">状态</th>
                      <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">耗时</th>
                      <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">代码预览</th>
                      <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">时间</th>
                      <th className="h-10 px-4 text-right text-sm font-medium text-muted-foreground">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {logs.map((log) => (
                      <tr key={log.id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                        <td className="px-4 py-3">
                          <div className="text-sm font-medium">{log.sandbox_name}</div>
                          <div className="text-xs text-muted-foreground">{log.sandbox_type}</div>
                        </td>
                        <td className="px-4 py-3">
                          {log.success ? (
                            <Badge variant="default" className="text-xs bg-green-500 hover:bg-green-600">
                              <CheckCircle className="h-3 w-3 mr-1" /> 成功
                            </Badge>
                          ) : (
                            <Badge variant="destructive" className="text-xs">
                              <XCircle className="h-3 w-3 mr-1" /> 失败
                            </Badge>
                          )}
                        </td>
                        <td className="px-4 py-3 text-sm text-muted-foreground">
                          <Clock className="h-3 w-3 inline mr-1" />
                          {log.elapsed_ms}ms
                        </td>
                        <td className="px-4 py-3">
                          <code className="text-xs bg-muted/30 px-2 py-1 rounded max-w-[300px] truncate inline-block">
                            {log.code.slice(0, 80)}{log.code.length > 80 ? '...' : ''}
                          </code>
                        </td>
                        <td className="px-4 py-3 text-xs text-muted-foreground">
                          {log.created_at}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Button variant="ghost" size="sm" onClick={() => setLogDetail(log)}>
                            详情
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {logsTotal > 50 && (
              <div className="flex items-center justify-between px-4 py-3 border-t">
                <span className="text-sm text-muted-foreground">共 {logsTotal} 条</span>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" disabled={logsPage <= 1} onClick={() => { setLogsPage(logsPage - 1); loadLogs(logsPage - 1); }}>
                    上一页
                  </Button>
                  <span className="text-sm">{logsPage}</span>
                  <Button variant="outline" size="sm" disabled={logsPage * 50 >= logsTotal} onClick={() => { setLogsPage(logsPage + 1); loadLogs(logsPage + 1); }}>
                    下一页
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* Log Detail Dialog */}
          <Dialog open={!!logDetail} onOpenChange={() => setLogDetail(null)}>
            <DialogContent className="max-w-[700px] max-h-[85vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>执行详情</DialogTitle>
              </DialogHeader>
              {logDetail && (
                <div className="space-y-4">
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <span className="text-muted-foreground">沙箱:</span>
                      <span className="ml-2 font-medium">{logDetail.sandbox_name}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">类型:</span>
                      <span className="ml-2">{logDetail.sandbox_type}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">耗时:</span>
                      <span className="ml-2">{logDetail.elapsed_ms}ms</span>
                    </div>
                  </div>

                  <div>
                    <div className="text-sm font-medium mb-2">执行代码</div>
                    <pre className="text-xs bg-[#1e1e1e] text-[#d4d4d4] rounded p-3 overflow-x-auto max-h-[200px]">
                      <code>{logDetail.code}</code>
                    </pre>
                  </div>

                  {logDetail.requirements && logDetail.requirements.length > 0 && (
                    <div>
                      <div className="text-sm font-medium mb-2">依赖包</div>
                      <div className="flex gap-2">
                        {logDetail.requirements.map((pkg, i) => (
                          <Badge key={i} variant="outline">{pkg}</Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {logDetail.stdout && (
                    <div>
                      <div className="text-sm font-medium mb-2">stdout</div>
                      <pre className="text-xs bg-black/10 rounded p-3 overflow-x-auto max-h-[200px] whitespace-pre-wrap">
                        {logDetail.stdout}
                      </pre>
                    </div>
                  )}

                  {logDetail.stderr && (
                    <div>
                      <div className="text-sm font-medium mb-2 text-red-500">stderr</div>
                      <pre className="text-xs bg-red-500/10 rounded p-3 overflow-x-auto max-h-[200px] whitespace-pre-wrap text-red-400">
                        {logDetail.stderr}
                      </pre>
                    </div>
                  )}

                  {logDetail.error && (
                    <div>
                      <div className="text-sm font-medium mb-2 text-red-500">错误</div>
                      <pre className="text-xs bg-red-500/10 rounded p-3 overflow-x-auto max-h-[200px] whitespace-pre-wrap text-red-400">
                        {logDetail.error}
                      </pre>
                    </div>
                  )}

                  {logDetail.result && (
                    <div>
                      <div className="text-sm font-medium mb-2">返回值</div>
                      <pre className="text-xs bg-muted/30 rounded p-3 overflow-x-auto whitespace-pre-wrap">
                        {logDetail.result}
                      </pre>
                    </div>
                  )}

                  {/* Re-execute button */}
                  <div className="flex justify-end gap-2 pt-4 border-t">
                    <Button variant="outline" onClick={() => {
                      // Copy code to clipboard
                      navigator.clipboard.writeText(logDetail.code);
                      toast.success('代码已复制');
                    }}>
                      复制代码
                    </Button>
                    <Button onClick={async () => {
                      // Re-execute the code
                      try {
                        toast.info('正在重新执行...');
                        const { data } = await client.post(`/sandbox/${logDetail.sandbox_id}/execute`, {
                          code: logDetail.code,
                          requirements: logDetail.requirements,
                        });
                        if (data.success) {
                          toast.success(`执行成功 (${data.elapsed_ms}ms)`);
                        } else {
                          toast.error(`执行失败: ${data.error || '未知错误'}`);
                        }
                        // Refresh logs
                        loadLogs();
                        setLogDetail(null);
                      } catch (e: any) {
                        toast.error(e.response?.data?.detail || '执行失败');
                      }
                    }}>
                      重新执行
                    </Button>
                  </div>
                </div>
              )}
            </DialogContent>
          </Dialog>
        </TabsContent>
      </Tabs>
    </div>
  );
}
