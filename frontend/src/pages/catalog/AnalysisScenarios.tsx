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
        toast.error('加载失败');
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
      setSubjectsError(e?.response?.data?.detail || '主体清单加载失败，请重试');
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
      setReportTemplatesError(e?.response?.data?.detail || '报告模板清单加载失败，请重试');
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
      toast.error('请填写场景名称和查询步骤说明');
      return;
    }
    if (splitTriggers(form.triggers).length === 0) {
      toast.error('请至少配置一个触发词');
      return;
    }
    // 主体范围必须显式声明：服务端也会 422 拒，但前端先拦一层，不让用户白点一次保存
    if (!form.subject_all && form.subjects.length === 0) {
      toast.error('请至少选择一个主体，或勾选「不限主体」——未声明主体的场景对所有角色都不会命中');
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
        toast.success('已更新');
      } else {
        await client.post('/admin/knowledge', payload);
        toast.success('已创建');
      }
      setFormOpen(false);
      loadItems();
    } catch (e: any) {
      // 必须把服务端原因原样递出去（422 “主体不存在/未声明主体”等），
      // 统一成“保存失败”会把可诊断的口径问题变成猜谜
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/admin/knowledge/${deleteTarget.id}`);
      toast.success('已删除');
      setDeleteTarget(null);
      loadItems();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (item: KnowledgeItem) => {
    try {
      await client.patch(`/admin/knowledge/${item.id}/toggle`);
      loadItems();
    } catch {
      toast.error('切换状态失败');
    }
  };

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-6 shrink-0">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Workflow className="h-6 w-6" />
            分析场景
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            用 Markdown 描述的场景化查询步骤，本质是对已建模的对象/指标/维度的编排。
            用户问题命中触发词时，这份说明会注入 AI 取数上下文（相当于针对本系统的定制版技能），
            所以改动会直接影响取数行为。
            可见范围按主体授权：角色对场景所绑本体对象的数据源有权限，才能看到并命中它。
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1" />新增分析场景
        </Button>
      </div>

      {/* Search */}
      <div className="mb-4 shrink-0">
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索场景名称或步骤..."
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
        <div className="text-center py-8 text-muted-foreground">加载中...</div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <BookX className="h-10 w-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">暂无分析场景</p>
          <p className="text-xs mt-1">点击上方按钮创建，命中触发词后将方案注入 AI 上下文</p>
        </div>
      ) : (
        <div className="rounded-lg border bg-card">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">场景名称</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">主体（可见范围）</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[90px]">优先级</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[80px]">启用</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[70px]">命中</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[160px]">更新时间</th>
                <th className="h-10 px-4 text-right text-sm font-medium text-muted-foreground w-[90px]">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => {
                const noTrigger = splitTriggers((item.metadata?.triggers || []).join(',')).length === 0;
                const subjectText = item.subject_all ? '不限主体（所有角色可见）'
                  : (item.subject_names || []).join('、') || '未绑定主体';
                return (
                  <tr key={item.id} className="border-b last:border-b-0 hover:bg-muted/30">
                    <td className="px-4 py-2 min-w-0">
                      <div className="flex items-center gap-2">
                        <Workflow className="h-3.5 w-3.5 shrink-0 text-primary" />
                        <span className="text-sm font-medium truncate" title={item.title}>{item.title}</span>
                      </div>
                      {noTrigger && (
                        <p className="mt-0.5 pl-5 text-xs text-amber-600">未配置触发词，该场景不会被命中</p>
                      )}
                      {/* 绑了哪个报告模板属于配置事实，要能在清单里扫到（不然只能逐条开弹窗看） */}
                      {item.metadata?.report_template && (
                        <p className="mt-0.5 pl-5 text-xs text-muted-foreground"
                          title="命中该场景时按此模板的版式出报告">
                          报告模板：{item.metadata.report_template}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-2 min-w-0">
                      <span className="text-sm" title={`场景按所绑主体授权：角色对该主体有数据源权限才能看到与命中它`}>{subjectText}</span>
                      {/* 不可见原因属于配置事实，必须当场看见（而不是到问答里发现“怎么没生效”） */}
                      {item.visible_to_me === false && item.visibility_note && (
                        <p className="mt-0.5 text-xs text-amber-600">{item.visibility_note}</p>
                      )}
                    </td>
                    <td className="px-4 py-2 text-sm tabular-nums">{item.priority}</td>
                    <td className="px-4 py-2">
                      <Switch checked={!!item.is_active} onCheckedChange={() => handleToggle(item)} />
                    </td>
                    <td className="px-4 py-2 text-sm tabular-nums text-muted-foreground" title="命中次数（服务端按触发词命中累加）">
                      {item.usage_count}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">{item.updated_at}</td>
                    <td className="px-4 py-2">
                      <div className="flex items-center justify-end gap-1">
                        <Button variant="ghost" size="sm" title="编辑（含触发词与查询步骤正文）" onClick={() => openEdit(item)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" title="删除该场景" onClick={() => setDeleteTarget(item)}>
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
            <DialogTitle>{editItem ? '编辑分析场景' : '新增分析场景'}</DialogTitle>
            <DialogDescription>
              配置场景名称、触发词与查询步骤说明；命中后这份说明会注入 AI 取数上下文
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>场景名称 *</Label>
              <Input
                value={form.title}
                onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
                placeholder="如: 按月统计销售额"
              />
            </div>
            <div>
              <Label>触发词 *（逗号分隔）</Label>
              <Input
                value={form.triggers}
                onChange={e => setForm(f => ({ ...f, triggers: e.target.value }))}
                placeholder="如: 销售额, GMV, 成交金额"
              />
              <p className="text-xs text-muted-foreground mt-1">用户问题命中任一触发词即套用本场景步骤</p>
            </div>
            <div>
              <Label>适用场景</Label>
              <Input
                value={form.scenario}
                onChange={e => setForm(f => ({ ...f, scenario: e.target.value }))}
                placeholder="简述这类问题何时出现（可选）"
              />
            </div>
            <div>
              <Label>报告模板（可选）</Label>
              {/* 场景绑定模板 = 命中该场景时按模板版式出报告。选择框只给业务名（候选走本域接口），
                  不让人手填字模 id：id 随环境漂移，“绑了个读不到的模板”最后会变成问答里的
                  静默回落（管理员看见“已绑定”，用户看见“没套用”）。 */}
              {reportTemplatesError ? (
                <div className="mt-1 flex items-center gap-2 text-xs text-destructive">
                  {reportTemplatesError}
                  <Button variant="outline" size="sm" className="h-6 text-xs"
                    onClick={loadReportTemplates}>重试</Button>
                </div>
              ) : (
                <Select value={form.report_template || NO_TEMPLATE}
                  onValueChange={v => setForm(f => ({
                    ...f, report_template: v === NO_TEMPLATE ? '' : v,
                  }))}>
                  <SelectTrigger className="mt-1 w-full" aria-label="报告模板">
                    <SelectValue placeholder="不绑定（按通用报告骨架出稿）" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_TEMPLATE}>不绑定（按通用报告骨架出稿）</SelectItem>
                    {reportTemplates.map(t => (
                      <SelectItem key={t.name} value={t.name}>
                        {t.name}{t.usable ? '' : '（未登记版式，只有示例正文）'}
                      </SelectItem>
                    ))}
                    {reportTemplates.length === 0 && (
                      <SelectItem value="__empty__" disabled>没有可用模板（可先到「报告模板」配置页新建）</SelectItem>
                    )}
                  </SelectContent>
                </Select>
              )}
              <p className="text-xs text-muted-foreground mt-1">
                绑定后：命中本场景的回合会把该模板的章节与图位写成版式契约交给 AI（数值仍一律取本轮查询）；
                模板被下架/读不到时会在回答里显式说明，不会默默换个版式。
              </p>
              {boundMissing && (
                <p className="mt-1 text-xs text-amber-600">
                  当前绑定的「{form.report_template}」不在可用模板清单里（可能已下架或改名），请换一个或改为不绑定。
                </p>
              )}
            </div>
            <div>
              <Label>主体范围 *</Label>
              {/* 场景按主体授权：角色对哪个本体对象有数据源权限，才能看到/命中该场景。
                  选择框从主体清单选（显示业务名），不让人手输 object_key。 */}
              <div className="mt-1 flex items-center gap-2">
                <Switch checked={form.subject_all}
                  onCheckedChange={v => setForm(f => ({ ...f, subject_all: v, subjects: v ? [] : f.subjects }))} />
                <Label className="text-xs font-normal">不限主体（所有角色都能命中该场景）</Label>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                勾选下方主体 = 只有拿到该数据源权限的角色能用这个场景；选「不限主体」是显式扩权，
                请确认这份查询步骤对所有角色都合适。
              </p>
              {!form.subject_all && (
                subjectsError ? (
                  <div className="mt-2 flex items-center gap-2 text-xs text-destructive">
                    {subjectsError}
                    <Button variant="outline" size="sm" className="h-6 text-xs" onClick={loadSubjects}>重试</Button>
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
                      <span className="text-xs text-muted-foreground">未获取到主体清单（请先到「本体建模」建好对象）</span>
                    )}
                  </div>
                )
              )}
            </div>
            <div>
              <Label>查询步骤说明（Markdown）*</Label>
              <Textarea
                value={form.content}
                onChange={e => setForm(f => ({ ...f, content: e.target.value }))}
                placeholder="告诉 AI 该如何查询：涉及的表/对象、口径、推荐步骤、示例 SQL 或查询条件"
                rows={5}
              />
            </div>
            <div>
              <Label>关联表名</Label>
              <Input
                value={form.related_tables}
                onChange={e => setForm(f => ({ ...f, related_tables: e.target.value }))}
                placeholder="逗号分隔，如: orders,users（可选）"
              />
            </div>
            <div>
              <Label>优先级</Label>
              <Input
                type="number"
                value={form.priority}
                onChange={e => setForm(f => ({ ...f, priority: Number(e.target.value) }))}
                placeholder="0"
              />
              <p className="text-xs text-muted-foreground mt-1">命中同级时越高越优先注入</p>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={form.is_active}
                onCheckedChange={v => setForm(f => ({ ...f, is_active: v }))}
              />
              <Label>启用</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除 "{deleteTarget?.title}" 吗？此操作不可撤销。
            </DialogDescription>
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
