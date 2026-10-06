import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import { Plus, Edit, Trash2, Play, FlaskConical, GitCompare, ShieldCheck, RefreshCw } from 'lucide-react';
import client from '@/api/client';

type Suite = 'compile' | 'retrieval' | 'llm';

interface SuiteMeta {
  suite: Suite;
  label: string;
  expected_keys: string[];
}

interface EvalCase {
  id: number;
  case_key: string;
  suite: Suite;
  question: string;
  tags: string;
  payload: Record<string, unknown>;
  expected: Record<string, unknown>;
  note: string;
  is_active: number;
  sort: number;
}

interface EvalRun {
  id: number;
  run_key: string;
  suite: Suite | 'all';
  trigger_type: string;
  status: string;
  total: number;
  passed: number;
  accuracy: number;
  baseline_ok: number;
  error: string;
  finished_at: string | null;
  created_at: string;
}

interface RunResult {
  case_key: string;
  suite: Suite;
  passed: number;
  reason: string;
  score: number | null;
  sources: Record<string, string>;
  duration_ms: number;
}

interface CompareResult {
  run_id: number;
  baseline_run_id: number | null;
  verdict: string;
  baseline_ok: boolean;
  regression: Record<string, { from: string; to: string; reason: string }>;
  coverage_loss: string[];
  fixed: string[];
  new: string[];
  removed: string[];
  baseline_accuracy: number | null;
  current_accuracy: number | null;
}

const SUITE_LABEL: Record<string, string> = {
  compile: '语义层编译',
  retrieval: '本体层检索',
  llm: 'LLM 功能',
  all: '全部',
};

const EMPTY_FORM = {
  case_key: '', suite: 'compile' as Suite, question: '', tags: '',
  payload: '{}', expected: '{}', note: '', sort: 0,
};

/**
 * 校验接口返回结构。
 *
 * 为什么必须校验：vite 代理是显式路径白名单，漏配的前缀会被 SPA 兑底成
 * index.html（200 + HTML），axios 拿到的 `data` 是字符串，
 * `data.items || []` 会把它掩盖成“空列表”——页面看起来正常但永远没数据。
 * 这里遇到非预期结构直接抛错，让错误显式冒出来。
 */
function expectList(payload: any, what: string): any[] {
  if (!payload || !Array.isArray(payload.items)) {
    const kind = typeof payload;
    throw new Error(
      `接口返回结构异常（期望 items 数组，实际 ${kind}）：${what}。` +
      '多半是 /api/eval 代理未配置，请求被 SPA 兑底成页面 HTML。',
    );
  }
  return payload.items;
}

