import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Plus, Trash2, Check, ChevronsUpDown, X, Copy, RefreshCw, Webhook } from 'lucide-react';
import CronInput from '@/components/CronInput';
import {
  createScheduledTask,
  updateScheduledTask,
  regenerateWebhookToken,
  listNotificationChannels,
  listScheduledAsBots,
  type ScheduledAsBotOption,
  type ScheduledTask,
  type ScheduledTaskCreateRequest,
  type TaskQuestion,
  type NotificationChannel,
} from '@/api/scheduledTask';
import { useVisLibrary } from '@/hooks/useVisLibrary';

import client from '@/api/client';
import { useWorkspaceStore } from '@/stores/workspaceStore';

interface WorkspaceDatasource { id: number; name: string; db_type: string; database_name: string; is_default: number; }

interface Props {
  task: ScheduledTask | null;
  onClose: (refresh?: boolean) => void;
}

/** Multi-select combobox using Popover + checkbox list */
function MultiSelect({
  options, selected, onToggle, placeholder,
}: {
  options: { value: string; label: string }[];
  selected: string[];
  onToggle: (val: string) => void;
  placeholder: string;
}) {
  const [open, setOpen] = useState(false);
  const display = selected.length === 0
    ? placeholder
    : selected.length <= 2
      ? selected.map(v => options.find(o => o.value === v)?.label || v).join('、')
      : `已选 ${selected.length} 项`;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" role="combobox" className="w-full justify-between font-normal text-sm h-9 overflow-hidden">
          <span className="truncate">{display}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0 max-h-60 overflow-auto" align="start">
        {options.length === 0 && (
          <div className="px-3 py-2 text-sm text-muted-foreground">无可用选项</div>
        )}
        {options.map(opt => {
          const isSelected = selected.includes(opt.value);
          return (
            <div
              key={opt.value}
              className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer hover:bg-accent"
              onClick={() => onToggle(opt.value)}
            >
              <div className={`w-4 h-4 border rounded flex items-center justify-center ${isSelected ? 'bg-primary border-primary' : 'border-muted-foreground'}`}>
                {isSelected && <Check className="h-3 w-3 text-primary-foreground" />}
              </div>
              <span className="flex-1 truncate">{opt.label}</span>
            </div>
          );
        })}
      </PopoverContent>
    </Popover>
  );
}

/** Display selected items as removable badges */
function SelectedBadges({ selected, onRemove, labelFn }: {
  selected: string[];
  onRemove: (val: string) => void;
  labelFn: (val: string) => string;
}) {
  if (selected.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {selected.map(v => (
        <Badge key={v} variant="secondary" className="text-xs gap-1 pr-1">
          {labelFn(v)}
          <button onClick={() => onRemove(v)} className="ml-0.5 hover:text-destructive">
            <X className="h-3 w-3" />
          </button>
        </Badge>
      ))}
    </div>
  );
}

