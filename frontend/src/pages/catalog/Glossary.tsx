import { useState, useEffect, useCallback } from 'react';
import client from '@/api/client';
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
  BookOpen,
  ToggleLeft,
  ToggleRight,
} from 'lucide-react';

interface Term {
  id: number;
  term_cn: string;
  term_en: string;
  term_type: string;
  target_table: string;
  target_column: string;
  description: string;
  usage_count: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface TermFormData {
  term_cn: string;
  term_en: string;
  term_type: string;
  target_table: string;
  target_column: string;
  description: string;
}

const TERM_TYPES = [
  { value: 'dimension', label: '维度' },
  { value: 'measure', label: '度量' },
  { value: 'concept', label: '概念' },
  { value: 'abbreviation', label: '缩写' },
  { value: 'business', label: '业务术语' },
];

const TERM_TYPE_COLORS: Record<string, string> = {
  dimension: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  measure: 'bg-green-500/10 text-green-500 border-green-500/20',
  concept: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  abbreviation: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
  business: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20',
};

const emptyForm: TermFormData = {
  term_cn: '',
  term_en: '',
  term_type: 'business',
  target_table: '',
  target_column: '',
  description: '',
};

export default function Glossary() {
  const [terms, setTerms] = useState<Term[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  // Dialog state
  const [formOpen, setFormOpen] = useState(false);
  const [editTerm, setEditTerm] = useState<Term | null>(null);
  const [formData, setFormData] = useState<TermFormData>(emptyForm);
  const [saving, setSaving] = useState(false);

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Term | null>(null);

  const loadTerms = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = { page, size: 20 };
      if (search) params.search = search;
      const res = await client.get('/terms', { params });
      const items = res?.data?.items ?? res?.data;
      setTerms(Array.isArray(items) ? items : []);
      setTotal(res?.data?.total || 0);
    } catch {
      setTerms([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  useEffect(() => {
    loadTerms();
  }, [loadTerms]);

  const handleOpenCreate = () => {
    setEditTerm(null);
    setFormData(emptyForm);
    setFormOpen(true);
  };

  const handleOpenEdit = (term: Term) => {
    setEditTerm(term);
    setFormData({
      term_cn: term.term_cn,
      term_en: term.term_en,
      term_type: term.term_type || 'business',
      target_table: term.target_table || '',
      target_column: term.target_column || '',
      description: term.description || '',
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!formData.term_cn) {
      toast.error('请填写中文术语');
      return;
    }
    setSaving(true);
    try {
      if (editTerm) {
        await client.put(`/terms/${editTerm.id}`, formData);
        toast.success('术语已更新');
      } else {
        await client.post('/terms', formData);
        toast.success('术语已创建');
      }
      setFormOpen(false);
      loadTerms();
    } catch {
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/terms/${deleteTarget.id}`);
      toast.success('术语已删除');
      setDeleteTarget(null);
      loadTerms();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (term: Term) => {
    try {
      await client.put(`/terms/${term.id}/toggle`);
      toast.success(term.is_active ? '已禁用' : '已启用');
      loadTerms();
    } catch {
      toast.error('操作失败');
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">业务术语</h1>
          <p className="text-muted-foreground text-sm mt-1">管理业务术语定义，用于 NL2SQL 语义理解和 RAG 检索增强</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadTerms}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button size="sm" onClick={handleOpenCreate}>
            <Plus className="w-4 h-4 mr-1" />
            新建术语
          </Button>
        </div>
      </div>

      {/* Search */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="搜索术语名称..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="pl-9"
          />
        </div>
      </div>

      {/* Terms Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">中文术语</th>
              <th className="text-left p-3 font-medium">英文术语</th>
              <th className="text-left p-3 font-medium">类型</th>
              <th className="text-left p-3 font-medium">目标表</th>
              <th className="text-left p-3 font-medium">目标列</th>
              <th className="text-center p-3 font-medium">使用次数</th>
              <th className="text-center p-3 font-medium">状态</th>
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
            ) : terms.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-muted-foreground">
                  暂无术语数据
                </td>
              </tr>
            ) : (
              terms.map((term) => (
                <tr key={term.id} className="border-t hover:bg-muted/30">
                  <td className="p-3">
                    <div className="flex items-center gap-2">
                      <BookOpen className="w-4 h-4 text-muted-foreground" />
                      <span className="font-medium">{term.term_cn}</span>
                    </div>
                    {term.description && (
                      <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-[300px]">{term.description}</p>
                    )}
                  </td>
                  <td className="p-3 font-mono text-xs text-muted-foreground">{term.term_en || '-'}</td>
                  <td className="p-3">
                    <Badge variant="outline" className={TERM_TYPE_COLORS[term.term_type] || ''}>
                      {TERM_TYPES.find((t) => t.value === term.term_type)?.label || term.term_type}
                    </Badge>
                  </td>
                  <td className="p-3 font-mono text-xs text-muted-foreground">{term.target_table || '-'}</td>
                  <td className="p-3 font-mono text-xs text-muted-foreground">{term.target_column || '-'}</td>
                  <td className="p-3 text-center text-muted-foreground">{term.usage_count || 0}</td>
                  <td className="p-3 text-center">
                    <button
                      onClick={() => handleToggle(term)}
                      className="inline-flex items-center"
                      title={term.is_active ? '点击禁用' : '点击启用'}
                    >
                      {term.is_active ? (
                        <ToggleRight className="w-5 h-5 text-green-500" />
                      ) : (
                        <ToggleLeft className="w-5 h-5 text-gray-400" />
                      )}
                    </button>
                  </td>
                  <td className="p-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => handleOpenEdit(term)}>
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => setDeleteTarget(term)}>
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

      {/* Create/Edit Term Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editTerm ? '编辑术语' : '新建术语'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">中文术语 *</label>
                <Input
                  placeholder="如: 交易总额"
                  value={formData.term_cn}
                  onChange={(e) => setFormData({ ...formData, term_cn: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">英文术语</label>
                <Input
                  placeholder="如: GMV"
                  value={formData.term_en}
                  onChange={(e) => setFormData({ ...formData, term_en: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">术语类型</label>
              <Select value={formData.term_type} onValueChange={(v) => setFormData({ ...formData, term_type: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TERM_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">目标表</label>
                <Input
                  placeholder="关联的数据库表"
                  value={formData.target_table}
                  onChange={(e) => setFormData({ ...formData, target_table: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">目标列</label>
                <Input
                  placeholder="关联的字段"
                  value={formData.target_column}
                  onChange={(e) => setFormData({ ...formData, target_column: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">描述</label>
              <Input
                placeholder="术语的业务含义说明"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              />
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

      {/* Delete Confirm Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要删除术语 <strong>{deleteTarget?.term_cn}</strong> 吗？此操作不可恢复。
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
