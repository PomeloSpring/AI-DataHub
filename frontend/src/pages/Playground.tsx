import { useState, useEffect, useRef } from 'react';
import { toast } from '@/lib/toast';
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
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

/** 数据集列表项（服务端不返 SQL 正文，载入时按 id 取详情） */
interface DatasetBrief {
  id: number;
  name: string;
  description?: string;
  source_type?: string;
  object_key?: string;
  sql_query?: string;
}

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
          <div className="text-xs font-semibold mb-1 text-muted-foreground">{tr('k_a245feef11')}</div>
          <pre className="text-xs font-mono whitespace-pre-wrap p-2 rounded bg-muted border">{base}</pre>
        </div>
        <div>
          <div className="text-xs font-semibold mb-1 text-muted-foreground">{tr('k_e6f1898a46')}</div>
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
    [tr('k_c1ca53ae0e'), b.object_key],
    [tr('k_512d256293'), b.catalog_ref || `${b.catalog_name || ''}.${b.db_name || ''}.${b.physical_table || ''}`],
    ['datasource_id', b.datasource_id],
    [tr('k_bc98f631b3'), b.source],
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
          <div className="text-xs font-semibold mb-1 text-muted-foreground">{tr('k_20f722ee3e')}</div>
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
  const { t } = useTranslation('ui');
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

  // SQL 卡片「在 Playground 打开」带入的语句:挂载时消费一次并预填编辑器
  useEffect(() => {
    const raw = sessionStorage.getItem('playground_sql');
    if (raw) {
      sessionStorage.removeItem('playground_sql');
      try {
        const { sql: s } = JSON.parse(raw);
        if (typeof s === 'string' && s) setSql(s);
      } catch { /* 忽略非法载荷 */ }
    }
  }, []);

  // 查询数据集侧栏: 点击载入其已治理的预制 SQL 到编辑器。
  // 列表不返 SQL 正文，故插入时按 id 取详情；失败显式提示不阻断编辑。
  const [datasets, setDatasets] = useState<DatasetBrief[]>([]);
  const [dsError, setDsError] = useState('');
  const [dsLoadingId, setDsLoadingId] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    client.get<{ items?: DatasetBrief[] }>('/datasets')
      .then(res => {
        if (!cancelled) setDatasets(res.data?.items || []);
      })
      .catch((e: any) => {
        if (!cancelled) setDsError(e?.message || tr('k_80d50b0ff1'));
      });
    return () => { cancelled = true; };
  }, []);

  const loadDatasetSql = async (d: DatasetBrief) => {
    if (d.source_type && d.source_type !== 'sql' && !d.sql_query) {
      toast.info(tr('k_d5d6027e07'));
      return;
    }
    setDsLoadingId(d.id);
    try {
      const { data } = await client.get<DatasetBrief>(`/datasets/${d.id}`);
      if (typeof data?.sql_query === 'string' && data.sql_query) {
        setSql(data.sql_query);
        toast.success(tr('k_6c5e7a542b', { p0: data.name || d.name }));
      } else {
        toast.info(tr('k_da0fdd9d3d'));
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_6002474108'));
    } finally {
      setDsLoadingId(null);
    }
  };


  const ANALYSIS_TABS: { key: ResultTab; label: string; icon: any }[] = [
    { key: 'result', label: tr('k_adaf94c06b'), icon: Play },
    { key: 'ast', label: 'AST', icon: Braces },
    { key: 'lineage', label: tr('k_c3fcb314b4'), icon: GitBranch },
    { key: 'rls', label: tr('k_318420a101'), icon: ShieldCheck },
    { key: 'provenance', label: tr('k_eb15279454'), icon: Crosshair },
  ];

  const analyzePath: Record<string, string> = {
    ast: '/playground/ast', lineage: '/playground/lineage',
    rls: '/playground/rls-diff',
  };

  // 受治理执行: body 只传 sql + datasource_id; 身份由后端从 JWT 解析, 不传 user_id
  const executeSql = async () => {
    if (!sql.trim()) { toast.error(tr('k_5247e599ce')); return; }
    if (!selectedDs) { toast.error(tr('k_192dc9f17f')); return; }
    setExecuting(true);
    setResultTab('result');
    try {
      const { data } = await client.post('/playground/execute', {
        sql: sql.trim(), datasource_id: selectedDs,
      });
      setExecResult(data);
      if (data.error) toast.error(data.error);
      else toast.success(tr('k_2bc548d009', { p0: data.row_count ?? data.rows?.length ?? 0 }));
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : (detail?.error || detail || tr('k_1c83d79715'));
      setExecResult({ error: msg });
      toast.error(msg);
    } finally {
      setExecuting(false);
    }
  };

  const runAnalyze = async (kind: 'ast' | 'lineage' | 'rls') => {
    if (!sql.trim()) { toast.error(tr('k_5247e599ce')); return; }
    if (kind === 'rls' && !selectedDs) { toast.error(tr('k_192dc9f17f')); return; }
    setAnalyzing(kind);
    setResultTab(kind);
    try {
      // 只发待分析的 SQL 与数据源(供改写预览); 身份由后端从 JWT 解析, body 不传 user_id
      const body: any = { sql: sql.trim(), datasource_id: selectedDs };
      const { data } = await client.post(analyzePath[kind], body);
      setAnalysis((a) => ({ ...a, [kind]: data }));
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      setAnalysis((a) => ({ ...a, [kind]: { error: typeof detail === 'string' ? detail : (detail?.error || detail || tr('k_6aee2d39c9')) } }));
      if (typeof detail === 'string') toast.error(detail);
    } finally {
      setAnalyzing(null);
    }
  };

  const runProvenance = async () => {
    if (!provObject.trim()) { toast.error(tr('k_65a84e650e')); return; }
    setAnalyzing('provenance');
    setResultTab('provenance');
    try {
      const { data } = await client.post('/playground/provenance', { object: provObject.trim(), datasource_id: selectedDs || 0 });
      setAnalysis((a) => ({ ...a, provenance: data }));
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      setAnalysis((a) => ({ ...a, provenance: { error: typeof detail === 'string' ? detail : (detail?.error || tr('k_52751b6c6e')) } }));
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
      toast.success(tr('k_3b108349b9'));
      setSaveModalOpen(false);
      setSaveName('');
      setSaveDesc('');
      setIsDataset(false);
      loadSavedQueries();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || tr('k_6de920b4e4'));
    }
  };

  const deleteQuery = async (id: number) => {
    try {
      await client.delete(`/playground/queries/${id}`);
      toast.success(tr('k_5cc232620c'));
      loadSavedQueries();
    } catch {
      toast.error(tr('k_acf0664a54'));
    }
  };

  const copySql = () => {
    navigator.clipboard.writeText(sql);
    toast.success(tr('k_4fb42e6eb1'));
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
              <SelectValue placeholder={t("k_422b9e314a")} />
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
            {tr('k_ef6dce68ce')}</Button>
          <Button variant="outline" onClick={copySql}>
            <Copy className="h-4 w-4 mr-2" />
            {tr('k_79d3abe929')}</Button>
          <Button variant="outline" onClick={() => setSaveModalOpen(true)}>
            <Save className="h-4 w-4 mr-2" />
            {tr('k_fadf24dbc5')}</Button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left sidebar - Tables */}
        <div className={`border-r flex flex-col overflow-hidden transition-all duration-200 ${sidebarCollapsed ? 'w-0' : 'w-[250px]'}`}>
          <div className="p-3 border-b flex items-center justify-between shrink-0">
            <h3 className="text-sm font-semibold">{t("k_61abbaa1c0")}</h3>
            <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setSidebarCollapsed(!sidebarCollapsed)}>
              <ChevronRight className={`h-4 w-4 transition-transform ${sidebarCollapsed ? 'rotate-180' : ''}`} />
            </Button>
          </div>
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-2">
              {!selectedDs && (
                <div className="text-center py-8 text-muted-foreground">
                  <Database className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p className="text-xs">{t("k_192dc9f17f")}</p>
                </div>
              )}
              {selectedDs && tables.length === 0 && (
                <div className="text-center py-8 text-muted-foreground">
                  <Table className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p className="text-xs">{t("k_611f92ed97")}</p>
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
            <h3 className="text-sm font-semibold">{t("k_2c3dddc637")}</h3>
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
                    <div className="text-xs text-muted-foreground">{q.is_dataset ? tr('k_fc2b4735c3') : tr('k_711363c424')}</div>
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

          <Separator className="shrink-0" />
          <div className="p-3 border-b shrink-0">
            <h3 className="text-sm font-semibold">{t("k_e0ddd762f9")}</h3>
          </div>
          <ScrollArea className="flex-1 min-h-0 max-h-[220px]">
            <div className="p-2">
              {dsError ? (
                <p className="px-2 py-1.5 text-xs text-destructive" role="alert">{dsError}</p>
              ) : datasets.length === 0 ? (
                <p className="px-2 py-1.5 text-xs text-muted-foreground">
                  {tr('k_732fcf5cbb')}</p>
              ) : datasets.map(d => (
                <div
                  key={d.id}
                  className="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer hover:bg-muted"
                  title={d.description || d.name}
                  onClick={() => void loadDatasetSql(d)}
                >
                  <Database className="h-4 w-4 text-primary flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{d.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {dsLoadingId === d.id ? tr('k_768bbcdb64') : tr('k_1d643fbe5a', { p0: d.source_type === 'sql' ? 'SQL' : tr('k_668cea6a49') })}
                    </div>
                  </div>
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
              placeholder={tr('k_d5f39cbb75')}
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
                      <span>{t("k_11d0241540")} <span className="font-medium text-foreground">{execResult.row_count ?? execResult.rows?.length ?? 0}</span> {t("k_2a2b777ae4")}</span>
                      {execResult.elapsed_ms != null && <span>{tr('k_a9704e1997')}{' '}{execResult.elapsed_ms}ms</span>}
                      <span className="text-amber-600">{t("k_bce7b02fd4")}</span>
                    </div>
                  )}
                  <div className="flex-1 overflow-auto">
                    {executing ? (
                      <div className="p-4"><Spinner size={16} /></div>
                    ) : !execResult ? (
                      <p className="p-4 text-sm text-muted-foreground">{tr('k_88da0fa337')}</p>
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
                      <p className="p-4 text-sm text-muted-foreground">{t("k_9b7997c71d")}</p>
                    ))}
                  </div>
                </div>
              )}

              {resultTab === 'ast' && (
                <ScrollArea className="h-full">
                  <div className="p-4">
                    {analyzing === 'ast' ? <Spinner size={16} /> : !analysis.ast ? (
                      <p className="text-sm text-muted-foreground">{t("k_5a364fec42")}</p>
                    ) : analysis.ast.error ? (
                      <p className="text-sm text-destructive">{String(analysis.ast.error)}</p>
                    ) : (
                      <div className="space-y-2">
                        <div className="text-xs text-muted-foreground">
                          {tr('k_35032ddf05')}{' '}<span className="font-mono">{analysis.ast.tables?.join(', ') || '—'}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-muted-foreground">{t("k_d4297c3860")}</span>
                          <Button size="sm" variant="ghost" className="h-6 text-xs" onClick={() => setAstRaw((v) => !v)}>
                            {astRaw ? tr('k_e6ebe938df') : tr('k_cf6a4cebcd')}
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
                      <p className="text-sm text-muted-foreground">{t("k_97097b00e3")}</p>
                    ) : analysis.lineage.error ? (
                      <p className="text-sm text-destructive">{String(analysis.lineage.error)}</p>
                    ) : (
                      <div className="space-y-4">
                        {(analysis.lineage.columns || []).map((c: any, i: number) => (
                          <div key={i}>
                            <div className="text-xs font-semibold mb-1">{t("k_ad3ee64830")} <span className="font-mono">{c.column}</span></div>
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
                      <p className="text-sm text-muted-foreground">{tr('k_496a137f22')}</p>
                    ) : (
                      <div className="space-y-3">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <Badge variant="secondary" className="font-normal">{tr('k_f9e104bf5a')}{' '}{(analysis.rls.rlsPolicies || []).join(', ') || tr('k_72077749f7')}</Badge>
                          <Badge variant="outline" className="font-normal">{tr('k_37cd8c0889')}{' '}{(analysis.rls.appliedRls || []).join(', ') || tr('k_72077749f7')}</Badge>
                          {!analysis.rls.changed && <span className="text-muted-foreground">{t("k_bd3f1f8d47")}</span>}
                          {analysis.rls.error && <span className="text-destructive">{String(analysis.rls.error)}</span>}
                        </div>
                        {/* 列级限制明细: 隐藏列已从 securedSql 剔除, 掩码列已替换为脱敏表达式 */}
                        {Object.keys(analysis.rls.columnRestrictions || {}).length > 0 && (
                          <div className="space-y-1.5 rounded-md border border-border/60 bg-muted/30 p-2.5">
                            {Object.entries(analysis.rls.columnRestrictions as Record<string, any>).map(([tbl, spec]: [string, any]) => (
                              <div key={tbl} className="flex flex-wrap items-center gap-2 text-xs">
                                <span className="font-mono text-foreground/80">{tbl}</span>
                                {(spec?.hidden || []).length > 0 && (
                                  <Badge variant="destructive" className="font-normal">{tr('k_a3b53ae21c')}{' '}{spec.hidden.join(', ')}</Badge>
                                )}
                                {Object.keys(spec?.masked || {}).length > 0 && (
                                  <Badge variant="outline" className="font-normal border-amber-400 text-amber-600">
                                    {tr('k_bb804e1c2c')}{' '}{Object.entries(spec.masked as Record<string, string>).map(([c, m]) => `${c}(${m})`).join(', ')}
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
                      placeholder={tr('k_7836413da9')}
                      className="h-7 w-[280px]"
                      onKeyDown={(e) => { if (e.key === 'Enter') runProvenance(); }}
                    />
                    <Button size="sm" className="h-7" onClick={runProvenance} disabled={analyzing === 'provenance'}>
                      {analyzing === 'provenance' ? <Spinner size={12} className="mr-2" /> : <Crosshair className="h-3.5 w-3.5 mr-1" />}
                      {tr('k_eb15279454')}</Button>
                  </div>
                  <div className="flex-1 overflow-auto p-4">
                    {analyzing === 'provenance' ? <Spinner size={16} /> : !analysis.provenance ? (
                      <p className="text-sm text-muted-foreground">{tr('k_679318fa72')}</p>
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
            <DialogTitle>{t("k_4993994952")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>{tr('k_456f3ac6b4')}</Label>
              <Input
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                placeholder={t("k_0a43e312b6")}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("k_412f54dc38")}</Label>
              <Textarea
                value={saveDesc}
                onChange={(e) => setSaveDesc(e.target.value)}
                placeholder={t("k_00f1ce3e12")}
                rows={2}
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={isDataset}
                onCheckedChange={setIsDataset}
              />
              <Label>{t("k_3e0c817ea4")}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveModalOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={saveQuery}>{t("k_fadf24dbc5")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