export default function ScheduledTaskForm({ task, onClose }: Props) {
  const { currentWorkspaceId } = useWorkspaceStore();
  const isEdit = !!task;
  const workspaceId = task?.workspace_id ?? currentWorkspaceId;

  // ── Form state ────────────────────────────────────────────────
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [taskType, setTaskType] = useState<'query' | 'agent'>('query');

  // Execution source — multi-select (arrays)
  const [datasourceIds, setDatasourceIds] = useState<number[]>([]);
  const [asBotKey, setAsBotKey] = useState('');

  // Questions
  const [questions, setQuestions] = useState<TaskQuestion[]>([{ title: '', sql: '' }]);
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
  const [wsDatasources, setWsDatasources] = useState<WorkspaceDatasource[]>([]);
  const [asBots, setAsBots] = useState<ScheduledAsBotOption[]>([]);
  const [asBotsLoading, setAsBotsLoading] = useState(false);
  const [asBotsError, setAsBotsError] = useState('');
  const selectedAsBot = asBots.find(w => w.as_bot_key === asBotKey);

  useEffect(() => {
    // Load channels without workspace filter to ensure saved channels are always visible
    listNotificationChannels().then(setChannels).catch(() => {});
    client.get('/datasources/').then(({ data }) => setWsDatasources(data || [])).catch(() => {});
  }, [currentWorkspaceId]);

  useEffect(() => {
    if (taskType !== 'agent') return;
    let cancelled = false;
    setAsBotsLoading(true);
    setAsBotsError('');
    setAsBots([]);
    listScheduledAsBots(workspaceId, task?.id)
      .then(data => { if (!cancelled) setAsBots(data); })
      .catch((error: any) => {
        if (!cancelled) setAsBotsError(error?.response?.data?.detail || '加载 AS-BOT 失败，请重新打开任务配置');
      })
      .finally(() => { if (!cancelled) setAsBotsLoading(false); });
    return () => { cancelled = true; };
  }, [workspaceId, task?.id, taskType]);

  // ── Populate form when editing ────────────────────────────────
  useEffect(() => {
    if (!task) return;
    setName(task.name);
    setDescription(task.description || '');
    setTaskType(task.requires_as_bot_migration ? 'agent' : task.task_type);

    const cfg = task.task_config || {};

    // Support both single value (legacy) and array (multi-select)
    if (Array.isArray(cfg.datasource_ids)) {
      setDatasourceIds(cfg.datasource_ids);
    } else if (cfg.datasource_id) {
      setDatasourceIds([cfg.datasource_id]);
    }

    setAsBotKey(task.requires_as_bot_migration ? '' : cfg.as_bot_key || '');

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
    setQuestions([...questions, { title: '', sql: '', question: '' }]);
  };

  const removeQuestion = (index: number) => {
    setQuestions(questions.filter((_, i) => i !== index));
  };

  const updateQuestion = (index: number, field: string, value: string) => {
    const updated = [...questions];
    updated[index] = { ...updated[index], [field]: value };
    setQuestions(updated);
  };

  // ── Multi-select toggle helpers ───────────────────────────────
  const toggleDatasource = (val: string) => {
    const num = Number(val);
    setDatasourceIds(prev => prev.includes(num) ? prev.filter(v => v !== num) : [...prev, num]);
  };

  // ── Submit ────────────────────────────────────────────────────
  const handleSubmit = async () => {
    if (!name.trim()) { toast.error('请输入任务名称'); return; }
    if (triggerType !== 'webhook' && !cronExpression.trim()) {
      toast.error('定时触发模式下请输入 Cron 表达式'); return;
    }

    if (taskType === 'agent') {
      if (asBotsLoading || asBotsError || !selectedAsBot?.available) {
        toast.error('请选择任务创建者已授权且可用的 AS-BOT'); return;
      }
      if (datasourceIds.length !== 1 || !selectedAsBot.datasource_ids.includes(datasourceIds[0])) {
        toast.error('请选择 AS-BOT 授权范围内的一个数据源'); return;
      }
    }
    // Build task_config with arrays
    const taskConfig: any = { questions };
    if (datasourceIds.length === 1) {
      taskConfig.datasource_id = datasourceIds[0]; // backward compatible single value
    } else if (datasourceIds.length > 1) {
      taskConfig.datasource_ids = datasourceIds;
    }
    if (taskType === 'agent') taskConfig.as_bot_key = asBotKey;

    if (taskType === 'query' && questions.some(q => !q.sql?.trim())) {
      toast.error('SQL 模式下每项必须包含 SQL'); return;
    }
    if (taskType === 'agent' && questions.some(q => !q.question?.trim())) {
      toast.error('Agent 模式下每项必须包含问题'); return;
    }

    if (context.trim()) taskConfig.context = context.trim();

    const req: ScheduledTaskCreateRequest = {
      name: name.trim(),
      description: description.trim(),
      task_type: taskType,
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
        toast.success('更新成功');
      } else {
        await createScheduledTask(req);
        toast.success('创建成功');
      }
      onClose(true);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  // ── Labels ──────────────────────────────────────────────────
  const questionLabel = taskType === 'query' ? 'SQL 列表' : '分析问题';

  // Multi-select option lists
  const dsOptions = wsDatasources.map(ds => ({
    value: String(ds.id),
    label: `${ds.name}（${ds.db_type} / ${ds.database_name}）${ds.is_default ? ' · 默认' : ''}`,
  }));
  const analysisDatasources = dsOptions.filter(ds => selectedAsBot?.datasource_ids.includes(Number(ds.value)));

  return (
    <div className="space-y-4 min-w-0 overflow-hidden">
      <DialogHeader>
        <DialogTitle>{isEdit ? '编辑定时任务' : '新建定时任务'}</DialogTitle>
        <DialogDescription>
          配置定时执行的数据查询或分析任务
        </DialogDescription>
      </DialogHeader>

      {/* ── Basic Info ──────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 min-w-0">
        <div className="space-y-1.5 min-w-0">
          <Label>任务名称 *</Label>
          <Input value={name} onChange={e => setName(e.target.value)} placeholder="如：每日销售日报" />
        </div>
        <div className="space-y-1.5">
          <Label>任务类型</Label>
          <Select value={taskType} onValueChange={(v: any) => setTaskType(v)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent onCloseAutoFocus={(e: any) => e.preventDefault()}>
              <SelectItem value="query">SQL 模式 — 直接执行</SelectItem>
              <SelectItem value="agent">Agent 模式 — LLM 分析</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label>描述</Label>
        <Textarea value={description} onChange={e => setDescription(e.target.value)} placeholder="任务说明（可选）" rows={2} />
      </div>

      {/* ── Execution Permissions ──────────────────────────────── */}
      <div className="space-y-2">
        <Label>{taskType === 'query' ? '数据源' : '执行权限'}</Label>
        {taskType === 'agent' && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">知识库和 MCP 由 AS-BOT 统一授权，按任务创建者权限执行。</p>
            {task?.requires_as_bot_migration && <p role="alert" className="text-sm text-destructive">需重新配置 AS-BOT：旧 Agent/MCP 配置已停用，请明确选择并保存。</p>}
            <Label htmlFor="scheduled-asBot">AS-BOT *</Label>
            <Select value={asBotKey} onValueChange={setAsBotKey} disabled={asBotsLoading}>
              <SelectTrigger id="scheduled-asBot"><SelectValue placeholder={asBotsLoading ? '加载 AS-BOT...' : '选择 AS-BOT'} /></SelectTrigger>
              <SelectContent>
                {asBots.map(w => <SelectItem key={w.as_bot_key} value={w.as_bot_key} disabled={!w.available}>{w.name}{!w.available ? `（${w.reason || '不可用'}）` : ''}</SelectItem>)}
              </SelectContent>
            </Select>
            {asBotsError && <p role="alert" className="text-sm text-destructive">{asBotsError}</p>}
            {!asBotsLoading && !asBotsError && asBots.length === 0 && <p className="text-sm text-destructive">没有已授权 AS-BOT，请先配置工作空间和角色的 AS-BOT 授权。</p>}
            {selectedAsBot && <div className="text-xs text-muted-foreground space-y-1">
              <p>定时可用工具：{selectedAsBot.tools.join('、') || '无'}</p>
              {selectedAsBot.unavailable_tools.map(t => <p key={t.name}>{t.name}：{t.reason}</p>)}
            </div>}
          </div>
        )}

        {/* Datasource */}
        <div className="space-y-1.5">
          {taskType === 'agent' && <Label className="text-xs font-normal text-muted-foreground">数据源</Label>}
          {taskType === 'agent' ? (
            <Select value={datasourceIds.length === 1 ? String(datasourceIds[0]) : ''} onValueChange={v => setDatasourceIds([Number(v)])} disabled={!selectedAsBot?.available}>
              <SelectTrigger aria-label="分析数据源"><SelectValue placeholder="选择一个已授权数据源" /></SelectTrigger>
              <SelectContent>{analysisDatasources.map(ds => <SelectItem key={ds.value} value={ds.value}>{ds.label}</SelectItem>)}</SelectContent>
            </Select>
          ) : <>
            <MultiSelect options={dsOptions} selected={datasourceIds.map(String)} onToggle={toggleDatasource} placeholder="选择数据源" />
            <SelectedBadges selected={datasourceIds.map(String)} onRemove={v => toggleDatasource(v)} labelFn={v => wsDatasources.find(ds => ds.id === Number(v))?.name || v} />
          </>}
        </div>

      </div>


      {/* ── Questions ───────────────────────────────────────── */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>{questionLabel}</Label>
          <Button variant="outline" size="sm" onClick={addQuestion}>
            <Plus className="w-3 h-3 mr-1" />添加
          </Button>
        </div>
        {questions.map((q, idx) => (
          <div key={idx} className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Input
                value={q.title}
                onChange={e => updateQuestion(idx, 'title', e.target.value)}
                placeholder="标题"
                className="flex-1"
              />
              {questions.length > 1 && (
                <Button variant="ghost" size="sm" onClick={() => removeQuestion(idx)}>
                  <Trash2 className="w-4 h-4 text-destructive" />
                </Button>
              )}
            </div>
            {/* SQL 模式: SQL 输入; Agent 模式: 问题输入 */}
            {taskType === 'query' ? (
              <Textarea
                value={q.sql || ''}
                onChange={e => updateQuestion(idx, 'sql', e.target.value)}
                placeholder="SELECT COUNT(*) AS cnt FROM ..."
                className="font-mono text-sm"
                rows={3}
              />
            ) : (
              <Textarea
                value={q.question || ''}
                onChange={e => updateQuestion(idx, 'question', e.target.value)}
                placeholder="分析本月销售趋势，找出异常波动"
                rows={2}
              />
            )}
          </div>
        ))}
      </div>

      {/* ── Context (for agent mode or mcp) ─────────────────── */}
      {taskType === 'agent' && (
        <div className="space-y-1.5">
          <Label>上下文（可选）</Label>
          <Textarea
            value={context}
            onChange={e => setContext(e.target.value)}
            placeholder="给 Agent 的额外说明，如：这是一份周报，请用简洁的商务语言"
            rows={2}
          />
        </div>
      )}

      {/* ── Trigger Type ──────────────────────────────────────── */}
      <div className="space-y-1.5">
        <Label>触发方式</Label>
        <Select value={triggerType} onValueChange={(v: 'cron' | 'webhook' | 'both') => setTriggerType(v)}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="cron">定时触发</SelectItem>
            <SelectItem value="webhook">Webhook 触发</SelectItem>
            <SelectItem value="both">两者都支持</SelectItem>
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
            <Label className="font-medium">Webhook 配置</Label>
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
                    toast.success('已复制 Webhook URL');
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
                      toast.success('已重新生成 Webhook URL');
                    } catch { toast.error('重新生成失败'); }
                  }}
                >
                  <RefreshCw className="h-4 w-4" />
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                外部系统 POST 此 URL 即可触发任务执行。重新生成后旧 URL 立即失效。
              </p>
            </div>
          )}

          {/* Webhook Secret */}
          <div className="space-y-1.5">
            <Label className="text-sm">签名密钥（可选）</Label>
            <Input
              type="password"
              value={webhookSecret}
              onChange={e => setWebhookSecret(e.target.value)}
              placeholder="留空则不验证签名"
            />
            <p className="text-xs text-muted-foreground">
              配置后，调用方需在请求头 <code>X-Webhook-Signature</code> 中传递 HMAC-SHA256 签名。
            </p>
          </div>
        </div>
      )}

      {/* ── Notification & Report ───────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 min-w-0">
        <div className="space-y-1.5 min-w-0">
          <Label>通知渠道</Label>
          <Select
            value={channelId ? String(channelId) : 'none'}
            onValueChange={v => setChannelId(v === 'none' ? null : Number(v))}
          >
            <SelectTrigger><SelectValue placeholder="不通知" /></SelectTrigger>
            <SelectContent onCloseAutoFocus={(e: any) => e.preventDefault()}>
              <SelectItem value="none">不通知</SelectItem>
              {channels.map(ch => (
                <SelectItem key={ch.id} value={String(ch.id)}>{ch.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>报告模板</Label>
          <Select
            value={reportTemplateKey || 'none'}
            onValueChange={v => setReportTemplateKey(v === 'none' ? '' : v)}
          >
            <SelectTrigger><SelectValue placeholder="无模板" /></SelectTrigger>
            <SelectContent onCloseAutoFocus={(e: any) => e.preventDefault()}>
              <SelectItem value="none">无模板</SelectItem>
              {reportTemplates.map(tpl => {
                const fmt = (tpl.style_config as any)?.format || 'markdown';
                return (
                  <SelectItem key={tpl.id} value={String(tpl.id)}>
                    {tpl.name}（{fmt === 'html' ? 'HTML' : 'MD'}）
                    {tpl.is_builtin ? ' · 内置' : ''}
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
          <Label className="text-sm">成功时通知</Label>
        </div>
        <div className="flex items-center gap-2">
          <Switch checked={notifyOnFailure} onCheckedChange={setNotifyOnFailure} />
          <Label className="text-sm">失败时通知</Label>
        </div>
        <div className="flex items-center gap-2">
          <Switch checked={isActive} onCheckedChange={setIsActive} />
          <Label className="text-sm">启用</Label>
        </div>
      </div>

      {/* ── Advanced ────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 min-w-0">
        <div className="space-y-1.5 min-w-0">
          <Label>超时（秒）</Label>
          <Input type="number" value={timeoutSeconds} onChange={e => setTimeoutSeconds(Number(e.target.value))} />
        </div>
        <div className="space-y-1.5">
          <Label>失败重试次数</Label>
          <Input type="number" value={maxRetries} onChange={e => setMaxRetries(Number(e.target.value))} />
        </div>
      </div>

      {/* ── Actions ─────────────────────────────────────────── */}
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={() => onClose()}>取消</Button>
        <Button onClick={handleSubmit} disabled={saving}>
          {saving ? '保存中...' : isEdit ? '更新' : '创建'}
        </Button>
      </div>
    </div>
  );
}
