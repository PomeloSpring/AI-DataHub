/**
 * AssetPoolWorkbench — 建模待办工作台（未归属资产 + 进化候选的分诊台）。
 *
 * 形态（2026-11-19 用户拍板，「建模待办就不要跳转了，直接在里面实现，外部的建模待办菜单去掉」）：
 * 作为「本体建模」页左侧「跨模型」分区的一个**页内页签**渲染（key='todo'），
 * 不再有 /data/asset-pool 独立路由与侧栏菜单——这些待办是建模缺口，
 * 处理它们本来就要回建模页，跳来跳去只会丢上下文。
 *
 * 为什么待办不属于任何一个模型：指标缺归属、术语没映射、解析失败的词，
 * 都是跨模型的语义层缺口，故单列成页签而不挂到某个模型下。
 *
 * 产品形态参考商业化工具的 triage 台（Linear Triage / GitHub Inbox /
 * Foundry proposals）：一条待办一行，左边说清"是什么、为什么在待办里、证据是什么"，
 * 右边只给**该条类型对应的出口**；处理完列表自更新（历史缺陷：收编/驳回不刷新，
 * 点完像没反应，用户只能反复点或手动刷新）。
 *
 * 与「进化中心」(/system/evolution) 的分工：进化中心是全量候选的历史与归纳大盘
 * （各状态、分布、归纳作业）；本组件只做**待办分诊**——pending 候选 + 字典缺归属，
 * 目标就是把列表清空。
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import client from '@/api/client';
import { metricsApi, type DictObjectFacet } from '@/api/metrics';
import { dimensionCategoryLabel, type FacetValue } from '@/lib/dictLabels';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import { hasPerm } from '@/stores/permissionStore';
import { useAuthStore } from '@/stores/authStore';
import {
  AlertTriangle, BarChart3, Bot, BookOpen, CheckCircle2, ExternalLink, Inbox,
  Link2, Loader2, RefreshCw, Search, Sparkles, Trash2, Unlink, UserPlus,
} from 'lucide-react';
import { t as tr } from '@/i18n/runtime';
import { useTranslation } from 'react-i18next';

// ── 数据类型（服务端 /metrics/asset-pool 的返回形状）──────────────────
interface DictRow {
  id: number;
  name: string;
  name_en?: string;
  aliases?: string[];
  bound_object_key?: string;
  pool_reason?: 'unbound' | 'orphan';
  unit?: string;
  agg_type?: string;
  category?: string;
  description?: string;
}

interface TermRow {
  id: number;
  term_cn: string;
  term_en?: string;
  term_type?: string;
  datasource_id?: number;
  description?: string;
}

interface Candidate {
  id: number;
  term: string;
  kind: string;
  source?: string;
  datasource_name?: string;
  object_hint?: string;
  target_type?: string;
  target_ref?: string;
  classify_reason?: string;
  hit_count?: number;
  updated_at?: string;
  candidates?: { name?: string; score?: number }[];
}

interface PoolData {
  metrics: DictRow[];
  dimensions: DictRow[];
  terms: TermRow[];
  suggestions: Candidate[];
  counts: Record<string, number>;
}

type QueueKey = 'metric' | 'dimension' | 'term'
  | 'c-alias' | 'c-new_metric' | 'c-new_dimension' | 'c-binding_gap' | 'c-term';

interface Task {
  id: string;
  queue: QueueKey;
  /** 归属类别显示名（列表行上的类型标记） */
  kindLabel: string;
  title: string;
  sub?: string;
  /** 为什么进待办（未绑定 / 孤儿引用 / 解析失败…） */
  reason?: { text: string; tone: 'amber' | 'red' | 'blue' | 'muted' };
  meta: { label: string; value: string }[];
  /** 优先级：命中次数（越多说明用户越常问到，越该先处理） */
  weight: number;
  dict?: DictRow;
  term?: TermRow;
  cand?: Candidate;
}

const QUEUE_META: Record<QueueKey, { label: string; hint: string; icon: typeof Inbox }> = {
  metric: { label: tr('k_7c332454d8'), hint: tr('k_32b98bf398'), icon: BarChart3 },
  dimension: { label: tr('k_0e6d51d643'), hint: tr('k_823aec3486'), icon: BarChart3 },
  term: { label: tr('k_18edcc5215'), hint: tr('k_b49ac112fd'), icon: BookOpen },
  'c-alias': { label: tr('k_48f08d7815'), hint: tr('k_9cbb6f5305'), icon: Bot },
  'c-new_metric': { label: tr('k_70ecbecf9e'), hint: tr('k_2d71d05081'), icon: Sparkles },
  'c-new_dimension': { label: tr('k_7386b3b37e'), hint: tr('k_fea4c6de49'), icon: Sparkles },
  'c-binding_gap': { label: tr('k_ab33ea20aa'), hint: tr('k_9f57b74d88'), icon: Link2 },
  'c-term': { label: tr('k_17ad7ca811'), hint: tr('k_c8fb9fafde'), icon: BookOpen },
};

const QUEUE_ORDER: QueueKey[] = [
  'metric', 'dimension', 'c-alias', 'c-new_metric', 'c-new_dimension',
  'c-binding_gap', 'c-term', 'term',
];

const TONE_CLS: Record<string, string> = {
  amber: 'border-amber-500/40 text-amber-600',
  red: 'border-red-500/40 text-red-600',
  blue: 'border-blue-500/40 text-blue-600',
  muted: 'border-border text-muted-foreground',
};

const CAND_KIND_TO_QUEUE: Record<string, QueueKey> = {
  alias: 'c-alias', new_metric: 'c-new_metric', new_dimension: 'c-new_dimension',
  binding_gap: 'c-binding_gap', term: 'c-term',
};

