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
import { t as tr } from '@/i18n/runtime';
import { useTranslation } from 'react-i18next';

const FORMAT_COLORS: Record<string, string> = {
  text: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
  markdown: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  html: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
};

/** 各分类的用法说明：配完看不出"在哪生效、有什么限制"就会重演"配了等于没配" */
const KIND_NOTES: Record<string, string> = {
  im: tr('k_b48ffae26a'),
  email: tr('k_f9f9b14637'),
  sms: tr('k_168c28bd7f'),
};

export default function MessageTemplatePanel({ kind }: { kind: string }) {
  const { t } = useTranslation('ui');
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
      toast.error(e?.response?.data?.detail || t('k_ce41be3c62'));
    } finally {
      setLoading(false);
    }
  }, [kind, includeInactive]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    // 变量清单与格式白名单的唯一来源：服务端加了变量，这里不用改前端
    getMessageTemplateMeta().then(setMeta).catch(() => toast.error(t('k_5bacd0478b')));
  }, []);

  const canEdit = (t: MessageTemplate) => isAdmin || !t.is_builtin;

  const openCreate = () => {
    setEditItem(null);
    setName('');
    setDescription('');
    setFormat(formats[0] || 'text');
    setSubject(kind === 'email' ? tr('k_65ada0b3b2') : '');
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
      toast.error(e?.response?.data?.detail || t('k_6a3a5ef00d'));
    } finally {
      setPreviewing(false);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) { toast.error(t('k_8f21b9868f')); return; }
    if (!content.trim()) { toast.error(t('k_8f76cb9810')); return; }
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
        toast.success(t('k_ea231340e0'));
      } else {
        await createMessageTemplate(payload);
        toast.success(t('k_05068da138'));
      }
      setFormOpen(false);
      load();
    } catch (e: any) {
      // 未登记变量 / 越级格式 / 邮件缺主题等原因都在 detail 里，必须原样端给用户
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteMessageTemplate(deleteTarget.id);
      toast.success(t('k_5cc232620c'));
      setDeleteTarget(null);
      load();
    } catch (e: any) {
      // 被渠道引用时服务端回渠道清单：不拦就会让那些渠道静默回默认文案
      toast.error(e?.response?.data?.detail || t('k_acf0664a54'));
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
            {t('k_8d09c1dddc')}
          </label>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />{t('k_38108eaa1d')}
          </Button>
          <Button size="sm" onClick={openCreate} data-testid="mt-create">
            <Plus className="h-4 w-4 mr-1" />{t('k_a38a511b8c')}
          </Button>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('k_bbc511d06d')}</TableHead>
            <TableHead>{t('k_9d2601c843')}</TableHead>
            {kind === 'email' && <TableHead>{t('k_546e8c5f09')}</TableHead>}
            <TableHead>{t('k_40b3886a8e')}</TableHead>
            <TableHead className="text-right">{t('k_72412e2290')}</TableHead>
            <TableHead>{t('k_62e951a692')}</TableHead>
            <TableHead>{t('k_093dea88c9')}</TableHead>
            <TableHead className="text-right">{t('k_f3ea6d345e')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={kind === 'email' ? 8 : 7}
                className="text-center py-8 text-muted-foreground">{t('k_26b5bd4947')}</TableCell>
            </TableRow>
          ) : items.length === 0 ? (
            <TableRow>
              <TableCell colSpan={kind === 'email' ? 8 : 7} className="text-center py-8 text-muted-foreground">
                {t('k_a9b5576140', { p0: KIND_LABELS[kind] || '' })}</TableCell>
            </TableRow>
          ) : items.map(t => (
            <TableRow key={t.id} className={!Number(t.is_active) ? 'opacity-60' : ''}>
              <TableCell>
                <div className="flex items-center gap-1.5 font-medium">
                  {t.name}
                  {!!t.is_builtin && <Lock className="h-3 w-3 text-muted-foreground" aria-label={tr('k_1cddfcf703')} />}
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
                    : <span className="text-xs text-muted-foreground">{tr('k_641232a814')}</span>}
                  {!!t.unregistered_variables?.length && (
                    // 发不出去的变量必须列得出来：发送时原样留字面量，不标出来就是"通知里凭空出现 {{xxx}}"
                    <Badge variant="outline" className="border-destructive/40 text-destructive gap-1"
                      title={tr('k_e62dd3c25a', { p0: t.unregistered_variables.join(', ') })}>
                      {tr('k_dff37067e4', { p0: t.unregistered_variables.length })}</Badge>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-right">
                {t.used_count
                  ? (
                    <Badge variant="outline" className="border-emerald-500/30 text-emerald-600"
                      title={t.used_by_channels.map(c => c.name).join('、')}>
                      {tr('k_b47714063e', { p0: t.used_count })}</Badge>
                  )
                  : <span className="text-muted-foreground text-xs">{tr('k_2c6270bcce')}</span>}
              </TableCell>
              <TableCell>
                <Badge variant="outline" className={Number(t.is_active) ? 'text-green-500' : 'text-gray-500'}>
                  {Number(t.is_active) ? tr('k_d4e9ca3dd4') : tr('k_b07628f2f1')}
                </Badge>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                {t.updated_at ? new Date(t.updated_at).toLocaleString() : '-'}
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  <Button variant="ghost" size="sm" disabled={!canEdit(t)} onClick={() => openEdit(t)}>
                    <Edit className="h-3.5 w-3.5 mr-1" />{canEdit(t) ? tr('k_a7f814c0a4') : tr('k_85541bd9f7')}
                  </Button>
                  <Button variant="ghost" size="sm" disabled={!canEdit(t)} onClick={() => setDeleteTarget(t)}
                    className="text-destructive" aria-label={tr('k_3755f56f2f')}>
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
            <DialogTitle>{editItem ? t('k_b3f4d08c98') : t('k_eac89791d6')}</DialogTitle>
            <DialogDescription>
              {t('k_8f9be3c65b', { p0: KIND_LABELS[kind] })}</DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>{t('k_bbc511d06d')}</Label>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder={t('k_6e00b2f65b')} />
            </div>
            <div className="space-y-1.5">
              <Label>{t('k_19b61b1c63')}</Label>
              <Select value={format} onValueChange={setFormat}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {formats.map(f => <SelectItem key={f} value={f}>{FORMAT_LABELS[f] || f}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                {t('k_776304f3a0')}
              </p>
            </div>
          </div>

          {kind === 'email' && (
            <div className="space-y-1.5">
              <Label>{t('k_546e8c5f09')}</Label>
              <Input value={subject} onChange={e => setSubject(e.target.value)}
                placeholder={t('k_65ada0b3b2')} />
            </div>
          )}

          <div className="space-y-1.5">
            <Label>{t('k_599531b7a0')}</Label>
            <div className="flex flex-wrap gap-1 text-xs">
              {(meta?.variables || []).map(v => (
                <button key={v.name} type="button" className="var-code" title={t('k_42c2061b78', { p0: v.sample })}
                  onClick={() => insertVariable(v.name)}>
                  {`{{${v.name}}}`} · {v.label}
                </button>
              ))}
            </div>
            {!meta && <p className="text-xs text-muted-foreground">{t('k_b989258d35')}</p>}
          </div>

          <div className="space-y-1.5">
            <Label>{t('k_7a2d6f18c1')}</Label>
            <Textarea id={bodyFieldId} value={content} onChange={e => setContent(e.target.value)}
              rows={7} className="font-mono text-sm"
              placeholder={t('k_21bb51fd5c')} />
            {kind === 'sms' && meta && (
              <p className="text-[11px] text-muted-foreground">
                {t('k_7c0c0a4e58', { p0: meta.sms_segment_chars })}</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>{t('k_412f54dc38')}</Label>
              <Input value={description} onChange={e => setDescription(e.target.value)} placeholder={t('k_abd27398a2')} />
            </div>
            {editItem && (
              <div className="flex items-end gap-2 pb-1.5">
                <Switch checked={isActive} onCheckedChange={setIsActive} />
                <Label>{t('k_40c49f46c2')}</Label>
              </div>
            )}
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>{t('k_67d03ddd6b')}</Label>
              <Button variant="outline" size="sm" className="h-8 text-xs" onClick={showPreview} disabled={previewing}>
                {previewing ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Eye className="h-3.5 w-3.5 mr-1" />}
                {tr('k_a7f9a6d4fc')}{' '}</Button>
            </div>
            {preview?.unknown_variables?.length ? (
              <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                {t('k_9b3aa948f2', { p0: preview.unknown_variables.join('、') })}</p>
            ) : null}
            <div className="border rounded-lg p-3 bg-card min-h-[80px]">
              {preview ? (
                <div className="space-y-2">
                  {kind === 'email' && <div className="text-sm font-medium">{t('k_6ea6ffb1fc', { p0: preview.subject })}</div>}
                  {format === 'markdown' || format === 'html'
                    ? <ReportContent content={preview.content} format={format === 'html' ? 'html' : 'markdown'} />
                    : <pre className="text-xs whitespace-pre-wrap font-mono">{preview.content}</pre>}
                  <p className="text-[11px] text-muted-foreground">
                    {t('k_c77a4cc432', { p0: preview.char_count, p1: preview.note })}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{t('k_9479cda02f')}</p>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t('k_4d0b4688c7')}</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />{t('k_6644f06197')}</> : editItem ? t('k_d9db02d07a') : t('k_fcbd093292')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('k_cdbfffdc5d')}</DialogTitle><DialogDescription>
            {t('k_8f68aa7526')}
          </DialogDescription></DialogHeader>
          <p className="text-sm text-muted-foreground">{t('k_5ff404ea8b', { p0: deleteTarget?.name })}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t('k_4d0b4688c7')}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t('k_3c06abe116')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
