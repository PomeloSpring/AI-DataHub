import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import {
  Plus,
  Edit,
  Trash2,
  RefreshCw,
  FileText,
  Code,
  Eye,
  Lock,
} from 'lucide-react';
import client from '@/api/client';

interface ReportTemplate {
  id: number;
  name: string;
  description: string;
  content: string;
  format: 'markdown' | 'html';
  is_system: boolean;
  workspace_id: number;
  owner_id: number;
  created_at: string;
  updated_at: string;
}

const FORMAT_MAP: Record<string, { label: string; color: string }> = {
  markdown: { label: 'Markdown', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  html: { label: 'HTML', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
};

export default function ReportTemplates() {
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editTpl, setEditTpl] = useState<ReportTemplate | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ReportTemplate | null>(null);

  // Form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [content, setContent] = useState('');
  const [format, setFormat] = useState<'markdown' | 'html'>('markdown');
  const [saving, setSaving] = useState(false);
  const [previewTab, setPreviewTab] = useState<'edit' | 'preview'>('edit');

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    try {
      // 系统管理页面：不按 workspace 过滤
      const { data } = await client.get('/report-templates');
      setTemplates(data || []);
    } catch {
      toast.error('加载模板失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  const openCreate = () => {
    setEditTpl(null);
    setName('');
    setDescription('');
    setContent(DEFAULT_TEMPLATE_CONTENT);
    setFormat('markdown');
    setFormOpen(true);
    setPreviewTab('edit');
  };

  const openEdit = (tpl: ReportTemplate) => {
    setEditTpl(tpl);
    setName(tpl.name);
    setDescription(tpl.description || '');
    setContent(tpl.content);
    setFormat(tpl.format);
    setFormOpen(true);
    setPreviewTab('edit');
  };

  const handleSave = async () => {
    if (!name.trim()) { toast.error('请输入模板名称'); return; }
    if (!content.trim()) { toast.error('请输入模板内容'); return; }

    setSaving(true);
    try {
      const req = {
        name: name.trim(),
        description: description.trim(),
        content,
        format,
        workspace_id: 0,
      };
      if (editTpl) {
        await client.put(`/report-templates/${editTpl.id}`, req);
        toast.success('更新成功');
      } else {
        await client.post('/report-templates', req);
        toast.success('创建成功');
      }
      setFormOpen(false);
      loadTemplates();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/report-templates/${deleteTarget.id}`);
      toast.success('已删除');
      setDeleteTarget(null);
      loadTemplates();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const renderPreview = () => {
    if (!content.trim()) return <div className="text-muted-foreground p-4">内容为空</div>;
    if (format === 'html') {
      return (
        <iframe
          srcDoc={content
            .replace(/\{\{.*?\}\}/g, m => {
              // Simple variable preview
              const varMap: Record<string, string> = {
                '{{ date }}': '2026-07-01',
                '{{ timestamp }}': '2026-07-01 10:00:00',
                '{{ task_name }}': '示例任务',
              };
              return varMap[m] || m;
            })
            .replace(/\{%.*?%\}/g, '') // Hide Jinja blocks for preview
          }
          className="w-full h-[400px] border rounded bg-white"
          sandbox=""
        />
      );
    }
    // Markdown: show raw content with variable hints highlighted
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
          <h1 className="text-2xl font-bold">报告模板</h1>
          <p className="text-muted-foreground text-sm mt-1">
            配置定时任务的报告输出模板，支持 Markdown 和 HTML 格式
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadTemplates}>
            <RefreshCw className="w-4 h-4 mr-1" />刷新
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />新建模板
          </Button>
        </div>
      </div>

      {/* Variable reference */}
      <div className="border rounded-lg p-3 bg-muted/30 text-sm">
        <span className="font-medium">可用变量：</span>
        <code className="mx-1 px-1 bg-muted rounded">{'{{ date }}'}</code>
        <code className="mx-1 px-1 bg-muted rounded">{'{{ timestamp }}'}</code>
        <code className="mx-1 px-1 bg-muted rounded">{'{{ task_name }}'}</code>
        <code className="mx-1 px-1 bg-muted rounded">{'{{ results }}'}</code>
        <code className="mx-1 px-1 bg-muted rounded">{'{{ succeeded }}'}</code>
        <code className="mx-1 px-1 bg-muted rounded">{'{{ failed }}'}</code>
        <span className="text-muted-foreground ml-2">（Jinja2 语法）</span>
      </div>

      {/* Template List */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? (
          <div className="col-span-full text-center p-8 text-muted-foreground">加载中...</div>
        ) : templates.length === 0 ? (
          <div className="col-span-full text-center p-8 text-muted-foreground">暂无模板</div>
        ) : (
          templates.map(tpl => (
            <div key={tpl.id} className="border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {tpl.format === 'html' ? <Code className="w-4 h-4 text-orange-500" /> : <FileText className="w-4 h-4 text-blue-500" />}
                  <span className="font-medium">{tpl.name}</span>
                </div>
                <div className="flex items-center gap-1">
                  <Badge variant="outline" className={FORMAT_MAP[tpl.format]?.color}>
                    {FORMAT_MAP[tpl.format]?.label}
                  </Badge>
                  {tpl.is_system && (
                    <Badge variant="outline" className="bg-gray-500/10 text-gray-500 border-gray-500/20">
                      <Lock className="w-3 h-3 mr-0.5" />内置
                    </Badge>
                  )}
                </div>
              </div>
              {tpl.description && (
                <p className="text-xs text-muted-foreground">{tpl.description}</p>
              )}
              <pre className="text-xs text-muted-foreground bg-muted/50 rounded p-2 max-h-[60px] overflow-hidden">
                {tpl.content.slice(0, 100)}...
              </pre>
              <div className="flex justify-end gap-1">
                <Button variant="ghost" size="sm" onClick={() => openEdit(tpl)}>
                  <Edit className="w-4 h-4" />
                </Button>
                {!tpl.is_system && (
                  <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(tpl)}>
                    <Trash2 className="w-4 h-4 text-destructive" />
                  </Button>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editTpl ? '编辑模板' : '新建模板'}</DialogTitle>
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
              <TabsTrigger value="edit">
                <Code className="w-4 h-4 mr-1" />编辑
              </TabsTrigger>
              <TabsTrigger value="preview">
                <Eye className="w-4 h-4 mr-1" />预览
              </TabsTrigger>
            </TabsList>
            <TabsContent value="edit">
              <Textarea
                value={content}
                onChange={e => setContent(e.target.value)}
                className="font-mono text-sm min-h-[400px]"
                placeholder={format === 'html' ? '<div>...</div>' : '# 标题\n\n内容...'}
              />
            </TabsContent>
            <TabsContent value="preview">
              {renderPreview()}
            </TabsContent>
          </Tabs>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : editTpl ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>确定要删除模板「{deleteTarget?.name}」吗？</DialogDescription>
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
