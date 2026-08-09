import { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import {
  Play, Save, Code, Trash2, Table, FileText,
  BarChart3, Copy, Database, ChevronRight,
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
import DashboardChart, { CHART_TYPES, ChartIcon } from '../components/DashboardChart';
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

export default function Playground() {
  const [sql, setSql] = useState('');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
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
  const [chartType, setChartType] = useState('bar');
  const [resultTab, setResultTab] = useState<'result' | 'chart'>('result');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  const selectedDsType = datasources.find(d => d.id === selectedDs)?.db_type || '';

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

  const executeSql = async () => {
    if (!sql.trim()) {
      toast.error('请输入 SQL');
      return;
    }
    if (!selectedDs) {
      toast.error('请先选择数据源');
      return;
    }
    setLoading(true);
    try {
      const { data } = await client.post('/playground/execute', {
        sql: sql.trim(),
        datasource_id: selectedDs,
      });
      setResult(data);
      if (data.error) {
        toast.error(data.error);
      } else {
        toast.success(`查询成功，返回 ${data.rows?.length || 0} 行`);
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '执行失败');
    } finally {
      setLoading(false);
    }
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

  const hasResult = result && !result.error && result.columns && result.rows;

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
          <Button onClick={executeSql} disabled={loading}>
            {loading ? <Spinner className="mr-2" size={16} /> : <Play className="h-4 w-4 mr-2" />}
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
              placeholder={selectedDsType === 'elasticsearch'
                ? '输入 ES SQL 查询，索引名需双引号包裹...\n例如: SELECT * FROM "my_index" WHERE "field" = \'value\' LIMIT 10\n(Ctrl+Enter 执行)'
                : '输入 SQL 查询... (Ctrl+Enter 执行)'}
              className="h-full resize-none font-mono text-sm rounded-none border-0"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  executeSql();
                }
              }}
            />
          </div>

          {/* Results area */}
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* Result title bar with tabs */}
            <div className="border-b flex items-center justify-between px-3 h-10 flex-shrink-0">
              <div className="flex items-center gap-1">
                <button
                  className={`px-3 py-1 text-sm font-medium rounded-t transition-colors ${
                    resultTab === 'result'
                      ? 'text-foreground border-b-2 border-primary'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                  onClick={() => setResultTab('result')}
                >
                  <Table className="h-3.5 w-3.5 inline mr-1.5" />
                  结果
                </button>
                <button
                  className={`px-3 py-1 text-sm font-medium rounded-t transition-colors ${
                    resultTab === 'chart'
                      ? 'text-foreground border-b-2 border-primary'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                  onClick={() => setResultTab('chart')}
                >
                  <BarChart3 className="h-3.5 w-3.5 inline mr-1.5" />
                  图表
                </button>
              </div>
              {hasResult && (
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span>返回 <span className="font-medium text-foreground">{result.row_count ?? result.rows?.length ?? 0}</span> 行</span>
                  {result.elapsed_ms != null && (
                    <span>耗时 <span className="font-medium text-foreground">{result.elapsed_ms}ms</span></span>
                  )}
                </div>
              )}
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-hidden">
              {result?.error ? (
                <div className="p-4 text-destructive">{result.error}</div>
              ) : resultTab === 'result' ? (
                /* Result table */
                hasResult ? (
                  <div className="h-full overflow-auto">
                    <table className="w-full">
                      <thead className="sticky top-0 z-10">
                        <tr className="border-b bg-muted/80">
                          <th className="h-8 px-3 text-left align-middle font-medium text-muted-foreground text-xs w-12">#</th>
                          {result.columns.map((col: string) => (
                            <th key={col} className="h-8 px-3 text-left align-middle font-medium text-muted-foreground text-xs">
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows?.map((row: any, i: number) => (
                          <tr key={i} className="border-b hover:bg-muted/50">
                            <td className="px-3 py-1.5 text-xs text-muted-foreground">{i + 1}</td>
                            {result.columns.map((col: string) => (
                              <td key={col} className="px-3 py-1.5 text-xs">
                                {row[col]?.toString() || '-'}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-full text-muted-foreground">
                    <div className="text-center">
                      <Code className="h-12 w-12 mx-auto mb-2 opacity-50" />
                      <p>执行 SQL 查看结果</p>
                      <p className="text-xs mt-1">Ctrl+Enter 快速执行</p>
                    </div>
                  </div>
                )
              ) : (
                /* Chart view */
                hasResult ? (
                  <div className="h-full flex flex-col">
                    <div className="p-2 border-b flex items-center gap-2 flex-shrink-0">
                      <Select value={chartType} onValueChange={setChartType}>
                        <SelectTrigger className="h-7 w-[160px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {CHART_TYPES.map(ct => (
                            <SelectItem key={ct.value} value={ct.value}>
                              <span className="flex items-center gap-2">
                                <ChartIcon name={ct.icon} className="h-4 w-4" />
                                {ct.label}
                              </span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex-1 p-2 overflow-hidden">
                      <DashboardChart
                        chartType={chartType}
                        data={{ columns: result.columns, rows: result.rows }}
                        config={{}}
                      />
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-full text-muted-foreground">
                    <div className="text-center">
                      <BarChart3 className="h-12 w-12 mx-auto mb-2 opacity-50" />
                      <p>执行 SQL 后查看图表</p>
                    </div>
                  </div>
                )
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
