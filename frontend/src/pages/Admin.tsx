import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import {
  RefreshCw, Search, Plus, Edit, Trash2, Database, FileText, BookOpen, Users, UserPlus,
  Wifi, Globe, Cylinder, Palette, Upload, X as XIcon, BarChart3, Link, AlertTriangle, Plug,
  HelpCircle, Maximize2, Minimize2, Cpu, Workflow, MessageSquare, Bot, Server, Calculator,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
// Separator removed — unused
import client from '../api/client';
import MenuEditorTab from '../components/MenuEditorTab';
import IntegrationApps from './admin/IntegrationApps';
import ERDiagram from '../components/ERDiagram';
import IntegrationLogs from './admin/IntegrationLogs';
import PromptManager from './admin/PromptManager';

// ── Helpers ────────────────────────────────────────────────────────────

function usePagedData(apiBase: string) {
  const [data, setData] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [size] = useState(50);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<Record<string, string>>({});

  const load = useCallback(async (p?: number, extra?: Record<string, string>) => {
    // Merge stored filters with any new extra params
    const mergedFilters = extra ? { ...filters, ...extra } : filters;
    // If extra is provided, update stored filters
    if (extra) setFilters(prev => ({ ...prev, ...extra }));
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size, ...mergedFilters };
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

  return { data, total, page, size, search, loading, setPage, setSearch, load, setData, filters, resetFilters };
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
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="inline-flex items-center text-muted-foreground hover:text-foreground transition-colors">
          <HelpCircle className="h-4 w-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-80 text-sm" side="right">
        <div className="space-y-2">
          <p className="font-medium text-foreground">检索关键词填写指南</p>
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
  const { data, total, page, size, loading, setPage, load, filters, resetFilters } = usePagedData('/admin/table-info');
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
      toast.success(res.message);
      load();
    } catch {
      toast.error('同步失败');
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

  const totalPages = Math.ceil(total / size);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Select value={filterDsId || "all"} onValueChange={(v) => { const dsId = v === "all" ? "" : v; setFilterDsId(dsId); setPage(1); load(1, { table_name: searchTable, ...(dsId ? { datasource_id: dsId } : {}) }); }}>
            <SelectTrigger className="w-[160px] h-9">
              <SelectValue placeholder="全部数据源" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部数据源</SelectItem>
              {datasources.map((ds) => (
                <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="搜索表名"
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
              <SelectValue placeholder="选择数据源" />
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
            此操作将删除该数据源下的<strong>所有表信息、字段元数据和关联关系</strong>，且不可撤销。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setClearDsOpen(false)}>取消</Button>
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
            <table className="w-full" aria-label="表信息列表">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">表名</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">数据源</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">表注释</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">业务描述</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">
                    <span className="flex items-center gap-1">检索关键词 <KeywordsHelp items={TABLE_KEYWORDS_HELP} /></span>
                  </th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">领域</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">区域</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">状态</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">操作</th>
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
                          placeholder="完整业务描述"
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
                          placeholder="检索关键词"
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
                          <Button size="sm" onClick={saveEdit}>保存</Button>
                          <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>取消</Button>
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

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t">
            <span className="text-sm text-muted-foreground">共 {total} 条</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(page - 1); load(page - 1); }}>
                上一页
              </Button>
              <span className="text-sm">{page} / {totalPages}</span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => { setPage(page + 1); load(page + 1); }}>
                下一页
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Add Table Info Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[560px]">
          <DialogHeader>
            <DialogTitle>手动添加表信息</DialogTitle>
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
              <Label>数据源</Label>
              <Select
                value={String(formValues.datasource_id || 0)}
                onValueChange={(v) => setFormValues({ ...formValues, datasource_id: Number(v) })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择数据源（可选）" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">不指定</SelectItem>
                  {datasources.map((ds) => (
                    <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>表注释</Label>
              <Input
                placeholder="表的中文注释"
                value={formValues.table_comment || ''}
                onChange={(e) => setFormValues({ ...formValues, table_comment: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>业务描述</Label>
              <Textarea
                placeholder="完整的表用途描述，传给 LLM 理解表的使用方法"
                rows={2}
                value={formValues.table_business_desc || ''}
                onChange={(e) => setFormValues({ ...formValues, table_business_desc: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Label>检索关键词</Label>
                <KeywordsHelp items={TABLE_KEYWORDS_HELP} />
              </div>
              <Input
                placeholder="精简关键词，用于向量检索匹配，如: 有多少用户、用户总数"
                value={formValues.keywords || ''}
                onChange={(e) => setFormValues({ ...formValues, keywords: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">短小精炼，影响向量检索准确性。业务描述传给 LLM，不参与检索。</p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>领域标签</Label>
                <Input
                  placeholder="如: 医疗"
                  value={formValues.domain_tag || ''}
                  onChange={(e) => setFormValues({ ...formValues, domain_tag: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>区域标签</Label>
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
              <Label>启用</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleCreate}>添加</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Column Metadata Tab ───────────────────────────────────────────────

function ColumnMetadataTab() {
  const { data, total, page, size, loading, setPage, load } = usePagedData('/admin/metadata');
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

  const totalPages = Math.ceil(total / size);

  return (
    <div className="space-y-4">
      {/* Sync per-table row */}
      <div className="flex items-center gap-2 p-3 border rounded-lg bg-muted/30">
        <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
        <span className="text-sm font-medium whitespace-nowrap">同步单表字段：</span>
        <Select value={String(syncDsId)} onValueChange={(v) => setSyncDsId(Number(v))}>
          <SelectTrigger className="w-[160px] h-8">
            <SelectValue placeholder="选择数据源" />
          </SelectTrigger>
          <SelectContent>
            {datasources.map((ds) => (
              <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          placeholder="输入表名"
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
            此操作将删除该表的<strong>表信息、字段元数据和相关联的关联关系</strong>，且不可撤销。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setClearTableOpen(false)}>取消</Button>
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
              <SelectValue placeholder="全部数据源" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部数据源</SelectItem>
              {datasources.map((ds) => (
                <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="搜索表名"
            value={searchTable}
            onChange={(e) => setSearchTable(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            className="w-[160px]"
          />
          <Input
            placeholder="搜索字段名"
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
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">数据源</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">表名</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">字段</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">类型</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">字段注释</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">业务描述</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">
                    <span className="flex items-center gap-1">检索关键词 <KeywordsHelp items={COL_KEYWORDS_HELP} /></span>
                  </th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">主键</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">状态</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">操作</th>
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
                          placeholder="完整业务描述"
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
                          placeholder="检索关键词"
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
                          <Button size="sm" onClick={saveEdit}>保存</Button>
                          <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>取消</Button>
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

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t">
            <span className="text-sm text-muted-foreground">共 {total} 条</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(page - 1); load(page - 1); }}>
                上一页
              </Button>
              <span className="text-sm">{page} / {totalPages}</span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => { setPage(page + 1); load(page + 1); }}>
                下一页
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Add Column Metadata Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[560px]">
          <DialogHeader>
            <DialogTitle>手动添加字段元数据</DialogTitle>
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
                <Label>数据类型</Label>
                <Input
                  placeholder="如: VARCHAR(255)"
                  value={formValues.data_type || ''}
                  onChange={(e) => setFormValues({ ...formValues, data_type: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>数据源</Label>
                <Select
                  value={String(formValues.datasource_id || 0)}
                  onValueChange={(v) => setFormValues({ ...formValues, datasource_id: Number(v) })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择数据源（可选）" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">不指定</SelectItem>
                    {datasources.map((ds) => (
                      <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>字段注释</Label>
              <Input
                placeholder="字段的中文注释"
                value={formValues.column_comment || ''}
                onChange={(e) => setFormValues({ ...formValues, column_comment: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>业务描述</Label>
              <Textarea
                placeholder="便于 AI 理解的字段含义"
                rows={2}
                value={formValues.business_desc || ''}
                onChange={(e) => setFormValues({ ...formValues, business_desc: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Label>检索关键词</Label>
                <KeywordsHelp items={COL_KEYWORDS_HELP} />
              </div>
              <Input
                placeholder="精简关键词，用于向量检索匹配"
                value={formValues.keywords || ''}
                onChange={(e) => setFormValues({ ...formValues, keywords: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>是否主键</Label>
                <Select
                  value={formValues.is_key || 'false'}
                  onValueChange={(v) => setFormValues({ ...formValues, is_key: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="false">否</SelectItem>
                    <SelectItem value="true">是</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>是否可空</Label>
                <Select
                  value={formValues.is_nullable || 'true'}
                  onValueChange={(v) => setFormValues({ ...formValues, is_nullable: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="true">是</SelectItem>
                    <SelectItem value="false">否</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={formValues.is_active !== false}
                onCheckedChange={(v) => setFormValues({ ...formValues, is_active: v })}
              />
              <Label>启用</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleCreate}>添加</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Metadata Tab (combined) ───────────────────────────────────────────

function MetadataTab() {
  return (
    <Tabs defaultValue="table-info">
      <TabsList>
        <TabsTrigger value="table-info">表信息</TabsTrigger>
        <TabsTrigger value="columns">字段元数据</TabsTrigger>
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

// ── Templates Tab ──────────────────────────────────────────────────────

function TemplatesTab() {
  const { data, total, page, size, loading, setPage, setSearch, load } = usePagedData('/admin/templates');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [formValues, setFormValues] = useState<any>({});
  const [datasources, setDatasources] = useState<any[]>([]);

  useEffect(() => {
    client.get('/datasources/').then(({ data }) => setDatasources(data)).catch(() => {});
  }, []);

  const openCreate = () => {
    setEditing(null);
    setFormValues({ is_active: true, datasource_id: 0 });
    setModalOpen(true);
  };

  const openEdit = (record: any) => {
    setEditing(record);
    setFormValues(record);
    setModalOpen(true);
  };

  const dsNameMap = Object.fromEntries(datasources.map((ds: any) => [ds.id, ds.name]));

  const handleSave = async () => {
    try {
      if (editing) {
        await client.put(`/admin/templates/${editing.id}`, formValues);
        toast.success('已更新');
      } else {
        await client.post('/admin/templates', formValues);
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
      await client.delete(`/admin/templates/${id}`);
      toast.success('已删除');
      load();
    } catch {
      toast.error('删除失败');
    }
  };

  const totalPages = Math.ceil(total / size);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Input
          placeholder="搜索模板名称或分类"
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
          新建模板
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
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">模板ID</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">名称</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">数据源</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">分类</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">意图关键词</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">SQL</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">使用次数</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">状态</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">操作</th>
                </tr>
              </thead>
              <tbody>
                {data.map((row) => (
                  <tr key={row.id} className="border-b hover:bg-muted/50 transition-colors">
                    <td className="p-4 align-middle text-sm max-w-[160px] truncate">{row.template_id}</td>
                    <td className="p-4 align-middle text-sm max-w-[160px] truncate">{row.template_name}</td>
                    <td className="p-4 align-middle text-sm">
                      {dsNameMap[row.datasource_id] || <span className="text-muted-foreground">全局</span>}
                    </td>
                    <td className="p-4 align-middle text-sm">{row.category}</td>
                    <td className="p-4 align-middle text-sm max-w-[160px] truncate">{row.intent_keywords}</td>
                    <td className="p-4 align-middle text-sm max-w-[200px] truncate">
                      <code className="text-xs bg-muted px-1 py-0.5 rounded">{row.sql_template}</code>
                    </td>
                    <td className="p-4 align-middle text-sm">{row.usage_count}</td>
                    <td className="p-4 align-middle">
                      <Badge variant={row.is_active ? 'default' : 'secondary'}>
                        {row.is_active ? '启用' : '禁用'}
                      </Badge>
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

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t">
            <span className="text-sm text-muted-foreground">共 {total} 条</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(page - 1); load(page - 1); }}>
                上一页
              </Button>
              <span className="text-sm">{page} / {totalPages}</span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => { setPage(page + 1); load(page + 1); }}>
                下一页
              </Button>
            </div>
          </div>
        )}
      </div>

      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[640px] max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑模板' : '新建模板'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>归属数据源</Label>
              <Select
                value={String(formValues.datasource_id || 0)}
                onValueChange={(v) => setFormValues({ ...formValues, datasource_id: Number(v) })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择数据源（可选，不选则全局可用）" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">全局（不指定数据源）</SelectItem>
                  {datasources.map((ds) => (
                    <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">指定数据源后，该模板仅在查询对应数据源时匹配</p>
            </div>
            {!editing && (
              <div className="space-y-2">
                <Label>模板ID *</Label>
                <Input
                  placeholder="如: case_count_by_region"
                  value={formValues.template_id || ''}
                  onChange={(e) => setFormValues({ ...formValues, template_id: e.target.value })}
                />
              </div>
            )}
            <div className="space-y-2">
              <Label>模板名称 *</Label>
              <Input
                placeholder="如: 各区域病例数量统计"
                value={formValues.template_name || ''}
                onChange={(e) => setFormValues({ ...formValues, template_name: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>分类</Label>
                <Input
                  placeholder="如: 统计分析"
                  value={formValues.category || ''}
                  onChange={(e) => setFormValues({ ...formValues, category: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>意图关键词</Label>
                <Input
                  placeholder="逗号分隔"
                  value={formValues.intent_keywords || ''}
                  onChange={(e) => setFormValues({ ...formValues, intent_keywords: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>SQL 模板 *</Label>
              <Textarea
                placeholder="SELECT ..."
                className="font-mono text-xs"
                rows={5}
                value={formValues.sql_template || ''}
                onChange={(e) => setFormValues({ ...formValues, sql_template: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>变量说明（JSON）</Label>
              <Textarea
                placeholder='{"region": "区域后缀"}'
                className="font-mono text-xs"
                rows={2}
                value={formValues.variables || ''}
                onChange={(e) => setFormValues({ ...formValues, variables: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>描述</Label>
              <Textarea
                placeholder="模板用途说明"
                rows={2}
                value={formValues.description || ''}
                onChange={(e) => setFormValues({ ...formValues, description: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>附加规则</Label>
              <Textarea
                placeholder="此模板匹配时注入到 LLM 的附加规则"
                rows={3}
                value={formValues.rules || ''}
                onChange={(e) => setFormValues({ ...formValues, rules: e.target.value })}
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={formValues.is_active !== false}
                onCheckedChange={(v) => setFormValues({ ...formValues, is_active: v })}
              />
              <Label>启用</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Terms Tab ──────────────────────────────────────────────────────────

function TermsTab() {
  const { data, total, page, size, loading, setPage, setSearch, load } = usePagedData('/admin/terms');
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

  const totalPages = Math.ceil(total / size);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Input
          placeholder="搜索术语名称或别名"
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
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">中文名</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">英文名</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">别名</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">类型</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">目标表</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">目标字段</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">计算公式</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">描述</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">使用次数</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">状态</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">操作</th>
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

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t">
            <span className="text-sm text-muted-foreground">共 {total} 条</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(page - 1); load(page - 1); }}>
                上一页
              </Button>
              <span className="text-sm">{page} / {totalPages}</span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => { setPage(page + 1); load(page + 1); }}>
                下一页
              </Button>
            </div>
          </div>
        )}
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
                <Label>英文名</Label>
                <Input
                  placeholder="如: case"
                  value={formValues.term_en || ''}
                  onChange={(e) => setFormValues({ ...formValues, term_en: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>别名（逗号分隔）</Label>
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
                  <SelectItem value="dimension">维度</SelectItem>
                  <SelectItem value="metric">指标</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>目标表</Label>
                <Input
                  placeholder="如: dim_case"
                  value={formValues.target_table || ''}
                  onChange={(e) => setFormValues({ ...formValues, target_table: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>目标字段</Label>
                <Input
                  placeholder="如: case_code"
                  value={formValues.target_column || ''}
                  onChange={(e) => setFormValues({ ...formValues, target_column: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>计算公式（指标类型填写）</Label>
              <Input
                placeholder="如: COUNT(*)"
                className="font-mono"
                value={formValues.calculation || ''}
                onChange={(e) => setFormValues({ ...formValues, calculation: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>描述</Label>
              <Textarea
                placeholder="术语含义说明"
                rows={2}
                value={formValues.description || ''}
                onChange={(e) => setFormValues({ ...formValues, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Datasource Tab ──────────────────────────────────────────────────────

const DB_TYPES = [
  { value: 'mysql', label: 'MySQL', icon: Database, color: 'text-blue-400', defaultPort: 3306 },
  { value: 'doris', label: 'Apache Doris', icon: Cylinder, color: 'text-cyan-400', defaultPort: 9030 },
  { value: 'elasticsearch', label: 'Elasticsearch', icon: Search, color: 'text-amber-400', defaultPort: 9200 },
];

function DatasourceTab() {
  const [datasources, setDatasources] = useState<any[]>([]);
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
  });
  const [testing, setTesting] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ id: number; success: boolean; message: string } | null>(null);

  const loadDatasources = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/datasources');
      setDatasources(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadDatasources(); }, []);

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
    });
    setModalOpen(true);
  };

  const openEdit = (record: any) => {
    setEditing(record);
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
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      if (editing) {
        await client.put(`/datasources/${editing.id}`, formValues);
        toast.success('已更新');
      } else {
        await client.post('/datasources', formValues);
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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">数据源管理</h3>
          <p className="text-sm text-muted-foreground">管理数据库连接，支持 MySQL、Apache Doris、Elasticsearch</p>
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
          <p className="text-lg font-medium mb-1">暂无数据源</p>
          <p className="text-sm mb-6">添加数据源以连接您的数据库</p>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />
            添加第一个数据源
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {datasources.map((ds) => {
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
                    <Badge variant="default" className="text-xs">默认</Badge>
                  ) : null}
                </div>

                <div className="space-y-1 text-sm text-muted-foreground mb-4">
                  <div className="flex items-center gap-2">
                    <Globe className="h-3 w-3" />
                    <span>{ds.host}:{ds.port}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Database className="h-3 w-3" />
                    <span>{ds.database_name || '-'}</span>
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
                    onClick={() => openEdit(ds)}
                  >
                    <Edit className="h-3 w-3" />
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
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
                placeholder="如：生产环境 MySQL"
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
                  {DB_TYPES.map((db) => {
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

            <div className="space-y-2">
              <Label>数据库名称</Label>
              <Input
                value={formValues.database_name}
                onChange={(e) => setFormValues({ ...formValues, database_name: e.target.value })}
                placeholder="数据库名"
              />
            </div>

            <div className="flex items-center gap-2">
              <Switch
                checked={formValues.is_default}
                onCheckedChange={(v) => setFormValues({ ...formValues, is_default: v })}
              />
              <Label>设为默认数据源</Label>
            </div>

            {formValues.db_type === 'elasticsearch' && (
              <div className="flex items-center gap-2">
                <Switch
                  checked={formValues.ssl || false}
                  onCheckedChange={(v) => setFormValues({ ...formValues, ssl: v })}
                />
                <Label>使用 HTTPS (SSL)</Label>
              </div>
            )}

            {formValues.db_type === 'elasticsearch' && (
              <div className="p-3 bg-muted rounded-lg text-sm text-muted-foreground">
                <p className="font-medium mb-1">Elasticsearch 连接说明：</p>
                <ul className="list-disc list-inside space-y-1">
                  <li>主机地址填写 ES 节点地址</li>
                  <li>端口默认 9200</li>
                  <li>用户名/密码用于认证（可选）</li>
                  <li>数据库名称填写索引名称或索引模式</li>
                  <li>如果 ES 启用了 HTTPS，请开启 SSL 选项</li>
                </ul>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleSave}>
              {editing ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
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
  const [users, setUsers] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [size] = useState(20);
  const [search, setSearch] = useState('');
  const [filterRole, setFilterRole] = useState('');
  const [loading, setLoading] = useState(false);

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

  const loadUsers = async (p?: number) => {
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size };
      if (search) params.search = search;
      if (filterRole) params.role = filterRole;
      const { data } = await client.get('/auth/users', { params });
      setUsers(data.items || []);
      setTotal(data.total || 0);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadUsers(); }, []);

  const doSearch = () => {
    setPage(1);
    loadUsers(1);
  };

  // ── Create user ──
  const handleCreate = async () => {
    if (!createForm.username?.trim()) { toast.error('请输入用户名'); return; }
    if (!createForm.password) { toast.error('请输入密码'); return; }
    try {
      await client.post('/auth/users', createForm);
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
      email: user.email || '',
      phone: user.phone || '',
      role: user.role,
    });
    setEditOpen(true);
  };

  const handleEdit = async () => {
    try {
      await client.put(`/auth/users/${selectedUser.id}`, editForm);
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
      await client.put(`/auth/users/${selectedUser.id}/password`, resetPwdForm);
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
      await client.put(`/auth/users/${user.id}/status`, { status: newStatus });
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
      await client.delete(`/auth/users/${selectedUser.id}`);
      toast.success('用户已删除');
      setDeleteOpen(false);
      loadUsers();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const totalPages = Math.ceil(total / size);

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Input
            placeholder="搜索用户名/邮箱/手机"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            className="w-[240px]"
          />
          <Select value={filterRole || 'all'} onValueChange={(v) => { setFilterRole(v === 'all' ? '' : v); setPage(1); }}>
            <SelectTrigger className="w-[130px] h-9">
              <SelectValue placeholder="全部角色" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部角色</SelectItem>
              <SelectItem value="admin">管理员</SelectItem>
              <SelectItem value="analyst">分析师</SelectItem>
              <SelectItem value="viewer">查看者</SelectItem>
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
            <table className="w-full" aria-label="用户列表">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">用户名</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">邮箱</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">手机</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">角色</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">状态</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">最后登录</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">创建时间</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id} className="border-b hover:bg-muted/50 transition-colors">
                    <td className="p-4 align-middle text-sm font-medium">{user.username}</td>
                    <td className="p-4 align-middle text-sm text-muted-foreground">{user.email || '-'}</td>
                    <td className="p-4 align-middle text-sm text-muted-foreground">{user.phone || '-'}</td>
                    <td className="p-4 align-middle">
                      <Badge variant={user.role === 'admin' ? 'destructive' : 'secondary'}>
                        {ROLE_LABELS[user.role] || user.role}
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
                        <Button size="sm" variant="ghost" onClick={() => openEdit(user)} title="编辑">
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => openResetPwd(user)} title="重置密码">
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
                        <Button size="sm" variant="ghost" onClick={() => openDelete(user)} title="删除">
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {users.length === 0 && (
                  <tr>
                    <td colSpan={8} className="p-8 text-center text-muted-foreground">
                      暂无用户数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </ScrollArea>
        )}

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t">
            <span className="text-sm text-muted-foreground">共 {total} 个用户</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(page - 1); loadUsers(page - 1); }}>
                上一页
              </Button>
              <span className="text-sm">{page} / {totalPages}</span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => { setPage(page + 1); loadUsers(page + 1); }}>
                下一页
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* ── Create User Modal ── */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-[480px]">
          <DialogHeader>
            <DialogTitle>添加用户</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>用户名 *</Label>
              <Input
                placeholder="请输入用户名"
                value={createForm.username || ''}
                onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>密码 *</Label>
              <Input
                type="password"
                placeholder="至少8位，包含字母和数字"
                value={createForm.password || ''}
                onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>邮箱</Label>
                <Input
                  type="email"
                  placeholder="user@example.com"
                  value={createForm.email || ''}
                  onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>手机</Label>
                <Input
                  placeholder="手机号码"
                  value={createForm.phone || ''}
                  onChange={(e) => setCreateForm({ ...createForm, phone: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>角色</Label>
              <Select value={createForm.role || 'viewer'} onValueChange={(v) => setCreateForm({ ...createForm, role: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="viewer">查看者</SelectItem>
                  <SelectItem value="analyst">分析师</SelectItem>
                  <SelectItem value="admin">管理员</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button onClick={handleCreate}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Edit User Modal ── */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-[480px]">
          <DialogHeader>
            <DialogTitle>编辑用户</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>用户名</Label>
              <Input
                value={editForm.username || ''}
                onChange={(e) => setEditForm({ ...editForm, username: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>邮箱</Label>
                <Input
                  type="email"
                  value={editForm.email || ''}
                  onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>手机</Label>
                <Input
                  value={editForm.phone || ''}
                  onChange={(e) => setEditForm({ ...editForm, phone: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>角色</Label>
              <Select value={editForm.role || 'viewer'} onValueChange={(v) => setEditForm({ ...editForm, role: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="viewer">查看者</SelectItem>
                  <SelectItem value="analyst">分析师</SelectItem>
                  <SelectItem value="admin">管理员</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>取消</Button>
            <Button onClick={handleEdit}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Reset Password Modal ── */}
      <Dialog open={resetPwdOpen} onOpenChange={setResetPwdOpen}>
        <DialogContent className="max-w-[400px]">
          <DialogHeader>
            <DialogTitle>重置密码</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              为用户 <strong>{selectedUser?.username}</strong> 设置新密码
            </p>
            <div className="space-y-2">
              <Label>新密码 *</Label>
              <Input
                type="password"
                placeholder="至少8位，包含字母和数字"
                value={resetPwdForm.new_password || ''}
                onChange={(e) => setResetPwdForm({ ...resetPwdForm, new_password: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setResetPwdOpen(false)}>取消</Button>
            <Button onClick={handleResetPwd}>确认重置</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete User Confirm Modal ── */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-[400px]">
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要删除用户 <strong>{selectedUser?.username}</strong> 吗？此操作不可撤销。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>确认删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Embedding Model Section ───────────────────────────────────────────

function EmbeddingModelSection() {
  const [models, setModels] = useState<any[]>([]);
  const [modelInfo, setModelInfo] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [installProgress, setInstallProgress] = useState<{ percent: number; message: string; status: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchDone, setSearchDone] = useState(false);
  const [customModelId, setCustomModelId] = useState('');
  const [hfEndpoint, setHfEndpoint] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadModels = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [modelsRes, infoRes] = await Promise.all([
        client.get('/admin/embedding/models'),
        client.get('/admin/embedding'),
      ]);
      setModels(modelsRes.data || []);
      setModelInfo(infoRes.data || {});
    } catch (e: any) {
      console.error('Failed to load embedding models:', e);
      setLoadError(e.response?.data?.detail || e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadModels(); }, []);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startPolling = (modelId: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await client.get(`/admin/embedding/install/progress/${encodeURIComponent(modelId)}`);
        if (data.status === 'idle') return;
        setInstallProgress(data);
        if (data.status === 'done' || data.status === 'error') {
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          setInstallingId(null);
          if (data.status === 'done') {
            toast.success(`${modelId} 安装成功`);
            await loadModels();
          } else {
            toast.error(data.message || '安装失败');
          }
          setTimeout(() => setInstallProgress(null), 3000);
        }
      } catch {
        // ignore polling errors
      }
    }, 1000);
  };

  const handleSearchOnline = async () => {
    setSearching(true);
    try {
      const { data } = await client.post('/admin/embedding/search', { timeout: 15 });
      // Merge online results into existing list (dedup by id)
      const existingIds = new Set(models.map((m: any) => m.id));
      const online = (data.models || [])
        .filter((m: any) => !existingIds.has(m.id))
        .map((m: any) => ({ ...m, status: 'available', installed: false, active: false }));
      setModels((prev) => [...prev, ...online]);
      setSearchDone(true);
      toast.success(`找到 ${online.length} 个在线模型`);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '搜索失败');
    } finally {
      setSearching(false);
    }
  };

  const handleInstall = async (modelId: string) => {
    setInstallingId(modelId);
    setInstallProgress({ percent: 0, message: '准备下载...', status: 'downloading' });
    try {
      const payload: any = { model_id: modelId };
      if (hfEndpoint.trim()) payload.hf_endpoint = hfEndpoint.trim();
      await client.post('/admin/embedding/install', payload);
      startPolling(modelId);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '安装失败');
      setInstallingId(null);
      setInstallProgress(null);
    }
  };

  const handleInstallCustom = async () => {
    const id = customModelId.trim();
    if (!id) { toast.error('请输入模型地址'); return; }
    setInstallingId(id);
    setInstallProgress({ percent: 0, message: '准备下载...', status: 'downloading' });
    try {
      const payload: any = { model_id: id };
      if (hfEndpoint.trim()) payload.hf_endpoint = hfEndpoint.trim();
      await client.post('/admin/embedding/install', payload);
      startPolling(id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '安装失败');
      setInstallingId(null);
      setInstallProgress(null);
    }
  };

  const handleUninstall = async (modelId: string) => {
    try {
      await client.post('/admin/embedding/uninstall', { model_id: modelId });
      toast.success(`${modelId} 已卸载`);
      await loadModels();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '卸载失败');
    }
  };

  const handleSetActive = async (modelId: string) => {
    setSwitchingId(modelId);
    try {
      await client.put('/admin/embedding', { model_path: modelId });
      toast.success(`已切换到 ${modelId}`);
      await loadModels();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '切换失败');
    } finally {
      setSwitchingId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <Spinner size={24} />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-semibold">Embedding 模型</h3>
        </div>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
          <p className="text-sm text-destructive">加载模型列表失败: {loadError}</p>
          <Button size="sm" variant="outline" className="mt-2" onClick={loadModels}>重试</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">Embedding 模型</h3>
          <p className="text-sm text-muted-foreground">
            管理用于向量检索的 Embedding 模型，输出维度必须为 768（与 Doris HNSW 索引一致）
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadModels}>
          <RefreshCw className="h-4 w-4 mr-1" />
          刷新
        </Button>
      </div>

      {/* Current active model summary */}
      {modelInfo && (
        <div className="rounded-lg border p-3 bg-muted/30">
          <div className="flex items-center gap-3">
            <Badge variant={modelInfo.model_loaded ? 'default' : 'destructive'}>
              {modelInfo.model_loaded ? '运行中' : '未加载'}
            </Badge>
            <span className="text-sm font-medium">{modelInfo.model_path}</span>
            <span className="text-xs text-muted-foreground">
              {modelInfo.model_type} · {modelInfo.embedding_dim}维
            </span>
          </div>
        </div>
      )}

      {/* Search online + mirror config */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleSearchOnline}
            disabled={searching || searchDone}
          >
            {searching ? (
              <>
                <Spinner size={14} className="mr-1" />
                搜索中…
              </>
            ) : searchDone ? (
              '已搜索'
            ) : (
              <>
                <Globe className="h-4 w-4 mr-1" />
                搜索在线模型
              </>
            )}
          </Button>
          <span className="text-xs text-muted-foreground">
            从 HuggingFace 搜索更多模型（超时 15 秒）
          </span>
        </div>

        {/* Custom model install */}
        <div className="rounded-lg border p-4 bg-muted/20 space-y-3">
          <div className="text-sm font-medium">安装自定义模型</div>
          <p className="text-xs text-muted-foreground">
            支持 HuggingFace 模型 ID（如 BAAI/bge-base-zh-v1.5）、本地路径（如 /data/models/my-model）或远程镜像库地址
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">模型地址 *</Label>
              <Input
                placeholder="BAAI/bge-base-zh-v1.5 或 /data/models/my-model"
                value={customModelId}
                onChange={(e) => setCustomModelId(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleInstallCustom()}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">镜像地址（可选）</Label>
              <Input
                placeholder="https://hf-mirror.com（留空使用默认）"
                value={hfEndpoint}
                onChange={(e) => setHfEndpoint(e.target.value)}
              />
            </div>
          </div>
          <Button
            size="sm"
            onClick={handleInstallCustom}
            disabled={!customModelId.trim() || !!installingId}
          >
            {installingId === customModelId.trim() ? (
              <>
                <Spinner size={14} className="mr-1" />
                安装中…
              </>
            ) : (
              '安装'
            )}
          </Button>
          {/* Progress bar for custom model install */}
          {installingId && installProgress && installingId === customModelId.trim() && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground truncate max-w-[300px]">{installProgress.message}</span>
                <span className="font-medium text-foreground">{installProgress.percent}%</span>
              </div>
              <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    installProgress.status === 'error' ? 'bg-destructive' : 'bg-primary'
                  }`}
                  style={{ width: `${Math.max(2, installProgress.percent)}%` }}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Model cards */}
      <div className="space-y-3">
        {models.map((m) => (
          <div
            key={m.id}
            className={`rounded-lg border p-4 transition-colors ${
              m.active ? 'border-primary bg-primary/5' : 'hover:bg-muted/30'
            }`}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1 min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-sm">{m.name}</span>
                  <code className="text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                    {m.id}
                  </code>
                  {m.dim && (
                    <Badge variant={m.dim === 768 ? 'secondary' : 'destructive'} className="text-xs px-1.5 py-0">
                      {m.dim}维{m.dim !== 768 ? ' (不兼容)' : ''}
                    </Badge>
                  )}
                  {m.tags?.map((tag: string) => (
                    <Badge key={tag} variant="outline" className="text-xs px-1.5 py-0">
                      {tag}
                    </Badge>
                  ))}
                </div>
                <p className="text-sm text-muted-foreground">{m.description}</p>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  {m.downloads > 0 && <span>下载量: {(m.downloads / 1000).toFixed(0)}k</span>}
                  {m.likes > 0 && <span>👍 {m.likes}</span>}
                  {m.installed && m.local_size_display && (
                    <span>本地大小: {m.local_size_display}</span>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                {m.active ? (
                  <Badge variant="default" className="shrink-0">当前使用</Badge>
                ) : m.installed ? (
                  <>
                    <Button
                      size="sm"
                      onClick={() => handleSetActive(m.id)}
                      disabled={switchingId === m.id}
                    >
                      {switchingId === m.id ? <Spinner size={14} className="mr-1" /> : null}
                      使用
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => handleUninstall(m.id)}
                    >
                      卸载
                    </Button>
                  </>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleInstall(m.id)}
                    disabled={installingId === m.id}
                  >
                    {installingId === m.id ? (
                      <>
                        <Spinner size={14} className="mr-1" />
                        安装中…
                      </>
                    ) : (
                      '安装'
                    )}
                  </Button>
                )}
              </div>
            </div>
            {/* Progress bar for model card install */}
            {installingId === m.id && installProgress && (
              <div className="mt-3 space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground truncate max-w-[300px]">{installProgress.message}</span>
                  <span className="font-medium text-foreground">{installProgress.percent}%</span>
                </div>
                <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      installProgress.status === 'error' ? 'bg-destructive' : 'bg-primary'
                    }`}
                    style={{ width: `${Math.max(2, installProgress.percent)}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}


// ── Brand Settings Tab ─────────────────────────────────────────────────

function BrandSettingsTab() {
  const [brand, setBrand] = useState({ app_name: 'AI-DataHub', logo_url: '', show_icon: true, show_text: true });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // RAG strategy config
  const [ragStrategy, setRagStrategy] = useState('hybrid');
  const [ragSaving, setRagSaving] = useState(false);

  useEffect(() => {
    setLoading(true);
    client.get('/admin/brand')
      .then(({ data }) => setBrand(data))
      .catch(() => {})
      .finally(() => setLoading(false));
    // Load RAG strategy from system config
    client.get('/model-config/system')
      .then(({ data }) => {
        if (data?.retrieval_strategy) setRagStrategy(data.retrieval_strategy);
      })
      .catch(() => {})
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const { data } = await client.put('/admin/brand', brand);
      setBrand(data);
      toast.success('品牌设置已保存');
      // Apply title immediately
      document.title = `${data.app_name || 'AI-DataHub'} - 数据分析平台`;
      // Apply or reset favicon
      let link = document.querySelector("link[rel~='icon']") as HTMLLinkElement;
      if (!link) {
        link = document.createElement('link');
        link.rel = 'icon';
        document.head.appendChild(link);
      }
      link.href = data.logo_url || '/vite.svg';
    } catch {
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleRagSave = async () => {
    setRagSaving(true);
    try {
      await client.put('/model-config/system', {
        key: 'retrieval_strategy',
        value: ragStrategy,
      });
      toast.success('检索策略已保存');
    } catch {
      toast.error('保存失败');
    } finally {
      setRagSaving(false);
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
        <h3 className="text-lg font-semibold">品牌设置</h3>
        <p className="text-sm text-muted-foreground">配置应用左上角显示的名称和图标，图标也会同步显示为浏览器标签页图标</p>
      </div>

      {/* Logo preview */}
      <div className="flex items-center gap-4 p-4 rounded-lg border bg-muted/30">
        <div className="flex items-center gap-2">
          {brand.show_icon && brand.logo_url ? (
            <img src={brand.logo_url} alt="Logo" className="h-8 w-8 rounded object-contain" />
          ) : brand.show_icon ? (
            <div className="h-8 w-8 rounded bg-primary/10 flex items-center justify-center">
              <span className="text-sm font-bold text-primary">CB</span>
            </div>
          ) : null}
          {brand.show_text && (
            <span className="text-xl font-bold">{brand.app_name || 'AI-DataHub'}</span>
          )}
        </div>
        <span className="text-xs text-muted-foreground ml-auto">预览效果</span>
      </div>

      {/* App name */}
      <div className="space-y-2">
        <Label>应用名称</Label>
        <Input
          value={brand.app_name}
          onChange={(e) => setBrand({ ...brand, app_name: e.target.value })}
          placeholder="AI-DataHub"
        />
        <p className="text-xs text-muted-foreground">显示在左上角和浏览器标签页标题中</p>
      </div>

      {/* Logo upload */}
      <div className="space-y-2">
        <Label>应用图标</Label>
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
            <span className="text-xs text-muted-foreground">已上传图标</span>
          </div>
        )}
        <p className="text-xs text-muted-foreground">支持 PNG/SVG/JPG，建议 64×64 或 128×128，不超过 512KB</p>
      </div>

      {/* Display options */}
      <div className="space-y-3">
        <Label>显示选项</Label>
        <div className="flex items-center gap-3">
          <Switch
            checked={brand.show_icon}
            onCheckedChange={(v) => setBrand({ ...brand, show_icon: v })}
          />
          <span className="text-sm">显示图标</span>
        </div>
        <div className="flex items-center gap-3">
          <Switch
            checked={brand.show_text}
            onCheckedChange={(v) => setBrand({ ...brand, show_text: v })}
          />
          <span className="text-sm">显示文字</span>
        </div>
        {!brand.show_icon && !brand.show_text && (
          <p className="text-xs text-destructive">图标和文字至少显示一项</p>
        )}
      </div>

      <Button
        onClick={handleSave}
        disabled={saving || (!brand.show_icon && !brand.show_text)}
      >
        {saving ? <Spinner className="h-4 w-4 mr-2" /> : null}
        保存设置
      </Button>

      {/* RAG Retrieval Strategy */}
      <div className="border-t pt-6 mt-6 space-y-4">
        <div>
          <h3 className="text-lg font-semibold">RAG 检索策略</h3>
          <p className="text-sm text-muted-foreground">选择默认的元数据检索模式，用户可在聊天页面覆盖此设置</p>
        </div>
        <div className="space-y-2">
          <Label>默认检索策略</Label>
          <Select value={ragStrategy} onValueChange={setRagStrategy}>
            <SelectTrigger className="w-[280px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="hybrid">混合检索 — BM25+向量 RRF 融合（推荐）</SelectItem>
              <SelectItem value="full_table">整表检索 — 返回命中表的全部字段</SelectItem>
              <SelectItem value="column_first">字段优先 — 向量搜字段，只返回匹配字段</SelectItem>
              <SelectItem value="two_stage">两阶段 — 先选表，再筛字段</SelectItem>
              <SelectItem value="bidirectional">双向合并 — 表+字段双路召回，筛字段</SelectItem>
              <SelectItem value="graph">图检索 — 关系遍历，只返回触及的字段</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button onClick={handleRagSave} disabled={ragSaving} variant="outline">
          {ragSaving ? <Spinner className="h-4 w-4 mr-2" /> : null}
          保存检索策略
        </Button>
      </div>

    </div>
  );
}

// ── Main Admin Page ────────────────────────────────────────────────────

// ── Relations Tab ──────────────────────────────────────────────────────

function RelationsTab() {
  const { data, total, page, size, loading, setPage, load } = usePagedData('/admin/relations');
  const [syncing, setSyncing] = useState(false);
  const [datasources, setDatasources] = useState<any[]>([]);
  const [syncDsId, setSyncDsId] = useState<number>(0);
  const [tables, setTables] = useState<any[]>([]);
  const [erCollapsed, setErCollapsed] = useState(false);
  const [erFullscreen, setErFullscreen] = useState(false);

  // 数据源筛选
  const [filterDsId, setFilterDsId] = useState<string>('');

  // 计算字段状态
  const [calcFields, setCalcFields] = useState<any[]>([]);
  const [calcFormOpen, setCalcFormOpen] = useState(false);
  const [editCalcField, setEditCalcField] = useState<any>(null);
  const [calcTableName, setCalcTableName] = useState('');
  const [calcFieldName, setCalcFieldName] = useState('');
  const [calcDisplayName, setCalcDisplayName] = useState('');
  const [calcExpression, setCalcExpression] = useState('');
  const [calcDataType, setCalcDataType] = useState('number');
  const [calcDesc, setCalcDesc] = useState('');

  // M-Schema 预览
  const [mschemaOpen, setMschemaOpen] = useState(false);
  const [mschemaText, setMschemaText] = useState('');
  const [mschemaLoading, setMschemaLoading] = useState(false);

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
      toast.success(res.message);
      doSearch();
    } catch {
      toast.error('同步失败');
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

  // ── 计算字段操作 ──────────────────────────────────────────────────

  const loadCalcFields = async () => {
    try {
      // 使用通用查询获取计算字段（通过 admin API）
      const { data: res } = await client.get('/admin/calculate-fields', {
        params: { size: 9999 }
      });
      setCalcFields(res.items || res || []);
    } catch {
      // 表可能还不存在，忽略
      setCalcFields([]);
    }
  };

  useEffect(() => { loadCalcFields(); }, []);

  const openCreateCalc = () => {
    setEditCalcField(null);
    setCalcTableName('');
    setCalcFieldName('');
    setCalcDisplayName('');
    setCalcExpression('');
    setCalcDataType('number');
    setCalcDesc('');
    setCalcFormOpen(true);
  };

  const openEditCalc = (cf: any) => {
    setEditCalcField(cf);
    setCalcTableName(cf.table_name || '');
    setCalcFieldName(cf.field_name || '');
    setCalcDisplayName(cf.display_name || '');
    setCalcExpression(cf.expression || '');
    setCalcDataType(cf.data_type || 'number');
    setCalcDesc(cf.description || '');
    setCalcFormOpen(true);
  };

  const handleSaveCalc = async () => {
    if (!calcTableName || !calcFieldName || !calcExpression) {
      toast.error('请填写表名、字段名和表达式');
      return;
    }
    try {
      const payload = {
        table_name: calcTableName,
        field_name: calcFieldName,
        display_name: calcDisplayName || calcFieldName,
        expression: calcExpression,
        data_type: calcDataType,
        description: calcDesc,
      };
      if (editCalcField) {
        await client.put(`/admin/calculate-fields/${editCalcField.id}`, payload);
        toast.success('计算字段已更新');
      } else {
        await client.post('/admin/calculate-fields', payload);
        toast.success('计算字段已创建');
      }
      setCalcFormOpen(false);
      loadCalcFields();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  };

  const handleDeleteCalc = async (id: number) => {
    try {
      await client.delete(`/admin/calculate-fields/${id}`);
      toast.success('已删除');
      loadCalcFields();
    } catch {
      toast.error('删除失败');
    }
  };

  // ── M-Schema 预览 ────────────────────────────────────────────────

  const handleMschemaPreview = async () => {
    const dsId = Number(filterDsId) || 0;
    if (!dsId) {
      toast.error('请先选择数据源');
      return;
    }
    setMschemaLoading(true);
    try {
      // 基于当前表和关系生成 M-Schema 预览
      const { data: tableData } = await client.get('/admin/table-info', {
        params: { datasource_id: dsId, size: 9999 }
      });
      const { data: colData } = await client.get('/admin/metadata', {
        params: { datasource_id: dsId, size: 9999 }
      });
      const tables = tableData.items || [];
      const columns = colData.items || [];
      const relations = data || [];

      // 构建 M-Schema 文本
      let lines: string[] = [];
      for (const t of tables) {
        const tname = t.table_name;
        const tdesc = t.table_business_desc || t.table_comment || '';
        lines.push(`Table: ${tname}${tdesc ? ' -- ' + tdesc : ''}`);
        lines.push('Columns:');
        const tcols = columns.filter((c: any) => c.table_name === tname);
        for (const c of tcols) {
          const cdesc = c.business_desc || c.column_comment || '';
          lines.push(`  - ${c.column_name} (${c.data_type})${cdesc ? ' -- ' + cdesc : ''}`);
        }
        // 计算字段
        const tcalc = calcFields.filter(cf => cf.table_name === tname);
        if (tcalc.length > 0) {
          lines.push('Calculated Fields:');
          for (const cf of tcalc) {
            lines.push(`  - ${cf.field_name} = ${cf.expression}${cf.description ? ' -- ' + cf.description : ''}`);
          }
        }
        lines.push('');
      }
      // 关系
      if (relations.length > 0) {
        lines.push('Relations:');
        for (const r of relations) {
          if (r.is_active) {
            const desc = r.description ? ' -- ' + r.description : '';
            lines.push(`  - ${r.source_table}.${r.source_column} -> ${r.target_table}.${r.target_column} (${r.relation_type}, ${r.join_type} JOIN)${desc}`);
          }
        }
      }

      setMschemaText(lines.join('\n'));
      setMschemaOpen(true);
    } catch {
      toast.error('生成失败');
    } finally {
      setMschemaLoading(false);
    }
  };

  const totalPages = Math.ceil(total / size);

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
              <SelectValue placeholder="全部数据源" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部数据源</SelectItem>
              {datasources.map((ds: any) => (
                <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(syncDsId)} onValueChange={(v) => setSyncDsId(Number(v))}>
            <SelectTrigger className="w-[180px] h-9">
              <SelectValue placeholder="选择数据源" />
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
          <Button variant="outline" onClick={handleMschemaPreview} disabled={mschemaLoading}>
            <FileText className="h-4 w-4 mr-2" />
            M-Schema 预览
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
              <span className="font-semibold">ER 关系图 — 全屏编辑</span>
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
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">数据源</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">源表</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">源字段</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">关联</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">目标表</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">目标字段</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">关系类型</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">JOIN 类型</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">描述</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">状态</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">操作</th>
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

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t">
            <span className="text-sm text-muted-foreground">共 {total} 条</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(page - 1); load(page - 1); }}>
                上一页
              </Button>
              <span className="text-sm">{page} / {totalPages}</span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => { setPage(page + 1); load(page + 1); }}>
                下一页
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* ── 计算字段区域 ──────────────────────────────────────────── */}
      <div className="rounded-lg border bg-card overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b bg-muted/30">
          <div className="flex items-center gap-2">
            <Calculator className="h-4 w-4 text-primary" />
            <span className="font-medium">计算字段</span>
            <Badge variant="secondary">{calcFields.length}</Badge>
          </div>
          <Button size="sm" variant="outline" onClick={openCreateCalc}>
            <Plus className="h-4 w-4 mr-1" />
            新增
          </Button>
        </div>
        {calcFields.length === 0 ? (
          <div className="p-6 text-center text-muted-foreground text-sm">
            暂无计算字段。计算字段可在 NL2SQL 时作为派生列使用。
          </div>
        ) : (
          <ScrollArea className="max-h-[300px]">
            <table className="w-full">
              <thead>
                <tr className="border-b bg-muted/30">
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">表名</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">字段名</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">显示名</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">表达式</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">类型</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">描述</th>
                  <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">操作</th>
                </tr>
              </thead>
              <tbody>
                {calcFields.map((cf: any) => (
                  <tr key={cf.id} className="border-b hover:bg-muted/30">
                    <td className="p-3 text-sm font-medium">{cf.table_name}</td>
                    <td className="p-3 text-sm font-mono">{cf.field_name}</td>
                    <td className="p-3 text-sm">{cf.display_name}</td>
                    <td className="p-3 text-sm font-mono max-w-[200px] truncate">{cf.expression}</td>
                    <td className="p-3"><Badge variant="outline">{cf.data_type}</Badge></td>
                    <td className="p-3 text-sm text-muted-foreground max-w-[150px] truncate">{cf.description}</td>
                    <td className="p-3">
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openEditCalc(cf)}>
                          <Edit className="h-3 w-3" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => handleDeleteCalc(cf.id)}>
                          <Trash2 className="h-3 w-3 text-destructive" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
        )}
      </div>

      {/* ── 计算字段编辑对话框 ────────────────────────────────────── */}
      <Dialog open={calcFormOpen} onOpenChange={setCalcFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editCalcField ? '编辑计算字段' : '新增计算字段'}</DialogTitle>
            <DialogDescription>
              定义派生列，NL2SQL 时 LLM 可直接引用这些计算字段
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>所属表名 *</Label>
              <Input value={calcTableName} onChange={e => setCalcTableName(e.target.value)} placeholder="如: orders" />
            </div>
            <div>
              <Label>字段名 *</Label>
              <Input value={calcFieldName} onChange={e => setCalcFieldName(e.target.value)} placeholder="如: profit" />
            </div>
            <div>
              <Label>显示名称</Label>
              <Input value={calcDisplayName} onChange={e => setCalcDisplayName(e.target.value)} placeholder="如: 利润" />
            </div>
            <div>
              <Label>计算表达式 *</Label>
              <Textarea value={calcExpression} onChange={e => setCalcExpression(e.target.value)} placeholder="如: revenue - cost" rows={3} className="font-mono text-sm" />
            </div>
            <div>
              <Label>数据类型</Label>
              <Select value={calcDataType} onValueChange={setCalcDataType}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="number">数值</SelectItem>
                  <SelectItem value="string">文本</SelectItem>
                  <SelectItem value="date">日期</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>描述</Label>
              <Input value={calcDesc} onChange={e => setCalcDesc(e.target.value)} placeholder="计算逻辑说明" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCalcFormOpen(false)}>取消</Button>
            <Button onClick={handleSaveCalc}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── M-Schema 预览对话框 ──────────────────────────────────── */}
      <Dialog open={mschemaOpen} onOpenChange={setMschemaOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>M-Schema 预览</DialogTitle>
            <DialogDescription>
              当前数据源的表结构描述，NL2SQL 时 LLM 将看到类似内容
            </DialogDescription>
          </DialogHeader>
          <pre className="text-xs bg-muted p-4 rounded overflow-auto max-h-[60vh] whitespace-pre-wrap">
            {mschemaText || '(无数据，请先选择数据源)'}
          </pre>
          <DialogFooter>
            <Button variant="outline" onClick={() => {
              navigator.clipboard.writeText(mschemaText);
              toast.success('已复制到剪贴板');
            }}>
              复制
            </Button>
            <Button variant="outline" onClick={() => setMschemaOpen(false)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}


export default function Admin({ embeddedTab }: { embeddedTab?: string } = {}) {
  // If embeddedTab is provided, show only that tab content
  if (embeddedTab) {
    const tabContents: Record<string, React.ReactNode> = {
      'datasources': <DatasourceTab />,
      'metadata': <MetadataTab />,
      'relations': <RelationsTab />,
      'templates': <TemplatesTab />,
      'terms': <TermsTab />,
      'users': <UsersTab />,
      'model-config': <ModelConfigTab />,
      'brand': <BrandSettingsTab />,
      'menu-editor': <MenuEditorTab />,
      'integration': (
        <Tabs defaultValue="apps">
          <TabsList>
            <TabsTrigger value="apps">应用管理</TabsTrigger>
            <TabsTrigger value="logs">调用日志</TabsTrigger>
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
      <h1 className="text-2xl font-bold mb-6">系统设置</h1>
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
          <TabsTrigger value="menu-editor">
            <BarChart3 className="h-4 w-4 mr-2" />
            菜单编辑
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
        <TabsContent value="menu-editor">
          <MenuEditorTab />
        </TabsContent>
        <TabsContent value="integration">
          <Tabs defaultValue="apps">
            <TabsList>
              <TabsTrigger value="apps">应用管理</TabsTrigger>
              <TabsTrigger value="logs">调用日志</TabsTrigger>
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
  is_default: number;
  is_active: number;
}

function ModelConfigTab() {
  const [models, setModels] = useState<LLMModelRow[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<LLMModelRow | null>(null);
  const [form, setForm] = useState<any>({ provider: 'anthropic', max_tokens: 4096, supports_thinking: true });

  useEffect(() => { loadModels(); }, []);

  const loadModels = async () => {
    try {
      const { data } = await client.get('/model-config/llm');
      setModels(data);
    } catch {}
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ provider: 'anthropic', max_tokens: 4096, supports_thinking: true });
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
          <h3 className="text-lg font-medium">大模型配置</h3>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />添加模型
          </Button>
        </div>
        <div className="overflow-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">名称</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">提供商</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">模型</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">API 地址</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">思考模式</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">状态</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">操作</th>
              </tr>
            </thead>
            <tbody>
              {models.map(m => (
                <tr key={m.id} className="border-b hover:bg-muted/50">
                  <td className="px-4 py-2 text-sm font-medium">{m.name}</td>
                  <td className="px-4 py-2 text-sm"><Badge variant="outline">{m.provider}</Badge></td>
                  <td className="px-4 py-2 text-sm font-mono">{m.model_name}</td>
                  <td className="px-4 py-2 text-sm font-mono max-w-[200px] truncate">{m.base_url}</td>
                  <td className="px-4 py-2 text-sm">{m.supports_thinking ? '✅' : '❌'}</td>
                  <td className="px-4 py-2">
                    {m.is_default ? <Badge className="bg-primary">默认</Badge> : <span className="text-xs text-muted-foreground">-</span>}
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

      {/* Embedding Config */}
      {/* Embedding Model */}
      <EmbeddingModelSection />

      {/* Add/Edit Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[500px]">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑模型' : '添加模型'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>显示名称</Label>
              <Input value={form.name || ''} onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="如: Claude 4 Sonnet" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>提供商</Label>
                <Select value={form.provider || 'anthropic'} onValueChange={(v) => setForm({ ...form, provider: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="anthropic">Anthropic</SelectItem>
                    <SelectItem value="openai">OpenAI</SelectItem>
                    <SelectItem value="deepseek">DeepSeek</SelectItem>
                    <SelectItem value="zhipu">智谱</SelectItem>
                    <SelectItem value="other">其他</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>模型名称</Label>
                <Input value={form.model_name || ''} onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                  placeholder="claude-sonnet-4-20250514" />
              </div>
            </div>
            <div className="space-y-2">
              <Label>API 地址</Label>
              <Input value={form.base_url || ''} onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.anthropic.com" />
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
              <div className="flex items-center gap-2 pt-6">
                <Switch checked={form.supports_thinking !== false}
                  onCheckedChange={(v) => setForm({ ...form, supports_thinking: v })} />
                <Label>支持思考模式</Label>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