/** 候选行的近似目标（evidence.candidates 里服务端算好的近似字典行/对象） */
function approxTargets(c: Candidate): string {
  const list = (c.candidates || []).slice(0, 3)
    .map((x) => x.name).filter(Boolean);
  if (list.length) return list.join(' / ');
  return c.target_ref || '';
}

function buildTasks(d: PoolData | null): Task[] {
  if (!d) return [];
  const out: Task[] = [];
  const dictTask = (row: DictRow, queue: QueueKey): Task => ({
    id: `${queue === 'metric' ? 'm' : 'd'}:${row.id}`,
    queue,
    kindLabel: queue === 'metric' ? tr('k_1dfc810a7b') : tr('k_72fa9ecdc4'),
    title: row.name,
    sub: [row.name_en, row.agg_type, row.category, row.unit].filter(Boolean).join(' · '),
    reason: row.pool_reason === 'orphan'
      ? { text: tr('k_bf40dea281', { p0: row.bound_object_key || '-' }), tone: 'red' }
      : { text: tr('k_ae7771ae22'), tone: 'amber' },
    meta: row.description ? [{ label: tr('k_d297f53a58'), value: row.description }] : [],
    weight: 1,
    dict: row,
  });
  (d.metrics || []).forEach((r) => out.push(dictTask(r, 'metric')));
  (d.dimensions || []).forEach((r) => out.push(dictTask(r, 'dimension')));
  (d.terms || []).forEach((r) => out.push({
    id: `t:${r.id}`,
    queue: 'term',
    kindLabel: tr('k_f4f39962ca'),
    title: r.term_cn,
    sub: [r.term_en, r.term_type].filter(Boolean).join(' · '),
    reason: { text: tr('k_30a54c9193'), tone: 'amber' },
    meta: r.description ? [{ label: tr('k_4de54f5278'), value: r.description }] : [],
    weight: 1,
    term: r,
  }));
  (d.suggestions || []).forEach((c) => {
    const queue = CAND_KIND_TO_QUEUE[c.kind];
    if (!queue) return;
    const approx = approxTargets(c);
    out.push({
      id: `c:${c.id}`,
      queue,
      kindLabel: QUEUE_META[queue].label,
      title: c.term,
      sub: approx ? tr('k_3f8238ea24', { p0: approx }) : c.object_hint || '',
      reason: { text: c.classify_reason || (c.source === 'parse_fail' ? tr('k_1ed09ddaab') : tr('k_5318867f8b')), tone: 'blue' },
      meta: [
        c.datasource_name ? { label: tr('k_a3ccf702c5'), value: c.datasource_name } : null,
        c.hit_count ? { label: tr('k_7a130d7fde'), value: tr('k_10dc772fe0', { p0: c.hit_count }) } : null,
        c.updated_at ? { label: tr('k_046c6233e0'), value: String(c.updated_at).slice(0, 16).replace('T', ' ') } : null,
      ].filter(Boolean) as { label: string; value: string }[],
      weight: Number(c.hit_count || 1),
      cand: c,
    });
  });
  return out;
}

// ── 可搜索选项列表（选择用选择框，不让人手输内部标识：ui-resource-display）──
interface Option { value: string; label: string; hint?: string }

