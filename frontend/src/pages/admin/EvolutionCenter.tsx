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
import { useTranslation } from 'react-i18next';
import { t as i18nTr , t as tr } from '@/i18n/runtime';

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
  new_metric: i18nTr('k_70ecbecf9e'),
  new_dimension: i18nTr('k_7386b3b37e'),
  alias: i18nTr('k_48f08d7815'),
  binding_gap: i18nTr('k_ab33ea20aa'),
  term: i18nTr('k_17ad7ca811'),
};

const KIND_HINT: Record<Kind, string> = {
  new_metric: i18nTr('k_acd1648500'),
  new_dimension: i18nTr('k_f4db52f0f9'),
  alias: i18nTr('k_9f522f0895'),
  binding_gap: i18nTr('k_6ded9d5bbc'),
  term: i18nTr('k_1ae14b5a15'),
};

const SOURCE_LABEL: Record<string, string> = {
  parse_fail: i18nTr('k_1ed09ddaab'),
  feedback: i18nTr('k_55f66363c6'),
  script: i18nTr('k_42684640cd'),
};

const STATUS_LABEL: Record<string, string> = {
  pending: i18nTr('k_047109def4'), converted: i18nTr('k_c6bc4767c4'), rejected: i18nTr('k_dbf36ddc55'),
  stale: i18nTr('k_512fdab3f8'), verified: i18nTr('k_579bd49bc6'),
};

/** 校验接口返回结构：代理未配时 vite 会把请求兑底成 index.html，
 *  `data.items || []` 会把故障掩盖成"空列表"，这里显式抛错。 */
function expectItems(payload: any, what: string): any[] {
  if (!payload || !Array.isArray(payload.items)) {
    throw new Error(tr('k_247ea64781', { p0: typeof payload, p1: what }));
  }
  return payload.items;
}

