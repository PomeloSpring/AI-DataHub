import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import {
  Plus, Edit, Trash2, Shield, FileText, Columns3, Rows3, ListFilter, ListX,
} from 'lucide-react';
import client from '@/api/client';
import {
  listRLSPolicies, createRLSPolicy, updateRLSPolicy, deleteRLSPolicy,
  getRLSColumnPolicies, setRLSColumnPolicies, listRLSAuditLogs,
  type RLSPolicy, type RLSAuditLog,
} from '@/api/rls';

interface Datasource { id: number; name: string; db_type?: string; }

interface PolicyFormData {
  name: string;
  description: string;
  datasource_id: number;
  table_name: string;
  policy_type: 'row' | 'column' | 'both';
  filter_type: 'condition' | 'user_attribute';
  filter_expr: string;
  user_attribute: string;
  is_active: boolean;
}

interface ColumnStrategyData {
  mode: 'whitelist' | 'blacklist';
  columns: { column_name: string; access_type: 'hidden' | 'masked' }[];
}

const DEFAULT_FORM: PolicyFormData = {
  name: '', description: '', datasource_id: 0, table_name: '',
  policy_type: 'row', filter_type: 'condition', filter_expr: '', user_attribute: '', is_active: true,
};

const DEFAULT_COL_STRATEGY: ColumnStrategyData = { mode: 'blacklist', columns: [] };

