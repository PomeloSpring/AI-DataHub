import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import {
  Plus, Edit, Trash2, RefreshCw, FileText, Code, Eye, Lock,
} from 'lucide-react';
import client from '@/api/client';
import { useAuthStore } from '@/stores/authStore';
import type { VisComponent } from '@/api/visLibrary';
import VisComponentPreview from '@/components/VisComponentPreview';

const FORMAT_MAP: Record<string, { label: string; color: string }> = {
  markdown: { label: 'Markdown', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  html: { label: 'HTML', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
};

const DEFAULT_TEMPLATE_CONTENT = `# {{ date }} 数据日报 — {{ task_name }}

## 执行结果

{% for r in succeeded %}
### ✅ {{ r.title }}
- 数据量：{{ r.get('row_count', 'N/A') }} 行
{% endfor %}

{% for r in failed %}
### ❌ {{ r.title }}
- 错误：{{ r.get('error', 'Unknown') }}
{% endfor %}

## 统计
- 总任务：{{ results | length }}
- 成功：{{ succeeded | length }}
- 失败：{{ failed | length }}

---
*由 AI-DataHub 自动生成 | {{ timestamp }}*`;

export default function ReportTemplates() {
  const isAdmin = useAuthStore(s => s.user?.role) === 'admin';
  const [items, setItems] = useState<VisComponent[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editItem, setEditItem] = useState<VisComponent | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<VisComponent | null>(null);

  // Form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [content, setContent] = useState('');
  const [format, setFormat] = useState<'markdown' | 'html'>('markdown');
  const [saving, setSaving] = useState(false);
  const [previewTab, setPreviewTab] = useState<'edit' | 'preview'>('edit');

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/vis-library/components', { params: { category: 'report_template' } });
      setItems(Array.isArray(data) ? data.filter((c: any) => c.category === 'report_template') : []);
    } catch {
      toast.error('加载模板失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const canEdit = (c: VisComponent) => isAdmin || !c.is_builtin;

  const openCreate = () => {
    setEditItem(null);
    setName('');
    setDescription('');
    setContent(DEFAULT_TEMPLATE_CONTENT);
    setFormat('markdown');
    setFormOpen(true);
    setPreviewTab('edit');
  };

  const openEdit = (c: VisComponent) => {
    setEditItem(c);
    setName(c.name);
    setDescription(c.description || '');
    setContent((c.style_config as any)?.content || '');
    setFormat((c.style_config as any)?.format || 'markdown');
    setFormOpen(true);
    setPreviewTab('edit');
  };

  const handleSave = async () => {
    if (!name.trim()) { toast.error('请输入模板名称'); return; }
    if (!content.trim()) { toast.error('请输入模板内容'); return; }

    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        category: 'report_template' as const,
        style_config: { content, format },
        description: description.trim(),
        is_builtin: false,
        source: 'custom',
      };
      if (editItem) {
        await client.put(`/vis-library/components/${editItem.id}`, payload);
        toast.success('更新成功');
      } else {
        await client.post('/vis-library/components', payload);
        toast.success('创建成功');
      }
      setFormOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/vis-library/components/${deleteTarget.id}`);
      toast.success('已下架');
      setDeleteTarget(null);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const renderPreview = () => {
    if (!content.trim()) return <div className="text-muted-foreground p-4">内容为空</div>;
    if (format === 'html') {
      // 替换变量：支持 {{ var }} 和 {var} 两种格式
      const varMap: Record<string, string> = {
        'date': '2026-07-01',
        'timestamp': '2026-07-01 10:00:00',
        'task_name': '示例任务',
        'results': '[]',
        'succeeded': '[]',
        'failed': '[]',
      };
      const previewContent = content
        // 先替换双花括号 {{ var }}
        .replace(/\{\{\s*(\w+)\s*\}\}/g, (_, name) => varMap[name] || `{{ ${name} }}`)
        // 再替换单花括号 {var}（排除 HTML 标签和 Jinja 控制结构）
        .replace(/\{(\w+)\}/g, (_, name) => varMap[name] || `{${name}}`)
        // 移除 Jinja 控制结构块
        .replace(/\{%.*?%\}/g, '');
      return (
        <iframe
          srcDoc={previewContent}
          className="w-full h-[400px] border rounded bg-white"
          sandbox=""
        />
      );
    }
    return (
      <pre className="p-4 text-sm whitespace-pre-wrap bg-muted rounded-md overflow-auto max-h-[400px]">
        {content}
      </pre>
    );
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2"><FileText className="h-5 w-5" /> 报告模板</h1>
          <p className="text-sm text-muted-foreground mt-1">配置定时任务的报告输出模板，支持 Markdown 和 HTML 格式</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />刷新</Button>
          <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4 mr-1" />新建模板</Button>
        </div>
      </div>

      {/* Variable reference */}
      <div className="border rounded-lg p-3 bg-muted/30 text-sm">
        <span className="font-medium">可用变量：</span>
        <code className="mx-1 px-1.5 py-0.5 rounded bg-muted text-xs font-mono">{'{{ date }}'}</code>
        <code className="mx-1 px-1.5 py-0.5 rounded bg-muted text-xs font-mono">{'{{ timestamp }}'}</code>
        <code className="mx-1 px-1.5 py-0.5 rounded bg-muted text-xs font-mono">{'{{ task_name }}'}</code>
        <code className="mx-1 px-1.5 py-0.5 rounded bg-muted text-xs font-mono">{'{{ results }}'}</code>
        <code className="mx-1 px-1.5 py-0.5 rounded bg-muted text-xs font-mono">{'{{ succeeded }}'}</code>
        <code className="mx-1 px-1.5 py-0.5 rounded bg-muted text-xs font-mono">{'{{ failed }}'}</code>
        <span className="text-muted-foreground ml-2">（Jinja2 语法）</span>
      </div>

      {/* Template List - 使用字模库卡片布局 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {loading ? (
          <div className="col-span-full text-center p-8 text-muted-foreground">加载中...</div>
        ) : items.length === 0 ? (
          <div className="col-span-full text-center p-8 text-muted-foreground">暂无模板</div>
        ) : (
          items.map(c => (
            <div key={c.id} className={`rounded-lg border p-3 space-y-2 ${c.is_active ? '' : 'opacity-50'}`}>
              <VisComponentPreview c={c} />
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium truncate flex items-center gap-1.5">
                    {c.name}
                    {!!c.is_builtin && <Lock className="h-3 w-3 text-muted-foreground" />}
                  </div>
                  <div className="text-xs text-muted-foreground truncate">{c.code}</div>
                </div>
                <Badge variant="outline" className={FORMAT_MAP[(c.style_config as any)?.format]?.color || FORMAT_MAP.markdown.color}>
                  {FORMAT_MAP[(c.style_config as any)?.format]?.label || 'Markdown'}
                </Badge>
              </div>
              {c.description && <p className="text-xs text-muted-foreground line-clamp-2">{c.description}</p>}
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Button variant="outline" size="sm" disabled={!canEdit(c)} onClick={() => openEdit(c)}>
                  <Edit className="h-3.5 w-3.5 mr-1" />{canEdit(c) ? '编辑' : '只读'}
                </Button>
                <Button variant="ghost" size="sm" disabled={!canEdit(c)} onClick={() => setDeleteTarget(c)} className="text-destructive">
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editItem ? '编辑模板' : '新建模板'}</DialogTitle>
            <DialogDescription>
              使用 Jinja2 语法编写报告模板，支持 {'{{ 变量 }}'} 和 {'{% 控制结构 %}'}
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>模板名称</Label>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder="如：日报模板" />
            </div>
            <div className="space-y-1.5">
              <Label>输出格式</Label>
              <Select value={format} onValueChange={(v: any) => setFormat(v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="markdown">Markdown</SelectItem>
                  <SelectItem value="html">HTML</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>描述</Label>
            <Input value={description} onChange={e => setDescription(e.target.value)} placeholder="模板用途说明（可选）" />
          </div>

          <Tabs value={previewTab} onValueChange={(v: any) => setPreviewTab(v)}>
            <TabsList>
              <TabsTrigger value="edit"><Code className="w-4 h-4 mr-1" />编辑</TabsTrigger>
              <TabsTrigger value="preview"><Eye className="w-4 h-4 mr-1" />预览</TabsTrigger>
            </TabsList>
            <TabsContent value="edit">
              <Textarea
                value={content}
                onChange={e => setContent(e.target.value)}
                className="font-mono text-sm min-h-[400px]"
                placeholder={format === 'html' ? '<div>...</div>' : '# 标题\n\n内容...'}
              />
            </TabsContent>
            <TabsContent value="preview">{renderPreview()}</TabsContent>
          </Tabs>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>{saving ? '保存中...' : editItem ? '更新' : '创建'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>下架模板?</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">将「{deleteTarget?.name}」下架(软删除)。</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>下架</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
