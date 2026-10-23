/**
 * EvolutionCenter — 自进化闭环的人工审核台。
 *
 * 一页看完四件事，缺一样这个闭环就不成立：
 *  1. 反馈大盘：赞/踩与待归纳负反馈有多少（采集不上来，后面全是空谈）；
 *  2. 候选看板：按处置方向分列（待开发指标/维度、待收编别名、待核绑定、术语），
 *     每条都显示**分类依据**与证据问句 —— 不显示依据的候选等于让人猜；
 *  3. 出口动作：收编别名 / 建指标草稿 / 建维度草稿 / 转术语 / 转评测用例 / 驳回。
 *     选择框一律显示业务 name（数据源、对象、业务属性），不让人手输内部 id 或物理列；
 *  4. 闭环成效：已转出的用例是真过了（verified）还是仍失败（stale）。
 *     "转出去了"不等于"修好了"，stale 必须红着显示，否则闭环自欺。
 */
import { useState, useEffect, useCallback } from 'react';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from '@/lib/toast';
import { Dna, RefreshCw, Play, Check, X } from 'lucide-react';
import client from '@/api/client';

type Kind = 'new_metric' | 'new_dimension' | 'alias' | 'binding_gap' | 'term';

interface Candidate {
  id: number;
  term: string;
  kind: Kind;
  source: string;
  datasource_name: string;
  object_hint?: string;
  target_ref?: string;
  target_type?: string;
  classify_reason: string;
  hit_count: number;
  status: string;
  converted_ref?: string;
  converted_kind?: string;
  decided_by?: string;
  evidence?: Record<string, unknown>;
}

interface Overview {
  pending_by_kind: Record<string, number>;
  pending_by_source: Record<string, number>;
  outcome: Record<string, number>;
  feedback: { up: number; down: number; pending_negative: number };
  reasons: string[];
  kinds: Kind[];
  sources: string[];
}

const KIND_LABEL: Record<Kind, string> = {
  new_metric: '待开发指标',
  new_dimension: '待开发维度',
  alias: '待收编别名',
  binding_gap: '待核绑定',
  term: '术语候选',
};

const KIND_HINT: Record<Kind, string> = {
  new_metric: '字典里确实没有这个度量，需要新建',
  new_dimension: '字典里确实没有这个分析维度，需要新建',
  alias: '概念已存在，只是叫法没登记为别名 —— 收编即可，不要新建重复行',
  binding_gap: '字典行已存在但对本对象不可达（target_table/bound_object_key 与对象主表不一致）—— 去核对绑定，补别名或新建都没用',
  term: '纯业务黑话，尚无归属，先进术语缓冲区待建模',
};

const SOURCE_LABEL: Record<string, string> = {
  parse_fail: '解析失败回流',
  feedback: '用户点踩反馈',
  script: '术语收编脚本',
};

const STATUS_LABEL: Record<string, string> = {
  pending: '待处理', converted: '已执行', rejected: '已驳回',
  stale: '已转出但未生效', verified: '已验证生效',
};

/** 校验接口返回结构：代理未配时 vite 会把请求兑底成 index.html，
 *  `data.items || []` 会把故障掩盖成"空列表"，这里显式抛错。 */
function expectItems(payload: any, what: string): any[] {
  if (!payload || !Array.isArray(payload.items)) {
    throw new Error(`接口返回结构异常（期望 items 数组，实际 ${typeof payload}）：${what}`);
  }
  return payload.items;
}

