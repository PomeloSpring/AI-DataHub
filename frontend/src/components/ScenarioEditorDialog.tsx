/**
 * ScenarioEditorDialog — 分析场景的唯一编辑入口（新建 / 编辑 / 追加到已有）。
 *
 * 抽出来的理由不是「想复用组件」，而是这套表单承载三条**硬口径**，一旦复制就会漂：
 *  1. 主体范围必须显式声明（选主体 或 勾「不限主体」）—— 没声明的场景对所有角色都不命中，
 *     静默保存等于配了个永远不会生效的场景；
 *  2. 主体与报告模板只能从服务端候选里选（显示业务名，value 用 key / 名字），
 *     不让人手输 object_key 或模板 id（id 随环境漂移，填了就绑一个读不到的模板）；
 *  3. 候选拉不到要显式报错 + 重试，不回退成空下拉（那会把"查不到"演成"没得选"）。
 *
 * 三个使用方：分析场景页（新建/编辑）、进化中心「知识更新」的 scenario 分支
 * （把提议正文追加到已有场景）、进化中心「直接维护 → 场景」。
 * 追加模式只做两件事：正文拼到所选场景正文之后、触发词取并集 —— 其余配置沿用原场景，
 * 不覆盖任何既有内容（覆盖式改写不是"追加"，也没有 diff 可审）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import client from '@/api/client';
import { useTranslation } from 'react-i18next';
import { t as i18nTr } from '@/i18n/runtime';

// ── Types ──────────────────────────────────────────────────────────

export interface ScenarioItem {
  id: number;
  workspace_id?: number;
  datasource_id?: number;
  knowledge_type?: string;
  title: string;
  content: string;
  metadata: {
    triggers?: string[]; scenario?: string; subjects?: string[]; subject_all?: boolean;
    report_template?: string;
  } | null;
  related_tables: string;
  priority: number;
  usage_count?: number;
  is_active: number;
  updated_at?: string;
  /** 服务端按主体权限派生的可见性标注（配置事实，不是运行时意外） */
  subject_keys?: string[];
  subject_names?: string[];
  subject_all?: boolean;
  visible_to_me?: boolean;
  visibility_note?: string;
}

/** 主体候选（本体对象）：显示业务名、value 用内部 key。 */
export interface SubjectOption { key: string; name: string; model_name: string; kind: string }

/**
 * 报告模板候选：只带业务名与形状，不带字模 id。
 * 场景绑定的口径就是名字（服务端按名字解析，id 随环境漂移），拿不到 id 是有意为之。
 */
export interface ReportTemplateOption { name: string; format: string; usable: boolean; sections: number }

/** 提议预填（来自进化中心的「知识更新」提议）：只给业务内容，不含任何内部 id。 */
export interface ScenarioPrefill { title?: string; content?: string; triggers?: string[] }

interface FormData {
  title: string;
  triggers: string;        // 逗号分隔的触发词
  scenario: string;        // 适用场景
  report_template: string; // 绑定的报告模板（业务名）
  content: string;         // 查询步骤说明（注入 AI 取数上下文的正文）
  related_tables: string;
  subjects: string[];      // 绑定的主体（本体对象 key）
  subject_all: boolean;    // 不限主体（显式扩权给全部角色）
  priority: number;
  is_active: boolean;
}

const DEFAULT_FORM: FormData = {
  title: '', triggers: '', scenario: '', report_template: '', content: '',
  related_tables: '', subjects: [], subject_all: false, priority: 0, is_active: true,
};

const NO_TEMPLATE = '__none__';

export const splitTriggers = (s: string): string[] =>
  s.split(/[,，]/).map(t => t.trim()).filter(Boolean);

/**
 * 主体候选按 key 归并。
 *
 * 同一个对象 key 可能同时出现在业务本体与系统能力本体里（真库就有 `user`/`role`
 * 两级重合）：直接用 key 当 React key 会撞车、徽标错位，人也会看到两个都叫「用户」
 * 的按钮不知选哪个。场景授权的判据就是 key 本身，所以合并成一行、把来源模型名拼进
 * 说明里（不合并就是让人在两个等价选项之间瞎猜）。
 */
