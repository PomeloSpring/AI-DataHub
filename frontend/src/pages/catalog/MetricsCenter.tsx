import { useState, useEffect, useCallback } from 'react';
import { metricsApi } from '@/api/metrics';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  Plus,
  Search,
  Edit,
  Trash2,
  RefreshCw,
  BarChart3,
  Layers,
} from 'lucide-react';

interface Metric {
  id: number;
  name: string;
  display_name: string;
  description?: string;
  metric_type: string;
  calculation_type: string;
  expression?: string;
  unit?: string;
  target_table?: string;
  target_column?: string;
  granularity?: string;
  owner?: string;
  tags?: string[];
  created_at: string;
  updated_at: string;
}

interface Dimension {
  id: number;
  metric_id: number;
  name: string;
  display_name: string;
  column_name: string;
  data_type: string;
  description?: string;
}

interface MetricFormData {
  name: string;
  display_name: string;
  description: string;
  metric_type: string;
  calculation_type: string;
  expression: string;
  unit: string;
  target_table: string;
  target_column: string;
  granularity: string;
}

interface DimensionFormData {
  name: string;
  display_name: string;
  column_name: string;
  data_type: string;
  description: string;
}

const METRIC_TYPES = [
  { value: 'basic', label: '基础指标' },
  { value: 'derived', label: '衍生指标' },
  { value: 'composite', label: '复合指标' },
];

const CALC_TYPES = [
  { value: 'sum', label: '求和' },
  { value: 'count', label: '计数' },
  { value: 'avg', label: '平均' },
  { value: 'max', label: '最大值' },
  { value: 'min', label: '最小值' },
  { value: 'custom', label: '自定义' },
];

const METRIC_TYPE_COLORS: Record<string, string> = {
  basic: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  derived: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  composite: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
};

const emptyMetricForm: MetricFormData = {
  name: '',
  display_name: '',
  description: '',
  metric_type: 'basic',
  calculation_type: 'sum',
  expression: '',
  unit: '',
  target_table: '',
  target_column: '',
  granularity: '',
};

const emptyDimensionForm: DimensionFormData = {
  name: '',
  display_name: '',
  column_name: '',
  data_type: 'string',
  description: '',
};

