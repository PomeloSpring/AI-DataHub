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
import { toast } from '@/lib/toast';
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
import { useTranslation } from 'react-i18next';
import { t as i18nTr } from '@/i18n/runtime';

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
  { value: 'manual', label: i18nTr('k_68004de140') },
  { value: 'rule', label: i18nTr('k_8fca52eaa6') },
  { value: 'computed', label: i18nTr('k_05c58fbe8d') },
];

const ENTITY_TYPES = [
  { value: 'user', label: i18nTr('k_9ba763ea34') },
  { value: 'table', label: i18nTr('k_6a954390e3') },
  { value: 'column', label: i18nTr('k_77a49f2c38') },
  { value: 'metric', label: i18nTr('k_1dfc810a7b') },
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
  const { t } = useTranslation('ui');
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
      toast.error(t('k_a176ce1dd1'));
      return;
    }
    try {
      await tagsApi.createCategory({ ...categoryForm, workspace_id: currentWorkspaceId });
      toast.success(t('k_dc6e658d40'));
      setCategoryFormOpen(false);
      setCategoryForm(emptyCategoryForm);
      loadCategories();
    } catch {
      toast.error(t('k_5d26bddb54'));
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
      toast.error(t('k_770da4f225'));
      return;
    }
    setSaving(true);
    try {
      if (editTag) {
        await tagsApi.update(editTag.id, { ...tagForm, workspace_id: currentWorkspaceId });
        toast.success(t('k_5b2e92acc6'));
      } else {
        await tagsApi.create({ ...tagForm, workspace_id: currentWorkspaceId });
        toast.success(t('k_e1b1fe7995'));
      }
      setTagFormOpen(false);
      loadTags();
    } catch {
      toast.error(t('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteTag = async () => {
    if (!deleteTarget) return;
    try {
      await tagsApi.delete(deleteTarget.id);
      toast.success(t('k_722459eec7'));
      setDeleteTarget(null);
      if (selectedTag?.id === deleteTarget.id) setSelectedTag(null);
      loadTags();
    } catch {
      toast.error(t('k_acf0664a54'));
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
      toast.error(t('k_6c7e62c155'));
      return;
    }
    setQueryLoading(true);
    try {
      const res = await tagsApi.queryByTags({
        conditions: queryConditions,
        operator: queryMode === 'intersection' ? 'AND' : 'OR',
        workspace_id: currentWorkspaceId,
      });
      const items = res.data?.items ?? res.data ?? [];
      setQueryResults(items);
      if (items.length === 0) toast.info(t('k_638c280f60'));
    } catch {
      toast.error(t('k_0d66ed02d7'));
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
          <h1 className="text-2xl font-bold">{t("k_4e02a991e1")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t("k_b84c065515")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { loadCategories(); loadTags(); }}>
            <RefreshCw className="w-4 h-4 mr-1" />
            {t('k_38108eaa1d')}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setQueryOpen(true)}>
            <Filter className="w-4 h-4 mr-1" />
            {t('k_ddb558cc28')}
          </Button>
        </div>
      </div>

      <div className="flex flex-1 gap-4 min-h-0">
        {/* Left Panel: Category Tree */}
        <div className="w-64 border rounded-lg flex flex-col shrink-0">
          <div className="p-3 border-b flex items-center justify-between">
            <span className="text-sm font-medium">{t("k_e3901c2d01")}</span>
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
              <span>{t("k_ffd92f5524")}</span>
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
                placeholder={t('k_6efccc0615')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9 h-8"
              />
            </div>
            <Button size="sm" onClick={handleOpenCreateTag}>
              <Plus className="w-4 h-4 mr-1" />
              {t('k_ef17979507')}
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="p-8 text-center text-muted-foreground">{t('k_26b5bd4947')}</div>
            ) : tags.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground">{t("k_ec7eb0e788")}</div>
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
                        <span className="font-medium truncate">{tag.display_name || tag.name || t('k_ac52a99171')}</span>
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
              {selectedTag ? t('k_b709ab7a13', { p0: selectedTag.display_name || selectedTag.name || t('k_ac52a99171') }) : t('k_143a4d6ab5')}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto">
            {!selectedTag ? (
              <div className="p-8 text-center text-muted-foreground text-sm">
                {t('k_a8f4125d48')}
              </div>
            ) : valuesLoading ? (
              <div className="p-8 text-center text-muted-foreground">{t('k_26b5bd4947')}</div>
            ) : tagValues.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground text-sm">{t("k_a6a3d71296")}</div>
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
            <DialogTitle>{t("k_65ef925644")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('k_de50f95528')}</label>
              <Input
                placeholder={t('k_56fb9bf148')}
                value={categoryForm.name}
                onChange={(e) => setCategoryForm({ ...categoryForm, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("k_75ae6a8a7d")}</label>
              <Input
                placeholder={t('k_0b8ef8ee7a')}
                value={categoryForm.display_name}
                onChange={(e) => setCategoryForm({ ...categoryForm, display_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("k_412f54dc38")}</label>
              <Input
                placeholder={t("k_9de027c1cc")}
                value={categoryForm.description}
                onChange={(e) => setCategoryForm({ ...categoryForm, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCategoryFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleCreateCategory}>{t("k_fcbd093292")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Create/Edit Tag Dialog */}
      <Dialog open={tagFormOpen} onOpenChange={setTagFormOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editTag ? t('k_4f3b1f6754') : t('k_ef17979507')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">{t('k_30a7bc2745')}</label>
                <Input
                  placeholder={t('k_5f5701e5b0')}
                  value={tagForm.name}
                  onChange={(e) => setTagForm({ ...tagForm, name: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">{t('k_f75459f6f0')}</label>
                <Input
                  placeholder={t('k_7e4289958d')}
                  value={tagForm.display_name}
                  onChange={(e) => setTagForm({ ...tagForm, display_name: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">{t("k_f862713fce")}</label>
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
                <label className="text-sm font-medium">{t("k_102b091f8d")}</label>
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
              <label className="text-sm font-medium">{t("k_b031dc9a85")}</label>
              <Select value={tagForm.data_type} onValueChange={(v) => setTagForm({ ...tagForm, data_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="string">{t("k_4dc9621a04")}</SelectItem>
                  <SelectItem value="int">{t("k_58306b30f4")}</SelectItem>
                  <SelectItem value="float">{t("k_82dbe08c5a")}</SelectItem>
                  <SelectItem value="boolean">{t("k_642a2c8a34")}</SelectItem>
                  <SelectItem value="date">{t("k_b6fed9af83")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {tagForm.tag_type === 'rule' && (
              <div className="space-y-2">
                <label className="text-sm font-medium">{t('k_f9ae4186e1')}</label>
                <Input
                  placeholder='{"field": "amount", "operator": ">", "value": 1000}'
                  value={tagForm.rule_config}
                  onChange={(e) => setTagForm({ ...tagForm, rule_config: e.target.value })}
                />
              </div>
            )}
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("k_412f54dc38")}</label>
              <Input
                placeholder={t("k_1d61efe733")}
                value={tagForm.description}
                onChange={(e) => setTagForm({ ...tagForm, description: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTagFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSaveTag} disabled={saving}>
              {saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('k_8a8093d488', { p0: deleteTarget?.display_name || deleteTarget?.name || t('k_ac52a99171') })}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDeleteTag}>{t("k_3755f56f2f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Tag Query Dialog */}
      <Dialog open={queryOpen} onOpenChange={setQueryOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("k_ddb558cc28")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">{t("k_3de5bfc915")}</span>
              <Select value={queryMode} onValueChange={(v: any) => setQueryMode(v)}>
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="intersection">{t('k_898f57087b')}</SelectItem>
                  <SelectItem value="union">{t('k_56c1c56cd2')}</SelectItem>
                </SelectContent>
              </Select>
              <Button variant="outline" size="sm" onClick={addQueryCondition}>
                <Plus className="w-4 h-4 mr-1" />
                {t('k_8db825de05')}
              </Button>
            </div>
            {queryConditions.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">{t('k_2c61766be4')}</p>
            ) : (
              <div className="space-y-2">
                {queryConditions.map((cond, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <Select
                      value={cond.tag_id ? String(cond.tag_id) : ''}
                      onValueChange={(v) => updateQueryCondition(idx, 'tag_id', Number(v))}
                    >
                      <SelectTrigger className="w-40">
                        <SelectValue placeholder={t("k_608b8e137e")} />
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
                        <SelectItem value="eq">{t("k_bd0f309115")}</SelectItem>
                        <SelectItem value="neq">{t("k_3b8d5ab52b")}</SelectItem>
                        <SelectItem value="contains">{t("k_f896fb3776")}</SelectItem>
                        <SelectItem value="gt">{t("k_9e2e25fc2d")}</SelectItem>
                        <SelectItem value="lt">{t("k_b445321cbd")}</SelectItem>
                      </SelectContent>
                    </Select>
                    <Input
                      placeholder={t("k_321b955840")}
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
                {queryLoading ? t('k_4ea9bfb770') : t('k_4fb36a6129')}
              </Button>
            )}
            {queryResults.length > 0 && (
              <div className="border rounded-lg max-h-60 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50 sticky top-0">
                    <tr>
                      <th className="text-left p-2 font-medium">{t("k_48c09a901b")}</th>
                      <th className="text-left p-2 font-medium">{t("k_102b091f8d")}</th>
                      <th className="text-left p-2 font-medium">{t("k_a6691a5c6a")}</th>
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
            <Button variant="outline" onClick={() => { setQueryOpen(false); setQueryResults([]); }}>{t("k_6c14bd7f6f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
