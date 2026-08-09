import { useState, useEffect, useCallback } from 'react';
import { tagsApi } from '@/api/tags';
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
  Tag,
  FolderOpen,
  Folder,
  ChevronRight,
  ChevronDown,
  Layers,
  Filter,
  X,
} from 'lucide-react';

interface TagCategory {
  id: number;
  name: string;
  display_name: string;
  description?: string;
  children?: TagCategory[];
  tag_count?: number;
}

interface TagItem {
  id: number;
  name: string;
  display_name: string;
  tag_type: string;
  entity_type: string;
  data_type?: string;
  rule_config?: string;
  description?: string;
  category_id?: number;
  category_name?: string;
  created_at: string;
}

interface TagValue {
  id: number;
  tag_id: number;
  entity_id: string;
  value: string;
  source: string;
  created_at: string;
}

interface CategoryFormData {
  name: string;
  display_name: string;
  description: string;
}

interface TagFormData {
  name: string;
  display_name: string;
  tag_type: string;
  entity_type: string;
  data_type: string;
  rule_config: string;
  description: string;
  category_id: number | null;
}

interface QueryCondition {
  tag_id: number;
  tag_name: string;
  operator: string;
  value: string;
}

const TAG_TYPES = [
  { value: 'manual', label: '手动标签' },
  { value: 'rule', label: '规则标签' },
  { value: 'computed', label: '计算标签' },
];

const ENTITY_TYPES = [
  { value: 'user', label: '用户' },
  { value: 'table', label: '表' },
  { value: 'column', label: '字段' },
  { value: 'metric', label: '指标' },
];

const TAG_TYPE_COLORS: Record<string, string> = {
  manual: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  rule: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  computed: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
};

const ENTITY_TYPE_COLORS: Record<string, string> = {
  user: 'bg-green-500/10 text-green-500 border-green-500/20',
  table: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20',
  column: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  metric: 'bg-pink-500/10 text-pink-500 border-pink-500/20',
};

const emptyCategoryForm: CategoryFormData = { name: '', display_name: '', description: '' };
const emptyTagForm: TagFormData = {
  name: '', display_name: '', tag_type: 'manual', entity_type: 'table',
  data_type: 'string', rule_config: '', description: '', category_id: null,
};