export default function EvolutionCenter() {
  const [tab, setTab] = useState('pending');
  const [status, setStatus] = useState('pending');
  const [kindFilter, setKindFilter] = useState<string>('');
  const [items, setItems] = useState<Candidate[]>([]);
  const [ov, setOv] = useState<Overview | null>(null);
  const [suites, setSuites] = useState<{ suite: string; label: string }[]>([]);
  const [capabilities, setCapabilities] = useState<{ key: string; label: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [digesting, setDigesting] = useState(false);

  // 出口对话框
  const [target, setTarget] = useState<Candidate | null>(null);
  const [exitKind, setExitKind] = useState<'' | 'alias' | 'metric' | 'dimension' | 'term' | 'eval_case'>('');
  const [busy, setBusy] = useState(false);
  // 数据源不从选择框选：候选自带 datasource_name（来自解析失败时的会话源或反馈 tags），
  // 让人重选一次反而会造成"候选归属"与"审核人随手选"两个口径。
  const [objOptions, setObjOptions] = useState<{ key: string; display_name: string }[]>([]);
  const [propOptions, setPropOptions] = useState<{ property: string; column_name: string }[]>([]);
  const [form, setForm] = useState<any>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await client.get('/evolution/candidates', {
        params: { status, kind: kindFilter, limit: 300 },
      });
      setItems(expectItems(data, '进化候选'));
    } catch (e: any) {
      const msg = e.message || e.response?.data?.detail || '加载候选失败';
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [status, kindFilter]);

  const loadOverview = useCallback(async () => {
    try {
      const { data } = await client.get('/evolution/overview');
      if (!data || typeof data.pending_by_kind !== 'object') {
        throw new Error('接口返回结构异常（期望 pending_by_kind 对象）');
      }
      setOv(data);
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || '加载进化大盘失败');
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    loadOverview();
    void Promise.all([client.get('/eval/suites')]).then(([s]) => {
      setSuites((s.data?.suites || []).map((x: any) => ({ suite: x.suite, label: x.label })));
      setCapabilities(s.data?.capabilities || []);
    }).catch(() => {
      // 评测层清单拉不到只影响"转评测用例"这一个出口，显式告知而不是静默空白
      toast.warning('评测层清单未能加载，"转评测用例"出口暂不可用');
    });
  }, [loadOverview]);

  const runDigest = async () => {
    setDigesting(true);
    try {
      const { data } = await client.post('/evolution/digest');
      toast.success(`归纳完成：${data.summary || '无摘要'}`);
      load(); loadOverview();
    } catch (e: any) {
      // 归纳失败必须显式：把"没有新待办"和"归纳挂了"混在一起是最危险的掩盖
      toast.error(e.response?.data?.detail || '归纳未完成，详见服务端日志');
    } finally {
      setDigesting(false);
    }
  };

  const openExit = async (c: Candidate, exit: typeof exitKind) => {
    setTarget(c); setExitKind(exit); setObjOptions([]); setPropOptions([]);
    setForm({
      target_type: c.target_type || (c.kind === 'new_metric' ? 'metric' : 'dimension'),
      target_ref: c.target_ref || '',
      datasource_name: c.datasource_name || '',
      bound_object_key: c.object_hint || '',
      property_name: '', display_name: c.term, formula: '', agg_type: 'COUNT', unit: '',
      category: '属性', suite: 'semantic', capability: c.kind === 'binding_gap' ? 'governance' : 'resolution',
      expected: '{"allowed": true}', note: `由进化候选 #${c.id}（${c.term}）固化`,
    });
    if (exit === 'metric' || exit === 'dimension') {
      try {
        const { data } = await client.get('/evolution/objects', {
          params: { datasource_name: c.datasource_name || '' },
        });
        setObjOptions(expectItems(data, '可归属对象'));
      } catch (e: any) {
        toast.error(e.message || '对象清单加载失败');
      }
    }
  };

  const loadProps = async (objectKey: string) => {
    setForm((f: any) => ({ ...f, bound_object_key: objectKey, property_name: '' }));
    setPropOptions([]);
    if (!objectKey || !target) return;
    try {
      const { data } = await client.get('/evolution/properties', {
        params: { datasource_name: target.datasource_name || '', object_key: objectKey },
      });
      setPropOptions(expectItems(data, '业务属性清单'));
    } catch (e: any) {
      toast.error(e.message || '业务属性清单加载失败');
    }
  };

  const submit = async () => {
    if (!target) return;
    setBusy(true);
    try {
      if (exitKind === 'alias') {
        const { data } = await client.post(`/evolution/candidates/${target.id}/alias`, {
          target_type: form.target_type, target_ref: form.target_ref,
        });
        // 目标字典行不存在不是错误，是"该走另一个出口"：据此改按钮而不是报错
        if (data?.success === false && data?.next_action) {
          toast.warning(data.message || '目标不存在，请改走"建草稿"出口');
          setExitKind('');
          openExit({ ...target, kind: data.next_action as Kind },
            data.next_action === 'new_metric' ? 'metric' : 'dimension');
          return;
        }
        toast.success('别名已收编并触发级联同步');
      } else if (exitKind === 'metric') {
        const { data } = await client.post(`/evolution/candidates/${target.id}/metric-draft`, {
          bound_object_key: form.bound_object_key, display_name: form.display_name,
          agg_type: form.agg_type, formula: form.formula, unit: form.unit,
        });
        toast.success(data.message || '指标草稿已创建');
        if (data?.needs_completion?.length) {
          toast.warning(`还需补全：${data.needs_completion.join('、')}（未补前不会进语义层）`);
        }
      } else if (exitKind === 'dimension') {
        if (!form.property_name) { toast.error('请选择业务属性（不手填物理列名）'); return; }
        const { data } = await client.post(`/evolution/candidates/${target.id}/dimension-draft`, {
          bound_object_key: form.bound_object_key, property_name: form.property_name,
          display_name: form.display_name, category: form.category,
        });
        toast.success(data.message || '维度草稿已创建');
      } else if (exitKind === 'term') {
        const { data } = await client.post(`/evolution/candidates/${target.id}/to-term`, {
          description: form.note || '',
        });
        toast.success(data.message || '已转入术语缓冲区');
      } else if (exitKind === 'eval_case') {
        let expected: object;
        try { expected = JSON.parse(form.expected || '{}'); }
        catch { toast.error('expected 不是合法 JSON'); return; }
        const { data } = await client.post(`/evolution/candidates/${target.id}/to-eval-case`, {
          suite: form.suite, capability: form.capability, question: target.term,
          payload: { intent: { object: form.bound_object_key || target.object_hint || '',
                               metrics: [target.term], limit: 10 } },
          expected, note: form.note,
        });
        toast.success(`已登记评测用例 ${data.case_key}，下次运行后回填 verified/stale`);
      }
      setTarget(null); setExitKind('');
      load(); loadOverview();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '出口动作未完成');
    } finally {
      setBusy(false);
    }
  };

  const reject = async (c: Candidate) => {
    try {
      await client.post(`/evolution/candidates/${c.id}/reject`);
      toast.success('已驳回（保留留痕，便于看出该类信号是否被反复判无效）');
      load(); loadOverview();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '驳回失败');
    }
  };

  const grouped = (kind: Kind) => items.filter(i => i.kind === kind);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Dna className="h-5 w-5" />
        <h1 className="text-lg font-semibold">进化中心</h1>
        <Badge variant="outline" className="text-[10px]">
          反馈 / 解析失败 → 归纳候选 → 人工四出口 → 评测回归验证
        </Badge>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => { load(); loadOverview(); }}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" />刷新
        </Button>
        <Button size="sm" onClick={runDigest} disabled={digesting}>
          <Play className="h-3.5 w-3.5 mr-1" />{digesting ? '归纳中…' : '立即归纳'}
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        每日 03:30 自动归纳（可在「任务监控」暂停内置任务 <code>evolution_digest</code>）。
        每条候选都带<b>分类依据</b>：命中权威名却解析不通的词被判为
        <b>待核绑定</b>而非缺资产 —— 给这类词补别名或另建字典行都是错行动。
      </p>

      {ov && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">反馈大盘</div>
            <div className="mt-1 font-medium">
              👍 {ov.feedback.up} · 👎 {ov.feedback.down}
            </div>
            <div className={ov.feedback.pending_negative ? 'text-amber-600' : 'text-muted-foreground'}>
              待归纳负反馈 {ov.feedback.pending_negative} 条
            </div>
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">信号来源</div>
            {Object.keys(ov.pending_by_source).length === 0 && (
              <div className="text-muted-foreground mt-1">无待办信号</div>
            )}
            {Object.entries(ov.pending_by_source).map(([k, n]) => (
              <div key={k} className="mt-0.5">{SOURCE_LABEL[k] || k}：{n}</div>
            ))}
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">处置分布（待处理）</div>
            {ov.kinds.map(k => (
              <div key={k} className="mt-0.5 flex items-center justify-between">
                <span>{KIND_LABEL[k as Kind]}</span>
                <span className="font-medium">{ov.pending_by_kind[k] ?? 0}</span>
              </div>
            ))}
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">闭环成效</div>
            <div className="mt-1 text-green-700">已验证生效 {ov.outcome.verified ?? 0}</div>
            <div className={ov.outcome.stale ? 'text-destructive font-medium' : 'text-muted-foreground'}>
              转出但未生效 {ov.outcome.stale ?? 0}
            </div>
            <div className="text-muted-foreground">已执行 {ov.outcome.converted ?? 0} · 已驳回 {ov.outcome.rejected ?? 0}</div>
          </div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-2 w-full max-w-sm">
          <TabsTrigger value="pending">待处理候选</TabsTrigger>
          <TabsTrigger value="history">处置历史</TabsTrigger>
        </TabsList>

        <TabsContent value="pending" className="space-y-3">
          <div className="flex items-center gap-2">
            <Label className="text-xs">类型筛选</Label>
            <Select value={kindFilter || 'all'} onValueChange={v => setKindFilter(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-[180px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部</SelectItem>
                {Object.entries(KIND_LABEL).map(([k, label]) => (
                  <SelectItem key={k} value={k}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {loading && <div className="text-xs text-muted-foreground p-4">加载中…</div>}
          {!loading && error && (
            <div className="text-xs text-destructive p-4 border border-destructive/40 rounded">
              加载失败：{error}
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="text-xs text-muted-foreground p-4 border rounded text-center">
              该状态无候选。若刚上线自进化，需要先用一段时间积累点踩反馈与解析失败词，
              或点「立即归纳」消费历史反馈。
            </div>
          )}

          {(Object.keys(KIND_LABEL) as Kind[]).filter(k => !kindFilter || k === kindFilter)
            .map(kind => grouped(kind).length === 0 ? null : (
              <div key={kind} className="border rounded-lg">
                <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
                  {KIND_LABEL[kind]}
                  <Badge variant="outline" className="text-[11px]">{grouped(kind).length}</Badge>
                  <span className="text-[11px] text-muted-foreground font-normal">{KIND_HINT[kind]}</span>
                </div>
                <div>
                  {grouped(kind).map(c => (
                    <div key={c.id} className="px-3 py-2.5 border-b last:border-0 text-sm space-y-1.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium">{c.term}</span>
                        <Badge variant="outline" className="text-[10px]">
                          {c.datasource_name || '全局/未定源'}
                        </Badge>
                        <Badge variant="secondary" className="text-[10px]">命中 {c.hit_count}</Badge>
                        <Badge variant="outline" className="text-[10px]">
                          {SOURCE_LABEL[c.source] || c.source}
                        </Badge>
                        {c.target_ref && (
                          <span className="text-[11px] text-muted-foreground">≈ {c.target_ref}</span>
                        )}
                        <div className="flex-1" />
                        {kind === 'alias' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => openExit(c, 'alias')}>
                            收编别名
                          </Button>
                        )}
                        {kind === 'new_metric' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => openExit(c, 'metric')}>
                            <Check className="h-3 w-3 mr-1" />建指标草稿
                          </Button>
                        )}
                        {kind === 'new_dimension' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => openExit(c, 'dimension')}>
                            <Check className="h-3 w-3 mr-1" />建维度草稿
                          </Button>
                        )}
                        {kind === 'term' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => openExit(c, 'term')}>
                            转术语
                          </Button>
                        )}
                        {kind === 'binding_gap' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => { setTarget(c); setExitKind('eval_case'); }}>
                            固化成用例
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                          onClick={() => { setTarget(c); setExitKind(''); setForm({
                            bound_object_key: c.object_hint || '', target_type: 'dimension',
                            capability: 'resolution', suite: 'semantic',
                            expected: '{"allowed": true}', note: `由候选 #${c.id} 固化`,
                          }); }}>
                          转评测用例
                        </Button>
                        <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                          onClick={() => reject(c)}>
                          <X className="h-3 w-3 mr-1" />驳回
                        </Button>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        <b>分类依据：</b>{c.classify_reason}
                      </div>
                      {Boolean((c.evidence as any)?.question) && (
                        <div className="text-[11px] text-muted-foreground">
                          证据问句：{String((c.evidence as any).question)}
                        </div>
                      )}
                      {Array.isArray((c.evidence as any)?.candidates) &&
                        ((c.evidence as any).candidates as string[]).length > 0 && (
                        <div className="text-[11px] text-muted-foreground">
                          近似候选：
                          {((c.evidence as any).candidates as string[]).slice(0, 5).join('、')}
                        </div>
                      )}
                      {Array.isArray((c.evidence as any)?.authoritative_hits) &&
                        ((c.evidence as any).authoritative_hits as any[]).length > 0 && (
                        <div className="text-[11px] text-amber-700">
                          字典现况：
                          {((c.evidence as any).authoritative_hits as any[])
                            .map(h => `${h.ref} → 表 ${h.target_table || '?'} / 对象 ${h.object_key || '?'}`)
                            .join('；')}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
        </TabsContent>

        <TabsContent value="history" className="space-y-3">
          <div className="flex items-center gap-2">
            <Label className="text-xs">状态</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-[180px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(STATUS_LABEL).map(([k, label]) => (
                  <SelectItem key={k} value={k}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-[11px] text-muted-foreground">
              「已转出但未生效」是红字项：候选转成评测用例后该用例仍失败，
              说明缺口没真补上 —— 这一栏不许被"已执行"糊过去。
            </span>
          </div>
          <div className="border rounded">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/40 text-left">
                  <th className="p-2">业务词</th>
                  <th className="p-2">类型</th>
                  <th className="p-2">数据源</th>
                  <th className="p-2">状态</th>
                  <th className="p-2">产出物</th>
                  <th className="p-2">决策人</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && !loading && (
                  <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">无记录</td></tr>
                )}
                {items.map(c => (
                  <tr key={c.id} className="border-t">
                    <td className="p-2">{c.term}</td>
                    <td className="p-2">{KIND_LABEL[c.kind] || c.kind}</td>
                    <td className="p-2">{c.datasource_name || '—'}</td>
                    <td className="p-2">
                      <Badge variant={c.status === 'stale' ? 'destructive'
                        : c.status === 'verified' ? 'default' : 'outline'} className="text-[9px]">
                        {STATUS_LABEL[c.status] || c.status}
                      </Badge>
                    </td>
                    <td className="p-2 text-muted-foreground">
                      {c.converted_ref ? `${c.converted_kind}：${c.converted_ref}` : '—'}
                    </td>
                    <td className="p-2 text-muted-foreground">{c.decided_by || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TabsContent>
      </Tabs>

      <Dialog open={!!exitKind} onOpenChange={(o) => { if (!o) { setExitKind(''); setTarget(null); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {target && `${exitKind === 'alias' ? '收编别名' : exitKind === 'metric' ? '建指标草稿'
                : exitKind === 'dimension' ? '建维度草稿' : exitKind === 'term' ? '转术语缓冲区'
                  : '转评测用例'} — ${target.term}`}
            </DialogTitle>
          </DialogHeader>
          {target && (
            <div className="space-y-3 text-sm">
              <div className="text-[11px] text-muted-foreground border-l-2 border-border pl-2">
                {target.classify_reason}
              </div>

              {exitKind === 'alias' && (
                <>
                  <div>
                    <Label className="mb-1 block">目标类型</Label>
                    <Select value={form.target_type} onValueChange={v => setForm({ ...form, target_type: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="metric">指标</SelectItem>
                        <SelectItem value="dimension">维度</SelectItem>
                        <SelectItem value="object">本体对象</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="mb-1 block">收编到哪个已有名称（必须已存在）</Label>
                    <Input value={form.target_ref}
                      onChange={e => setForm({ ...form, target_ref: e.target.value })}
                      placeholder={'从上方「≈ xxx」提示里取，或填已登记的字典名 / 对象 key'} />
                  </div>
                </>
              )}

              {(exitKind === 'metric' || exitKind === 'dimension' || exitKind === 'eval_case') && (
                <div>
                  <Label className="mb-1 block">归属到本模型对象</Label>
                  <Select value={form.bound_object_key || undefined} onValueChange={loadProps}>
                    <SelectTrigger><SelectValue placeholder="选择业务对象" /></SelectTrigger>
                    <SelectContent>
                      {objOptions.map(o => (
                        <SelectItem key={o.key} value={o.key}>{o.display_name || o.key}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {objOptions.length === 0 && (
                    <p className="mt-1 text-[11px] text-amber-600">
                      该数据源下没有可归属的 active 本体对象。此时不应新建字典行（会立刻成为
                      资产池里的孤儿待办），请先在本体建模里补对象。
                    </p>
                  )}
                </div>
              )}

              {exitKind === 'metric' && (
                <>
                  <div>
                    <Label className="mb-1 block">业务名</Label>
                    <Input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} />
                  </div>
                  <div>
                    <Label className="mb-1 block">聚合方式 / 口径公式</Label>
                    <div className="flex gap-2">
                      <Select value={form.agg_type} onValueChange={v => setForm({ ...form, agg_type: v })}>
                        <SelectTrigger className="w-[140px]"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {['COUNT', 'SUM', 'AVG', 'MAX', 'MIN', 'COUNT_DISTINCT'].map(a => (
                            <SelectItem key={a} value={a}>{a}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Input value={form.formula} onChange={e => setForm({ ...form, formula: e.target.value })}
                        placeholder="如 COUNT(CASE WHEN del_flag = 0 THEN id END)；留空则存为待补口径草稿" />
                    </div>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    草稿以未启用状态入库：没口径的指标 planner 编译不了，
                    启用了只会让取数静默降级。补完 formula 再在指标中心启用。
                  </p>
                </>
              )}

              {exitKind === 'dimension' && (
                <>
                  <div>
                    <Label className="mb-1 block">业务名</Label>
                    <Input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} />
                  </div>
                  <div>
                    <Label className="mb-1 block">对应业务属性（物理列由服务端映射，不手填）</Label>
                    <Select value={form.property_name || undefined}
                      onValueChange={v => setForm({ ...form, property_name: v })}>
                      <SelectTrigger><SelectValue placeholder="先选对象，再选业务属性" /></SelectTrigger>
                      <SelectContent>
                        {propOptions.map(p => (
                          <SelectItem key={p.property} value={p.property}>{p.property}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </>
              )}

              {exitKind === 'term' && (
                <div>
                  <Label className="mb-1 block">说明（可选）</Label>
                  <Textarea rows={2} value={form.note} onChange={e => setForm({ ...form, note: e.target.value })}
                    placeholder="这个黑话在业务里指什么；后续要映射到哪个对象/字典" />
                </div>
              )}

              {exitKind === 'eval_case' && (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label className="mb-1 block">评测层</Label>
                      <Select value={form.suite} onValueChange={v => setForm({ ...form, suite: v })}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {suites.map(s => <SelectItem key={s.suite} value={s.suite}>{s.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label className="mb-1 block">能力契约</Label>
                      <Select value={form.capability} onValueChange={v => setForm({ ...form, capability: v })}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {capabilities.map(c => <SelectItem key={c.key} value={c.key}>{c.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div>
                    <Label className="mb-1 block">expected（确定性断言，JSON）</Label>
                    <Textarea rows={3} className="font-mono text-[11px]"
                      value={form.expected} onChange={e => setForm({ ...form, expected: e.target.value })} />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    期望值请人据此判断填写，不由系统猜测；归纳作业会在该用例下次运行后
                    回填 verified / stale。
                  </p>
                </>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setExitKind(''); setTarget(null); }}>取消</Button>
            <Button onClick={submit} disabled={busy}>{busy ? '处理中…' : '确认执行'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
