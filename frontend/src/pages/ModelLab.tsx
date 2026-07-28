import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import {
  FlaskConical, Cpu, Search, RotateCcw, Clock, Zap,
  CheckCircle, AlertCircle, Database, BarChart3, Layers,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import client from '../api/client';

interface ModelInfo {
  model_path: string;
  embedding_dim: number;
  model_loaded: boolean;
  model_type: string;
  cache_size?: number;
}

interface Step {
  name: string;
  duration_ms: number;
  detail: string;
}

interface EmbedResult {
  vector: number[];
  vector_full: number[];
  dim: number;
  stats: {
    min: number;
    max: number;
    mean: number;
    nonzero_count: number;
    total_dim: number;
  };
  model_type: string;
  cache_hit: boolean;
  steps: Step[];
  total_ms: number;
}

interface SearchResult {
  rank: number;
  table_name: string;
  table_comment?: string;
  table_business_desc?: string;
  keywords?: string;
  column_name?: string;
  data_type?: string;
  column_comment?: string;
  distance: number;
}

interface SearchResponse {
  results: SearchResult[];
  query_text: string;
  query_vector?: number[];
  datasource_id: number;
  steps: Step[];
  total_ms: number;
}

interface WhatIfRow {
  table_name: string;
  table_comment: string;
  original_keywords: string;
  edited_keywords: string;
  original_distance: number;
  simulated_distance: number | null;
  simulating: boolean;
}

interface Datasource {
  id: number;
  name: string;
  db_type: string;
}

/** Compute L2 (Euclidean) distance between two vectors */
function l2Distance(a: number[], b: number[]): number {
  let sum = 0;
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const d = a[i] - b[i];
    sum += d * d;
  }
  return Math.sqrt(sum);
}

