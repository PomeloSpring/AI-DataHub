import { useState, useEffect, useCallback } from 'react';
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
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { Plus, Edit, Trash2, Workflow, Search, BookX } from 'lucide-react';
import client from '@/api/client';
import { useTranslation } from 'react-i18next';
import { t as i18nTr } from '@/i18n/runtime';

// ── Types ──────────────────────────────────────────────────────────

interface KnowledgeItem {
  id: number;
  workspace_id: number;
  datasource_id: number;
  knowledge_type: string;
  title: string;
  content: string;
  metadata: { triggers?: string[]; scenario?: string; subjects?: string[]; subject_all?: boolean;
    report_template?: string } | null;
  related_tables: string;
  priority: number;
  usage_count: number;
  positive_count: number;
  negative_count: number;
  is_active: number;
  created_at: string;
  updated_at: string;
  /** 服务端按主体权限派生的可见性标注（配置事实，不是运行时意外） */
  subject_keys?: string[];
  subject_names?: string[];
  subject_all?: boolean;
  visible_to_me?: boolean;
  visibility_note?: string;
}

/** 主体候选（本体对象）：选择框数据源，显示业务名、value 用内部 key。 */
interface SubjectOption { key: string; name: string; model_name: string; kind: string }

/**
 * 报告模板候选：只带业务名与形状，不带字模 id。
 * 场景绑定的口径就是名字（服务端按名字解析，id 随环境漂移），拿不到 id 是有意为之：
 * 递过去就会有人手填它（后端也会直接 422 拒 id）。
 */
interface ReportTemplateOption { name: string; format: string; usable: boolean; sections: number }

interface FormData {
  title: string;
  triggers: string;       // 逗号分隔的触发词
  scenario: string;       // 适用场景
  report_template: string; // 绑定的报告模板（业务名，命中该场景时按它的版式出报告）
  content: string;        // 查询步骤说明（注入 AI 取数上下文的正文）
  related_tables: string;
  subjects: string[];     // 绑定的主体（本体对象 key）
  subject_all: boolean;   // 不限主体（显式扩权给全部角色）
  priority: number;
  is_active: boolean;
}

const DEFAULT_FORM: FormData = {
  title: '',
  triggers: '',
  scenario: '',
  report_template: '',
  content: '',
  related_tables: '',
  subjects: [],
  subject_all: false,
  priority: 0,
  is_active: true,
};

const NO_TEMPLATE = '__none__';

const splitTriggers = (s: string): string[] =>
  s.split(/[,，]/).map(t => t.trim()).filter(Boolean);