function OptionList({ options, value, onChange, placeholder, disabled }: {
  options: Option[];
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation('ui');
  const [kw, setKw] = useState('');
  const picked = options.find((o) => o.value === value);
  const list = useMemo(() => {
    const k = kw.trim().toLowerCase();
    const base = k ? options.filter((o) => o.label.toLowerCase().includes(k)
      || String(o.value).toLowerCase().includes(k) || (o.hint || '').toLowerCase().includes(k)) : options;
    return base.slice(0, 80);
  }, [options, kw]);
  return (
    <div className={disabled ? 'opacity-60 pointer-events-none' : ''}>
      <div className="relative">
        <Search className="h-3.5 w-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input value={kw} onChange={(e) => setKw(e.target.value)}
          placeholder={picked ? t('k_b8d56be145', { p0: picked.label }) : placeholder}
          className="h-8 pl-7 text-xs" />
      </div>
      <div className="mt-1.5 max-h-44 overflow-y-auto rounded-md border divide-y">
        {list.length === 0 && (
          <div className="px-2.5 py-3 text-xs text-muted-foreground">
            {options.length ? t('k_d838b822bc') : t('k_1deed0d770')}
          </div>
        )}
        {list.map((o) => (
          <button key={String(o.value)} type="button"
            onClick={() => { onChange(o.value); setKw(''); }}
            className={`w-full text-left px-2.5 py-1.5 text-xs hover:bg-muted/60 flex items-center gap-2
              ${value === o.value ? 'bg-primary/5 text-primary' : ''}`}>
            <span className="truncate font-medium">{o.label}</span>
            {o.hint && <span className="ml-auto shrink-0 text-[11px] text-muted-foreground truncate max-w-[45%]">{o.hint}</span>}
            {value === o.value && <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />}
          </button>
        ))}
      </div>
      {picked?.hint && (
        <div className="mt-1 text-[11px] text-muted-foreground">{t('k_f3fae52990', { p0: picked.label, p1: picked.hint })}</div>
      )}
    </div>
  );
}

type DialogState =
  | { kind: 'assign'; tasks: Task[] }
  | { kind: 'delete'; task: Task }
  | { kind: 'alias'; task: Task }
  | { kind: 'metric-draft'; task: Task }
  | { kind: 'dim-draft'; task: Task }
  | { kind: 'to-term'; task: Task }
  | null;

export default function AssetPoolWorkbench({ onChanged, onOpenModel }: {
  /** 处理完一条待办后通知外层刷新「建模待办」入口计数（页签挂在建模页里，计数在左侧分区） */
  onChanged?: () => void;
  /** 「去本体建模」出口：内嵌时由外层切到对象页签，独立使用时退回路由跳转 */
  onOpenModel?: () => void;
} = {}) {
  const { t } = useTranslation('ui');
  const navigate = useNavigate();
  const [pool, setPool] = useState<PoolData | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [queue, setQueue] = useState<QueueKey | 'all'>('all');
  const [kw, setKw] = useState('');
  const [busyId, setBusyId] = useState('');
  const [pickedIds, setPickedIds] = useState<Set<string>>(new Set());
  const [handled, setHandled] = useState(0);
  const [dialog, setDialog] = useState<DialogState>(null);
  /** 对象清单（归属选择框数据源）与聚合/分类词表：来自字典接口，前端不写死枚举 */
  const [objects, setObjects] = useState<DictObjectFacet[]>([]);
  const [aggTypes, setAggTypes] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loadOptsError, setLoadOptsError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const { data } = await metricsApi.assetPool();
      setPool(data as PoolData);
      setPickedIds(new Set());
    } catch (e: any) {
      // 失败即暴露：不把接口错误渲染成"待办已清空"
      setPool(null);
      setLoadError(String(e?.response?.data?.detail || e?.message || t('k_22d159f3b7')));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadOptions = useCallback(async () => {
    try {
      const { data } = await metricsApi.filters();
      setObjects(data?.objects || []);
      setAggTypes((data?.metrics?.agg_types || []).map((f: FacetValue) => f.value));
      setCategories((data?.dimensions?.categories || []).map((f: FacetValue) => f.value));
      setLoadOptsError('');
    } catch (e: any) {
      setLoadOptsError(String(e?.response?.data?.detail || e?.message || t('k_a11afdff4c')));
    }
  }, []);

  useEffect(() => { void load(); void loadOptions(); }, [load, loadOptions]);

  const tasks = useMemo(() => buildTasks(pool), [pool]);
  const counts = useMemo(() => {
    const m: Record<string, number> = {};
    tasks.forEach((t) => { m[t.queue] = (m[t.queue] || 0) + 1; });
    return m;
  }, [tasks]);

  const visible = useMemo(() => {
    const k = kw.trim().toLowerCase();
    return tasks
      .filter((t) => (queue === 'all' || t.queue === queue)
        && (!k || t.title.toLowerCase().includes(k) || (t.sub || '').toLowerCase().includes(k)
          || t.meta.some((m) => m.value.toLowerCase().includes(k))))
      .sort((a, b) => b.weight - a.weight || a.title.localeCompare(b.title, 'zh'));
  }, [tasks, queue, kw]);

  const assignable = useMemo(
    () => visible.filter((t) => t.queue === 'metric' || t.queue === 'dimension'),
    [visible]);
  const pickedAssign = useMemo(
    () => assignable.filter((t) => pickedIds.has(t.id)),
    [assignable, pickedIds]);

  const objectOptions: Option[] = useMemo(() => objects.map((o) => ({
    value: o.key,
    label: o.display_name || o.key,
    hint: t('k_cb3994da81', { p0: (o.metric_count || 0) + (o.dimension_count || 0) }),
  })), [objects]);

  const { user } = useAuthStore();
  /**
   * 出口可用性必须与服务端**实际**把关同源，否则渲染出“点下去必 403”的死开关：
   * - 字典行归属/删除：api_permission 中间件按 `/api/metrics*` + 方法反查 metrics:manage；
   * - 进化候选五个出口（收编/驳回/转指标/转维度/转术语）：`backend/modules/catalog/api/evolution.py`
   *   先过 `require_admin`（role != admin 直接拒）**再**按功能码（ontology:save / metrics:manage）裁决；
   *   所以非管理员在本页只能只读分诊——这是后端真实口径，页面显式说出来而不是让人撞 403。
   */
  const isAdmin = user?.role === 'admin';
  const canAssign = hasPerm('metrics:manage');
  const canCandidate = isAdmin && hasPerm('ontology:save');
  const canDraft = isAdmin && hasPerm('metrics:manage');
  const canAlias = canCandidate;
  const whyCandidateBlocked = isAdmin ? t('k_23627e700b') : t('k_b4c9333200');
  const whyDraftBlocked = isAdmin ? t('k_bb317e469e') : t('k_b4c9333200');
  const modelBusy = useCallback(async (id: string, fn: () => Promise<unknown>, okMsg: string) => {
    setBusyId(id);
    try {
      await fn();
      setHandled((n) => n + 1);
      toast.success(okMsg);
      await load();          // 处理完必须自更新：历史缺陷是收编/驳回后列表不动
      onChanged?.();
      return true;
    } catch (e: any) {
      toast.error(String(e?.response?.data?.detail || e?.message || t('k_5fa802bef5')));
      return false;
    } finally {
      setBusyId('');
    }
  }, [load, onChanged]);

  // ── 出口动作 ──
  const doAssign = async (task: Task, objectKey: string) => {
    const objName = objectOptions.find((o) => o.value === objectKey)?.label || objectKey;
    return modelBusy(task.id, async () => {
      if (task.queue === 'metric') await metricsApi.update(Number(task.dict!.id), { bound_object_key: objectKey });
      else await metricsApi.updateDimension(Number(task.dict!.id), { bound_object_key: objectKey });
    }, t('k_aca5c67a51', { p0: task.title, p1: objName }));
  };

  const doBatchAssign = async (list: Task[], objectKey: string) => {
    const objName = objectOptions.find((o) => o.value === objectKey)?.label || objectKey;
    let ok = 0;
    const failed: string[] = [];
    for (const t of list) {
      try {
        if (t.queue === 'metric') await metricsApi.update(Number(t.dict!.id), { bound_object_key: objectKey });
        else await metricsApi.updateDimension(Number(t.dict!.id), { bound_object_key: objectKey });
        ok += 1;
      } catch (e: any) {
        failed.push(`${t.title}（${e?.response?.data?.detail || tr('k_3e3c8068bb')}）`);
      }
    }
    if (ok) {
      setHandled((n) => n + ok);
      toast.success(tr('k_9745b0093b', { p0: ok, p1: objName }));
    }
    if (failed.length) toast.error(tr('k_8e21c089ab', { p0: failed.length, p1: failed.slice(0, 2).join(tr('k_c4530db333')) }));
    await load();
    onChanged?.();
    // 批量一律收口：失败项在刷新后的清单里原样保留、失败原因 toast 显式报出，
    // 弹窗里重试只会把已成功的行再写一遍（返回 false 让弹窗挂着反而挡住视图）
    return true;
  };

  const doDelete = (task: Task) => modelBusy(task.id, async () => {
    if (task.queue === 'metric') await metricsApi.delete(Number(task.dict!.id));
    else await metricsApi.deleteDimension(Number(task.dict!.id));
  }, t('k_b873150c44', { p0: task.title }));

  const doAliasAdopt = async (task: Task, targetType: string, targetRef: string) => {
    setBusyId(task.id);
    try {
      const { data } = await client.post(`/evolution/candidates/${task.cand!.id}/alias`,
        { target_type: targetType, target_ref: targetRef });
      if (data?.success) {
        setHandled((n) => n + 1);
        toast.success(targetType === 'object'
          ? tr('k_5a196cf82f', { p0: task.title })
          : tr('k_0adf4773dc', { p0: task.title }));
        await load();
        onChanged?.();
        return true;
      }
      // 目标缺行不是失败，是"该走另一个出口"：服务端回 next_action 指路
      const nxt = data?.next_action === 'new_metric' ? tr('k_3a04effa1d') : tr('k_23df711ec9');
      toast.warning(data?.message || t('k_038c08b840', { p0: nxt }), {
        description: t('k_f75c3fecdf'),
      });
      return false;
    } catch (e: any) {
      toast.error(String(e?.response?.data?.detail || e?.message || t('k_3a1d7e62b8')));
      return false;
    } finally {
      setBusyId('');
    }
  };

  const doReject = (task: Task) => modelBusy(task.id,
    () => client.post(`/evolution/candidates/${task.cand!.id}/reject`),
    t('k_c8c5cfc4f8', { p0: task.title }));

  const doToTerm = async (task: Task, description: string) => modelBusy(task.id,
    () => client.post(`/evolution/candidates/${task.cand!.id}/to-term`, { description }),
    t('k_f10fe4646f', { p0: task.title }));

  const doMetricDraft = async (task: Task, form: Record<string, string>) => modelBusy(task.id,
    () => client.post(`/evolution/candidates/${task.cand!.id}/metric-draft`, form),
    t('k_8d3078711f', { p0: task.title }));

  const doDimDraft = async (task: Task, form: Record<string, string>) => modelBusy(task.id,
    () => client.post(`/evolution/candidates/${task.cand!.id}/dimension-draft`, form),
    t('k_3ac8ff345b', { p0: task.title }));

  /** 打开候选所属数据源的对象清单（候选出口按源选对象，避免选到别站对象后写不进） */
  const [candObjects, setCandObjects] = useState<Record<string, Option[]>>({});
  const candObjectOptions = useCallback(async (dsName: string): Promise<Option[]> => {
    if (!dsName) return objectOptions;
    if (candObjects[dsName]) return candObjects[dsName];
    try {
      const { data } = await client.get('/evolution/objects', { params: { datasource_name: dsName } });
      const opts = (data?.items || []).map((o: any) => ({ value: o.key, label: o.display_name || o.key }));
      setCandObjects((p) => ({ ...p, [dsName]: opts }));
      return opts;
    } catch {
      return objectOptions;   // 源内清单拉不到时回全局清单，并在表单里显式标注
    }
  }, [candObjects, objectOptions]);

  const total = tasks.length;

  return (
    <div className="space-y-4">
      {/* 顶栏 */}
      <div className="flex items-start gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Inbox className="h-5 w-5 text-amber-500" />
            <h1 className="text-lg font-semibold">{t('k_ede7cf6189')}</h1>
            <Badge variant="outline"
              className={total ? 'border-amber-500/40 text-amber-600' : pool ? 'border-green-500/40 text-green-600' : 'border-border text-muted-foreground'}>
              {total ? t('k_9706ceac00', { p0: total }) : pool ? t('k_3683077f3b') : t('k_467b3e0314')}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {t('k_e12220a877')}
          </p>
        </div>
        <div className="flex-1" />
        {handled > 0 && (
          <span className="text-xs text-green-600 inline-flex items-center gap-1">
            <CheckCircle2 className="h-3.5 w-3.5" />{t('k_654f54473f', { p0: handled })}
          </span>
        )}
        <Button variant="outline" size="sm" onClick={() => navigate('/system/evolution')}>
          <Bot className="h-3.5 w-3.5 mr-1" />{t('k_d123009917')}
        </Button>
        <Button variant="outline" size="sm" onClick={() => { void load(); void loadOptions(); }} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />{t('k_38108eaa1d')}
        </Button>
      </div>

      {loadError && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-600">
          <AlertTriangle className="h-4 w-4 mr-1 inline" />{loadError}
          <Button size="sm" variant="ghost" className="ml-2 h-6 text-xs" onClick={() => void load()}>{t('k_e2d53a6d3a')}</Button>
        </div>
      )}

      {/* 非管理员：候选出口在服端就进不了（require_admin），直说当前是只读分诊，
          不渲染“能点但必 403”的出口（no-silent-degradation：失败要提前可见） */}
      {!isAdmin && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700">
          <AlertTriangle className="h-3.5 w-3.5 mr-1 inline" />
          {t('k_3c380f078e')}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        {/* 左栏：分诊队列 */}
        <Card className="xl:col-span-1 h-fit">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('k_dd00b830ce')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <button
              onClick={() => setQueue('all')}
              className={`w-full text-left rounded-lg border px-3 py-2 text-sm flex items-center gap-2 transition-colors
                ${queue === 'all' ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}`}>
              <Inbox className="h-4 w-4 text-muted-foreground" />
              {t('k_04d85c53c7')}
              <Badge variant="outline" className="ml-auto">{total}</Badge>
            </button>
            {QUEUE_ORDER.map((q) => {
              const M = QUEUE_META[q];
              const n = counts[q] || 0;
              const Icon = M.icon;
              return (
                <button key={q} onClick={() => setQueue(q)} disabled={!n}
                  title={M.hint}
                  className={`w-full text-left rounded-lg border px-3 py-2 text-sm flex items-center gap-2 transition-colors
                    ${queue === q ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}
                    ${!n ? 'opacity-50' : ''}`}>
                  <Icon className="h-4 w-4 text-muted-foreground" />
                  <span className="truncate">{M.label}</span>
                  <Badge variant="outline" className="ml-auto shrink-0">{n}</Badge>
                </button>
              );
            })}
            {loadOptsError && (
              <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-600">
                {t('k_d25afa7dbd', { p0: loadOptsError })}
                <button className="underline ml-1" onClick={() => void loadOptions()}>{t('k_e2d53a6d3a')}</button>
              </div>
            )}
            <div className="mt-3 text-[11px] text-muted-foreground leading-relaxed">
              {t('k_c7b1f885eb')}
            </div>
          </CardContent>
        </Card>

        {/* 右栏：待办列表 */}
        <Card className="xl:col-span-3">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2 flex-wrap">
              <CardTitle className="text-sm">
                {queue === 'all' ? t('k_04d85c53c7') : QUEUE_META[queue].label}
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {queue === 'all' ? t('k_fb4d3cf0fd') : QUEUE_META[queue].hint}
                </span>
              </CardTitle>
              <div className="relative ml-auto w-52">
                <Search className="h-3.5 w-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <Input value={kw} onChange={(e) => setKw(e.target.value)}
                  placeholder={t('k_1e98d79840')} className="h-8 pl-7 text-xs" />
              </div>
            </div>
            {pickedAssign.length > 0 && (
              <div className="mt-2 flex items-center gap-2 rounded-md border border-primary/40 bg-primary/5 px-2.5 py-1.5 text-xs">
                <UserPlus className="h-3.5 w-3.5" />
                {t('k_c455076e11', { p0: pickedAssign.length })}
                <Button size="sm" className="h-6 text-xs ml-auto"
                  onClick={() => setDialog({ kind: 'assign', tasks: pickedAssign })}>
                  {t('k_89151c3a7d')}
                </Button>
                <Button size="sm" variant="ghost" className="h-6 text-xs"
                  onClick={() => setPickedIds(new Set())}>{t('k_f02e943954')}</Button>
              </div>
            )}
          </CardHeader>
          <CardContent>
            {loading && !pool ? (
              <div className="py-10 text-center text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 mr-1 inline animate-spin" />{t('k_ee6faa79f4')}
              </div>
            ) : !pool ? (
              /* 接口失败时 total 也是 0，但绝不能渲染成绿色的「没有待办缺口」——
                 那是把失败掩藏成成功（no-silent-degradation），顶部红条之外这里再挡一次 */
              <div className="py-10 text-center text-sm text-muted-foreground">
                <AlertTriangle className="h-4 w-4 mr-1 inline text-red-500" />
                {loadError ? t('k_3b410835f6') : t('k_abb09e1f3c')}
              </div>
            ) : !total ? (
              <div className="py-12 text-center">
                <CheckCircle2 className="h-8 w-8 mx-auto mb-2 text-green-500 opacity-70" />
                <div className="text-sm font-medium">{t('k_d69335fa17')}</div>
                <div className="text-xs text-muted-foreground mt-1">
                  {t('k_7bdfc906f7')}
                </div>
              </div>
            ) : visible.length === 0 ? (
              <div className="py-10 text-center text-sm text-muted-foreground">
                {t('k_c54e79f64e', { p0: kw })}</div>
            ) : (
              <ScrollArea className="h-[64vh] pr-2">
                <div className="space-y-1.5">
                  {visible.map((t) => {
                    const selectable = t.queue === 'metric' || t.queue === 'dimension';
                    const checked = pickedIds.has(t.id);
                    const busy = busyId === t.id;
                    return (
                      <div key={t.id}
                        className={`rounded-lg border px-3 py-2.5 flex items-start gap-3 ${busy ? 'opacity-70' : ''}`}>
                        {selectable && (
                          <input type="checkbox" checked={checked} className="mt-1.5 shrink-0"
                            onChange={() => setPickedIds((p) => {
                              const n = new Set(p);
                              if (n.has(t.id)) n.delete(t.id); else n.add(t.id);
                              return n;
                            })} />
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-medium">{t.title}</span>
                            <Badge variant="outline" className="text-[10px] px-1.5 h-4 shrink-0">
                              {t.kindLabel}
                            </Badge>
                            {t.reason && (
                              <Badge variant="outline"
                                className={`text-[10px] px-1.5 h-4 shrink-0 ${TONE_CLS[t.reason.tone]}`}>
                                {t.reason.text}
                              </Badge>
                            )}
                            {t.weight > 1 && (
                              <span className="text-[11px] text-muted-foreground">{tr('k_434db9d24e', { p0: t.weight })}</span>
                            )}
                            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
                          </div>
                          {t.sub && (
                            <div className="text-[11px] text-muted-foreground mt-0.5 truncate">{t.sub}</div>
                          )}
                          {t.meta.length > 0 && (
                            <div className="mt-1 space-y-0.5">
                              {t.meta.map((m, i) => (
                                <div key={i} className="text-[11px] text-muted-foreground">
                                  <span className="text-foreground/70">{m.label}：</span>{m.value}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
                          {(t.queue === 'metric' || t.queue === 'dimension') && (
                            <>
                              <Button size="sm" variant="outline" className="h-7 text-xs"
                                disabled={!canAssign || busy}
                                title={!canAssign ? tr('k_5806d13ef6') : undefined}
                                onClick={() => setDialog({ kind: 'assign', tasks: [t] })}>
                                <UserPlus className="h-3 w-3 mr-1" />{tr('k_ae9956c8b3')}
                              </Button>
                              <Button size="sm" variant="ghost" className="h-7 text-xs"
                                onClick={() => navigate(`/data/metrics?search=${encodeURIComponent(t.title)}`)}>
                                <ExternalLink className="h-3 w-3 mr-1" />{tr('k_05b3227b46')}
                              </Button>
                              <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-muted-foreground hover:text-red-500"
                                title={tr('k_f0ca2d215d')} disabled={busy} onClick={() => setDialog({ kind: 'delete', task: t })}>
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </>
                          )}
                          {t.queue === 'term' && (
                            <Button size="sm" variant="outline" className="h-7 text-xs"
                              onClick={() => navigate('/data/glossary')}>
                              <ExternalLink className="h-3 w-3 mr-1" />{tr('k_8f2d2537d8')}
                            </Button>
                          )}
                          {(t.queue === 'c-new_metric' || t.queue === 'c-alias'
                            || t.queue === 'c-binding_gap' || t.queue === 'c-term'
                            || t.queue === 'c-new_dimension') && (
                            <Button size="sm" variant="outline" className="h-7 text-xs"
                              disabled={!canAlias || busy}
                              title={!canAlias ? whyCandidateBlocked : tr('k_058e5a15c3')}
                              onClick={() => setDialog({ kind: 'alias', task: t })}>
                              {tr('k_7a7efc8444')}
                            </Button>
                          )}
                          {t.queue === 'c-new_metric' && (
                            <Button size="sm" className="h-7 text-xs" disabled={!canDraft || busy}
                              title={!canDraft ? whyDraftBlocked : undefined}
                              onClick={() => setDialog({ kind: 'metric-draft', task: t })}>
                              {tr('k_3a04effa1d')}
                            </Button>
                          )}
                          {t.queue === 'c-new_dimension' && (
                            <Button size="sm" className="h-7 text-xs" disabled={!canDraft || busy}
                              title={!canDraft ? whyDraftBlocked : undefined}
                              onClick={() => setDialog({ kind: 'dim-draft', task: t })}>
                              {tr('k_23df711ec9')}
                            </Button>
                          )}
                          {(t.queue === 'c-new_metric' || t.queue === 'c-new_dimension'
                            || t.queue === 'c-binding_gap') && (
                            <Button size="sm" variant="ghost" className="h-7 text-xs"
                              onClick={() => (onOpenModel ? onOpenModel() : navigate('/data/ontology'))}>
                              <Unlink className="h-3 w-3 mr-1" />{tr('k_e0730d5ac1')}
                            </Button>
                          )}
                          {t.queue === 'c-term' && (
                            <Button size="sm" variant="outline" className="h-7 text-xs"
                              disabled={!canAlias || busy}
                              onClick={() => setDialog({ kind: 'to-term', task: t })}>
                              {tr('k_9abf0019e8')}
                            </Button>
                          )}
                          {t.cand && (
                            <Button size="sm" variant="ghost" className="h-7 text-xs text-muted-foreground hover:text-red-500"
                              disabled={!canCandidate || busy}
                              title={!canCandidate ? whyCandidateBlocked
                                : tr('k_bc71c16ca9')}
                              onClick={() => void doReject(t)}>
                              {tr('k_32525478cd')}
                            </Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 出口弹窗 */}
      <ExitDialog dialog={dialog} onClose={() => setDialog(null)}
        objectOptions={objectOptions} candObjectOptions={candObjectOptions}
        aggTypes={aggTypes} categories={categories}
        onAssign={async (list, key) => {
          if (list.length > 1) return await doBatchAssign(list, key);
          return await doAssign(list[0], key);
        }}
        onDelete={doDelete}
        onAlias={doAliasAdopt}
        onToTerm={doToTerm}
        onMetricDraft={doMetricDraft}
        onDimDraft={doDimDraft} />
    </div>
  );
}

// ── 出口弹窗：一个组件承载 6 种出口，字段按出口类型分支 ──────────────
function ExitDialog({ dialog, onClose, objectOptions, candObjectOptions, aggTypes, categories,
  onAssign, onDelete, onAlias, onToTerm, onMetricDraft, onDimDraft }: {
  dialog: DialogState;
  onClose: () => void;
  objectOptions: Option[];
  candObjectOptions: (dsName: string) => Promise<Option[]>;
  aggTypes: string[];
  categories: string[];
  onAssign: (tasks: Task[], objectKey: string) => Promise<boolean>;
  onDelete: (task: Task) => Promise<boolean | never>;
  onAlias: (task: Task, targetType: string, targetRef: string) => Promise<boolean>;
  onToTerm: (task: Task, description: string) => Promise<boolean>;
  onMetricDraft: (task: Task, form: Record<string, string>) => Promise<boolean>;
  onDimDraft: (task: Task, form: Record<string, string>) => Promise<boolean>;
}) {
  const { t } = useTranslation('ui');
  const [objKey, setObjKey] = useState('');
  const [targetType, setTargetType] = useState('dimension');
  const [targetRef, setTargetRef] = useState('');
  const [targetOptions, setTargetOptions] = useState<Option[]>([]);
  const [props, setProps] = useState<Option[]>([]);
  const [desc, setDesc] = useState('');
  const [form, setForm] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [optsLoading, setOptsLoading] = useState(false);
  const [hint, setHint] = useState('');

  const task = dialog?.kind === 'assign' ? dialog.tasks[0] : dialog?.kind === 'alias' ? dialog.task
    : dialog ? dialog.task : null;
  const assignList: Task[] = dialog?.kind === 'assign' ? dialog.tasks : [];

  useEffect(() => {
    if (!dialog) return;
    setObjKey(''); setTargetRef(''); setDesc(''); setHint(''); setSubmitting(false);
    setProps([]); setTargetOptions([]);
    const t = 'task' in dialog ? dialog.task : null;
    if (dialog.kind === 'alias' && t?.cand) {
      setTargetType(t.cand.target_type || 'dimension');
      setTargetRef(t.cand.target_ref || '');
    }
    if (dialog.kind === 'metric-draft' && t?.cand) {
      setForm({ display_name: t.cand.term, agg_type: aggTypes[0] || 'COUNT', formula: '', unit: '', description: '' });
    }
    if (dialog.kind === 'dim-draft' && t?.cand) {
      setForm({ display_name: t.cand.term, category: categories[0] || tr('k_24d67862f8'), property_name: '', description: '' });
    }
    if (dialog.kind === 'to-term') setDesc(t?.cand?.classify_reason || '');
  }, [dialog, aggTypes, categories]);

  // 收编目标清单：按类型取字典行/对象（选项显示业务名，值用内部标识）
  useEffect(() => {
    if (dialog?.kind !== 'alias') return;
    let cancelled = false;
    const run = async () => {
      setOptsLoading(true);
      try {
        if (targetType === 'object') {
          const opts = await candObjectOptions(task?.cand?.datasource_name || '');
          if (!cancelled) setTargetOptions(opts);
        } else if (targetType === 'metric') {
          const { data } = await metricsApi.list({ page: 1, size: 500, is_active: '1' });
          const items = data?.items ?? data ?? [];
          if (!cancelled) setTargetOptions((items as any[]).map((m) => ({
            value: String(m.name || ''), label: String(m.name || ''), hint: m.agg_type || '' })));
        } else {
          const { data } = await metricsApi.listAllDimensions({ is_active: '1' });
          const items = data?.items ?? data ?? [];
          if (!cancelled) setTargetOptions((items as any[]).slice(0, 500).map((d) => ({
            value: String(d.name || ''), label: String(d.name || ''), hint: d.category || '' })));
        }
      } catch (e: any) {
        if (!cancelled) setHint(t('k_42a2f11712', { p0: e?.response?.data?.detail || e?.message || t('k_974e748456') }));
      } finally {
        if (!cancelled) setOptsLoading(false);
      }
    };
    void run();
    return () => { cancelled = true; };
  }, [dialog, targetType, task, candObjectOptions]);

  // 维度草稿的属性清单：对象主表上的业务属性名（人不接触物理列）
  useEffect(() => {
    if (dialog?.kind !== 'dim-draft' || !objKey || !task?.cand) return;
    let cancelled = false;
    void (async () => {
      setOptsLoading(true);
      try {
        const { data } = await client.get('/evolution/properties', {
          params: { datasource_name: task.cand?.datasource_name || '', object_key: objKey } });
        const items = (data?.items || []).map((p: any) => ({
          value: String(p.property || ''), label: String(p.property || '') }));
        if (!cancelled) {
          setProps(items);
          if (!items.length) setHint(t('k_c41d584c7b'));
        }
      } catch (e: any) {
        if (!cancelled) setHint(t('k_81b767641f', { p0: e?.response?.data?.detail || e?.message || t('k_974e748456') }));
      } finally {
        if (!cancelled) setOptsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [dialog, objKey, task]);

  const submit = async () => {
    if (!dialog) return;
    setSubmitting(true);
    try {
      let ok = false;
      if (dialog.kind === 'assign') ok = await onAssign(assignList, objKey);
      else if (dialog.kind === 'delete') ok = await onDelete(dialog.task);
      else if (dialog.kind === 'alias') ok = await onAlias(dialog.task, targetType, targetRef);
      else if (dialog.kind === 'to-term') ok = await onToTerm(dialog.task, desc);
      else if (dialog.kind === 'metric-draft') ok = await onMetricDraft(dialog.task, { ...form, bound_object_key: objKey });
      else if (dialog.kind === 'dim-draft') ok = await onDimDraft(dialog.task, { ...form, bound_object_key: objKey });
      if (ok) onClose();
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = !submitting && (
    (dialog?.kind === 'assign' && !!objKey)
    || (dialog?.kind === 'delete')
    || (dialog?.kind === 'alias' && !!targetRef)
    || (dialog?.kind === 'to-term')
    || (dialog?.kind === 'metric-draft' && !!objKey)
    || (dialog?.kind === 'dim-draft' && !!objKey && !!form.property_name)
  );

  if (!dialog) return null;
  const titles: Record<string, string> = {
    assign: assignList.length > 1 ? t('k_82da6f9feb', { p0: assignList.length }) : t('k_112ee32be5'),
    delete: t('k_8d57e08bd5'), alias: t('k_e3b3907ae1'), 'metric-draft': t('k_9a37a77217'),
    'dim-draft': t('k_89dde23a8a'), 'to-term': t('k_020bd120ea'),
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className={dialog.kind === 'assign' ? 'max-w-md' : 'max-w-lg'}>
        <DialogHeader>
          <DialogTitle>{titles[dialog.kind]}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          {/* 单项出口点名是哪条待办；批量只给计数与汇总，不逐条摊标题（与背后清单重复、且只显第一条会误导） */}
          {dialog.kind === 'assign' && assignList.length > 1 ? (
            <div className="text-xs text-muted-foreground">
              {t('k_c668aefc12', { p0: assignList.length })}
              {assignList.map((t) => t.title).join(', ')}
            </div>
          ) : task && (
            <div className="text-xs text-muted-foreground">
              {t('k_08a4d71586')}<span className="text-foreground font-medium">{task.title}</span>
              {task.cand?.datasource_name && <span className="ml-1">{t('k_b0d4d3ebda', { p0: task.cand.datasource_name })}</span>}
              {task.cand?.source === 'parse_fail' && (
                <span className="ml-1">{t('k_18ecd32934')}</span>)}
            </div>
          )}

          {dialog.kind === 'assign' && (
            <>
              <p className="text-xs text-muted-foreground">
                {t('k_b75546e743', { p0: <code>bound_object_key</code> })}
              </p>
              <OptionList options={objectOptions} value={objKey} onChange={setObjKey}
                placeholder={t('k_e96bb3bc25')} />
            </>
          )}

          {dialog.kind === 'delete' && (
            <p className="text-sm text-muted-foreground">
              {t('k_b2972726f3', { p0: task?.queue === 'metric' ? t('k_1dfc810a7b') : t('k_72fa9ecdc4'), p1: task?.title })}
            </p>
          )}

          {dialog.kind === 'alias' && (
            <>
              <p className="text-xs text-muted-foreground">
                {t('k_2275d7e6d3')}
              </p>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground shrink-0">{t('k_c44a4e3da1')}</span>
                <Select value={targetType} onValueChange={(v) => { setTargetType(v); setTargetRef(''); }}>
                  <SelectTrigger className="h-8 text-xs w-40"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="dimension">{t('k_4f1918eeb6')}</SelectItem>
                    <SelectItem value="metric">{t('k_a2f1ce2da5')}</SelectItem>
                    <SelectItem value="object">{t('k_e5b5171f27')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="text-xs text-muted-foreground flex items-center gap-1">
                {optsLoading ? <><Loader2 className="h-3 w-3 animate-spin" />{t('k_76de2d49b5')}</> : null}
              </div>
              <OptionList options={targetOptions} value={targetRef} onChange={setTargetRef}
                placeholder={t('k_508016c121')} disabled={optsLoading} />
            </>
          )}

          {dialog.kind === 'to-term' && (
            <>
              <p className="text-xs text-muted-foreground">
                {t('k_321e62a6e1')}
              </p>
              <Textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={3}
                placeholder={t('k_71a40f2fb6')} className="text-xs" />
            </>
          )}

          {(dialog.kind === 'metric-draft' || dialog.kind === 'dim-draft') && (
            <>
              <p className="text-xs text-muted-foreground">
                {t('k_9b2dc2c756')}
              </p>
              <div className="space-y-2">
                <div>
                  <label className="text-xs text-muted-foreground">{t('k_3d44b0cfee')}</label>
                  <OptionList options={objectOptions} value={objKey}
                    onChange={(v) => { setObjKey(v); setForm((f) => ({ ...f, property_name: '' })); }}
                    placeholder={t('k_3074e5ba35')} />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-xs text-muted-foreground">{t('k_c35a132911')}</label>
                    <Input value={form.display_name || ''} className="h-8 text-xs"
                      onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))} />
                  </div>
                  {dialog.kind === 'metric-draft' ? (
                    <div>
                      <label className="text-xs text-muted-foreground">{t('k_fa3207b98d')}</label>
                      <Select value={form.agg_type || 'COUNT'}
                        onValueChange={(v) => setForm((f) => ({ ...f, agg_type: v }))}>
                        <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {(aggTypes.length ? aggTypes : ['COUNT', 'SUM', 'AVG']).map((a) => (
                            <SelectItem key={a} value={a}>{a}</SelectItem>))}
                        </SelectContent>
                      </Select>
                    </div>
                  ) : (
                    <div>
                      <label className="text-xs text-muted-foreground">{t('k_435c5259e4')}</label>
                      <Select value={form.category || t('k_24d67862f8')}
                        onValueChange={(v) => setForm((f) => ({ ...f, category: v }))}>
                        <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {(categories.length ? categories : [tr('k_24d67862f8')]).map((c) => (
                            <SelectItem key={c} value={c}>{dimensionCategoryLabel(c)}</SelectItem>))}
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                </div>
                {dialog.kind === 'metric-draft' ? (
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-xs text-muted-foreground">{t('k_3c006a1f49')}</label>
                      <Input value={form.formula || ''} className="h-8 text-xs"
                        onChange={(e) => setForm((f) => ({ ...f, formula: e.target.value }))} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">{t('k_8ec8c8c712')}</label>
                      <Input value={form.unit || ''} className="h-8 text-xs"
                        onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))} />
                    </div>
                  </div>
                ) : (
                  <div>
                    <label className="text-xs text-muted-foreground">{t('k_242977a103')}</label>
                    {optsLoading && <div className="text-[11px] text-muted-foreground"><Loader2 className="h-3 w-3 inline animate-spin mr-1" />{t('k_22a54c63e7')}</div>}
                    {!objKey && <div className="text-[11px] text-muted-foreground">{t('k_faa36bc873')}</div>}
                    {!!objKey && <OptionList options={props} value={form.property_name || ''}
                      onChange={(v) => setForm((f) => ({ ...f, property_name: v }))}
                      placeholder={t('k_16489ce1f7')} disabled={optsLoading} />}
                  </div>
                )}
                <div>
                  <label className="text-xs text-muted-foreground">{t('k_53a989d8cc')}</label>
                  <Textarea value={form.description || ''} rows={2} className="text-xs"
                    onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
                </div>
              </div>
            </>
          )}

          {hint && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-600">
              {hint}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>{t('k_4d0b4688c7')}</Button>
          <Button onClick={() => void submit()} disabled={!canSubmit}>
            {submitting && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
            {dialog.kind === 'delete' ? t('k_3c06abe116') : t('k_f526c89937')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
