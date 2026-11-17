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
import { toast } from '@/lib/toast';
import {
  Plus,
  Edit,
  Trash2,
  RefreshCw,
  Send,
  CheckCircle,
  XCircle,
} from 'lucide-react';
import {
  listNotificationChannels,
  createNotificationChannel,
  updateNotificationChannel,
  deleteNotificationChannel,
  testNotificationChannel,
  type NotificationChannel,
  type NotificationChannelCreateRequest,
} from '@/api/scheduledTask';
import {
  listMessageTemplates,
  CHANNEL_KIND,
  KIND_LABELS,
  type MessageTemplate,
} from '@/api/messageTemplate';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

const CHANNEL_TYPE_MAP: Record<string, { label: string; icon: string }> = {
  dingtalk: { label: tr('k_4a0e9142e7'), icon: '🤖' },
  feishu: { label: tr('k_7714e5cf3c'), icon: '🐦' },
  wecom: { label: tr('k_ff17b9f9cd'), icon: '💬' },
  email: { label: tr('k_1c8e464184'), icon: '📧' },
  webhook: { label: 'Webhook', icon: '🔗' },
  sms: { label: tr('k_17e1a481d8'), icon: '📱' },
};

/**
 * 消息模板只存引用（config.message_template_key → 模板中心）。
 * 旧形态是在渠道里手填一段 config.message_template，而发送侧从来不读它 —— 改了十遍也发不出去；
 * 模板中心改造已把存量内联文本一次性沉淀成模板并回填引用，这个死字段不再消费。
 */
const TEMPLATE_NONE = '__platform_default__';

/** 短信参数分层：服务商凭据只读 backend/.env，渠道里只放业务参数（凭据写进 config 就是泄露面） */
const SMS_FIELD_NOTE = tr('k_a650ee74d4');

interface FormData {
  name: string;
  channel_type: string;
  config: Record<string, any>;
  is_active: boolean;
}

const DEFAULT_FORM: FormData = {
  name: '',
  channel_type: 'webhook',
  config: {},
  is_active: true,
};

