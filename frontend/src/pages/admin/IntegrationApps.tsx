import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { Plus, Copy, RefreshCw, Edit, Key, Shield, Trash2, Check, X, ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import client from '../../api/client';

interface Application {
  id: number;
  name: string;
  status: string;
  enable_chat: boolean;
  allowed_dashboards: string | null;
  allowed_tables: string | null;
  rate_limit: number;
  description: string;
  last_used_at: string | null;
  created_at: string;
}

interface DashboardOption {
  id: number;
  name: string;
}

// ── Multi-Select Component ────────────────────────────────────────

function MultiSelect({
  options, selected, onChange, placeholder, labelKey = 'label', valueKey = 'value',
}: {
  options: { label: string; value: string | number }[];
  selected: (string | number)[];
  onChange: (vals: (string | number)[]) => void;
  placeholder?: string;
  labelKey?: string;
  valueKey?: string;
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('');

  const filtered = options.filter(o =>
    o.label.toLowerCase().includes(filter.toLowerCase())
  );

  const toggle = (val: string | number) => {
    if (selected.includes(val)) {
      onChange(selected.filter(v => v !== val));
    } else {
      onChange([...selected, val]);
    }
  };

  const selectAll = () => onChange(filtered.map(o => o.value));
  const clearAll = () => onChange([]);

  return (
    <div className="relative">
      <div
        className="flex flex-wrap gap-1 min-h-[36px] p-1.5 border rounded-md cursor-pointer bg-background hover:border-primary"
        onClick={() => setOpen(!open)}
      >
        {selected.length === 0 && (
          <span className="text-muted-foreground text-sm py-0.5">{placeholder || '请选择'}</span>
        )}
        {selected.map(val => {
          const opt = options.find(o => o.value === val);
          return (
            <Badge key={val} variant="secondary" className="text-xs gap-1">
              {opt?.label || val}
              <X className="h-3 w-3 cursor-pointer" onClick={(e) => { e.stopPropagation(); toggle(val); }} />
            </Badge>
          );
        })}
      </div>
      {open && (
        <div className="absolute z-50 mt-1 w-full border rounded-md bg-popover shadow-md">
          <div className="p-2 border-b">
            <Input
              placeholder="搜索..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="h-7 text-xs"
              onClick={(e) => e.stopPropagation()}
            />
          </div>
          <div className="flex gap-1 px-2 py-1 border-b">
            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={selectAll}>全选</Button>
            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={clearAll}>清空</Button>
          </div>
          <ScrollArea className="max-h-48">
            {filtered.map(opt => (
              <div
                key={opt.value}
                className="flex items-center gap-2 px-2 py-1.5 hover:bg-accent cursor-pointer text-sm"
                onClick={() => toggle(opt.value)}
              >
                <div className={`w-4 h-4 border rounded flex items-center justify-center ${selected.includes(opt.value) ? 'bg-primary border-primary' : 'border-muted-foreground'}`}>
                  {selected.includes(opt.value) && <Check className="h-3 w-3 text-primary-foreground" />}
                </div>
                {opt.label}
              </div>
            ))}
            {filtered.length === 0 && (
              <div className="px-2 py-3 text-sm text-muted-foreground text-center">无匹配项</div>
            )}
          </ScrollArea>
        </div>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────

export default function IntegrationApps() {
  const [apps, setApps] = useState<Application[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingApp, setEditingApp] = useState<Application | null>(null);
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [newKey, setNewKey] = useState('');
  // Store API keys per app, persisted in localStorage
  const [appKeys, setAppKeysRaw] = useState<Record<number, string>>(() => {
    try { return JSON.parse(localStorage.getItem('chatbi_app_keys') || '{}'); } catch { return {}; }
  });
  const setAppKeys = (updater: Record<number, string> | ((prev: Record<number, string>) => Record<number, string>)) => {
    setAppKeysRaw(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      localStorage.setItem('chatbi_app_keys', JSON.stringify(next));
      return next;
    });
  };

  // Selector data
  const [allDashboards, setAllDashboards] = useState<DashboardOption[]>([]);
  const [allTables, setAllTables] = useState<string[]>([]);
  const [selectedDashboards, setSelectedDashboards] = useState<(string | number)[]>([]);
  const [selectedTables, setSelectedTables] = useState<(string | number)[]>([]);

  const [formValues, setFormValues] = useState({
    name: '', description: '', rate_limit: 60, enable_chat: true,
  });

  const load = useCallback(async (p?: number) => {
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size: 50 };
      if (search) params.search = search;
      const { data } = await client.get('/embed/admin/applications', { params });
      setApps(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error('加载应用列表失败');
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  const loadSelectorData = async () => {
    try {
      const [dsRes, tblRes] = await Promise.all([
        client.get('/embed/admin/dashboards-for-select'),
        client.get('/embed/admin/tables-for-select'),
      ]);
      setAllDashboards(dsRes.data.items || []);
      setAllTables(tblRes.data.items || []);
    } catch {
      // silent
    }
  };

  useEffect(() => { load(); loadSelectorData(); }, []);

  const openCreate = () => {
    setEditingApp(null);
    setFormValues({ name: '', description: '', rate_limit: 60, enable_chat: true });
    setSelectedDashboards([]);
    setSelectedTables([]);
    setModalOpen(true);
  };

  const openEdit = (app: Application) => {
    setEditingApp(app);
    setFormValues({
      name: app.name,
      description: app.description || '',
      rate_limit: app.rate_limit,
      enable_chat: app.enable_chat,
    });
    // Parse existing selections
    try {
      setSelectedDashboards(app.allowed_dashboards ? JSON.parse(app.allowed_dashboards) : []);
    } catch { setSelectedDashboards([]); }
    try {
      setSelectedTables(app.allowed_tables ? JSON.parse(app.allowed_tables) : []);
    } catch { setSelectedTables([]); }
    setModalOpen(true);
  };

  const handleSave = async () => {
    if (!formValues.name.trim()) {
      toast.error('应用名称不能为空');
      return;
    }
    try {
      const payload: any = {
        name: formValues.name,
        description: formValues.description,
        rate_limit: formValues.rate_limit,
        enable_chat: formValues.enable_chat,
      };
      if (selectedDashboards.length > 0) {
        payload.allowed_dashboards = selectedDashboards.map(Number);
      }
      if (selectedTables.length > 0) {
        payload.allowed_tables = selectedTables.map(String);
      }

      if (editingApp) {
        await client.put(`/embed/admin/applications/${editingApp.id}`, payload);
        toast.success('更新成功');
      } else {
        const { data } = await client.post('/embed/admin/applications', payload);
        setNewKey(data.api_key);
        setKeyModalOpen(true);
        setAppKeys(prev => ({ ...prev, [data.id]: data.api_key }));
        toast.success('创建成功，请保存API Key');
      }
      setModalOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '操作失败');
    }
  };

  const handleDelete = async (app: Application) => {
    if (!confirm(`确定要删除应用"${app.name}"吗？此操作不可撤销。`)) return;
    try {
      await client.delete(`/embed/admin/applications/${app.id}`);
      toast.success('删除成功');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const handleRotateKey = async (appId: number) => {
    if (!confirm('确定要轮换API Key吗？旧Key将立即失效。')) return;
    try {
      const { data } = await client.post(`/embed/admin/applications/${appId}/rotate-key`);
      setNewKey(data.api_key);
      setKeyModalOpen(true);
      setAppKeys(prev => ({ ...prev, [appId]: data.api_key }));
      toast.success('Key已轮换，请保存新Key');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '轮换失败');
    }
  };

  const handlePreview = (app: Application) => {
    const apiKey = appKeys[app.id] || '';
    const backendHost = window.location.hostname === 'localhost'
      ? 'http://localhost:8000'
      : window.location.origin;
    const params = new URLSearchParams({
      host: backendHost,
      userId: 'preview-user',
    });
    if (apiKey) params.set('apiKey', apiKey);
    window.open(`/test/index.html?${params.toString()}`, '_blank');
    if (!apiKey) {
      toast.warning('未找到该应用的API Key，请先在创建时保存，或重新轮换Key');
    }
  };

  const handleToggleStatus = async (app: Application) => {
    const newStatus = app.status === 'active' ? 'disabled' : 'active';
    try {
      await client.put(`/embed/admin/applications/${app.id}`, { status: newStatus });
      toast.success(newStatus === 'active' ? '已启用' : '已禁用');
      load();
    } catch {
      toast.error('操作失败');
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('已复制到剪贴板');
  };

  const maskKey = (appId: number) => `ck_****${String(appId).slice(-4)}`;

  const dashboardOptions = allDashboards.map(d => ({ label: d.name, value: d.id }));
  const tableOptions = allTables.map(t => ({ label: t, value: t }));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Input
            placeholder="搜索应用..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && load(1)}
            className="w-64"
          />
          <Button variant="outline" size="sm" onClick={() => load(1)}>
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-2" />新建应用
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : (
        <div className="border rounded-lg">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="p-3 text-left">应用名称</th>
                <th className="p-3 text-left">状态</th>
                <th className="p-3 text-left">API Key</th>
                <th className="p-3 text-left">功能</th>
                <th className="p-3 text-left">可访问仪表盘</th>
                <th className="p-3 text-left">频率限制</th>
                <th className="p-3 text-left">操作</th>
              </tr>
            </thead>
            <tbody>
              {apps.map((app) => {
                let dashIds: number[] = [];
                let tblNames: string[] = [];
                try { dashIds = app.allowed_dashboards ? JSON.parse(app.allowed_dashboards) : []; } catch {}
                try { tblNames = app.allowed_tables ? JSON.parse(app.allowed_tables) : []; } catch {}

                return (
                  <tr key={app.id} className="border-b hover:bg-muted/30">
                    <td className="p-3">
                      <div className="font-medium">{app.name}</div>
                      {app.description && <div className="text-xs text-muted-foreground">{app.description}</div>}
                    </td>
                    <td className="p-3">
                      <Badge variant={app.status === 'active' ? 'default' : 'secondary'}>
                        {app.status === 'active' ? '启用' : '禁用'}
                      </Badge>
                    </td>
                    <td className="p-3">
                      <code className="text-xs bg-muted px-2 py-1 rounded">{maskKey(app.id)}</code>
                    </td>
                    <td className="p-3 text-xs">
                      <div className="flex flex-wrap gap-1">
                        {app.enable_chat && <Badge variant="default">Chat分析</Badge>}
                        {dashIds.length > 0 && <Badge variant="secondary">Dashboard</Badge>}
                        {!app.enable_chat && dashIds.length === 0 && <Badge variant="outline">未配置</Badge>}
                      </div>
                    </td>
                    <td className="p-3 text-xs">
                      {dashIds.length === 0
                        ? <Badge variant="outline">全部</Badge>
                        : dashIds.map(id => {
                            const d = allDashboards.find(x => x.id === id);
                            return <Badge key={id} variant="secondary" className="mr-1 mb-0.5">{d?.name || id}</Badge>;
                          })
                      }
                    </td>
                    <td className="p-3">{app.rate_limit}次/分</td>
                    <td className="p-3">
                      <div className="flex items-center gap-1">
                        <Button variant="ghost" size="sm" onClick={() => openEdit(app)} title="编辑">
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleRotateKey(app.id)} title="轮换Key">
                          <Key className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleToggleStatus(app)} title={app.status === 'active' ? '禁用' : '启用'}>
                          <Shield className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handlePreview(app)} title="预览集成效果">
                          <ExternalLink className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleDelete(app)} title="删除" className="text-destructive hover:text-destructive">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {apps.length === 0 && (
                <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">暂无应用</td></tr>

              )}
            </tbody>
          </table>
        </div>
      )}

      {total > 50 && (
        <div className="flex justify-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(p => p - 1); load(page - 1); }}>上一页</Button>
          <span className="py-1 px-3 text-sm">第 {page} 页</span>
          <Button variant="outline" size="sm" disabled={page * 50 >= total} onClick={() => { setPage(p => p + 1); load(page + 1); }}>下一页</Button>
        </div>
      )}

      {/* Create/Edit Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingApp ? '编辑应用' : '新建应用'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div>
              <Label>应用名称 *</Label>
              <Input value={formValues.name} onChange={(e) => setFormValues(v => ({ ...v, name: e.target.value }))} placeholder="如：OA系统BI模块" />
            </div>
            <div>
              <Label>描述</Label>
              <Input value={formValues.description} onChange={(e) => setFormValues(v => ({ ...v, description: e.target.value }))} placeholder="应用用途说明" />
            </div>
            <div className="flex items-center justify-between">
              <Label>启用 Chat 数据分析</Label>
              <Switch
                checked={formValues.enable_chat}
                onCheckedChange={(checked) => setFormValues(v => ({ ...v, enable_chat: checked }))}
              />
            </div>
            <div>
              <Label>频率限制（次/分钟）</Label>
              <Input type="number" value={formValues.rate_limit} onChange={(e) => setFormValues(v => ({ ...v, rate_limit: Number(e.target.value) }))} />
            </div>
            <div>
              <Label>可访问仪表盘（留空=全部）</Label>
              <MultiSelect
                options={dashboardOptions}
                selected={selectedDashboards}
                onChange={setSelectedDashboards}
                placeholder="全部仪表盘"
              />
            </div>
            <div>
              <Label>可查询数据表（留空=不限制）</Label>
              <MultiSelect
                options={tableOptions}
                selected={selectedTables}
                onChange={setSelectedTables}
                placeholder="不限制"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleSave}>{editingApp ? '保存' : '创建'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* API Key Display Modal */}
      <Dialog open={keyModalOpen} onOpenChange={setKeyModalOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>API Key</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="p-4 bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-200 dark:border-yellow-800 rounded-lg">
              <p className="text-sm text-yellow-800 dark:text-yellow-200 mb-2">
                ⚠️ 请立即保存此API Key，关闭后将无法再次查看。
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 p-2 bg-background rounded text-sm break-all">{newKey}</code>
                <Button size="sm" variant="outline" onClick={() => copyToClipboard(newKey)}>
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div>
              <Label>接入示例</Label>
              <pre className="p-3 bg-muted rounded text-xs overflow-auto mt-1">
{`<script src="https://your-chatbi-host/sdk/v1/chatbi-sdk.min.js"></script>
<chatbi-chat
  host="https://your-chatbi-host"
  api-key="${newKey}"
  user-id="your-user-id"
  height="600px">
</chatbi-chat>`}
              </pre>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => { setKeyModalOpen(false); copyToClipboard(newKey); }}>
              复制并关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
