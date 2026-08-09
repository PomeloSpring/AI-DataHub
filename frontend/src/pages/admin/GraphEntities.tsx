import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import {
  Plus, Edit, Trash2, RefreshCw, Search, BarChart3, Layers,
  Network, Save, X
} from 'lucide-react';
import axios from 'axios';
import { toast } from 'sonner';

// ── Types ──────────────────────────────────────────────────────────────

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
  datasource_id: number;
  created_at: string;
  updated_at: string;
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
  datasource_id: number;
  created_at: string;
  updated_at: string;
}

// ── API Helper ─────────────────────────────────────────────────────────

const API_BASE = '/api/graph';

function getAuthHeader() {
  const token = localStorage.getItem('token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Main Component ─────────────────────────────────────────────────────

export default function GraphEntities() {
  const [activeTab, setActiveTab] = useState('metrics');
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [dimensions, setDimensions] = useState<Dimension[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  // Dialog state
  const [showMetricDialog, setShowMetricDialog] = useState(false);
  const [showDimensionDialog, setShowDimensionDialog] = useState(false);
  const [editingMetric, setEditingMetric] = useState<Metric | null>(null);
  const [editingDimension, setEditingDimension] = useState<Dimension | null>(null);

  // Form state
  const [metricForm, setMetricForm] = useState({
    name: '', name_en: '', formula: '', unit: '', agg_type: 'SUM',
    target_table: '', target_column: '', description: '', category: ''
  });
  const [dimensionForm, setDimensionForm] = useState({
    name: '', name_en: '', hierarchy: '', level: 0,
    target_table: '', target_column: '', description: '', category: ''
  });

  // ── Fetch Data ─────────────────────────────────────────────────────

  const fetchMetrics = useCallback(async () => {
    try {
      const response = await axios.get(`${API_BASE}/metrics`, { headers: getAuthHeader() });
      setMetrics(response.data);
    } catch (error) {
      console.error('Failed to fetch metrics:', error);
      toast.error('获取指标列表失败');
    }
  }, []);

  const fetchDimensions = useCallback(async () => {
    try {
      const response = await axios.get(`${API_BASE}/dimensions`, { headers: getAuthHeader() });
      setDimensions(response.data);
    } catch (error) {
      console.error('Failed to fetch dimensions:', error);
      toast.error('获取维度列表失败');
    }
  }, []);

  useEffect(() => {
    fetchMetrics();
    fetchDimensions();
  }, [fetchMetrics, fetchDimensions]);

  // ── Metric CRUD ────────────────────────────────────────────────────

  const handleCreateMetric = async () => {
    try {
      await axios.post(`${API_BASE}/metrics`, metricForm, { headers: getAuthHeader() });
      toast.success('指标创建成功');
      setShowMetricDialog(false);
      resetMetricForm();
      fetchMetrics();
    } catch (error) {
      toast.error('创建指标失败');
    }
  };

  const handleUpdateMetric = async () => {
    if (!editingMetric) return;
    try {
      await axios.put(`${API_BASE}/metrics/${editingMetric.id}`, metricForm, { headers: getAuthHeader() });
      toast.success('指标更新成功');
      setShowMetricDialog(false);
      setEditingMetric(null);
      resetMetricForm();
      fetchMetrics();
    } catch (error) {
      toast.error('更新指标失败');
    }
  };

  const handleDeleteMetric = async (id: number) => {
    if (!confirm('确定要删除此指标吗？')) return;
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
      name: metric.name,
      name_en: metric.name_en,
      formula: metric.formula,
      unit: metric.unit,
      agg_type: metric.agg_type,
      target_table: metric.target_table,
      target_column: metric.target_column,
      description: metric.description,
      category: metric.category
    });
    setShowMetricDialog(true);
  };

  const resetMetricForm = () => {
    setMetricForm({
      name: '', name_en: '', formula: '', unit: '', agg_type: 'SUM',
      target_table: '', target_column: '', description: '', category: ''
    });
  };

  // ── Dimension CRUD ─────────────────────────────────────────────────

  const handleCreateDimension = async () => {
    try {
      await axios.post(`${API_BASE}/dimensions`, dimensionForm, { headers: getAuthHeader() });
      toast.success('维度创建成功');
      setShowDimensionDialog(false);
      resetDimensionForm();
      fetchDimensions();
    } catch (error) {
      toast.error('创建维度失败');
    }
  };

  const handleUpdateDimension = async () => {
    if (!editingDimension) return;
    try {
      await axios.put(`${API_BASE}/dimensions/${editingDimension.id}`, dimensionForm, { headers: getAuthHeader() });
      toast.success('维度更新成功');
      setShowDimensionDialog(false);
      setEditingDimension(null);
      resetDimensionForm();
      fetchDimensions();
    } catch (error) {
      toast.error('更新维度失败');
    }
  };

  const handleDeleteDimension = async (id: number) => {
    if (!confirm('确定要删除此维度吗？')) return;
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
      name: dimension.name,
      name_en: dimension.name_en,
      hierarchy: dimension.hierarchy,
      level: dimension.level,
      target_table: dimension.target_table,
      target_column: dimension.target_column,
      description: dimension.description,
      category: dimension.category
    });
    setShowDimensionDialog(true);
  };

  const resetDimensionForm = () => {
    setDimensionForm({
      name: '', name_en: '', hierarchy: '', level: 0,
      target_table: '', target_column: '', description: '', category: ''
    });
  };

  // ── Sync to Neo4j ──────────────────────────────────────────────────

  const handleSync = async () => {
    setIsLoading(true);
    try {
      const response = await axios.post(`${API_BASE}/sync`, {}, { headers: getAuthHeader() });
      if (response.data.success) {
        toast.success(`同步成功: ${response.data.metrics || 0} 指标, ${response.data.dimensions || 0} 维度`);
      } else {
        toast.error(response.data.message || '同步失败');
      }
    } catch (error) {
      toast.error('同步失败');
    } finally {
      setIsLoading(false);
    }
  };

  // ── Filter ─────────────────────────────────────────────────────────

  const filteredMetrics = metrics.filter(m =>
    m.name.includes(searchQuery) ||
    m.name_en?.includes(searchQuery) ||
    m.category?.includes(searchQuery)
  );

  const filteredDimensions = dimensions.filter(d =>
    d.name.includes(searchQuery) ||
    d.name_en?.includes(searchQuery) ||
    d.category?.includes(searchQuery)
  );

  // ── Render ─────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Network className="h-6 w-6 text-primary" />
            图谱实体管理
          </h1>
          <p className="text-muted-foreground mt-1">
            管理业务指标和分析维度，同步到Neo4j知识图谱
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleSync} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
            同步到图谱
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">指标数量</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{metrics.length}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">维度数量</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{dimensions.length}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">状态</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge variant="outline" className="text-green-500">正常</Badge>
          </CardContent>
        </Card>
      </div>

      {/* Search */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索指标或维度..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="metrics" className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4" />
            指标管理
          </TabsTrigger>
          <TabsTrigger value="dimensions" className="flex items-center gap-2">
            <Layers className="h-4 w-4" />
            维度管理
          </TabsTrigger>
        </TabsList>

        {/* Metrics Tab */}
        <TabsContent value="metrics" className="space-y-4">
          <div className="flex justify-end">
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
                {filteredMetrics.map((metric) => (
                  <TableRow key={metric.id}>
                    <TableCell className="font-medium">{metric.name}</TableCell>
                    <TableCell className="text-muted-foreground">{metric.name_en}</TableCell>
                    <TableCell className="font-mono text-xs">{metric.formula}</TableCell>
                    <TableCell>{metric.unit}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{metric.agg_type}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{metric.category}</Badge>
                    </TableCell>
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
        <TabsContent value="dimensions" className="space-y-4">
          <div className="flex justify-end">
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
                {filteredDimensions.map((dimension) => (
                  <TableRow key={dimension.id}>
                    <TableCell className="font-medium">{dimension.name}</TableCell>
                    <TableCell className="text-muted-foreground">{dimension.name_en}</TableCell>
                    <TableCell>{dimension.level}</TableCell>
                    <TableCell className="text-xs">{dimension.hierarchy}</TableCell>
                    <TableCell className="font-mono text-xs">{dimension.target_table}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{dimension.category}</Badge>
                    </TableCell>
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
      </Tabs>

      {/* Metric Dialog */}
      <Dialog open={showMetricDialog} onOpenChange={setShowMetricDialog}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingMetric ? '编辑指标' : '新增指标'}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-4 py-4">
            <div className="space-y-2">
              <Label>指标名称 *</Label>
              <Input
                value={metricForm.name}
                onChange={(e) => setMetricForm({...metricForm, name: e.target.value})}
                placeholder="如: GMV"
              />
            </div>
            <div className="space-y-2">
              <Label>英文名称</Label>
              <Input
                value={metricForm.name_en}
                onChange={(e) => setMetricForm({...metricForm, name_en: e.target.value})}
                placeholder="如: Gross Merchandise Volume"
              />
            </div>
            <div className="col-span-2 space-y-2">
              <Label>计算公式</Label>
              <Input
                value={metricForm.formula}
                onChange={(e) => setMetricForm({...metricForm, formula: e.target.value})}
                placeholder="如: SUM(order_amount)"
              />
            </div>
            <div className="space-y-2">
              <Label>单位</Label>
              <Input
                value={metricForm.unit}
                onChange={(e) => setMetricForm({...metricForm, unit: e.target.value})}
                placeholder="如: 元、人、次"
              />
            </div>
            <div className="space-y-2">
              <Label>聚合类型</Label>
              <Select value={metricForm.agg_type} onValueChange={(v) => setMetricForm({...metricForm, agg_type: v})}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
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
              <Input
                value={metricForm.target_table}
                onChange={(e) => setMetricForm({...metricForm, target_table: e.target.value})}
                placeholder="如: orders"
              />
            </div>
            <div className="space-y-2">
              <Label>目标字段</Label>
              <Input
                value={metricForm.target_column}
                onChange={(e) => setMetricForm({...metricForm, target_column: e.target.value})}
                placeholder="如: amount"
              />
            </div>
            <div className="space-y-2">
              <Label>分类</Label>
              <Input
                value={metricForm.category}
                onChange={(e) => setMetricForm({...metricForm, category: e.target.value})}
                placeholder="如: 交易、用户、流量"
              />
            </div>
            <div className="col-span-2 space-y-2">
              <Label>描述</Label>
              <Textarea
                value={metricForm.description}
                onChange={(e) => setMetricForm({...metricForm, description: e.target.value})}
                placeholder="指标说明..."
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowMetricDialog(false)}>取消</Button>
            <Button onClick={editingMetric ? handleUpdateMetric : handleCreateMetric}>
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
              <Input
                value={dimensionForm.name}
                onChange={(e) => setDimensionForm({...dimensionForm, name: e.target.value})}
                placeholder="如: 时间、地区"
              />
            </div>
            <div className="space-y-2">
              <Label>英文名称</Label>
              <Input
                value={dimensionForm.name_en}
                onChange={(e) => setDimensionForm({...dimensionForm, name_en: e.target.value})}
                placeholder="如: Time, Region"
              />
            </div>
            <div className="space-y-2">
              <Label>层级</Label>
              <Input
                type="number"
                value={dimensionForm.level}
                onChange={(e) => setDimensionForm({...dimensionForm, level: parseInt(e.target.value) || 0})}
                placeholder="0"
              />
            </div>
            <div className="space-y-2">
              <Label>目标表</Label>
              <Input
                value={dimensionForm.target_table}
                onChange={(e) => setDimensionForm({...dimensionForm, target_table: e.target.value})}
                placeholder="如: users"
              />
            </div>
            <div className="col-span-2 space-y-2">
              <Label>层级关系 (JSON)</Label>
              <Input
                value={dimensionForm.hierarchy}
                onChange={(e) => setDimensionForm({...dimensionForm, hierarchy: e.target.value})}
                placeholder='如: ["国家","省","市"]'
              />
            </div>
            <div className="space-y-2">
              <Label>目标字段</Label>
              <Input
                value={dimensionForm.target_column}
                onChange={(e) => setDimensionForm({...dimensionForm, target_column: e.target.value})}
                placeholder="如: region"
              />
            </div>
            <div className="space-y-2">
              <Label>分类</Label>
              <Input
                value={dimensionForm.category}
                onChange={(e) => setDimensionForm({...dimensionForm, category: e.target.value})}
                placeholder="如: 基础、地理、渠道"
              />
            </div>
            <div className="col-span-2 space-y-2">
              <Label>描述</Label>
              <Textarea
                value={dimensionForm.description}
                onChange={(e) => setDimensionForm({...dimensionForm, description: e.target.value})}
                placeholder="维度说明..."
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDimensionDialog(false)}>取消</Button>
            <Button onClick={editingDimension ? handleUpdateDimension : handleCreateDimension}>
              <Save className="h-4 w-4 mr-2" />
              {editingDimension ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
