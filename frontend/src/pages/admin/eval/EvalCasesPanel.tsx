/**
 * 用例管理面板：搜索 / 按能力契约分组 / 分页 + 结构化编辑表单。
 *
 * 相对旧版的两处实质改动：
 *  1. 表单按 (层, 能力契约) 预填 payload/expected 骨架，裸 JSON 文本框降级为
 *     「高级」折叠项。旧版把两个 JSON 文本框直接摊给运营同学，只能提交后被后端
 *     拒收才知道键名写错。
 *  2. 用例状态三态可标注，且 retired / known_defect 必须写原因（与后端同一口径）。
 *     删除不再是表达"这条用例针对的形态没了"的手段 —— 那会掩盖覆盖度流失。
 */
import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import PaginationBar from '@/components/PaginationBar';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import client from '@/api/client';
import {
  CASE_STATUS_LABEL, SUITE_LABEL, templateFor,
  type CapabilityMeta, type CaseStatus, EvalCase, type SuiteMeta,
} from './shared';

interface Props {
  suites: SuiteMeta[];
  capabilities: CapabilityMeta[];
  cases: EvalCase[];
  loading: boolean;
  error: string;
  suiteFilter: string;
  onSuiteFilter: (v: string) => void;
  /** 概览矩阵点击带入的预填目标 */
  preset: { suite: string; capability: string; nonce: number } | null;
  onChanged: () => void;
}

const EMPTY = {
  case_key: '', suite: 'compile', question: '', capability: '',
  case_status: 'active', status_note: '', tags: '',
  payload: '{}', expected: '{}', note: '', sort: 0,
};

const PAGE_SIZE_DEFAULT = 25;

