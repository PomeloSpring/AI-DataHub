import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { t } from '@/i18n/runtime';
import {
  RefreshCw, Search, Plus, Edit, Trash2, Database, Users, UserPlus,
  Wifi, Globe, Cylinder, Palette, Upload, X as XIcon, Link, AlertTriangle, Plug,
  HelpCircle, Maximize2, Minimize2, Server,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
// Separator removed — unused
import client from '../api/client';
import BrandLockup from '@/components/BrandLockup';
import { useBrandStore, type BrandSettings } from '@/stores/brandStore';
import { DEFAULT_BRAND, DEFAULT_APP_NAME, DEFAULT_TAGLINE, buildAppTitle } from '@/lib/brand';
import IntegrationApps from './admin/IntegrationApps';
import ERDiagram from '../components/ERDiagram';
import IntegrationLogs from './admin/IntegrationLogs';
import SiteManagement from './admin/SiteManagement';
import { listSites, siteNameOf, concreteSites, type Site } from '../api/sites';
import { modelCapabilityTags, MODEL_TAG_TONE_CLASS } from '../lib/llmModelTags';
import { useTranslation } from 'react-i18next';

// ── Helpers ────────────────────────────────────────────────────────────

function usePagedData(apiBase: string) {
  const [data, setData] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(DEFAULT_PAGE_SIZE);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<Record<string, string>>({});

  const load = useCallback(async (p?: number, extra?: Record<string, string>, sizeOverride?: number) => {
    // Merge stored filters with any new extra params
    const mergedFilters = extra ? { ...filters, ...extra } : filters;
    // If extra is provided, update stored filters
    if (extra) setFilters(prev => ({ ...prev, ...extra }));
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size: sizeOverride ?? size, ...mergedFilters };
      if (search) params.search = search;
      const { data: res } = await client.get(apiBase, { params });
      setData(res.items || []);
      setTotal(res.total || 0);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [apiBase, page, size, search, filters]);

  // Reset filters and reload
  const resetFilters = useCallback(() => {
    setFilters({});
  }, []);

  useEffect(() => { load(); }, []);

  return { data, total, page, size, search, loading, setPage, setSize, setSearch, load, setData, filters, resetFilters };
}

// ── Keywords Help Popover ──────────────────────────────────────────────

const TABLE_KEYWORDS_HELP = [
  { title: '用途', desc: '用于向量检索匹配，影响 AI 能否快速找到这张表' },
  { title: '填写原则', desc: '写用户可能会问的原话，而不是对表内容的描述' },
  { title: '分隔方式', desc: '多个关键词用中文顿号 、 分隔' },
  { title: '推荐示例', desc: '有多少用户、用户总数、用户数量统计' },
  { title: '避免', desc: '❌ 用户信息、用户管理（太泛化，无法区分表）' },
];

const COL_KEYWORDS_HELP = [
  { title: '用途', desc: '用于向量检索匹配，影响 AI 能否找到正确的字段' },
  { title: '填写原则', desc: '写该字段在查询中的常见表达方式' },
  { title: '分隔方式', desc: '多个关键词用中文顿号 、 分隔' },
  { title: '推荐示例', desc: '用户数量、注册时间、账号状态' },
  { title: '避免', desc: '❌ id、name（太简短，语义不明确）' },
];

function KeywordsHelp({ items }: { items: { title: string; desc: string }[] }) {
  const { t } = useTranslation('ui');
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="inline-flex items-center text-muted-foreground hover:text-foreground transition-colors">
          <HelpCircle className="h-4 w-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-80 text-sm" side="right">
        <div className="space-y-2">
          <p className="font-medium text-foreground">{t("k_4d8cf1e584")}</p>
          {items.map((item, i) => (
            <div key={i} className="flex gap-2">
              <span className="font-medium text-muted-foreground shrink-0">{item.title}:</span>
              <span>{item.desc}</span>
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

// ── Table Info Tab ─────────────────────────────────────────────────────

function TableInfoTab() {
  const { t } = useTranslation('ui');
  const { data, total, page, size, loading, setPage, setSize, load } = usePagedData('/admin/table-info');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValues, setEditValues] = useState<any>({});
  const [syncing, setSyncing] = useState(false);
  const [searchTable, setSearchTable] = useState('');
  const [datasources, setDatasources] = useState<any[]>([]);
  const [syncDsId, setSyncDsId] = useState<number>(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [formValues, setFormValues] = useState<any>({});
  const [clearDsOpen, setClearDsOpen] = useState(false);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    client.get('/datasources/').then(({ data }) => setDatasources(data)).catch(() => {});
  }, []);

  const [filterDsId, setFilterDsId] = useState<string>('');

  const doSearch = () => {
    setPage(1);
    const extra: Record<string, string> = { table_name: searchTable };
    if (filterDsId) extra.datasource_id = filterDsId;
    load(1, extra);
  };

  const handleSync = async () => {
    if (!syncDsId) {
      toast.error('请先选择要同步的数据源');
      return;
    }
    setSyncing(true);
    try {
      const { data: res } = await client.post('/admin/sync/metadata', { datasource_id: syncDsId });
      if (res.success === false) {
        toast.error(res.message || '同步失败');
      } else {
        toast.success(res.message);
        load();
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail || e.response?.data?.message || '同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const handleClearDs = async () => {
    if (!syncDsId) {
      toast.error('请先选择要清理的数据源');
      return;
    }
    setClearing(true);
    try {
      const { data: res } = await client.post('/admin/metadata/clear-by-datasource', { datasource_id: syncDsId });
      if (res.success) {
        toast.success(res.message);
        load();
      } else {
        toast.error(res.message);
      }
    } catch (e: any) {
      toast.error(e.response?.data?.message || '清理失败');
    } finally {
      setClearing(false);
      setClearDsOpen(false);
    }
  };

  const openCreate = () => {
    setFormValues({ is_active: true, datasource_id: 0 });
    setModalOpen(true);
  };

  const handleCreate = async () => {
    if (!formValues.table_name?.trim()) {
      toast.error('请输入表名');
      return;
    }
    try {
      await client.post('/admin/table-info', formValues);
      toast.success('已添加');
      setModalOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '添加失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/admin/table-info/${id}`);
      toast.success('已删除');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const startEdit = (record: any) => {
    setEditingId(record.id);
    setEditValues({
      table_comment: record.table_comment || '',
      table_business_desc: record.table_business_desc || '',
      keywords: record.keywords || '',
      domain_tag: record.domain_tag || '',
      region_tag: record.region_tag || '',
    });
  };

  const saveEdit = async () => {
    try {
      await client.put(`/admin/table-info/${editingId}`, editValues);
      toast.success('已保存');
      setEditingId(null);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  };

  const toggleActive = async (record: any) => {
    try {
      await client.put(`/admin/table-info/${record.id}`, { is_active: !record.is_active });
      toast.success(record.is_active ? '已禁用' : '已启用');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '操作失败');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Select value={filterDsId || "all"} onValueChange={(v) => { const dsId = v === "all" ? "" : v; setFilterDsId(dsId); setPage(1); load(1, { table_name: searchTable, ...(dsId ? { datasource_id: dsId } : {}) }); }}>
            <SelectTrigger className="w-[160px] h-9">
              <SelectValue placeholder={t("k_ae6802c77c")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_ae6802c77c")}</SelectItem>
              {datasources.map((ds) => (
                <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder={t("k_17a019127d")}
            value={searchTable}
            onChange={(e) => setSearchTable(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            className="w-[220px]"
          />
          <Button onClick={doSearch}>
            <Search className="h-4 w-4 mr-2" />
            搜索
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />
            手动添加
          </Button>
          <Select value={String(syncDsId)} onValueChange={(v) => setSyncDsId(Number(v))}>
            <SelectTrigger className="w-[180px] h-9">
              <SelectValue placeholder={t("k_422b9e314a")} />
            </SelectTrigger>
            <SelectContent>
              {datasources.map((ds) => (
                <SelectItem key={ds.id} value={String(ds.id)}>
                  {ds.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={handleSync} disabled={syncing || !syncDsId}>
            <RefreshCw className={`h-4 w-4 mr-2 ${syncing ? 'animate-spin' : ''}`} />
            同步元数据
          </Button>
          <Button variant="destructive" onClick={() => setClearDsOpen(true)} disabled={!syncDsId}>
            <Trash2 className="h-4 w-4 mr-2" />
            清理数据源
          </Button>
        </div>
      </div>

      {/* Clear Datasource Confirmation Dialog */}
      <Dialog open={clearDsOpen} onOpenChange={setClearDsOpen}>
        <DialogContent className="max-w-[420px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              确认清理
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要清理数据源 <strong>{datasources.find(ds => ds.id === syncDsId)?.name}</strong> 的所有元数据吗？
            <br /><br />
            此操作将删除该数据源下的<strong>{t("k_2c314c996a")}</strong>，且不可撤销。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setClearDsOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleClearDs} disabled={clearing}>
              {clearing && <Spinner className="h-4 w-4 mr-2" />}
              确认清理
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="rounded-lg border bg-card overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-64">
            <Spinner size={32} />
          </div>
        ) : (
          <ScrollArea className="h-[600px]">
            <table className="w-full" aria-label={t("k_005848dafd")}>
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_584c112b78")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_a3ccf702c5")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_80e60abd25")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_581778ce60")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">
                    <span className="flex items-center gap-1">{t("k_035664a01b")} <KeywordsHelp items={TABLE_KEYWORDS_HELP} /></span>
                  </th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_388b18c89a")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_17fc93c9cd")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_62e951a692")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_f3ea6d345e")}</th>
                </tr>
              </thead>
              <tbody>
                {data.map((row) => (
                  <tr key={row.id} className="border-b hover:bg-muted/50 transition-colors">
                    <td className="p-4 align-middle text-sm max-w-[180px] truncate">{row.table_name}</td>
                    <td className="p-4 align-middle text-sm">
                      {datasources.find((ds: any) => ds.id === row.datasource_id)?.name || <span className="text-muted-foreground">-</span>}
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[180px] truncate">
                      {editingId === row.id ? (
                        <Input
                          size={20}
                          value={editValues.table_comment}
                          onChange={(e) => setEditValues({ ...editValues, table_comment: e.target.value })}
                        />
                      ) : (
                        row.table_comment || <span className="text-muted-foreground">-</span>
                      )}
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[250px] truncate">
                      {editingId === row.id ? (
                        <Input
                          size={20}
                          value={editValues.table_business_desc}
                          placeholder={t("k_9d7c52435a")}
                          onChange={(e) => setEditValues({ ...editValues, table_business_desc: e.target.value })}
                        />
                      ) : (
                        row.table_business_desc || <span className="text-muted-foreground">-</span>
                      )}
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[180px] truncate">
                      {editingId === row.id ? (
                        <Input
                          size={20}
                          value={editValues.keywords}
                          placeholder={t("k_035664a01b")}
                          onChange={(e) => setEditValues({ ...editValues, keywords: e.target.value })}
                        />
                      ) : (
                        row.keywords || <span className="text-muted-foreground">-</span>
                      )}
                    </td>
                    <td className="p-4 align-middle">
                      {editingId === row.id ? (
                        <Input
                          size={20}
                          value={editValues.domain_tag}
                          className="w-[56px]"
                          onChange={(e) => setEditValues({ ...editValues, domain_tag: e.target.value })}
                        />
                      ) : (
                        row.domain_tag ? <Badge variant="outline">{row.domain_tag}</Badge> : '-'
                      )}
                    </td>
                    <td className="p-4 align-middle">
                      {editingId === row.id ? (
                        <Input
                          size={20}
                          value={editValues.region_tag}
                          className="w-[48px]"
                          onChange={(e) => setEditValues({ ...editValues, region_tag: e.target.value })}
                        />
                      ) : (
                        row.region_tag || '-'
                      )}
                    </td>
                    <td className="p-4 align-middle">
                      <Switch
                        checked={!!row.is_active}
                        onCheckedChange={() => toggleActive(row)}
                      />
                    </td>
                    <td className="p-4 align-middle">
                      {editingId === row.id ? (
                        <div className="flex items-center gap-2">
                          <Button size="sm" onClick={saveEdit}>{t("k_fadf24dbc5")}</Button>
                          <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>{t("k_4d0b4688c7")}</Button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1">
                          <Button size="sm" variant="ghost" onClick={() => startEdit(row)}>
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => handleDelete(row.id)}>
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
        )}

        <PaginationBar
          page={page}
          pageSize={size}
          total={total}
          onChange={(p: number) => { setPage(p); load(p); }}
          onPageSizeChange={(n: number) => { setSize(n); setPage(1); load(1, undefined, n); }}
          hideWhenSingle
          className="rounded-b-lg"
        />
      </div>

      {/* Add Table Info Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[560px]">
          <DialogHeader>
            <DialogTitle>{t("k_952c850282")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>表名 *</Label>
              <Input
                placeholder="如: dim_case"
                value={formValues.table_name || ''}
                onChange={(e) => setFormValues({ ...formValues, table_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("k_a3ccf702c5")}</Label>
              <Select
                value={String(formValues.datasource_id || 0)}
                onValueChange={(v) => setFormValues({ ...formValues, datasource_id: Number(v) })}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t("k_8a5f5aed02")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">{t("k_1aad39c63a")}</SelectItem>
                  {datasources.map((ds) => (
                    <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>{t("k_80e60abd25")}</Label>
              <Input
                placeholder={t("k_46747c0b90")}
                value={formValues.table_comment || ''}
                onChange={(e) => setFormValues({ ...formValues, table_comment: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("k_581778ce60")}</Label>
              <Textarea
                placeholder={t("k_85e0747560")}
                rows={2}
                value={formValues.table_business_desc || ''}
                onChange={(e) => setFormValues({ ...formValues, table_business_desc: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Label>{t("k_035664a01b")}</Label>
                <KeywordsHelp items={TABLE_KEYWORDS_HELP} />
              </div>
              <Input
                placeholder="精简关键词，用于向量检索匹配，如: 有多少用户、用户总数"
                value={formValues.keywords || ''}
                onChange={(e) => setFormValues({ ...formValues, keywords: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">{t("k_f42b512745")}</p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_49aa0a7282")}</Label>
                <Input
                  placeholder="如: 医疗"
                  value={formValues.domain_tag || ''}
                  onChange={(e) => setFormValues({ ...formValues, domain_tag: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("k_c286d8e149")}</Label>
                <Input
                  placeholder="如: 华东"
                  value={formValues.region_tag || ''}
                  onChange={(e) => setFormValues({ ...formValues, region_tag: e.target.value })}
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={formValues.is_active !== false}
                onCheckedChange={(v) => setFormValues({ ...formValues, is_active: v })}
              />
              <Label>{t("k_d4e9ca3dd4")}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleCreate}>{t("k_94191ce210")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Column Metadata Tab ───────────────────────────────────────────────

function ColumnMetadataTab() {
  const { t } = useTranslation('ui');
  const { data, total, page, size, loading, setPage, setSize, load } = usePagedData('/admin/metadata');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValues, setEditValues] = useState<any>({});
  const [searchTable, setSearchTable] = useState('');
  const [searchColumn, setSearchColumn] = useState('');
  const [filterDsId, setFilterDsId] = useState<string>('');
  const [modalOpen, setModalOpen] = useState(false);
  const [formValues, setFormValues] = useState<any>({});
  const [datasources, setDatasources] = useState<any[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [syncDsId, setSyncDsId] = useState<number>(0);
  const [syncTableName, setSyncTableName] = useState('');
  const [clearTableOpen, setClearTableOpen] = useState(false);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    client.get('/datasources/').then(({ data }) => setDatasources(data)).catch(() => {});
  }, []);

  const dsNameMap = Object.fromEntries(datasources.map((ds: any) => [ds.id, ds.name]));
  const syncDisabled = !syncDsId || !syncTableName.trim() || syncing;

  const doSearch = () => {
    setPage(1);
    const extra: Record<string, string> = { table_name: searchTable, column_name: searchColumn };
    if (filterDsId) extra.datasource_id = filterDsId;
    load(1, extra);
  };

  const handleSync = async () => {
    if (!syncDsId) {
      toast.error('请先选择数据源');
      return;
    }
    if (!syncTableName.trim()) {
      toast.error('请输入要同步的表名');
      return;
    }
    setSyncing(true);
    try {
      const { data: res } = await client.post('/admin/sync/metadata/columns', {
        datasource_id: syncDsId,
        table_name: syncTableName.trim(),
      });
      if (res.success) {
        toast.success(res.message);
        load();
      } else {
        toast.error(res.message);
      }
    } catch (e: any) {
      toast.error(e.response?.data?.message || '同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const handleClearTable = async () => {
    if (!syncDsId) {
      toast.error('请先选择数据源');
      return;
    }
    if (!syncTableName.trim()) {
      toast.error('请输入要清理的表名');
      return;
    }
    setClearing(true);
    try {
      const { data: res } = await client.post('/admin/metadata/clear-by-table', {
        datasource_id: syncDsId,
        table_name: syncTableName.trim(),
      });
      if (res.success) {
        toast.success(res.message);
        load();
      } else {
        toast.error(res.message);
      }
    } catch (e: any) {
      toast.error(e.response?.data?.message || '清理失败');
    } finally {
      setClearing(false);
      setClearTableOpen(false);
    }
  };

  const openCreate = () => {
    setFormValues({ is_active: true, data_type: 'VARCHAR', is_key: 'false', is_nullable: 'true', datasource_id: 0 });
    setModalOpen(true);
  };

  const handleCreate = async () => {
    if (!formValues.table_name?.trim()) {
      toast.error('请输入表名');
      return;
    }
    if (!formValues.column_name?.trim()) {
      toast.error('请输入字段名');
      return;
    }
    try {
      await client.post('/admin/metadata', formValues);
      toast.success('已添加');
      setModalOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '添加失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/admin/metadata/${id}`);
      toast.success('已删除');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const startEdit = (record: any) => {
    setEditingId(record.id);
    setEditValues({
      column_comment: record.column_comment || '',
      business_desc: record.business_desc || '',
      keywords: record.keywords || '',
    });
  };

  const saveEdit = async () => {
    try {
      await client.put(`/admin/metadata/${editingId}`, editValues);
      toast.success('已保存');
      setEditingId(null);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  };

  const toggleActive = async (record: any) => {
    try {
      await client.put(`/admin/metadata/${record.id}`, { is_active: !record.is_active });
      toast.success(record.is_active ? '已禁用' : '已启用');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '操作失败');
    }
  };

  return (
    <div className="space-y-4">
      {/* Sync per-table row */}
      <div className="flex items-center gap-2 p-3 border rounded-lg bg-muted/30">
        <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
        <span className="text-sm font-medium whitespace-nowrap">{t("k_364135d47b")}</span>
        <Select value={String(syncDsId)} onValueChange={(v) => setSyncDsId(Number(v))}>
          <SelectTrigger className="w-[160px] h-8">
            <SelectValue placeholder={t("k_422b9e314a")} />
          </SelectTrigger>
          <SelectContent>
            {datasources.map((ds) => (
              <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          placeholder={t("k_47a244c67f")}
          value={syncTableName}
          onChange={(e) => setSyncTableName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSync()}
          className="w-[200px] h-8"
        />
        <Button size="sm" onClick={handleSync} disabled={syncDisabled}>
          <RefreshCw className={`h-3 w-3 mr-1 ${syncing ? 'animate-spin' : ''}`} />
          同步字段
        </Button>
        <Button size="sm" variant="destructive" onClick={() => setClearTableOpen(true)} disabled={!syncDsId || !syncTableName.trim()}>
          <Trash2 className="h-3 w-3 mr-1" />
          清理单表
        </Button>
      </div>

      {/* Clear Table Confirmation Dialog */}
      <Dialog open={clearTableOpen} onOpenChange={setClearTableOpen}>
        <DialogContent className="max-w-[420px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              确认清理
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要清理表 <strong>{syncTableName}</strong> 的所有元数据吗？
            <br /><br />
            此操作将删除该表的<strong>{t("k_b874fc9ed3")}</strong>，且不可撤销。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setClearTableOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleClearTable} disabled={clearing}>
              {clearing && <Spinner className="h-4 w-4 mr-2" />}
              确认清理
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Search + filter row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Select value={filterDsId || "all"} onValueChange={(v) => { const dsId = v === "all" ? "" : v; setFilterDsId(dsId); setPage(1); load(1, { table_name: searchTable, column_name: searchColumn, ...(dsId ? { datasource_id: dsId } : {}) }); }}>
            <SelectTrigger className="w-[140px] h-9">
              <SelectValue placeholder={t("k_ae6802c77c")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_ae6802c77c")}</SelectItem>
              {datasources.map((ds) => (
                <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder={t("k_17a019127d")}
            value={searchTable}
            onChange={(e) => setSearchTable(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            className="w-[160px]"
          />
          <Input
            placeholder={t("k_04e0947134")}
            value={searchColumn}
            onChange={(e) => setSearchColumn(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            className="w-[160px]"
          />
          <Button onClick={doSearch}>
            <Search className="h-4 w-4 mr-2" />
            搜索
          </Button>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-2" />
          手动添加
        </Button>
      </div>

      <div className="rounded-lg border bg-card overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-64">
            <Spinner size={32} />
          </div>
        ) : (
          <ScrollArea className="h-[600px]">
            <table className="w-full">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_a3ccf702c5")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_584c112b78")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_77a49f2c38")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_e4e46c7235")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_6a61acba62")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_581778ce60")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">
                    <span className="flex items-center gap-1">{t("k_035664a01b")} <KeywordsHelp items={COL_KEYWORDS_HELP} /></span>
                  </th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_502fce2b8d")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_62e951a692")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_f3ea6d345e")}</th>
                </tr>
              </thead>
              <tbody>
                {data.map((row) => (
                  <tr key={row.id} className="border-b hover:bg-muted/50 transition-colors">
                    <td className="p-4 align-middle text-sm">
                      {dsNameMap[row.datasource_id] || <span className="text-muted-foreground">-</span>}
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[160px] truncate">{row.table_name}</td>
                    <td className="p-4 align-middle text-sm max-w-[120px] truncate">{row.column_name}</td>
                    <td className="p-4 align-middle text-sm">{row.data_type}</td>
                    <td className="p-4 align-middle text-sm max-w-[160px] truncate">
                      {editingId === row.id ? (
                        <Input
                          size={20}
                          value={editValues.column_comment}
                          onChange={(e) => setEditValues({ ...editValues, column_comment: e.target.value })}
                        />
                      ) : (
                        row.column_comment || <span className="text-muted-foreground">-</span>
                      )}
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[200px] truncate">
                      {editingId === row.id ? (
                        <Input
                          size={20}
                          value={editValues.business_desc}
                          placeholder={t("k_9d7c52435a")}
                          onChange={(e) => setEditValues({ ...editValues, business_desc: e.target.value })}
                        />
                      ) : (
                        row.business_desc || <span className="text-muted-foreground">-</span>
                      )}
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[150px] truncate">
                      {editingId === row.id ? (
                        <Input
                          size={20}
                          value={editValues.keywords}
                          placeholder={t("k_035664a01b")}
                          onChange={(e) => setEditValues({ ...editValues, keywords: e.target.value })}
                        />
                      ) : (
                        row.keywords || <span className="text-muted-foreground">-</span>
                      )}
                    </td>
                    <td className="p-4 align-middle">
                      {row.is_key === 'true' && <Badge variant="secondary">PK</Badge>}
                    </td>
                    <td className="p-4 align-middle">
                      <Switch
                        checked={!!row.is_active}
                        onCheckedChange={() => toggleActive(row)}
                      />
                    </td>
                    <td className="p-4 align-middle">
                      {editingId === row.id ? (
                        <div className="flex items-center gap-2">
                          <Button size="sm" onClick={saveEdit}>{t("k_fadf24dbc5")}</Button>
                          <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>{t("k_4d0b4688c7")}</Button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1">
                          <Button size="sm" variant="ghost" onClick={() => startEdit(row)}>
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => handleDelete(row.id)}>
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
        )}

        <PaginationBar
          page={page}
          pageSize={size}
          total={total}
          onChange={(p: number) => { setPage(p); load(p); }}
          onPageSizeChange={(n: number) => { setSize(n); setPage(1); load(1, undefined, n); }}
          hideWhenSingle
          className="rounded-b-lg"
        />
      </div>

      {/* Add Column Metadata Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[560px]">
          <DialogHeader>
            <DialogTitle>{t("k_725edc307d")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>表名 *</Label>
                <Input
                  placeholder="如: dim_case"
                  value={formValues.table_name || ''}
                  onChange={(e) => setFormValues({ ...formValues, table_name: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>字段名 *</Label>
                <Input
                  placeholder="如: case_code"
                  value={formValues.column_name || ''}
                  onChange={(e) => setFormValues({ ...formValues, column_name: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_b031dc9a85")}</Label>
                <Input
                  placeholder="如: VARCHAR(255)"
                  value={formValues.data_type || ''}
                  onChange={(e) => setFormValues({ ...formValues, data_type: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("k_a3ccf702c5")}</Label>
                <Select
                  value={String(formValues.datasource_id || 0)}
                  onValueChange={(v) => setFormValues({ ...formValues, datasource_id: Number(v) })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t("k_8a5f5aed02")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">{t("k_1aad39c63a")}</SelectItem>
                    {datasources.map((ds) => (
                      <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("k_6a61acba62")}</Label>
              <Input
                placeholder={t("k_9ba0843858")}
                value={formValues.column_comment || ''}
                onChange={(e) => setFormValues({ ...formValues, column_comment: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("k_581778ce60")}</Label>
              <Textarea
                placeholder={t("k_fffc8dfcf9")}
                rows={2}
                value={formValues.business_desc || ''}
                onChange={(e) => setFormValues({ ...formValues, business_desc: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Label>{t("k_035664a01b")}</Label>
                <KeywordsHelp items={COL_KEYWORDS_HELP} />
              </div>
              <Input
                placeholder={t("k_b2a133b593")}
                value={formValues.keywords || ''}
                onChange={(e) => setFormValues({ ...formValues, keywords: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_395fd29b92")}</Label>
                <Select
                  value={formValues.is_key || 'false'}
                  onValueChange={(v) => setFormValues({ ...formValues, is_key: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="false">{t("k_8bf5c10ad9")}</SelectItem>
                    <SelectItem value="true">{t("k_30160a21b9")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>{t("k_2323793e51")}</Label>
                <Select
                  value={formValues.is_nullable || 'true'}
                  onValueChange={(v) => setFormValues({ ...formValues, is_nullable: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="true">{t("k_30160a21b9")}</SelectItem>
                    <SelectItem value="false">{t("k_8bf5c10ad9")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={formValues.is_active !== false}
                onCheckedChange={(v) => setFormValues({ ...formValues, is_active: v })}
              />
              <Label>{t("k_d4e9ca3dd4")}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleCreate}>{t("k_94191ce210")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Metadata Tab (combined) ───────────────────────────────────────────

function MetadataTab() {
  const { t } = useTranslation('ui');
  return (
    <Tabs defaultValue="table-info">
      <TabsList>
        <TabsTrigger value="table-info">{t("k_c48cadc8f1")}</TabsTrigger>
        <TabsTrigger value="columns">{t("k_596b3a5adf")}</TabsTrigger>
      </TabsList>
      <TabsContent value="table-info">
        <TableInfoTab />
      </TabsContent>
      <TabsContent value="columns">
        <ColumnMetadataTab />
      </TabsContent>
    </Tabs>
  );
}

// ── Terms Tab ──────────────────────────────────────────────────────────

function TermsTab() {
  const { t } = useTranslation('ui');
  const { data, total, page, size, loading, setPage, setSize, setSearch, load } = usePagedData('/admin/terms');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [formValues, setFormValues] = useState<any>({});

  const openCreate = () => {
    setEditing(null);
    setFormValues({ term_type: 'dimension' });
    setModalOpen(true);
  };

  const openEdit = (record: any) => {
    setEditing(record);
    setFormValues(record);
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      if (editing) {
        await client.put(`/admin/terms/${editing.id}`, formValues);
        toast.success('已更新');
      } else {
        await client.post('/admin/terms', formValues);
        toast.success('已创建');
      }
      setModalOpen(false);
      load();
    } catch (e: any) {
      if (e.response?.data?.detail) toast.error(e.response.data.detail);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/admin/terms/${id}`);
      toast.success('已删除');
      load();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (id: number) => {
    try {
      const { data } = await client.put(`/admin/terms/${id}/toggle`);
      toast.success(data.is_active ? '已启用' : '已禁用');
      load();
    } catch {
      toast.error('切换状态失败');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Input
          placeholder={t("k_7e5a71727f")}
          className="w-[280px]"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              setSearch((e.target as HTMLInputElement).value);
              setPage(1);
              load(1);
            }
          }}
        />
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-2" />
          新建术语
        </Button>
      </div>

      <div className="rounded-lg border bg-card overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-64">
            <Spinner size={32} />
          </div>
        ) : (
          <ScrollArea className="h-[600px]">
            <table className="w-full">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_d085e83eec")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_decc83f773")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_ec537c546d")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_e4e46c7235")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_442be9c497")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_167c3f094d")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_a2330638a0")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_412f54dc38")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_2d7d18da79")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_62e951a692")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_f3ea6d345e")}</th>
                </tr>
              </thead>
              <tbody>
                {data.map((row) => (
                  <tr key={row.id} className="border-b hover:bg-muted/50 transition-colors">
                    <td className="p-4 align-middle text-sm">{row.term_cn}</td>
                    <td className="p-4 align-middle text-sm max-w-[120px] truncate">{row.term_en}</td>
                    <td className="p-4 align-middle text-sm max-w-[160px] truncate">{row.term_aliases}</td>
                    <td className="p-4 align-middle">
                      <Badge variant={row.term_type === 'metric' ? 'default' : 'secondary'}>
                        {row.term_type === 'metric' ? '指标' : '维度'}
                      </Badge>
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[120px] truncate">{row.target_table}</td>
                    <td className="p-4 align-middle text-sm max-w-[120px] truncate">{row.target_column}</td>
                    <td className="p-4 align-middle text-sm max-w-[140px] truncate">{row.calculation}</td>
                    <td className="p-4 align-middle text-sm max-w-[200px] truncate">{row.description}</td>
                    <td className="p-4 align-middle text-sm">{row.usage_count}</td>
                    <td className="p-4 align-middle">
                      <Switch
                        checked={row.is_active === 1 || row.is_active === null}
                        onCheckedChange={() => handleToggle(row.id)}
                      />
                    </td>
                    <td className="p-4 align-middle">
                      <div className="flex items-center gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openEdit(row)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => handleDelete(row.id)}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
        )}

        <PaginationBar
          page={page}
          pageSize={size}
          total={total}
          onChange={(p: number) => { setPage(p); load(p); }}
          onPageSizeChange={(n: number) => { setSize(n); setPage(1); load(1, undefined, n); }}
          hideWhenSingle
          className="rounded-b-lg"
        />
      </div>

      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[600px]">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑术语' : '新建术语'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>中文名 *</Label>
                <Input
                  placeholder="如: 病例"
                  value={formValues.term_cn || ''}
                  onChange={(e) => setFormValues({ ...formValues, term_cn: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("k_decc83f773")}</Label>
                <Input
                  placeholder="如: case"
                  value={formValues.term_en || ''}
                  onChange={(e) => setFormValues({ ...formValues, term_en: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("k_5816a023d3")}</Label>
              <Input
                placeholder="如: case_code,诊断,检查"
                value={formValues.term_aliases || ''}
                onChange={(e) => setFormValues({ ...formValues, term_aliases: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>类型 *</Label>
              <Select value={formValues.term_type || 'dimension'} onValueChange={(v) => setFormValues({ ...formValues, term_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="dimension">{t("k_72fa9ecdc4")}</SelectItem>
                  <SelectItem value="metric">{t("k_1dfc810a7b")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_442be9c497")}</Label>
                <Input
                  placeholder="如: dim_case"
                  value={formValues.target_table || ''}
                  onChange={(e) => setFormValues({ ...formValues, target_table: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("k_167c3f094d")}</Label>
                <Input
                  placeholder="如: case_code"
                  value={formValues.target_column || ''}
                  onChange={(e) => setFormValues({ ...formValues, target_column: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("k_b434ec47f3")}</Label>
              <Input
                placeholder="如: COUNT(*)"
                className="font-mono"
                value={formValues.calculation || ''}
                onChange={(e) => setFormValues({ ...formValues, calculation: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("k_412f54dc38")}</Label>
              <Textarea
                placeholder={t("k_2c6b7e5670")}
                rows={2}
                value={formValues.description || ''}
                onChange={(e) => setFormValues({ ...formValues, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>{t("k_fadf24dbc5")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Datasource Tab ──────────────────────────────────────────────────────

// 数据库类别分区（关系型 / 数仓 / 其他）：列表分区展示与类型选择分组共用
const DB_CATEGORIES: Record<string, { label: string; icon: any; desc: string }> = {
  relational: { label: '关系型数据库', icon: Database, desc: '事务型业务库（OLTP）：MySQL、PostgreSQL' },
  warehouse: { label: '数据仓库', icon: Cylinder, desc: '分析型数仓（OLAP）：Apache Doris' },
  other: { label: '其他数据服务', icon: Server, desc: '日志/检索等外部服务接入' },
};
const DB_CATEGORY_ORDER = ['relational', 'warehouse', 'other'];

const DB_TYPES = [
  { value: 'mysql', label: 'MySQL', icon: Database, color: 'text-blue-400', defaultPort: 3306, category: 'relational' },
  { value: 'postgresql', label: 'PostgreSQL', icon: Database, color: 'text-indigo-400', defaultPort: 5432, category: 'relational' },
  { value: 'doris', label: 'Apache Doris', icon: Cylinder, color: 'text-cyan-400', defaultPort: 9030, category: 'warehouse' },
  { value: 'sls', label: 'SLS (PG协议接入)', icon: Server, color: 'text-teal-400', defaultPort: 5432, category: 'other' },
  // ES 没有 DB-API 连接（走 REST /_sql），“库”这一层由索引别名承担，因此没有数据库名字段
  { value: 'es', label: 'Elasticsearch', icon: Server, color: 'text-rose-400', defaultPort: 9200, category: 'other' },
];

const isEsDbType = (dbType: string) => String(dbType || '').toLowerCase() === 'es'
  || String(dbType || '').toLowerCase() === 'elasticsearch';

/** extra_config  tolerant 解析（后端可能回对象、JSON 字符串或 NULL）。
 *
 * ES 与 SQL 型的高级配置都存在同一列（超时/最大行数/连接池 vs 连接超时/查询超时），
 * 所以一个解析器同时回两套字段；谁可编辑由各类型的表单块自己决定（不给非对应类型摆入口）。
 */
function esExtraOf(raw: any): {
  aliases: string[]; patterns: string[]; timeoutSec: string; maxRows: string;
  verifyCerts: boolean; poolSize: string; connectSec: string; readSec: string;
  maxConcurrency: string; queueWaitSec: string;
} {
  let extra: any = raw?.extra_config ?? raw ?? {};
  if (typeof extra === 'string') {
    try { extra = JSON.parse(extra || '{}'); } catch { extra = {}; }
  }
  if (!extra || typeof extra !== 'object') extra = {};
  const num = (v: any) => (v != null ? String(v) : '');
  return {
    aliases: (extra.allowed_aliases || []).map(String),
    patterns: (extra.allowed_index_patterns || []).map(String),
    timeoutSec: num(extra.timeout_sec),
    maxRows: num(extra.max_rows),
    poolSize: num(extra.pool_size),
    verifyCerts: extra.verify_certs !== false,
    connectSec: num(extra.connect_timeout_sec),
    readSec: num(extra.read_timeout_sec),
    // 取数并发闸门：ES 与 SQL 型共用同一组键（不存在“只有一类源能配”的分局）
    maxConcurrency: num(extra.max_concurrency),
    queueWaitSec: num(extra.queue_wait_sec),
  };
}

function dbCategoryOf(dbType: string): string {
  return DB_TYPES.find(d => d.value === dbType)?.category || 'other';
}

/** 清单式录入（标签 chips + 输入框添加/删除）：ES 开放范围用它，不要求管理员手拼 JSON。 */
function EsTagField({ label, placeholder, values, hint, onChange }: {
  label: string;
  placeholder: string;
  values: string[];
  hint?: string;
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const text = draft.trim();
    if (!text) return;
    if (!values.includes(text)) onChange([...values, text]);
    setDraft('');
  };
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {values.map((v) => (
            <Badge key={v} variant="secondary" className="text-xs gap-1">
              {v}
              <button
                type="button"
                aria-label={`移除 ${v}`}
                onClick={() => onChange(values.filter((x) => x !== v))}
              >
                <XIcon className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          placeholder={placeholder}
        />
        <Button type="button" size="sm" variant="outline" onClick={add}>{t("k_94191ce210")}</Button>
      </div>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** 取数并发闸门录入（ES 与 SQL 型都适用，共用一套 state 键名，不另开两份字段）。
 *
 * 消费方 `backend/core/query_gate.py`：一次看板刷新 = N 图 × M 个用户，而 SQL 型源
 * 每次取数都新建连接，不限并发就是集体撞源侧 max_connections。满员先排队（FIFO），
 * 排不过「排队等待上限」才给用户一句可读的“稍后重试”（不会把空结果当成没数据）。
 * `queue_wait_sec` 允许 0（不排队直接拒），提交侧因此判 `!== ''` 而不是判真值。
 */
function QueryGateFields({ values, onPatch }: { values: any; onPatch: (patch: Record<string, any>) => void }) {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t("k_22fc0012af")}</Label>
          <Input
            type="number" min={1} max={64}
            value={values.gate_max_concurrency}
            onChange={(e) => onPatch({ gate_max_concurrency: e.target.value })}
            placeholder={t("k_4e08cb8aa3")}
          />
        </div>
        <div className="space-y-2">
          <Label>{t("k_5d8b46babe")}</Label>
          <Input
            type="number" min={0} max={120}
            value={values.gate_queue_wait_sec}
            onChange={(e) => onPatch({ gate_queue_wait_sec: e.target.value })}
            placeholder="默认 10 秒；填 0 = 不排队直接拒"
          />
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        保护源侧资源：同时打到本源的查询数不超过上限，超出先排队，排不到则提示“稍后重试”。
        留空 = 用平台默认（8 并发 / 排队 10 秒）；保存后即时生效，不重启也不打断在跑的查询。
      </p>
    </div>
  );
}

// 导出供用例直接验证 ES 型表单（extra_config 往返与 fail-closed 护栏），不必从整页导航进入
export function DatasourceTab() {
  const { t } = useTranslation('ui');
  const [datasources, setDatasources] = useState<any[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [formValues, setFormValues] = useState<any>({
    name: '',
    db_type: 'mysql',
    host: '',
    port: 3306,
    username: '',
    password: '',
    database_name: '',
    is_default: false,
    ssl: false,
    site_code: '',
    // ES 专用（开放范围与预算）：提交前组装成 extra_config，不直接入库
    es_aliases: [] as string[],
    es_patterns: [] as string[],
    es_timeout_sec: '',
    es_max_rows: '',
    es_pool_size: '',
    es_verify_certs: true,
    // SQL 型专用（连接/查询超时）：同样进 extra_config，留空 = 用代码缺省
    sql_connect_timeout_sec: '',
    sql_read_timeout_sec: '',
    // 取数并发闸门（ES 与 SQL 型共用，不另开两套名字）
    gate_max_concurrency: '',
    gate_queue_wait_sec: '',
  });
  const [testing, setTesting] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ id: number; success: boolean; message: string } | null>(null);

  const loadDatasources = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/datasources');
      setDatasources(Array.isArray(data) ? data : []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadDatasources(); }, []);

  // 站点候选来自 adh_sites（选择框不手输代码）；加载失败只让站点框空，不拖垮列表
  useEffect(() => {
    listSites({ includeAggregate: false }).then(setSites).catch(() => setSites([]));
  }, []);

  const openCreate = () => {
    setEditing(null);
    setFormValues({
      name: '',
      db_type: 'mysql',
      host: '',
      port: 3306,
      username: '',
      password: '',
      database_name: '',
      is_default: false,
      ssl: false,
      site_code: '',
      es_aliases: [],
      es_patterns: [],
      es_timeout_sec: '',
      es_max_rows: '',
      es_pool_size: '',
      es_verify_certs: true,
      sql_connect_timeout_sec: '',
      sql_read_timeout_sec: '',
      gate_max_concurrency: '',
      gate_queue_wait_sec: '',
    });
    setModalOpen(true);
  };

  const openEdit = (record: any) => {
    setEditing(record);
    const extra = esExtraOf(record);
    setFormValues({
      name: record.name || '',
      db_type: record.db_type || 'mysql',
      host: record.host || '',
      port: record.port || 3306,
      username: record.username || '',
      password: '',  // Don't show existing password
      database_name: record.database_name || '',
      is_default: !!record.is_default,
      ssl: !!record.ssl,
      site_code: record.site_code || '',
      es_aliases: extra.aliases,
      es_patterns: extra.patterns,
      es_timeout_sec: extra.timeoutSec,
      es_max_rows: extra.maxRows,
      es_pool_size: extra.poolSize,
      es_verify_certs: extra.verifyCerts,
      sql_connect_timeout_sec: extra.connectSec,
      sql_read_timeout_sec: extra.readSec,
      gate_max_concurrency: extra.maxConcurrency,
      gate_queue_wait_sec: extra.queueWaitSec,
    });
    setModalOpen(true);
  };

  /** 提交载荷：高级配置组装进 extra_config（ES 与 SQL 型共用一列，各自只写自己那一套）。
   *
   * SQL 型两个超时都留空时传 null = 清空该列回到代码缺省（不是“不提交”）：
   * 否则配上去的预算在 UI 上改不回来，只能加不能减。
   */
  const buildPayload = () => {
    const {
      es_aliases, es_patterns, es_timeout_sec, es_max_rows, es_pool_size, es_verify_certs,
      sql_connect_timeout_sec, sql_read_timeout_sec,
      gate_max_concurrency, gate_queue_wait_sec, ...rest
    } = formValues;
    // 闸门旋钮两类源都写（同一组 extra_config 键，消费方同一处）
    const gateExtra = () => {
      const patch: Record<string, any> = {};
      if (gate_max_concurrency !== '' && gate_max_concurrency != null) {
        patch.max_concurrency = Number(gate_max_concurrency);
      }
      if (gate_queue_wait_sec !== '' && gate_queue_wait_sec != null) {
        patch.queue_wait_sec = Number(gate_queue_wait_sec);   // 0 是真配置（不排队），不能当没填
      }
      return patch;
    };
    if (!isEsDbType(formValues.db_type)) {
      const extra: Record<string, any> = { ...gateExtra() };
      if (sql_connect_timeout_sec) extra.connect_timeout_sec = Number(sql_connect_timeout_sec);
      if (sql_read_timeout_sec) extra.read_timeout_sec = Number(sql_read_timeout_sec);
      return { ...rest, extra_config: Object.keys(extra).length ? JSON.stringify(extra) : null };
    }
    const extra: Record<string, any> = {
      allowed_aliases: (es_aliases || []).map((s: string) => String(s).trim()).filter(Boolean),
      allowed_index_patterns: (es_patterns || []).map((s: string) => String(s).trim()).filter(Boolean),
      verify_certs: !!es_verify_certs,
      ...gateExtra(),
    };
    if (es_timeout_sec) extra.timeout_sec = Number(es_timeout_sec);
    if (es_max_rows) extra.max_rows = Number(es_max_rows);
    if (es_pool_size) extra.pool_size = Number(es_pool_size);
    return { ...rest, extra_config: JSON.stringify(extra) };
  };

  const handleSave = async () => {
    if (isEsDbType(formValues.db_type)
        && !(formValues.es_aliases || []).length && !(formValues.es_patterns || []).length) {
      // 与后端同一口径（开放范围为空即拒建源）：前端先拦一道，不让管理员存一个永远跑不通的源
      toast.error('请至少登记一个可查索引别名（开放范围为空 = 不开放任何索引）');
      return;
    }
    try {
      const payload = buildPayload();
      if (editing) {
        await client.put(`/datasources/${editing.id}`, payload);
        toast.success('已更新');
      } else {
        await client.post('/datasources', payload);
        toast.success('已创建');
      }
      setModalOpen(false);
      loadDatasources();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '操作失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/datasources/${id}`);
      toast.success('已删除');
      loadDatasources();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleTest = async (id: number) => {
    setTesting(id);
    setTestResult(null);
    try {
      const { data } = await client.post(`/datasources/${id}/test`);
      setTestResult({ id, success: data.success, message: data.message });
      if (data.success) {
        toast.success('连接成功');
      } else {
        toast.error(data.message || '连接失败');
      }
    } catch (e: any) {
      setTestResult({ id, success: false, message: e.response?.data?.detail || '测试失败' });
      toast.error('连接测试失败');
    } finally {
      setTesting(null);
    }
  };

  const handleDbTypeChange = (dbType: string) => {
    const dbInfo = DB_TYPES.find(d => d.value === dbType);
    setFormValues({
      ...formValues,
      db_type: dbType,
      port: dbInfo?.defaultPort || 3306,
    });
  };

  return (
    <Tabs defaultValue="sources" className="space-y-4">
      <TabsList>
        <TabsTrigger value="sources"><Database className="h-4 w-4 mr-2" />{t("k_a3ccf702c5")}</TabsTrigger>
        <TabsTrigger value="sites"><Globe className="h-4 w-4 mr-2" />{t("k_f40b79d76f")}</TabsTrigger>
      </TabsList>
      <TabsContent value="sources">
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">{t("k_2bb6501b4f")}</h3>
          <p className="text-sm text-muted-foreground">管理数据库连接，支持 MySQL、Apache Doris、PostgreSQL、SLS(PG协议)、Elasticsearch</p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-2" />
          添加数据源
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner size={32} />
        </div>
      ) : datasources.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <Database className="h-14 w-14 mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium mb-1">{t("k_bb6750d7bc")}</p>
          <p className="text-sm mb-6">{t("k_6d26d3e88e")}</p>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />
            添加第一个数据源
          </Button>
        </div>
      ) : (
        <div className="space-y-8">
          {DB_CATEGORY_ORDER.map((catKey) => {
            const cat = DB_CATEGORIES[catKey];
            const CatIcon = cat.icon;
            const items = datasources.filter((ds) => dbCategoryOf(ds.db_type) === catKey);
            return (
              <section key={catKey} className="space-y-3">
                <div className="flex items-center gap-2 border-b pb-2">
                  <CatIcon className="h-4 w-4 text-primary" />
                  <h4 className="text-sm font-semibold">{cat.label}</h4>
                  <Badge variant="secondary" className="text-xs">{items.length}</Badge>
                  <span className="text-xs text-muted-foreground">{cat.desc}</span>
                </div>
                {items.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-4">{t("k_405fcd685c")}</p>
                ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {items.map((ds) => {
              const dbInfo = DB_TYPES.find(d => d.value === ds.db_type);
              const DbIcon = dbInfo?.icon || Database;
              return (
                <div
                  key={ds.id}
                  className="border rounded-lg p-4 hover:shadow-md transition-shadow"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <div className={`w-10 h-10 rounded-lg bg-muted flex items-center justify-center`}>
                        <DbIcon className={`h-5 w-5 ${dbInfo?.color || 'text-muted-foreground'}`} />
                      </div>
                      <div>
                        <h4 className="font-medium">{ds.name}</h4>
                        <div className="flex gap-1 mt-1">
                          <Badge variant="outline" className="text-xs">
                            {dbInfo?.label || ds.db_type}
                          </Badge>
                          {ds.ssl ? (
                            <Badge variant="secondary" className="text-xs">SSL</Badge>
                          ) : null}
                        </div>
                      </div>
                    </div>
                    {ds.is_default ? (
                      <Badge variant="default" className="text-xs">{t("k_c8d09cf955")}</Badge>
                    ) : null}
                  </div>

                  <div className="space-y-1 text-sm text-muted-foreground mb-4">
                    <div className="flex items-center gap-2">
                      <Globe className="h-3 w-3" />
                      <span>{ds.host}:{ds.port}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Database className="h-3 w-3" />
                      {/* ES 没有库名：这一行说开放索引别名（数量 + 首个名字），不拿「-」充数 */}
                      <span>{isEsDbType(ds.db_type)
                        ? `${esExtraOf(ds).aliases.length} 个索引别名（如 ${esExtraOf(ds).aliases[0] || '未登记'}）`
                        : (ds.database_name || '-')}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Globe className="h-3 w-3" />
                      {/* 显示站点业务名（不显示裸 code）；未站点化显式标注 */}
                      <span>{siteNameOf(sites, ds.site_code) || '未站点化'}</span>
                    </div>
                  </div>

                  {testResult && testResult.id === ds.id && (
                    <div className={`text-xs p-2 rounded mb-3 border ${
                      testResult.success
                        ? 'bg-green-500/10 text-green-400 border-green-500/20'
                        : 'bg-red-500/10 text-red-400 border-red-500/20'
                    }`}>
                      {testResult.message}
                    </div>
                  )}

                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      className="flex-1"
                      onClick={() => handleTest(ds.id)}
                      disabled={testing === ds.id}
                    >
                      {testing === ds.id ? (
                        <Spinner className="h-3 w-3 mr-1" />
                      ) : (
                        <Wifi className="h-3 w-3 mr-1" />
                      )}
                      测试连接
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label={`编辑数据源 ${ds.name}`}
                      onClick={() => openEdit(ds)}
                    >
                      <Edit className="h-3 w-3" />
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label={`删除数据源 ${ds.name}`}
                      onClick={() => handleDelete(ds.id)}
                    >
                      <Trash2 className="h-3 w-3 text-destructive" />
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
                )}
              </section>
            );
          })}
        </div>
      )}

      {/* Add/Edit Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[500px]">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑数据源' : '添加数据源'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>数据源名称 *</Label>
              <Input
                value={formValues.name}
                onChange={(e) => setFormValues({ ...formValues, name: e.target.value })}
                placeholder={t("k_bd20d81092")}
              />
            </div>

            <div className="space-y-2">
              <Label>数据库类型 *</Label>
              <Select
                value={formValues.db_type}
                onValueChange={handleDbTypeChange}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DB_CATEGORY_ORDER.map((catKey) => {
                    const cat = DB_CATEGORIES[catKey];
                    const dbs = DB_TYPES.filter(d => d.category === catKey);
                    if (!dbs.length) return null;
                    return (
                      <SelectGroup key={catKey}>
                        <SelectLabel>{cat.label}</SelectLabel>
                        {dbs.map((db) => {
                          const Icon = db.icon;
                          return (
                            <SelectItem key={db.value} value={db.value}>
                              <div className="flex items-center gap-2">
                                <Icon className={`h-4 w-4 ${db.color}`} />
                                {db.label}
                              </div>
                            </SelectItem>
                          );
                        })}
                      </SelectGroup>
                    );
                  })}
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div className="col-span-2 space-y-2">
                <Label>主机地址 *</Label>
                <Input
                  value={formValues.host}
                  onChange={(e) => setFormValues({ ...formValues, host: e.target.value })}
                  placeholder="localhost"
                />
              </div>
              <div className="space-y-2">
                <Label>端口 *</Label>
                <Input
                  type="number"
                  value={formValues.port}
                  onChange={(e) => setFormValues({ ...formValues, port: parseInt(e.target.value) || 0 })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>用户名 *</Label>
                <Input
                  value={formValues.username}
                  onChange={(e) => setFormValues({ ...formValues, username: e.target.value })}
                  placeholder="root"
                />
              </div>
              <div className="space-y-2">
                <Label>密码 {editing ? '(留空不修改)' : '*'}</Label>
                <Input
                  type="password"
                  value={formValues.password}
                  onChange={(e) => setFormValues({ ...formValues, password: e.target.value })}
                  placeholder="••••••"
                />
              </div>
            </div>

            {/* ES 没有 database 概念：“库”这一层 = 已登记的索引别名/模式（开放范围） */}
            {isEsDbType(formValues.db_type) ? (
              <>
                <EsTagField
                  label="可查索引别名 *"
                  placeholder="如：t_software_app_operation_log"
                  values={formValues.es_aliases || []}
                  onChange={(v) => setFormValues({ ...formValues, es_aliases: v })}
                  hint="别名是集群侧的索引集合名，与策略/敏感登记用的表名同一套名字；不向集群展开具体索引。"
                />
                <EsTagField
                  label={t("k_1312376ae7")}
                  placeholder="如：t-t_equipment_warning_log*"
                  values={formValues.es_patterns || []}
                  onChange={(v) => setFormValues({ ...formValues, es_patterns: v })}
                  hint="与别名取并集：两种写法都能过白名单硬门（存量正文里仍有模式写法）；但去掉 t- 前缀的模式会多匹配另一族索引，按扩权拒。"
                />
                <div className="grid grid-cols-3 gap-4">
                  <div className="space-y-2">
                    <Label>{t("k_c9a694a3d0")}</Label>
                    <Input
                      type="number" min={1} max={300}
                      value={formValues.es_timeout_sec}
                      onChange={(e) => setFormValues({ ...formValues, es_timeout_sec: e.target.value })}
                      placeholder={t("k_601f811175")}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t("k_1bb6b96c43")}</Label>
                    <Input
                      type="number" min={1}
                      value={formValues.es_max_rows}
                      onChange={(e) => setFormValues({ ...formValues, es_max_rows: e.target.value })}
                      placeholder={t("k_4a4d717dac")}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t("k_c2a8930585")}</Label>
                    <Input
                      type="number" min={1} max={64}
                      value={formValues.es_pool_size}
                      onChange={(e) => setFormValues({ ...formValues, es_pool_size: e.target.value })}
                      placeholder={t("k_08abec272d")}
                    />
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  预算按源配（不同源体量与链路不同）：超时 1~300 秒、连接池 1~64。
                  连接池 = 对本集群复用的 HTTP 连接数，看板一次刷新多张图不再各自重做 TLS 握手；
                  池只管复用不管限流（池满时仍会新建连接），“同时最多多少条查询”由下面的并发上限控。
                </p>
                <QueryGateFields
                  values={formValues}
                  onPatch={(patch) => setFormValues({ ...formValues, ...patch })}
                />
                <p className="text-xs text-muted-foreground">
                  Elasticsearch 源支持行级/列级策略（行过滤以 AND 写进顶层 WHERE，列脱敏编译进 SQL）；
                  hash 型脱敏本引擎没有对应函数，只能选 partial 或将数据物化到支持哈希的引擎。
                </p>
              </>
            ) : (
              <>
                <div className="space-y-2">
                  <Label>{t("k_9b26d2cb8b")}</Label>
                  <Input
                    value={formValues.database_name}
                    onChange={(e) => setFormValues({ ...formValues, database_name: e.target.value })}
                    placeholder={t("k_8a98ff0618")}
                  />
                </div>
                {/* 高级配置：这两个预算过去写死在代码里（不同源要给不同预算只能改代码），现按数据源可配 */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t("k_6a7f252fd9")}</Label>
                    <Input
                      type="number" min={1} max={120}
                      value={formValues.sql_connect_timeout_sec}
                      onChange={(e) => setFormValues({ ...formValues, sql_connect_timeout_sec: e.target.value })}
                      placeholder={t("k_08abec272d")}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t("k_c9a694a3d0")}</Label>
                    <Input
                      type="number" min={1} max={600}
                      value={formValues.sql_read_timeout_sec}
                      onChange={(e) => setFormValues({ ...formValues, sql_read_timeout_sec: e.target.value })}
                      placeholder={t("k_a956e3f1c4")}
                    />
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  跨境/高延迟源可适当拉高；两项都清空 = 回到默认（连接 10 秒 / 查询 30 秒）。
                  查询超时在源侧强制执行（MySQL/Doris 走 socket read_timeout，PG/SLS 下发 statement_timeout）。
                </p>
                {/* 超时只管“单条查询多久”，不管“同时多少条”：后者才是源侧连接数/配额被打爆的原因 */}
                <QueryGateFields
                  values={formValues}
                  onPatch={(patch) => setFormValues({ ...formValues, ...patch })}
                />
              </>
            )}

            {/* 站点归属：决定该源在哪个查询站点下可被选中（聊天站点选择器/本体适用站点路由） */}
            <div className="space-y-2">
              <Label>{t("k_0ca3301e7e")}</Label>
              <Select
                value={formValues.site_code || '__none__'}
                onValueChange={(v) => setFormValues({ ...formValues, site_code: v === '__none__' ? '' : v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t("k_94d1041dc4")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">{t("k_39562424c8")}</SelectItem>
                  {concreteSites(sites).map((s) => (
                    <SelectItem key={s.site_code} value={s.site_code}>{s.display_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {sites.length === 0 && (
                <p className="text-xs text-muted-foreground">{t("k_9ed4ad4db3")}</p>
              )}
            </div>

            <div className="flex items-center gap-2">
              <Switch
                checked={formValues.is_default}
                onCheckedChange={(v) => setFormValues({ ...formValues, is_default: v })}
              />
              <Label>{t("k_32f03c2a60")}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>
              {editing ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
      </TabsContent>
      {/* 站点清单与数据源同属部署拓扑，共用一个菜单入口（不新增菜单页） */}
      <TabsContent value="sites">
        <SiteManagement />
      </TabsContent>
    </Tabs>
  );
}

// ── Users Tab ──────────────────────────────────────────────────────────

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  analyst: '分析师',
  viewer: '查看者',
};

const STATUS_LABELS: Record<string, { label: string; variant: 'default' | 'destructive' | 'secondary' | 'outline' }> = {
  active: { label: '正常', variant: 'default' },
  disabled: { label: '已禁用', variant: 'destructive' },
  locked: { label: '已锁定', variant: 'secondary' },
};

function UsersTab() {
  const { t } = useTranslation('ui');
  const [users, setUsers] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(DEFAULT_PAGE_SIZE);
  const [search, setSearch] = useState('');
  const [filterRole, setFilterRole] = useState('');
  const [loading, setLoading] = useState(false);
  const [roles, setRoles] = useState<Array<{ name: string; display_name: string }>>([]);

  // Modals
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [resetPwdOpen, setResetPwdOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<any>(null);

  // Form values
  const [createForm, setCreateForm] = useState<any>({ role: 'viewer' });
  const [editForm, setEditForm] = useState<any>({});
  const [resetPwdForm, setResetPwdForm] = useState<any>({});

  const loadUsers = async (p?: number, sizeOverride?: number) => {
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size: sizeOverride ?? size };
      if (search) params.search = search;
      if (filterRole) params.role = filterRole;
      const { data } = await client.get('/users/', { params });
      const items = (data.items || []).map((u: any) => ({ ...u, role: u.user_role ?? u.role }));
      setUsers(items);
      setTotal(data.total || 0);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadUsers(); }, []);

  // 角色下拉联动「角色权限配置」中的角色列表
  useEffect(() => {
    client.get('/roles/').then(({ data }) => {
      setRoles(Array.isArray(data)
        ? data.map((r: any) => ({ name: r.name, display_name: r.display_name }))
        : []);
    }).catch(() => { /* 列表为空时静默处理 */ });
  }, []);

  const roleLabel = (name?: string) =>
    roles.find((r) => r.name === name)?.display_name || ROLE_LABELS[name || ''] || name || '';

  const doSearch = () => {
    setPage(1);
    loadUsers(1);
  };

  // ── Create user ──
  const handleCreate = async () => {
    if (!createForm.username?.trim()) { toast.error('请输入用户名'); return; }
    if (!createForm.password) { toast.error('请输入密码'); return; }
    try {
      await client.post('/users/', createForm);
      toast.success('用户创建成功');
      setCreateOpen(false);
      setCreateForm({ role: 'viewer' });
      loadUsers();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '创建失败');
    }
  };

  // ── Edit user ──
  const openEdit = (user: any) => {
    setSelectedUser(user);
    setEditForm({
      username: user.username,
      display_name: user.display_name || '',
      email: user.email || '',
      phone: user.phone || '',
      role: user.role,
    });
    setEditOpen(true);
  };

  const handleEdit = async () => {
    try {
      await client.put(`/users/${selectedUser.id}`, editForm);
      toast.success('用户信息已更新');
      setEditOpen(false);
      loadUsers();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '更新失败');
    }
  };

  // ── Reset password ──
  const openResetPwd = (user: any) => {
    setSelectedUser(user);
    setResetPwdForm({ new_password: '' });
    setResetPwdOpen(true);
  };

  const handleResetPwd = async () => {
    if (!resetPwdForm.new_password) { toast.error('请输入新密码'); return; }
    try {
      await client.put(`/users/${selectedUser.id}/password`, resetPwdForm);
      toast.success('密码已重置');
      setResetPwdOpen(false);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '重置失败');
    }
  };

  // ── Toggle status ──
  const toggleStatus = async (user: any) => {
    const newStatus = user.status === 'active' ? 'disabled' : 'active';
    try {
      await client.put(`/users/${user.id}/status`, { status: newStatus });
      toast.success(newStatus === 'active' ? '已启用' : '已禁用');
      loadUsers();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '操作失败');
    }
  };

  // ── Delete user ──
  const openDelete = (user: any) => {
    setSelectedUser(user);
    setDeleteOpen(true);
  };

  const handleDelete = async () => {
    try {
      await client.delete(`/users/${selectedUser.id}`);
      toast.success('用户已删除');
      setDeleteOpen(false);
      loadUsers();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Input
            placeholder={t("k_801b0a1216")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            className="w-[240px]"
          />
          <Select value={filterRole || 'all'} onValueChange={(v) => { setFilterRole(v === 'all' ? '' : v); setPage(1); }}>
            <SelectTrigger className="w-[130px] h-9">
              <SelectValue placeholder={t("k_9adaf88c6d")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_9adaf88c6d")}</SelectItem>
              {roles.map((r) => (
                <SelectItem key={r.name} value={r.name}>{r.display_name || r.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={doSearch}>
            <Search className="h-4 w-4 mr-2" />
            搜索
          </Button>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <UserPlus className="h-4 w-4 mr-2" />
          添加用户
        </Button>
      </div>

      {/* Users table */}
      <div className="rounded-lg border bg-card overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-64">
            <Spinner size={32} />
          </div>
        ) : (
          <ScrollArea className="h-[600px]">
            <table className="w-full" aria-label={t("k_b3d9235f92")}>
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_a1aaf352cb")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_be4c2616b1")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_9ed627bcf6")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_9c01ad0911")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_6b26695e4d")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_62e951a692")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_12f998a88f")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_84e3802f60")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_f3ea6d345e")}</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id} className="border-b hover:bg-muted/50 transition-colors">
                    <td className="p-4 align-middle text-sm font-medium">{user.username}</td>
                    <td className="p-4 align-middle text-sm">{user.display_name || '-'}</td>
                    <td className="p-4 align-middle text-sm text-muted-foreground">{user.email || '-'}</td>
                    <td className="p-4 align-middle text-sm text-muted-foreground">{user.phone || '-'}</td>
                    <td className="p-4 align-middle">
                      <Badge variant={user.role === 'admin' ? 'destructive' : 'secondary'}>
                        {roleLabel(user.role)}
                      </Badge>
                    </td>
                    <td className="p-4 align-middle">
                      <Badge variant={STATUS_LABELS[user.status]?.variant || 'outline'}>
                        {STATUS_LABELS[user.status]?.label || user.status}
                      </Badge>
                    </td>
                    <td className="p-4 align-middle text-sm text-muted-foreground">
                      {user.last_login ? new Date(user.last_login).toLocaleString('zh-CN') : '从未登录'}
                    </td>
                    <td className="p-4 align-middle text-sm text-muted-foreground">
                      {user.created_at ? new Date(user.created_at).toLocaleString('zh-CN') : ''}
                    </td>
                    <td className="p-4 align-middle">
                      <div className="flex items-center gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openEdit(user)} title={t("k_a7f814c0a4")}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => openResetPwd(user)} title={t("k_7e422146dd")}>
                          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
                            <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                          </svg>
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => toggleStatus(user)}
                          title={user.status === 'active' ? '禁用' : '启用'}
                        >
                          {user.status === 'active' ? (
                            <svg className="h-4 w-4 text-destructive" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <circle cx="12" cy="12" r="10"/>
                              <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
                            </svg>
                          ) : (
                            <svg className="h-4 w-4 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                              <polyline points="22 4 12 14.01 9 11.01"/>
                            </svg>
                          )}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => openDelete(user)} title={t("k_3755f56f2f")}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {users.length === 0 && (
                  <tr>
                    <td colSpan={9} className="p-8 text-center text-muted-foreground">
                      暂无用户数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </ScrollArea>
        )}

        <PaginationBar
          page={page}
          pageSize={size}
          total={total}
          onChange={(p: number) => { setPage(p); loadUsers(p); }}
          onPageSizeChange={(n: number) => { setSize(n); setPage(1); loadUsers(1, n); }}
          hideWhenSingle
          className="rounded-b-lg"
        />
      </div>

      {/* ── Create User Modal ── */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-[480px]">
          <DialogHeader>
            <DialogTitle>{t("k_4f965d4969")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>用户名 *</Label>
                <Input
                  placeholder={t("k_57553a09bb")}
                  value={createForm.username || ''}
                  onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("k_500d9eb940")}</Label>
                <Input
                  placeholder={t("k_51158d12b6")}
                  value={createForm.display_name || ''}
                  onChange={(e) => setCreateForm({ ...createForm, display_name: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>密码 *</Label>
              <Input
                type="password"
                placeholder={t("k_31273e6ecf")}
                value={createForm.password || ''}
                onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_ce48cbb709")}</Label>
                <Input
                  type="email"
                  placeholder="user@example.com"
                  value={createForm.email || ''}
                  onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("k_9c01ad0911")}</Label>
                <Input
                  placeholder={t("k_9f6834dc95")}
                  value={createForm.phone || ''}
                  onChange={(e) => setCreateForm({ ...createForm, phone: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("k_6b26695e4d")}</Label>
              <Select value={createForm.role || 'viewer'} onValueChange={(v) => setCreateForm({ ...createForm, role: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {roles.map((r) => (
                    <SelectItem key={r.name} value={r.name}>{r.display_name || r.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleCreate}>{t("k_fcbd093292")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Edit User Modal ── */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-[480px]">
          <DialogHeader>
            <DialogTitle>{t("k_2b8f00100b")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_a1aaf352cb")}</Label>
                <Input
                  value={editForm.username || ''}
                  onChange={(e) => setEditForm({ ...editForm, username: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("k_500d9eb940")}</Label>
                <Input
                  placeholder={t("k_51158d12b6")}
                  value={editForm.display_name || ''}
                  onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_ce48cbb709")}</Label>
                <Input
                  type="email"
                  value={editForm.email || ''}
                  onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("k_9c01ad0911")}</Label>
                <Input
                  value={editForm.phone || ''}
                  onChange={(e) => setEditForm({ ...editForm, phone: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("k_6b26695e4d")}</Label>
              <Select value={editForm.role || 'viewer'} onValueChange={(v) => setEditForm({ ...editForm, role: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {roles.map((r) => (
                    <SelectItem key={r.name} value={r.name}>{r.display_name || r.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleEdit}>{t("k_fadf24dbc5")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Reset Password Modal ── */}
      <Dialog open={resetPwdOpen} onOpenChange={setResetPwdOpen}>
        <DialogContent className="max-w-[400px]">
          <DialogHeader>
            <DialogTitle>{t("k_7e422146dd")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              为用户 <strong>{selectedUser?.username}</strong> 设置新密码
            </p>
            <div className="space-y-2">
              <Label>新密码 *</Label>
              <Input
                type="password"
                placeholder={t("k_31273e6ecf")}
                value={resetPwdForm.new_password || ''}
                onChange={(e) => setResetPwdForm({ ...resetPwdForm, new_password: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setResetPwdOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleResetPwd}>{t("k_30a6079ce3")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete User Confirm Modal ── */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-[400px]">
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要删除用户 <strong>{selectedUser?.username}</strong> 吗？此操作不可撤销。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t("k_3c06abe116")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Brand Settings Tab ─────────────────────────────────────────────────

function BrandSettingsTab() {
  const { t } = useTranslation('ui');
  const [brand, setBrand] = useState<BrandSettings>(DEFAULT_BRAND);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fetchBrand = useBrandStore(s => s.fetchBrand);
  const updateBrand = useBrandStore(s => s.updateBrand);

  useEffect(() => {
    setLoading(true);
    // 读取也走 brandStore：保存后全端（侧栏/顶栏/标签）与本页表单同一份品牌快照
    fetchBrand()
      .then(() => setBrand(useBrandStore.getState().brand))
      .finally(() => setLoading(false));
  }, [fetchBrand]);

  const handleSave = async () => {
    setSaving(true);
    try {
      // updateBrand 内部已 PUT + 刷新全端品牌快照 + 写回标签标题与图标，本页不再重复实现
      const saved = await updateBrand(brand);
      setBrand(saved);
      toast.success('品牌设置已保存');
    } catch {
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleLogoUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Max 512KB
    if (file.size > 512 * 1024) {
      toast.error('图标文件不能超过 512KB');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setBrand({ ...brand, logo_url: reader.result as string });
    };
    reader.readAsDataURL(file);
  };

  const clearLogo = () => {
    setBrand({ ...brand, logo_url: '' });
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-[560px]">
      <div>
        <h3 className="text-lg font-semibold">{t("k_645d9daf0b")}</h3>
        <p className="text-sm text-muted-foreground">{t("k_2c4cee2e80")}</p>
      </div>

      {/* Logo preview */}
      <div className="flex items-center gap-4 p-4 rounded-lg border bg-muted/30">
        <BrandLockup
          brand={brand}
          size={32}
          nameClassName="text-xl leading-tight"
          taglineClassName="text-xs leading-tight"
        />
        <span className="text-xs text-muted-foreground ml-auto shrink-0">{t("k_953f44ecac")}</span>
      </div>

      {/* App name */}
      <div className="space-y-2">
        <Label>{t("k_2d87d51825")}</Label>
        <Input
          value={brand.app_name}
          onChange={(e) => setBrand({ ...brand, app_name: e.target.value })}
          placeholder="AI-DataHub"
        />
        <p className="text-xs text-muted-foreground">显示在左上角、登录页与浏览器标签页标题（固定格式「{buildAppTitle(brand.app_name || DEFAULT_APP_NAME)}」），标签页图标上的字母也由此名称派生</p>
      </div>

      {/* Tagline */}
      <div className="space-y-2">
        <Label>{t("k_90943311b6")}</Label>
        <Input
          value={brand.tagline}
          onChange={(e) => setBrand({ ...brand, tagline: e.target.value })}
          placeholder={DEFAULT_TAGLINE}
          maxLength={60}
        />
        <p className="text-xs text-muted-foreground">{t("k_6f7ea0f666")}</p>
      </div>

      {/* Logo upload */}
      <div className="space-y-2">
        <Label>{t("k_f93d423da3")}</Label>
        <div className="flex items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleLogoUpload}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload className="h-4 w-4 mr-2" />
            上传图标
          </Button>
          {brand.logo_url && (
            <Button variant="ghost" size="sm" onClick={clearLogo}>
              <XIcon className="h-4 w-4 mr-1" />
              清除
            </Button>
          )}
        </div>
        {brand.logo_url && (
          <div className="flex items-center gap-2 mt-2">
            <img src={brand.logo_url} alt="Logo preview" className="h-10 w-10 rounded border object-contain bg-white" />
            <span className="text-xs text-muted-foreground">{t("k_0b2b879cd7")}</span>
          </div>
        )}
        <p className="text-xs text-muted-foreground">支持 PNG/SVG/JPG，建议 64×64 或 128×128，不超过 512KB</p>
      </div>

      {/* Display options */}
      <div className="space-y-3">
        <Label>{t("k_6ccda90dfe")}</Label>
        <p className="text-xs text-muted-foreground">{t("k_4662a85b62")}</p>
        <div className="flex items-center gap-3">
          <Switch
            checked={brand.show_icon}
            onCheckedChange={(v) => setBrand({ ...brand, show_icon: v })}
          />
          <span className="text-sm">{t("k_e3320fc239")}</span>
        </div>
        <div className="flex items-center gap-3">
          <Switch
            checked={brand.show_text}
            onCheckedChange={(v) => setBrand({ ...brand, show_text: v })}
          />
          <span className="text-sm">{t("k_6f95d47739")}</span>
        </div>
        {!brand.show_icon && !brand.show_text && (
          <p className="text-xs text-destructive">{t("k_e039653171")}</p>
        )}
      </div>

      <Button
        onClick={handleSave}
        disabled={saving || (!brand.show_icon && !brand.show_text)}
      >
        {saving ? <Spinner className="h-4 w-4 mr-2" /> : null}
        保存设置
      </Button>

    </div>
  );
}

// ── Main Admin Page ────────────────────────────────────────────────────

// ── Relations Tab ──────────────────────────────────────────────────────

function RelationsTab() {
  const { t } = useTranslation('ui');
  const { data, total, page, size, loading, setPage, setSize, load } = usePagedData('/admin/relations');
  const [syncing, setSyncing] = useState(false);
  const [datasources, setDatasources] = useState<any[]>([]);
  const [syncDsId, setSyncDsId] = useState<number>(0);
  const [tables, setTables] = useState<any[]>([]);
  const [erCollapsed, setErCollapsed] = useState(false);
  const [erFullscreen, setErFullscreen] = useState(false);

  // 数据源筛选
  const [filterDsId, setFilterDsId] = useState<string>('');

  useEffect(() => {
    client.get('/datasources/').then(({ data }) => setDatasources(data)).catch(() => {});
  }, []);

  // 根据筛选的数据源加载表列表
  useEffect(() => {
    const params: any = { size: 9999 };
    if (filterDsId) params.datasource_id = filterDsId;
    client.get('/admin/table-info', { params }).then(({ data }) => setTables(data.items || [])).catch(() => {});
  }, [filterDsId]);

  const doSearch = (dsId?: string) => {
    setPage(1);
    const extra: Record<string, string> = {};
    const ds = dsId !== undefined ? dsId : filterDsId;
    if (ds) extra.datasource_id = ds;
    load(1, extra);
  };

  // ── ER 图操作回调 ────────────────────────────────────────────────────

  const handleERCreate = async (relation: any) => {
    const dsId = Number(filterDsId) || 0;
    if (!dsId) {
      toast.error('请先选择数据源');
      return;
    }
    try {
      await client.post('/admin/relations', { ...relation, datasource_id: dsId });
      toast.success('关联关系已创建');
      doSearch();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '创建失败');
    }
  };

  const handleERUpdate = async (id: number, relation: any) => {
    try {
      await client.put(`/admin/relations/${id}`, relation);
      toast.success('关联关系已更新');
      doSearch();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '更新失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/admin/relations/${id}`);
      toast.success('已删除');
      doSearch();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleSync = async () => {
    if (!syncDsId) {
      toast.error('请先选择要同步的数据源');
      return;
    }
    setSyncing(true);
    try {
      const { data: res } = await client.post('/admin/sync/relations', { datasource_id: syncDsId });
      if (res.success === false) {
        toast.error(res.message || '同步失败');
      } else {
        toast.success(res.message);
        doSearch();
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail || e.response?.data?.message || '同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const toggleActive = async (record: any) => {
    try {
      await client.put(`/admin/relations/${record.id}`, { is_active: !record.is_active });
      toast.success(record.is_active ? '已禁用' : '已启用');
      doSearch();
    } catch {
      toast.error('操作失败');
    }
  };

  const getDsName = (dsId: number) => {
    const ds = datasources.find((d: any) => d.id === dsId);
    return ds ? ds.name : (dsId ? `#${dsId}` : '全部');
  };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Select
            value={filterDsId || "all"}
            onValueChange={(v) => {
              const dsId = v === "all" ? "" : v;
              setFilterDsId(dsId);
              setPage(1);
              doSearch(dsId);
            }}
          >
            <SelectTrigger className="w-[180px] h-9">
              <SelectValue placeholder={t("k_ae6802c77c")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_ae6802c77c")}</SelectItem>
              {datasources.map((ds: any) => (
                <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(syncDsId)} onValueChange={(v) => setSyncDsId(Number(v))}>
            <SelectTrigger className="w-[180px] h-9">
              <SelectValue placeholder={t("k_422b9e314a")} />
            </SelectTrigger>
            <SelectContent>
              {datasources.map((ds: any) => (
                <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={handleSync} disabled={syncing}>
            {syncing ? <Spinner size={16} className="mr-2" /> : <RefreshCw className="h-4 w-4 mr-2" />}
            同步外键
          </Button>
          <Button variant="outline" onClick={() => setErCollapsed(!erCollapsed)}>
            <Link className="h-4 w-4 mr-2" />
            {erCollapsed ? '展开 ER 图' : '折叠 ER 图'}
          </Button>
          <Button variant="outline" onClick={() => setErFullscreen(true)}>
            <Maximize2 className="h-4 w-4 mr-2" />
            全屏编辑
          </Button>
        </div>
      </div>

      {/* ER Diagram (inline, collapsible) */}
      {!erCollapsed && (
        <div className="border rounded-lg overflow-hidden" style={{ height: 420 }}>
          <ERDiagram
            tables={tables}
            relations={data}
            onRelationCreate={handleERCreate}
            onRelationDelete={handleDelete}
            onRelationUpdate={handleERUpdate}
          />
        </div>
      )}

      {/* ER Diagram fullscreen overlay */}
      {erFullscreen && (
        <div className="fixed inset-0 z-50 bg-background flex flex-col">
          <div className="flex items-center justify-between px-4 py-2 border-b bg-card shrink-0">
            <div className="flex items-center gap-2">
              <Link className="h-5 w-5 text-primary" />
              <span className="font-semibold">{t("k_e7753971eb")}</span>
            </div>
            <Button variant="outline" size="sm" onClick={() => setErFullscreen(false)}>
              <Minimize2 className="h-4 w-4 mr-2" />
              退出全屏
            </Button>
          </div>
          <div className="flex-1 overflow-hidden">
            <ERDiagram
              tables={tables}
              relations={data}
              onRelationCreate={handleERCreate}
              onRelationDelete={handleDelete}
              onRelationUpdate={handleERUpdate}
            />
          </div>
        </div>
      )}

      {/* Relations list */}
      <div className="rounded-lg border bg-card overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-64">
            <Spinner size={32} />
          </div>
        ) : (
          <ScrollArea className="h-[400px]">
            <table className="w-full">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_a3ccf702c5")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_294b663c77")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_8e32143204")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_dc91aa56fb")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_442be9c497")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_167c3f094d")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_9d0d00b8f5")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_308a012113")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_412f54dc38")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_62e951a692")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_f3ea6d345e")}</th>
                </tr>
              </thead>
              <tbody>
                {data.map((row: any) => (
                  <tr key={row.id} className="border-b hover:bg-muted/50 transition-colors">
                    <td className="p-4 align-middle text-sm text-muted-foreground">{getDsName(row.datasource_id)}</td>
                    <td className="p-4 align-middle text-sm font-medium">{row.source_table}</td>
                    <td className="p-4 align-middle text-sm font-mono">{row.source_column}</td>
                    <td className="p-4 align-middle text-center">
                      <span className="text-muted-foreground">→</span>
                    </td>
                    <td className="p-4 align-middle text-sm font-medium">{row.target_table}</td>
                    <td className="p-4 align-middle text-sm font-mono">{row.target_column}</td>
                    <td className="p-4 align-middle">
                      <Badge variant={row.relation_type === 'N:N' ? 'default' : row.relation_type === '1:1' ? 'outline' : 'secondary'}>
                        {row.relation_type}
                      </Badge>
                    </td>
                    <td className="p-4 align-middle text-sm">{row.join_type}</td>
                    <td className="p-4 align-middle text-sm max-w-[200px] truncate">{row.description}</td>
                    <td className="p-4 align-middle">
                      <Switch checked={!!row.is_active} onCheckedChange={() => toggleActive(row)} />
                    </td>
                    <td className="p-4 align-middle">
                      <Button size="sm" variant="ghost" onClick={() => handleDelete(row.id)}>
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </td>
                  </tr>
                ))}
                {data.length === 0 && (
                  <tr>
                    <td colSpan={11} className="p-8 text-center text-muted-foreground">
                      暂无关联关系，请在上方 ER 图中通过连线创建
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </ScrollArea>
        )}

        <PaginationBar
          page={page}
          pageSize={size}
          total={total}
          onChange={(p: number) => { setPage(p); load(p); }}
          onPageSizeChange={(n: number) => { setSize(n); setPage(1); load(1, undefined, n); }}
          hideWhenSingle
          className="rounded-b-lg"
        />
      </div>
    </div>
  );
}


export default function Admin({ embeddedTab }: { embeddedTab?: string } = {}) {
  const { t } = useTranslation('ui');
  // If embeddedTab is provided, show only that tab content
  if (embeddedTab) {
    const tabContents: Record<string, React.ReactNode> = {
      'datasources': <DatasourceTab />,
      'metadata': <MetadataTab />,
      'relations': <RelationsTab />,
      'terms': <TermsTab />,
      'users': <UsersTab />,
      'model-config': <ModelConfigTab />,
      'brand': <BrandSettingsTab />,
      'integration': (
        <Tabs defaultValue="apps">
          <TabsList>
            <TabsTrigger value="apps">{t("k_52fa962ab3")}</TabsTrigger>
            <TabsTrigger value="logs">{t("k_d7c73dd777")}</TabsTrigger>
          </TabsList>
          <TabsContent value="apps">
            <IntegrationApps />
          </TabsContent>
          <TabsContent value="logs">
            <IntegrationLogs />
          </TabsContent>
        </Tabs>
      ),
    };
    return tabContents[embeddedTab] || null;
  }

  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6">{t("k_e976eab5d8")}</h1>
      <Tabs defaultValue="users">
        <TabsList>
          <TabsTrigger value="users">
            <Users className="h-4 w-4 mr-2" />
            用户管理
          </TabsTrigger>
          <TabsTrigger value="brand">
            <Palette className="h-4 w-4 mr-2" />
            系统设置
          </TabsTrigger>
          <TabsTrigger value="integration">
            <Plug className="h-4 w-4 mr-2" />
            集成管理
          </TabsTrigger>
        </TabsList>
        <TabsContent value="users">
          <UsersTab />
        </TabsContent>
        <TabsContent value="brand">
          <BrandSettingsTab />
        </TabsContent>
        <TabsContent value="integration">
          <Tabs defaultValue="apps">
            <TabsList>
              <TabsTrigger value="apps">{t("k_52fa962ab3")}</TabsTrigger>
              <TabsTrigger value="logs">{t("k_d7c73dd777")}</TabsTrigger>
            </TabsList>
            <TabsContent value="apps">
              <IntegrationApps />
            </TabsContent>
            <TabsContent value="logs">
              <IntegrationLogs />
            </TabsContent>
          </Tabs>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── Model Config Tab ──────────────────────────────────────────────────

interface LLMModelRow {
  id: number;
  name: string;
  provider: string;
  base_url: string;
  api_key_masked: string;
  model_name: string;
  max_tokens: number;
  supports_thinking: number;
  supports_vision: number;
  /** 能力序：1=最强，数字越大越弱/越省；99=未定档（排序沉底） */
  capability_rank: number;
  input_price_per_1k: number | string | null;
  output_price_per_1k: number | string | null;
  currency: string | null;
  is_default: number;
  is_active: number;
}

function emptyModelForm() {
  // 新登记不预设任何能力结论：多模态 false（未实测）、能力序 99（未定档）、单价留空（未定价）
  return { provider: 'openai', max_tokens: 4096, context_window: 200000,
    supports_thinking: true, supports_vision: false, capability_rank: 99,
    input_price_per_1k: null, output_price_per_1k: null, currency: 'USD' };
}

function ModelConfigTab() {
  const { t } = useTranslation('ui');
  const [models, setModels] = useState<LLMModelRow[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<LLMModelRow | null>(null);
  const [form, setForm] = useState<any>(emptyModelForm());

  useEffect(() => { loadModels(); }, []);

  const loadModels = async () => {
    try {
      const { data } = await client.get('/model-config/llm');
      setModels(Array.isArray(data) ? data : []);
    } catch {}
  };

  const openCreate = () => {
    setEditing(null);
    setForm(emptyModelForm());
    setModalOpen(true);
  };

  const openEdit = (row: LLMModelRow) => {
    setEditing(row);
    setForm({ ...row, api_key: '' }); // api_key not returned, user must re-enter
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      if (editing) {
        const updateData: any = { ...form };
        if (!updateData.api_key) delete updateData.api_key; // Don't update if empty
        await client.put(`/model-config/llm/${editing.id}`, updateData);
        toast.success('已更新');
      } else {
        await client.post('/model-config/llm', form);
        toast.success('已添加');
      }
      setModalOpen(false);
      loadModels();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/model-config/llm/${id}`);
      toast.success('已删除');
      loadModels();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleSetDefault = async (id: number) => {
    try {
      await client.put(`/model-config/llm/${id}/default`);
      toast.success('已设为默认');
      loadModels();
    } catch {
      toast.error('设置失败');
    }
  };

  return (
    <div className="space-y-6">
      {/* LLM Models */}
      <div className="rounded-lg border bg-card">
        <div className="flex items-center justify-between p-4 border-b">
          <div>
            <h3 className="text-lg font-medium">{t("k_dc95e4a9f8")}</h3>
            {/* 排序口径写在使用现场：管理员不会猜“为什么旗舰在第七行”，也不会误以为顶行就是最强 */}
            <p className="mt-1 text-xs text-muted-foreground">
              列表按「能力序」排序（1=最强，数字越大越弱越省）；99 为未定档，会沉到底部。
              “默认”只是不指定模型时的选型，不代表能力最强。能力标签由已登记的列派生（能力序/多模态/思考/单价），不另存一份。
            </p>
          </div>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />添加模型
          </Button>
        </div>
        <div className="overflow-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_3cb4ed7d9f")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_315a11512f")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_1be7ae4fc2")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_219c099b6e")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_98fd0cbd9c")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_acd8a50431")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_2daaba9438")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_62e951a692")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_f3ea6d345e")}</th>
              </tr>
            </thead>
            <tbody>
              {models.map(m => (
                <tr key={m.id} className="border-b hover:bg-muted/50">
                  <td className="px-4 py-2 text-sm">
                    {(() => {
                      // 读不到 capability_rank（旧后端未重启/未返回该列）时也要说“未定档”，
                      // 不能渲成空白——空白会被读成“这个模型没能力档位”而不是“服务端没给我数据”。
                      const rank = m.capability_rank;
                      return typeof rank === 'number' && rank <= 90
                        ? <span className="font-mono tabular-nums" title="1=最强，数字越大越弱/越省">{rank}</span>
                        : <Badge variant="outline" className="text-muted-foreground" title={t("k_87834c9795")}>{t("k_18f6424c02")}</Badge>;
                    })()}
                  </td>
                  <td className="px-4 py-2">
                    {/* 标签全部由 modelCapabilityTags 从已有列派生（多存一份 tags 就是第二个事实源） */}
                    <div className="flex flex-wrap gap-1">
                      {modelCapabilityTags(m).map(t => (
                        <Badge key={t.key} variant="outline" title={t.title}
                          className={MODEL_TAG_TONE_CLASS[t.tone]}>
                          {t.label}
                        </Badge>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-sm font-medium">{m.name}</td>
                  <td className="px-4 py-2 text-sm"><Badge variant="outline">{m.provider}</Badge></td>
                  <td className="px-4 py-2 text-sm font-mono">{m.model_name}</td>
                  <td className="px-4 py-2 text-sm font-mono max-w-[200px] truncate">{m.base_url}</td>
                  <td className="px-4 py-2 text-sm">{m.supports_thinking ? '✅' : '❌'}</td>
                  <td className="px-4 py-2">
                    {m.is_default ? <Badge className="bg-primary">{t("k_c8d09cf955")}</Badge> : <span className="text-xs text-muted-foreground">-</span>}
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-1">
                      {!m.is_default && (
                        <Button size="sm" variant="ghost" className="text-xs" onClick={() => handleSetDefault(m.id)}>
                          设为默认
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => openEdit(m)}>
                        <Edit className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => handleDelete(m.id)}>
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add/Edit Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[500px]">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑模型' : '添加模型'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>{t("k_75ae6a8a7d")}</Label>
              <Input value={form.name || ''} onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="如: GPT-4o 旗舰版" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_219c099b6e")}</Label>
                <Select value={form.provider || 'openai'} onValueChange={(v) => setForm({ ...form, provider: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="openai">OpenAI</SelectItem>
                    <SelectItem value="deepseek">DeepSeek</SelectItem>
                    <SelectItem value="zhipu">{t("k_90fd8f044d")}</SelectItem>
                    <SelectItem value="other">{t("k_1a26edf94a")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>{t("k_38719c9968")}</Label>
                <Input value={form.model_name || ''} onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                  placeholder="gpt-4o-2024-05-13" />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("k_acd8a50431")}</Label>
              <Input value={form.base_url || ''} onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1" />
            </div>
            <div className="space-y-2">
              <Label>API Key {editing && <span className="text-muted-foreground">(留空则不更新)</span>}</Label>
              <Input type="password" value={form.api_key || ''} onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder="sk-..." />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>Max Tokens</Label>
                <Input type="number" value={form.max_tokens || 4096}
                  onChange={(e) => setForm({ ...form, max_tokens: parseInt(e.target.value) || 4096 })} />
              </div>
              <div className="space-y-2">
                <Label>{t("k_3cb4ed7d9f")} <span className="text-muted-foreground">(1=最强，99=未定档)</span></Label>
                <Input type="number" value={form.capability_rank ?? 99}
                  onChange={(e) => setForm({ ...form, capability_rank: parseInt(e.target.value) || 99 })} />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <Switch checked={form.supports_thinking !== false}
                  onCheckedChange={(v) => setForm({ ...form, supports_thinking: v })} />
                <Label>{t("k_cbbe1c0087")}</Label>
              </div>
              {/* 多模态不拿默认值当“支持”：没实测过就选不上（实测脚本见 logs/probe_vision.py 口径） */}
              <div className="flex items-center gap-2">
                <Switch checked={form.supports_vision === true || form.supports_vision === 1}
                  onCheckedChange={(v) => setForm({ ...form, supports_vision: v })} />
                <Label>{t("k_b3f4b869f5")}</Label>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label>{t("k_a00bd919df")} <span className="text-muted-foreground">/1K tok</span></Label>
                <Input type="number" step="0.0001" value={form.input_price_per_1k ?? ''}
                  onChange={(e) => setForm({ ...form, input_price_per_1k: e.target.value === '' ? null : Number(e.target.value) })} />
              </div>
              <div className="space-y-2">
                <Label>{t("k_c69f558c79")} <span className="text-muted-foreground">/1K tok</span></Label>
                <Input type="number" step="0.0001" value={form.output_price_per_1k ?? ''}
                  onChange={(e) => setForm({ ...form, output_price_per_1k: e.target.value === '' ? null : Number(e.target.value) })} />
              </div>
              <div className="space-y-2">
                <Label>{t("k_a81ab5e100")}</Label>
                <Input value={form.currency || 'USD'} onChange={(e) => setForm({ ...form, currency: e.target.value })}
                  placeholder="USD / CNY" />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              单价留空就是「未定价」：能力标签与成本页会如实写未定价，不会拿模型名猜一个价格给你。
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>{t("k_fadf24dbc5")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
