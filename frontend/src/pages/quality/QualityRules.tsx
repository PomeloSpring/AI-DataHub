import { useState, useEffect, useCallback } from 'react';
import { qualityApi } from '@/api/quality';
import client from '@/api/client';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
  Play,
  Edit,
  Trash2,
  RefreshCw,
  Search,
  Filter,
} from 'lucide-react';

interface QualityRule {
  id: number;
  name: string;
  description: string;
  rule_type: string;
  target_table: string;
  target_column: string;
  target_datasource_id: number | null;
  severity: string;
  is_active: boolean;
  rule_config: Record<string, any>;
  last_check_at: string | null;
  last_check_status: string | null;
  workspace_id: number;
  created_at: string;
  updated_at: string;
}

interface Datasource {
  id: number;
  name: string;
}

const RULE_TYPE_OPTIONS = [
  { value: 'not_null', label: '非空检查' },
  { value: 'unique', label: '唯一性检查' },
  { value: 'range', label: '范围检查' },
  { value: 'format', label: '格式检查' },
  { value: 'referential', label: '引用完整性' },
  { value: 'custom_sql', label: '自定义 SQL' },
  { value: 'freshness', label: '数据新鲜度' },
  { value: 'row_count', label: '行数检查' },
  { value: 'distribution', label: '分布检查' },
];

const SEVERITY_OPTIONS = [
  { value: 'critical', label: '严重' },
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' },
];