export default function EvalManagement() {
  const [tab, setTab] = useState('cases');
  const [suites, setSuites] = useState<SuiteMeta[]>([]);
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [suiteFilter, setSuiteFilter] = useState<string>('');
  const [loading, setLoading] = useState(false);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<EvalCase | null>(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);

  const [runDetail, setRunDetail] = useState<{ run: EvalRun; results: RunResult[] } | null>(null);
  const [cmp, setCmp] = useState<CompareResult | null>(null);
  const [precheck, setPrecheck] = useState<any>(null);
  const [compareBase, setCompareBase] = useState('');
  const [loadError, setLoadError] = useState('');

  const loadSuites = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/suites');
      if (!data || !Array.isArray(data.suites)) {
        throw new Error('接口返回结构异常（期望 suites 数组）。多半是 /api/eval 代理未配置。');
      }
      setSuites(data.suites);
      setLoadError('');
    } catch (e: any) {
      setLoadError(e.message || e.response?.data?.detail || '加载评测集定义失败');
      toast.error(e.message || e.response?.data?.detail || '加载评测集定义失败');
    }
  }, []);

  const loadCases = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/eval/cases', {
        params: { suite: suiteFilter, include_inactive: true },
      });
      // 不用 `data.items || []`：那样会把“接口挂了/返回 HTML”掩盖成空列表
      setCases(expectList(data, '评测用例'));
      setLoadError('');
    } catch (e: any) {
      setLoadError(e.message || e.response?.data?.detail || '加载用例失败');
      toast.error(e.message || e.response?.data?.detail || '加载用例失败');
    } finally {
      setLoading(false);
    }
  }, [suiteFilter]);

  const loadRuns = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/runs', { params: { limit: 50 } });
      setRuns(expectList(data, '评测运行记录'));
      setLoadError('');
    } catch (e: any) {
      setLoadError(e.message || e.response?.data?.detail || '加载运行记录失败');
      toast.error(e.message || e.response?.data?.detail || '加载运行记录失败');
    }
  }, []);

  const loadPrecheck = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/precheck');
      if (!data || typeof data.suites !== 'object') {
        throw new Error('接口返回结构异常（期望 suites 对象）。多半是 /api/eval 代理未配置。');
      }
      setPrecheck(data);
      setLoadError('');
    } catch (e: any) {
      setLoadError(e.message || e.response?.data?.detail || '加载发布前检查失败');
      toast.error(e.message || e.response?.data?.detail || '加载发布前检查失败');
    }
  }, []);

  useEffect(() => {
    loadSuites();
    loadRuns();
    loadPrecheck();
  }, [loadSuites, loadRuns, loadPrecheck]);
  useEffect(() => { loadCases(); }, [loadCases]);

  // ── 用例 CRUD ───────────────────────────────────────────────────────
  const openCreate = () => {
    setEditing(null);
    setForm({ ...EMPTY_FORM, suite: (suiteFilter as Suite) || 'compile' });
    setFormOpen(true);
  };
  const openEdit = (c: EvalCase) => {
    setEditing(c);
    setForm({
      case_key: c.case_key, suite: c.suite, question: c.question,
      tags: c.tags || '', payload: JSON.stringify(c.payload ?? {}, null, 2),
      expected: JSON.stringify(c.expected ?? {}, null, 2), note: c.note || '', sort: c.sort || 0,
    });
    setFormOpen(true);
  };

  const saveCase = async () => {
    let payload: any, expected: any;
    try { payload = JSON.parse(form.payload || '{}'); }
    catch { toast.error('payload 不是合法 JSON'); return; }
    try { expected = JSON.parse(form.expected || '{}'); }
    catch { toast.error('expected 不是合法 JSON'); return; }

    const body = {
      case_key: form.case_key.trim(),
      suite: form.suite,
      question: form.question,
      tags: form.tags.split(',').map(s => s.trim()).filter(Boolean),
      payload, expected, note: form.note, sort: Number(form.sort) || 0,
      is_active: editing?.is_active ?? 1,
    };
    if (!body.case_key) { toast.error('用例标识不能为空'); return; }
    if (!body.question) { toast.error('问题不能为空'); return; }

    setSaving(true);
    try {
      if (editing) {
        await client.put(`/eval/cases/${editing.id}`, body);
        toast.success('用例已更新');
      } else {
        await client.post('/eval/cases', body);
        toast.success('用例已创建');
      }
      setFormOpen(false);
      loadCases();
    } catch (e: any) {
      // 后端会明确回"用例不合法"/"标识已存在"，原样透出，不做静默降级
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const deleteCase = async (c: EvalCase) => {
    try {
      await client.delete(`/eval/cases/${c.id}`);
      toast.success('用例已删除（历史运行结果仍保留）');
      loadCases();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const toggleActive = async (c: EvalCase) => {
    try {
      await client.post(`/eval/cases/${c.id}/active`, null, { params: { active: !c.is_active } });
      loadCases();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '操作失败');
    }
  };

  // ── 运行 ───────────────────────────────────────────────────────────
  const triggerRun = async (suite: string, mode: 'sync' | 'async') => {
    try {
      const { data } = await client.post('/eval/runs', { suite, mode, trigger_type: 'manual' });
      if (mode === 'async') {
        toast.success(data.note || '已投递评测任务');
      } else {
        toast.success('评测完成');
        if (data.report) {
          const rep = data.report.suites ? data.report.suites[suite] : data.report;
          if (rep) {
            toast.info(`${SUITE_LABEL[rep.suite] || rep.suite}：${rep.passed}/${rep.total}，${rep.verdict}`);
          }
        }
      }
      loadRuns();
      loadPrecheck();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '触发评测失败');
    }
  };

  const openRun = async (run: EvalRun) => {
    try {
      const { data } = await client.get(`/eval/runs/${run.id}/results`);
      setRunDetail({ run, results: data.items || [] });
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '加载结果失败');
    }
  };

  const doCompare = async (run: EvalRun) => {
    try {
      const { data } = await client.get(`/eval/runs/${run.id}/compare`, {
        params: compareBase ? { baseline_id: compareBase } : {},
      });
      setCmp(data);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '版本对比失败');
    }
  };

  const metaFor = (s: string) => suites.find(x => x.suite === s);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2">
        <FlaskConical className="h-5 w-5" />
        <h1 className="text-lg font-semibold">评测中心</h1>
        <Badge variant="outline" className="text-[10px]">
          用例存库 · 页面可增删改查
        </Badge>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => { loadCases(); loadRuns(); loadPrecheck(); }}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" />刷新
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        评测集三个维度：<b>语义层编译</b>（intent→binding→plan 确定性断言）、
        <b>本体层检索</b>（真跑 GraphRAG 等检索策略，按检索来源分桶）、
        <b>LLM 功能</b>（工具调用轨迹 / 出站脱敏 / 涉密拒绝）。
        通过与否由确定性断言决定，LLM 评分只辅助评价解释质量。
        两类分桶语义不同：编译集是解析档位，检索集是检索来源，不能互相背书。
      </p>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-3 w-full max-w-xl">
          <TabsTrigger value="cases"><Plus className="h-3.5 w-3.5 mr-1" />用例管理</TabsTrigger>
          <TabsTrigger value="runs"><Play className="h-3.5 w-3.5 mr-1" />评测运行</TabsTrigger>
          <TabsTrigger value="compare"><GitCompare className="h-3.5 w-3.5 mr-1" />版本对比</TabsTrigger>
        </TabsList>

        {/* ── 用例管理 ───────────────────────────────────────────── */}
        <TabsContent value="cases" className="space-y-3">
          <div className="flex items-center gap-2">
            <Label className="text-xs">评测集</Label>
            <Select value={suiteFilter || 'all'} onValueChange={(v) => setSuiteFilter(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-[180px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部</SelectItem>
                {suites.map(s => <SelectItem key={s.suite} value={s.suite}>{s.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <div className="flex-1" />
            <Button size="sm" onClick={openCreate}><Plus className="h-3.5 w-3.5 mr-1" />新增用例</Button>
          </div>

          <div className="border rounded">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/40 text-left">
                  <th className="p-2">标识</th>
                  <th className="p-2">评测集</th>
                  <th className="p-2">问题</th>
                  <th className="p-2">标签</th>
                  <th className="p-2">状态</th>
                  <th className="p-2 w-[140px]">操作</th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">加载中…</td></tr>}
                {!loading && loadError && (
                  <tr><td colSpan={6} className="p-4 text-center text-destructive">
                    加载失败：{loadError}
                  </td></tr>
                )}
                {!loading && !loadError && cases.length === 0 && (
                  <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">暂无用例</td></tr>
                )}
                {cases.map(c => (
                  <tr key={c.id} className="border-t align-top">
                    <td className="p-2 font-mono text-[10px]">{c.case_key}</td>
                    <td className="p-2"><Badge variant="outline" className="text-[9px]">{SUITE_LABEL[c.suite]}</Badge></td>
                    <td className="p-2 max-w-[320px] truncate" title={c.question}>{c.question}</td>
                    <td className="p-2 text-muted-foreground">{c.tags}</td>
                    <td className="p-2">
                      <Badge variant={c.is_active ? 'default' : 'outline'} className="text-[9px]">
                        {c.is_active ? '启用' : '停用'}
                      </Badge>
                    </td>
                    <td className="p-2">
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" className="h-6 px-1.5" onClick={() => openEdit(c)}>
                          <Edit className="h-3 w-3" />
                        </Button>
                        <Button variant="ghost" size="sm" className="h-6 px-1.5" onClick={() => toggleActive(c)}>
                          {c.is_active ? '停用' : '启用'}
                        </Button>
                        <Button variant="ghost" size="sm" className="h-6 px-1.5 text-destructive"
                          onClick={() => deleteCase(c)}>
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TabsContent>

        {/* ── 评测运行 ───────────────────────────────────────────── */}
        <TabsContent value="runs" className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => triggerRun('all', 'sync')}>
              <Play className="h-3.5 w-3.5 mr-1" />跑全部（同步）
            </Button>
            <Button size="sm" variant="outline" onClick={() => triggerRun('all', 'async')}>
              丢给 Celery 跑全部
            </Button>
            <div className="w-px h-5 bg-border" />
            {suites.map(s => (
              <Button key={s.suite} size="sm" variant="ghost"
                onClick={() => triggerRun(s.suite, s.suite === 'compile' ? 'sync' : 'async')}>
                {s.label}
              </Button>
            ))}
          </div>

          <div className="border rounded">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/40 text-left">
                  <th className="p-2">ID</th>
                  <th className="p-2">评测集</th>
                  <th className="p-2">触发</th>
                  <th className="p-2">状态</th>
                  <th className="p-2">通过</th>
                  <th className="p-2">正确率</th>
                  <th className="p-2">基线</th>
                  <th className="p-2">完成时间</th>
                  <th className="p-2 w-[100px]">操作</th>
                </tr>
              </thead>
              <tbody>
                {runs.length === 0 && (
                  <tr><td colSpan={9} className="p-4 text-center text-muted-foreground">暂无运行记录</td></tr>
                )}
                {runs.map(r => (
                  <tr key={r.id} className="border-t">
                    <td className="p-2 font-mono">{r.id}</td>
                    <td className="p-2">{SUITE_LABEL[r.suite] || r.suite}</td>
                    <td className="p-2 text-muted-foreground">{r.trigger_type}</td>
                    <td className="p-2">
                      <Badge variant={r.status === 'done' ? 'default' : r.status === 'failed' ? 'destructive' : 'outline'}
                        className="text-[9px]">{r.status}</Badge>
                    </td>
                    <td className="p-2">{r.passed}/{r.total}</td>
                    <td className="p-2">{(Number(r.accuracy) * 100).toFixed(1)}%</td>
                    <td className="p-2">
                      {r.status !== 'done' ? '—' : (
                        <Badge variant={r.baseline_ok ? 'secondary' : 'destructive'} className="text-[9px]">
                          {r.baseline_ok ? '未回退' : '回退'}
                        </Badge>
                      )}
                    </td>
                    <td className="p-2 text-muted-foreground">{r.finished_at || '—'}</td>
                    <td className="p-2">
                      <Button variant="ghost" size="sm" className="h-6 px-1.5" onClick={() => openRun(r)}>
                        结果
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {runDetail && (
            <div className="border rounded p-3 space-y-2">
              <div className="flex items-center gap-2">
                <b className="text-xs">运行 #{runDetail.run.id} 逐用例结果</b>
                <Badge variant="outline" className="text-[9px]">{SUITE_LABEL[runDetail.run.suite]}</Badge>
                <div className="flex-1" />
                <Button variant="ghost" size="sm" className="h-6" onClick={() => setRunDetail(null)}>收起</Button>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-muted/40 text-left">
                    <th className="p-1.5">用例</th>
                    <th className="p-1.5">结果</th>
                    <th className="p-1.5">原因 / 归因分桶</th>
                  </tr>
                </thead>
                <tbody>
                  {runDetail.results.map(r => (
                    <tr key={r.case_key} className="border-t align-top">
                      <td className="p-1.5 font-mono text-[10px]">{r.case_key}</td>
                      <td className="p-1.5">
                        <Badge variant={r.passed ? 'secondary' : 'destructive'} className="text-[9px]">
                          {r.passed ? '通过' : '失败'}
                        </Badge>
                        {r.score != null && <span className="ml-1 text-muted-foreground">评分 {r.score}</span>}
                      </td>
                      <td className="p-1.5">
                        {r.reason && <div className="text-destructive">{r.reason}</div>}
                        {Object.keys(r.sources || {}).length > 0 && (
                          <div className="text-muted-foreground">
                            {Object.entries(r.sources).map(([k, v]) => `${k}=${v}`).join(' · ')}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </TabsContent>

        {/* ── 版本对比 / 发布前检查 ──────────────────────────────── */}
        <TabsContent value="compare" className="space-y-3">
          <div className="border rounded p-3 space-y-2">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" />
              <b className="text-xs">发布前检查</b>
              <div className="flex-1" />
              <Button variant="outline" size="sm" className="h-6" onClick={loadPrecheck}>刷新</Button>
            </div>
            {precheck ? (
              <>
                <div className={`text-sm font-medium ${precheck.release_ok ? 'text-green-600' : 'text-destructive'}`}>
                  {precheck.verdict}
                </div>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-muted/40 text-left">
                      <th className="p-1.5">评测集</th>
                      <th className="p-1.5">最近运行</th>
                      <th className="p-1.5">通过</th>
                      <th className="p-1.5">正确率</th>
                      <th className="p-1.5">基线判定</th>
                    </tr>
                  </thead>
                  <tbody>
                    {suites.map(s => {
                      const v = precheck.suites?.[s.suite];
                      return (
                        <tr key={s.suite} className="border-t">
                          <td className="p-1.5">{s.label}</td>
                          <td className="p-1.5">{v?.has_run ? `#${v.run_id}` : '—'}</td>
                          <td className="p-1.5">{v?.has_run ? `${v.passed}/${v.total}` : '—'}</td>
                          <td className="p-1.5">{v?.has_run ? `${(Number(v.accuracy) * 100).toFixed(1)}%` : '—'}</td>
                          <td className="p-1.5">
                            {!v?.has_run ? <span className="text-muted-foreground">无运行</span> : (
                              <Badge variant={v.baseline_ok ? 'secondary' : 'destructive'} className="text-[9px]">
                                {v.baseline_ok ? '通过' : '回退'}
                              </Badge>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">加载中…</p>
            )}
          </div>

          <div className="border rounded p-3 space-y-2">
            <div className="flex items-center gap-2">
              <GitCompare className="h-4 w-4" />
              <b className="text-xs">版本对比</b>
            </div>
            <div className="flex items-center gap-2">
              <Label className="text-xs">基线运行</Label>
              <Select value={compareBase || 'auto'} onValueChange={(v) => setCompareBase(v === 'auto' ? '' : v)}>
                <SelectTrigger className="w-[220px]"><SelectValue placeholder="自动（最近一次完成）" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">自动（最近一次完成）</SelectItem>
                  {runs.filter(r => r.status === 'done').map(r => (
                    <SelectItem key={r.id} value={String(r.id)}>
                      #{r.id} {SUITE_LABEL[r.suite] || r.suite} · {(Number(r.accuracy) * 100).toFixed(0)}%
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-[10px] text-muted-foreground">选中运行后点其行内「结果」旁的对比按钮</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {runs.filter(r => r.status === 'done').slice(0, 12).map(r => (
                <Button key={r.id} size="sm" variant="outline" className="h-6 text-[10px]"
                  onClick={() => doCompare(r)}>
                  对比 #{r.id}（{SUITE_LABEL[r.suite] || r.suite}）
                </Button>
              ))}
            </div>

            {cmp && (
              <div className="border rounded p-2 space-y-1.5">
                <div className={`text-sm font-medium ${cmp.baseline_ok ? 'text-green-600' : 'text-destructive'}`}>
                  {cmp.verdict}
                </div>
                <div className="text-xs text-muted-foreground">
                  正确率 {cmp.baseline_accuracy != null ? `${(cmp.baseline_accuracy * 100).toFixed(1)}%` : '—'}
                  {' → '}{cmp.current_accuracy != null ? `${(cmp.current_accuracy * 100).toFixed(1)}%` : '—'}
                  {' '}（基线 #{cmp.baseline_run_id ?? '无'}）
                </div>
                {Object.keys(cmp.regression || {}).length > 0 && (
                  <div className="text-xs">
                    <b className="text-destructive">回退：</b>
                    {Object.entries(cmp.regression).map(([k, v]) => (
                      <div key={k} className="pl-2 text-destructive">{k}：{v.from}→{v.to}（{v.reason}）</div>
                    ))}
                  </div>
                )}
                {cmp.coverage_loss?.length > 0 && (
                  <div className="text-xs text-amber-600">
                    覆盖度流失（基线通过但用例已删）：{cmp.coverage_loss.join('、')}
                  </div>
                )}
                {cmp.fixed?.length > 0 && (
                  <div className="text-xs text-green-600">修好：{cmp.fixed.join('、')}</div>
                )}
                {cmp.new?.length > 0 && (
                  <div className="text-xs text-muted-foreground">新增：{cmp.new.join('、')}</div>
                )}
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>

      {/* ── 用例编辑对话框 ───────────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editing ? `编辑用例 — ${editing.case_key}` : '新增评测用例'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="mb-1 block">用例标识（跨版本对比用，勿随意改）</Label>
                <Input value={form.case_key} disabled={!!editing}
                  onChange={e => setForm({ ...form, case_key: e.target.value })}
                  placeholder="如 retrieval_no_physical_leak" />
              </div>
              <div>
                <Label className="mb-1 block">评测集</Label>
                <Select value={form.suite} onValueChange={(v: Suite) => setForm({ ...form, suite: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {suites.map(s => <SelectItem key={s.suite} value={s.suite}>{s.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label className="mb-1 block">问题（golden question）</Label>
              <Input value={form.question} onChange={e => setForm({ ...form, question: e.target.value })} />
            </div>
            <div>
              <Label className="mb-1 block">标签（逗号分隔，用于分桶报告）</Label>
              <Input value={form.tags} onChange={e => setForm({ ...form, tags: e.target.value })}
                placeholder="如 retrieval,no_leak" />
            </div>
            <div>
              <Label className="mb-1 block">payload（执行适配器入参，JSON）</Label>
              <Textarea rows={5} className="font-mono text-[11px]"
                value={form.payload} onChange={e => setForm({ ...form, payload: e.target.value })} />
            </div>
            <div>
              <Label className="mb-1 block">
                expected（确定性断言，JSON）— 可用键：
                <code className="ml-1 text-[10px] text-muted-foreground">
                  {(metaFor(form.suite)?.expected_keys || []).join(' / ')}
                </code>
              </Label>
              <Textarea rows={5} className="font-mono text-[11px]"
                value={form.expected} onChange={e => setForm({ ...form, expected: e.target.value })} />
            </div>
            <div>
              <Label className="mb-1 block">覆盖意图说明</Label>
              <Input value={form.note} onChange={e => setForm({ ...form, note: e.target.value })}
                placeholder="为什么要有这条用例" />
            </div>
            <p className="text-[10px] text-muted-foreground">
              提示：expected 的键必须属于所选评测集，写错会被拒绝保存（不会静默忽略）。
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
