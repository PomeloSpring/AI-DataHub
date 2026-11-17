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
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

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
  ready: { text: tr('k_c0d2181d57'), cls: 'text-green-600' },
  incomplete: { text: tr('k_9d48a1a3a0'), cls: 'text-amber-600' },
  disabled: { text: tr('k_8bb38ef00c'), cls: 'text-muted-foreground' },
  // 库里有内置行、代码里没有实现（服务已退役或迁移漏登记）：属配置漂移，必须显式可见
  unknown: { text: tr('k_964cc58f16'), cls: 'text-destructive' },
}

const builtinBadge = (s: MCPServer) =>
  BUILTIN_STATE_BADGE[s.state || ''] || { text: s.state || tr('k_1622dc9b6b'), cls: 'text-muted-foreground' };

const EMPTY_FORM: Record<string, any> = { transport: 'sse', is_active: 1 };

export default function MCPConfig() {
  const { t } = useTranslation('ui');
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
    } catch { toast.error(t('k_866b795eae')); }
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
    if (!form.name) { toast.error(t('k_d314caa149')); return; }
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
        toast.success(t('k_1b92092d63'));
      } else {
        await client.post('/admin/mcp-servers', submitData);
        toast.success(t('k_2d4c5dcc74'));
      }
      closeForm();
      load();
    } catch { toast.error(t('k_6de920b4e4')); }
  };

  const handleDelete = async (id: number) => {
    if (!confirm(t('k_7eee526351'))) return;
    try {
      await client.delete(`/admin/mcp-servers/${id}`);
      toast.success(t('k_5cc232620c'));
      load();
    } catch { toast.error(t('k_acf0664a54')); }
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
      toast.error(t('k_9710d9b9f1'));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("k_463f3bc770")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t("k_be7afb815d")}</p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={openCreate}>
            <Plus className="h-4 w-4 mr-1" /> {t('k_bd700b7228')}
          </Button>
        </div>
      </div>

      {/* Form —— 新建/编辑统一走弹窗（不再内联顶开列表；工具清单等长内容靠 DialogContent 限高滚动） */}
      <Dialog open={formOpen} onOpenChange={(open) => { if (!open) closeForm(); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{editing ? t('k_9d31430bcb') : t('k_3136ac5d1b')}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>{t("k_d82acb1b50")}</Label>
              <Input value={form.name || ''} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="filesystem" />
            </div>
            <div className="space-y-1.5">
              <Label>{t("k_e659fd134c")}</Label>
              <Select value={form.transport} onValueChange={v => setForm({ ...form, transport: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="sse">{t('k_df0c1d623e')}</SelectItem>
                  <SelectItem value="streamable_http">{t('k_3f5a04e0a5')}</SelectItem>
                  <SelectItem value="stdio">{t('k_9842fd972d')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>{t("k_412f54dc38")}</Label>
            <Input value={form.description || ''} onChange={e => setForm({ ...form, description: e.target.value })} placeholder={t("k_3ab1d1dbf4")} />
          </div>
          {form.transport === 'sse' || form.transport === 'streamable_http' ? (
            <div className="space-y-1.5">
              <Label>{t("k_86e118291e")}</Label>
              <Input value={form.url || ''} onChange={e => setForm({ ...form, url: e.target.value })} placeholder="http://localhost:3001/mcp" />
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>{t("k_b114b91547")}</Label>
                  <Input value={form.command || ''} onChange={e => setForm({ ...form, command: e.target.value })} placeholder="npx" />
                </div>
                <div className="space-y-1.5">
                  <Label>{t('k_9a5d3a4b8c')}</Label>
                  <Input value={form.args || ''} onChange={e => setForm({ ...form, args: e.target.value })} placeholder="-y,@modelcontextprotocol/server-filesystem" />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>{t('k_53a53eae3a')}</Label>
                <Textarea
                  value={form.env || ''}
                  onChange={e => setForm({ ...form, env: e.target.value })}
                  rows={2}
                  placeholder={'{"ES_URL": "http://localhost:9200", "ES_AUTH": "user:password"}'}
                />
                <p className="text-xs text-muted-foreground">{t("k_a4cdf0b1da")}</p>
              </div>
            </>
          )}
          {discoveredToolsList.length > 0 ? (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>{t("k_2a83bbaad0")}</Label>
                <span className="text-xs text-muted-foreground">
                  {t('k_3ae55abc41', { p0: selectedToolNames.size, p1: discoveredToolsList.length })}
                </span>
              </div>
              <div className="border rounded-md p-3 max-h-60 overflow-y-auto space-y-1.5 bg-background">
                <div className="flex gap-3 mb-2 pb-2 border-b">
                  <button type="button" className="text-xs text-primary hover:underline"
                    onClick={() => setSelectedToolNames(new Set(discoveredToolsList.map(t => t.name)))}>{t("k_3e44b2a933")}</button>
                  <button type="button" className="text-xs text-muted-foreground hover:underline"
                    onClick={() => setSelectedToolNames(new Set())}>{t("k_ba20eba2d6")}</button>
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
              <p className="text-xs text-muted-foreground">{t('k_59fbfad2b4')}</p>
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label>{t('k_1504305056')}</Label>
              <Textarea value={form.tools_config || ''} onChange={e => setForm({ ...form, tools_config: e.target.value })} placeholder={t('k_7f8705ac6a')} rows={3} />
              <p className="text-xs text-muted-foreground">{t("k_cb765303ba")}</p>
            </div>
          )}
          <div className="flex items-center gap-2">
            <Switch checked={form.is_active === 1} onCheckedChange={v => setForm({ ...form, is_active: v ? 1 : 0 })} />
            <span className="text-sm">{t("k_d4e9ca3dd4")}</span>
          </div>
          <DialogFooter>
            <Button size="sm" variant="ghost" onClick={closeForm}>{t("k_4d0b4688c7")}</Button>
            <Button size="sm" onClick={handleSave}>{t("k_fadf24dbc5")}</Button>
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
                      <span className="ml-2 text-xs text-muted-foreground">{t("k_7bec3656c9")}</span>
                      <span className={`ml-2 text-xs ${builtinBadge(s).cls}`}>{builtinBadge(s).text}</span>
                    </>
                  ) : (
                    <>
                      <span className="ml-2 text-xs text-muted-foreground">({s.transport})</span>
                      {!s.is_active && <span className="ml-2 text-xs text-destructive">{t("k_0fe5a98e9f")}</span>}
                      {s.last_test_status === 'success' && <span className="ml-2 text-xs text-green-600">{t('k_b384f6d509')}</span>}
                      {s.last_test_status === 'failed' && <span className="ml-2 text-xs text-destructive">{t('k_2696d9e237')}</span>}
                    </>
                  )}
                </div>
                {/* 只显业务描述：旧写法在描述为空时回退显示 url/command，会把连接地址当名称摊开 */}
                <div className="text-xs text-muted-foreground max-w-3xl">{s.description || t('k_d496aef938')}</div>
                {s.builtin && s.status_message && (
                  <div className="text-xs text-amber-600 mt-1">{s.status_message}</div>
                )}
                {/* 内置服务给数量不给名单：管理员要能判断“配了没配”，但页面读不到配置本身 */}
                {s.builtin && typeof s.open_view_count === 'number' && (
                  <div className="text-xs text-muted-foreground mt-1">
                    {t('k_1a4a49b0c0', { p0: s.open_view_count })}</div>
                )}
              </div>
              <div className="flex gap-1">
                <Button size="sm" variant="outline" onClick={() => handleTest(s.id)} disabled={testing === s.id}>
                  {testing === s.id ? t('k_6c501b52f1') : t('k_10b7d8eccc')}
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
                      {s.builtin ? t('k_bedc8b0cd9') : t('k_8bff48d76e')} ({tools.length})
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
                {t('k_949f1fb4cf')}
              </div>
            )}
          </div>
        ))}
        {!loading && servers.length === 0 && (
          <div className="text-sm text-muted-foreground text-center py-12">{t("k_5a72647e0e")}</div>
        )}
      </div>
    </div>
  );
}