export default function ModelLab() {
  const [activeTab, setActiveTab] = useState<'embed' | 'search' | 'info'>('embed');
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [loading, setLoading] = useState(false);

  // Embed tab state
  const [embedText, setEmbedText] = useState('');
  const [embedResult, setEmbedResult] = useState<EmbedResult | null>(null);

  // Search tab state
  const [searchText, setSearchText] = useState('');
  const [searchDs, setSearchDs] = useState<number>(0);
  const [searchLimit, setSearchLimit] = useState(10);
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [searchTab, setSearchTab] = useState<'tables' | 'columns'>('tables');
  const [datasources, setDatasources] = useState<Datasource[]>([]);

  // What-if state
  const [whatIfRows, setWhatIfRows] = useState<WhatIfRow[]>([]);
  const [whatIfVisible, setWhatIfVisible] = useState(false);

  useEffect(() => {
    loadModelInfo();
    loadDatasources();
  }, []);

  const loadModelInfo = async () => {
    try {
      const { data } = await client.get('/model-lab/info');
      setModelInfo(data);
    } catch (e) {
      console.error('Failed to load model info:', e);
    }
  };

  const loadDatasources = async () => {
    try {
      const { data } = await client.get('/datasources');
      setDatasources(data);
    } catch (e) {
      console.error('Failed to load datasources:', e);
    }
  };

  const handleEmbed = async () => {
    if (!embedText.trim()) {
      toast.error('请输入文本');
      return;
    }
    setLoading(true);
    setEmbedResult(null);
    try {
      const { data } = await client.post('/model-lab/embed', { text: embedText });
      setEmbedResult(data);
      toast.success(`Embedding 生成成功，耗时 ${data.total_ms}ms`);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || 'Embedding 生成失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async () => {
    if (!searchText.trim()) {
      toast.error('请输入检索文本');
      return;
    }
    setLoading(true);
    setSearchResult(null);
    setWhatIfRows([]);
    setWhatIfVisible(false);
    try {
      const endpoint = searchTab === 'tables' ? '/model-lab/search' : '/model-lab/search-columns';
      const { data } = await client.post(endpoint, {
        text: searchText,
        datasource_id: searchDs,
        limit: searchLimit,
      });
      setSearchResult(data);
      // Initialize what-if rows
      if (data.results && searchTab === 'tables') {
        setWhatIfRows(data.results.map((r: SearchResult) => ({
          table_name: r.table_name,
          table_comment: r.table_comment,
          original_keywords: r.keywords || '',
          edited_keywords: r.keywords || '',
          original_distance: r.distance,
          simulated_distance: null,
          simulating: false,
        })));
      }
      toast.success(`检索完成，返回 ${data.results.length} 条结果，耗时 ${data.total_ms}ms`);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '检索失败');
    } finally {
      setLoading(false);
    }
  };

  const handleWhatIfSimulate = async (index: number) => {
    if (!searchResult?.query_vector) return;
    const row = whatIfRows[index];
    const embedText = `${row.table_name} ${row.table_comment} ${row.edited_keywords}`.trim();

    setWhatIfRows(prev => prev.map((r, i) => i === index ? { ...r, simulating: true } : r));
    try {
      const { data } = await client.post('/model-lab/embed', { text: embedText });
      const simDist = l2Distance(searchResult.query_vector!, data.vector_full);
      setWhatIfRows(prev => prev.map((r, i) =>
        i === index ? { ...r, simulated_distance: simDist, simulating: false } : r
      ));
    } catch {
      toast.error('模拟计算失败');
      setWhatIfRows(prev => prev.map((r, i) => i === index ? { ...r, simulating: false } : r));
    }
  };

  const handleWhatIfReset = () => {
    if (!searchResult?.results) return;
    setWhatIfRows(searchResult.results.map((r: SearchResult) => ({
      table_name: r.table_name,
      table_comment: r.table_comment,
      original_keywords: r.keywords || '',
      edited_keywords: r.keywords || '',
      original_distance: r.distance,
      simulated_distance: null,
      simulating: false,
    })));
  };

  // Sorted what-if rows (by simulated distance if available, otherwise original)
  const sortedWhatIfRows = [...whatIfRows].sort((a, b) => {
    const da = a.simulated_distance ?? a.original_distance;
    const db = b.simulated_distance ?? b.original_distance;
    return da - db;
  });

  const handleReload = async (modelPath?: string) => {
    setLoading(true);
    try {
      const { data } = await client.post('/model-lab/reload', { model_path: modelPath });
      setModelInfo(data.model_info);
      toast.success(`模型重载成功，耗时 ${data.total_ms}ms`);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '模型重载失败');
    } finally {
      setLoading(false);
    }
  };

  const getDistanceColor = (distance: number) => {
    if (distance < 0.5) return 'text-green-600 bg-green-50';
    if (distance < 1.0) return 'text-yellow-600 bg-yellow-50';
    if (distance < 1.5) return 'text-orange-600 bg-orange-50';
    return 'text-red-600 bg-red-50';
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-5 w-5 text-primary" />
          <h1 className="text-xl font-bold">模型 Lab</h1>
          <Badge variant="outline" className="text-xs">调试工具</Badge>
        </div>
        <div className="flex items-center gap-2">
          {modelInfo && (
            <Badge variant={modelInfo.model_loaded ? 'default' : 'destructive'} className="text-xs">
              {modelInfo.model_loaded ? '模型已加载' : '使用 Hash Fallback'}
            </Badge>
          )}
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex border-b">
        <button
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 ${
            activeTab === 'embed'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
          onClick={() => setActiveTab('embed')}
        >
          <Cpu className="h-4 w-4" />
          Embedding 测试
        </button>
        <button
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 ${
            activeTab === 'search'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
          onClick={() => setActiveTab('search')}
        >
          <Search className="h-4 w-4" />
          向量检索测试
        </button>
        <button
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 ${
            activeTab === 'info'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
          onClick={() => setActiveTab('info')}
        >
          <Cpu className="h-4 w-4" />
          模型信息
        </button>
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'embed' && (
          <div className="h-full flex">
            {/* Left: Input */}
            <div className="w-1/2 border-r flex flex-col">
              <div className="p-4 border-b">
                <Label className="text-sm font-medium">输入文本</Label>
                <Textarea
                  value={embedText}
                  onChange={(e) => setEmbedText(e.target.value)}
                  placeholder="输入要生成 Embedding 的文本..."
                  className="mt-2 min-h-[120px]"
                />
                <Button onClick={handleEmbed} disabled={loading} className="mt-3">
                  {loading ? <Spinner className="mr-2" size={16} /> : <Zap className="h-4 w-4 mr-2" />}
                  生成 Embedding
                </Button>
              </div>
              <div className="flex-1 p-4 overflow-auto">
                <h3 className="text-sm font-medium mb-3">执行过程</h3>
                {embedResult?.steps ? (
                  <div className="space-y-2">
                    {embedResult.steps.map((step, i) => (
                      <StepCard key={i} step={step} index={i} />
                    ))}
                    <div className="flex items-center gap-2 p-2 bg-muted rounded-md">
                      <Clock className="h-4 w-4 text-muted-foreground" />
                      <span className="text-sm font-medium">总耗时: {embedResult.total_ms}ms</span>
                    </div>
                  </div>
                ) : (
                  <div className="text-center text-muted-foreground py-8">
                    <FlaskConical className="h-8 w-8 mx-auto mb-2 opacity-50" />
                    <p className="text-sm">点击"生成 Embedding"查看执行过程</p>
                  </div>
                )}
              </div>
            </div>

            {/* Right: Results */}
            <div className="w-1/2 flex flex-col overflow-hidden">
              <div className="p-4 border-b">
                <h3 className="text-sm font-medium">Embedding 结果</h3>
              </div>
              <ScrollArea className="flex-1 p-4">
                {embedResult ? (
                  <div className="space-y-4">
                    {/* Stats */}
                    <Card className="p-4">
                      <h4 className="text-sm font-medium mb-3 flex items-center gap-2">
                        <BarChart3 className="h-4 w-4" />
                        向量统计
                      </h4>
                      <div className="grid grid-cols-2 gap-3">
                        <StatItem label="维度" value={`${embedResult.dim}`} />
                        <StatItem label="模型类型" value={embedResult.model_type} />
                        <StatItem label="最小值" value={embedResult.stats.min.toFixed(6)} />
                        <StatItem label="最大值" value={embedResult.stats.max.toFixed(6)} />
                        <StatItem label="均值" value={embedResult.stats.mean.toFixed(6)} />
                        <StatItem label="非零元素" value={`${embedResult.stats.nonzero_count}/${embedResult.stats.total_dim}`} />
                        <StatItem label="缓存命中" value={embedResult.cache_hit ? '是' : '否'} />
                      </div>
                    </Card>

                    {/* Vector Preview */}
                    <Card className="p-4">
                      <h4 className="text-sm font-medium mb-3 flex items-center gap-2">
                        <Layers className="h-4 w-4" />
                        向量预览 (前 50 个值)
                      </h4>
                      <div className="font-mono text-xs bg-muted p-3 rounded-md overflow-x-auto">
                        [{embedResult.vector.map((v, i) => (
                          <span key={i}>
                            {i > 0 && ', '}
                            <span className={v === 0 ? 'text-muted-foreground' : 'text-foreground'}>
                              {v.toFixed(4)}
                            </span>
                          </span>
                        ))}]
                      </div>
                    </Card>
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-full text-muted-foreground">
                    <div className="text-center">
                      <Cpu className="h-12 w-12 mx-auto mb-2 opacity-50" />
                      <p>生成 Embedding 后查看结果</p>
                    </div>
                  </div>
                )}
              </ScrollArea>
            </div>
          </div>
        )}

        {activeTab === 'search' && (
          <div className="h-full flex">
            {/* Left: Input & Steps */}
            <div className="w-1/2 border-r flex flex-col">
              <div className="p-4 border-b space-y-3">
                <div>
                  <Label className="text-sm font-medium">检索文本</Label>
                  <Textarea
                    value={searchText}
                    onChange={(e) => setSearchText(e.target.value)}
                    placeholder="输入自然语言问题..."
                    className="mt-2 min-h-[80px]"
                  />
                </div>
                <div className="flex gap-3">
                  <div className="flex-1">
                    <Label className="text-sm">数据源</Label>
                    <Select value={searchDs.toString()} onValueChange={(v) => setSearchDs(parseInt(v))}>
                      <SelectTrigger className="mt-1">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="0">全部数据源</SelectItem>
                        {datasources.map((ds) => (
                          <SelectItem key={ds.id} value={ds.id.toString()}>
                            <div className="flex items-center gap-2">
                              <Database className="h-3.5 w-3.5" />
                              <span>{ds.name}</span>
                              <Badge variant="outline" className="text-xs">{ds.db_type}</Badge>
                            </div>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="w-24">
                    <Label className="text-sm">Top-K</Label>
                    <Input
                      type="number"
                      min={1}
                      max={50}
                      value={searchLimit}
                      onChange={(e) => setSearchLimit(parseInt(e.target.value) || 10)}
                      className="mt-1"
                    />
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button
                    onClick={handleSearch}
                    disabled={loading}
                    variant={searchTab === 'tables' ? 'default' : 'outline'}
                    className="flex-1"
                    onClickCapture={() => setSearchTab('tables')}
                  >
                    {loading && searchTab === 'tables' ? <Spinner className="mr-2" size={16} /> : <Search className="h-4 w-4 mr-2" />}
                    表检索
                  </Button>
                  <Button
                    onClick={handleSearch}
                    disabled={loading}
                    variant={searchTab === 'columns' ? 'default' : 'outline'}
                    className="flex-1"
                    onClickCapture={() => setSearchTab('columns')}
                  >
                    {loading && searchTab === 'columns' ? <Spinner className="mr-2" size={16} /> : <Search className="h-4 w-4 mr-2" />}
                    列检索
                  </Button>
                </div>
              </div>
              <div className="flex-1 p-4 overflow-auto">
                <h3 className="text-sm font-medium mb-3">执行过程</h3>
                {searchResult?.steps ? (
                  <div className="space-y-2">
                    {searchResult.steps.map((step, i) => (
                      <StepCard key={i} step={step} index={i} />
                    ))}
                    <div className="flex items-center gap-2 p-2 bg-muted rounded-md">
                      <Clock className="h-4 w-4 text-muted-foreground" />
                      <span className="text-sm font-medium">总耗时: {searchResult.total_ms}ms</span>
                    </div>
                  </div>
                ) : (
                  <div className="text-center text-muted-foreground py-8">
                    <Search className="h-8 w-8 mx-auto mb-2 opacity-50" />
                    <p className="text-sm">执行检索后查看执行过程</p>
                  </div>
                )}
              </div>
            </div>

            {/* Right: Results */}
            <div className="w-1/2 flex flex-col overflow-hidden">
              <div className="p-4 border-b">
                <h3 className="text-sm font-medium">
                  检索结果 ({searchResult?.results?.length || 0} 条)
                </h3>
              </div>
              <ScrollArea className="flex-1">
                {searchResult?.results?.length ? (
                  <>
                    {/* Toggle what-if mode */}
                    {searchTab === 'tables' && searchResult.query_vector && (
                      <div className="px-3 py-2 border-b flex items-center gap-2">
                        <Button
                          size="sm"
                          variant={whatIfVisible ? 'default' : 'outline'}
                          onClick={() => setWhatIfVisible(!whatIfVisible)}
                        >
                          <Zap className="h-3.5 w-3.5 mr-1" />
                          What-If 实验
                        </Button>
                        {whatIfVisible && (
                          <Button size="sm" variant="ghost" onClick={handleWhatIfReset}>
                            重置
                          </Button>
                        )}
                      </div>
                    )}

                    {!whatIfVisible ? (
                      /* Normal results table */
                      <table className="w-full">
                        <thead className="sticky top-0 z-10 bg-muted/80">
                          <tr className="border-b">
                            <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground w-12">#</th>
                            {searchTab === 'tables' ? (
                              <>
                                <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground">表名</th>
                                <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground">注释</th>
                                <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground">关键词</th>
                              </>
                            ) : (
                              <>
                                <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground">表名</th>
                                <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground">字段</th>
                                <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground">类型</th>
                                <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground">注释</th>
                              </>
                            )}
                            <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground w-24">距离</th>
                          </tr>
                        </thead>
                        <tbody>
                          {searchResult.results.map((row) => (
                            <tr key={row.rank} className="border-b hover:bg-muted/50">
                              <td className="px-3 py-2 text-xs text-muted-foreground">{row.rank}</td>
                              <td className="px-3 py-2 text-xs font-mono">{row.table_name}</td>
                              {searchTab === 'tables' ? (
                                <>
                                  <td className="px-3 py-2 text-xs">{row.table_comment || '-'}</td>
                                  <td className="px-3 py-2 text-xs text-muted-foreground max-w-[150px] truncate">{row.keywords || '-'}</td>
                                </>
                              ) : (
                                <>
                                  <td className="px-3 py-2 text-xs font-mono">{row.column_name || '-'}</td>
                                  <td className="px-3 py-2 text-xs"><Badge variant="outline" className="text-xs">{row.data_type || '-'}</Badge></td>
                                  <td className="px-3 py-2 text-xs max-w-[200px] truncate">{row.column_comment || '-'}</td>
                                </>
                              )}
                              <td className="px-3 py-2">
                                <Badge variant="outline" className={`text-xs ${getDistanceColor(row.distance)}`}>
                                  {row.distance.toFixed(4)}
                                </Badge>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      /* What-if editable table */
                      <table className="w-full">
                        <thead className="sticky top-0 z-10 bg-muted/80">
                          <tr className="border-b">
                            <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground w-8">#</th>
                            <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground">表名</th>
                            <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground w-[200px]">模拟关键词</th>
                            <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground w-20">原距离</th>
                            <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground w-20">模拟距离</th>
                            <th className="h-8 px-3 text-left text-xs font-medium text-muted-foreground w-16">操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sortedWhatIfRows.map((row, sortedIdx) => {
                            const origIdx = whatIfRows.findIndex(r => r.table_name === row.table_name);
                            return (
                              <tr key={row.table_name} className="border-b hover:bg-muted/50">
                                <td className="px-3 py-1.5 text-xs text-muted-foreground">{sortedIdx + 1}</td>
                                <td className="px-3 py-1.5">
                                  <div className="text-xs font-mono">{row.table_name}</div>
                                  <div className="text-xs text-muted-foreground truncate max-w-[180px]">{row.table_comment}</div>
                                </td>
                                <td className="px-3 py-1.5">
                                  <Input
                                    value={row.edited_keywords}
                                    onChange={(e) => {
                                      const val = e.target.value;
                                      setWhatIfRows(prev => prev.map((r, i) =>
                                        i === origIdx ? { ...r, edited_keywords: val, simulated_distance: null } : r
                                      ));
                                    }}
                                    placeholder="输入模拟关键词"
                                    className="h-7 text-xs"
                                  />
                                </td>
                                <td className="px-3 py-1.5">
                                  <Badge variant="outline" className={`text-xs ${getDistanceColor(row.original_distance)}`}>
                                    {row.original_distance.toFixed(4)}
                                  </Badge>
                                </td>
                                <td className="px-3 py-1.5">
                                  {row.simulating ? (
                                    <Spinner size={14} />
                                  ) : row.simulated_distance !== null ? (
                                    <Badge variant="outline" className={`text-xs ${getDistanceColor(row.simulated_distance)}`}>
                                      {row.simulated_distance.toFixed(4)}
                                      {row.simulated_distance < row.original_distance && (
                                        <span className="ml-1 text-green-600">↓</span>
                                      )}
                                      {row.simulated_distance > row.original_distance && (
                                        <span className="ml-1 text-red-600">↑</span>
                                      )}
                                    </Badge>
                                  ) : (
                                    <span className="text-xs text-muted-foreground">-</span>
                                  )}
                                </td>
                                <td className="px-3 py-1.5">
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    className="h-6 px-2 text-xs"
                                    onClick={() => handleWhatIfSimulate(origIdx)}
                                    disabled={row.simulating || row.edited_keywords === row.original_keywords}
                                  >
                                    模拟
                                  </Button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}
                  </>
                ) : (
                  <div className="flex items-center justify-center h-full text-muted-foreground">
                    <div className="text-center">
                      <Search className="h-12 w-12 mx-auto mb-2 opacity-50" />
                      <p>执行检索后查看结果</p>
                    </div>
                  </div>
                )}
              </ScrollArea>
            </div>
          </div>
        )}

        {activeTab === 'info' && (
          <div className="p-6 max-w-2xl mx-auto">
            <Card className="p-6">
              <h3 className="text-lg font-medium mb-4 flex items-center gap-2">
                <Cpu className="h-5 w-5" />
                模型状态
              </h3>
              {modelInfo ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem label="模型路径" value={modelInfo.model_path || '未加载'} />
                    <StatItem label="向量维度" value={`${modelInfo.embedding_dim}`} />
                    <StatItem label="模型类型" value={modelInfo.model_type} />
                    <StatItem label="加载状态" value={modelInfo.model_loaded ? '已加载' : '未加载'} />
                    <StatItem label="缓存大小" value={`${modelInfo.cache_size ?? 0} 条`} />
                  </div>
                  <Separator />
                  <div className="flex gap-3">
                    <Button onClick={() => handleReload()} disabled={loading}>
                      {loading ? <Spinner className="mr-2" size={16} /> : <RotateCcw className="h-4 w-4 mr-2" />}
                      重新加载模型
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <Spinner className="mx-auto mb-2" size={24} />
                  <p>加载模型信息中...</p>
                </div>
              )}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

function StepCard({ step, index }: { step: Step; index: number }) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-md border bg-card">
      <div className="flex-shrink-0 w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center">
        <span className="text-xs font-medium text-primary">{index + 1}</span>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">{step.name}</span>
          <Badge variant="outline" className="text-xs">
            <Clock className="h-3 w-3 mr-1" />
            {step.duration_ms}ms
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground mt-1">{step.detail}</p>
      </div>
    </div>
  );
}

function StatItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <p className="text-sm font-medium font-mono">{value}</p>
    </div>
  );
}
