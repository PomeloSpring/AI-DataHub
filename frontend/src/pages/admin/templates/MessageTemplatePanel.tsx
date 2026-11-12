// 模板中心 · 消息模板（IM / 邮件 / 短信），通知渠道按 code 引用这里的一条。
//
// 为什么单独建这一份而不写在渠道里：旧形态是渠道 config.message_template 手填一段文本，
// 而发送侧根本没读它（全平台死字段），渠道里改十遍也发不出去。现在正文唯一来源是这里的模板，
// 渠道只保存引用（message_template_key），改文案一处生效、谁在用看得见。
//
// 可用变量必须来自服务端 `/message-templates/variables`：旧 UI 自己硬写过 9 个变量，
// 其中 task_name / result_summary 属于业务内容，按安全护栏不允许进通知 —— 页面广告了却发不出去。
import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import {
  Plus, Edit, Trash2, RefreshCw, Lock, Loader2, Eye,
} from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import ReportContent from '@/components/ReportContent';
import {
  createMessageTemplate, deleteMessageTemplate, getMessageTemplateMeta, listMessageTemplates,
  previewMessageTemplate, updateMessageTemplate,
  FORMAT_LABELS, KIND_LABELS,
  type MessageTemplate, type MessageTemplateMeta, type MessageTemplatePreview,
} from '@/api/messageTemplate';

const FORMAT_COLORS: Record<string, string> = {
  text: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
  markdown: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  html: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
};

/** 各分类的用法说明：配完看不出"在哪生效、有什么限制"就会重演"配了等于没配" */
const KIND_NOTES: Record<string, string> = {
  im: '钉钉/飞书/企业微信机器人与通用 Webhook 用这份文案。留空引用（渠道不选模板）时按平台默认文案发送。',
  email: '邮件通道用这份文案：主题必填（没有主题的邮件在客户端里就是空邮件），正文可选 HTML 直接当邮件体发送。',
  sms: '短信本期为通道占位（未接服务商）：模板可维护，真实发送会显式报「服务商未配置」。国内短信正文须走运营商已审核模板，平台文案是镜像，改这里不会改运营商侧模板。',
};