export default function NotificationChannels() {
  const { t } = useTranslation('ui');
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editChannel, setEditChannel] = useState<NotificationChannel | null>(null);
  const [form, setForm] = useState<FormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<NotificationChannel | null>(null);
  // 编辑既有渠道时改了类型：下面会把 config 清重填（不同通道参数不通用），
  // 这件事必须显式提醒 —— 静默清掉 webhook 密钥与模板引用就是“看着改完了、其实空了”
  const [typeReset, setTypeReset] = useState(false);
  // 消息模板清单（全部分类一次取回）：下拉按当前渠道类型过滤，列表里也要看得出引用的是哪一条
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);

  const loadChannels = useCallback(async () => {
    setLoading(true);
    try {
      // 系统管理页面：不按 workspace 过滤
      const data = await listNotificationChannels();
      setChannels(Array.isArray(data) ? data : []);
    } catch {
      toast.error(t('k_11bb85d173'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadChannels(); }, [loadChannels]);

  const loadTemplates = useCallback(async () => {
    try {
      // include_inactive=true：已下架的模板也得认得出来 —— 渠道正引用它时要显式提示，不能当成"没选"
      setTemplates(await listMessageTemplates('', true));
    } catch {
      toast.error(t('k_bf0c8b7031'));
    }
  }, []);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  const templateKind = CHANNEL_KIND[form.channel_type] || 'im';
  /** 可选模板：分类不符的模板不能选（短信渠道选邮件模板 = 长度与格式都不对，发出去才知道） */
  const templateOptions = templates.filter(t => t.template_kind === templateKind && Number(t.is_active));
  const templateOf = (key: string) => templates.find(t => t.code === key);

  const selectedKey = String(form.config.message_template_key || '');
  const selectedTemplate = selectedKey ? templateOf(selectedKey) : undefined;
  // 引用指向已删除/下架/错分类的模板时，发送会退回平台默认文案：这条必须在编辑时就看得见（§1）
  const brokenReference = !!selectedKey && (
    !selectedTemplate
    || !Number(selectedTemplate.is_active)
    || selectedTemplate.template_kind !== templateKind
  );

  const openCreate = () => {
    setEditChannel(null);
    setForm(DEFAULT_FORM);
    setTypeReset(false);
    setFormOpen(true);
  };

  const openEdit = (ch: NotificationChannel) => {
    setEditChannel(ch);
    setTypeReset(false);
    setForm({
      name: ch.name,
      channel_type: ch.channel_type,
      config: ch.config || {},
      is_active: ch.is_active,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error(t('k_3fdb506c84'));
      return;
    }
    setSaving(true);
    try {
      // 内联 message_template 已不再消费：保存时清掉，免得"内联 + 引用"两套真值源并存、下次编辑又看到一段发不出去的文案
      const cfg: Record<string, any> = { ...form.config };
      delete cfg.message_template;
      if (!cfg.message_template_key || cfg.message_template_key === TEMPLATE_NONE) delete cfg.message_template_key;
      const req: NotificationChannelCreateRequest = {
        name: form.name.trim(),
        channel_type: form.channel_type,
        config: cfg,
        is_active: form.is_active,
        workspace_id: 0,
      };
      if (editChannel) {
        await updateNotificationChannel(editChannel.id, req);
        toast.success(t('k_55aa6366c0'));
      } else {
        await createNotificationChannel(req);
        toast.success(t('k_04a691b377'));
      }
      setFormOpen(false);
      loadChannels();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (ch: NotificationChannel) => {
    setTesting(ch.id);
    try {
      const out = await testNotificationChannel(ch.id);
      // 测试发送走的是与定时任务同一条渲染链：要把"实际发出去的是什么形态"说清楚，
      // 只报一句"发送成功"会把"发的是默认文案/模板已失效"掩掉（§1）
      const meta = out?.result || {};
      const notes: string[] = [];
      if (meta.status === 'default') notes.push(tr('k_00e6e3308a'));
      if (meta.status === 'degraded') notes.push(tr('k_5c393d9a9a', { p0: (meta.reasons || []).join('；') || tr('k_f91d273b96') }));
      if (meta.unknown_variables?.length) notes.push(tr('k_ef177bce23', { p0: meta.unknown_variables.join('、') }));
      if (meta.warnings?.length) notes.push(...meta.warnings);
      if (notes.length) toast.warning(t('k_a07ecab6f2', { p0: notes.join('；') }));
      else toast.success(t('k_97daa3b62c'));
      loadChannels();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_9710d9b9f1'));
    } finally {
      setTesting(null);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteNotificationChannel(deleteTarget.id);
      toast.success(t('k_5cc232620c'));
      setDeleteTarget(null);
      loadChannels();
    } catch {
      toast.error(t('k_acf0664a54'));
    }
  };

  const updateConfig = (key: string, value: any) => {
    setForm(prev => ({ ...prev, config: { ...prev.config, [key]: value } }));
  };

  const renderConfigFields = () => {
    const ct = form.channel_type;
    if (ct === 'dingtalk') {
      return (
        <>
          <div className="space-y-1.5">
            <Label>Webhook URL</Label>
            <Input
              value={form.config.webhook_url || ''}
              onChange={e => updateConfig('webhook_url', e.target.value)}
              placeholder="https://oapi.dingtalk.com/robot/send?access_token=xxx"
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("k_0782ff8e4d")}</Label>
            <Input
              value={form.config.secret || ''}
              onChange={e => updateConfig('secret', e.target.value)}
              placeholder="SEC..."
            />
          </div>
        </>
      );
    }
    if (ct === 'feishu') {
      return (
        <div className="space-y-1.5">
          <Label>Webhook URL</Label>
          <Input
            value={form.config.webhook_url || ''}
            onChange={e => updateConfig('webhook_url', e.target.value)}
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
          />
        </div>
      );
    }
    if (ct === 'wecom') {
      return (
        <div className="space-y-1.5">
          <Label>Webhook URL</Label>
          <Input
            value={form.config.webhook_url || ''}
            onChange={e => updateConfig('webhook_url', e.target.value)}
            placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
          />
        </div>
      );
    }
    if (ct === 'email') {
      return (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>{t("k_941346a5f4")}</Label>
              <Input
                value={form.config.smtp_host || ''}
                onChange={e => updateConfig('smtp_host', e.target.value)}
                placeholder="smtp.example.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t("k_6cbb7335b5")}</Label>
              <Input
                type="number"
                value={form.config.smtp_port || 465}
                onChange={e => updateConfig('smtp_port', Number(e.target.value))}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>{t("k_a1aaf352cb")}</Label>
              <Input
                value={form.config.smtp_user || ''}
                onChange={e => updateConfig('smtp_user', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t("k_c839a8ff17")}</Label>
              <Input
                type="password"
                value={form.config.smtp_password || ''}
                onChange={e => updateConfig('smtp_password', e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>{t("k_7621421730")}</Label>
            <Input
              value={Array.isArray(form.config.to_addrs) ? form.config.to_addrs.join(',') : (form.config.to_addrs || '')}
              onChange={e => updateConfig('to_addrs', e.target.value.split(',').map((s: string) => s.trim()))}
              placeholder="admin@example.com, boss@example.com"
            />
          </div>
        </>
      );
    }
    if (ct === 'sms') {
      return (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>{t("k_f57fae8462")}</Label>
              <Input value={form.config.sign_name || ''} onChange={e => updateConfig('sign_name', e.target.value)}
                placeholder={t("k_c1404a5ad1")} />
            </div>
            <div className="space-y-1.5">
              <Label>{t("k_65a5c8a193")}</Label>
              <Input value={form.config.template_code || ''} onChange={e => updateConfig('template_code', e.target.value)}
                placeholder="SMS_123456789" />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>{t("k_b2fb81945b")}</Label>
            <Input
              value={Array.isArray(form.config.to_numbers) ? form.config.to_numbers.join(',') : (form.config.to_numbers || '')}
              onChange={e => updateConfig('to_numbers', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
              placeholder="13800000000, 13900000000" />
          </div>
          <p className="text-xs text-amber-600">
            {t('k_6ea8d02cc2')}
          </p>
          <p className="text-[11px] text-muted-foreground">{SMS_FIELD_NOTE}</p>
        </>
      );
    }
    // webhook
    return (
      <>
        <div className="space-y-1.5">
          <Label>URL</Label>
          <Input
            value={form.config.url || ''}
            onChange={e => updateConfig('url', e.target.value)}
            placeholder="https://example.com/webhook"
          />
        </div>
        <div className="space-y-1.5">
          <Label>{t("k_b435afecc0")}</Label>
          <Textarea
            value={form.config.headers ? JSON.stringify(form.config.headers) : ''}
            onChange={e => {
              try {
                updateConfig('headers', JSON.parse(e.target.value));
              } catch {}
            }}
            placeholder='{"Authorization": "Bearer xxx"}'
            rows={2}
          />
        </div>
      </>
    );
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("k_56807dfa08")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t("k_0dc350db4d")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadChannels}>
            <RefreshCw className="w-4 h-4 mr-1" />
            {t('k_38108eaa1d')}
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            {t('k_82bd472eab')}
          </Button>
        </div>
      </div>

      {/* Channel List */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? (
          <div className="col-span-full text-center p-8 text-muted-foreground">{t('k_26b5bd4947')}</div>
        ) : channels.length === 0 ? (
          <div className="col-span-full text-center p-8 text-muted-foreground">{t("k_5709a77ffe")}</div>
        ) : (
          channels.map(ch => (
            <div key={ch.id} className="border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-lg">{CHANNEL_TYPE_MAP[ch.channel_type]?.icon || '📢'}</span>
                  <div>
                    <div className="font-medium">{ch.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {CHANNEL_TYPE_MAP[ch.channel_type]?.label || ch.channel_type}
                    </div>
                    {/* 文案来自哪：一眼看出这个渠道按哪条消息模板发（还是根本没选、在发默认文案） */}
                    <div className="text-xs text-muted-foreground">
                      {ch.config?.message_template_key
                        ? tr('k_ddc15fc814', { p0: templateOf(String(ch.config.message_template_key))?.name || tr('k_0ab2cd648f') })
                        : t('k_faf0435e6b')}
                    </div>
                  </div>
                </div>
                <Badge variant="outline" className={ch.is_active ? 'text-green-500' : 'text-gray-500'}>
                  {ch.is_active ? t('k_d4e9ca3dd4') : t('k_be70be5a2e')}
                </Badge>
              </div>
              {ch.last_test_at && (
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  {ch.last_test_status === 'success' ? (
                    <CheckCircle className="w-3 h-3 text-green-500" />
                  ) : (
                    <XCircle className="w-3 h-3 text-red-500" />
                  )}
                  {tr('k_e2ba385eca')}{' '}{new Date(ch.last_test_at).toLocaleString()}
                </div>
              )}
              <div className="flex justify-end gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleTest(ch)}
                  disabled={testing === ch.id}
                >
                  <Send className="w-4 h-4 mr-1" />
                  {testing === ch.id ? t('k_ba1a3a35e6') : t('k_db06c78d1e')}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => openEdit(ch)} aria-label={t("k_a7f814c0a4")}>
                  <Edit className="w-4 h-4" />
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(ch)} aria-label={t("k_3755f56f2f")}>
                  <Trash2 className="w-4 h-4 text-destructive" />
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editChannel ? t('k_c4714508f3') : t('k_82bd472eab')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>{t("k_99e9ec8d48")}</Label>
              <Input
                value={form.name}
                onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))}
                placeholder={t("k_a3fd66e434")}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t("k_7ba72e5172")}</Label>
              <Select
                value={form.channel_type}
                onValueChange={v => {
                  if (v !== form.channel_type) setTypeReset(true);
                  setForm(prev => ({ ...prev, channel_type: v, config: {} }));
                }}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(CHANNEL_TYPE_MAP).map(([key, val]) => (
                    <SelectItem key={key} value={key}>{val.icon} {val.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {typeReset && (
              <p role="alert" className="text-xs text-amber-600">
                {t('k_918d426dba')}
              </p>
            )}
            {renderConfigFields()}

            {/* 消息模板：只选引用，文案在模板中心维护。旧形态是这里手填一段 config.message_template，
                而发送侧从不读它（改了十遍也发不出去），并且自己列了 9 个变量 ——
                其中 task_name/result_summary 属业务内容，按护栏不允许进通知 */}
            <div className="space-y-1.5">
              <Label>{t("k_1c3b85787f")}</Label>
              <Select
                value={selectedKey && templateOf(selectedKey) ? selectedKey : (selectedKey || TEMPLATE_NONE)}
                onValueChange={v => updateConfig('message_template_key', v === TEMPLATE_NONE ? '' : v)}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={TEMPLATE_NONE}>{t("k_c7a4b86add")}</SelectItem>
                  {templateOptions.map(t => (
                    <SelectItem key={t.code} value={t.code}>{t.name}</SelectItem>
                  ))}
                  {/* 引用已失效时保留一条只读顶位：不然下拉框只剩“默认”，用户以为是自已没选 */}
                  {brokenReference && (
                    <SelectItem value={selectedKey} disabled>
                      {selectedTemplate?.name || t('k_7ac64dc888')}{tr('k_82bc955af4')}{' '}</SelectItem>
                  )}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                {t('k_de26c90023', { p0: KIND_LABELS[templateKind] })}</p>
              {!templateOptions.length && (
                <p role="alert" className="text-xs text-destructive">
                  {t('k_50e2e6a08b')}
                </p>
              )}
              {form.config.message_template && (
                <p role="alert" className="text-xs text-destructive">
                  {t('k_1ac0189e7e')}
                </p>
              )}
              {brokenReference && (
                <p role="alert" className="text-xs text-destructive">
                  {t('k_aa8ba86266')}
                </p>
              )}
              {selectedTemplate && (
                // 所见即所得：不另写一套替换，直接取模板中心的原文（变量仍是字面量，发送时才展开）
                <div className="rounded-md border bg-muted/30 p-2 space-y-1">
                  <div className="text-xs font-medium">{selectedTemplate.name}</div>
                  {selectedTemplate.subject && (
                    <div className="text-xs text-muted-foreground">{t('k_6ea6ffb1fc', { p0: selectedTemplate.subject })}</div>
                  )}
                  <pre className="whitespace-pre-wrap font-mono text-xs text-muted-foreground">{selectedTemplate.content}</pre>
                  <div className="text-[11px] text-muted-foreground">
                    {tr('k_6c41a5b2e0')}{(selectedTemplate.variables || []).length
                      ? (selectedTemplate.variables || []).map(v => `{{${v}}}`).join(' ')
                      : t('k_1627e8961a')}
                    {!!(selectedTemplate.unregistered_variables || []).length && (
                      <span className="text-destructive">
                        　{t('k_bd30afcefd', { p0: (selectedTemplate.unregistered_variables || []).join('、') })}
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center gap-2">
              <Switch
                checked={form.is_active}
                onCheckedChange={v => setForm(prev => ({ ...prev, is_active: v }))}
              />
              <Label>{t("k_d4e9ca3dd4")}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave} disabled={saving}>{saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {t('k_ddba134119', { p0: deleteTarget?.name })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t("k_3755f56f2f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
