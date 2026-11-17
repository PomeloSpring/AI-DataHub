import { useState, useEffect, useCallback } from 'react';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
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
import { useTranslation } from 'react-i18next';

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
  options, selected, onChange, placeholder,
}: {
  options: { label: string; value: string | number }[];
  selected: (string | number)[];
  onChange: (vals: (string | number)[]) => void;
  placeholder?: string;


}) {
  const { t } = useTranslation('ui');
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
          <span className="text-muted-foreground text-sm py-0.5">{placeholder || t('k_382f4b5559')}</span>
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
              placeholder={t('k_64e327ed1f')}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="h-7 text-xs"
              onClick={(e) => e.stopPropagation()}
            />
          </div>
          <div className="flex gap-1 px-2 py-1 border-b">
            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={selectAll}>{t("k_3e44b2a933")}</Button>
            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={clearAll}>{t("k_84fcd70d42")}</Button>
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
              <div className="px-2 py-3 text-sm text-muted-foreground text-center">{t("k_79b78dfab0")}</div>
            )}
          </ScrollArea>
        </div>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────

export default function IntegrationApps() {
  const { t } = useTranslation('ui');
  const [apps, setApps] = useState<Application[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
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
      const params: any = { page: p ?? page, size: pageSize };
      if (search) params.search = search;
      const { data } = await client.get('/embed/admin/applications', { params });
      setApps(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error(t('k_f3cb7ef6b4'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, search]);

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
      toast.error(t('k_b6cdcb3f82'));
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
        toast.success(t('k_55aa6366c0'));
      } else {
        const { data } = await client.post('/embed/admin/applications', payload);
        setNewKey(data.api_key);
        setKeyModalOpen(true);
        setAppKeys(prev => ({ ...prev, [data.id]: data.api_key }));
        toast.success(t('k_b110d33361'));
      }
      setModalOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_5fa802bef5'));
    }
  };

  const handleDelete = async (app: Application) => {
    if (!confirm(t('k_77f5d47c4f', { p0: app.name }))) return;
    try {
      await client.delete(`/embed/admin/applications/${app.id}`);
      toast.success(t('k_0007d170de'));
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_acf0664a54'));
    }
  };

  const handleRotateKey = async (appId: number) => {
    if (!confirm(t('k_1c55b83b6a'))) return;
    try {
      const { data } = await client.post(`/embed/admin/applications/${appId}/rotate-key`);
      setNewKey(data.api_key);
      setKeyModalOpen(true);
      setAppKeys(prev => ({ ...prev, [appId]: data.api_key }));
      toast.success(t('k_68c5671003'));
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_5a471cadcb'));
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
      toast.warning(t('k_095e36bff3'));
    }
  };

  const handleToggleStatus = async (app: Application) => {
    const newStatus = app.status === 'active' ? 'disabled' : 'active';
    try {
      await client.put(`/embed/admin/applications/${app.id}`, { status: newStatus });
      toast.success(newStatus === 'active' ? t('k_53ace4305f') : t('k_0fe5a98e9f'));
      load();
    } catch {
      toast.error(t('k_5fa802bef5'));
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success(t('k_4fb42e6eb1'));
  };

  const maskKey = (appId: number) => `ck_****${String(appId).slice(-4)}`;

  const dashboardOptions = allDashboards.map(d => ({ label: d.name, value: d.id }));
  const tableOptions = allTables.map(t => ({ label: t, value: t }));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Input
            placeholder={t('k_86cf57819d')}
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
          <Plus className="h-4 w-4 mr-2" />{t('k_6638cae28f')}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : (
        <div className="border rounded-lg">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="p-3 text-left">{t("k_2d87d51825")}</th>
                <th className="p-3 text-left">{t("k_62e951a692")}</th>
                <th className="p-3 text-left">API Key</th>
                <th className="p-3 text-left">{t("k_4e1ff3eb77")}</th>
                <th className="p-3 text-left">{t("k_75409eded9")}</th>
                <th className="p-3 text-left">{t("k_cad33537ae")}</th>
                <th className="p-3 text-left">{t("k_f3ea6d345e")}</th>
              </tr>
            </thead>
            <tbody>
              {apps.map((app) => {
                let dashIds: number[] = [];
                try { dashIds = app.allowed_dashboards ? JSON.parse(app.allowed_dashboards) : []; } catch {}


                return (
                  <tr key={app.id} className="border-b hover:bg-muted/30">
                    <td className="p-3">
                      <div className="font-medium">{app.name}</div>
                      {app.description && <div className="text-xs text-muted-foreground">{app.description}</div>}
                    </td>
                    <td className="p-3">
                      <Badge variant={app.status === 'active' ? 'default' : 'secondary'}>
                        {app.status === 'active' ? t('k_d4e9ca3dd4') : t('k_be70be5a2e')}
                      </Badge>
                    </td>
                    <td className="p-3">
                      <code className="text-xs bg-muted px-2 py-1 rounded">{maskKey(app.id)}</code>
                    </td>
                    <td className="p-3 text-xs">
                      <div className="flex flex-wrap gap-1">
                        {app.enable_chat && <Badge variant="default">{t("k_0e5e0c32d1")}</Badge>}
                        {dashIds.length > 0 && <Badge variant="secondary">Dashboard</Badge>}
                        {!app.enable_chat && dashIds.length === 0 && <Badge variant="outline">{t("k_63595e95b7")}</Badge>}
                      </div>
                    </td>
                    <td className="p-3 text-xs">
                      {dashIds.length === 0
                        ? <Badge variant="outline">{t("k_778fc8f994")}</Badge>
                        : dashIds.map(id => {
                            const d = allDashboards.find(x => x.id === id);
                            return <Badge key={id} variant="secondary" className="mr-1 mb-0.5">{d?.name || id}</Badge>;
                          })
                      }
                    </td>
                    <td className="p-3">{t('k_65d6797da4', { p0: app.rate_limit })}</td>
                    <td className="p-3">
                      <div className="flex items-center gap-1">
                        <Button variant="ghost" size="sm" onClick={() => openEdit(app)} title={t("k_a7f814c0a4")}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleRotateKey(app.id)} title={t("k_0e7ba9d312")}>
                          <Key className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleToggleStatus(app)} title={app.status === 'active' ? t('k_be70be5a2e') : t('k_d4e9ca3dd4')}>
                          <Shield className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handlePreview(app)} title={t("k_3a5a1f5a8b")}>
                          <ExternalLink className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleDelete(app)} title={t("k_3755f56f2f")} className="text-destructive hover:text-destructive">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {apps.length === 0 && (
                <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">{t("k_b0bcd19036")}</td></tr>

              )}
            </tbody>
          </table>
        </div>
      )}

      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        busy={loading}
        onChange={(p: number) => { setPage(p); load(p); }}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); load(1); }}
        hideWhenSingle
      />

      {/* Create/Edit Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingApp ? t('k_396fb46d83') : t('k_6638cae28f')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div>
              <Label>{t('k_bfda9af938')}</Label>
              <Input value={formValues.name} onChange={(e) => setFormValues(v => ({ ...v, name: e.target.value }))} placeholder={t("k_1d3d40d3b8")} />
            </div>
            <div>
              <Label>{t("k_412f54dc38")}</Label>
              <Input value={formValues.description} onChange={(e) => setFormValues(v => ({ ...v, description: e.target.value }))} placeholder={t("k_aa717b4149")} />
            </div>
            <div className="flex items-center justify-between">
              <Label>{t("k_74155d36e6")}</Label>
              <Switch
                checked={formValues.enable_chat}
                onCheckedChange={(checked) => setFormValues(v => ({ ...v, enable_chat: checked }))}
              />
            </div>
            <div>
              <Label>{t("k_0024bee0b2")}</Label>
              <Input type="number" value={formValues.rate_limit} onChange={(e) => setFormValues(v => ({ ...v, rate_limit: Number(e.target.value) }))} />
            </div>
            <div>
              <Label>{t('k_e8c9e2a95a')}</Label>
              <MultiSelect
                options={dashboardOptions}
                selected={selectedDashboards}
                onChange={setSelectedDashboards}
                placeholder={t("k_6029cc532a")}
              />
            </div>
            <div>
              <Label>{t('k_7d3eff8c89')}</Label>
              <MultiSelect
                options={tableOptions}
                selected={selectedTables}
                onChange={setSelectedTables}
                placeholder={t("k_204f25ffa1")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>{editingApp ? t('k_fadf24dbc5') : t('k_fcbd093292')}</Button>
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
                {t('k_6ccb591fa3')}
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 p-2 bg-background rounded text-sm break-all">{newKey}</code>
                <Button size="sm" variant="outline" onClick={() => copyToClipboard(newKey)}>
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div>
              <Label>{t("k_e87b289be9")}</Label>
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
              {t('k_c4519cdc3e')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
