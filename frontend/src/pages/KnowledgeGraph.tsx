import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import {
  Database, Network, GitBranch, Search, RefreshCw,
  Plus, Edit, Terminal, BarChart3, Layers, Save, Trash2
} from 'lucide-react';
import KnowledgeGraphView from '@/components/graph/KnowledgeGraph';
import { useGraphStore } from '@/stores/graphStore';
import axios from 'axios';
import { toast } from 'sonner';

// ── Types ──────────────────────────────────────────────────────────────

type GraphType = 'table-relation' | 'business-knowledge' | 'data-lineage';
type ViewMode = 'view' | 'edit' | 'ask';
type ActiveTab = 'graph' | 'metrics' | 'dimensions' | 'cypher';

interface Metric {
  id: number;
  name: string;
  name_en: string;
  formula: string;
  unit: string;
  agg_type: string;
  target_table: string;
  target_column: string;
  description: string;
  category: string;
}

interface Dimension {
  id: number;
  name: string;
  name_en: string;
  hierarchy: string;
  level: number;
  target_table: string;
  target_column: string;
  description: string;
  category: string;
}

// ── API Helpers ────────────────────────────────────────────────────────

const API_BASE = '/api/graph';

function getAuthHeader() {
  const token = localStorage.getItem('token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Main Component ─────────────────────────────────────────────────────

export default function KnowledgeGraph() {
  const [searchParams, setSearchParams] = useSearchParams();

  // State from URL
  const graphType = (searchParams.get('type') as GraphType) || 'table-relation';
  const viewMode = (searchParams.get('mode') as ViewMode) || 'view';

  // Local state
  const [activeTab, setActiveTab] = useState<ActiveTab>('graph');
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // Metrics & Dimensions state
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [dimensions, setDimensions] = useState<Dimension[]>([]);
  const [showMetricDialog, setShowMetricDialog] = useState(false);
  const [showDimensionDialog, setShowDimensionDialog] = useState(false);
  const [editingMetric, setEditingMetric] = useState<Metric | null>(null);
  const [editingDimension, setEditingDimension] = useState<Dimension | null>(null);
  const [metricForm, setMetricForm] = useState({
    name: '', name_en: '', formula: '', unit: '', agg_type: 'SUM',
    target_table: '', target_column: '', description: '', category: ''
  });
  const [dimensionForm, setDimensionForm] = useState({
    name: '', name_en: '', hierarchy: '', level: 0,
    target_table: '', target_column: '', description: '', category: ''
  });

  // Cypher query state
  const [cypherQuery, setCypherQuery] = useState('MATCH (n) RETURN n LIMIT 25');
  const [cypherResult, setCypherResult] = useState<any>(null);

  // Store
  const {
    selectedNode,
    fetchGraphData, searchNodes, syncGraph,
    getUpstream, getDownstream, getImpactAnalysis,
    setSelectedNode
  } = useGraphStore();

  // ── Effects ────────────────────────────────────────────────────────

  useEffect(() => {
    if (activeTab === 'graph') {
      loadGraphData();
    }
  }, [graphType, activeTab]);

  useEffect(() => {
    if (activeTab === 'metrics') {
      fetchMetrics();
    } else if (activeTab === 'dimensions') {
      fetchDimensions();
    }
  }, [activeTab]);

  // ── Graph Handlers ─────────────────────────────────────────────────

  const loadGraphData = async () => {
    setIsLoading(true);
    try {
      await fetchGraphData({ graphType, limit: 200 });
    } catch (error) {
      toast.error('加载图谱数据失败');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      await loadGraphData();
      return;
    }
    setIsLoading(true);
    try {
      await searchNodes(searchQuery);
    } catch (error) {
      toast.error('搜索失败');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSync = async () => {
    setIsLoading(true);
    try {
      const result = await syncGraph(0);
      if (result.success) {
        toast.success(`同步成功: ${result.tables} 表, ${result.columns} 字段, ${result.terms} 术语`);
        await loadGraphData();
      } else {
        toast.error(result.message || '同步失败');
      }
    } catch (error) {
      toast.error('同步失败');
    } finally {
      setIsLoading(false);
    }
  };

  // ── Metrics Handlers ───────────────────────────────────────────────

  const fetchMetrics = async () => {
    try {
      const response = await axios.get(`${API_BASE}/metrics`, { headers: getAuthHeader() });
      const items = response?.data;
      setMetrics(Array.isArray(items) ? items : []);
    } catch {
      setMetrics([]);
    }
  };

  const handleSaveMetric = async () => {
    try {
      if (editingMetric) {
        await axios.put(`${API_BASE}/metrics/${editingMetric.id}`, metricForm, { headers: getAuthHeader() });
        toast.success('指标更新成功');
      } else {
        await axios.post(`${API_BASE}/metrics`, metricForm, { headers: getAuthHeader() });
        toast.success('指标创建成功');
      }
      setShowMetricDialog(false);
      setEditingMetric(null);
      resetMetricForm();
      fetchMetrics();
    } catch (error) {
      toast.error('保存指标失败');
    }
  };

  const handleDeleteMetric = async (id: number) => {
    if (!confirm('确定删除此指标？')) return;
    try {
      await axios.delete(`${API_BASE}/metrics/${id}`, { headers: getAuthHeader() });
      toast.success('指标删除成功');
      fetchMetrics();
    } catch (error) {
      toast.error('删除指标失败');
    }
  };

  const openEditMetric = (metric: Metric) => {
    setEditingMetric(metric);
    setMetricForm({
      name: metric.name, name_en: metric.name_en, formula: metric.formula,
      unit: metric.unit, agg_type: metric.agg_type, target_table: metric.target_table,
      target_column: metric.target_column, description: metric.description, category: metric.category
    });
    setShowMetricDialog(true);
  };

  const resetMetricForm = () => {
    setMetricForm({ name: '', name_en: '', formula: '', unit: '', agg_type: 'SUM', target_table: '', target_column: '', description: '', category: '' });
  };

  // ── Dimensions Handlers ────────────────────────────────────────────

  const fetchDimensions = async () => {
    try {
      const response = await axios.get(`${API_BASE}/dimensions`, { headers: getAuthHeader() });
      const items = response?.data;
      setDimensions(Array.isArray(items) ? items : []);
    } catch {
      setDimensions([]);
    }
  };

  const handleSaveDimension = async () => {
    try {
      if (editingDimension) {
        await axios.put(`${API_BASE}/dimensions/${editingDimension.id}`, dimensionForm, { headers: getAuthHeader() });
        toast.success('维度更新成功');
      } else {
        await axios.post(`${API_BASE}/dimensions`, dimensionForm, { headers: getAuthHeader() });
        toast.success('维度创建成功');
      }
      setShowDimensionDialog(false);
      setEditingDimension(null);
      resetDimensionForm();
      fetchDimensions();
    } catch (error) {
      toast.error('保存维度失败');
    }
  };

  const handleDeleteDimension = async (id: number) => {
    if (!confirm('确定删除此维度？')) return;
    try {
      await axios.delete(`${API_BASE}/dimensions/${id}`, { headers: getAuthHeader() });
      toast.success('维度删除成功');
      fetchDimensions();
    } catch (error) {
      toast.error('删除维度失败');
    }
  };

  const openEditDimension = (dimension: Dimension) => {
    setEditingDimension(dimension);
    setDimensionForm({
      name: dimension.name, name_en: dimension.name_en, hierarchy: dimension.hierarchy,
      level: dimension.level, target_table: dimension.target_table, target_column: dimension.target_column,
      description: dimension.description, category: dimension.category
    });
    setShowDimensionDialog(true);
  };

  const resetDimensionForm = () => {
    setDimensionForm({ name: '', name_en: '', hierarchy: '', level: 0, target_table: '', target_column: '', description: '', category: '' });
  };

  // ── Cypher Handler ─────────────────────────────────────────────────

  const handleExecuteCypher = async () => {
    setIsLoading(true);
    try {
      const response = await axios.post(`${API_BASE}/cypher`, { query: cypherQuery }, { headers: getAuthHeader() });
      setCypherResult(response.data);
      toast.success('查询执行成功');
    } catch (error) {
      toast.error('Cypher查询失败');
    } finally {
      setIsLoading(false);
    }
  };

  // ── Config ─────────────────────────────────────────────────────────

  const graphTypeConfig = {
    'table-relation': { icon: Database, label: '表关系图', description: '展示数据库表之间的关联关系' },
    'business-knowledge': { icon: Network, label: '业务知识图', description: '展示业务术语、指标、维度的定义关系' },
    'data-lineage': { icon: GitBranch, label: '数据血缘图', description: '展示数据从源头到应用的流转路径' }
  };

  // ── Render ─────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b bg-background">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Network className="h-6 w-6 text-primary" />
            <h1 className="text-xl font-semibold">知识图谱</h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleSync} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${isLoading ? 'animate-spin' : ''}`} />
            同步图谱
          </Button>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-hidden">
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as ActiveTab)} className="h-full flex flex-col">
          <div className="px-4 pt-2 border-b">
            <TabsList>
              <TabsTrigger value="graph" className="flex items-center gap-2">
                <Network className="h-4 w-4" />
                图谱可视化
              </TabsTrigger>
              <TabsTrigger value="metrics" className="flex items-center gap-2">
                <BarChart3 className="h-4 w-4" />
                指标管理
              </TabsTrigger>
              <TabsTrigger value="dimensions" className="flex items-center gap-2">
                <Layers className="h-4 w-4" />
                维度管理
              </TabsTrigger>
              <TabsTrigger value="cypher" className="flex items-center gap-2">
                <Terminal className="h-4 w-4" />
                Cypher查询
              </TabsTrigger>
            </TabsList>
          </div>

          {/* Graph Tab */}
          <TabsContent value="graph" className="flex-1 overflow-hidden m-0">
            <div className="flex h-full">
              {/* Left Sidebar */}
              <div className="w-64 border-r bg-muted/30 flex flex-col p-4 space-y-4">
                {/* Graph Type Selector */}
                <div>
                  <Label className="text-xs font-medium mb-2 block">图谱类型</Label>
                  <div className="space-y-2">
                    {Object.entries(graphTypeConfig).map(([type, config]) => (
                      <Button
                        key={type}
                        variant={graphType === type ? 'default' : 'outline'}
                        className="w-full justify-start"
                        size="sm"
                        onClick={() => setSearchParams({ type, mode: viewMode })}
                      >
                        <config.icon className="h-4 w-4 mr-2" />
                        {config.label}
                      </Button>
                    ))}
                  </div>
                </div>

                {/* Search */}
                <div>
                  <Label className="text-xs font-medium mb-2 block">搜索</Label>
                  <div className="flex gap-2">
                    <Input
                      placeholder="搜索节点..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                      className="h-8 text-xs"
                    />
                    <Button variant="outline" size="sm" onClick={handleSearch} className="h-8 w-8 p-0">
                      <Search className="h-4 w-4" />
                    </Button>
                  </div>
                </div>

                {/* Node Detail */}
                {selectedNode && (
                  <div className="flex-1 overflow-auto">
                    <Label className="text-xs font-medium mb-2 block">节点详情</Label>
                    <Card>
                      <CardHeader className="p-3">
                        <CardTitle className="text-sm flex items-center gap-2">
                          <Badge>{selectedNode.label}</Badge>
                          <span className="truncate">{selectedNode.properties.name || selectedNode.id}</span>
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="p-3 pt-0">
                        <ScrollArea className="h-48">
                          <div className="space-y-2 text-xs">
                            {Object.entries(selectedNode.properties).map(([key, value]) => (
                              <div key={key}>
                                <span className="text-muted-foreground">{key}: </span>
                                <span className="font-mono">{String(value)}</span>
                              </div>
                            ))}
                          </div>
                        </ScrollArea>
                      </CardContent>
                    </Card>

                    {/* Lineage Actions */}
                    {graphType === 'data-lineage' && (
                      <div className="mt-4 space-y-2">
                        <Label className="text-xs font-medium">血缘追踪</Label>
                        <div className="grid grid-cols-2 gap-2">
                          <Button variant="outline" size="sm" className="text-xs" onClick={async () => {
                            const data = await getUpstream(selectedNode.id);
                            toast.info(`找到 ${data.nodes.length} 个上游节点`);
                          }}>
                            <GitBranch className="h-3 w-3 mr-1 rotate-180" />
                            上游追溯
                          </Button>
                          <Button variant="outline" size="sm" className="text-xs" onClick={async () => {
                            const data = await getDownstream(selectedNode.id);
                            toast.info(`找到 ${data.nodes.length} 个下游节点`);
                          }}>
                            <GitBranch className="h-3 w-3 mr-1" />
                            下游追踪
                          </Button>
                        </div>
                        <Button variant="outline" size="sm" className="w-full text-xs" onClick={async () => {
                          const result = await getImpactAnalysis(selectedNode.id);
                          if (result) {
                            toast.info(`影响分析: ${result.impact_summary.total_affected} 个节点受影响`);
                          }
                        }}>
                          <Network className="h-3 w-3 mr-1" />
                          影响分析
                        </Button>
                      </div>
                    )}
                  </div>
                )}

                {/* Legend */}
                <div className="border-t pt-4">
                  <Label className="text-xs font-medium mb-2 block">图例</Label>
                  <div className="space-y-1.5 text-xs">
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-blue-500" /><span>表</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-green-500" /><span>字段</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-purple-500" /><span>术语</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-orange-500" /><span>指标</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-teal-500" /><span>维度</span></div>
                    {graphType === 'data-lineage' && (
                      <>
                        <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-cyan-500" /><span>数据源</span></div>
                        <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-amber-500" /><span>ETL任务</span></div>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {/* Graph Canvas */}
              <div className="flex-1 relative">
                <KnowledgeGraphView
                  graphType={graphType}
                  viewMode={viewMode}
                  isLoading={isLoading}
                  onNodeSelect={setSelectedNode}
                  onRefresh={loadGraphData}
                />
              </div>
            </div>
          </TabsContent>

          {/* Metrics Tab */}
          <TabsContent value="metrics" className="flex-1 overflow-auto m-0 p-4">
            <div className="flex justify-end mb-4">
              <Button onClick={() => { resetMetricForm(); setEditingMetric(null); setShowMetricDialog(true); }}>
                <Plus className="h-4 w-4 mr-2" />
                新增指标
              </Button>
            </div>
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>指标名称</TableHead>
                    <TableHead>英文名</TableHead>
                    <TableHead>公式</TableHead>
                    <TableHead>单位</TableHead>
                    <TableHead>聚合类型</TableHead>
                    <TableHead>分类</TableHead>
                    <TableHead>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {metrics.map((metric) => (
                    <TableRow key={metric.id}>
                      <TableCell className="font-medium">{metric.name}</TableCell>
                      <TableCell className="text-muted-foreground">{metric.name_en}</TableCell>
                      <TableCell className="font-mono text-xs">{metric.formula}</TableCell>
                      <TableCell>{metric.unit}</TableCell>
                      <TableCell><Badge variant="outline">{metric.agg_type}</Badge></TableCell>
                      <TableCell><Badge variant="secondary">{metric.category}</Badge></TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Button variant="ghost" size="sm" onClick={() => openEditMetric(metric)}>
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => handleDeleteMetric(metric.id)}>
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          </TabsContent>

          {/* Dimensions Tab */}
          <TabsContent value="dimensions" className="flex-1 overflow-auto m-0 p-4">
            <div className="flex justify-end mb-4">
              <Button onClick={() => { resetDimensionForm(); setEditingDimension(null); setShowDimensionDialog(true); }}>
                <Plus className="h-4 w-4 mr-2" />
                新增维度
              </Button>
            </div>
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>维度名称</TableHead>
                    <TableHead>英文名</TableHead>
                    <TableHead>层级</TableHead>
                    <TableHead>层级关系</TableHead>
                    <TableHead>目标表</TableHead>
                    <TableHead>分类</TableHead>
                    <TableHead>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {dimensions.map((dimension) => (
                    <TableRow key={dimension.id}>
                      <TableCell className="font-medium">{dimension.name}</TableCell>
                      <TableCell className="text-muted-foreground">{dimension.name_en}</TableCell>
                      <TableCell>{dimension.level}</TableCell>
                      <TableCell className="text-xs">{dimension.hierarchy}</TableCell>
                      <TableCell className="font-mono text-xs">{dimension.target_table}</TableCell>
                      <TableCell><Badge variant="secondary">{dimension.category}</Badge></TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Button variant="ghost" size="sm" onClick={() => openEditDimension(dimension)}>
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => handleDeleteDimension(dimension.id)}>
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          </TabsContent>

          {/* Cypher Query Tab */}
          <TabsContent value="cypher" className="flex-1 overflow-auto m-0 p-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Terminal className="h-5 w-5" />
                  Cypher 查询控制台
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label>查询语句</Label>
                  <Textarea
                    value={cypherQuery}
                    onChange={(e) => setCypherQuery(e.target.value)}
                    placeholder="输入Cypher查询语句..."
                    className="font-mono text-sm h-32"
                  />
                </div>
                <Button onClick={handleExecuteCypher} disabled={isLoading}>
                  <Terminal className="h-4 w-4 mr-2" />
                  执行查询
                </Button>
                {cypherResult && (
                  <div className="space-y-2">
                    <Label>查询结果</Label>
                    <pre className="bg-muted p-4 rounded-lg text-xs overflow-auto max-h-96">
                      {JSON.stringify(cypherResult, null, 2)}
                    </pre>
                  </div>
                )}
                <div className="text-xs text-muted-foreground">
                  💡 常用查询示例：
                  <ul className="mt-1 space-y-1 list-disc list-inside">
                    <li><code className="px-1 bg-muted rounded">MATCH (n) RETURN n LIMIT 25</code> - 查看所有节点</li>
                    <li><code className="px-1 bg-muted rounded">{"MATCH (t:Table)-[:HAS_COLUMN]->(c:Column) RETURN t, c LIMIT 25"}</code> - 查看表和字段</li>
                    <li><code className="px-1 bg-muted rounded">MATCH (m:Metric) RETURN m</code> - 查看所有指标</li>
                  </ul>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      {/* Metric Dialog */}
      <Dialog open={showMetricDialog} onOpenChange={setShowMetricDialog}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingMetric ? '编辑指标' : '新增指标'}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-4 py-4">
            <div className="space-y-2">
              <Label>指标名称 *</Label>
              <Input value={metricForm.name} onChange={(e) => setMetricForm({...metricForm, name: e.target.value})} placeholder="如: GMV" />
            </div>
            <div className="space-y-2">
              <Label>英文名称</Label>
              <Input value={metricForm.name_en} onChange={(e) => setMetricForm({...metricForm, name_en: e.target.value})} placeholder="如: Gross Merchandise Volume" />
            </div>
            <div className="col-span-2 space-y-2">
              <Label>计算公式</Label>
              <Input value={metricForm.formula} onChange={(e) => setMetricForm({...metricForm, formula: e.target.value})} placeholder="如: SUM(order_amount)" />
            </div>
            <div className="space-y-2">
              <Label>单位</Label>
              <Input value={metricForm.unit} onChange={(e) => setMetricForm({...metricForm, unit: e.target.value})} placeholder="如: 元" />
            </div>
            <div className="space-y-2">
              <Label>聚合类型</Label>
              <Select value={metricForm.agg_type} onValueChange={(v) => setMetricForm({...metricForm, agg_type: v})}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="SUM">SUM</SelectItem>
                  <SelectItem value="AVG">AVG</SelectItem>
                  <SelectItem value="COUNT">COUNT</SelectItem>
                  <SelectItem value="MAX">MAX</SelectItem>
                  <SelectItem value="MIN">MIN</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>目标表</Label>
              <Input value={metricForm.target_table} onChange={(e) => setMetricForm({...metricForm, target_table: e.target.value})} placeholder="如: orders" />
            </div>
            <div className="space-y-2">
              <Label>目标字段</Label>
              <Input value={metricForm.target_column} onChange={(e) => setMetricForm({...metricForm, target_column: e.target.value})} placeholder="如: amount" />
            </div>
            <div className="space-y-2">
              <Label>分类</Label>
              <Input value={metricForm.category} onChange={(e) => setMetricForm({...metricForm, category: e.target.value})} placeholder="如: 交易" />
            </div>
            <div className="col-span-2 space-y-2">
              <Label>描述</Label>
              <Textarea value={metricForm.description} onChange={(e) => setMetricForm({...metricForm, description: e.target.value})} placeholder="指标说明..." />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowMetricDialog(false)}>取消</Button>
            <Button onClick={handleSaveMetric}>
              <Save className="h-4 w-4 mr-2" />
              {editingMetric ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dimension Dialog */}
      <Dialog open={showDimensionDialog} onOpenChange={setShowDimensionDialog}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingDimension ? '编辑维度' : '新增维度'}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-4 py-4">
            <div className="space-y-2">
              <Label>维度名称 *</Label>
              <Input value={dimensionForm.name} onChange={(e) => setDimensionForm({...dimensionForm, name: e.target.value})} placeholder="如: 时间" />
            </div>
            <div className="space-y-2">
              <Label>英文名称</Label>
              <Input value={dimensionForm.name_en} onChange={(e) => setDimensionForm({...dimensionForm, name_en: e.target.value})} placeholder="如: Time" />
            </div>
            <div className="space-y-2">
              <Label>层级</Label>
              <Input type="number" value={dimensionForm.level} onChange={(e) => setDimensionForm({...dimensionForm, level: parseInt(e.target.value) || 0})} />
            </div>
            <div className="space-y-2">
              <Label>目标表</Label>
              <Input value={dimensionForm.target_table} onChange={(e) => setDimensionForm({...dimensionForm, target_table: e.target.value})} placeholder="如: users" />
            </div>
            <div className="col-span-2 space-y-2">
              <Label>层级关系 (JSON)</Label>
              <Input value={dimensionForm.hierarchy} onChange={(e) => setDimensionForm({...dimensionForm, hierarchy: e.target.value})} placeholder='如: ["国家","省","市"]' />
            </div>
            <div className="space-y-2">
              <Label>目标字段</Label>
              <Input value={dimensionForm.target_column} onChange={(e) => setDimensionForm({...dimensionForm, target_column: e.target.value})} placeholder="如: region" />
            </div>
            <div className="space-y-2">
              <Label>分类</Label>
              <Input value={dimensionForm.category} onChange={(e) => setDimensionForm({...dimensionForm, category: e.target.value})} placeholder="如: 地理" />
            </div>
            <div className="col-span-2 space-y-2">
              <Label>描述</Label>
              <Textarea value={dimensionForm.description} onChange={(e) => setDimensionForm({...dimensionForm, description: e.target.value})} placeholder="维度说明..." />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDimensionDialog(false)}>取消</Button>
            <Button onClick={handleSaveDimension}>
              <Save className="h-4 w-4 mr-2" />
              {editingDimension ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