export default function EvalCasesPanel({
  suites, capabilities, cases, loading, error, suiteFilter,
  onSuiteFilter, preset, onChanged,
}: Props) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [PAGE_SIZE, setPageSize] = useState(PAGE_SIZE_DEFAULT);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<EvalCase | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  // payload/expected 是否仍是模板原文：只有在没人改过时才随层/契约切换刷新，
  // 否则用户填一半就被模板盖掉，是比不给模板更坏的体验。
  const [pristine, setPristine] = useState(true);

  const openCreateWith = (suite: string, capability: string) => {
    const tpl = templateFor(suite, capability);
    setEditing(null);
    setForm({
      ...EMPTY, suite: (suite || 'compile') as any, capability: capability || '',
      payload: tpl?.payload || '{}', expected: tpl?.expected || '{}',
    });
    setPristine(Boolean(tpl));
    setAdvanced(!tpl);
    setFormOpen(true);
  };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return cases.filter(c => !q
      || c.question.toLowerCase().includes(q)
      || c.case_key.toLowerCase().includes(q)
      || (c.tags || '').toLowerCase().includes(q)
      || (c.capability || '').toLowerCase().includes(q));
  }, [cases, search]);

  const grouped = useMemo(() => {
    const m = new Map<string, EvalCase[]>();
    for (const c of filtered) {
      const k = c.capability || '未登记';
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(c);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  const capLabel = (k: string) =>
    capabilities.find(c => c.key === k)?.label || k;

  // 概览矩阵跳转：nonce 变化时消费一次（渲染期直接 setState 会触发 React 错误）
  useEffect(() => {
    if (preset) openCreateWith(preset.suite, preset.capability);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset?.nonce]);

  const openCreate = () => openCreateWith(suiteFilter || 'compile', '');

  const openEdit = (c: EvalCase) => {
    setEditing(c);
    setForm({
      case_key: c.case_key, suite: c.suite, question: c.question,
      capability: c.capability || '', case_status: c.case_status || 'active',
      status_note: c.status_note || '', tags: c.tags || '',
      payload: JSON.stringify(c.payload ?? {}, null, 2),
      expected: JSON.stringify(c.expected ?? {}, null, 2),
      note: c.note || '', sort: c.sort || 0,
    });
    setAdvanced(true);   // 编辑既有用例时默认露出 JSON，便于核对真实内容
    setFormOpen(true);
  };

  /** 换层/换契约时刷新模板；仅当两侧仍是无人改动的模板原文才替换。 */
  const applyTemplate = (suite: string, capability: string) => {
    const tpl = templateFor(suite, capability);
    setForm(f => {
      if (!tpl) return { ...f, suite, capability };
      const oldTpl = templateFor(f.suite, f.capability);
      const payloadUntouched = !oldTpl || (pristine ? true : f.payload === oldTpl.payload);
      const expectedUntouched = !oldTpl || (pristine ? true : f.expected === oldTpl.expected);
      return {
        ...f, suite, capability,
        payload: payloadUntouched ? tpl.payload : f.payload,
        expected: expectedUntouched ? tpl.expected : f.expected,
      };
    });
    if (!tpl) setPristine(false);
  };

  const saveCase = async () => {
    let payload: unknown, expected: unknown;
    try { payload = JSON.parse(form.payload || '{}'); }
    catch { toast.error('payload 不是合法 JSON'); return; }
    try { expected = JSON.parse(form.expected || '{}'); }
    catch { toast.error('expected 不是合法 JSON'); return; }

    const body = {
      case_key: form.case_key.trim(), suite: form.suite, question: form.question,
      capability: form.capability, case_status: form.case_status,
      status_note: form.status_note.trim(),
      tags: String(form.tags || '').split(',').map(s => s.trim()).filter(Boolean),
      payload, expected, note: form.note, sort: Number(form.sort) || 0,
      is_active: editing?.is_active ?? 1,
    };
    if (!body.case_key) { toast.error('用例标识不能为空'); return; }
    if (!body.question) { toast.error('问题不能为空'); return; }
    if (!body.capability) { toast.error('必须选择能力契约组（覆盖矩阵据此判缺口，不容空值混过）'); return; }
    if (body.case_status !== 'active' && !body.status_note) {
      toast.error('标为退役/已知缺陷必须写原因，否则等于把失败扫到地毯下');
      return;
    }

    setSaving(true);
    try {
      if (editing) await client.put(`/eval/cases/${editing.id}`, body);
      else await client.post('/eval/cases', body);
      toast.success(editing ? '用例已更新' : '用例已创建');
      setFormOpen(false);
      onChanged();
    } catch (e: any) {
      // 后端会明确回"用例不合法/标识已存在"，原样透出，不做静默降级
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (c: EvalCase) => {
    try {
      await client.delete(`/eval/cases/${c.id}`);
      toast.success('用例已删除（历史运行结果仍按 case_key 保留）');
      onChanged();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const toggleActive = async (c: EvalCase) => {
    try {
      await client.post(`/eval/cases/${c.id}/active`, null, { params: { active: !c.is_active } });
      onChanged();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '操作失败');
    }
  };

  const pageRows = (list: EvalCase[]) =>
    list.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const Row = (c: EvalCase) => (
    <tr key={c.id} className="border-t align-top">
      <td className="p-2 font-mono text-[10px] max-w-[180px] break-all" title={c.case_key}>
        {c.case_key}
      </td>
      <td className="p-2"><Badge variant="outline" className="text-[9px]">{SUITE_LABEL[c.suite]}</Badge></td>
      <td className="p-2 max-w-[360px]" title={c.question}>
        <div className="truncate">{c.question}</div>
        {c.note && <div className="text-[10px] text-muted-foreground truncate">{c.note}</div>}
      </td>
      <td className="p-2 text-muted-foreground text-[10px]">{c.tags}</td>
      <td className="p-2">
        <div className="flex flex-col gap-0.5">
          <Badge variant={c.is_active ? 'default' : 'outline'} className="text-[9px]">
            {c.is_active ? '启用' : '停用'}
          </Badge>
          {c.case_status !== 'active' && (
            <Badge variant="destructive" className="text-[9px]" title={c.status_note}>
              {CASE_STATUS_LABEL[c.case_status] || c.case_status}
            </Badge>
          )}
        </div>
      </td>
      <td className="p-2">
        <div className="flex gap-1">
          <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px]" onClick={() => openEdit(c)}>编辑</Button>
          <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px]" onClick={() => toggleActive(c)}>
            {c.is_active ? '停用' : '启用'}
          </Button>
          <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-destructive" onClick={() => remove(c)}>
            删除
          </Button>
        </div>
      </td>
    </tr>
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Label className="text-xs">评测层</Label>
        <Select value={suiteFilter || 'all'} onValueChange={v => { setPage(0); onSuiteFilter(v === 'all' ? '' : v); }}>
          <SelectTrigger className="w-[190px] h-8"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            {suites.map(s => <SelectItem key={s.suite} value={s.suite}>{s.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Input
          value={search} className="h-8 text-xs w-[240px]"
          onChange={e => { setSearch(e.target.value); setPage(0); }}
          placeholder="搜索问句 / 标识 / 标签 / 契约"
        />
        <Badge variant="outline" className="text-[10px]">
          {filtered.length} / {cases.length} 条
        </Badge>
        <div className="flex-1" />
        <Button size="sm" onClick={openCreate}><Plus className="h-3.5 w-3.5 mr-1" />新增用例</Button>
      </div>

      {loading && <div className="text-xs text-muted-foreground p-4">加载中…</div>}
      {!loading && error && (
        <div className="text-xs text-destructive p-4 border border-destructive/40 rounded">
          加载失败：{error}
        </div>
      )}
      {!loading && !error && filtered.length === 0 && (
        <div className="text-xs text-muted-foreground p-4 border rounded text-center">无用例</div>
      )}

      {grouped.map(([cap, list]) => {
        const isCollapsed = !!collapsed[cap];
        const failed = list.filter(c => c.case_status === 'known_defect').length;
        return (
          <div key={cap} className="border rounded-md">
            <button
              type="button"
              onClick={() => setCollapsed(s => ({ ...s, [cap]: !s[cap] }))}
              className="w-full px-3 py-2 flex items-center gap-2 text-sm bg-muted/30 hover:bg-muted/50"
            >
              {isCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              <span className="font-medium">{capLabel(cap)}</span>
              <span className="font-mono text-[10px] text-muted-foreground">{cap}</span>
              <Badge variant="outline" className="text-[10px]">{list.length}</Badge>
              {failed > 0 && (
                <Badge variant="destructive" className="text-[10px]">已知缺陷 {failed}</Badge>
              )}
            </button>
            {!isCollapsed && (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left border-t">
                      <th className="p-2">标识</th>
                      <th className="p-2">层</th>
                      <th className="p-2">问题 / 覆盖意图</th>
                      <th className="p-2">标签</th>
                      <th className="p-2">状态</th>
                      <th className="p-2 w-[170px]">操作</th>
                    </tr>
                  </thead>
                  <tbody>{pageRows(list).map(Row)}</tbody>
                </table>
                <PaginationBar
                  page={page + 1}
                  pageSize={PAGE_SIZE}
                  total={list.length}
                  onChange={(p: number) => setPage(Math.max(0, p - 1))}
                  onPageSizeChange={(n: number) => { setPageSize(n); setPage(0); }}
                  hideWhenSingle
                  className="rounded-b-lg"
                />
              </div>
            )}
          </div>
        );
      })}

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-2xl max-h-[88vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? `编辑用例 — ${editing.case_key}` : '新增评测用例'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="mb-1 block" htmlFor="f-case-key">用例标识（版本对比对齐用，勿随意改）</Label>
                <Input id="f-case-key" value={form.case_key} disabled={!!editing}
                  onChange={e => setForm({ ...form, case_key: e.target.value })}
                  placeholder="如 sem_gov_mask_applies_to_admin" />
              </div>
              <div>
                <Label className="mb-1 block" htmlFor="f-question">问题（golden question）</Label>
                <Input id="f-question" value={form.question} onChange={e => setForm({ ...form, question: e.target.value })} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="mb-1 block" htmlFor="f-suite">被测层</Label>
                <Select value={form.suite} onValueChange={v => applyTemplate(v, form.capability)}>
                  <SelectTrigger id="f-suite"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {suites.map(s => <SelectItem key={s.suite} value={s.suite}>{s.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                {suites.find(s => s.suite === form.suite)?.scope_note && (
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    {suites.find(s => s.suite === form.suite)!.scope_note}
                  </p>
                )}
              </div>
              <div>
                <Label className="mb-1 block" htmlFor="f-capability">能力契约组（必填）</Label>
                <Select value={form.capability || undefined}
                  onValueChange={v => applyTemplate(form.suite, v)}>
                  <SelectTrigger id="f-capability"><SelectValue placeholder="选一个能力契约组" /></SelectTrigger>
                  <SelectContent>
                    {capabilities.map(c => <SelectItem key={c.key} value={c.key}>{c.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="mb-1 block" htmlFor="f-status">用例状态</Label>
                <Select value={form.case_status}
                  onValueChange={v => setForm({ ...form, case_status: v as CaseStatus })}>
                  <SelectTrigger id="f-status"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Object.entries(CASE_STATUS_LABEL).map(([k, label]) => (
                      <SelectItem key={k} value={k}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="mb-1 block" htmlFor="f-tags">标签（逗号分隔，用于分桶报告）</Label>
                <Input id="f-tags" value={form.tags} onChange={e => setForm({ ...form, tags: e.target.value })}
                  placeholder="如 governance,sensitive_block" />
              </div>
            </div>

            {form.case_status !== 'active' && (
              <div>
                <Label className="mb-1 block" htmlFor="f-status-note">
                  {form.case_status === 'retired' ? '退役原因（必填）' : '缺陷描述（必填）'}
                </Label>
                <Textarea id="f-status-note" rows={2} value={form.status_note}
                  onChange={e => setForm({ ...form, status_note: e.target.value })}
                  placeholder="例：断言的形态已不存在 / 图种子抽取契约缺失，影响哪条链路" />
                {form.case_status === 'retired' && (
                  <p className="mt-1 text-[10px] text-amber-600">
                    退役后不再跑、不计入门禁分母；条数与原因会在发布前检查里常驻展示。
                  </p>
                )}
              </div>
            )}

            {/* 结构化入口：按层+契约预填骨架，人只改值 */}
            <div className="border rounded p-2 space-y-2 bg-muted/20">
              <div className="flex items-center gap-2">
                <b className="text-xs">用例内容</b>
                <Badge variant="outline" className="text-[10px]">
                  {templateFor(form.suite, form.capability) ? '已按层+契约预填模板' : '该组合暂无模板，请手写'}
                </Badge>
                <div className="flex-1" />
                <Button variant="ghost" size="sm" className="h-6 text-[11px]"
                  onClick={() => setAdvanced(a => !a)}>
                  {advanced ? '收起 JSON' : '编辑 JSON'}
                </Button>
              </div>
              {!advanced && templateFor(form.suite, form.capability) && (
                <pre className="text-[10px] font-mono whitespace-pre-wrap text-muted-foreground max-h-40 overflow-y-auto">
                  {`payload: ${templateFor(form.suite, form.capability)!.payload}\nexpected: ${templateFor(form.suite, form.capability)!.expected}`}
                </pre>
              )}
              {advanced && (
                <>
                  <div>
                    <Label className="mb-1 block" htmlFor="f-payload">payload（适配器入参 JSON）</Label>
                    <Textarea id="f-payload" rows={6} className="font-mono text-[11px]"
                      value={form.payload}
                      onChange={e => { setPristine(false); setForm({ ...form, payload: e.target.value }); }} />
                  </div>
                  <div>
                    <Label className="mb-1 block" htmlFor="f-expected">
                      expected（确定性断言 JSON）— 可用键：
                      <code className="ml-1 text-[10px] text-muted-foreground">
                        {(suites.find(s => s.suite === form.suite)?.expected_keys || []).join(' / ')}
                      </code>
                    </Label>
                    <Textarea id="f-expected" rows={6} className="font-mono text-[11px]"
                      value={form.expected}
                      onChange={e => { setPristine(false); setForm({ ...form, expected: e.target.value }); }} />
                  </div>
                </>
              )}
            </div>

            <div>
              <Label className="mb-1 block" htmlFor="f-note">覆盖意图说明（为什么要有这条用例）</Label>
              <Input id="f-note" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} />
            </div>
            <p id="f-no-self-proof" className="text-[10px] text-muted-foreground">
              期望键必须属于所选层，写错会被后端拒绝保存（不静默忽略）；
              期望值需人工判定，页面不代生成 —— 评测是评价 AI 的标尺，不能由工具自证。
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={saveCase} disabled={saving}>{saving ? '保存中…' : '保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
