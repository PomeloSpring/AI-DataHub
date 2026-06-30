import { useState, useEffect, useMemo } from 'react';
import {
  Search, Database, Code, Globe, X,
  Zap, FileText, Package,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import client from '../api/client';
import DashboardChart, { CHART_TYPES, CHART_TYPE_CATEGORIES, ChartIcon, type ChartTypeItem } from './DashboardChart';

interface DataSource {
  type: 'snapshot' | 'query' | 'dataset';
  id: number;
  name: string;
  description?: string;
  chart_type?: string;
  created_at: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onAdd: (chart: { name: string; chart_type: string; sql_query: string; config: any; source_type: string; source_id: number; data_cache: string }) => void;
}

export default function AddChartModal({ open, onClose, onAdd }: Props) {
  const [step, setStep] = useState<'type' | 'data' | 'preview' | 'widget-config'>('type');
  const [sources, setSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedSource, setSelectedSource] = useState<DataSource | null>(null);
  const [previewRawData, setPreviewRawData] = useState<{ columns: string[]; rows: any[]; sql_query?: string } | null>(null);
  const [chartType, setChartType] = useState('bar');
  const [chartName, setChartName] = useState('');
  const [xCol, setXCol] = useState('');
  const [yCol, setYCol] = useState('');
  const [dataSourceType, setDataSourceType] = useState<'sql' | 'api' | 'existing'>('sql');
  const [sqlQuery, setSqlQuery] = useState('');
  const [apiUrl, setApiUrl] = useState('');
  const [apiMethod, setApiMethod] = useState('GET');
  const [apiBody, setApiBody] = useState('');
  const [typeSearch, setTypeSearch] = useState('');
  const [sysDatasources, setSysDatasources] = useState<any[]>([]);
  const [selectedDsId, setSelectedDsId] = useState<number>(0);

  // Widget config state
  const [paramKey, setParamKey] = useState('');
  const [paramLabel, setParamLabel] = useState('');
  const [paramPlaceholder, setParamPlaceholder] = useState('');
  const [paramDefault, setParamDefault] = useState('');
  const [widgetOptions, setWidgetOptions] = useState('');
  const [numMin, setNumMin] = useState('');
  const [numMax, setNumMax] = useState('');
  const [numStep, setNumStep] = useState('1');

  const isWidget = chartType.startsWith('widget_');
  const isButtonWidget = ['widget_search', 'widget_reset', 'widget_export'].includes(chartType);

  useEffect(() => {
    if (open) {
      loadSources();
      setStep('type');
      setSelectedSource(null);
      setPreviewRawData(null);
      setChartType('bar');
      setChartName('');
      setXCol('');
      setYCol('');
      setDataSourceType('sql');
      setSqlQuery('');
      setApiUrl('');
      setApiMethod('GET');
      setApiBody('');
      setTypeSearch('');
      setSelectedDsId(0);
      setParamKey('');
      setParamLabel('');
      setParamPlaceholder('');
      setParamDefault('');
      setWidgetOptions('');
      setNumMin('');
      setNumMax('');
      setNumStep('1');
      // Load system datasources
      client.get('/datasources/').then(({ data }) => {
        setSysDatasources(data);
        const defaultDs = data.find((d: any) => d.is_default) || data[0];
        if (defaultDs) setSelectedDsId(defaultDs.id);
      }).catch(() => setSysDatasources([]));
    }
  }, [open]);

  const loadSources = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/dashboard/datasources');
      setSources(data);
    } catch {
      setSources([]);
    } finally {
      setLoading(false);
    }
  };

  const loadDataForSource = async (src: DataSource) => {
    setLoading(true);
    try {
      if (src.type === 'snapshot') {
        const { data } = await client.get(`/dashboard/snapshots/${src.id}/data`);
        if (data.columns && data.data_snapshot) {
          return { columns: data.columns, rows: data.data_snapshot, sql_query: data.sql_query || '' };
        }
      } else {
        const { data } = await client.post('/dashboard/preview', { source_type: src.type, source_id: src.id });
        if (data.columns && data.rows) {
          return { columns: data.columns, rows: data.rows, sql_query: '' };
        }
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
    return null;
  };

  const handleExecuteSql = async () => {
    if (!sqlQuery.trim()) return;
    setLoading(true);
    try {
      const { data } = await client.post('/playground/execute', {
        sql: sqlQuery.trim(),
        datasource_id: selectedDsId || undefined,
      });
      if (data.columns && data.rows) {
        setPreviewRawData({ columns: data.columns, rows: data.rows, sql_query: sqlQuery });
        autoSelectColumns(data.columns, data.rows);
        setStep('preview');
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  };

  const handleExecuteApi = async () => {
    if (!apiUrl.trim()) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl, {
        method: apiMethod,
        headers: { 'Content-Type': 'application/json' },
        body: apiMethod === 'POST' ? apiBody : undefined,
      });
      const data = await response.json();
      if (Array.isArray(data) && data.length > 0) {
        const columns = Object.keys(data[0]);
        setPreviewRawData({ columns, rows: data });
        autoSelectColumns(columns, data);
        setStep('preview');
      } else if (data.columns && data.rows) {
        setPreviewRawData(data);
        autoSelectColumns(data.columns, data.rows);
        setStep('preview');
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  };

  const selectSource = async (src: DataSource) => {
    setSelectedSource(src);
    setChartName(src.name?.slice(0, 30) || '');
    setChartType(src.chart_type || 'bar');

    const data = await loadDataForSource(src);
    if (data) {
      setPreviewRawData(data);
      autoSelectColumns(data.columns, data.rows);
      setStep('preview');
    }
  };

  const autoSelectColumns = (columns: string[], rows: any[]) => {
    if (rows.length === 0) return;
    const strCol = columns.find(c => typeof rows[0][c] === 'string');
    const numCol = columns.find(c => typeof rows[0][c] === 'number');
    setXCol(strCol || columns[0] || '');
    setYCol(numCol || columns[1] || columns[0] || '');
  };

  const columns = previewRawData?.columns || [];
  const hasData = previewRawData && previewRawData.rows.length > 0 && columns.length > 0;

  // Filter and group chart types
  const filteredGroups = useMemo(() => {
    const q = typeSearch.toLowerCase().trim();
    const groups: { category: typeof CHART_TYPE_CATEGORIES[number]; items: ChartTypeItem[] }[] = [];
    for (const cat of CHART_TYPE_CATEGORIES) {
      const items = CHART_TYPES.filter(t => {
        if (t.category !== cat.key) return false;
        if (q && !t.label.includes(q) && !t.value.includes(q)) return false;
        return true;
      });
      if (items.length > 0) {
        groups.push({ category: cat, items });
      }
    }
    return groups;
  }, [typeSearch]);

  const selectedTypeInfo = CHART_TYPES.find(t => t.value === chartType);

  const handleAdd = (skipData = false) => {
    const config: any = skipData ? {} : { xCol, yCol, datasource_id: selectedDsId };
    const dataCache = (!skipData && previewRawData) ? JSON.stringify({ columns: previewRawData.columns, rows: previewRawData.rows.slice(0, 200) }) : '';

    let sourceType = skipData ? 'empty' : 'manual';
    let sourceId = 0;
    if (!skipData && dataSourceType === 'existing' && selectedSource) {
      sourceType = selectedSource.type;
      sourceId = selectedSource.id;
    }

    onAdd({
      name: chartName || '未命名图表',
      chart_type: chartType,
      sql_query: skipData ? '' : (sqlQuery || previewRawData?.sql_query || ''),
      config,
      source_type: sourceType,
      source_id: sourceId,
      data_cache: dataCache,
    });
    onClose();
  };

  const filteredSources = (type: string) => sources.filter(s => s.type === type);

  const handleAddWidget = () => {
    const widgetConfig: any = isButtonWidget ? {
      label: paramLabel.trim() || (chartType === 'widget_search' ? '搜索' : chartType === 'widget_reset' ? '重置' : '导出'),
    } : {
      paramKey: paramKey.trim(),
      label: paramLabel.trim() || paramKey.trim(),
      placeholder: paramPlaceholder.trim(),
      defaultValue: paramDefault,
      labelPosition: 'left',
    };

    if (chartType === 'widget_select' || chartType === 'widget_multi_select') {
      widgetConfig.options = widgetOptions
        .split(',')
        .map(s => s.trim())
        .filter(Boolean)
        .map(s => ({ label: s, value: s }));
    }

    if (chartType === 'widget_number') {
      if (numMin) widgetConfig.min = Number(numMin);
      if (numMax) widgetConfig.max = Number(numMax);
      widgetConfig.step = Number(numStep) || 1;
    }

    onAdd({
      name: paramLabel.trim() || paramKey.trim() || (chartType === 'widget_search' ? '搜索' : chartType === 'widget_reset' ? '重置' : chartType === 'widget_export' ? '导出' : '参数控件'),
      chart_type: chartType,
      sql_query: '',
      config: widgetConfig,
      source_type: 'widget',
      source_id: 0,
      data_cache: '',
    });
    onClose();
  };

  const renderFooter = () => {
    if (step === 'type') {
      return (
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          {isWidget ? (
            <Button disabled={!chartType} onClick={() => setStep('widget-config')}>下一步</Button>
          ) : (
            <>
              <Button variant="secondary" disabled={!chartType} onClick={() => handleAdd(true)}>
                直接添加
              </Button>
              <Button disabled={!chartType} onClick={() => setStep('data')}>下一步</Button>
            </>
          )}
        </DialogFooter>
      );
    }
    if (step === 'widget-config') {
      return (
        <DialogFooter>
          <Button variant="outline" onClick={() => setStep('type')}>上一步</Button>
          <Button disabled={!isButtonWidget && !paramKey.trim()} onClick={() => handleAddWidget()}>添加到仪表盘</Button>
        </DialogFooter>
      );
    }
    if (step === 'data') {
      return (
        <DialogFooter>
          <Button variant="outline" onClick={() => setStep('type')}>上一步</Button>
          <Button variant="secondary" onClick={() => handleAdd(true)}>
            跳过，直接添加
          </Button>
          {dataSourceType === 'sql' && (
            <Button disabled={!sqlQuery.trim() || loading} onClick={handleExecuteSql}>
              {loading ? <Spinner className="h-4 w-4 mr-2" /> : null}
              执行SQL
            </Button>
          )}
          {dataSourceType === 'api' && (
            <Button disabled={!apiUrl.trim() || loading} onClick={handleExecuteApi}>
              {loading ? <Spinner className="h-4 w-4 mr-2" /> : null}
              获取数据
            </Button>
          )}
        </DialogFooter>
      );
    }
    return (
      <DialogFooter>
        <Button variant="outline" onClick={() => setStep('data')}>上一步</Button>
        <Button onClick={() => handleAdd(false)}>添加到仪表盘</Button>
      </DialogFooter>
    );
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-[720px] h-[85vh] flex flex-col p-0 gap-0">
        <DialogHeader className="shrink-0 px-6 pt-6 pb-4">
          <DialogTitle>
            {step === 'type' ? '选择图表类型' : step === 'widget-config' ? '配置参数控件' : step === 'data' ? '配置数据源' : '预览图表'}
          </DialogTitle>
        </DialogHeader>

        <ScrollArea className="flex-1 min-h-0 px-6">
          {step === 'type' && (
            <div className="space-y-4">
              {/* Chart name */}
              <div className="space-y-2">
                <Label htmlFor="chart-name">图表名称</Label>
                <Input
                  id="chart-name"
                  value={chartName}
                  onChange={e => setChartName(e.target.value)}
                  placeholder="输入图表名称"
                />
              </div>

              {/* Search */}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  value={typeSearch}
                  onChange={e => setTypeSearch(e.target.value)}
                  placeholder="搜索图表类型..."
                  className="pl-9 pr-8"
                />
                {typeSearch && (
                  <button
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-sm hover:bg-muted text-muted-foreground"
                    onClick={() => setTypeSearch('')}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>

              {/* Selected type preview */}
              {selectedTypeInfo && !typeSearch && (
                <div className="flex items-center gap-3 p-3 rounded-lg bg-primary/5 border border-primary/20">
                  <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
                    <ChartIcon name={selectedTypeInfo.icon} className="h-5 w-5 text-primary" />
                  </div>
                  <div>
                    <div className="text-sm font-medium">{selectedTypeInfo.label}</div>
                    <div className="text-xs text-muted-foreground">{selectedTypeInfo.value}</div>
                  </div>
                </div>
              )}

              {/* Chart type groups */}
              {filteredGroups.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  未找到匹配的图表类型
                </div>
              ) : (
                filteredGroups.map(({ category, items }) => (
                  <div key={category.key} className="space-y-2">
                    <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider px-1">
                      {category.label}
                    </div>
                    <div className="grid grid-cols-4 gap-2">
                      {items.map(ct => {
                        const isSelected = chartType === ct.value;
                        return (
                          <button
                            key={ct.value}
                            type="button"
                            onClick={() => setChartType(ct.value)}
                            className={`
                              flex flex-col items-center justify-center gap-1.5 p-3 rounded-lg
                              min-h-[72px] transition-all duration-150 cursor-pointer text-center
                              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2
                              ${isSelected
                                ? 'bg-primary text-primary-foreground shadow-sm scale-[1.02]'
                                : 'border border-border hover:border-primary/40 hover:bg-accent'
                              }
                            `}
                          >
                            <ChartIcon
                              name={ct.icon}
                              className={`h-5 w-5 ${isSelected ? 'text-primary-foreground' : 'text-muted-foreground'}`}
                            />
                            <span className={`text-xs leading-tight ${isSelected ? 'font-medium text-primary-foreground' : ''}`}>
                              {ct.label}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {step === 'widget-config' && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 p-3 rounded-lg bg-primary/5 border border-primary/20">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
                  <ChartIcon name={selectedTypeInfo?.icon || 'Sliders'} className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <div className="text-sm font-medium">{selectedTypeInfo?.label}</div>
                  <div className="text-xs text-muted-foreground">
                    {isButtonWidget ? '配置按钮控件' : '配置参数控件'}
                  </div>
                </div>
              </div>

              {!isButtonWidget && (
              <div className="space-y-2">
                <Label>参数Key <span className="text-destructive">*</span></Label>
                <Input
                  value={paramKey}
                  onChange={e => setParamKey(e.target.value)}
                  placeholder="如 site, date_start, category"
                />
                <p className="text-xs text-muted-foreground">
                  图表 SQL 中使用 {'{{paramKey}}'} 引用此参数值
                </p>
              </div>
              )}

              <div className="space-y-2">
                <Label>{isButtonWidget ? '按钮文字' : '显示标签'}</Label>
                <Input
                  value={paramLabel}
                  onChange={e => setParamLabel(e.target.value)}
                  placeholder="如 站点、开始日期、分类"
                />
              </div>

              {!isButtonWidget && (
              <>
              <div className="space-y-2">
                <Label>占位提示</Label>
                <Input
                  value={paramPlaceholder}
                  onChange={e => setParamPlaceholder(e.target.value)}
                  placeholder="如 请输入、请选择"
                />
              </div>

              <div className="space-y-2">
                <Label>默认值</Label>
                <Input
                  value={paramDefault}
                  onChange={e => setParamDefault(e.target.value)}
                  placeholder="留空则无默认值"
                />
              </div>
              </>
              )}

              {(chartType === 'widget_select' || chartType === 'widget_multi_select') && (
                <div className="space-y-2">
                  <Label>选项（逗号分隔）</Label>
                  <Input
                    value={widgetOptions}
                    onChange={e => setWidgetOptions(e.target.value)}
                    placeholder="如 北京,上海,广州,深圳"
                  />
                  <p className="text-xs text-muted-foreground">
                    每个选项的标签和值相同。后续版本将支持动态 SQL 选项。
                  </p>
                </div>
              )}

              {chartType === 'widget_number' && (
                <div className="grid grid-cols-3 gap-2">
                  <div className="space-y-2">
                    <Label>最小值</Label>
                    <Input type="number" value={numMin} onChange={e => setNumMin(e.target.value)} placeholder="不限" />
                  </div>
                  <div className="space-y-2">
                    <Label>最大值</Label>
                    <Input type="number" value={numMax} onChange={e => setNumMax(e.target.value)} placeholder="不限" />
                  </div>
                  <div className="space-y-2">
                    <Label>步长</Label>
                    <Input type="number" value={numStep} onChange={e => setNumStep(e.target.value)} placeholder="1" />
                  </div>
                </div>
              )}
            </div>
          )}

          {step === 'data' && (
            <div className="space-y-4">
              <Tabs value={dataSourceType} onValueChange={(v) => setDataSourceType(v as any)}>
                <TabsList className="w-full">
                  <TabsTrigger value="sql" className="flex-1">
                    <Code className="h-4 w-4 mr-2" />
                    手动SQL
                  </TabsTrigger>
                  <TabsTrigger value="api" className="flex-1">
                    <Globe className="h-4 w-4 mr-2" />
                    API查询
                  </TabsTrigger>
                  <TabsTrigger value="existing" className="flex-1">
                    <Database className="h-4 w-4 mr-2" />
                    已有查询
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="sql" className="space-y-4 mt-4">
                  <div className="space-y-2">
                    <Label>数据源</Label>
                    <Select
                      value={String(selectedDsId || '')}
                      onValueChange={(v) => setSelectedDsId(Number(v))}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="选择数据源" />
                      </SelectTrigger>
                      <SelectContent>
                        {sysDatasources.map((ds: any) => (
                          <SelectItem key={ds.id} value={String(ds.id)}>
                            <span className="flex items-center gap-2">
                              <Database className="h-4 w-4 text-muted-foreground" />
                              {ds.name}
                              <Badge variant="outline" className="text-xs">{ds.db_type}</Badge>
                              {ds.is_default ? <Badge variant="secondary" className="text-xs">默认</Badge> : null}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="sql-input">SQL查询</Label>
                    <Textarea
                      id="sql-input"
                      value={sqlQuery}
                      onChange={e => setSqlQuery(e.target.value)}
                      placeholder="SELECT * FROM table_name WHERE ..."
                      rows={8}
                      className="font-mono text-sm"
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    选择数据源后输入SQL查询语句，点击"执行SQL"按钮获取数据
                  </p>
                </TabsContent>

                <TabsContent value="api" className="space-y-4 mt-4">
                  <div className="space-y-2">
                    <Label htmlFor="api-url">API地址</Label>
                    <Input
                      id="api-url"
                      value={apiUrl}
                      onChange={e => setApiUrl(e.target.value)}
                      placeholder="https://api.example.com/data"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>请求方法</Label>
                      <Select value={apiMethod} onValueChange={setApiMethod}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="GET">GET</SelectItem>
                          <SelectItem value="POST">POST</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    {apiMethod === 'POST' && (
                      <div className="space-y-2">
                        <Label htmlFor="api-body">请求体 (JSON)</Label>
                        <Input
                          id="api-body"
                          value={apiBody}
                          onChange={e => setApiBody(e.target.value)}
                          placeholder='{"key": "value"}'
                        />
                      </div>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    API应返回 JSON 数组或 {"{ columns: [], rows: [] }"} 格式
                  </p>
                </TabsContent>

                <TabsContent value="existing" className="mt-4">
                  <Tabs defaultValue="snapshot">
                    <TabsList>
                      <TabsTrigger value="snapshot">
                        <Zap className="h-4 w-4 mr-2" />
                        近期查询
                      </TabsTrigger>
                      <TabsTrigger value="query">
                        <FileText className="h-4 w-4 mr-2" />
                        我的查询
                      </TabsTrigger>
                      <TabsTrigger value="dataset">
                        <Database className="h-4 w-4 mr-2" />
                        数据集
                      </TabsTrigger>
                    </TabsList>
                    {['snapshot', 'query', 'dataset'].map(type => (
                      <TabsContent key={type} value={type}>
                        <SourceList
                          sources={filteredSources(type)}
                          loading={loading}
                          onSelect={selectSource}
                        />
                      </TabsContent>
                    ))}
                  </Tabs>
                </TabsContent>
              </Tabs>
            </div>
          )}

          {step === 'preview' && (
            <div className="space-y-4">
              <div className="flex gap-2 items-center">
                <Badge variant="default">
                  {selectedTypeInfo ? (
                    <span className="flex items-center gap-1.5">
                      <ChartIcon name={selectedTypeInfo.icon} className="h-3 w-3" />
                      {selectedTypeInfo.label}
                    </span>
                  ) : chartType}
                </Badge>
                <span className="text-sm text-muted-foreground">{chartName}</span>
                {hasData && <Badge variant="secondary">{previewRawData.rows.length} 行数据</Badge>}
              </div>

              {hasData && (
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>X 轴 / 分类</Label>
                    <Select value={xCol} onValueChange={setXCol}>
                      <SelectTrigger>
                        <SelectValue placeholder="选择列" />
                      </SelectTrigger>
                      <SelectContent>
                        {columns.map((c: string) => (
                          <SelectItem key={c} value={c}>{c}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label>Y 轴 / 数值</Label>
                    <Select value={yCol} onValueChange={setYCol}>
                      <SelectTrigger>
                        <SelectValue placeholder="选择列" />
                      </SelectTrigger>
                      <SelectContent>
                        {columns
                          .filter((c: string) => previewRawData && previewRawData.rows.length > 0 && typeof previewRawData.rows[0][c] === 'number')
                          .map((c: string) => (
                            <SelectItem key={c} value={c}>{c}</SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}

              {hasData ? (
                <div className="h-[300px] border rounded-lg p-2">
                  <DashboardChart chartType={chartType} data={{ columns, rows: previewRawData.rows }} config={{ xCol, yCol }} />
                </div>
              ) : (
                <div className="h-[300px] flex items-center justify-center text-muted-foreground border rounded-lg">
                  {loading ? <Spinner size={32} /> : '暂无数据，请返回上一步配置数据源'}
                </div>
              )}
            </div>
          )}
        </ScrollArea>

        <div className="shrink-0 border-t bg-background px-6 py-4">
          {renderFooter()}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SourceList({ sources, loading, onSelect }: {
  sources: DataSource[]; loading: boolean; onSelect: (s: DataSource) => void;
}) {
  if (loading) return <div className="flex justify-center py-12"><Spinner size={32} /></div>;
  if (sources.length === 0) return <div className="text-center py-12 text-muted-foreground">暂无数据</div>;

  const SOURCE_ICONS: Record<string, React.ComponentType<any>> = {
    snapshot: Zap,
    query: FileText,
    dataset: Package,
  };
  const SOURCE_LABELS: Record<string, string> = {
    snapshot: '近期查询',
    query: '查询',
    dataset: '数据集',
  };

  return (
    <ScrollArea className="h-[300px]">
      <div className="space-y-1">
        {sources.map((item) => {
          const Icon = SOURCE_ICONS[item.type] || Database;
          return (
            <div
              key={item.id}
              className="flex items-center gap-3 p-3 rounded-lg cursor-pointer hover:bg-accent transition-colors min-h-[48px]"
              onClick={() => onSelect(item)}
            >
              <div className="w-8 h-8 rounded bg-muted flex items-center justify-center shrink-0">
                <Icon className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{item.name}</div>
                <div className="flex gap-2 mt-1">
                  <Badge variant="outline" className="text-xs">
                    {SOURCE_LABELS[item.type] || item.type}
                  </Badge>
                  {item.chart_type && <Badge variant="outline" className="text-xs">{item.chart_type}</Badge>}
                  <span className="text-xs text-muted-foreground">{item.created_at?.slice(0, 16)}</span>
                </div>
              </div>
              <Button size="sm" variant="ghost" className="shrink-0">选择</Button>
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}
