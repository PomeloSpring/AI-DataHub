import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
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
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import {
  Plus, Edit, Trash2, BookOpen, MessageSquare, HelpCircle,
  FileQuestion, BarChart3, Search,
} from 'lucide-react';
import client from '@/api/client';

// ── Types ──────────────────────────────────────────────────────────

interface KnowledgeItem {
  id: number;
  workspace_id: number;
  datasource_id: number;
  knowledge_type: string;
  title: string;
  content: string;
  metadata: any;
  related_tables: string;
  priority: number;
  usage_count: number;
  positive_count: number;
  negative_count: number;
  is_active: number;
  created_at: string;
  updated_at: string;
}

interface KnowledgeStats {
  total: number;
  by_type: Array<{
    knowledge_type: string;
    cnt: number;
    total_usage: number;
    total_positive: number;
    total_negative: number;
  }>;
}

const TYPE_CONFIG: Record<string, { label: string; icon: any; color: string }> = {
  instruction: { label: '指令', icon: BookOpen, color: 'bg-blue-100 text-blue-700' },
  sql_pair: { label: 'SQL 对', icon: FileQuestion, color: 'bg-green-100 text-green-700' },
  recommend_question: { label: '推荐问题', icon: HelpCircle, color: 'bg-purple-100 text-purple-700' },
  followup_case: { label: '追问案例', icon: MessageSquare, color: 'bg-orange-100 text-orange-700' },
};

interface FormData {
  knowledge_type: string;
  title: string;
  content: string;
  related_tables: string;
  priority: number;
  metadata: any;
  is_active: boolean;
}

const DEFAULT_FORM: FormData = {
  knowledge_type: 'instruction',
  title: '',
  content: '',
  related_tables: '',
  priority: 0,
  metadata: null,
  is_active: true,
};

