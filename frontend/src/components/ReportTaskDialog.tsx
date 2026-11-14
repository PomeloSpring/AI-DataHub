// 从报告快捷创建同款 Agent 定时任务。
//
// 复用口径：问题正文预填报告登记的可复跑问题（服务端存的，已按会话取数事实归位），
// **允许就地改一句**：登记的句子口语化、归位不准时，属主才是最终裁判；改了写回报告，
// 「用最新数据重新生成」与后续建任务共用同一句（一份报告一个口径）。
// 任务不传 AS-BOT、不传数据源——每次执行都按属主（=报告属主）的角色
// 实时继承分析能力与可查数据范围，因此这里没有任何资源选择器：让用户去挑一个他
// 看不见也管不到的内部标识，既不可读，也把平台职责推给了使用者。
// 唯一选得出的是**报告模板**（版式资产，不是资源）：选了就每次执行按模板版式出报告，
// 不选则按平台通用版式输出；选项显示模板名，字模 id 只当值用。
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import CronInput from '@/components/CronInput';
import {
  createTaskFromReport, listReportTemplates,
  type ReportDetail, type ReportTemplateOption,
} from '@/api/report';
import { triggerScheduledTask } from '@/api/scheduledTask';
import i18n from '@/i18n';

export default function ReportTaskDialog({ open, report, onClose, onCreated }: {
  open: boolean;
  report: ReportDetail | null;
  /** 兼容既有调用点（报告中心传 workspaceId）：任务不再需要按工作空间取资源候选 */
  workspaceId?: number;
  onClose: () => void;
  onCreated?: (taskId: number) => void;
}) {
  const { t } = useTranslation(['report', 'common']);
  const [name, setName] = useState('');
  const [question, setQuestion] = useState('');
  const [cron, setCron] = useState('0 9 * * *');
  const [runNow, setRunNow] = useState(false);
  const [saving, setSaving] = useState(false);
  const [templateKey, setTemplateKey] = useState('');
  const [templates, setTemplates] = useState<ReportTemplateOption[]>([]);

  // 登记值只当预填：没登记（手写/历史报告）也让人现场写一句，不再把入口堵死
  const registered = report?.origin?.question || '';
  const questionNote = report?.origin?.question_note || '';

  // 打开即重置：任务名默认「报告名 定时报告」（模板默认不套，沿用报告自己的版式）
  useEffect(() => {
    if (!open) return;
    setName(i18n.t('report:task.nameDefault', { name: report?.title || i18n.t('report:export.fileFallback') }));
    setQuestion(registered);
    setCron('0 9 * * *');
    setRunNow(false);
    setTemplateKey('');
    listReportTemplates()
      .then(rows => setTemplates(rows.filter(t => t.usable)))
      .catch(() => setTemplates([]));
  }, [open, report?.id, report?.title, registered]);

  const submit = async () => {
    if (!report) return;
    const asked = question.trim();
    if (!asked) { toast.error(t('task.questionRequired')); return; }
    if (!cron.trim()) { toast.error(t('task.cronRequired')); return; }
    setSaving(true);
    try {
      const { task_id, name: created } = await createTaskFromReport(report.id, {
        name: name.trim(),
        cron_expression: cron.trim(),
        question: asked,
        report_template_key: templateKey || undefined,
      });
      if (runNow) {
        try { await triggerScheduledTask(task_id); } catch {
          toast.error(t('task.createdRunFailed', { name: created }));
          onClose();
          onCreated?.(task_id);
          return;
        }
      }
      toast.success(runNow ? t('task.createdRunning', { name: created }) : t('task.created', { name: created }));
      onCreated?.(task_id);
      onClose();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('task.createFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={o => { if (!o) onClose(); }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{t('task.title')}</DialogTitle>
          <DialogDescription>
            {t('task.desc')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          <Label htmlFor="task-question">{t('task.question')}</Label>
          <Textarea id="task-question" rows={3} value={question} onChange={e => setQuestion(e.target.value)}
            placeholder={t('task.questionPlaceholder')} />
          {questionNote && (
            <p className="text-[11px] text-muted-foreground">{questionNote}。</p>
          )}
          {!registered && (
            <p className="text-[11px] text-amber-600 dark:text-amber-500">
              {t('task.unregistered')}
            </p>
          )}
          <p className="text-[11px] text-muted-foreground">
            {t('task.questionNote')}
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="task-name">{t('task.name')}</Label>
          <Input id="task-name" value={name} onChange={e => setName(e.target.value)} />
        </div>

        <CronInput value={cron} onChange={setCron} />

        <div className="space-y-1.5">
          <Label>{t('task.template')}</Label>
          <Select value={templateKey || '__none__'} onValueChange={v => setTemplateKey(v === '__none__' ? '' : v)}>
            <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">{t('task.noTemplate')}</SelectItem>
              {templates.map(tpl => (
                <SelectItem key={tpl.id} value={tpl.id}>
                  {tpl.name}（{tpl.format === 'html' ? 'HTML' : 'MD'}）
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[11px] text-muted-foreground">
            {t('task.templateNote')}
          </p>
        </div>

        <div className="rounded-lg border bg-muted/30 p-3 space-y-1">
          <Label className="text-xs font-normal text-muted-foreground">{t('task.identity')}</Label>
          <p className="text-xs text-muted-foreground leading-5">
            {t('task.identityNote')}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Switch checked={runNow} onCheckedChange={setRunNow} id="run-now" />
          <Label htmlFor="run-now" className="text-sm">{t('task.runNow')}</Label>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>{t('common:action.cancel')}</Button>
          <Button onClick={submit} disabled={saving || !question.trim()}>
            {saving ? t('task.creating') : t('task.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