const SEVERITY_MAP: Record<string, { label: string; color: string }> = {
  critical: { label: '严重', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  high: { label: '高', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  medium: { label: '中', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  low: { label: '低', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
};

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  passed: { label: '通过', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  failed: { label: '失败', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  error: { label: '错误', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
};

const RULE_TYPE_MAP: Record<string, string> = Object.fromEntries(
  RULE_TYPE_OPTIONS.map(o => [o.value, o.label])
);

const EMPTY_FORM = {
  name: '',
  description: '',
  rule_type: 'not_null',
  target_datasource_id: '',
  target_table: '',
  target_column: '',
  severity: 'medium',
  rule_config: '{}',
};

export default function QualityRules() {
  const { currentWorkspaceId } = useWorkspaceStore();
  const [rules, setRules] = useState<QualityRule[]>([]);
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editRule, setEditRule] = useState<QualityRule | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<QualityRule | null>(null);
  const [saving, setSaving] = useState(false);

  // Form state
  const [form, setForm] = useState(EMPTY_FORM);
  const [configError, setConfigError] = useState('');

  // Filters
  const [filterType, setFilterType] = useState<string>('all');
  const [filterSeverity, setFilterSeverity] = useState<string>('all');
  const [filterTable, setFilterTable] = useState('');
  const [searchText, setSearchText] = useState('');

  const loadRules = useCallback(async () => {
    if (!currentWorkspaceId) return;
    setLoading(true);
    try {
      const { data } = await qualityApi.getRules(currentWorkspaceId);
      setRules(data || []);
    } catch {
      toast.error('加载质量规则失败');
    } finally {
      setLoading(false);
    }
  }, [currentWorkspaceId]);

  useEffect(() => { loadRules(); }, [loadRules]);

  useEffect(() => {
    client.get('/datasources')
      .then(({ data }) => setDatasources(Array.isArray(data) ? data : []))
      .catch(() => setDatasources([]));
  }, []);

  const resetForm = () => {
    setForm(EMPTY_FORM);
    setConfigError('');
    setEditRule(null);
  };

  const openCreate = () => {
    resetForm();
    setFormOpen(true);
  };

  const openEdit = (rule: QualityRule) => {
    setEditRule(rule);
    setForm({
      name: rule.name,
      description: rule.description || '',
      rule_type: rule.rule_type,
      target_datasource_id: rule.target_datasource_id ? String(rule.target_datasource_id) : '',
      target_table: rule.target_table || '',
      target_column: rule.target_column || '',
      severity: rule.severity,
      rule_config: JSON.stringify(rule.rule_config || {}, null, 2),
    });
    setConfigError('');
    setFormOpen(true);
  };

  const validateForm = (): boolean => {
    if (!form.name.trim()) {
      toast.error('请输入规则名称');
      return false;
    }
    if (!form.target_datasource_id) {
      toast.error('请选择目标数据源');
      return false;
    }
    if (!form.target_table.trim()) {
      toast.error('请输入目标表名');
      return false;
    }
    try {
      JSON.parse(form.rule_config);
      setConfigError('');
    } catch {
      setConfigError('JSON 格式不正确');
      return false;
    }
    return true;
  };

  const handleSave = async () => {
    if (!validateForm()) return;

    setSaving(true);
    try {
      const payload = {
        ...form,
        target_datasource_id: Number(form.target_datasource_id),
        rule_config: JSON.parse(form.rule_config),
        workspace_id: currentWorkspaceId,
      };
      if (editRule) {
        await qualityApi.updateRule(editRule.id, payload);
        toast.success('规则更新成功');
      } else {
        await qualityApi.createRule(payload);
        toast.success('规则创建成功');
      }
      setFormOpen(false);
      resetForm();
      loadRules();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (rule: QualityRule) => {
    try {
      await qualityApi.updateRule(rule.id, { is_active: !rule.is_active });
      toast.success(rule.is_active ? '已禁用' : '已启用');
      loadRules();
    } catch {
      toast.error('操作失败');
    }
  };

  const handleExecute = async (rule: QualityRule) => {
    try {
      await qualityApi.executeRule(rule.id);
      toast.success(`规则「${rule.name}」已触发执行`);
    } catch {
      toast.error('执行失败');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await qualityApi.deleteRule(deleteTarget.id);
      toast.success('规则已删除');
      setDeleteTarget(null);
      loadRules();
    } catch {
      toast.error('删除失败');
    }
  };

  // Filtered rules
  const filteredRules = rules.filter(rule => {
    if (filterType !== 'all' && rule.rule_type !== filterType) return false;
    if (filterSeverity !== 'all' && rule.severity !== filterSeverity) return false;
    if (filterTable && !rule.target_table.toLowerCase().includes(filterTable.toLowerCase())) return false;
    if (searchText && !rule.name.toLowerCase().includes(searchText.toLowerCase())) return false;
    return true;
  });

  if (!currentWorkspaceId) {
    return (
      <div className="p-6 text-center text-muted-foreground">
        请先选择工作空间
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">质量规则</h1>
          <p className="text-muted-foreground text-sm mt-1">
            管理数据质量检查规则，配置检查项和严重等级
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadRules} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            新建规则
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 p-3 border rounded-lg bg-muted/30">
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Filter className="w-4 h-4" />
          筛选
        </div>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="搜索规则名称..."
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            className="pl-8 h-8 w-48"
          />
        </div>
        <Select value={filterType} onValueChange={setFilterType}>
          <SelectTrigger className="h-8 w-36">
            <SelectValue placeholder="规则类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            {RULE_TYPE_OPTIONS.map(opt => (
              <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={filterSeverity} onValueChange={setFilterSeverity}>
          <SelectTrigger className="h-8 w-32">
            <SelectValue placeholder="严重度" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部严重度</SelectItem>
            {SEVERITY_OPTIONS.map(opt => (
              <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          placeholder="目标表名..."
          value={filterTable}
          onChange={e => setFilterTable(e.target.value)}
          className="h-8 w-48"
        />
        {(filterType !== 'all' || filterSeverity !== 'all' || filterTable || searchText) && (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 text-xs"
            onClick={() => { setFilterType('all'); setFilterSeverity('all'); setFilterTable(''); setSearchText(''); }}
          >
            清除筛选
          </Button>
        )}
      </div>

      {/* Rules Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">规则名称</th>
              <th className="text-left p-3 font-medium">类型</th>
              <th className="text-left p-3 font-medium">目标表</th>
              <th className="text-center p-3 font-medium">严重度</th>
              <th className="text-center p-3 font-medium">状态</th>
              <th className="text-left p-3 font-medium">上次检查</th>
              <th className="text-center p-3 font-medium">启用</th>
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
            ) : filteredRules.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-muted-foreground">
                  {rules.length === 0 ? '暂无质量规则，点击"新建规则"创建' : '没有匹配的规则'}
                </td>
              </tr>
            ) : (
              filteredRules.map(rule => (
                <tr key={rule.id} className="border-t hover:bg-muted/30">
                  <td className="p-3">
                    <div className="font-medium">{rule.name}</div>
                    {rule.description && (
                      <div className="text-xs text-muted-foreground mt-0.5 truncate max-w-[250px]">
                        {rule.description}
                      </div>
                    )}
                  </td>
                  <td className="p-3">
                    <Badge variant="outline" className="bg-purple-500/10 text-purple-500 border-purple-500/20">
                      {RULE_TYPE_MAP[rule.rule_type] || rule.rule_type}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
                      {rule.target_table}
                    </code>
                    {rule.target_column && (
                      <span className="text-xs text-muted-foreground ml-1">
                        .{rule.target_column}
                      </span>
                    )}
                  </td>
                  <td className="p-3 text-center">
                    <Badge variant="outline" className={SEVERITY_MAP[rule.severity]?.color}>
                      {SEVERITY_MAP[rule.severity]?.label || rule.severity}
                    </Badge>
                  </td>
                  <td className="p-3 text-center">
                    {rule.last_check_status ? (
                      <Badge variant="outline" className={STATUS_MAP[rule.last_check_status]?.color}>
                        {STATUS_MAP[rule.last_check_status]?.label || rule.last_check_status}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">未检查</span>
                    )}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {rule.last_check_at ? new Date(rule.last_check_at).toLocaleString() : '-'}
                  </td>
                  <td className="p-3 text-center">
                    <Switch
                      checked={rule.is_active}
                      onCheckedChange={() => handleToggle(rule)}
                    />
                  </td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleExecute(rule)}
                        title="执行检查"
                      >
                        <Play className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(rule)}
                        title="编辑"
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(rule)}
                        title="删除"
                      >
                        <Trash2 className="w-4 h-4 text-destructive" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Summary */}
      {rules.length > 0 && (
        <div className="text-xs text-muted-foreground text-right">
          共 {rules.length} 条规则
          {filteredRules.length !== rules.length && `，筛选显示 ${filteredRules.length} 条`}
        </div>
      )}

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={(open) => { if (!open) resetForm(); setFormOpen(open); }}>
        <DialogContent
          className="max-w-2xl max-h-[90vh] overflow-y-auto"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{editRule ? '编辑规则' : '新建质量规则'}</DialogTitle>
            <DialogDescription>
              配置数据质量检查规则的参数和检查条件
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>规则名称 <span className="text-destructive">*</span></Label>
                <Input
                  value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="如：订单金额非空检查"
                />
              </div>
              <div className="space-y-1.5">
                <Label>规则类型</Label>
                <Select
                  value={form.rule_type}
                  onValueChange={v => setForm(f => ({ ...f, rule_type: v }))}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {RULE_TYPE_OPTIONS.map(opt => (
                      <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>描述</Label>
              <Input
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="规则用途说明（可选）"
              />
            </div>

            <div className="space-y-1.5">
              <Label>目标数据源 <span className="text-destructive">*</span></Label>
              <Select
                value={form.target_datasource_id}
                onValueChange={v => setForm(f => ({ ...f, target_datasource_id: v }))}
              >
                <SelectTrigger><SelectValue placeholder="选择数据源" /></SelectTrigger>
                <SelectContent>
                  {datasources.map(ds => (
                    <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>目标表 <span className="text-destructive">*</span></Label>
                <Input
                  value={form.target_table}
                  onChange={e => setForm(f => ({ ...f, target_table: e.target.value }))}
                  placeholder="如：orders"
                />
              </div>
              <div className="space-y-1.5">
                <Label>目标列</Label>
                <Input
                  value={form.target_column}
                  onChange={e => setForm(f => ({ ...f, target_column: e.target.value }))}
                  placeholder="如：amount（可选）"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>严重等级</Label>
              <Select
                value={form.severity}
                onValueChange={v => setForm(f => ({ ...f, severity: v }))}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SEVERITY_OPTIONS.map(opt => (
                    <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>规则配置 (JSON)</Label>
              <Textarea
                value={form.rule_config}
                onChange={e => {
                  setForm(f => ({ ...f, rule_config: e.target.value }));
                  setConfigError('');
                }}
                className="font-mono text-sm min-h-[120px]"
                placeholder='{"min_value": 0, "max_value": 1000000}'
              />
              {configError && (
                <p className="text-xs text-destructive">{configError}</p>
              )}
              <p className="text-xs text-muted-foreground">
                根据规则类型填写对应的配置参数，JSON 格式
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setFormOpen(false); resetForm(); }}>
              取消
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : editRule ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除质量规则「{deleteTarget?.name}」吗？相关检查结果也将被清除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
