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

export default function RLSManagement() {
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [rowPolicies, setRowPolicies] = useState<RLSPolicy[]>([]);
  const [colPolicies, setColPolicies] = useState<RLSPolicy[]>([]);
  const [tablePolicies, setTablePolicies] = useState<RLSPolicy[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
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

  // Load policies split by type (行/列/表三个页面签各自独立清单; both 为遗留混合类型只读)
  const loadPolicies = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listRLSPolicies(0, undefined, undefined, page);
      const items = data.items || [];
      setRowPolicies(items.filter(p => p.policy_type === 'row' || p.policy_type === 'both'));
      setColPolicies(items.filter(p => p.policy_type === 'column'));
      setTablePolicies(items.filter(p => p.policy_type === 'table'));
      setTotal(data.total || 0);
    } catch { toast.error('加载策略失败'); }
    finally { setLoading(false); }
  }, [page]);

  useEffect(() => { loadPolicies(); }, [loadPolicies]);

  const loadAuditLogs = useCallback(async () => {
    try {
      const data = await listRLSAuditLogs(0, undefined, auditPage);
      setAuditLogs(data.items || []);
      setAuditTotal(data.total || 0);
    } catch { toast.error('加载审计日志失败'); }
  }, [auditPage]);

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
      } catch { toast.error('列策略明细加载失败'); }
    }
  };

  const handleSave = async () => {
    if (!form.name) { toast.error('请填写策略名称'); return; }
    if (!form.datasource_id) { toast.error('请选择数据源'); return; }
    // 列策略同样需要目标表(黑名单列属于该表), 不得豁免
    if (!form.table_name) { toast.error('请选择目标表'); return; }
    if (editContext === 'row' && form.filter_type === 'user_attribute' && !form.user_attribute) {
      toast.error('请选择属性名'); return;
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
      toast.success(editContext === 'column' ? '列策略已保存' : editContext === 'table' ? '表策略已保存' : '行策略已保存');
      setFormOpen(false); loadPolicies();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try { await deleteRLSPolicy(deleteTarget.id); toast.success('策略已删除'); setDeleteTarget(null); loadPolicies(); }
    catch { toast.error('删除失败'); }
  };

  const handleToggle = async (policy: RLSPolicy) => {
    try { await updateRLSPolicy(policy.id, { is_active: policy.is_active ? 0 : 1 }); loadPolicies(); }
    catch { toast.error('切换状态失败'); }
  };

  // 黑名单列增删(候选来自列清单搜索框; 已存在的列在选择框中禁选提示)
  const addColToBlacklist = () => {
    if (!newColName) { toast.error('请选择列名'); return; }
    if (form.columns.some(c => c.column_name === newColName)) { toast.error('列已存在'); return; }
    setForm(f => ({ ...f, columns: [...f.columns, { column_name: newColName, access_type: newColAccess, mask_pattern: '' }] }));
    setNewColName('');
  };

  const removeColFromBlacklist = (colName: string) => {
    setForm(f => ({ ...f, columns: f.columns.filter(c => c.column_name !== colName) }));
  };

  // 页面显示资源一律用 name; 已删除数据源给可读标注(原 id 仅进 title 次要信息)
  const dsName = (id: number) => datasources.find(d => d.id === id)?.name || '数据源已删除';
  const dsTitle = (id: number) => (datasources.find(d => d.id === id) ? undefined : `原数据源 ID: ${id}`);
  // 审计行用户同口径：name 为主，未加载用占位，不可见用户给可读标注(原 id 仅进 title)
  const auditUserName = (uid: number) => userNames[uid] || (usersLoaded ? '未知用户' : '加载中…');

  const renderPolicyCard = (policy: RLSPolicy, extra?: React.ReactNode) => (
    <div key={policy.id} className="border rounded-lg p-4 flex items-start justify-between">
      <div className="flex-1">
        <div className="flex items-center gap-2 mb-1">
          <span className="font-medium">{policy.name}</span>
          <Badge variant={policy.policy_type === 'both' ? 'outline' : 'secondary'}>
            {policy.policy_type === 'row' ? '行策略' : policy.policy_type === 'column' ? '列策略'
              : policy.policy_type === 'table' ? '表策略' : '行/列(遗留)'}
          </Badge>
          <Badge variant={policy.is_active ? 'default' : 'outline'}>{policy.is_active ? '启用' : '禁用'}</Badge>
        </div>
        <div className="text-sm text-muted-foreground space-y-1">
          <p>数据源: <strong title={dsTitle(policy.datasource_id)}>{dsName(policy.datasource_id)}</strong> | 表: <code>{policy.table_name}</code></p>
          {policy.filter_expr && <p>过滤: <code className="bg-muted px-1 rounded">{policy.filter_expr}</code></p>}
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
      <Button onClick={() => openCreate(ctx)} size="sm"><Plus className="h-4 w-4 mr-2" />新建策略</Button>
    </div>
  );

  const renderPagination = (current: number, totalItems: number, setCurrent: (p: number) => void) => {
    if (totalItems <= 20) return null;
    return (
      <div className="flex justify-center gap-2 mt-4">
        <Button variant="outline" size="sm" disabled={current <= 1} onClick={() => setCurrent(current - 1)}>上一页</Button>
        <span className="py-1 px-3 text-sm">{current} / {Math.ceil(totalItems / 20)}</span>
        <Button variant="outline" size="sm" disabled={current * 20 >= totalItems} onClick={() => setCurrent(current + 1)}>下一页</Button>
      </div>
    );
  };

  const filteredRows = rowPolicies.filter(p => !rowSearch || `${p.name} ${p.table_name}`.toLowerCase().includes(rowSearch.toLowerCase()));
  const filteredCols = colPolicies.filter(p => !colSearch || `${p.name} ${p.table_name}`.toLowerCase().includes(colSearch.toLowerCase()));
  const filteredTables = tablePolicies.filter(p => !tableSearch || `${p.name} ${p.table_name}`.toLowerCase().includes(tableSearch.toLowerCase()));

  const colOptions = (form.datasource_id && form.table_name)
    ? (colsByTable[`${form.datasource_id}:${form.table_name}`] ?? null)
    : undefined;

  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
        <Shield className="h-6 w-6" />
        数据安全策略
      </h1>

      <Tabs defaultValue="row">
        <TabsList>
          <TabsTrigger value="row"><Rows3 className="h-4 w-4 mr-2" />行策略</TabsTrigger>
          <TabsTrigger value="column"><Columns3 className="h-4 w-4 mr-2" />列策略</TabsTrigger>
          <TabsTrigger value="table"><Table2 className="h-4 w-4 mr-2" />表策略</TabsTrigger>
          <TabsTrigger value="audit" onClick={() => loadAuditLogs()}><FileText className="h-4 w-4 mr-2" />审计日志</TabsTrigger>
        </TabsList>

        {/* ── 行策略 Tab ──────────────────────────────────────── */}
        <TabsContent value="row">
          <div className="flex justify-between items-center mb-4 gap-2">
            <p className="text-sm text-muted-foreground shrink-0">共 {filteredRows.length} 条行策略</p>
            <div className="flex-1 max-w-xs"><Input value={rowSearch} onChange={e => setRowSearch(e.target.value)} placeholder="搜索策略名/表名…" className="h-8 text-xs" /></div>
            <Button onClick={() => openCreate('row')} size="sm"><Plus className="h-4 w-4 mr-2" />新建行策略</Button>
          </div>
          {loading ? <div className="text-center py-8 text-muted-foreground">加载中...</div>
            : filteredRows.length === 0 ? renderEmpty('暂无行策略，点击"新建行策略"开始配置', 'row')
            : <div className="space-y-3">{filteredRows.map(p => renderPolicyCard(p))}</div>
          }
          {renderPagination(page, total, setPage)}
        </TabsContent>

        {/* ── 列策略 Tab (纯黑名单) ───────────────────────────── */}
        <TabsContent value="column">
          <div className="flex justify-between items-center mb-4 gap-2">
            <p className="text-sm text-muted-foreground shrink-0">共 {filteredCols.length} 条列策略</p>
            <div className="flex-1 max-w-xs"><Input value={colSearch} onChange={e => setColSearch(e.target.value)} placeholder="搜索策略名/表名…" className="h-8 text-xs" /></div>
            <Button onClick={() => openCreate('column')} size="sm"><Plus className="h-4 w-4 mr-2" />新建列策略</Button>
          </div>
          <p className="text-xs text-muted-foreground mb-3">
            列策略是黑名单：列出的列将被隐藏/脱敏，未列出的列默认可访问（敏感数据管理的屏蔽列对所有用户额外生效）。
          </p>
          {loading ? <div className="text-center py-8 text-muted-foreground">加载中...</div>
            : filteredCols.length === 0 ? renderEmpty('暂无列策略，点击"新建列策略"开始配置', 'column')
            : <div className="space-y-3">{filteredCols.map(p => renderPolicyCard(p))}</div>
          }
          {renderPagination(page, total, setPage)}
        </TabsContent>

        {/* ── 表策略 Tab (白名单) ─────────────────────────────── */}
        <TabsContent value="table">
          <div className="flex justify-between items-center mb-4 gap-2">
            <p className="text-sm text-muted-foreground shrink-0">共 {filteredTables.length} 条表策略</p>
            <div className="flex-1 max-w-xs"><Input value={tableSearch} onChange={e => setTableSearch(e.target.value)} placeholder="搜索策略名/表名…" className="h-8 text-xs" /></div>
            <Button onClick={() => openCreate('table')} size="sm"><Plus className="h-4 w-4 mr-2" />新建表策略</Button>
          </div>
          <p className="text-xs text-muted-foreground mb-3">
            表策略是白名单：角色一旦绑定任一表策略，该角色在此数据源下仅可访问清单内的表；未绑定表策略的角色不受表级限制（仍受角色数据源/表授权约束）。
          </p>
          {loading ? <div className="text-center py-8 text-muted-foreground">加载中...</div>
            : filteredTables.length === 0 ? renderEmpty('暂无表策略，点击"新建表策略"开始配置', 'table')
            : <div className="space-y-3">{filteredTables.map(p => renderPolicyCard(p))}</div>
          }
          {renderPagination(page, total, setPage)}
        </TabsContent>

        {/* ─ 审计日志 Tab ─────────────────────────────────────── */}
        <TabsContent value="audit">
          <div className="mb-4 text-sm text-muted-foreground">共 {auditTotal} 条审计记录</div>
          {auditLogs.length === 0 ? <div className="text-center py-8 text-muted-foreground">暂无审计记录<br /><span className="text-xs">提示：创建/修改/删除策略时将自动记录审计日志</span></div>
            : <div className="space-y-2">{auditLogs.map(log => {
              const actionLabels: Record<string, string> = {
                policy_create: '创建策略',
                policy_update: '修改策略',
                policy_delete: '删除策略',
                column_policy_update: '更新列策略',
                row_filter: '行级过滤',
                column_hide: '列隐藏',
                column_mask: '列脱敏',
                allow: '允许访问',
                deny: '拒绝访问',
              };
              return (
              <div key={log.id} className="border rounded p-3 text-sm">
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant={log.action?.includes('deny') ? 'destructive' : 'outline'}>
                    {actionLabels[log.action] || log.action}
                  </Badge>
                  <span className="text-muted-foreground text-xs">{log.created_at}</span>
                  {log.user_id > 0 && (
                    <span className="text-xs text-muted-foreground" title={`原用户 ID: ${log.user_id}`}>
                      用户: {auditUserName(log.user_id)}
                    </span>
                  )}
                </div>
                {log.policy_name && <p className="text-xs">策略: {log.policy_name}</p>}
                {log.table_name && <p className="text-xs">表: {log.table_name}</p>}
                {(log.deny_reason || log.original_sql || log.filtered_sql) && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-muted-foreground">查看详情</summary>
                    <div className="mt-1 space-y-1">
                      {log.deny_reason && (
                        <p className="text-xs"><span className="text-muted-foreground">拒绝原因:</span> {log.deny_reason}</p>
                      )}
                      {log.original_sql && (
                        <div>
                          <p className="text-xs text-muted-foreground">原始 SQL</p>
                          <pre className="mt-1 text-xs bg-muted p-2 rounded overflow-auto max-h-32">{log.original_sql}</pre>
                        </div>
                      )}
                      {log.filtered_sql && (
                        <div>
                          <p className="text-xs text-muted-foreground">治理后 SQL</p>
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
          {renderPagination(auditPage, auditTotal, setAuditPage)}
        </TabsContent>
      </Tabs>

      {/* ── Create/Edit Dialog (按编辑上下文 行/列/表 渲染) ────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {editPolicy ? '编辑' : '新建'}{editContext === 'row' ? '行策略' : editContext === 'column' ? '列策略' : '表策略'}
            </DialogTitle>
            <DialogDescription>
              {editContext === 'row' ? '控制用户在目标表中能看到的行（WHERE 过滤）'
                : editContext === 'column' ? '黑名单：选择目标表，再勾选要禁止访问的列（列出的列被隐藏/脱敏，其余列不受影响）'
                : '白名单：勾选该策略允许访问的表；角色绑定任一表策略后，此源仅能访问清单内表'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>策略名称 *</Label>
              <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="如: 仅看本区域数据" />
            </div>
            <div>
              <Label>数据源 *</Label>
              <Select value={form.datasource_id ? String(form.datasource_id) : ''}
                onValueChange={v => {
                  // 换源后原表/列选择必然失效: 主动清空下游, 不静默保留错配值
                  setForm(f => ({ ...f, datasource_id: Number(v), table_name: '', columns: [] }));
                  setNewColName('');
                }}>
                <SelectTrigger aria-label="数据源"><SelectValue placeholder="选择数据源" /></SelectTrigger>
                <SelectContent>
                  {datasources.map(ds => <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}{ds.db_type ? ` (${ds.db_type})` : ''}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            {/* 目标表: 选择搜索框(不手填); 行/列/表三个上下文共用 */}
            <div>
              <Label>目标表 *</Label>
              <SearchSelect
                ariaLabel="目标表"
                value={form.table_name}
                onValueChange={v => {
                  // 换表后列黑名单必然失效: 清空, 界面可见不静默
                  setForm(f => ({ ...f, table_name: v, columns: [] }));
                  setNewColName('');
                }}
                options={form.datasource_id ? (tablesByDs[form.datasource_id] ?? []) : []}
                disabled={!form.datasource_id}
                loading={!!tablesLoadingDs || (form.datasource_id !== 0 && tablesByDs[form.datasource_id] === undefined)}
                error={form.datasource_id && tablesByDs[form.datasource_id] === null ? '表清单加载失败，请检查数据源连接' : null}
                onRetry={() => loadTables(form.datasource_id, true)}
                placeholder={form.datasource_id ? '搜索并选择表' : '请先选择数据源'}
              />
            </div>

            {/* ── 行策略专属: 过滤类型/表达式/属性名 ── */}
            {editContext === 'row' && (
              <>
                <div>
                  <Label>过滤类型</Label>
                  <Select value={form.filter_type} onValueChange={(v: any) => setForm(f => ({ ...f, filter_type: v }))}>
                    <SelectTrigger aria-label="过滤类型"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="condition">条件表达式</SelectItem>
                      <SelectItem value="user_attribute">角色属性</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>过滤表达式</Label>
                  <Textarea value={form.filter_expr} onChange={e => setForm(f => ({ ...f, filter_expr: e.target.value }))}
                    placeholder={'如: region = :user_region\n或: status = \'active\''} rows={3} />
                  <p className="text-xs text-muted-foreground mt-1">自由 SQL 条件（无清单可枚举，允许手填）；使用 :user_xxx 引用角色属性值</p>
                </div>
                {form.filter_type === 'user_attribute' && (
                  <div>
                    <Label>属性名</Label>
                    <SearchSelect
                      ariaLabel="属性名"
                      value={form.user_attribute}
                      onValueChange={v => setForm(f => ({ ...f, user_attribute: v }))}
                      options={attrKeys ?? []}
                      loading={attrKeys === undefined}
                      error={attrKeys === null ? '角色属性清单加载失败' : null}
                      onRetry={() => loadAttrs(true)}
                      emptyText="尚未定义角色属性（请先在角色管理中配置）"
                      placeholder="搜索并选择属性"
                    />
                  </div>
                )}
              </>
            )}

            {/* ── 列策略专属: 黑名单编辑 ── */}
            {editContext === 'column' && (
              <div className="space-y-2">
                <Label>黑名单列（禁止访问的列）</Label>
                <div className="flex gap-2">
                  <div className="flex-1">
                    <SearchSelect
                      ariaLabel="列名"
                      value={newColName}
                      onValueChange={setNewColName}
                      options={(colOptions ?? []).map(o => ({
                        ...o,
                        label: form.columns.some(c => c.column_name === o.value) ? `${o.value}（已添加）` : o.value,
                      }))}
                      disabled={!form.table_name || !form.datasource_id || colOptions === null}
                      loading={!!colsLoadingKey || (form.datasource_id !== 0 && form.table_name !== '' && colOptions === undefined)}
                      error={colOptions === null ? '列清单加载失败，请检查数据源连接' : null}
                      onRetry={() => loadCols(form.datasource_id, form.table_name, true)}
                      placeholder={form.table_name ? '搜索并选择列' : '请先选择目标表'}
                      searchPlaceholder="输入列名/类型/注释过滤…"
                    />
                  </div>
                  <Select value={newColAccess} onValueChange={(v: any) => setNewColAccess(v)}>
                    <SelectTrigger aria-label="屏蔽方式" className="w-28"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="hidden">隐藏</SelectItem>
                      <SelectItem value="masked">脱敏</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button aria-label="添加黑名单列" onClick={addColToBlacklist} disabled={!newColName || !!colsLoadingKey}>
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
                {form.columns.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-3 border rounded">黑名单为空：该策略不屏蔽任何列</p>
                ) : (
                  <div className="space-y-2">
                    {form.columns.map(col => (
                      <div key={col.column_name} className="flex items-center justify-between border rounded px-3 py-2">
                        <div className="flex items-center gap-2">
                          <code className="text-sm">{col.column_name}</code>
                          <Badge variant={col.access_type === 'hidden' ? 'destructive' : 'secondary'}>
                            {col.access_type === 'hidden' ? '隐藏' : '脱敏'}
                          </Badge>
                        </div>
                        <Button variant="ghost" size="sm" aria-label={`移除 ${col.column_name}`} onClick={() => removeColFromBlacklist(col.column_name)}>
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div>
              <Label>描述</Label>
              <Textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} placeholder="策略说明..." rows={2} />
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={form.is_active} onCheckedChange={v => setForm(f => ({ ...f, is_active: v }))} />
              <Label>启用策略</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>{saving ? '保存中...' : '保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>确认删除</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">确定要删除策略 "{deleteTarget?.name}" 吗？此操作不可撤销。</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
