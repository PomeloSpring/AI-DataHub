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
import { Plus, Edit, Trash2, FileQuestion, Search, Database } from 'lucide-react';
import client from '@/api/client';

// ── Types ──────────────────────────────────────────────────────────

interface SqlPairItem {
  id: number;
  workspace_id: number;
  datasource_id: number;
  knowledge_type: string;
  title: string;
  content: string;
  metadata: {
    question?: string;
    answer_sql?: string;
    explanation?: string;
  } | null;
  related_tables: string;
  priority: number;
  usage_count: number;
  is_active: number;
  created_at: string;
  updated_at: string;
}

interface FormData {
  title: string;
  question: string;
  answer_sql: string;
  explanation: string;
  related_tables: string;
  priority: number;
  is_active: boolean;
}

const DEFAULT_FORM: FormData = {
  title: '',
  question: '',
  answer_sql: '',
  explanation: '',
  related_tables: '',
  priority: 0,
  is_active: true,
};

export default function SqlPairs() {
  const [items, setItems] = useState<SqlPairItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editItem, setEditItem] = useState<SqlPairItem | null>(null);
  const [form, setForm] = useState<FormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SqlPairItem | null>(null);

  const PAGE_SIZE = 20;

  // ── Load items ───────────────────────────────────────────────────

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = {
        workspace_id: 0,
        knowledge_type: 'sql_pair',
        page,
        size: PAGE_SIZE,
      };
      if (searchText) params.search = searchText;
      const { data } = await client.get('/admin/knowledge', { params });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (e: any) {
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

  const openEdit = (item: SqlPairItem) => {
    setEditItem(item);
    const meta = item.metadata || {};
    setForm({
      title: item.title,
      question: meta.question || item.title,
      answer_sql: meta.answer_sql || item.content,
      explanation: meta.explanation || '',
      related_tables: item.related_tables || '',
      priority: item.priority,
      is_active: !!item.is_active,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.question || !form.answer_sql) {
      toast.error('请填写问题和 SQL 答案');
      return;
    }
    setSaving(true);
    try {
      const payload: any = {
        workspace_id: 0,
        knowledge_type: 'sql_pair',
        title: form.title || form.question,
        content: form.answer_sql,
        metadata: {
          question: form.question,
          answer_sql: form.answer_sql,
          explanation: form.explanation,
        },
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

  const handleToggle = async (item: SqlPairItem) => {
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
            <FileQuestion className="h-6 w-6" />
            SQL 示例对
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            配置问答 SQL 对作为 LLM 的 few-shot 参考，提升自然语言转 SQL 的准确率
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1" />新增 SQL 对
        </Button>
      </div>

      {/* Search */}
      <div className="mb-4">
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索 SQL 对..."
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
          <Database className="h-10 w-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">暂无 SQL 示例对</p>
          <p className="text-xs mt-1">添加问答对帮助 LLM 更准确地生成 SQL</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(item => {
            const meta = item.metadata || {};
            return (
              <div
                key={item.id}
                className="border rounded-lg p-4 hover:bg-muted/30 transition-colors"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2">
                      <Badge variant="outline" className="text-green-600 border-green-200 bg-green-50">
                        <FileQuestion className="h-3 w-3 mr-1" />SQL 对
                      </Badge>
                      <span className="font-medium">{meta.question || item.title}</span>
                      <Badge variant={item.is_active ? 'default' : 'outline'} className="text-xs">
                        {item.is_active ? '启用' : '禁用'}
                      </Badge>
                      {item.priority > 0 && (
                        <Badge variant="secondary" className="text-xs">优先级: {item.priority}</Badge>
                      )}
                    </div>
                    <div className="bg-muted/50 rounded p-2 mb-2">
                      <code className="text-xs font-mono text-foreground whitespace-pre-wrap break-all">
                        {meta.answer_sql || item.content}
                      </code>
                    </div>
                    {meta.explanation && (
                      <p className="text-xs text-muted-foreground">{meta.explanation}</p>
                    )}
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
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editItem ? '编辑 SQL 对' : '新增 SQL 对'}</DialogTitle>
            <DialogDescription>
              添加问答对作为 LLM 的 few-shot 参考，提升自然语言转 SQL 的准确率
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>问题 *</Label>
              <Input
                value={form.question}
                onChange={e => setForm(f => ({ ...f, question: e.target.value }))}
                placeholder="用户可能问的问题，如: 各区域销售额排名"
              />
            </div>
            <div>
              <Label>SQL 答案 *</Label>
              <Textarea
                value={form.answer_sql}
                onChange={e => setForm(f => ({ ...f, answer_sql: e.target.value }))}
                placeholder="正确的 SQL 查询"
                rows={5}
                className="font-mono text-sm"
              />
            </div>
            <div>
              <Label>标题（可选）</Label>
              <Input
                value={form.title}
                onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
                placeholder="简短描述（默认使用问题文本）"
              />
            </div>
            <div>
              <Label>解释（可选）</Label>
              <Textarea
                value={form.explanation}
                onChange={e => setForm(f => ({ ...f, explanation: e.target.value }))}
                placeholder="SQL 的解释说明"
                rows={2}
              />
            </div>
            <div>
              <Label>关联表名</Label>
              <Input
                value={form.related_tables}
                onChange={e => setForm(f => ({ ...f, related_tables: e.target.value }))}
                placeholder="逗号分隔，如: orders,users"
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
              <p className="text-xs text-muted-foreground mt-1">越高越优先被检索匹配</p>
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
              确定要删除这个 SQL 对吗？此操作不可撤销。
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