export default function RLSManagement() {
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [rowPolicies, setRowPolicies] = useState<RLSPolicy[]>([]);
  const [colPolicies, setColPolicies] = useState<RLSPolicy[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editPolicy, setEditPolicy] = useState<RLSPolicy | null>(null);
  const [form, setForm] = useState<PolicyFormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<RLSPolicy | null>(null);

  // Column strategy dialog
  const [colStrategyTarget, setColStrategyTarget] = useState<RLSPolicy | null>(null);
  const [colStrategy, setColStrategy] = useState<ColumnStrategyData>(DEFAULT_COL_STRATEGY);
  const [newColName, setNewColName] = useState('');
  const [newColAccess, setNewColAccess] = useState<'hidden' | 'masked'>('hidden');
  const [colSaving, setColSaving] = useState(false);

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

  // Load policies split by type
  const loadPolicies = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listRLSPolicies(0, undefined, undefined, page);
      const items = data.items || [];
      setRowPolicies(items.filter(p => p.policy_type === 'row' || p.policy_type === 'both'));
      setColPolicies(items.filter(p => p.policy_type === 'column' || p.policy_type === 'both'));
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

  // Policy CRUD
  const openCreate = (type: 'row' | 'column' | 'both' = 'row') => {
    setEditPolicy(null);
    setForm({ ...DEFAULT_FORM, policy_type: type });
    setFormOpen(true);
  };

  const openEdit = (policy: RLSPolicy) => {
    setEditPolicy(policy);
    setForm({
      name: policy.name, description: policy.description || '',
      datasource_id: policy.datasource_id, table_name: policy.table_name,
      policy_type: policy.policy_type, filter_type: policy.filter_type,
      filter_expr: policy.filter_expr || '', user_attribute: policy.user_attribute || '',
      is_active: !!policy.is_active,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name || !form.table_name) { toast.error('请填写策略名称和目标表名'); return; }
    if (!form.datasource_id) { toast.error('请选择数据源'); return; }
    setSaving(true);
    try {
      const payload = { ...form, workspace_id: 0, is_active: form.is_active ? 1 : 0 };
      if (editPolicy) { await updateRLSPolicy(editPolicy.id, payload); toast.success('策略已更新'); }
      else { await createRLSPolicy(payload); toast.success('策略已创建'); }
      setFormOpen(false); loadPolicies();
    } catch { toast.error('保存失败'); }
    finally { setSaving(false); }
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

  // Column strategy management
  const openColStrategy = async (policy: RLSPolicy) => {
    setColStrategyTarget(policy);
    setNewColName('');
    try {
      const cols = await getRLSColumnPolicies(policy.id);
      const mode: 'whitelist' | 'blacklist' = (cols as any)?.mode || 'blacklist';
      const items = (cols as any)?.columns || cols;
      setColStrategy({
        mode,
        columns: Array.isArray(items) ? items.map((c: any) => ({ column_name: c.column_name, access_type: c.access_type as 'hidden' | 'masked' })) : [],
      });
    } catch { setColStrategy(DEFAULT_COL_STRATEGY); }
  };

  const addColToStrategy = () => {
    if (!newColName) return;
    if (colStrategy.columns.some(c => c.column_name === newColName)) { toast.error('列已存在'); return; }
    setColStrategy(s => ({ ...s, columns: [...s.columns, { column_name: newColName, access_type: newColAccess }] }));
    setNewColName('');
  };

  const removeColFromStrategy = (colName: string) => {
    setColStrategy(s => ({ ...s, columns: s.columns.filter(c => c.column_name !== colName) }));
  };

  const saveColStrategy = async () => {
    if (!colStrategyTarget) return;
    setColSaving(true);
    try {
      await setRLSColumnPolicies(colStrategyTarget.id, {
        mode: colStrategy.mode,
        columns: colStrategy.columns,
      } as any);
      toast.success('列策略已保存');
      setColStrategyTarget(null);
    } catch { toast.error('保存失败'); }
    finally { setColSaving(false); }
  };

  // 页面显示资源一律用 name; 已删除数据源给可读标注(原 id 仅进 title 次要信息)
  const dsName = (id: number) => datasources.find(d => d.id === id)?.name || '数据源已删除';
  const dsTitle = (id: number) => (datasources.find(d => d.id === id) ? undefined : `原数据源 ID: ${id}`);
  // 审计行用户同口径：name 为主，未加载用占位，不可见用户给可读标注(原 id 仅进 title)
  const auditUserName = (uid: number) => userNames[uid] || (usersLoaded ? '未知用户' : '加载中…');

  const renderPolicyCard = (policy: RLSPolicy, showColBtn: boolean) => (
    <div key={policy.id} className="border rounded-lg p-4 flex items-start justify-between">
      <div className="flex-1">
        <div className="flex items-center gap-2 mb-1">
          <span className="font-medium">{policy.name}</span>
          <Badge variant={policy.policy_type === 'both' ? 'default' : 'secondary'}>
            {policy.policy_type === 'row' ? '行级' : policy.policy_type === 'column' ? '列级' : '行+列'}
          </Badge>
          <Badge variant={policy.is_active ? 'default' : 'outline'}>{policy.is_active ? '启用' : '禁用'}</Badge>
        </div>
        <div className="text-sm text-muted-foreground space-y-1">
          <p>数据源: <strong title={dsTitle(policy.datasource_id)}>{dsName(policy.datasource_id)}</strong> | 表: <code>{policy.table_name}</code></p>
          {policy.filter_expr && <p>过滤: <code className="bg-muted px-1 rounded">{policy.filter_expr}</code></p>}
          {policy.description && <p>{policy.description}</p>}
        </div>
      </div>
      <div className="flex items-center gap-2 ml-4">
        <Switch checked={!!policy.is_active} onCheckedChange={() => handleToggle(policy)} />
        {showColBtn && (
          <Button variant="outline" size="sm" onClick={() => openColStrategy(policy)} title="列策略">
            <Columns3 className="h-4 w-4" />
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={() => openEdit(policy)}><Edit className="h-4 w-4" /></Button>
        <Button variant="outline" size="sm" onClick={() => setDeleteTarget(policy)}><Trash2 className="h-4 w-4" /></Button>
      </div>
    </div>
  );

  const renderEmpty = (msg: string, type: 'row' | 'column') => (
    <div className="text-center py-8 text-muted-foreground">
      <p className="mb-3">{msg}</p>
      <Button onClick={() => openCreate(type)} size="sm"><Plus className="h-4 w-4 mr-2" />新建策略</Button>
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
          <TabsTrigger value="audit" onClick={() => loadAuditLogs()}><FileText className="h-4 w-4 mr-2" />审计日志</TabsTrigger>
        </TabsList>

        {/* ── 行策略 Tab ──────────────────────────────────────── */}
        <TabsContent value="row">
          <div className="flex justify-between items-center mb-4">
            <p className="text-sm text-muted-foreground">共 {rowPolicies.length} 条行级策略</p>
            <Button onClick={() => openCreate('row')} size="sm"><Plus className="h-4 w-4 mr-2" />新建行策略</Button>
          </div>
          {loading ? <div className="text-center py-8 text-muted-foreground">加载中...</div>
            : rowPolicies.length === 0 ? renderEmpty('暂无行级策略，点击"新建行策略"开始配置', 'row')
            : <div className="space-y-3">{rowPolicies.map(p => renderPolicyCard(p, true))}</div>
          }
          {renderPagination(page, total, setPage)}
        </TabsContent>

        {/* ── 列策略 Tab ──────────────────────────────────────── */}
        <TabsContent value="column">
          <div className="flex justify-between items-center mb-4">
            <p className="text-sm text-muted-foreground">共 {colPolicies.length} 条列级策略</p>
            <Button onClick={() => openCreate('column')} size="sm"><Plus className="h-4 w-4 mr-2" />新建列策略</Button>
          </div>
          {loading ? <div className="text-center py-8 text-muted-foreground">加载中...</div>
            : colPolicies.length === 0 ? renderEmpty('暂无列级策略，点击"新建列策略"开始配置', 'column')
            : <div className="space-y-3">{colPolicies.map(p => renderPolicyCard(p, true))}</div>
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

      {/* ── Create/Edit Dialog ─────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editPolicy ? '编辑策略' : form.policy_type === 'column' ? '新建列策略' : '新建行策略'}</DialogTitle>
            <DialogDescription>
              {form.policy_type === 'column'
                ? '配置列级安全策略，指定可访问或不可访问的列'
                : '配置行级安全策略，控制用户对数据行的访问权限'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>策略名称 *</Label>
              <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="如: 仅看本区域数据" />
            </div>
            <div>
              <Label>数据源 *</Label>
              <Select value={form.datasource_id ? String(form.datasource_id) : ''} onValueChange={v => setForm(f => ({ ...f, datasource_id: Number(v) }))}>
                <SelectTrigger><SelectValue placeholder="选择数据源" /></SelectTrigger>
                <SelectContent>
                  {datasources.map(ds => <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}{ds.db_type ? ` (${ds.db_type})` : ''}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>目标表名 *</Label>
              <Input value={form.table_name} onChange={e => setForm(f => ({ ...f, table_name: e.target.value }))} placeholder="如: orders" />
            </div>

            {/* 行策略配置 */}
            {(form.policy_type === 'row' || form.policy_type === 'both') && (
              <>
                <div>
                  <Label>过滤类型</Label>
                  <Select value={form.filter_type} onValueChange={(v: any) => setForm(f => ({ ...f, filter_type: v }))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
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
                  <p className="text-xs text-muted-foreground mt-1">使用 :user_xxx 引用角色属性值（在角色管理中配置）</p>
                </div>
                {form.filter_type === 'user_attribute' && (
                  <div>
                    <Label>属性名</Label>
                    <Input value={form.user_attribute} onChange={e => setForm(f => ({ ...f, user_attribute: e.target.value }))} placeholder="如: region" />
                  </div>
                )}
              </>
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

      {/* ── Column Strategy Dialog ──────────────────────────────── */}
      <Dialog open={!!colStrategyTarget} onOpenChange={() => setColStrategyTarget(null)}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>列策略 — {colStrategyTarget?.name}</DialogTitle>
            <DialogDescription>
              表 <code>{colStrategyTarget?.table_name}</code> 的列级访问控制。
              最终可访问列 = 全部列 − 敏感数据管理屏蔽列 − 黑名单列（或仅白名单列）。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {/* Mode selector */}
            <div>
              <Label>策略模式</Label>
              <div className="flex gap-2 mt-1">
                <Button variant={colStrategy.mode === 'blacklist' ? 'default' : 'outline'} size="sm"
                  onClick={() => setColStrategy(s => ({ ...s, mode: 'blacklist' }))}>
                  <ListX className="h-4 w-4 mr-1" />黑名单（排除指定列）
                </Button>
                <Button variant={colStrategy.mode === 'whitelist' ? 'default' : 'outline'} size="sm"
                  onClick={() => setColStrategy(s => ({ ...s, mode: 'whitelist' }))}>
                  <ListFilter className="h-4 w-4 mr-1" />白名单（仅允许指定列）
                </Button>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {colStrategy.mode === 'blacklist'
                  ? '黑名单模式：列出的列将被隐藏/脱敏，其余列默认可见（仍需排除敏感数据管理中的屏蔽列）'
                  : '白名单模式：仅列出的列可访问，其余列全部隐藏（敏感数据管理中的屏蔽列同样不可见）'}
              </p>
            </div>

            {/* Add column */}
            <div className="flex gap-2">
              <Input placeholder="列名" value={newColName} onChange={e => setNewColName(e.target.value)}
                className="flex-1" onKeyDown={e => e.key === 'Enter' && addColToStrategy()} />
              <Select value={newColAccess} onValueChange={(v: any) => setNewColAccess(v)}>
                <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="hidden">隐藏</SelectItem>
                  <SelectItem value="masked">脱敏</SelectItem>
                </SelectContent>
              </Select>
              <Button onClick={addColToStrategy} disabled={!newColName}><Plus className="h-4 w-4" /></Button>
            </div>

            {/* Column list */}
            {colStrategy.columns.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                {colStrategy.mode === 'whitelist' ? '暂无白名单列，添加后仅这些列可访问' : '暂无黑名单列，所有非敏感列均可访问'}
              </p>
            ) : (
              <div className="space-y-2">
                {colStrategy.columns.map(col => (
                  <div key={col.column_name} className="flex items-center justify-between border rounded px-3 py-2">
                    <div className="flex items-center gap-2">
                      <code className="text-sm">{col.column_name}</code>
                      <Badge variant={col.access_type === 'hidden' ? 'destructive' : 'secondary'}>
                        {col.access_type === 'hidden' ? '隐藏' : '脱敏'}
                      </Badge>
                    </div>
                    <Button variant="ghost" size="sm" onClick={() => removeColFromStrategy(col.column_name)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setColStrategyTarget(null)}>取消</Button>
            <Button onClick={saveColStrategy} disabled={colSaving}>{colSaving ? '保存中...' : '保存'}</Button>
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