export default function EvolutionCenter() {
  const { t } = useTranslation('ui');
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
      setItems(expectItems(data, tr('k_ea540c8bf9')));
    } catch (e: any) {
      const msg = e.message || e.response?.data?.detail || tr('k_22889ef6db');
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
        throw new Error(tr('k_e0f640a638'));
      }
      setOv(data);
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || t('k_3b2ade9517'));
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
      toast.warning(t('k_47e4a9aede'));
    });
  }, [loadOverview]);

  const runDigest = async () => {
    setDigesting(true);
    try {
      const { data } = await client.post('/evolution/digest');
      toast.success(tr('k_b280571b32', { p0: data.summary || tr('k_44f7ba5fa8') }));
      load(); loadOverview();
    } catch (e: any) {
      // 归纳失败必须显式：把"没有新待办"和"归纳挂了"混在一起是最危险的掩盖
      toast.error(e.response?.data?.detail || t('k_d10b113b12'));
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
      expected: '{"allowed": true}', note: t('k_dada6897bf', { p0: c.id, p1: c.term }),
    });
    if (exit === 'metric' || exit === 'dimension') {
      try {
        const { data } = await client.get('/evolution/objects', {
          params: { datasource_name: c.datasource_name || '' },
        });
        setObjOptions(expectItems(data, tr('k_4d9054b950')));
      } catch (e: any) {
        toast.error(e.message || t('k_9aa34a93cb'));
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
      setPropOptions(expectItems(data, tr('k_eb47890f6b')));
    } catch (e: any) {
      toast.error(e.message || t('k_b33fcc9c4c'));
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
          toast.warning(data.message || t('k_59e95054a8'));
          setExitKind('');
          openExit({ ...target, kind: data.next_action as Kind },
            data.next_action === 'new_metric' ? 'metric' : 'dimension');
          return;
        }
        toast.success(t('k_c8586b2abd'));
      } else if (exitKind === 'metric') {
        const { data } = await client.post(`/evolution/candidates/${target.id}/metric-draft`, {
          bound_object_key: form.bound_object_key, display_name: form.display_name,
          agg_type: form.agg_type, formula: form.formula, unit: form.unit,
        });
        toast.success(data.message || t('k_027c9e552e'));
        if (data?.needs_completion?.length) {
          toast.warning(t('k_1feeca1e18', { p0: data.needs_completion.join('、') }));
        }
      } else if (exitKind === 'dimension') {
        if (!form.property_name) { toast.error(t('k_fa85fe050c')); return; }
        const { data } = await client.post(`/evolution/candidates/${target.id}/dimension-draft`, {
          bound_object_key: form.bound_object_key, property_name: form.property_name,
          display_name: form.display_name, category: form.category,
        });
        toast.success(data.message || t('k_37315326aa'));
      } else if (exitKind === 'term') {
        const { data } = await client.post(`/evolution/candidates/${target.id}/to-term`, {
          description: form.note || '',
        });
        toast.success(data.message || t('k_c85d305d6d'));
      } else if (exitKind === 'eval_case') {
        let expected: object;
        try { expected = JSON.parse(form.expected || '{}'); }
        catch { toast.error(t('k_4c59fb400b')); return; }
        const { data } = await client.post(`/evolution/candidates/${target.id}/to-eval-case`, {
          suite: form.suite, capability: form.capability, question: target.term,
          payload: { intent: { object: form.bound_object_key || target.object_hint || '',
                               metrics: [target.term], limit: 10 } },
          expected, note: form.note,
        });
        toast.success(t('k_8c39926ff0', { p0: data.case_key }));
      }
      setTarget(null); setExitKind('');
      load(); loadOverview();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_a5c20b174b'));
    } finally {
      setBusy(false);
    }
  };

  const reject = async (c: Candidate) => {
    try {
      await client.post(`/evolution/candidates/${c.id}/reject`);
      toast.success(t('k_397ff0a184'));
      load(); loadOverview();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_3e20d5d1ce'));
    }
  };

  const grouped = (kind: Kind) => items.filter(i => i.kind === kind);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Dna className="h-5 w-5" />
        <h1 className="text-lg font-semibold">{t("k_849e449611")}</h1>
        <Badge variant="outline" className="text-[10px]">
          {t('k_46d94e86ab')}
        </Badge>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => { load(); loadOverview(); }}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" />{t('k_38108eaa1d')}
        </Button>
        <Button size="sm" onClick={runDigest} disabled={digesting}>
          <Play className="h-3.5 w-3.5 mr-1" />{digesting ? t('k_4f2449291a') : t('k_e87e2177ca')}
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        {tr('k_1325d59b1c')}{' '}<code>evolution_digest</code>{tr('k_da74f8b5fc')}<b>{t("k_ccabc70a21")}</b>{tr('k_d816bfdaa6')}{' '}<b>{t("k_ab33ea20aa")}</b>{t('k_4271b04c1a')}
      </p>

      {ov && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">{t("k_fcc974676e")}</div>
            <div className="mt-1 font-medium">
              👍 {ov.feedback.up} · 👎 {ov.feedback.down}
            </div>
            <div className={ov.feedback.pending_negative ? 'text-amber-600' : 'text-muted-foreground'}>
              {t('k_307eca81e4', { p0: ov.feedback.pending_negative })}</div>
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">{t("k_9ab9b98fe5")}</div>
            {Object.keys(ov.pending_by_source).length === 0 && (
              <div className="text-muted-foreground mt-1">{t("k_0f61703c58")}</div>
            )}
            {Object.entries(ov.pending_by_source).map(([k, n]) => (
              <div key={k} className="mt-0.5">{SOURCE_LABEL[k] || k}：{n}</div>
            ))}
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">{t("k_0ce8c74cda")}</div>
            {ov.kinds.map(k => (
              <div key={k} className="mt-0.5 flex items-center justify-between">
                <span>{KIND_LABEL[k as Kind]}</span>
                <span className="font-medium">{ov.pending_by_kind[k] ?? 0}</span>
              </div>
            ))}
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">{t("k_a35dbe4317")}</div>
            <div className="mt-1 text-green-700">{t('k_25c0f09d35', { p0: ov.outcome.verified ?? 0 })}</div>
            <div className={ov.outcome.stale ? 'text-destructive font-medium' : 'text-muted-foreground'}>
              {t('k_0af6c4a530', { p0: ov.outcome.stale ?? 0 })}
            </div>
            <div className="text-muted-foreground">{t('k_5932b93d07', { p0: ov.outcome.converted ?? 0, p1: ov.outcome.rejected ?? 0 })}</div>
          </div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-2 w-full max-w-sm">
          <TabsTrigger value="pending">{t("k_df8af0853b")}</TabsTrigger>
          <TabsTrigger value="history">{t("k_8416c62f82")}</TabsTrigger>
        </TabsList>

        <TabsContent value="pending" className="space-y-3">
          <div className="flex items-center gap-2">
            <Label className="text-xs">{t("k_6bf1c6de44")}</Label>
            <Select value={kindFilter || 'all'} onValueChange={v => setKindFilter(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-[180px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("k_778fc8f994")}</SelectItem>
                {Object.entries(KIND_LABEL).map(([k, label]) => (
                  <SelectItem key={k} value={k}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {loading && <div className="text-xs text-muted-foreground p-4">{t("k_300ee3dee4")}</div>}
          {!loading && error && (
            <div className="text-xs text-destructive p-4 border border-destructive/40 rounded">
              {t('k_fc3f31a5be', { p0: error })}
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="text-xs text-muted-foreground p-4 border rounded text-center">
              {t('k_37dd8112e8')}
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
                          {c.datasource_name || t('k_d881edb46e')}
                        </Badge>
                        <Badge variant="secondary" className="text-[10px]">{t('k_029b4888a0', { p0: c.hit_count })}</Badge>
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
                            {t('k_4d7b245f64')}
                          </Button>
                        )}
                        {kind === 'new_metric' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => openExit(c, 'metric')}>
                            <Check className="h-3 w-3 mr-1" />{t('k_7b3561a815')}
                          </Button>
                        )}
                        {kind === 'new_dimension' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => openExit(c, 'dimension')}>
                            <Check className="h-3 w-3 mr-1" />{t('k_ce353b2d85')}
                          </Button>
                        )}
                        {kind === 'term' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => openExit(c, 'term')}>
                            {t('k_0e67e0c015')}
                          </Button>
                        )}
                        {kind === 'binding_gap' && (
                          <Button size="sm" variant="outline" className="h-6 text-[11px]"
                            onClick={() => { setTarget(c); setExitKind('eval_case'); }}>
                            {t('k_22056c6e98')}
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                          onClick={() => { setTarget(c); setExitKind(''); setForm({
                            bound_object_key: c.object_hint || '', target_type: 'dimension',
                            capability: 'resolution', suite: 'semantic',
                            expected: '{"allowed": true}', note: t('k_ca29eaac1e', { p0: c.id }),
                          }); }}>
                          {t('k_057cb4672d')}
                        </Button>
                        <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                          onClick={() => reject(c)}>
                          <X className="h-3 w-3 mr-1" />{t('k_32525478cd')}
                        </Button>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        <b>{t("k_d7d6020732")}</b>{c.classify_reason}
                      </div>
                      {Boolean((c.evidence as any)?.question) && (
                        <div className="text-[11px] text-muted-foreground">
                          {t('k_2361a0d37b', { p0: String((c.evidence as any).question) })}
                        </div>
                      )}
                      {Array.isArray((c.evidence as any)?.candidates) &&
                        ((c.evidence as any).candidates as string[]).length > 0 && (
                        <div className="text-[11px] text-muted-foreground">
                          {t('k_e556670542', { p0: ((c.evidence as any).candidates as string[]).slice(0, 5).join('、') })}
                        </div>
                      )}
                      {Array.isArray((c.evidence as any)?.authoritative_hits) &&
                        ((c.evidence as any).authoritative_hits as any[]).length > 0 && (
                        <div className="text-[11px] text-amber-700">
                          {t('k_c4565c3782', { p0: ((c.evidence as any).authoritative_hits as any[])
                            .map(h => tr('k_54940ce18a', { p0: h.ref, p1: h.target_table || '?', p2: h.object_key || '?' }))
                            .join('；') })}
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
            <Label className="text-xs">{t("k_62e951a692")}</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-[180px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(STATUS_LABEL).map(([k, label]) => (
                  <SelectItem key={k} value={k}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-[11px] text-muted-foreground">
              {t('k_7ea4f64c3e')}
            </span>
          </div>
          <div className="border rounded">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/40 text-left">
                  <th className="p-2">{t("k_434eb25505")}</th>
                  <th className="p-2">{t("k_e4e46c7235")}</th>
                  <th className="p-2">{t("k_a3ccf702c5")}</th>
                  <th className="p-2">{t("k_62e951a692")}</th>
                  <th className="p-2">{t("k_f87c5ca889")}</th>
                  <th className="p-2">{t("k_7086f8f94a")}</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && !loading && (
                  <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">{t("k_afaa05cdc4")}</td></tr>
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
              {target && `${exitKind === 'alias' ? tr('k_4d7b245f64') : exitKind === 'metric' ? tr('k_7b3561a815')
                : exitKind === 'dimension' ? tr('k_ce353b2d85') : exitKind === 'term' ? tr('k_9abf0019e8')
                  : tr('k_057cb4672d')} — ${target.term}`}
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
                    <Label className="mb-1 block">{t("k_c44a4e3da1")}</Label>
                    <Select value={form.target_type} onValueChange={v => setForm({ ...form, target_type: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="metric">{t("k_1dfc810a7b")}</SelectItem>
                        <SelectItem value="dimension">{t("k_72fa9ecdc4")}</SelectItem>
                        <SelectItem value="object">{t("k_e5b5171f27")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="mb-1 block">{t("k_28223086a8")}</Label>
                    <Input value={form.target_ref}
                      onChange={e => setForm({ ...form, target_ref: e.target.value })}
                      placeholder={t('k_8468fb9fb8')} />
                  </div>
                </>
              )}

              {(exitKind === 'metric' || exitKind === 'dimension' || exitKind === 'eval_case') && (
                <div>
                  <Label className="mb-1 block">{t("k_72317c17c2")}</Label>
                  <Select value={form.bound_object_key || undefined} onValueChange={loadProps}>
                    <SelectTrigger><SelectValue placeholder={t("k_63d7b41003")} /></SelectTrigger>
                    <SelectContent>
                      {objOptions.map(o => (
                        <SelectItem key={o.key} value={o.key}>{o.display_name || o.key}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {objOptions.length === 0 && (
                    <p className="mt-1 text-[11px] text-amber-600">
                      {t('k_f67262fdb6')}
                    </p>
                  )}
                </div>
              )}

              {exitKind === 'metric' && (
                <>
                  <div>
                    <Label className="mb-1 block">{t("k_a35ae2c7f1")}</Label>
                    <Input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} />
                  </div>
                  <div>
                    <Label className="mb-1 block">{t("k_7045c9b015")}</Label>
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
                        placeholder={t('k_c3702db6c7')} />
                    </div>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {t('k_480f9d5977')}
                  </p>
                </>
              )}

              {exitKind === 'dimension' && (
                <>
                  <div>
                    <Label className="mb-1 block">{t("k_a35ae2c7f1")}</Label>
                    <Input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} />
                  </div>
                  <div>
                    <Label className="mb-1 block">{t("k_f4ec4a6499")}</Label>
                    <Select value={form.property_name || undefined}
                      onValueChange={v => setForm({ ...form, property_name: v })}>
                      <SelectTrigger><SelectValue placeholder={t("k_f6120079d3")} /></SelectTrigger>
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
                  <Label className="mb-1 block">{t("k_f946bf0221")}</Label>
                  <Textarea rows={2} value={form.note} onChange={e => setForm({ ...form, note: e.target.value })}
                    placeholder={t("k_cf358d46dc")} />
                </div>
              )}

              {exitKind === 'eval_case' && (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label className="mb-1 block">{t("k_0e8e7b44a1")}</Label>
                      <Select value={form.suite} onValueChange={v => setForm({ ...form, suite: v })}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {suites.map(s => <SelectItem key={s.suite} value={s.suite}>{s.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label className="mb-1 block">{t("k_b13ab1ec69")}</Label>
                      <Select value={form.capability} onValueChange={v => setForm({ ...form, capability: v })}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {capabilities.map(c => <SelectItem key={c.key} value={c.key}>{c.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div>
                    <Label className="mb-1 block">{t("k_1eae33674b")}</Label>
                    <Textarea rows={3} className="font-mono text-[11px]"
                      value={form.expected} onChange={e => setForm({ ...form, expected: e.target.value })} />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {t('k_8149724441')}
                  </p>
                </>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setExitKind(''); setTarget(null); }}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={submit} disabled={busy}>{busy ? t('k_cf978c0252') : t('k_d575bfd288')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
