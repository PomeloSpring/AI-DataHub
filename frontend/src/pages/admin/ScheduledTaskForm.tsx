import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { Plus, Trash2, Copy, RefreshCw, Webhook } from 'lucide-react';
import CronInput from '@/components/CronInput';
import {
  createScheduledTask,
  updateScheduledTask,
  regenerateWebhookToken,
  listNotificationChannels,
  type ScheduledTask,
  type ScheduledTaskCreateRequest,
  type TaskQuestion,
  type NotificationChannel,
} from '@/api/scheduledTask';
import { useVisLibrary } from '@/hooks/useVisLibrary';

import { useWorkspaceStore } from '@/stores/workspaceStore';

interface Props {
  task: ScheduledTask | null;
  onClose: (refresh?: boolean) => void;
}

export default function ScheduledTaskForm({ task, onClose }: Props) {
  const { t } = useTranslation(['workspace', 'common']);
  const { currentWorkspaceId } = useWorkspaceStore();
  const isEdit = !!task;
  const workspaceId = task?.workspace_id ?? currentWorkspaceId;

  // ── Form state ────────────────────────────────────────────────
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  // Questions
  const [questions, setQuestions] = useState<TaskQuestion[]>([{ title: '', question: '' }]);
  const [context, setContext] = useState('');

  // Schedule
  const [cronExpression, setCronExpression] = useState('0 9 * * *');
  const [triggerType, setTriggerType] = useState<'cron' | 'webhook' | 'both'>('cron');
  const [webhookSecret, setWebhookSecret] = useState('');
  const [webhookToken, setWebhookToken] = useState<string | null>(null);

  // Notification
  const [channelId, setChannelId] = useState<number | null>(null);
  const [notifyOnSuccess, setNotifyOnSuccess] = useState(true);
  const [notifyOnFailure, setNotifyOnFailure] = useState(true);
  const [reportTemplateKey, setReportTemplateKey] = useState('');

  // Advanced
  const [timeoutSeconds, setTimeoutSeconds] = useState(300);
  const [maxRetries, setMaxRetries] = useState(0);
  const [isActive, setIsActive] = useState(true);
  const [saving, setSaving] = useState(false);

  // ── Dropdown data ─────────────────────────────────────────────
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const { items: visItems } = useVisLibrary(true);
  const reportTemplates = visItems.filter(i => i.category === 'report_template');

  useEffect(() => {
    // Load channels without workspace filter to ensure saved channels are always visible
    listNotificationChannels().then(setChannels).catch(() => {});
  }, [currentWorkspaceId]);

  // ── Populate form when editing ────────────────────────────────
  useEffect(() => {
    if (!task) return;
    setName(task.name);
    setDescription(task.description || '');

    const cfg = task.task_config || {};
    setQuestions(cfg.questions || []);
    setContext(cfg.context || '');
    setCronExpression(task.cron_expression || '');
    const tt = task.trigger_type;
    setTriggerType(tt === 'webhook' || tt === 'both' ? tt : 'cron');
    setWebhookSecret(task.webhook_secret || '');
    setWebhookToken(task.webhook_token || null);
    setChannelId(task.channel_id);
    setNotifyOnSuccess(task.notify_on_success);
    setNotifyOnFailure(task.notify_on_failure);
    setReportTemplateKey(task.report_template_key || '');
    setTimeoutSeconds(task.timeout_seconds);
    setMaxRetries(task.max_retries);
    setIsActive(task.is_active);
  }, [task]);

  // ── Questions helpers ─────────────────────────────────────────
  const addQuestion = () => {
    setQuestions([...questions, { title: '', question: '' }]);
  };

  const removeQuestion = (index: number) => {
    setQuestions(questions.filter((_, i) => i !== index));
  };

  const updateQuestion = (index: number, field: string, value: string) => {
    const updated = [...questions];
    updated[index] = { ...updated[index], [field]: value };
    setQuestions(updated);
  };

  // ── Submit ────────────────────────────────────────────────────
  const handleSubmit = async () => {
    if (!name.trim()) { toast.error(t('schedule.form.nameRequired')); return; }
    if (triggerType !== 'webhook' && !cronExpression.trim()) {
      toast.error(t('schedule.form.cronRequired')); return;
    }

    // 任务只带问题清单：AS-BOT 与数据源都不选——能力与可查范围按当前用户的角色实时继承，
    // 让用户去选一个他看不见也管不了的内部标识，等于把平台职责推给使用者。
    if (questions.some(q => !q.question?.trim())) {
      toast.error(t('schedule.form.questionRequired')); return;
    }

    const taskConfig: any = { questions };
    if (context.trim()) taskConfig.context = context.trim();

    const req: ScheduledTaskCreateRequest = {
      name: name.trim(),
      description: description.trim(),
      task_config: taskConfig,
      cron_expression: triggerType === 'webhook' ? '' : cronExpression.trim(),
      trigger_type: triggerType,
      webhook_secret: webhookSecret.trim() || undefined,
      channel_id: channelId || undefined,
      notify_on_success: notifyOnSuccess,
      notify_on_failure: notifyOnFailure,
      report_template_key: reportTemplateKey || undefined,
      timeout_seconds: timeoutSeconds,
      max_retries: maxRetries,
      is_active: isActive,
      workspace_id: workspaceId,
    };

    setSaving(true);
    try {
      if (isEdit && task) {
        await updateScheduledTask(task.id, req);
        toast.success(t('schedule.form.updateSuccess'));
      } else {
        await createScheduledTask(req);
        toast.success(t('schedule.form.createSuccess'));
      }
      onClose(true);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('common:state.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  // ── Labels ──────────────────────────────────────────────────
  const questionLabel = t('schedule.form.questions');

  // 根容器不写 overflow-hidden：输入框是 w-full，focus 环（ring-2 + ring-offset-2 = 4px）
  // 本来就要画到盒子外；这里再剪一刀，左右两段圆弧就没了（只剩上下两条，看着像提示线断了）。
  // 横向不撑爆弹窗自有 DialogContent 的 overflow-x-hidden + [&>*]:min-w-0 负责。
  return (
    <div className="space-y-4 min-w-0">
      <DialogHeader>
        <DialogTitle>{isEdit ? t('schedule.form.editTitle') : t('schedule.form.createTitle')}</DialogTitle>
        <DialogDescription>
          {t('schedule.subtitle')}
        </DialogDescription>
      </DialogHeader>

      {/* ── Basic Info ──────────────────────────────────────── */}
      <div className="space-y-1.5">
        <Label>{t('schedule.form.nameLabel')}</Label>
        <Input value={name} onChange={e => setName(e.target.value)} placeholder={t('schedule.form.namePlaceholder')} />
      </div>

      {/* ── 执行身份（新架构：零选择，按当前用户角色继承）──────────────────
          这里刻意不放 AS-BOT 与数据源选择器：
          - AS-BOT 由角色绑定决定（一个角色一份配置），任务跟着角色走，换绑即生效；
          - 数据源对用户是黑盒（护栏 §7），用户既看不见也不该选；可查范围就是角色授权集。
          范围有歧义时由 Agent 按站点业务名自解析，跨站需求写进问题或上下文即可。 */}
      <div className="rounded-lg border bg-muted/30 p-3 space-y-1">
        <Label className="text-xs font-normal text-muted-foreground">{t('schedule.form.identity')}</Label>
        <p className="text-xs text-muted-foreground leading-5">
          {t('schedule.form.identityNote')}
        </p>
      </div>

      {/* ── Questions ───────────────────────────────────────── */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>{questionLabel}</Label>
          <Button variant="outline" size="sm" onClick={addQuestion}>
            <Plus className="w-3 h-3 mr-1" />{t('common:action.add')}
          </Button>
        </div>
        {questions.map((q, idx) => (
          <div key={idx} className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Input
                value={q.title}
                onChange={e => updateQuestion(idx, 'title', e.target.value)}
                placeholder={t('schedule.form.questionTitlePlaceholder')}
                className="flex-1"
              />
              {questions.length > 1 && (
                <Button variant="ghost" size="sm" onClick={() => removeQuestion(idx)}>
                  <Trash2 className="w-4 h-4 text-destructive" />
                </Button>
              )}
            </div>
            <Textarea
              value={q.question || ''}
              onChange={e => updateQuestion(idx, 'question', e.target.value)}
              placeholder={t('schedule.form.questionPlaceholder')}
              rows={2}
            />
          </div>
        ))}
      </div>

      {/* ── Context ─────────────────────────────────────────── */}
      <div className="space-y-1.5">
        <Label>{t('schedule.form.context')}</Label>
        <Textarea
          value={context}
          onChange={e => setContext(e.target.value)}
          placeholder={t('schedule.form.contextPlaceholder')}
          rows={2}
        />
      </div>

      {/* ── Trigger Type ──────────────────────────────────────── */}
      <div className="space-y-1.5">
        <Label>{t('schedule.col.trigger')}</Label>
        <Select value={triggerType} onValueChange={(v: 'cron' | 'webhook' | 'both') => setTriggerType(v)}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="cron">{t('schedule.form.triggerCron')}</SelectItem>
            <SelectItem value="webhook">{t('schedule.form.triggerWebhook')}</SelectItem>
            <SelectItem value="both">{t('schedule.form.triggerBoth')}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* ── Cron (only for cron/both) ─────────────────────────── */}
      {triggerType !== 'webhook' && (
        <CronInput value={cronExpression} onChange={setCronExpression} />
      )}

      {/* ── Webhook Config (only for webhook/both) ────────────── */}
      {triggerType !== 'cron' && (
        <div className="space-y-3 rounded-lg border p-4 bg-muted/30">
          <div className="flex items-center gap-2">
            <Webhook className="h-4 w-4" />
            <Label className="font-medium">{t('schedule.form.webhookConfig')}</Label>
          </div>

          {/* Webhook URL — only in edit mode */}
          {isEdit && webhookToken && (
            <div className="space-y-1.5">
              <Label className="text-sm text-muted-foreground">Webhook URL</Label>
              <div className="flex gap-2">
                <Input
                  readOnly
                  value={`${window.location.origin}/api/webhook/tasks/${task?.id}/${webhookToken}`}
                  className="font-mono text-xs"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => {
                    navigator.clipboard.writeText(`${window.location.origin}/api/webhook/tasks/${task?.id}/${webhookToken}`);
                    toast.success(t('schedule.webhookCopied'));
                  }}
                >
                  <Copy className="h-4 w-4" />
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={async () => {
                    if (!task) return;
                    try {
                      const res = await regenerateWebhookToken(task.id);
                      setWebhookToken(res.webhook_token);
                      toast.success(t('schedule.form.webhookRegenerated'));
                    } catch { toast.error(t('schedule.form.webhookRegenFailed')); }
                  }}
                >
                  <RefreshCw className="h-4 w-4" />
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {t('schedule.form.webhookNote')}
              </p>
            </div>
          )}

          {/* Webhook Secret */}
          <div className="space-y-1.5">
            <Label className="text-sm">{t('schedule.form.secret')}</Label>
            <Input
              type="password"
              value={webhookSecret}
              onChange={e => setWebhookSecret(e.target.value)}
              placeholder={t('schedule.form.secretPlaceholder')}
            />
            <p className="text-xs text-muted-foreground">
              {t('schedule.form.secretNoteBefore')}<code>X-Webhook-Signature</code>{t('schedule.form.secretNoteAfter')}
            </p>
          </div>
        </div>
      )}

      {/* ── Notification & Report ───────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 min-w-0">
        <div className="space-y-1.5 min-w-0">
          <Label>{t('schedule.form.channel')}</Label>
          <Select
            value={channelId ? String(channelId) : 'none'}
            onValueChange={v => setChannelId(v === 'none' ? null : Number(v))}
          >
            <SelectTrigger><SelectValue placeholder={t('schedule.form.noNotify')} /></SelectTrigger>
            <SelectContent onCloseAutoFocus={(e: any) => e.preventDefault()}>
              <SelectItem value="none">{t('schedule.form.noNotify')}</SelectItem>
              {channels.map(ch => (
                <SelectItem key={ch.id} value={String(ch.id)}>{ch.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>{t('schedule.form.template')}</Label>
          <Select
            value={reportTemplateKey || 'none'}
            onValueChange={v => setReportTemplateKey(v === 'none' ? '' : v)}
          >
            <SelectTrigger><SelectValue placeholder={t('schedule.form.noTemplate')} /></SelectTrigger>
            <SelectContent onCloseAutoFocus={(e: any) => e.preventDefault()}>
              <SelectItem value="none">{t('schedule.form.noTemplate')}</SelectItem>
              {reportTemplates.map(tpl => {
                const fmt = (tpl.style_config as any)?.format || 'markdown';
                return (
                  <SelectItem key={tpl.id} value={String(tpl.id)}>
                    {tpl.name}（{fmt === 'html' ? 'HTML' : 'MD'}）
                    {tpl.is_builtin ? t('schedule.form.builtinSuffix') : ''}
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* ── Options ─────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-4 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <Switch checked={notifyOnSuccess} onCheckedChange={setNotifyOnSuccess} />
          <Label className="text-sm">{t('schedule.form.notifySuccess')}</Label>
        </div>
        <div className="flex items-center gap-2">
          <Switch checked={notifyOnFailure} onCheckedChange={setNotifyOnFailure} />
          <Label className="text-sm">{t('schedule.form.notifyFailure')}</Label>
        </div>
        <div className="flex items-center gap-2">
          <Switch checked={isActive} onCheckedChange={setIsActive} />
          <Label className="text-sm">{t('schedule.col.enabled')}</Label>
        </div>
      </div>

      {/* ── Advanced ────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 min-w-0">
        <div className="space-y-1.5 min-w-0">
          <Label>{t('schedule.form.timeout')}</Label>
          <Input type="number" value={timeoutSeconds} onChange={e => setTimeoutSeconds(Number(e.target.value))} />
        </div>
        <div className="space-y-1.5">
          <Label>{t('schedule.form.retries')}</Label>
          <Input type="number" value={maxRetries} onChange={e => setMaxRetries(Number(e.target.value))} />
        </div>
      </div>

      {/* ── Actions ─────────────────────────────────────────── */}
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={() => onClose()}>{t('common:action.cancel')}</Button>
        <Button onClick={handleSubmit} disabled={saving}>
          {saving ? t('common:action.saving') : isEdit ? t('schedule.form.update') : t('schedule.form.create')}
        </Button>
      </div>
    </div>
  );
}
