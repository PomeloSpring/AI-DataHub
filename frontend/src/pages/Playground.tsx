import { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import {
  Save, Trash2, Table, FileText,
  Copy, Database, ChevronRight, Play,
  Braces, GitBranch, ShieldCheck, Crosshair,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import ASTTreeView from '../components/ASTTreeView';
import client from '../api/client';

interface Datasource {
  id: number;
  name: string;
  db_type: string;
  host: string;
  port: number;
  database_name: string;
  is_default: number;
}

interface TableInfo {
  TABLE_NAME: string;
  TABLE_COMMENT: string;
  TABLE_ROWS: number;
}

interface ColumnInfo {
  COLUMN_NAME: string;
  DATA_TYPE: string;
  COLUMN_COMMENT: string;
  COLUMN_KEY: string;
}

interface SavedQuery {
  id: number;
  name: string;
  description: string;
  sql_query: string;
  is_dataset: number;
  dataset_keywords: string;
  created_at: string;
}

type ResultTab = 'result' | 'ast' | 'lineage' | 'rls' | 'provenance';

// ── Phase 6: 语义层可观测面板的展示组件 ────────────────────────

function TabButton({ active, loading, onClick, icon: Icon, children }: {
  active: boolean; loading?: boolean; onClick: () => void;
  icon: any; children: React.ReactNode;
}) {
  return (
    <button
      className={`px-3 py-1 text-sm font-medium rounded-t transition-colors whitespace-nowrap flex items-center gap-1.5 ${
        active ? 'text-foreground border-b-2 border-primary' : 'text-muted-foreground hover:text-foreground'
      }`}
      onClick={onClick}
    >
      {loading ? <Spinner size={12} /> : <Icon className="h-3.5 w-3.5" />}
      {children}
    </button>
  );
}

function JsonBlock({ value }: { value: any }) {
  return (
    <pre className="text-xs font-mono whitespace-pre-wrap break-all text-foreground/90">
      {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

function LineageNode({ node, depth = 0 }: { node: any; depth?: number }) {
  if (!node) return null;
  return (
    <div style={{ marginLeft: depth * 16 }} className="py-0.5">
      <div className="flex items-center gap-2 text-sm">
        <GitBranch className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        <span className="font-mono">{node.name}</span>
        {node.dataset && <Badge variant="outline" className="text-xs font-normal">{node.dataset}</Badge>}
        {node.expression && <span className="text-xs text-muted-foreground truncate">{node.expression}</span>}
      </div>
      {(node.downstream || []).map((c: any, i: number) => (
        <LineageNode key={i} node={c} depth={depth + 1} />
      ))}
    </div>
  );
}

function SqlDiff({ base, secured, diff }: { base: string; secured: string; diff: string[] }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs font-semibold mb-1 text-muted-foreground">baseSql (改写前)</div>
          <pre className="text-xs font-mono whitespace-pre-wrap p-2 rounded bg-muted border">{base}</pre>
        </div>
        <div>
          <div className="text-xs font-semibold mb-1 text-muted-foreground">securedSql (改写后 · 所见即所执行)</div>
          <pre className="text-xs font-mono whitespace-pre-wrap p-2 rounded bg-muted border">{secured}</pre>
        </div>
      </div>
      {diff?.length > 0 && (
        <div>
          <div className="text-xs font-semibold mb-1 text-muted-foreground">unified diff</div>
          <pre className="text-xs font-mono p-2 rounded border overflow-auto">
            {diff.map((l, i) => (
              <div
                key={i}
                className={
                  l.startsWith('+++') || l.startsWith('---')
                    ? 'text-muted-foreground'
                    : l.startsWith('+') ? 'text-green-600 bg-green-50 dark:bg-green-950/30'
                    : l.startsWith('-') ? 'text-red-600 bg-red-50 dark:bg-red-950/30'
                    : ''
                }
              >{l}</div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}

function ProvenanceCard({ data }: { data: any }) {
  const b = data?.binding || {};
  const g = b.guardrail || {};
  const a = b.access || {};
  const rows: [string, React.ReactNode][] = [
    ['对象 (object)', b.object_key],
    ['物理表 (catalog.db.table)', b.catalog_ref || `${b.catalog_name || ''}.${b.db_name || ''}.${b.physical_table || ''}`],
    ['datasource_id', b.datasource_id],
    ['绑定来源', b.source],
    ['sync_state', <Badge key="s" variant={b.sync_state === 'bound' ? 'secondary' : 'destructive'} className="text-xs font-normal">{b.sync_state}</Badge>],
    ['query_mode', g.query_mode],
    ['size_class', g.size_class],
    ['allow_full_scan', String(g.allow_full_scan)],
    ['requiredPerms', (a.permission_tokens || []).join(', ') || '—'],
    ['rlsPolicies', (a.rls_policy_refs || []).join(', ') || '—'],
    ['maskedColumns', (a.masked_columns || []).join(', ') || '—'],
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 max-w-2xl">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between gap-3 border-b border-dashed py-1">
            <span className="text-xs text-muted-foreground">{k}</span>
            <span className="text-sm font-mono text-right truncate">{v ?? '—'}</span>
          </div>
        ))}
      </div>
      {data?.plan?.sql && (
        <div>
          <div className="text-xs font-semibold mb-1 text-muted-foreground">编译 baseSql (dry-run)</div>
          <pre className="text-xs font-mono whitespace-pre-wrap p-2 rounded bg-muted border">{data.plan.sql}</pre>
        </div>
      )}
      {data?.warnings?.length > 0 && (
        <div className="text-xs text-amber-600">{data.warnings.join(' · ')}</div>
      )}
    </div>
  );
}

export default function Playground() {
  const [sql, setSql] = useState('');
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [selectedDs, setSelectedDs] = useState<number | null>(null);
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [columns, setColumns] = useState<ColumnInfo[]>([]);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [savedQueries, setSavedQueries] = useState<SavedQuery[]>([]);
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveDesc, setSaveDesc] = useState('');
  const [isDataset, setIsDataset] = useState(false);
  const [resultTab, setResultTab] = useState<ResultTab>('ast');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  // 受治理的交互式取数结果(继承当前用户角色权限; 与看板 raw_sql 同源)
  const [execResult, setExecResult] = useState<any>(null);
  const [executing, setExecuting] = useState(false);

  // Phase 6: 语义层可观测分析状态 (AST / 血缘 / RLS 改写预览 / 溯源) — 均不返回数据行
  const [analysis, setAnalysis] = useState<Record<string, any>>({});
  // AST 页签: 默认树形可视化, 可切回原始 JSON
  const [astRaw, setAstRaw] = useState(false);
  const [analyzing, setAnalyzing] = useState<string | null>(null);
  const [provObject, setProvObject] = useState('');

  const selectedDsType = datasources.find(d => d.id === selectedDs)?.db_type || '';

  const ANALYSIS_TABS: { key: ResultTab; label: string; icon: any }[] = [
    { key: 'result', label: '执行结果', icon: Play },
    { key: 'ast', label: 'AST', icon: Braces },
    { key: 'lineage', label: '血缘', icon: GitBranch },
    { key: 'rls', label: 'RLS 改写', icon: ShieldCheck },
    { key: 'provenance', label: '溯源', icon: Crosshair },
  ];

  const analyzePath: Record<string, string> = {
    ast: '/playground/ast', lineage: '/playground/lineage',
    rls: '/playground/rls-diff',
  };

  // 受治理执行: body 只传 sql + datasource_id; 身份由后端从 JWT 解析, 不传 user_id
  const executeSql = async () => {
    if (!sql.trim()) { toast.error('请输入 SQL'); return; }
    if (!selectedDs) { toast.error('请先选择数据源'); return; }
    setExecuting(true);
    setResultTab('result');
    try {
      const { data } = await client.post('/playground/execute', {
        sql: sql.trim(), datasource_id: selectedDs,
      });
      setExecResult(data);
      if (data.error) toast.error(data.error);
      else toast.success(`查询成功，返回 ${data.row_count ?? data.rows?.length ?? 0} 行`);
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : (detail?.error || detail || '执行失败');
      setExecResult({ error: msg });
      toast.error(msg);
    } finally {
      setExecuting(false);
    }
  };

  const runAnalyze = async (kind: 'ast' | 'lineage' | 'rls') => {
    if (!sql.trim()) { toast.error('请输入 SQL'); return; }
    if (kind === 'rls' && !selectedDs) { toast.error('请先选择数据源'); return; }
    setAnalyzing(kind);
    setResultTab(kind);
    try {
      // 只发待分析的 SQL 与数据源(供改写预览); 身份由后端从 JWT 解析, body 不传 user_id
      const body: any = { sql: sql.trim(), datasource_id: selectedDs };
      const { data } = await client.post(analyzePath[kind], body);
      setAnalysis((a) => ({ ...a, [kind]: data }));
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      setAnalysis((a) => ({ ...a, [kind]: { error: typeof detail === 'string' ? detail : (detail?.error || detail || '分析失败') } }));
      if (typeof detail === 'string') toast.error(detail);
    } finally {
      setAnalyzing(null);
    }
  };

  const runProvenance = async () => {
    if (!provObject.trim()) { toast.error('请输入本体对象名'); return; }
    setAnalyzing('provenance');
    setResultTab('provenance');
    try {
      const { data } = await client.post('/playground/provenance', { object: provObject.trim(), datasource_id: selectedDs || 0 });
      setAnalysis((a) => ({ ...a, provenance: data }));
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      setAnalysis((a) => ({ ...a, provenance: { error: typeof detail === 'string' ? detail : (detail?.error || '溯源失败') } }));
    } finally {
      setAnalyzing(null);
    }
  };

  useEffect(() => {
    loadDatasources();
    loadSavedQueries();
  }, []);

  const loadDatasources = async () => {
    try {
      const { data } = await client.get('/datasources');
      setDatasources(Array.isArray(data) ? data : []);
      // 不自动选中数据源，由用户手动选择
    } catch (e) {
      console.error('Failed to load datasources:', e);
    }
  };

  const loadTables = async (dsId: number) => {
    try {
      const { data } = await client.get(`/datasources/${dsId}/tables`);
      setTables(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error('Failed to load tables:', e);
    }
  };

  const loadColumns = async (dsId: number, tableName: string) => {
    try {
      const { data } = await client.get(`/datasources/${dsId}/tables/${tableName}/columns`);
      setColumns(Array.isArray(data) ? data : []);
      setSelectedTable(tableName);
    } catch (e) {
      console.error('Failed to load columns:', e);
    }
  };

  const loadSavedQueries = async () => {
    try {
      const { data } = await client.get('/playground/queries');
      setSavedQueries(Array.isArray(data) ? data : []);
    } catch {}
  };

  const saveQuery = async () => {
    try {
      await client.post('/playground/queries', {
        name: saveName,
        description: saveDesc,
        sql_query: sql,
        is_dataset: isDataset ? 1 : 0,
      });
      toast.success('保存成功');
      setSaveModalOpen(false);
      setSaveName('');
      setSaveDesc('');
      setIsDataset(false);
      loadSavedQueries();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  };

  const deleteQuery = async (id: number) => {
    try {
      await client.delete(`/playground/queries/${id}`);
      toast.success('已删除');
      loadSavedQueries();
    } catch {
      toast.error('删除失败');
    }
  };

  const copySql = () => {
    navigator.clipboard.writeText(sql);
    toast.success('已复制到剪贴板');
  };

  const insertTableName = (tableName: string) => {
    if (editorRef.current) {
      const start = editorRef.current.selectionStart;
      const end = editorRef.current.selectionEnd;
      const newSql = sql.substring(0, start) + tableName + sql.substring(end);
      setSql(newSql);
    }
  };

  const insertColumnName = (colName: string) => {
    if (editorRef.current) {
      const start = editorRef.current.selectionStart;
      const end = editorRef.current.selectionEnd;
      const newSql = sql.substring(0, start) + colName + sql.substring(end);
      setSql(newSql);
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b">
        <h1 className="text-xl font-bold">SQL Playground</h1>
        <div className="flex items-center gap-2">
          <Select
            value={selectedDs?.toString() || ''}
            onValueChange={(v) => {
              const dsId = parseInt(v);
              setSelectedDs(dsId);
              loadTables(dsId);
            }}
          >
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="选择数据源" />
            </SelectTrigger>
            <SelectContent>
              {datasources.map((ds) => (
                <SelectItem key={ds.id} value={ds.id.toString()}>
                  <div className="flex items-center gap-2">
                    <Database className="h-4 w-4" />
                    <span>{ds.name}</span>
                    <Badge variant="outline" className="text-xs">{ds.db_type}</Badge>
                  </div>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={executeSql} disabled={executing || !selectedDs}>
            {executing ? <Spinner size={12} className="mr-2" /> : <Play className="h-4 w-4 mr-2" />}
            执行 (Ctrl+Enter)
          </Button>
          <Button variant="outline" onClick={copySql}>
            <Copy className="h-4 w-4 mr-2" />
            复制
          </Button>
          <Button variant="outline" onClick={() => setSaveModalOpen(true)}>
            <Save className="h-4 w-4 mr-2" />
            保存
          </Button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left sidebar - Tables */}
        <div className={`border-r flex flex-col overflow-hidden transition-all duration-200 ${sidebarCollapsed ? 'w-0' : 'w-[250px]'}`}>
          <div className="p-3 border-b flex items-center justify-between shrink-0">
            <h3 className="text-sm font-semibold">数据表</h3>
            <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setSidebarCollapsed(!sidebarCollapsed)}>
              <ChevronRight className={`h-4 w-4 transition-transform ${sidebarCollapsed ? 'rotate-180' : ''}`} />
            </Button>
          </div>
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-2">
              {!selectedDs && (
                <div className="text-center py-8 text-muted-foreground">
                  <Database className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p className="text-xs">请先选择数据源</p>
                </div>
              )}
              {selectedDs && tables.length === 0 && (
                <div className="text-center py-8 text-muted-foreground">
                  <Table className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p className="text-xs">暂无数据表</p>
                </div>
              )}
              {tables.map((table) => (
                <div
                  key={table.TABLE_NAME}
                  className={`flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer hover:bg-muted ${
                    selectedTable === table.TABLE_NAME ? 'bg-muted' : ''
                  }`}
                  onClick={() => selectedDs && loadColumns(selectedDs, table.TABLE_NAME)}
                  onDoubleClick={() => insertTableName(table.TABLE_NAME)}
                >
                  <Table className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{table.TABLE_NAME}</div>
                    <div className="text-xs text-muted-foreground truncate">{table.TABLE_COMMENT}</div>
                  </div>
                  <Badge variant="outline" className="text-xs">{table.TABLE_ROWS}</Badge>
                </div>
              ))}
            </div>
          </ScrollArea>

          {selectedTable && (
            <>
              <Separator className="shrink-0" />
              <div className="p-3 border-b shrink-0">
                <h3 className="text-sm font-semibold">{selectedTable}</h3>
              </div>
              <ScrollArea className="h-[200px] shrink-0">
                <div className="p-2">
                  {columns.map((col) => (
                    <div
                      key={col.COLUMN_NAME}
                      className="flex items-center gap-2 px-2 py-1 text-sm cursor-pointer hover:bg-muted"
                      onClick={() => insertColumnName(col.COLUMN_NAME)}
                    >
                      <Badge variant="outline" className="text-xs w-16 justify-center">{col.DATA_TYPE}</Badge>
                      <span className="truncate">{col.COLUMN_NAME}</span>
                      {col.COLUMN_KEY === 'PRI' && <Badge variant="secondary" className="text-xs">PK</Badge>}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </>
          )}

          <Separator className="shrink-0" />
          <div className="p-3 border-b shrink-0">
            <h3 className="text-sm font-semibold">已保存查询</h3>
          </div>
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-2">
              {savedQueries.map((q) => (
                <div
                  key={q.id}
                  className="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer hover:bg-muted"
                  onClick={() => {
                    setSql(q.sql_query);
                  }}
                >
                  <FileText className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{q.name}</div>
                    <div className="text-xs text-muted-foreground">{q.is_dataset ? '数据集' : '查询'}</div>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 w-6 p-0"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteQuery(q.id);
                    }}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>

        {/* Main area */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* SQL Editor */}
          <div className="h-[200px] border-b flex-shrink-0">
            <Textarea
              ref={editorRef}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                  e.preventDefault();
                  executeSql();
                }
              }}
              placeholder={selectedDsType === 'elasticsearch'
                ? '输入 ES SQL 查询... (Ctrl+Enter 执行; 结果按当前用户角色权限施加敏感列屏蔽/脱敏 + 行级 RLS)\n例如: SELECT * FROM "my_index" LIMIT 100'
                : '输入 SQL 查询... (Ctrl+Enter 执行; 结果按当前用户角色权限施加敏感列屏蔽/脱敏 + 行级 RLS)'}
              className="h-full resize-none font-mono text-sm rounded-none border-0"
            />
          </div>

          {/* Results area */}
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* Result title bar with tabs */}
            <div className="border-b flex items-center justify-between px-3 h-10 flex-shrink-0">
              <div className="flex items-center gap-1 overflow-x-auto">
                {ANALYSIS_TABS.map((t) => (
                  <TabButton
                    key={t.key}
                    active={resultTab === t.key}
                    loading={analyzing === t.key}
                    icon={t.icon}
                    onClick={() => {
                      if (t.key === 'provenance') setResultTab('provenance');
                      else if (t.key === 'result') setResultTab('result');
                      else runAnalyze(t.key as 'ast' | 'lineage' | 'rls');
                    }}
                  >
                    {t.label}
                  </TabButton>
                ))}
              </div>
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-hidden">
              {resultTab === 'result' && (
                <div className="h-full flex flex-col overflow-hidden">
                  {execResult && !execResult.error && (execResult.columns?.length > 0) && (
                    <div className="px-3 py-1.5 text-xs text-muted-foreground border-b flex gap-4 shrink-0">
                      <span>返回 <span className="font-medium text-foreground">{execResult.row_count ?? execResult.rows?.length ?? 0}</span> 行</span>
                      {execResult.elapsed_ms != null && <span>耗时 {execResult.elapsed_ms}ms</span>}
                      <span className="text-amber-600">已按当前用户权限脱敏/屏蔽与行级过滤</span>
                    </div>
                  )}
                  <div className="flex-1 overflow-auto">
                    {executing ? (
                      <div className="p-4"><Spinner size={16} /></div>
                    ) : !execResult ? (
                      <p className="p-4 text-sm text-muted-foreground">点击「执行」运行 SQL；结果按当前用户的角色权限（敏感列屏蔽/脱敏 + 行级 RLS）返回。</p>
                    ) : execResult.error ? (
                      <div className="p-4 text-sm text-destructive">{String(execResult.error)}</div>
                    ) : (execResult.columns?.length ? (
                      <table className="w-full text-sm border-collapse">
                        <thead className="sticky top-0 bg-muted">
                          <tr>
                            {execResult.columns.map((col: string) => (
                              <th key={col} className="text-left px-3 py-2 border-b font-medium whitespace-nowrap">{col}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {(execResult.rows || []).map((row: any, i: number) => (
                            <tr key={i} className="hover:bg-muted/50">
                              {execResult.columns.map((col: string) => (
                                <td key={col} className="px-3 py-1.5 border-b whitespace-nowrap max-w-[320px] truncate">
                                  {row[col] == null ? '' : String(row[col])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <p className="p-4 text-sm text-muted-foreground">无结果</p>
                    ))}
                  </div>
                </div>
              )}

              {resultTab === 'ast' && (
                <ScrollArea className="h-full">
                  <div className="p-4">
                    {analyzing === 'ast' ? <Spinner size={16} /> : !analysis.ast ? (
                      <p className="text-sm text-muted-foreground">点击「AST」按钮解析当前 SQL 的语法树。</p>
                    ) : analysis.ast.error ? (
                      <p className="text-sm text-destructive">{String(analysis.ast.error)}</p>
                    ) : (
                      <div className="space-y-2">
                        <div className="text-xs text-muted-foreground">
                          引用表: <span className="font-mono">{analysis.ast.tables?.join(', ') || '—'}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-muted-foreground">语法树</span>
                          <Button size="sm" variant="ghost" className="h-6 text-xs" onClick={() => setAstRaw((v) => !v)}>
                            {astRaw ? '树视图' : '查看原始 JSON'}
                          </Button>
                        </div>
                        {astRaw ? <JsonBlock value={analysis.ast.ast} /> : <ASTTreeView dump={analysis.ast.ast} />}
                      </div>
                    )}
                  </div>
                </ScrollArea>
              )}

              {resultTab === 'lineage' && (
                <ScrollArea className="h-full">
                  <div className="p-4">
                    {analyzing === 'lineage' ? <Spinner size={16} /> : !analysis.lineage ? (
                      <p className="text-sm text-muted-foreground">点击「血缘」按钮查看列级血缘。</p>
                    ) : analysis.lineage.error ? (
                      <p className="text-sm text-destructive">{String(analysis.lineage.error)}</p>
                    ) : (
                      <div className="space-y-4">
                        {(analysis.lineage.columns || []).map((c: any, i: number) => (
                          <div key={i}>
                            <div className="text-xs font-semibold mb-1">列 <span className="font-mono">{c.column}</span></div>
                            {c.error ? <p className="text-xs text-destructive">{c.error}</p> : <LineageNode node={c.lineage} />}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </ScrollArea>
              )}

              {resultTab === 'rls' && (
                <ScrollArea className="h-full">
                  <div className="p-4">
                    {analyzing === 'rls' ? <Spinner size={16} /> : !analysis.rls ? (
                      <p className="text-sm text-muted-foreground">点击「RLS 改写」按钮查看当前用户下 baseSql→securedSql 的行级+列级改写。</p>
                    ) : (
                      <div className="space-y-3">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <Badge variant="secondary" className="font-normal">生效策略: {(analysis.rls.rlsPolicies || []).join(', ') || '无'}</Badge>
                          <Badge variant="outline" className="font-normal">改写表: {(analysis.rls.appliedRls || []).join(', ') || '无'}</Badge>
                          {!analysis.rls.changed && <span className="text-muted-foreground">当前用户对该查询无生效 RLS</span>}
                          {analysis.rls.error && <span className="text-destructive">{String(analysis.rls.error)}</span>}
                        </div>
                        {/* 列级限制明细: 隐藏列已从 securedSql 剔除, 掩码列已替换为脱敏表达式 */}
                        {Object.keys(analysis.rls.columnRestrictions || {}).length > 0 && (
                          <div className="space-y-1.5 rounded-md border border-border/60 bg-muted/30 p-2.5">
                            {Object.entries(analysis.rls.columnRestrictions as Record<string, any>).map(([tbl, spec]: [string, any]) => (
                              <div key={tbl} className="flex flex-wrap items-center gap-2 text-xs">
                                <span className="font-mono text-foreground/80">{tbl}</span>
                                {(spec?.hidden || []).length > 0 && (
                                  <Badge variant="destructive" className="font-normal">隐藏列: {spec.hidden.join(', ')}</Badge>
                                )}
                                {Object.keys(spec?.masked || {}).length > 0 && (
                                  <Badge variant="outline" className="font-normal border-amber-400 text-amber-600">
                                    掩码列: {Object.entries(spec.masked as Record<string, string>).map(([c, m]) => `${c}(${m})`).join(', ')}
                                  </Badge>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                        <SqlDiff base={analysis.rls.baseSql} secured={analysis.rls.securedSql} diff={analysis.rls.diff || []} />
                      </div>
                    )}
                  </div>
                </ScrollArea>
              )}

              {resultTab === 'provenance' && (
                <div className="h-full flex flex-col">
                  <div className="p-2 border-b flex items-center gap-2 flex-shrink-0">
                    <Input
                      value={provObject}
                      onChange={(e) => setProvObject(e.target.value)}
                      placeholder="本体对象名 (如 user / Patient / obj:Order)"
                      className="h-7 w-[280px]"
                      onKeyDown={(e) => { if (e.key === 'Enter') runProvenance(); }}
                    />
                    <Button size="sm" className="h-7" onClick={runProvenance} disabled={analyzing === 'provenance'}>
                      {analyzing === 'provenance' ? <Spinner size={12} className="mr-2" /> : <Crosshair className="h-3.5 w-3.5 mr-1" />}
                      溯源
                    </Button>
                  </div>
                  <div className="flex-1 overflow-auto p-4">
                    {analyzing === 'provenance' ? <Spinner size={16} /> : !analysis.provenance ? (
                      <p className="text-sm text-muted-foreground">输入本体对象名, 查看其 binding / query_mode / size_class 溯源。</p>
                    ) : analysis.provenance.error ? (
                      <p className="text-sm text-destructive">{String(analysis.provenance.error)}</p>
                    ) : (
                      <ProvenanceCard data={analysis.provenance} />
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Save Modal */}
      <Dialog open={saveModalOpen} onOpenChange={setSaveModalOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>保存查询</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>名称 *</Label>
              <Input
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                placeholder="查询名称"
              />
            </div>
            <div className="space-y-2">
              <Label>描述</Label>
              <Textarea
                value={saveDesc}
                onChange={(e) => setSaveDesc(e.target.value)}
                placeholder="查询描述"
                rows={2}
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={isDataset}
                onCheckedChange={setIsDataset}
              />
              <Label>保存为数据集</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveModalOpen(false)}>取消</Button>
            <Button onClick={saveQuery}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