export function dedupeSubjects(items: SubjectOption[]): SubjectOption[] {
  const byKey = new Map<string, SubjectOption>();
  for (const raw of items) {
    const key = String(raw?.key || '');
    if (!key) continue;
    const prev = byKey.get(key);
    if (!prev) {
      byKey.set(key, { ...raw, key });
      continue;
    }
    const models = Array.from(new Set(
      [prev.model_name, raw.model_name].map(m => String(m || '').trim()).filter(Boolean)));
    byKey.set(key, { ...prev, model_name: models.join(' / ') });
  }
  return Array.from(byKey.values());
}

// ── 候选清单（主体 / 报告模板）──────────────────────────────────────

export interface ScenarioFormOptions {
  subjects: SubjectOption[];
  subjectsError: string;
  reloadSubjects: () => void;
  reportTemplates: ReportTemplateOption[];
  reportTemplatesError: string;
  reloadReportTemplates: () => void;
}

/**
 * 主体与报告模板候选的唯一加载实现。
 *
 * 为什么两种候选都要「错误态 + 重试」而不是 catch 成空数组：下拉里空着一片，
 * 用户唯一能得出的结论是"本系统没有可绑对象/模板"，于是去别处造一个 —— 而真正
 * 的原因是这次请求失败了。加载态也必须在调用方可见（两个页面都要显式重试按钮）。
 */
export function useScenarioFormOptions(): ScenarioFormOptions {
  const { t } = useTranslation('ui');
  const [subjects, setSubjects] = useState<SubjectOption[]>([]);
  const [subjectsError, setSubjectsError] = useState('');
  const [reportTemplates, setReportTemplates] = useState<ReportTemplateOption[]>([]);
  const [reportTemplatesError, setReportTemplatesError] = useState('');

  // 主体清单（本体对象）：与权限判定同一数据源，不另建一份候选
  const reloadSubjects = useCallback(() => {
    setSubjectsError('');
    client.get('/admin/knowledge/subjects')
      .then(({ data }) => setSubjects(
        dedupeSubjects(Array.isArray(data?.items) ? data.items : [])))
      .catch((e: any) => setSubjectsError(e?.response?.data?.detail || t('k_16740df890')));
  }, [t]);

  // 报告模板候选走本域接口（配场景的读码就能拿），不走 /reports/templates ——
  // 那要「报告模板」菜单权限，配场景的人不一定有
  const reloadReportTemplates = useCallback(() => {
    setReportTemplatesError('');
    client.get('/admin/knowledge/report-templates')
      .then(({ data }) => setReportTemplates(Array.isArray(data?.items) ? data.items : []))
      .catch((e: any) => setReportTemplatesError(e?.response?.data?.detail || t('k_56025ae1f0')));
  }, [t]);

  useEffect(() => { reloadSubjects(); }, [reloadSubjects]);
  useEffect(() => { reloadReportTemplates(); }, [reloadReportTemplates]);

  return { subjects, subjectsError, reloadSubjects, reportTemplates, reportTemplatesError, reloadReportTemplates };
}

// ── 表单换算 ────────────────────────────────────────────────────────

function formFromItem(item: ScenarioItem): FormData {
  return {
    title: item.title,
    triggers: (item.metadata?.triggers || []).join(', '),
    scenario: item.metadata?.scenario || '',
    report_template: item.metadata?.report_template || '',
    content: item.content || '',
    related_tables: item.related_tables || '',
    subjects: item.metadata?.subjects || [],
    subject_all: !!item.metadata?.subject_all,
    priority: item.priority,
    is_active: !!item.is_active,
  };
}

/** 追加：正文拼在原正文之后，触发词取并集；其余字段沿用原场景（不改即不覆盖）。 */
function appendPrefillTo(base: FormData, prefill: ScenarioPrefill | null | undefined): FormData {
  if (!prefill) return base;
  const extra = (prefill.content || '').trim();
  const content = extra
    ? [base.content.trim(), extra].filter(Boolean).join('\n\n')
    : base.content;
  const triggers = splitTriggers(base.triggers);
  for (const word of prefill.triggers || []) {
    const w = String(word || '').trim();
    if (w && !triggers.some(x => x.toLowerCase() === w.toLowerCase())) triggers.push(w);
  }
  return { ...base, content, triggers: triggers.join(', ') };
}

