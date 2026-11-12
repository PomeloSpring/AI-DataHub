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

export default function ReportTaskDialog({ open, report, onClose, onCreated }: {
  open: boolean;
  report: ReportDetail | null;
  /** 兼容既有调用点（报告中心传 workspaceId）：任务不再需要按工作空间取资源候选 */
  workspaceId?: number;
  onClose: () => void;
  onCreated?: (taskId: number) => void;
}) {
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
    setName(`${report?.title || '报告'} 定时报告`);
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
    if (!asked) { toast.error('请填写要复用的分析问题'); return; }
    if (!cron.trim()) { toast.error('请填写执行周期'); return; }
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
          toast.error(`任务「${created}」已创建，但立即执行派发失败，请到任务调度里手动触发`);
          onClose();
          onCreated?.(task_id);
          return;
        }
      }
      toast.success(runNow ? `任务已创建并开始执行：${created}` : `任务已创建：${created}`);
      onCreated?.(task_id);
      onClose();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '创建定时任务失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={o => { if (!o) onClose(); }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>创建同款定时任务</DialogTitle>
          <DialogDescription>
            复用本报告登记的分析问题，按设定周期无人值守重新取数并产出报告。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          <Label htmlFor="task-question">复用的分析问题</Label>
          <Textarea id="task-question" rows={3} value={question} onChange={e => setQuestion(e.target.value)}
            placeholder="每次执行都按这句话重新取数并出报告，如：查近 7 天各页面访问 IP 归属地分布，输出 HTML 报告" />
          {questionNote && (
            <p className="text-[11px] text-muted-foreground">{questionNote}。</p>
          )}
          {!registered && (
            <p className="text-[11px] text-amber-600 dark:text-amber-500">
              本报告未登记原始分析问题（手写报告或改造前的历史报告），此处填写后每次执行与「用最新数据重新生成」都按这句取数。
            </p>
          )}
          <p className="text-[11px] text-muted-foreground">
            改过的问题会记到本报告上，「用最新数据重新生成」也按这句跑（一份报告一个口径）。
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="task-name">任务名称</Label>
          <Input id="task-name" value={name} onChange={e => setName(e.target.value)} />
        </div>

        <CronInput value={cron} onChange={setCron} />

        <div className="space-y-1.5">
          <Label>报告模板（可选）</Label>
          <Select value={templateKey || '__none__'} onValueChange={v => setTemplateKey(v === '__none__' ? '' : v)}>
            <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">不用模板（按平台通用版式输出）</SelectItem>
              {templates.map(t => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name}（{t.format === 'html' ? 'HTML' : 'MD'}）
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[11px] text-muted-foreground">
            选定后每次执行都按这份版式出报告（只换数据、不换章节与图表）；模板被下架时执行会显式失败，不静默换版式。
          </p>
        </div>

        <div className="rounded-lg border bg-muted/30 p-3 space-y-1">
          <Label className="text-xs font-normal text-muted-foreground">执行身份</Label>
          <p className="text-xs text-muted-foreground leading-5">
            按你的角色与权限执行：AS-BOT 与可查数据范围都随角色授权变化，无需在此选择；
            授权被收回时执行会被服务端拒绝并如实报错。跨站点需求请在报告的问题正文里点名站点。
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Switch checked={runNow} onCheckedChange={setRunNow} id="run-now" />
          <Label htmlFor="run-now" className="text-sm">创建后立即执行一次（按最新数据出一份报告）</Label>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>取消</Button>
          <Button onClick={submit} disabled={saving || !question.trim()}>
            {saving ? '创建中…' : '创建任务'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