export default function AnalysisScenarios() {
  const { t } = useTranslation('ui');
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editItem, setEditItem] = useState<KnowledgeItem | null>(null);
  const [form, setForm] = useState<FormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeItem | null>(null);
  // 主体候选清单与加载态：拉不到就不能让人手输 object_key（宁可显式报错重试）
  const [subjects, setSubjects] = useState<SubjectOption[]>([]);
  const [subjectsError, setSubjectsError] = useState('');
  // 报告模板候选与加载态：同一口径 —— 拉不到就显式报错重试，不让人手填模板名或 id
  const [reportTemplates, setReportTemplates] = useState<ReportTemplateOption[]>([]);
  const [reportTemplatesError, setReportTemplatesError] = useState('');

  const [PAGE_SIZE, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  // ── Load items ───────────────────────────────────────────────────

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = {
        workspace_id: 0,
        knowledge_type: 'quick_path',
        page,
        size: PAGE_SIZE,
      };
      if (searchText) params.search = searchText;
      const { data } = await client.get('/admin/knowledge', { params });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      // 404 = 暂无数据，不弹 toast
      if (e?.response?.status && e.response.status >= 500) {
        toast.error(t('k_866b795eae'));
      }
    } finally {
      setLoading(false);
    }
  }, [page, PAGE_SIZE, searchText]);

  useEffect(() => { loadItems(); }, [loadItems]);

  // 主体清单（本体对象）：服务端 GET /admin/knowledge/subjects，与权限判定同一数据源
  const loadSubjects = useCallback(async () => {
    setSubjectsError('');
    try {
      const { data } = await client.get('/admin/knowledge/subjects');
      setSubjects(Array.isArray(data?.items) ? data.items : []);
    } catch (e: any) {
      setSubjectsError(e?.response?.data?.detail || t('k_16740df890'));
    }
  }, []);

  useEffect(() => { loadSubjects(); }, [loadSubjects]);

  // 报告模板候选：走本域 `/admin/knowledge/report-templates`（配场景的读码就能拿），
  // 不走 `/reports/templates` —— 那要「报告模板」菜单权限，配场景的人不一定有。
  const loadReportTemplates = useCallback(async () => {
    setReportTemplatesError('');
    try {
      const { data } = await client.get('/admin/knowledge/report-templates');
      setReportTemplates(Array.isArray(data?.items) ? data.items : []);
    } catch (e: any) {
      setReportTemplatesError(e?.response?.data?.detail || t('k_56025ae1f0'));
    }
  }, []);

  useEffect(() => { loadReportTemplates(); }, [loadReportTemplates]);

  // ── Form handling ────────────────────────────────────────────────

  const openCreate = () => {
    setEditItem(null);
    setForm({ ...DEFAULT_FORM });
    setFormOpen(true);
  };

  const openEdit = (item: KnowledgeItem) => {
    setEditItem(item);
    setForm({
      title: item.title,
      triggers: (item.metadata?.triggers || []).join(', '),
      scenario: item.metadata?.scenario || '',
      report_template: item.metadata?.report_template || '',
      content: item.content,
      related_tables: item.related_tables || '',
      subjects: item.metadata?.subjects || [],
      subject_all: !!item.metadata?.subject_all,
      priority: item.priority,
      is_active: !!item.is_active,
    });
    setFormOpen(true);
  };

  // 当前绑定值不在候选里（模板被下架/改名）：必须当场看得见，不能从下拉里静默消失
  // 让人以为“没绑过”，保存时又被服务端拦下却不知道为什么。
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
        // 主体授权的唯一声明：服务端拿它算“哪些角色能看见/能命中”
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

      if (editItem) {
        await client.put(`/admin/knowledge/${editItem.id}`, payload);
        toast.success(t('k_1b92092d63'));
      } else {
        await client.post('/admin/knowledge', payload);
        toast.success(t('k_2d4c5dcc74'));
      }
      setFormOpen(false);
      loadItems();
    } catch (e: any) {
      // 必须把服务端原因原样递出去（422 “主体不存在/未声明主体”等），
      // 统一成“保存失败”会把可诊断的口径问题变成猜谜
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/admin/knowledge/${deleteTarget.id}`);
      toast.success(t('k_5cc232620c'));
      setDeleteTarget(null);
      loadItems();
    } catch {
      toast.error(t('k_acf0664a54'));
    }
  };

  const handleToggle = async (item: KnowledgeItem) => {
    try {
      await client.patch(`/admin/knowledge/${item.id}/toggle`);
      loadItems();
    } catch {
      toast.error(t('k_c2312c491e'));
    }
  };

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-6 shrink-0">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Workflow className="h-6 w-6" />
            {t('k_b2be89d785')}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t('k_faf550d2a6')}
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1" />{t('k_5bd56c5159')}
        </Button>
      </div>

      {/* Search */}
      <div className="mb-4 shrink-0">
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder={t('k_e0cba2043d')}
            value={searchText}
            onChange={e => { setSearchText(e.target.value); setPage(1); }}
            className="pl-8 w-64"
          />
        </div>
      </div>

      {/* 清单：分页表格（不用卡片式）。列只留“能扫读”的关键信息，
          触发词与查询步骤正文不进列表——它们是一整段配置详情，平铺在清单里
          会把两三条场景撑成整屏，反而看不清有哪些场景（去编辑弹窗里看）。 */}
      <div className="flex-1 min-h-0 overflow-auto">
      {loading ? (
        <div className="text-center py-8 text-muted-foreground">{t('k_26b5bd4947')}</div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <BookX className="h-10 w-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">{t("k_dc1a608775")}</p>
          <p className="text-xs mt-1">{t("k_0697d54ecb")}</p>
        </div>
      ) : (
        <div className="rounded-lg border bg-card">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_7b345ebc76")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_4731a17e52")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[90px]">{t("k_7cabe771eb")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[80px]">{t("k_d4e9ca3dd4")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[70px]">{t("k_7a130d7fde")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[160px]">{t("k_093dea88c9")}</th>
                <th className="h-10 px-4 text-right text-sm font-medium text-muted-foreground w-[90px]">{t("k_f3ea6d345e")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => {
                const noTrigger = splitTriggers((item.metadata?.triggers || []).join(',')).length === 0;
                const subjectText = item.subject_all ? t('k_5216e74601')
                  : (item.subject_names || []).join(t('k_b505660a97')) || t('k_737f192a0d');
                return (
                  <tr key={item.id} className="border-b last:border-b-0 hover:bg-muted/30">
                    <td className="px-4 py-2 min-w-0">
                      <div className="flex items-center gap-2">
                        <Workflow className="h-3.5 w-3.5 shrink-0 text-primary" />
                        <span className="text-sm font-medium truncate" title={item.title}>{item.title}</span>
                      </div>
                      {noTrigger && (
                        <p className="mt-0.5 pl-5 text-xs text-amber-600">{t("k_acf7bde256")}</p>
                      )}
                      {/* 绑了哪个报告模板属于配置事实，要能在清单里扫到（不然只能逐条开弹窗看） */}
                      {item.metadata?.report_template && (
                        <p className="mt-0.5 pl-5 text-xs text-muted-foreground"
                          title={t("k_3aeb0a262d")}>
                          {t('k_08ff53ef7d', { p0: item.metadata.report_template })}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-2 min-w-0">
                      <span className="text-sm" title={t('k_65e7ca7c61')}>{subjectText}</span>
                      {/* 不可见原因属于配置事实，必须当场看见（而不是到问答里发现“怎么没生效”） */}
                      {item.visible_to_me === false && item.visibility_note && (
                        <p className="mt-0.5 text-xs text-amber-600">{item.visibility_note}</p>
                      )}
                    </td>
                    <td className="px-4 py-2 text-sm tabular-nums">{item.priority}</td>
                    <td className="px-4 py-2">
                      <Switch checked={!!item.is_active} onCheckedChange={() => handleToggle(item)} />
                    </td>
                    <td className="px-4 py-2 text-sm tabular-nums text-muted-foreground" title={t("k_728e7f3434")}>
                      {item.usage_count}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">{item.updated_at}</td>
                    <td className="px-4 py-2">
                      <div className="flex items-center justify-end gap-1">
                        <Button variant="ghost" size="sm" title={t("k_1958a21d26")} onClick={() => openEdit(item)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" title={t("k_aa8aa282e6")} onClick={() => setDeleteTarget(item)}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      </div>

      {/* Pagination —— 统一分页条：固定底栏（仅清单区滚动，页头/分页不随页面滚走） */}
      <PaginationBar
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        busy={loading}
        onChange={(p: number) => setPage(p)}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
        hideWhenSingle
        className="shrink-0 mt-2"
      />

      {/* ── Create/Edit Dialog ──────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{editItem ? t('k_a3ff0d0814') : t('k_5bd56c5159')}</DialogTitle>
            <DialogDescription>
              {t('k_6266d2b2f8')}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
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
              <p className="text-xs text-muted-foreground mt-1">{t("k_1d107b19d6")}</p>
            </div>
            <div>
              <Label>{t("k_3b05370cd8")}</Label>
              <Input
                value={form.scenario}
                onChange={e => setForm(f => ({ ...f, scenario: e.target.value }))}
                placeholder={t("k_9a95855d69")}
              />
            </div>
            <div>
              <Label>{t("k_2f531daf33")}</Label>
              {/* 场景绑定模板 = 命中该场景时按模板版式出报告。选择框只给业务名（候选走本域接口），
                  不让人手填字模 id：id 随环境漂移，“绑了个读不到的模板”最后会变成问答里的
                  静默回落（管理员看见“已绑定”，用户看见“没套用”）。 */}
              {reportTemplatesError ? (
                <div className="mt-1 flex items-center gap-2 text-xs text-destructive">
                  {reportTemplatesError}
                  <Button variant="outline" size="sm" className="h-6 text-xs"
                    onClick={loadReportTemplates}>{t("k_e2d53a6d3a")}</Button>
                </div>
              ) : (
                <Select value={form.report_template || NO_TEMPLATE}
                  onValueChange={v => setForm(f => ({
                    ...f, report_template: v === NO_TEMPLATE ? '' : v,
                  }))}>
                  <SelectTrigger className="mt-1 w-full" aria-label={t("k_e7d5880a37")}>
                    <SelectValue placeholder={t("k_71a045b6fc")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_TEMPLATE}>{t("k_71a045b6fc")}</SelectItem>
                    {reportTemplates.map(t => (
                      <SelectItem key={t.name} value={t.name}>
                        {t.name}{t.usable ? '' : i18nTr('k_e87351d2d6')}
                      </SelectItem>
                    ))}
                    {reportTemplates.length === 0 && (
                      <SelectItem value="__empty__" disabled>{t("k_2f831213c2")}</SelectItem>
                    )}
                  </SelectContent>
                </Select>
              )}
              <p className="text-xs text-muted-foreground mt-1">
                {t('k_7e456f6f56')}
              </p>
              {boundMissing && (
                <p className="mt-1 text-xs text-amber-600">
                  {t('k_ec73638eec', { p0: form.report_template })}</p>
              )}
            </div>
            <div>
              <Label>{t('k_7584e655d3')}</Label>
              {/* 场景按主体授权：角色对哪个本体对象有数据源权限，才能看到/命中该场景。
                  选择框从主体清单选（显示业务名），不让人手输 object_key。 */}
              <div className="mt-1 flex items-center gap-2">
                <Switch checked={form.subject_all}
                  onCheckedChange={v => setForm(f => ({ ...f, subject_all: v, subjects: v ? [] : f.subjects }))} />
                <Label className="text-xs font-normal">{t("k_51607eff2b")}</Label>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {t('k_2e63c0d8f5')}
              </p>
              {!form.subject_all && (
                subjectsError ? (
                  <div className="mt-2 flex items-center gap-2 text-xs text-destructive">
                    {subjectsError}
                    <Button variant="outline" size="sm" className="h-6 text-xs" onClick={loadSubjects}>{t("k_e2d53a6d3a")}</Button>
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
                      <span className="text-xs text-muted-foreground">{t("k_44adf77702")}</span>
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
                placeholder={t("k_05c06b56ba")}
                rows={5}
              />
            </div>
            <div>
              <Label>{t("k_342aa303e4")}</Label>
              <Input
                value={form.related_tables}
                onChange={e => setForm(f => ({ ...f, related_tables: e.target.value }))}
                placeholder={t('k_1c502b6384')}
              />
            </div>
            <div>
              <Label>{t("k_7cabe771eb")}</Label>
              <Input
                type="number"
                value={form.priority}
                onChange={e => setForm(f => ({ ...f, priority: Number(e.target.value) }))}
                placeholder="0"
              />
              <p className="text-xs text-muted-foreground mt-1">{t("k_c472cf8e19")}</p>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={form.is_active}
                onCheckedChange={v => setForm(f => ({ ...f, is_active: v }))}
              />
              <Label>{t("k_d4e9ca3dd4")}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {t('k_5851dad150', { p0: deleteTarget?.title })}</DialogDescription>
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