export default function TagsManager() {
  const { currentWorkspaceId } = useWorkspaceStore();

  // Left panel state
  const [categories, setCategories] = useState<TagCategory[]>([]);
  const [expandedCats, setExpandedCats] = useState<Set<number>>(new Set());
  const [selectedCatId, setSelectedCatId] = useState<number | null>(null);

  // Right panel state
  const [tags, setTags] = useState<TagItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  // Dialogs
  const [categoryFormOpen, setCategoryFormOpen] = useState(false);
  const [categoryForm, setCategoryForm] = useState<CategoryFormData>(emptyCategoryForm);

  const [tagFormOpen, setTagFormOpen] = useState(false);
  const [editTag, setEditTag] = useState<TagItem | null>(null);
  const [tagForm, setTagForm] = useState<TagFormData>(emptyTagForm);
  const [saving, setSaving] = useState(false);

  // Tag values panel
  const [selectedTag, setSelectedTag] = useState<TagItem | null>(null);
  const [tagValues, setTagValues] = useState<TagValue[]>([]);
  const [valuesLoading, setValuesLoading] = useState(false);

  // Query panel
  const [queryOpen, setQueryOpen] = useState(false);
  const [queryConditions, setQueryConditions] = useState<QueryCondition[]>([]);
  const [queryMode, setQueryMode] = useState<'intersection' | 'union'>('intersection');
  const [queryResults, setQueryResults] = useState<any[]>([]);
  const [queryLoading, setQueryLoading] = useState(false);

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<TagItem | null>(null);

  const loadCategories = useCallback(async () => {
    try {
      const res = await tagsApi.getCategories(currentWorkspaceId);
      const items = res?.data;
      setCategories(Array.isArray(items) ? items : []);
    } catch {
      setCategories([]);
    }
  }, [currentWorkspaceId]);

  const loadTags = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = { workspace_id: currentWorkspaceId };
      if (selectedCatId) params.category_id = selectedCatId;
      if (search) params.search = search;
      const res = await tagsApi.list(params);
      const items = res?.data?.items ?? res?.data;
      setTags(Array.isArray(items) ? items : []);
    } catch {
      setTags([]);
    } finally {
      setLoading(false);
    }
  }, [currentWorkspaceId, selectedCatId, search]);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  useEffect(() => {
    loadTags();
  }, [loadTags]);

  const toggleExpand = (catId: number) => {
    setExpandedCats((prev) => {
      const next = new Set(prev);
      if (next.has(catId)) next.delete(catId);
      else next.add(catId);
      return next;
    });
  };

  const handleCreateCategory = async () => {
    if (!categoryForm.name) {
      toast.error('请填写分类名称');
      return;
    }
    try {
      await tagsApi.createCategory({ ...categoryForm, workspace_id: currentWorkspaceId });
      toast.success('分类已创建');
      setCategoryFormOpen(false);
      setCategoryForm(emptyCategoryForm);
      loadCategories();
    } catch {
      toast.error('创建分类失败');
    }
  };

  const handleOpenCreateTag = () => {
    setEditTag(null);
    setTagForm({ ...emptyTagForm, category_id: selectedCatId });
    setTagFormOpen(true);
  };

  const handleOpenEditTag = (tag: TagItem) => {
    setEditTag(tag);
    setTagForm({
      name: tag.name,
      display_name: tag.display_name,
      tag_type: tag.tag_type,
      entity_type: tag.entity_type,
      data_type: tag.data_type || 'string',
      rule_config: tag.rule_config || '',
      description: tag.description || '',
      category_id: tag.category_id || null,
    });
    setTagFormOpen(true);
  };

  const handleSaveTag = async () => {
    if (!tagForm.name || !tagForm.display_name) {
      toast.error('请填写标签名称和显示名称');
      return;
    }
    setSaving(true);
    try {
      if (editTag) {
        await tagsApi.update(editTag.id, { ...tagForm, workspace_id: currentWorkspaceId });
        toast.success('标签已更新');
      } else {
        await tagsApi.create({ ...tagForm, workspace_id: currentWorkspaceId });
        toast.success('标签已创建');
      }
      setTagFormOpen(false);
      loadTags();
    } catch {
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteTag = async () => {
    if (!deleteTarget) return;
    try {
      await tagsApi.delete(deleteTarget.id);
      toast.success('标签已删除');
      setDeleteTarget(null);
      if (selectedTag?.id === deleteTarget.id) setSelectedTag(null);
      loadTags();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleViewValues = async (tag: TagItem) => {
    setSelectedTag(tag);
    setValuesLoading(true);
    try {
      const res = await tagsApi.getValues(tag.id);
      const items = res?.data;
      setTagValues(Array.isArray(items) ? items : []);
    } catch {
      setTagValues([]);
    } finally {
      setValuesLoading(false);
    }
  };

  const handleQuery = async () => {
    if (queryConditions.length === 0) {
      toast.error('请添加至少一个查询条件');
      return;
    }
    setQueryLoading(true);
    try {
      const res = await tagsApi.queryByTags({
        conditions: queryConditions,
        mode: queryMode,
        workspace_id: currentWorkspaceId,
      });
      setQueryResults(res.data || []);
      if ((res.data || []).length === 0) toast.info('未找到匹配的实体');
    } catch {
      toast.error('查询失败');
    } finally {
      setQueryLoading(false);
    }
  };

  const addQueryCondition = () => {
    setQueryConditions([...queryConditions, { tag_id: 0, tag_name: '', operator: 'eq', value: '' }]);
  };

  const updateQueryCondition = (idx: number, field: keyof QueryCondition, value: any) => {
    const updated = [...queryConditions];
    updated[idx] = { ...updated[idx], [field]: value };
    if (field === 'tag_id') {
      const tag = tags.find((t) => t.id === value);
      if (tag) updated[idx].tag_name = tag.display_name;
    }
    setQueryConditions(updated);
  };

  const removeQueryCondition = (idx: number) => {
    setQueryConditions(queryConditions.filter((_, i) => i !== idx));
  };

  // Render category tree node
  const renderCategoryNode = (cat: TagCategory, depth: number = 0) => {
    const hasChildren = cat.children && cat.children.length > 0;
    const isExpanded = expandedCats.has(cat.id);
    const isSelected = selectedCatId === cat.id;

    return (
      <div key={cat.id}>
        <div
          className={`flex items-center gap-1 px-2 py-1.5 rounded-md cursor-pointer text-sm hover:bg-muted/50 ${
            isSelected ? 'bg-muted font-medium' : ''
          }`}
          style={{ paddingLeft: `${depth * 16 + 8}px` }}
          onClick={() => setSelectedCatId(isSelected ? null : cat.id)}
        >
          {hasChildren ? (
            <button
              className="p-0.5 hover:bg-muted rounded"
              onClick={(e) => { e.stopPropagation(); toggleExpand(cat.id); }}
            >
              {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            </button>
          ) : (
            <span className="w-5" />
          )}
          {isExpanded ? <FolderOpen className="w-4 h-4 text-muted-foreground mr-1.5" /> : <Folder className="w-4 h-4 text-muted-foreground mr-1.5" />}
          <span className="flex-1 truncate">{cat.display_name || cat.name}</span>
          {cat.tag_count !== undefined && (
            <span className="text-xs text-muted-foreground">{cat.tag_count}</span>
          )}
        </div>
        {hasChildren && isExpanded && cat.children!.map((child) => renderCategoryNode(child, depth + 1))}
      </div>
    );
  };

  return (
    <div className="p-6 h-full flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">标签管理</h1>
          <p className="text-muted-foreground text-sm mt-1">管理标签分类、标签定义和标签值</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { loadCategories(); loadTags(); }}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button variant="outline" size="sm" onClick={() => setQueryOpen(true)}>
            <Filter className="w-4 h-4 mr-1" />
            标签查询
          </Button>
        </div>
      </div>

      <div className="flex flex-1 gap-4 min-h-0">
        {/* Left Panel: Category Tree */}
        <div className="w-64 border rounded-lg flex flex-col shrink-0">
          <div className="p-3 border-b flex items-center justify-between">
            <span className="text-sm font-medium">标签分类</span>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => { setCategoryForm(emptyCategoryForm); setCategoryFormOpen(true); }}>
              <Plus className="w-4 h-4" />
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            <div
              className={`flex items-center gap-1 px-2 py-1.5 rounded-md cursor-pointer text-sm hover:bg-muted/50 ${
                selectedCatId === null ? 'bg-muted font-medium' : ''
              }`}
              onClick={() => setSelectedCatId(null)}
            >
              <Layers className="w-4 h-4 text-muted-foreground mr-1.5" />
              <span>全部标签</span>
            </div>
            {categories.map((cat) => renderCategoryNode(cat))}
          </div>
        </div>

        {/* Center Panel: Tags List */}
        <div className="flex-1 border rounded-lg flex flex-col min-w-0">
          <div className="p-3 border-b flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                placeholder="搜索标签..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9 h-8"
              />
            </div>
            <Button size="sm" onClick={handleOpenCreateTag}>
              <Plus className="w-4 h-4 mr-1" />
              新建标签
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="p-8 text-center text-muted-foreground">加载中...</div>
            ) : tags.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground">暂无标签</div>
            ) : (
              <div className="divide-y">
                {tags.map((tag) => (
                  <div
                    key={tag.id}
                    className={`px-4 py-3 hover:bg-muted/30 cursor-pointer ${
                      selectedTag?.id === tag.id ? 'bg-muted/50' : ''
                    }`}
                    onClick={() => handleViewValues(tag)}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 min-w-0">
                        <Tag className="w-4 h-4 text-muted-foreground shrink-0" />
                        <span className="font-medium truncate">{tag.display_name}</span>
                        <Badge variant="outline" className={TAG_TYPE_COLORS[tag.tag_type] || ''}>
                          {TAG_TYPES.find((t) => t.value === tag.tag_type)?.label || tag.tag_type}
                        </Badge>
                        <Badge variant="outline" className={ENTITY_TYPE_COLORS[tag.entity_type] || ''}>
                          {ENTITY_TYPES.find((t) => t.value === tag.entity_type)?.label || tag.entity_type}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleOpenEditTag(tag)}>
                          <Edit className="w-3.5 h-3.5" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => setDeleteTarget(tag)}>
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                    {tag.description && (
                      <p className="text-xs text-muted-foreground mt-1 truncate">{tag.description}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right Panel: Tag Values */}
        <div className="w-80 border rounded-lg flex flex-col shrink-0">
          <div className="p-3 border-b">
            <span className="text-sm font-medium">
              {selectedTag ? `${selectedTag.display_name} - 标签值` : '标签值'}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto">
            {!selectedTag ? (
              <div className="p-8 text-center text-muted-foreground text-sm">
                点击标签查看其值
              </div>
            ) : valuesLoading ? (
              <div className="p-8 text-center text-muted-foreground">加载中...</div>
            ) : tagValues.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground text-sm">暂无标签值</div>
            ) : (
              <div className="divide-y">
                {tagValues.map((v) => (
                  <div key={v.id} className="px-3 py-2 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs text-muted-foreground">{v.entity_id}</span>
                      <Badge variant="outline" className="text-xs">{v.source}</Badge>
                    </div>
                    <div className="mt-1 font-medium">{v.value}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Create Category Dialog */}
      <Dialog open={categoryFormOpen} onOpenChange={setCategoryFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建标签分类</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">分类名称 *</label>
              <Input
                placeholder="如: business"
                value={categoryForm.name}
                onChange={(e) => setCategoryForm({ ...categoryForm, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">显示名称</label>
              <Input
                placeholder="如: 业务标签"
                value={categoryForm.display_name}
                onChange={(e) => setCategoryForm({ ...categoryForm, display_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">描述</label>
              <Input
                placeholder="分类说明"
                value={categoryForm.description}
                onChange={(e) => setCategoryForm({ ...categoryForm, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCategoryFormOpen(false)}>取消</Button>
            <Button onClick={handleCreateCategory}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Create/Edit Tag Dialog */}
      <Dialog open={tagFormOpen} onOpenChange={setTagFormOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editTag ? '编辑标签' : '新建标签'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">标签名称 *</label>
                <Input
                  placeholder="如: vip_level"
                  value={tagForm.name}
                  onChange={(e) => setTagForm({ ...tagForm, name: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">显示名称 *</label>
                <Input
                  placeholder="如: VIP等级"
                  value={tagForm.display_name}
                  onChange={(e) => setTagForm({ ...tagForm, display_name: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">标签类型</label>
                <Select value={tagForm.tag_type} onValueChange={(v) => setTagForm({ ...tagForm, tag_type: v })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TAG_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">实体类型</label>
                <Select value={tagForm.entity_type} onValueChange={(v) => setTagForm({ ...tagForm, entity_type: v })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ENTITY_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">数据类型</label>
              <Select value={tagForm.data_type} onValueChange={(v) => setTagForm({ ...tagForm, data_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="string">字符串</SelectItem>
                  <SelectItem value="int">整数</SelectItem>
                  <SelectItem value="float">浮点数</SelectItem>
                  <SelectItem value="boolean">布尔值</SelectItem>
                  <SelectItem value="date">日期</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {tagForm.tag_type === 'rule' && (
              <div className="space-y-2">
                <label className="text-sm font-medium">规则配置 (JSON)</label>
                <Input
                  placeholder='{"field": "amount", "operator": ">", "value": 1000}'
                  value={tagForm.rule_config}
                  onChange={(e) => setTagForm({ ...tagForm, rule_config: e.target.value })}
                />
              </div>
            )}
            <div className="space-y-2">
              <label className="text-sm font-medium">描述</label>
              <Input
                placeholder="标签说明"
                value={tagForm.description}
                onChange={(e) => setTagForm({ ...tagForm, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTagFormOpen(false)}>取消</Button>
            <Button onClick={handleSaveTag} disabled={saving}>
              {saving ? '保存中...' : '保存'}
            </Button>
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
            确定要删除标签 <strong>{deleteTarget?.display_name}</strong> 吗？此操作不可恢复。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDeleteTag}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Tag Query Dialog */}
      <Dialog open={queryOpen} onOpenChange={setQueryOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>标签查询</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">匹配模式：</span>
              <Select value={queryMode} onValueChange={(v: any) => setQueryMode(v)}>
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="intersection">交集 (AND)</SelectItem>
                  <SelectItem value="union">并集 (OR)</SelectItem>
                </SelectContent>
              </Select>
              <Button variant="outline" size="sm" onClick={addQueryCondition}>
                <Plus className="w-4 h-4 mr-1" />
                添加条件
              </Button>
            </div>
            {queryConditions.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">点击"添加条件"开始构建查询</p>
            ) : (
              <div className="space-y-2">
                {queryConditions.map((cond, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <Select
                      value={cond.tag_id ? String(cond.tag_id) : ''}
                      onValueChange={(v) => updateQueryCondition(idx, 'tag_id', Number(v))}
                    >
                      <SelectTrigger className="w-40">
                        <SelectValue placeholder="选择标签" />
                      </SelectTrigger>
                      <SelectContent>
                        {tags.map((t) => (
                          <SelectItem key={t.id} value={String(t.id)}>{t.display_name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Select value={cond.operator} onValueChange={(v) => updateQueryCondition(idx, 'operator', v)}>
                      <SelectTrigger className="w-24">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="eq">等于</SelectItem>
                        <SelectItem value="neq">不等于</SelectItem>
                        <SelectItem value="contains">包含</SelectItem>
                        <SelectItem value="gt">大于</SelectItem>
                        <SelectItem value="lt">小于</SelectItem>
                      </SelectContent>
                    </Select>
                    <Input
                      placeholder="值"
                      value={cond.value}
                      onChange={(e) => updateQueryCondition(idx, 'value', e.target.value)}
                      className="flex-1"
                    />
                    <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => removeQueryCondition(idx)}>
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
            {queryConditions.length > 0 && (
              <Button onClick={handleQuery} disabled={queryLoading} className="w-full">
                {queryLoading ? '查询中...' : '执行查询'}
              </Button>
            )}
            {queryResults.length > 0 && (
              <div className="border rounded-lg max-h-60 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50 sticky top-0">
                    <tr>
                      <th className="text-left p-2 font-medium">实体ID</th>
                      <th className="text-left p-2 font-medium">实体类型</th>
                      <th className="text-left p-2 font-medium">匹配标签</th>
                    </tr>
                  </thead>
                  <tbody>
                    {queryResults.map((r, idx) => (
                      <tr key={idx} className="border-t">
                        <td className="p-2 font-mono text-xs">{r.entity_id}</td>
                        <td className="p-2">{r.entity_type}</td>
                        <td className="p-2">
                          {(r.matched_tags || []).map((t: string, i: number) => (
                            <Badge key={i} variant="outline" className="mr-1 text-xs">{t}</Badge>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setQueryOpen(false); setQueryResults([]); }}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
