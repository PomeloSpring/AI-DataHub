import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { Plus, Edit, Trash2, Route, Search, BookX } from 'lucide-react';
import client from '@/api/client';

// ── Types ──────────────────────────────────────────────────────────

interface KnowledgeItem {
  id: number;
  workspace_id: number;
  datasource_id: number;
  knowledge_type: string;
  title: string;
  content: string;
  metadata: { triggers?: string[]; scenario?: string } | null;
  related_tables: string;
  priority: number;
  usage_count: number;
  positive_count: number;
  negative_count: number;
  is_active: number;
  created_at: string;
  updated_at: string;
}

interface FormData {
  title: string;
  triggers: string;       // 逗号分隔的触发词
  scenario: string;       // 适用场景
  content: string;        // 查询方案说明
  related_tables: string;
  priority: number;
  is_active: boolean;
}

const DEFAULT_FORM: FormData = {
  title: '',
  triggers: '',
  scenario: '',
  content: '',
  related_tables: '',
  priority: 0,
  is_active: true,
};

const splitTriggers = (s: string): string[] =>
  s.split(/[,，]/).map(t => t.trim()).filter(Boolean);

export default function KnowledgeManagement() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editItem, setEditItem] = useState<KnowledgeItem | null>(null);
  const [form, setForm] = useState<FormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeItem | null>(null);

  const PAGE_SIZE = 20;

  // ── Load items ───────────────────────────────────────────────────

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = {
        workspace_id: 0,
        knowledge_type: 'quick_path',
        page,
        size: PAGE_SIZE,
      };
      if (searchText) params.search = searchText;
      const { data } = await client.get('/admin/knowledge', { params });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      // 404 = 暂无数据，不弹 toast
      if (e?.response?.status && e.response.status >= 500) {
        toast.error('加载失败');
      }
    } finally {
      setLoading(false);
    }
  }, [page, searchText]);

  useEffect(() => { loadItems(); }, [loadItems]);

  // ── Form handling ────────────────────────────────────────────────

  const openCreate = () => {
    setEditItem(null);
    setForm({ ...DEFAULT_FORM });
    setFormOpen(true);
  };

  const openEdit = (item: KnowledgeItem) => {
    setEditItem(item);
    setForm({
      title: item.title,
      triggers: (item.metadata?.triggers || []).join(', '),
      scenario: item.metadata?.scenario || '',
      content: item.content,
      related_tables: item.related_tables || '',
      priority: item.priority,
      is_active: !!item.is_active,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.title.trim() || !form.content.trim()) {
      toast.error('请填写路径名称和查询方案说明');
      return;
    }
    if (splitTriggers(form.triggers).length === 0) {
      toast.error('请至少配置一个触发词');
      return;
    }
    setSaving(true);
    try {
      const metadata = {
        triggers: splitTriggers(form.triggers),
        scenario: form.scenario.trim(),
      };
      const payload: any = {
        workspace_id: 0,
        datasource_id: 0,
        knowledge_type: 'quick_path',
        title: form.title,
        content: form.content,
        metadata,
        related_tables: form.related_tables,
        priority: form.priority,
        is_active: form.is_active ? 1 : 0,
      };

      if (editItem) {
        await client.put(`/admin/knowledge/${editItem.id}`, payload);
        toast.success('已更新');
      } else {
        await client.post('/admin/knowledge', payload);
        toast.success('已创建');
      }
      setFormOpen(false);
      loadItems();
    } catch {
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/admin/knowledge/${deleteTarget.id}`);
      toast.success('已删除');
      setDeleteTarget(null);
      loadItems();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (item: KnowledgeItem) => {
    try {
      await client.patch(`/admin/knowledge/${item.id}/toggle`);
      loadItems();
    } catch {
      toast.error('切换状态失败');
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Route className="h-6 w-6" />
            快速路径
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            配置查询方案路径：用户问题命中触发词时，将该路径的方案说明注入 AI 取数上下文（类似技能）
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1" />新增快速路径
        </Button>
      </div>

      {/* Search */}
      <div className="mb-4">
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索路径名称或方案..."
            value={searchText}
            onChange={e => { setSearchText(e.target.value); setPage(1); }}
            className="pl-8 w-64"
          />
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div className="text-center py-8 text-muted-foreground">加载中...</div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <BookX className="h-10 w-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">暂无快速路径</p>
          <p className="text-xs mt-1">点击上方按钮创建，命中触发词后将方案注入 AI 上下文</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(item => {
            const triggers = item.metadata?.triggers || [];
            return (
              <div
                key={item.id}
                className="border rounded-lg p-4 flex items-start justify-between hover:bg-muted/30 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <Badge variant="outline" className="text-indigo-600 border-indigo-200 bg-indigo-50">
                      <Route className="h-3 w-3 mr-1" />快速路径
                    </Badge>
                    <span className="font-medium">{item.title}</span>
                    <Badge variant={item.is_active ? 'default' : 'outline'} className="text-xs">
                      {item.is_active ? '启用' : '禁用'}
                    </Badge>
                    {item.priority > 0 && (
                      <Badge variant="secondary" className="text-xs">优先级: {item.priority}</Badge>
                    )}
                  </div>
                  {triggers.length > 0 ? (
                    <div className="flex items-center gap-1.5 flex-wrap mb-1.5">
                      <span className="text-xs text-muted-foreground">触发词:</span>
                      {triggers.map(t => (
                        <Badge key={t} variant="secondary" className="text-[11px] font-normal">{t}</Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-amber-600 mb-1.5">未配置触发词，该路径不会命中</p>
                  )}
                  {item.metadata?.scenario && (
                    <p className="text-xs text-muted-foreground mb-1">场景: {item.metadata.scenario}</p>
                  )}
                  <p className="text-sm text-muted-foreground line-clamp-2">{item.content}</p>
                  <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
                    {item.related_tables && <span>关联表: {item.related_tables}</span>}
                    <span>命中: {item.usage_count}</span>
                    <span>{item.updated_at}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-4 shrink-0">
                  <Switch
                    checked={!!item.is_active}
                    onCheckedChange={() => handleToggle(item)}
                  />
                  <Button variant="ghost" size="sm" onClick={() => openEdit(item)}>
                    <Edit className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(item)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between mt-4 pt-3 border-t">
          <span className="text-sm text-muted-foreground">共 {total} 条</span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</Button>
            <span className="text-sm tabular-nums">{page} / {totalPages}</span>
            <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>下一页</Button>
          </div>
        </div>
      )}

      {/* ── Create/Edit Dialog ──────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{editItem ? '编辑快速路径' : '新增快速路径'}</DialogTitle>
            <DialogDescription>
              配置路径名称、触发词与查询方案说明；命中后将方案注入 AI 取数上下文
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>路径名称 *</Label>
              <Input
                value={form.title}
                onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
                placeholder="如: 按月统计销售额"
              />
            </div>
            <div>
              <Label>触发词 *（逗号分隔）</Label>
              <Input
                value={form.triggers}
                onChange={e => setForm(f => ({ ...f, triggers: e.target.value }))}
                placeholder="如: 销售额, GMV, 成交金额"
              />
              <p className="text-xs text-muted-foreground mt-1">用户问题命中任一触发词即套用本路径方案</p>
            </div>
            <div>
              <Label>适用场景</Label>
              <Input
                value={form.scenario}
                onChange={e => setForm(f => ({ ...f, scenario: e.target.value }))}
                placeholder="简述这类问题何时出现（可选）"
              />
            </div>
            <div>
              <Label>查询方案说明 *</Label>
              <Textarea
                value={form.content}
                onChange={e => setForm(f => ({ ...f, content: e.target.value }))}
                placeholder="告诉 AI 该如何查询：涉及的表/对象、口径、推荐步骤、示例 SQL 或查询条件"
                rows={5}
              />
            </div>
            <div>
              <Label>关联表名</Label>
              <Input
                value={form.related_tables}
                onChange={e => setForm(f => ({ ...f, related_tables: e.target.value }))}
                placeholder="逗号分隔，如: orders,users（可选）"
              />
            </div>
            <div>
              <Label>优先级</Label>
              <Input
                type="number"
                value={form.priority}
                onChange={e => setForm(f => ({ ...f, priority: Number(e.target.value) }))}
                placeholder="0"
              />
              <p className="text-xs text-muted-foreground mt-1">命中同级时越高越优先注入</p>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={form.is_active}
                onCheckedChange={v => setForm(f => ({ ...f, is_active: v }))}
              />
              <Label>启用</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除 "{deleteTarget?.title}" 吗？此操作不可撤销。
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