export default function KnowledgeManagement() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filterType, setFilterType] = useState<string>('');
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editItem, setEditItem] = useState<KnowledgeItem | null>(null);
  const [form, setForm] = useState<FormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeItem | null>(null);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);

  // SQL pair specific fields
  const [sqlPairQuestion, setSqlPairQuestion] = useState('');
  const [sqlPairAnswer, setSqlPairAnswer] = useState('');
  const [sqlPairExplanation, setSqlPairExplanation] = useState('');

  // ── Load items ───────────────────────────────────────────────────

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = { workspace_id: 0, page, size: 20 };
      if (filterType) params.knowledge_type = filterType;
      if (searchText) params.search = searchText;
      const { data } = await client.get('/admin/knowledge', { params });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error('加载知识列表失败');
    } finally {
      setLoading(false);
    }
  }, [page, filterType, searchText]);

  const loadStats = useCallback(async () => {
    try {
      const { data } = await client.get('/admin/knowledge/stats', { params: { workspace_id: 0 } });
      setStats(data);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => { loadItems(); }, [loadItems]);
  useEffect(() => { loadStats(); }, [loadStats]);

  // ── Form handling ────────────────────────────────────────────────

  const openCreate = (type?: string) => {
    setEditItem(null);
    setForm({ ...DEFAULT_FORM, knowledge_type: type || 'instruction' });
    setSqlPairQuestion('');
    setSqlPairAnswer('');
    setSqlPairExplanation('');
    setFormOpen(true);
  };

  const openEdit = (item: KnowledgeItem) => {
    setEditItem(item);
    setForm({
      knowledge_type: item.knowledge_type,
      title: item.title,
      content: item.content,
      related_tables: item.related_tables || '',
      priority: item.priority,
      metadata: item.metadata,
      is_active: !!item.is_active,
    });
    if (item.knowledge_type === 'sql_pair' && item.metadata) {
      const meta = typeof item.metadata === 'string' ? JSON.parse(item.metadata) : item.metadata;
      setSqlPairQuestion(meta.question || '');
      setSqlPairAnswer(meta.answer_sql || '');
      setSqlPairExplanation(meta.explanation || '');
    }
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.title || !form.content) {
      toast.error('请填写标题和内容');
      return;
    }
    setSaving(true);
    try {
      const payload: any = {
        ...form,
        workspace_id: 0,
        is_active: form.is_active ? 1 : 0,
      };

      // For SQL pairs, build metadata
      if (form.knowledge_type === 'sql_pair') {
        payload.metadata = {
          question: sqlPairQuestion || form.title,
          answer_sql: sqlPairAnswer || form.content,
          explanation: sqlPairExplanation,
        };
      }

      if (editItem) {
        await client.put(`/admin/knowledge/${editItem.id}`, payload);
        toast.success('知识已更新');
      } else {
        await client.post('/admin/knowledge', payload);
        toast.success('知识已创建');
      }
      setFormOpen(false);
      loadItems();
      loadStats();
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
      toast.success('知识已删除');
      setDeleteTarget(null);
      loadItems();
      loadStats();
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

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
        <BookOpen className="h-6 w-6" />
        知识管理
      </h1>

      <Tabs defaultValue="all">
        <TabsList>
          <TabsTrigger value="all" onClick={() => setFilterType('')}>
            全部 ({stats?.total || 0})
          </TabsTrigger>
          {Object.entries(TYPE_CONFIG).map(([type, config]) => {
            const count = stats?.by_type.find(b => b.knowledge_type === type)?.cnt || 0;
            return (
              <TabsTrigger key={type} value={type} onClick={() => setFilterType(type)}>
                <config.icon className="h-4 w-4 mr-1" />
                {config.label} ({count})
              </TabsTrigger>
            );
          })}
          <TabsTrigger value="stats">
            <BarChart3 className="h-4 w-4 mr-1" />
            统计
          </TabsTrigger>
        </TabsList>

        {/* ── List Tab ──────────────────────────────────────────── */}
        {['all', ...Object.keys(TYPE_CONFIG)].map(tabValue => (
          <TabsContent key={tabValue} value={tabValue}>
            <div className="flex justify-between items-center mb-4">
              <div className="flex items-center gap-2">
                <div className="relative">
                  <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="搜索知识..."
                    value={searchText}
                    onChange={e => setSearchText(e.target.value)}
                    className="pl-8 w-64"
                  />
                </div>
              </div>
              <div className="flex gap-2">
                {Object.entries(TYPE_CONFIG).map(([type, config]) => (
                  <Button
                    key={type}
                    variant="outline"
                    size="sm"
                    onClick={() => openCreate(type)}
                  >
                    <config.icon className="h-4 w-4 mr-1" />
                    新增{config.label}
                  </Button>
                ))}
              </div>
            </div>

            {loading ? (
              <div className="text-center py-8 text-muted-foreground">加载中...</div>
            ) : items.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                暂无知识条目，点击上方按钮创建
              </div>
            ) : (
              <div className="space-y-3">
                {items.map(item => {
                  const config = TYPE_CONFIG[item.knowledge_type] || TYPE_CONFIG.instruction;
                  const Icon = config.icon;
                  return (
                    <div
                      key={item.id}
                      className="border rounded-lg p-4 flex items-start justify-between"
                    >
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1">
                          <Badge className={config.color}>
                            <Icon className="h-3 w-3 mr-1" />
                            {config.label}
                          </Badge>
                          <span className="font-medium">{item.title}</span>
                          <Badge variant={item.is_active ? 'default' : 'outline'}>
                            {item.is_active ? '启用' : '禁用'}
                          </Badge>
                          {item.priority > 0 && (
                            <Badge variant="secondary">优先级: {item.priority}</Badge>
                          )}
                        </div>
                        <p className="text-sm text-muted-foreground line-clamp-2">
                          {item.content}
                        </p>
                        <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
                          {item.related_tables && <span>关联表: {item.related_tables}</span>}
                          <span>命中: {item.usage_count}</span>
                          <span>👍 {item.positive_count}</span>
                          <span>👎 {item.negative_count}</span>
                          <span>{item.updated_at}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 ml-4">
                        <Switch
                          checked={!!item.is_active}
                          onCheckedChange={() => handleToggle(item)}
                        />
                        <Button variant="outline" size="sm" onClick={() => openEdit(item)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => setDeleteTarget(item)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {total > 20 && (
              <div className="flex justify-center gap-2 mt-4">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                  上一页
                </Button>
                <span className="py-1 px-3 text-sm">{page} / {Math.ceil(total / 20)}</span>
                <Button variant="outline" size="sm" disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)}>
                  下一页
                </Button>
              </div>
            )}
          </TabsContent>
        ))}

        {/* ── Stats Tab ─────────────────────────────────────────── */}
        <TabsContent value="stats">
          {stats ? (
            <div className="grid grid-cols-2 gap-4">
              <div className="border rounded-lg p-4">
                <h3 className="font-medium mb-3">知识类型分布</h3>
                <div className="space-y-2">
                  {stats.by_type.map(bt => {
                    const config = TYPE_CONFIG[bt.knowledge_type] || TYPE_CONFIG.instruction;
                    return (
                      <div key={bt.knowledge_type} className="flex items-center justify-between">
                        <Badge className={config.color}>{config.label}</Badge>
                        <span className="font-mono">{bt.cnt} 条</span>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="border rounded-lg p-4">
                <h3 className="font-medium mb-3">使用统计</h3>
                <div className="space-y-2">
                  {stats.by_type.map(bt => {
                    const config = TYPE_CONFIG[bt.knowledge_type] || TYPE_CONFIG.instruction;
                    return (
                      <div key={bt.knowledge_type} className="flex items-center justify-between">
                        <Badge className={config.color}>{config.label}</Badge>
                        <span className="text-sm">
                          命中 {bt.total_usage} | 👍 {bt.total_positive} | 👎 {bt.total_negative}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">加载中...</div>
          )}
        </TabsContent>
      </Tabs>

      {/* ── Create/Edit Dialog ──────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editItem ? '编辑知识' : `新增${TYPE_CONFIG[form.knowledge_type]?.label || '知识'}`}
            </DialogTitle>
            <DialogDescription>
              {form.knowledge_type === 'instruction' && '添加指令让 LLM 遵循特定行为规则'}
              {form.knowledge_type === 'sql_pair' && '添加问答对作为 LLM 的 few-shot 参考'}
              {form.knowledge_type === 'recommend_question' && '添加推荐问题展示给用户'}
              {form.knowledge_type === 'followup_case' && '添加追问案例引导用户深入分析'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>知识类型</Label>
              <Select
                value={form.knowledge_type}
                onValueChange={v => setForm(f => ({ ...f, knowledge_type: v }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(TYPE_CONFIG).map(([type, config]) => (
                    <SelectItem key={type} value={type}>{config.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>标题 *</Label>
              <Input
                value={form.title}
                onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
                placeholder="简短描述"
              />
            </div>

            {form.knowledge_type === 'sql_pair' ? (
              <>
                <div>
                  <Label>问题</Label>
                  <Input
                    value={sqlPairQuestion}
                    onChange={e => setSqlPairQuestion(e.target.value)}
                    placeholder="用户可能问的问题"
                  />
                </div>
                <div>
                  <Label>SQL 答案 *</Label>
                  <Textarea
                    value={sqlPairAnswer}
                    onChange={e => setSqlPairAnswer(e.target.value)}
                    placeholder="正确的 SQL 查询"
                    rows={4}
                    className="font-mono text-sm"
                  />
                </div>
                <div>
                  <Label>解释</Label>
                  <Textarea
                    value={sqlPairExplanation}
                    onChange={e => setSqlPairExplanation(e.target.value)}
                    placeholder="SQL 的解释说明"
                    rows={2}
                  />
                </div>
              </>
            ) : (
              <div>
                <Label>内容 *</Label>
                <Textarea
                  value={form.content}
                  onChange={e => setForm(f => ({ ...f, content: e.target.value }))}
                  placeholder={
                    form.knowledge_type === 'instruction' ? '如: 所有金额单位为万元' :
                    form.knowledge_type === 'recommend_question' ? '如: 本月销售额是多少？' :
                    '输入内容...'
                  }
                  rows={4}
                />
              </div>
            )}

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
              <p className="text-xs text-muted-foreground mt-1">越高越优先被检索</p>
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