export default function MetricsCenter() {
  const { currentWorkspaceId } = useWorkspaceStore();
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState<string>('all');

  // Dialog states
  const [formOpen, setFormOpen] = useState(false);
  const [editMetric, setEditMetric] = useState<Metric | null>(null);
  const [formData, setFormData] = useState<MetricFormData>(emptyMetricForm);
  const [saving, setSaving] = useState(false);

  // Detail view
  const [detailMetric, setDetailMetric] = useState<Metric | null>(null);
  const [dimensions, setDimensions] = useState<Dimension[]>([]);
  const [dimensionFormOpen, setDimensionFormOpen] = useState(false);
  const [dimensionForm, setDimensionForm] = useState<DimensionFormData>(emptyDimensionForm);

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Metric | null>(null);

  const loadMetrics = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = { page, size: 20, workspace_id: currentWorkspaceId };
      if (search) params.search = search;
      if (filterType !== 'all') params.metric_type = filterType;
      const res = await metricsApi.list(params);
      const items = res?.data?.items ?? res?.data;
      setMetrics(Array.isArray(items) ? items : []);
      setTotal(res?.data?.total || 0);
    } catch {
      // API not available or empty — show empty state, no error toast
      setMetrics([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, search, filterType, currentWorkspaceId]);

  useEffect(() => {
    loadMetrics();
  }, [loadMetrics]);

  const loadDimensions = async (metricId: number) => {
    try {
      const res = await metricsApi.getDimensions(metricId);
      const items = res?.data;
      setDimensions(Array.isArray(items) ? items : []);
    } catch {
      setDimensions([]);
    }
  };

  const handleViewDetail = async (metric: Metric) => {
    setDetailMetric(metric);
    await loadDimensions(metric.id);
  };

  const handleOpenCreate = () => {
    setEditMetric(null);
    setFormData(emptyMetricForm);
    setFormOpen(true);
  };

  const handleOpenEdit = (metric: Metric) => {
    setEditMetric(metric);
    setFormData({
      name: metric.name,
      display_name: metric.display_name,
      description: metric.description || '',
      metric_type: metric.metric_type,
      calculation_type: metric.calculation_type,
      expression: metric.expression || '',
      unit: metric.unit || '',
      target_table: metric.target_table || '',
      target_column: metric.target_column || '',
      granularity: metric.granularity || '',
    });
    setFormOpen(true);
  };

  const handleSaveMetric = async () => {
    if (!formData.name || !formData.display_name) {
      toast.error('请填写名称和显示名称');
      return;
    }
    setSaving(true);
    try {
      if (editMetric) {
        await metricsApi.update(editMetric.id, { ...formData, workspace_id: currentWorkspaceId });
        toast.success('指标已更新');
      } else {
        await metricsApi.create({ ...formData, workspace_id: currentWorkspaceId });
        toast.success('指标已创建');
      }
      setFormOpen(false);
      loadMetrics();
    } catch {
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await metricsApi.delete(deleteTarget.id);
      toast.success('指标已删除');
      setDeleteTarget(null);
      loadMetrics();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleAddDimension = async () => {
    if (!detailMetric || !dimensionForm.name || !dimensionForm.column_name) {
      toast.error('请填写维度名称和列名');
      return;
    }
    try {
      await metricsApi.addDimension(detailMetric.id, dimensionForm);
      toast.success('维度已添加');
      setDimensionFormOpen(false);
      setDimensionForm(emptyDimensionForm);
      await loadDimensions(detailMetric.id);
    } catch {
      toast.error('添加维度失败');
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">指标中心</h1>
          <p className="text-muted-foreground text-sm mt-1">管理业务指标定义、计算口径和维度</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadMetrics}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button size="sm" onClick={handleOpenCreate}>
            <Plus className="w-4 h-4 mr-1" />
            新建指标
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="搜索指标名称..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="pl-9"
          />
        </div>
        <Select value={filterType} onValueChange={(v) => { setFilterType(v); setPage(1); }}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="指标类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            {METRIC_TYPES.map((t) => (
              <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Metrics Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">指标名称</th>
              <th className="text-left p-3 font-medium">显示名称</th>
              <th className="text-left p-3 font-medium">类型</th>
              <th className="text-left p-3 font-medium">计算方式</th>
              <th className="text-left p-3 font-medium">单位</th>
              <th className="text-left p-3 font-medium">目标表</th>
              <th className="text-left p-3 font-medium">负责人</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-muted-foreground">
                  加载中...
                </td>
              </tr>
            ) : metrics.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-muted-foreground">
                  暂无指标数据
                </td>
              </tr>
            ) : (
              metrics.map((m) => (
                <tr key={m.id} className="border-t hover:bg-muted/30 cursor-pointer" onClick={() => handleViewDetail(m)}>
                  <td className="p-3">
                    <div className="flex items-center gap-2">
                      <BarChart3 className="w-4 h-4 text-muted-foreground" />
                      <span className="font-medium">{m.name}</span>
                    </div>
                  </td>
                  <td className="p-3 text-muted-foreground">{m.display_name}</td>
                  <td className="p-3">
                    <Badge variant="outline" className={METRIC_TYPE_COLORS[m.metric_type] || ''}>
                      {METRIC_TYPES.find((t) => t.value === m.metric_type)?.label || m.metric_type}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <Badge variant="outline">
                      {CALC_TYPES.find((t) => t.value === m.calculation_type)?.label || m.calculation_type}
                    </Badge>
                  </td>
                  <td className="p-3 text-muted-foreground">{m.unit || '-'}</td>
                  <td className="p-3 text-muted-foreground font-mono text-xs">{m.target_table || '-'}</td>
                  <td className="p-3 text-muted-foreground">{m.owner || '-'}</td>
                  <td className="p-3 text-right">
                    <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => handleOpenEdit(m)}>
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => setDeleteTarget(m)}>
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > 20 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>共 {total} 条</span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              上一页
            </Button>
            <Button variant="outline" size="sm" disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>
              下一页
            </Button>
          </div>
        </div>
      )}

      {/* Create/Edit Metric Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editMetric ? '编辑指标' : '新建指标'}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">指标名称 *</label>
              <Input
                placeholder="如: gmv"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">显示名称 *</label>
              <Input
                placeholder="如: 交易总额"
                value={formData.display_name}
                onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
              />
            </div>
            <div className="col-span-2 space-y-2">
              <label className="text-sm font-medium">描述</label>
              <Input
                placeholder="指标的业务含义说明"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">指标类型</label>
              <Select value={formData.metric_type} onValueChange={(v) => setFormData({ ...formData, metric_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {METRIC_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">计算方式</label>
              <Select value={formData.calculation_type} onValueChange={(v) => setFormData({ ...formData, calculation_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CALC_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="col-span-2 space-y-2">
              <label className="text-sm font-medium">计算表达式</label>
              <Input
                placeholder="如: SUM(amount) 或 COUNT(DISTINCT user_id)"
                value={formData.expression}
                onChange={(e) => setFormData({ ...formData, expression: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">单位</label>
              <Input
                placeholder="如: 元, 次, %"
                value={formData.unit}
                onChange={(e) => setFormData({ ...formData, unit: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">粒度</label>
              <Input
                placeholder="如: daily, hourly"
                value={formData.granularity}
                onChange={(e) => setFormData({ ...formData, granularity: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">目标表</label>
              <Input
                placeholder="数据来源表名"
                value={formData.target_table}
                onChange={(e) => setFormData({ ...formData, target_table: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">目标列</label>
              <Input
                placeholder="数据来源列名"
                value={formData.target_column}
                onChange={(e) => setFormData({ ...formData, target_column: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSaveMetric} disabled={saving}>
              {saving ? '保存中...' : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Metric Detail Dialog */}
      <Dialog open={!!detailMetric} onOpenChange={(open) => { if (!open) setDetailMetric(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BarChart3 className="w-5 h-5" />
              {detailMetric?.display_name}
              <Badge variant="outline" className={METRIC_TYPE_COLORS[detailMetric?.metric_type || ''] || ''}>
                {METRIC_TYPES.find((t) => t.value === detailMetric?.metric_type)?.label || detailMetric?.metric_type}
              </Badge>
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div><span className="text-muted-foreground">名称：</span>{detailMetric?.name}</div>
              <div><span className="text-muted-foreground">计算方式：</span>{CALC_TYPES.find((t) => t.value === detailMetric?.calculation_type)?.label}</div>
              <div><span className="text-muted-foreground">单位：</span>{detailMetric?.unit || '-'}</div>
              <div><span className="text-muted-foreground">粒度：</span>{detailMetric?.granularity || '-'}</div>
              <div><span className="text-muted-foreground">目标表：</span><span className="font-mono text-xs">{detailMetric?.target_table || '-'}</span></div>
              <div><span className="text-muted-foreground">目标列：</span><span className="font-mono text-xs">{detailMetric?.target_column || '-'}</span></div>
              {detailMetric?.expression && (
                <div className="col-span-2">
                  <span className="text-muted-foreground">表达式：</span>
                  <code className="ml-1 px-2 py-0.5 bg-muted rounded text-xs">{detailMetric.expression}</code>
                </div>
              )}
              {detailMetric?.description && (
                <div className="col-span-2">
                  <span className="text-muted-foreground">描述：</span>{detailMetric.description}
                </div>
              )}
            </div>

            {/* Dimensions */}
            <div className="border-t pt-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium flex items-center gap-2">
                  <Layers className="w-4 h-4" />
                  维度列表
                </h3>
                <Button variant="outline" size="sm" onClick={() => { setDimensionForm(emptyDimensionForm); setDimensionFormOpen(true); }}>
                  <Plus className="w-4 h-4 mr-1" />
                  添加维度
                </Button>
              </div>
              {dimensions.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">暂无维度</p>
              ) : (
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="text-left p-2 font-medium">维度名</th>
                        <th className="text-left p-2 font-medium">显示名</th>
                        <th className="text-left p-2 font-medium">列名</th>
                        <th className="text-left p-2 font-medium">类型</th>
                        <th className="text-left p-2 font-medium">描述</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dimensions.map((d) => (
                        <tr key={d.id} className="border-t">
                          <td className="p-2 font-medium">{d.name}</td>
                          <td className="p-2 text-muted-foreground">{d.display_name}</td>
                          <td className="p-2 font-mono text-xs">{d.column_name}</td>
                          <td className="p-2 text-muted-foreground">{d.data_type}</td>
                          <td className="p-2 text-muted-foreground">{d.description || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Add Dimension Dialog */}
      <Dialog open={dimensionFormOpen} onOpenChange={setDimensionFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>添加维度</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">维度名称 *</label>
              <Input
                placeholder="如: region"
                value={dimensionForm.name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">显示名称</label>
              <Input
                placeholder="如: 地区"
                value={dimensionForm.display_name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, display_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">列名 *</label>
              <Input
                placeholder="数据库中的列名"
                value={dimensionForm.column_name}
                onChange={(e) => setDimensionForm({ ...dimensionForm, column_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">数据类型</label>
              <Select value={dimensionForm.data_type} onValueChange={(v) => setDimensionForm({ ...dimensionForm, data_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="string">字符串</SelectItem>
                  <SelectItem value="int">整数</SelectItem>
                  <SelectItem value="float">浮点数</SelectItem>
                  <SelectItem value="date">日期</SelectItem>
                  <SelectItem value="datetime">日期时间</SelectItem>
                  <SelectItem value="boolean">布尔值</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">描述</label>
              <Input
                placeholder="维度说明"
                value={dimensionForm.description}
                onChange={(e) => setDimensionForm({ ...dimensionForm, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDimensionFormOpen(false)}>取消</Button>
            <Button onClick={handleAddDimension}>添加</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要删除指标 <strong>{deleteTarget?.display_name}</strong> 吗？此操作不可恢复。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