// ── 组件 ────────────────────────────────────────────────────────────

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 候选清单（由调用方的 useScenarioFormOptions 提供，便于在页面层先就绪） */
  options: ScenarioFormOptions;
  /** 非空 = 编辑这个场景（appendMode 时作为已选中的追加目标） */
  item?: ScenarioItem | null;
  /** 追加模式：先选一个已有场景，再把 prefill 追加进去 */
  appendMode?: boolean;
  prefill?: ScenarioPrefill | null;
  /** 保存成功回执：新建回新建 id、追加回被追加场景 id、编辑回自身 id */
  onSaved?: (ref: string) => void;
}

export default function ScenarioEditorDialog({
  open, onOpenChange, options, item = null, appendMode = false, prefill = null, onSaved,
}: Props) {
  const { t } = useTranslation('ui');
  const { subjects, subjectsError, reloadSubjects, reportTemplates, reportTemplatesError, reloadReportTemplates } = options;

  const [form, setForm] = useState<FormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  /** 当前要 PUT 的场景 id；空 = 走 POST 新建 */
  const [targetId, setTargetId] = useState<number | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioItem[]>([]);
  const [scenariosError, setScenariosError] = useState('');
  const [scenariosLoading, setScenariosLoading] = useState(false);

  // prefill 每次渲染都是新对象，不能进依赖数组（否则一打字就被重置回预填值）
  const prefillRef = useRef(prefill);
  prefillRef.current = prefill;

  // 打开时初始化：编辑/追加用原场景打底，新建则用提议预填（或空表单）
  useEffect(() => {
    if (!open) return;
    const p = prefillRef.current;
    if (item) {
      setTargetId(item.id);
      setForm(appendMode ? appendPrefillTo(formFromItem(item), p) : formFromItem(item));
      return;
    }
    setTargetId(null);
    setForm({
      ...DEFAULT_FORM,
      title: appendMode ? '' : (p?.title || ''),
      triggers: appendMode ? '' : (p?.triggers || []).join(', '),
      content: appendMode ? '' : (p?.content || ''),
    });
  }, [open, item, appendMode]);

  // 追加模式的场景候选：只列分析场景（quick_path），不跨类型揣测
  const loadScenarios = useCallback(() => {
    setScenariosLoading(true);
    setScenariosError('');
    client.get('/admin/knowledge', {
      params: { workspace_id: 0, knowledge_type: 'quick_path', page: 1, size: 200 },
    })
      .then(({ data }) => setScenarios(Array.isArray(data?.items) ? data.items : []))
      .catch((e: any) => setScenariosError(e?.response?.data?.detail || t('k_d1e29894fa')))
      .finally(() => setScenariosLoading(false));
  }, [t]);

  useEffect(() => {
    if (!open || !appendMode) return;
    loadScenarios();
  }, [open, appendMode, loadScenarios]);

  const pickAppendTarget = (id: string) => {
    const hit = scenarios.find(s => String(s.id) === id);
    if (!hit) return;
    setTargetId(hit.id);
    setForm(appendPrefillTo(formFromItem(hit), prefillRef.current));
  };

  // 当前绑定值不在候选里（模板被下架/改名）：必须当场看得见，不能让下拉静默吞掉
  const boundMissing = !!form.report_template
    && !reportTemplates.some(t => t.name === form.report_template);

  const handleSave = async () => {
    if (!form.title.trim() || !form.content.trim()) {
      toast.error(t('k_39fcc83d53'));
      return;
    }
    if (splitTriggers(form.triggers).length === 0) {
      toast.error(t('k_c0d93f7a98'));
      return;
    }
    // 主体范围必须显式声明：服务端也会 422 拒，但前端先拦一层，不让用户白点一次保存
    if (!form.subject_all && form.subjects.length === 0) {
      toast.error(t('k_fc6ce67e50'));
      return;
    }
    setSaving(true);
    try {
      const metadata: Record<string, any> = {
        triggers: splitTriggers(form.triggers),
        scenario: form.scenario.trim(),
        // 主体授权的唯一声明：服务端拿它算"哪些角色能看见/能命中"
        subjects: form.subject_all ? [] : form.subjects,
        subject_all: form.subject_all,
      };
      // 没绑模板就不往 metadata 里写空键（旧数据里多一个意义不明的字段）
      if (form.report_template.trim()) metadata.report_template = form.report_template.trim();
      const payload: any = {
        workspace_id: 0,
        datasource_id: 0,
        knowledge_type: 'quick_path',
        title: form.title,
        content: form.content,
        metadata,
        related_tables: form.related_tables,
        priority: form.priority,
        is_active: form.is_active ? 1 : 0,
      };

      let savedId = targetId;
      if (targetId != null) {
        await client.put(`/admin/knowledge/${targetId}`, payload);
        toast.success(appendMode && item === null ? t('k_87a9810577') : t('k_1b92092d63'));
      } else {
        const { data } = await client.post('/admin/knowledge', payload);
        savedId = Number(data?.id) || null;
        toast.success(t('k_2d4c5dcc74'));
      }
      onOpenChange(false);
      onSaved?.(savedId != null ? String(savedId) : '');
    } catch (e: any) {
      // 必须把服务端原因原样递出去（422 "主体不存在/未声明主体"等），
      // 统一成"保存失败"会把可诊断的口径问题变成猜谜
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  const title = appendMode ? t('k_8236b45e16') : item ? t('k_a3ff0d0814') : t('k_5bd56c5159');
  const canSubmit = !appendMode || targetId != null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[88vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{t('k_6266d2b2f8')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {appendMode && item === null && (
            <div>
              <Label>{t('k_ffc00e99d7')}</Label>
              {scenariosError ? (
                <div className="mt-1 flex items-center gap-2 text-xs text-destructive">
                  {scenariosError}
                  <Button variant="outline" size="sm" className="h-6 text-xs"
                    onClick={loadScenarios}>{t('k_e2d53a6d3a')}</Button>
                </div>
              ) : (
                <Select value={targetId != null ? String(targetId) : ''}
                  onValueChange={pickAppendTarget} disabled={scenariosLoading}>
                  <SelectTrigger className="mt-1 w-full" aria-label={t('k_ffc00e99d7')}>
                    <SelectValue placeholder={scenariosLoading ? t('k_26b5bd4947') : t('k_cf71a636fa')} />
                  </SelectTrigger>
                  <SelectContent>
                    {scenarios.map(s => (
                      <SelectItem key={s.id} value={String(s.id)}>{s.title}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <p className="mt-1 text-xs text-muted-foreground">{t('k_cec78b8fa9')}</p>
            </div>
          )}

          {!item && !appendMode && prefill?.content && (
            <p className="rounded border bg-muted/40 p-2 text-xs text-muted-foreground">
              {t('k_258d562c38')}
            </p>
          )}

          <div>
            <Label>{t('k_d2f28317ae')}</Label>
            <Input
              value={form.title}
              onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              placeholder={t('k_047add24a6')}
            />
          </div>
          <div>
            <Label>{t('k_f558d82c4d')}</Label>
            <Input
              value={form.triggers}
              onChange={e => setForm(f => ({ ...f, triggers: e.target.value }))}
              placeholder={t('k_b19a14c672')}
            />
            <p className="text-xs text-muted-foreground mt-1">{t('k_1d107b19d6')}</p>
          </div>
          <div>
            <Label>{t('k_3b05370cd8')}</Label>
            <Input
              value={form.scenario}
              onChange={e => setForm(f => ({ ...f, scenario: e.target.value }))}
              placeholder={t('k_9a95855d69')}
            />
          </div>
          <div>
            <Label>{t('k_2f531daf33')}</Label>
            {/* 场景绑定模板 = 命中该场景时按模板版式出报告。选择框只给业务名（候选走本域接口），
                不让人手填字模 id：id 随环境漂移，"绑了个读不到的模板"最后会变成问答里的
                静默回落（管理员看见"已绑定"，用户看见"没套用"）。 */}
            {reportTemplatesError ? (
              <div className="mt-1 flex items-center gap-2 text-xs text-destructive">
                {reportTemplatesError}
                <Button variant="outline" size="sm" className="h-6 text-xs"
                  onClick={reloadReportTemplates}>{t('k_e2d53a6d3a')}</Button>
              </div>
            ) : (
              <Select value={form.report_template || NO_TEMPLATE}
                onValueChange={v => setForm(f => ({
                  ...f, report_template: v === NO_TEMPLATE ? '' : v,
                }))}>
                <SelectTrigger className="mt-1 w-full" aria-label={t('k_e7d5880a37')}>
                  <SelectValue placeholder={t('k_71a045b6fc')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_TEMPLATE}>{t('k_71a045b6fc')}</SelectItem>
                  {reportTemplates.map(t => (
                    <SelectItem key={t.name} value={t.name}>
                      {t.name}{t.usable ? '' : i18nTr('k_e87351d2d6')}
                    </SelectItem>
                  ))}
                  {reportTemplates.length === 0 && (
                    <SelectItem value="__empty__" disabled>{t('k_2f831213c2')}</SelectItem>
                  )}
                </SelectContent>
              </Select>
            )}
            <p className="text-xs text-muted-foreground mt-1">{t('k_7e456f6f56')}</p>
            {boundMissing && (
              <p className="mt-1 text-xs text-amber-600">{t('k_ec73638eec', { p0: form.report_template })}</p>
            )}
          </div>
          <div>
            <Label>{t('k_7584e655d3')}</Label>
            {/* 场景按主体授权：角色对哪个本体对象有数据源权限，才能看到/命中该场景。
                选择框从主体清单选（显示业务名），不让人手输 object_key。 */}
            <div className="mt-1 flex items-center gap-2">
              <Switch checked={form.subject_all}
                onCheckedChange={v => setForm(f => ({ ...f, subject_all: v, subjects: v ? [] : f.subjects }))} />
              <Label className="text-xs font-normal">{t('k_51607eff2b')}</Label>
            </div>
            <p className="text-xs text-muted-foreground mt-1">{t('k_2e63c0d8f5')}</p>
            {!form.subject_all && (
              subjectsError ? (
                <div className="mt-2 flex items-center gap-2 text-xs text-destructive">
                  {subjectsError}
                  <Button variant="outline" size="sm" className="h-6 text-xs" onClick={reloadSubjects}>{t('k_e2d53a6d3a')}</Button>
                </div>
              ) : (
                <div className="mt-2 flex flex-wrap gap-2 max-h-[150px] overflow-y-auto">
                  {subjects.map(s => {
                    const active = form.subjects.includes(s.key);
                    return (
                      <Badge key={s.key} variant={active ? 'default' : 'outline'}
                        className="cursor-pointer text-xs font-normal"
                        title={`${s.model_name} · ${s.key}`}
                        onClick={() => setForm(f => ({
                          ...f, subjects: active ? f.subjects.filter(x => x !== s.key) : [...f.subjects, s.key],
                        }))}>
                        {s.name}
                      </Badge>
                    );
                  })}
                  {subjects.length === 0 && (
                    <span className="text-xs text-muted-foreground">{t('k_44adf77702')}</span>
                  )}
                </div>
              )
            )}
          </div>
          <div>
            <Label>{t('k_7c13baa2c0')}</Label>
            <Textarea
              value={form.content}
              onChange={e => setForm(f => ({ ...f, content: e.target.value }))}
              placeholder={t('k_05c06b56ba')}
              rows={8}
            />
          </div>
          <div>
            <Label>{t('k_342aa303e4')}</Label>
            <Input
              value={form.related_tables}
              onChange={e => setForm(f => ({ ...f, related_tables: e.target.value }))}
              placeholder={t('k_1c502b6384')}
            />
          </div>
          <div>
            <Label>{t('k_7cabe771eb')}</Label>
            <Input
              type="number"
              value={form.priority}
              onChange={e => setForm(f => ({ ...f, priority: Number(e.target.value) }))}
              placeholder="0"
            />
            <p className="text-xs text-muted-foreground mt-1">{t('k_c472cf8e19')}</p>
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={form.is_active} onCheckedChange={v => setForm(f => ({ ...f, is_active: v }))} />
            <Label>{t('k_d4e9ca3dd4')}</Label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('k_4d0b4688c7')}</Button>
          <Button onClick={handleSave} disabled={saving || !canSubmit}>
            {saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}