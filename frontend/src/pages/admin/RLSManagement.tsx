import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { SearchSelect } from '@/components/ui/search-select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import {
  Plus, Edit, Trash2, Shield, FileText, Columns3, Rows3, Table2, X,
} from 'lucide-react';
import client from '@/api/client';
import {
  listRLSPolicies, createRLSPolicy, updateRLSPolicy, deleteRLSPolicy,
  getRLSColumnPolicies, setRLSColumnPolicies, listRLSAuditLogs,
  listDatasourceTables, listDatasourceColumns, listRoleAttrKeys,
  type RLSPolicy, type RLSAuditLog, type SelectOption,
} from '@/api/rls';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

interface Datasource { id: number; name: string; db_type?: string; }

/** 编辑上下文: 行/列/表三类策略完全分开管理(列表分页面签、表单按上下文渲染) */
type EditContext = 'row' | 'column' | 'table';

interface PolicyFormData {
  name: string;
  description: string;
  datasource_id: number;
  table_name: string;
  filter_type: 'condition' | 'user_attribute';
  filter_expr: string;
  user_attribute: string;
  is_active: boolean;
  // 列策略(纯黑名单): 每一行 = 一个被剔除/脱敏的列; 空数组 = 不屏蔽任何列
  columns: { column_name: string; access_type: 'hidden' | 'masked'; mask_pattern: string }[];
}

const DEFAULT_FORM: PolicyFormData = {
  name: '', description: '', datasource_id: 0, table_name: '',
  filter_type: 'condition', filter_expr: '', user_attribute: '', is_active: true,
  columns: [],
};

/** 页签 → 服务端 policy_type 筛选口径（both 为遗留混合类型，归入行策略清单） */
const TAB_TYPES: Record<string, string[]> = { row: ['row', 'both'], column: ['column'], table: ['table'] };