export default function MessageTemplatePanel({ kind }: { kind: string }) {
  const isAdmin = useAuthStore(s => s.user?.role) === 'admin';
  const [meta, setMeta] = useState<MessageTemplateMeta | null>(null);
  const [items, setItems] = useState<MessageTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editItem, setEditItem] = useState<MessageTemplate | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MessageTemplate | null>(null);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [format, setFormat] = useState('text');
  const [subject, setSubject] = useState('');
  const [content, setContent] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<MessageTemplatePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const formats = meta?.formats?.[kind] || ['text'];
  const bodyFieldId = `mt-body-${kind}`;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listMessageTemplates(kind, includeInactive));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '加载消息模板失败');
    } finally {
      setLoading(false);
    }
  }, [kind, includeInactive]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    // 变量清单与格式白名单的唯一来源：服务端加了变量，这里不用改前端
    getMessageTemplateMeta().then(setMeta).catch(() => toast.error('加载可用变量失败'));
  }, []);

  const canEdit = (t: MessageTemplate) => isAdmin || !t.is_builtin;

  const openCreate = () => {
    setEditItem(null);
    setName('');
    setDescription('');
    setFormat(formats[0] || 'text');
    setSubject(kind === 'email' ? '分析报告已生成（{{date}}）' : '');
    setContent('');
    setIsActive(true);
    setPreview(null);
    setFormOpen(true);
  };

  const openEdit = (t: MessageTemplate) => {
    setEditItem(t);
    setName(t.name);
    setDescription(t.description || '');
    setFormat(t.format);
    setSubject(t.subject || '');
    setContent(t.content || '');
    setIsActive(!!Number(t.is_active));
    setPreview(null);
    setFormOpen(true);
  };

  /** 点变量插到光标处：插入的是 `{{name}}`，与保存/渲染同一字面口径 */
  const insertVariable = (varName: string) => {
    const el = document.getElementById(bodyFieldId) as HTMLTextAreaElement | null;
    const token = `{{${varName}}}`;
    if (!el) { setContent(p => p + token); return; }
    const start = el.selectionStart;
    const end = el.selectionEnd;
    setContent(p => p.slice(0, start) + token + p.slice(end));
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = start + token.length;
    });
  };

  const showPreview = async () => {
    setPreviewing(true);
    try {
      // 走服务端 preview：与真实发送同一个 render 函数，预览什么样发出去就什么样
      setPreview(await previewMessageTemplate({
        template_kind: kind, format, subject, content,
      }));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '预览失败');
    } finally {
      setPreviewing(false);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) { toast.error('请输入模板名称'); return; }
    if (!content.trim()) { toast.error('请输入模板正文'); return; }
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        template_kind: kind,
        format,
        subject: kind === 'email' ? subject.trim() : '',
        content,
        description: description.trim(),
      };
      if (editItem) {
        const out = await updateMessageTemplate(editItem.id, { ...payload, is_active: isActive ? 1 : 0 });
        // 短信超长这类是"能保存但会影响计费"的事实：显式说出来，不静默通过
        if (out?.warnings?.length) toast.warning(out.warnings.join('；'));
        toast.success('模板已更新，引用它的渠道下次发送即生效');
      } else {
        await createMessageTemplate(payload);
        toast.success('模板已创建：可在通知渠道里选到它');
      }
      setFormOpen(false);
      load();
    } catch (e: any) {
      // 未登记变量 / 越级格式 / 邮件缺主题等原因都在 detail 里，必须原样端给用户
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteMessageTemplate(deleteTarget.id);
      toast.success('已删除');
      setDeleteTarget(null);
      load();
    } catch (e: any) {
      // 被渠道引用时服务端回渠道清单：不拦就会让那些渠道静默回默认文案
      toast.error(e?.response?.data?.detail || '删除失败');
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground max-w-3xl">
          {KIND_NOTES[kind]}
          {meta?.excluded_note && <span className="ml-1 text-amber-600">{meta.excluded_note}</span>}
        </p>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <Switch checked={includeInactive} onCheckedChange={setIncludeInactive} />
            显示已下架
          </label>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />刷新
          </Button>
          <Button size="sm" onClick={openCreate} data-testid="mt-create">
            <Plus className="h-4 w-4 mr-1" />新建模板
          </Button>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>模板名称</TableHead>
            <TableHead>格式</TableHead>
            {kind === 'email' && <TableHead>邮件主题</TableHead>}
            <TableHead>用到变量</TableHead>
            <TableHead className="text-right">被渠道引用</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>更新时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={kind === 'email' ? 8 : 7}
                className="text-center py-8 text-muted-foreground">加载中...</TableCell>
            </TableRow>
          ) : items.length === 0 ? (
            <TableRow>
              <TableCell colSpan={kind === 'email' ? 8 : 7} className="text-center py-8 text-muted-foreground">
                暂无{KIND_LABELS[kind] || ''}模板：点右上「新建」，或到通知渠道把现有渠道改选这里的模板
              </TableCell>
            </TableRow>
          ) : items.map(t => (
            <TableRow key={t.id} className={!Number(t.is_active) ? 'opacity-60' : ''}>
              <TableCell>
                <div className="flex items-center gap-1.5 font-medium">
                  {t.name}
                  {!!t.is_builtin && <Lock className="h-3 w-3 text-muted-foreground" aria-label="内置模板" />}
                </div>
                {t.description && (
                  <div className="text-xs text-muted-foreground max-w-[360px] truncate">{t.description}</div>
                )}
              </TableCell>
              <TableCell>
                <Badge variant="outline" className={FORMAT_COLORS[t.format] || FORMAT_COLORS.text}>
                  {FORMAT_LABELS[t.format] || t.format}
                </Badge>
              </TableCell>
              {kind === 'email' && (
                <TableCell className="text-xs max-w-[240px] truncate" title={t.subject}>{t.subject || '-'}</TableCell>
              )}
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {t.variables?.length
                    ? t.variables.map(v => <code key={v} className="var-code">{`{{${v}}}`}</code>)
                    : <span className="text-xs text-muted-foreground">无变量（固定文案）</span>}
                  {!!t.unregistered_variables?.length && (
                    // 发不出去的变量必须列得出来：发送时原样留字面量，不标出来就是"通知里凭空出现 {{xxx}}"
                    <Badge variant="outline" className="border-destructive/40 text-destructive gap-1"
                      title={`这些变量发送时不会展开：${t.unregistered_variables.join(', ')}`}>
                      {t.unregistered_variables.length} 个用不了
                    </Badge>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-right">
                {t.used_count
                  ? (
                    <Badge variant="outline" className="border-emerald-500/30 text-emerald-600"
                      title={t.used_by_channels.map(c => c.name).join('、')}>
                      {t.used_count} 个渠道
                    </Badge>
                  )
                  : <span className="text-muted-foreground text-xs">未引用</span>}
              </TableCell>
              <TableCell>
                <Badge variant="outline" className={Number(t.is_active) ? 'text-green-500' : 'text-gray-500'}>
                  {Number(t.is_active) ? '启用' : '已下架'}
                </Badge>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                {t.updated_at ? new Date(t.updated_at).toLocaleString() : '-'}
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  <Button variant="ghost" size="sm" disabled={!canEdit(t)} onClick={() => openEdit(t)}>
                    <Edit className="h-3.5 w-3.5 mr-1" />{canEdit(t) ? '编辑' : '只读'}
                  </Button>
                  <Button variant="ghost" size="sm" disabled={!canEdit(t)} onClick={() => setDeleteTarget(t)}
                    className="text-destructive" aria-label="删除">
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-3xl max-h-[92vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editItem ? '编辑消息模板' : '新建消息模板'}</DialogTitle>
            <DialogDescription>
              分类：{KIND_LABELS[kind]} · 通知渠道选中后每次发送按当轮执行结果展开；模板里不保存任何数值。
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>模板名称</Label>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder="如：任务完成通知（运维群）" />
            </div>
            <div className="space-y-1.5">
              <Label>消息格式</Label>
              <Select value={format} onValueChange={setFormat}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {formats.map(f => <SelectItem key={f} value={f}>{FORMAT_LABELS[f] || f}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                可选格式由服务端按分类给出（短信只能纯文本；邮件可 HTML）。
              </p>
            </div>
          </div>

          {kind === 'email' && (
            <div className="space-y-1.5">
              <Label>邮件主题</Label>
              <Input value={subject} onChange={e => setSubject(e.target.value)}
                placeholder="分析报告已生成（{{date}}）" />
            </div>
          )}

          <div className="space-y-1.5">
            <Label>可用变量</Label>
            <div className="flex flex-wrap gap-1 text-xs">
              {(meta?.variables || []).map(v => (
                <button key={v.name} type="button" className="var-code" title={`示例值：${v.sample}`}
                  onClick={() => insertVariable(v.name)}>
                  {`{{${v.name}}}`} · {v.label}
                </button>
              ))}
            </div>
            {!meta && <p className="text-xs text-muted-foreground">变量清单加载中…</p>}
          </div>

          <div className="space-y-1.5">
            <Label>模板正文</Label>
            <Textarea id={bodyFieldId} value={content} onChange={e => setContent(e.target.value)}
              rows={7} className="font-mono text-sm"
              placeholder={'📅 {{date}} {{time}}\n状态：{{status}}（成功 {{succeeded}} / 共 {{total}}，失败 {{failed}}）\n🔗 查看完整报告：{{report_link}}'} />
            {kind === 'sms' && meta && (
              <p className="text-[11px] text-muted-foreground">
                短信单条约 {meta.sms_segment_chars} 字；保存时按渲染后字数超长会提示多条计费。
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>描述</Label>
              <Input value={description} onChange={e => setDescription(e.target.value)} placeholder="适用场景说明（可选）" />
            </div>
            {editItem && (
              <div className="flex items-end gap-2 pb-1.5">
                <Switch checked={isActive} onCheckedChange={setIsActive} />
                <Label>启用（下架后渠道选不到，已引用的渠道按默认文案发送并显式告警）</Label>
              </div>
            )}
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>效果预览</Label>
              <Button variant="outline" size="sm" className="h-8 text-xs" onClick={showPreview} disabled={previewing}>
                {previewing ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Eye className="h-3.5 w-3.5 mr-1" />}
                按示例值预览
              </Button>
            </div>
            {preview?.unknown_variables?.length ? (
              <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                这些变量发不出去，收件人会看到字面量：{preview.unknown_variables.join('、')}。
                保存会被服务端挡住 —— 请改用上方列出的变量。
              </p>
            ) : null}
            <div className="border rounded-lg p-3 bg-card min-h-[80px]">
              {preview ? (
                <div className="space-y-2">
                  {kind === 'email' && <div className="text-sm font-medium">主题：{preview.subject}</div>}
                  {format === 'markdown' || format === 'html'
                    ? <ReportContent content={preview.content} format={format === 'html' ? 'html' : 'markdown'} />
                    : <pre className="text-xs whitespace-pre-wrap font-mono">{preview.content}</pre>}
                  <p className="text-[11px] text-muted-foreground">
                    正文 {preview.char_count} 字 · {preview.note}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">点「按示例值预览」看展开后的文案（与真实发送同一个渲染函数）。</p>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />保存中…</> : editItem ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>删除消息模板?</DialogTitle><DialogDescription>
            被通知渠道引用的模板会被服务端挡住并给出渠道清单 —— 请先到通知渠道改选其它模板，或把本模板下架（保留引用可追溯）。
          </DialogDescription></DialogHeader>
          <p className="text-sm text-muted-foreground">将删除「{deleteTarget?.name}」。</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>确认删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