export default function RLSManagement() {
  const { t } = useTranslation('ui');
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  // 行/列/表三个页签 = 三份独立的服务端分页清单（类型筛选下推到 SQL，
  // 不得“整张清单分页 + 前端再筛”，否则每页条数与「共 N 条」永远对不上）
  const [tab, setTab] = useState('row');
  const [rowPolicies, setRowPolicies] = useState<RLSPolicy[]>([]);
  const [colPolicies, setColPolicies] = useState<RLSPolicy[]>([]);
  const [tablePolicies, setTablePolicies] = useState<RLSPolicy[]>([]);
  const [rowTotal, setRowTotal] = useState(0);
  const [colTotal, setColTotal] = useState(0);
  const [tableTotal, setTableTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editContext, setEditContext] = useState<EditContext>('row');
  const [editPolicy, setEditPolicy] = useState<RLSPolicy | null>(null);
  const [form, setForm] = useState<PolicyFormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<RLSPolicy | null>(null);

  const [newColName, setNewColName] = useState('');
  const [newColAccess, setNewColAccess] = useState<'hidden' | 'masked'>('hidden');
  // 页面级搜索框(纯前端过滤当前页清单)
  const [rowSearch, setRowSearch] = useState('');
  const [colSearch, setColSearch] = useState('');
  const [tableSearch, setTableSearch] = useState('');

  // 选择搜索框候选清单(目录元数据/角色属性): undefined=未加载, null=加载失败, 数组=成功
  const [tablesByDs, setTablesByDs] = useState<Record<number, SelectOption[] | null>>({});
  const [tablesLoadingDs, setTablesLoadingDs] = useState<number>(0);
  const [colsByTable, setColsByTable] = useState<Record<string, SelectOption[] | null>>({});
  const [colsLoadingKey, setColsLoadingKey] = useState<string>('');
  const [attrKeys, setAttrKeys] = useState<SelectOption[] | null | undefined>(undefined);

  // Audit logs
  const [auditLogs, setAuditLogs] = useState<RLSAuditLog[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditPage, setAuditPage] = useState(1);
  const [auditPageSize, setAuditPageSize] = useState(DEFAULT_PAGE_SIZE);
  // 审计行用户显示 name（ui-resource-display：不裸显 user_id）
  const [userNames, setUserNames] = useState<Record<number, string>>({});
  const [usersLoaded, setUsersLoaded] = useState(false);

  // Load datasources
  useEffect(() => {
    client.get('/datasources/').then(({ data }) => setDatasources(Array.isArray(data) ? data : [])).catch(() => {});
  }, []);

  // Load users for audit log name mapping（加载失败不拖垮页面，仅降级为占位标注）
  useEffect(() => {
    client.get('/users/', { params: { page: 1, size: 200 } })
      .then(({ data }) => {
        const items = Array.isArray(data) ? data : data?.items || [];
        const map: Record<number, string> = {};
        for (const u of items) {
          const uid = Number(u.id ?? u.user_id);
          if (uid) map[uid] = u.username || u.name || '';
        }
        setUserNames(map);
        setUsersLoaded(true);
      })
      .catch(() => setUsersLoaded(true));
  }, []);

  // Load policies split by type (行/列/表三个页面签各自独立清单; both 为遗留混合类型归入行策略)
  // 页签类型→服务端筛选口径见 TAB_TYPES：只拉当前页签的那一类，分页与计数同口径
  const loadPolicies = useCallback(async () => {
    if (tab === 'audit') return;
    setLoading(true);
    try {
      const data = await listRLSPolicies(0, undefined, undefined, page, pageSize, TAB_TYPES[tab]);
      const items = data.items || [];
      const total = data.total || 0;
      if (tab === 'column') { setColPolicies(items); setColTotal(total); }
      else if (tab === 'table') { setTablePolicies(items); setTableTotal(total); }
      else { setRowPolicies(items); setRowTotal(total); }
    } catch { toast.error(t('k_9b895c37ff')); }
    finally { setLoading(false); }
  }, [tab, page, pageSize]);

  useEffect(() => { loadPolicies(); }, [loadPolicies]);

  const loadAuditLogs = useCallback(async () => {
    try {
      const data = await listRLSAuditLogs(0, undefined, auditPage, auditPageSize);
      setAuditLogs(data.items || []);
      setAuditTotal(data.total || 0);
    } catch { toast.error(t('k_73026bb9e2')); }
  }, [auditPage, auditPageSize]);

  // 审计日志页签激活时才拉数（页签切换即刷新，避免停在旧页）
  useEffect(() => { if (tab === 'audit') loadAuditLogs(); }, [tab, loadAuditLogs]);

  // ── 选择搜索框候选清单加载 (ui-resource-display §2: 表/列/属性名不手填) ──
  // 缓存三态: undefined=未加载 / null=加载失败(显式暴露+可重试) / 数组=成功
  const loadTables = useCallback(async (dsId: number, force = false) => {
    if (!dsId) return;
    if (!force && tablesByDs[dsId] !== undefined) return;
    setTablesLoadingDs(dsId);
    try {
      const rows = await listDatasourceTables(dsId);
      setTablesByDs(m => ({ ...m, [dsId]: rows }));
    } catch {
      setTablesByDs(m => ({ ...m, [dsId]: null }));
    } finally { setTablesLoadingDs(0); }
  }, [tablesByDs]);

  const loadCols = useCallback(async (dsId: number, table: string, force = false) => {
    if (!dsId || !table) return;
    const key = `${dsId}:${table}`;
    if (!force && colsByTable[key] !== undefined) return;
    setColsLoadingKey(key);
    try {
      const rows = await listDatasourceColumns(dsId, table);
      setColsByTable(m => ({ ...m, [key]: rows }));
    } catch {
      setColsByTable(m => ({ ...m, [key]: null }));
    } finally { setColsLoadingKey(''); }
  }, [colsByTable]);

  const loadAttrs = useCallback(async (force = false) => {
    if (!force && attrKeys !== undefined) return;
    setAttrKeys(undefined as unknown as SelectOption[] | null); // 触发 loading 态
    try {
      setAttrKeys(await listRoleAttrKeys());
    } catch {
      setAttrKeys(null);
    }
  }, [attrKeys]);

  // 打开表单即预载当前数据源的表清单
  useEffect(() => {
    if (formOpen && form.datasource_id) loadTables(form.datasource_id);
  }, [formOpen, form.datasource_id, loadTables]);

  // 列策略编辑: 选定 数据源+表 后预载列清单
  useEffect(() => {
    if (formOpen && editContext === 'column' && form.datasource_id && form.table_name) {
      loadCols(form.datasource_id, form.table_name);
    }
  }, [formOpen, editContext, form.datasource_id, form.table_name, loadCols]);

  // 行策略(角色属性过滤): 打开表单时加载属性 key 清单
  useEffect(() => {
    if (formOpen && editContext === 'row' && form.filter_type === 'user_attribute') loadAttrs();
  }, [formOpen, editContext, form.filter_type, loadAttrs]);

  // Policy CRUD
  const openCreate = (ctx: EditContext) => {
    setEditPolicy(null);
    setEditContext(ctx);
    setForm({ ...DEFAULT_FORM });
    setNewColName('');
    setFormOpen(true);
  };

  const openEdit = async (policy: RLSPolicy) => {
    const ctx: EditContext = policy.policy_type === 'table' ? 'table' : policy.policy_type === 'column' ? 'column' : 'row';
    setEditPolicy(policy);
    setEditContext(ctx);
    setForm({
      name: policy.name, description: policy.description || '',
      datasource_id: policy.datasource_id, table_name: policy.table_name,
      filter_type: policy.filter_type,
      filter_expr: policy.filter_expr || '', user_attribute: policy.user_attribute || '',
      is_active: !!policy.is_active,
      columns: [],
    });
    setNewColName('');
    setFormOpen(true);
    // 列策略: 拉取黑名单明细(非法 access_type 的遗留脏数据标注后保存时会被后端拒绝, 提醒修复)
    if (ctx === 'column') {
      try {
        const cols = await getRLSColumnPolicies(policy.id);
        setForm(f => ({
          ...f,
          columns: (Array.isArray(cols) ? cols : []).map((c: any) => ({
            column_name: c.column_name,
            access_type: (c.access_type === 'masked' ? 'masked' : 'hidden') as 'hidden' | 'masked',
            mask_pattern: c.mask_pattern || '',
          })),
        }));
      } catch { toast.error(t('k_cc8d800ef7')); }
    }
  };

  const handleSave = async () => {
    if (!form.name) { toast.error(t('k_03c8ace38e')); return; }
    if (!form.datasource_id) { toast.error(t('k_9acb966d94')); return; }
    // 列策略同样需要目标表(黑名单列属于该表), 不得豁免
    if (!form.table_name) { toast.error(t('k_0e12db279f')); return; }
    if (editContext === 'row' && form.filter_type === 'user_attribute' && !form.user_attribute) {
      toast.error(t('k_b1074200ea')); return;
    }
    setSaving(true);
    try {
      const payload: Record<string, any> = {
        name: form.name, description: form.description,
        datasource_id: form.datasource_id, table_name: form.table_name,
        policy_type: editContext, is_active: form.is_active ? 1 : 0,
        workspace_id: 0,
      };
      if (editContext === 'row') {
        payload.filter_type = form.filter_type;
        payload.filter_expr = form.filter_expr;
        payload.user_attribute = form.user_attribute;
      }
      let policyId = editPolicy?.id;
      if (editPolicy) { await updateRLSPolicy(editPolicy.id, payload); }
      else { policyId = (await createRLSPolicy(payload)).id; }
      // 列策略: 保存后同事务落黑名单(全量替换; 空数组=清除全部黑名单行)
      if (editContext === 'column' && policyId) {
        await setRLSColumnPolicies(policyId, form.columns.map(c => ({
          column_name: c.column_name, access_type: c.access_type, mask_pattern: c.mask_pattern,
        })));
      }
      toast.success(editContext === 'column' ? t('k_43a8420d22') : editContext === 'table' ? t('k_ae8bb66545') : t('k_e98d9694cb'));
      setFormOpen(false); loadPolicies();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    } finally { setSaving(false); }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try { await deleteRLSPolicy(deleteTarget.id); toast.success(t('k_4b4c642b45')); setDeleteTarget(null); loadPolicies(); }
    catch { toast.error(t('k_acf0664a54')); }
  };

  const handleToggle = async (policy: RLSPolicy) => {
    try { await updateRLSPolicy(policy.id, { is_active: policy.is_active ? 0 : 1 }); loadPolicies(); }
    catch { toast.error(t('k_c2312c491e')); }
  };

  // 黑名单列增删(候选来自列清单搜索框; 已存在的列在选择框中禁选提示)
  const addColToBlacklist = () => {
    if (!newColName) { toast.error(t('k_ed29686955')); return; }
    if (form.columns.some(c => c.column_name === newColName)) { toast.error(t('k_4a28e369a1')); return; }
    setForm(f => ({ ...f, columns: [...f.columns, { column_name: newColName, access_type: newColAccess, mask_pattern: '' }] }));
    setNewColName('');
  };

  const removeColFromBlacklist = (colName: string) => {
    setForm(f => ({ ...f, columns: f.columns.filter(c => c.column_name !== colName) }));
  };

  // 页面显示资源一律用 name; 已删除数据源给可读标注(原 id 仅进 title 次要信息)
  const dsName = (id: number) => datasources.find(d => d.id === id)?.name || tr('k_32cc05509f');
  const dsTitle = (id: number) => (datasources.find(d => d.id === id) ? undefined : tr('k_377a4fc2e8', { p0: id }));
  // 审计行用户同口径：name 为主，未加载用占位，不可见用户给可读标注(原 id 仅进 title)
  const auditUserName = (uid: number) => userNames[uid] || (usersLoaded ? tr('k_1c2d3afd8b') : tr('k_300ee3dee4'));

  const renderPolicyCard = (policy: RLSPolicy, extra?: React.ReactNode) => (
    <div key={policy.id} className="border rounded-lg p-4 flex items-start justify-between">
      <div className="flex-1">
        <div className="flex items-center gap-2 mb-1">
          <span className="font-medium">{policy.name}</span>
          <Badge variant={policy.policy_type === 'both' ? 'outline' : 'secondary'}>
            {policy.policy_type === 'row' ? t('k_aff627e54b') : policy.policy_type === 'column' ? t('k_a8b880d21f')
              : policy.policy_type === 'table' ? t('k_90fa0ea225') : t('k_8f64c985d2')}
          </Badge>
          <Badge variant={policy.is_active ? 'default' : 'outline'}>{policy.is_active ? t('k_d4e9ca3dd4') : t('k_be70be5a2e')}</Badge>
        </div>
        <div className="text-sm text-muted-foreground space-y-1">
          <p>{tr('k_d580bbe805')}{' '}<strong title={dsTitle(policy.datasource_id)}>{dsName(policy.datasource_id)}</strong> {tr('k_5615aff38c')}{' '}<code>{policy.table_name}</code></p>
          {policy.filter_expr && <p>{tr('k_a6781ee3f6')}{' '}<code className="bg-muted px-1 rounded">{policy.filter_expr}</code></p>}
          {extra && <p>{extra}</p>}
          {policy.description && <p>{policy.description}</p>}
        </div>
      </div>
      <div className="flex items-center gap-2 ml-4">
        <Switch checked={!!policy.is_active} onCheckedChange={() => handleToggle(policy)} />
        <Button variant="outline" size="sm" onClick={() => openEdit(policy)}><Edit className="h-4 w-4" /></Button>
        <Button variant="outline" size="sm" onClick={() => setDeleteTarget(policy)}><Trash2 className="h-4 w-4" /></Button>
      </div>
    </div>
  );

  const renderEmpty = (msg: string, ctx: EditContext) => (
    <div className="text-center py-8 text-muted-foreground">
      <p className="mb-3">{msg}</p>
      <Button onClick={() => openCreate(ctx)} size="sm"><Plus className="h-4 w-4 mr-2" />{t("k_e42e8184fd")}</Button>
    </div>
  );

  const renderPagination = (current: number, totalItems: number, setCurrent: (p: number) => void, size = pageSize, setSize?: (n: number) => void) => {
    return (
      <PaginationBar
        page={current}
        pageSize={size}
        total={totalItems}
        onChange={setCurrent}
        onPageSizeChange={setSize}
        busy={loading}
        hideWhenSingle
        className="shrink-0 mt-2"
      />
    );
  };

  const filteredRows = rowPolicies.filter(p => !rowSearch || `${p.name} ${p.table_name}`.toLowerCase().includes(rowSearch.toLowerCase()));
  const filteredCols = colPolicies.filter(p => !colSearch || `${p.name} ${p.table_name}`.toLowerCase().includes(colSearch.toLowerCase()));
  const filteredTables = tablePolicies.filter(p => !tableSearch || `${p.name} ${p.table_name}`.toLowerCase().includes(tableSearch.toLowerCase()));

  const colOptions = (form.datasource_id && form.table_name)
    ? (colsByTable[`${form.datasource_id}:${form.table_name}`] ?? null)
    : undefined;

  return (
    <div className="flex flex-col h-full">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2 shrink-0">
        <Shield className="h-6 w-6" />
        {t('k_e284beb40c')}
      </h1>

      <Tabs value={tab} onValueChange={(v) => { setTab(v); setPage(1); setAuditPage(1); }} className="flex-1 min-h-0 flex flex-col">
        {/* self-start: flex-col 的 Tabs 会把 inline-flex 的 TabsList 拉伸成通栏色带, 页签会跑到正中 */}
        <TabsList className="shrink-0 self-start">
          <TabsTrigger value="row"><Rows3 className="h-4 w-4 mr-2" />{t("k_aff627e54b")}</TabsTrigger>
          <TabsTrigger value="column"><Columns3 className="h-4 w-4 mr-2" />{t("k_a8b880d21f")}</TabsTrigger>
          <TabsTrigger value="table"><Table2 className="h-4 w-4 mr-2" />{t("k_90fa0ea225")}</TabsTrigger>
          <TabsTrigger value="audit"><FileText className="h-4 w-4 mr-2" />{t("k_7666cd4323")}</TabsTrigger>
        </TabsList>

        {/* ── 行策略 Tab ──────────────────────────────────────── */}
        <TabsContent value="row" className="flex-1 min-h-0 flex flex-col">
          <div className="flex justify-between items-center mb-4 gap-2 shrink-0">
            <p className="text-sm text-muted-foreground shrink-0">{t('k_70ac2b1429', { p0: rowTotal })}</p>
            <div className="flex-1 max-w-xs"><Input value={rowSearch} onChange={e => setRowSearch(e.target.value)} placeholder={t("k_bf495ba6c2")} className="h-8 text-xs" /></div>
            <Button onClick={() => openCreate('row')} size="sm"><Plus className="h-4 w-4 mr-2" />{t("k_bc1cd6e26d")}</Button>
          </div>
          <div className="flex-1 min-h-0 overflow-auto">
          {loading ? <div className="text-center py-8 text-muted-foreground">{t('k_26b5bd4947')}</div>
            : filteredRows.length === 0 ? renderEmpty(tr('k_2337f23fe6'), 'row')
            : <div className="space-y-3">{filteredRows.map(p => renderPolicyCard(p))}</div>
          }
          </div>
          {renderPagination(page, rowTotal, setPage, pageSize, (n) => { setPageSize(n); setPage(1); })}
        </TabsContent>

        {/* ── 列策略 Tab (纯黑名单) ───────────────────────────── */}
        <TabsContent value="column" className="flex-1 min-h-0 flex flex-col">
          <div className="flex justify-between items-center mb-4 gap-2 shrink-0">
            <p className="text-sm text-muted-foreground shrink-0">{t('k_2b58833038', { p0: colTotal })}</p>
            <div className="flex-1 max-w-xs"><Input value={colSearch} onChange={e => setColSearch(e.target.value)} placeholder={t("k_bf495ba6c2")} className="h-8 text-xs" /></div>
            <Button onClick={() => openCreate('column')} size="sm"><Plus className="h-4 w-4 mr-2" />{t("k_54023673d4")}</Button>
          </div>
          <p className="text-xs text-muted-foreground mb-3 shrink-0">
            {t('k_2b0565f65e')}
          </p>
          <div className="flex-1 min-h-0 overflow-auto">
          {loading ? <div className="text-center py-8 text-muted-foreground">{t('k_26b5bd4947')}</div>
            : filteredCols.length === 0 ? renderEmpty(tr('k_06589593c4'), 'column')
            : <div className="space-y-3">{filteredCols.map(p => renderPolicyCard(p))}</div>
          }
          </div>
          {renderPagination(page, colTotal, setPage, pageSize, (n) => { setPageSize(n); setPage(1); })}
        </TabsContent>

        {/* ── 表策略 Tab (白名单) ─────────────────────────────── */}
        <TabsContent value="table" className="flex-1 min-h-0 flex flex-col">
          <div className="flex justify-between items-center mb-4 gap-2 shrink-0">
            <p className="text-sm text-muted-foreground shrink-0">{t('k_857536597e', { p0: tableTotal })}</p>
            <div className="flex-1 max-w-xs"><Input value={tableSearch} onChange={e => setTableSearch(e.target.value)} placeholder={t("k_bf495ba6c2")} className="h-8 text-xs" /></div>
            <Button onClick={() => openCreate('table')} size="sm"><Plus className="h-4 w-4 mr-2" />{t("k_c440add3f0")}</Button>
          </div>
          <p className="text-xs text-muted-foreground mb-3 shrink-0">
            {t('k_741d1763ea')}
          </p>
          <div className="flex-1 min-h-0 overflow-auto">
          {loading ? <div className="text-center py-8 text-muted-foreground">{t('k_26b5bd4947')}</div>
            : filteredTables.length === 0 ? renderEmpty(tr('k_fbcda27da7'), 'table')
            : <div className="space-y-3">{filteredTables.map(p => renderPolicyCard(p))}</div>
          }
          </div>
          {renderPagination(page, tableTotal, setPage, pageSize, (n) => { setPageSize(n); setPage(1); })}
        </TabsContent>

        {/* ─ 审计日志 Tab ─────────────────────────────────────── */}
        <TabsContent value="audit" className="flex-1 min-h-0 flex flex-col">
          <div className="mb-4 text-sm text-muted-foreground shrink-0">{t('k_0e1ed88ce3', { p0: auditTotal })}</div>
          <div className="flex-1 min-h-0 overflow-auto">
          {auditLogs.length === 0 ? <div className="text-center py-8 text-muted-foreground">{t("k_2c18694012")}<br /><span className="text-xs">{t("k_885907c4ae")}</span></div>
            : <div className="space-y-2">{auditLogs.map(log => {
              const actionLabels: Record<string, string> = {
                policy_create: t('k_f61c59e9e8'),
                policy_update: t('k_2cc322327f'),
                policy_delete: t('k_a0315f0b3b'),
                column_policy_update: t('k_ff21bb59e2'),
                row_filter: t('k_ef50cfccb7'),
                column_hide: t('k_1f3958d109'),
                column_mask: t('k_d630e1be38'),
                allow: t('k_948e7e19b5'),
                deny: t('k_b1f44a976e'),
              };
              return (
              <div key={log.id} className="border rounded p-3 text-sm">
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant={log.action?.includes('deny') ? 'destructive' : 'outline'}>
                    {actionLabels[log.action] || log.action}
                  </Badge>
                  <span className="text-muted-foreground text-xs">{log.created_at}</span>
                  {log.user_id > 0 && (
                    <span className="text-xs text-muted-foreground" title={tr('k_e400df6d54', { p0: log.user_id })}>
                      {t('k_f8e243c725', { p0: auditUserName(log.user_id) })}
                    </span>
                  )}
                </div>
                {log.policy_name && <p className="text-xs">{t('k_8ac0011ea5', { p0: log.policy_name })}</p>}
                {log.table_name && <p className="text-xs">{t('k_2a2c1d9230', { p0: log.table_name })}</p>}
                {(log.deny_reason || log.original_sql || log.filtered_sql) && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-muted-foreground">{t("k_faea8c1db9")}</summary>
                    <div className="mt-1 space-y-1">
                      {log.deny_reason && (
                        <p className="text-xs"><span className="text-muted-foreground">{t('k_de77c53f29')}</span> {log.deny_reason}</p>
                      )}
                      {log.original_sql && (
                        <div>
                          <p className="text-xs text-muted-foreground">{t("k_b4a2faa115")}</p>
                          <pre className="mt-1 text-xs bg-muted p-2 rounded overflow-auto max-h-32">{log.original_sql}</pre>
                        </div>
                      )}
                      {log.filtered_sql && (
                        <div>
                          <p className="text-xs text-muted-foreground">{t("k_c2f5f2c808")}</p>
                          <pre className="mt-1 text-xs bg-muted p-2 rounded overflow-auto max-h-32">{log.filtered_sql}</pre>
                        </div>
                      )}
                    </div>
                  </details>
                )}
              </div>
              );
            })}</div>
          }
          </div>
          {renderPagination(auditPage, auditTotal, setAuditPage, auditPageSize, (n) => { setAuditPageSize(n); setAuditPage(1); })}
        </TabsContent>
      </Tabs>

      {/* ── Create/Edit Dialog (按编辑上下文 行/列/表 渲染) ────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {editPolicy ? t('k_a7f814c0a4') : t('k_0cda8d1c71')}{editContext === 'row' ? t('k_aff627e54b') : editContext === 'column' ? t('k_a8b880d21f') : t('k_90fa0ea225')}
            </DialogTitle>
            <DialogDescription>
              {editContext === 'row' ? t('k_b30c1850af')
                : editContext === 'column' ? t('k_5b493fdaac')
                : t('k_bb423f20ba')}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>{t('k_adae23a521')}</Label>
              <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder={t('k_7555e4e500')} />
            </div>
            <div>
              <Label>{t('k_0e3c853488')}</Label>
              <Select value={form.datasource_id ? String(form.datasource_id) : ''}
                onValueChange={v => {
                  // 换源后原表/列选择必然失效: 主动清空下游, 不静默保留错配值
                  setForm(f => ({ ...f, datasource_id: Number(v), table_name: '', columns: [] }));
                  setNewColName('');
                }}>
                <SelectTrigger aria-label={t("k_a3ccf702c5")}><SelectValue placeholder={t("k_422b9e314a")} /></SelectTrigger>
                <SelectContent>
                  {datasources.map(ds => <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}{ds.db_type ? ` (${ds.db_type})` : ''}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            {/* 目标表: 选择搜索框(不手填); 行/列/表三个上下文共用 */}
            <div>
              <Label>{t('k_c7c8c8f4b0')}</Label>
              <SearchSelect
                ariaLabel={t('k_442be9c497')}
                value={form.table_name}
                onValueChange={v => {
                  // 换表后列黑名单必然失效: 清空, 界面可见不静默
                  setForm(f => ({ ...f, table_name: v, columns: [] }));
                  setNewColName('');
                }}
                options={form.datasource_id ? (tablesByDs[form.datasource_id] ?? []) : []}
                disabled={!form.datasource_id}
                loading={!!tablesLoadingDs || (form.datasource_id !== 0 && tablesByDs[form.datasource_id] === undefined)}
                error={form.datasource_id && tablesByDs[form.datasource_id] === null ? t('k_b19a5a889e') : null}
                onRetry={() => loadTables(form.datasource_id, true)}
                placeholder={form.datasource_id ? t('k_8534f25602') : t('k_192dc9f17f')}
              />
            </div>

            {/* ── 行策略专属: 过滤类型/表达式/属性名 ── */}
            {editContext === 'row' && (
              <>
                <div>
                  <Label>{t("k_4a3d1afbed")}</Label>
                  <Select value={form.filter_type} onValueChange={(v: any) => setForm(f => ({ ...f, filter_type: v }))}>
                    <SelectTrigger aria-label={t("k_4a3d1afbed")}><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="condition">{t("k_25290fa627")}</SelectItem>
                      <SelectItem value="user_attribute">{t("k_32ecdb1729")}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>{t("k_1048013bc1")}</Label>
                  <Textarea value={form.filter_expr} onChange={e => setForm(f => ({ ...f, filter_expr: e.target.value }))}
                    placeholder={t('k_7935cecdc8')} rows={3} />
                  <p className="text-xs text-muted-foreground mt-1">{t('k_d87d895717')}</p>
                </div>
                {form.filter_type === 'user_attribute' && (
                  <div>
                    <Label>{t("k_f18a118e3d")}</Label>
                    <SearchSelect
                      ariaLabel={t('k_f18a118e3d')}
                      value={form.user_attribute}
                      onValueChange={v => setForm(f => ({ ...f, user_attribute: v }))}
                      options={attrKeys ?? []}
                      loading={attrKeys === undefined}
                      error={attrKeys === null ? t('k_87857c6f12') : null}
                      onRetry={() => loadAttrs(true)}
                      emptyText={t('k_a46e7fc51b')}
                      placeholder={t("k_c45755d24d")}
                    />
                  </div>
                )}
              </>
            )}

            {/* ── 列策略专属: 黑名单编辑 ── */}
            {editContext === 'column' && (
              <div className="space-y-2">
                <Label>{t("k_92532d8cd3")}</Label>
                <div className="flex gap-2">
                  <div className="flex-1">
                    <SearchSelect
                      ariaLabel={t('k_56dc7e14b9')}
                      value={newColName}
                      onValueChange={setNewColName}
                      options={(colOptions ?? []).map(o => ({
                        ...o,
                        label: form.columns.some(c => c.column_name === o.value) ? t('k_cc9a32289d', { p0: o.value }) : o.value,
                      }))}
                      disabled={!form.table_name || !form.datasource_id || colOptions === null}
                      loading={!!colsLoadingKey || (form.datasource_id !== 0 && form.table_name !== '' && colOptions === undefined)}
                      error={colOptions === null ? t('k_357e2dffc9') : null}
                      onRetry={() => loadCols(form.datasource_id, form.table_name, true)}
                      placeholder={form.table_name ? t('k_7851e1eddd') : t('k_dd64b3a272')}
                      searchPlaceholder={t('k_1dfa8de609')}
                    />
                  </div>
                  <Select value={newColAccess} onValueChange={(v: any) => setNewColAccess(v)}>
                    <SelectTrigger aria-label={t("k_9e026afea1")} className="w-28"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="hidden">{t("k_bb0e7e01aa")}</SelectItem>
                      <SelectItem value="masked">{t("k_9e5bb0438d")}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button aria-label={t("k_86e5773185")} onClick={addColToBlacklist} disabled={!newColName || !!colsLoadingKey}>
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
                {form.columns.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-3 border rounded">{t("k_2be4194fa0")}</p>
                ) : (
                  <div className="space-y-2">
                    {form.columns.map(col => (
                      <div key={col.column_name} className="flex items-center justify-between border rounded px-3 py-2">
                        <div className="flex items-center gap-2">
                          <code className="text-sm">{col.column_name}</code>
                          <Badge variant={col.access_type === 'hidden' ? 'destructive' : 'secondary'}>
                            {col.access_type === 'hidden' ? t('k_bb0e7e01aa') : t('k_9e5bb0438d')}
                          </Badge>
                        </div>
                        <Button variant="ghost" size="sm" aria-label={t('k_9cb6e8d291', { p0: col.column_name })} onClick={() => removeColFromBlacklist(col.column_name)}>
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div>
              <Label>{t("k_412f54dc38")}</Label>
              <Textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} placeholder={t('k_82f803bb69')} rows={2} />
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={form.is_active} onCheckedChange={v => setForm(f => ({ ...f, is_active: v }))} />
              <Label>{t("k_fc94dc5947")}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave} disabled={saving}>{saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("k_3c06abe116")}</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">{t('k_27995de982', { p0: deleteTarget?.name })}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t("k_3755f56f2f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
