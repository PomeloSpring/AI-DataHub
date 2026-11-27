/**
 * DictTodoTab — 字典待办：未归属/孤儿指标维度 + 未绑定术语（建模缺口分诊）。
 *
 * 归属（2026-11-21 用户拍板）：进化中心「字典待办」页签。
 * 原来的「建模待办」工作台把它与进化候选混在同一个列表里，于是同一批 pending 候选
 * 在「建模待办」和「进化中心」各有一套分类与处置弹窗（五个出口全部重复），
 * 两套口径必然漂移。现在候选只归进化中心「待建」「核绑定」两个页签，
 * 本页签只处理**必须回字典/术语表改**的缺口：
 *   - 指标/维度 bound_object_key 为空（未归属）或指向已归档对象（孤儿）→ 归属 / 删除；
 *   - 术语未绑定任何表 → 去术语表维护。
 * 这两类动作的写入口是 `metricsApi.update/updateDimension/delete`（按 metrics:manage 把关），
 * 与候选的五出口不是一回事，所以不再互相调用、也不在本地另算候选分类。
 *
 * 失败必须显式：接口挂了绝不渲染成「没有待办缺口」（no-silent-degradation）。
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { metricsApi, type DictObjectFacet } from '@/api/metrics';
import { Button } from '@/components/ui/button';
import { PaginationBar, DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { hasPerm } from '@/stores/permissionStore';
import {
  AlertTriangle, BarChart3, BookOpen, CheckCircle2, ExternalLink, Inbox,
  Loader2, RefreshCw, Search, Trash2, UserPlus,
} from 'lucide-react';
import { t as tr } from '@/i18n/runtime';
import { useTranslation } from 'react-i18next';

// ── 数据类型（服务端 /metrics/asset-pool 的返回形状；suggestions 由进化中心消费，本页签不用）
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
  datasource_id?: number;
}

interface TermRow {
  id: number;
  term_cn: string;
  term_en?: string;
  term_type?: string;
  datasource_id?: number;
  description?: string;
}

interface PoolData {
  metrics: DictRow[];
  dimensions: DictRow[];
  terms: TermRow[];
}

type QueueKey = 'metric' | 'dimension' | 'term';

interface Task {
  id: string;
  queue: QueueKey;
  /** 归属类别显示名（列表行上的类型标记） */
  kindLabel: string;
  title: string;
  sub?: string;
  /** 为什么进待办（未绑定 / 孤儿引用） */
  reason?: { text: string; tone: 'amber' | 'red' };
  meta: { label: string; value: string }[];
  dict?: DictRow;
  term?: TermRow;
}

const QUEUE_META: Record<QueueKey, { label: string; hint: string; icon: typeof Inbox }> = {
  metric: { label: tr('k_7c332454d8'), hint: tr('k_32b98bf398'), icon: BarChart3 },
  dimension: { label: tr('k_0e6d51d643'), hint: tr('k_823aec3486'), icon: BarChart3 },
  term: { label: tr('k_18edcc5215'), hint: tr('k_b49ac112fd'), icon: BookOpen },
};

const QUEUE_ORDER: QueueKey[] = ['metric', 'dimension', 'term'];

const TONE_CLS: Record<string, string> = {
  amber: 'border-amber-500/40 text-amber-600',
  red: 'border-red-500/40 text-red-600',
};

/** 信封混排行（queue/title/aliases JSON 数组）→ 各队列旧行形状（buildTasks 口径不变）。 */
function toDictRow(r: any): DictRow {
  return {
    id: Number(r.id), name: String(r.title || ''), name_en: r.name_en || '',
    aliases: Array.isArray(r.aliases) ? r.aliases : [],
    bound_object_key: r.bound_object_key || '', pool_reason: r.pool_reason,
    unit: r.unit || '', agg_type: r.agg_type || '', category: r.category || '',
    description: r.description || '', datasource_id: Number(r.datasource_id || 0),
  };
}

function toTermRow(r: any): TermRow {
  return {
    id: Number(r.id), term_cn: String(r.title || ''), term_en: r.name_en || '',
    term_type: r.term_type || '', datasource_id: Number(r.datasource_id || 0),
    description: r.description || '',
  };
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
    term: r,
  }));
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
  | null;

export default function DictTodoTab({ onChanged }: {
  /** 处理完一条待办后通知外层（进化中心页签计数/其它视图）刷新 */
  onChanged?: () => void;
} = {}) {
  const { t } = useTranslation('ui');
  const navigate = useNavigate();
  const [pool, setPool] = useState<PoolData | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [queue, setQueue] = useState<QueueKey | 'all'>('all');
  const [kw, setKw] = useState('');
  // 服务端真分页（P1-2）：queue/q 下推 SQL，total 来自 COUNT，不在前端 filter/slice 全量
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  const [serverCounts, setServerCounts] = useState<Record<string, number> | null>(null);
  const [kwd, setKwd] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => {
      if (kwd.trim() !== kw.trim()) { setKwd(kw.trim()); setPage(1); }
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kw]);
  useEffect(() => { setPage(1); }, [queue]);
  const [busyId, setBusyId] = useState('');
  const [pickedIds, setPickedIds] = useState<Set<string>>(new Set());
  const [handled, setHandled] = useState(0);
  const [dialog, setDialog] = useState<DialogState>(null);
  /** 对象清单（归属选择框数据源）：来自字典接口，前端不写死枚举 */
  const [objects, setObjects] = useState<DictObjectFacet[]>([]);
  const [loadOptsError, setLoadOptsError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const { data } = await metricsApi.assetPool({
        queue: queue === 'all' ? undefined : queue,
        q: kwd || undefined,
        page, page_size: pageSize,
      });
      // 信封 items 是三队列混排行；suggestions 归进化中心，本页签不消费（避免第二份候选真源）
      const rows: any[] = Array.isArray(data?.items) ? data.items : [];
      setPool({
        metrics: rows.filter((r) => r.queue === 'metric').map(toDictRow),
        dimensions: rows.filter((r) => r.queue === 'dimension').map(toDictRow),
        terms: rows.filter((r) => r.queue === 'term').map(toTermRow),
      });
      setTotal(Number(data?.total) || 0);
      setServerCounts(data?.counts || null);
      setPickedIds(new Set());
    } catch (e: any) {
      // 失败即暴露：不把接口错误渲染成"待办已清空"
      setPool(null);
      setLoadError(String(e?.response?.data?.detail || e?.message || t('k_22d159f3b7')));
    } finally {
      setLoading(false);
    }
  }, [queue, kwd, page, pageSize]);

  const loadOptions = useCallback(async () => {
    try {
      const { data } = await metricsApi.filters();
      setObjects(data?.objects || []);
      setLoadOptsError('');
    } catch (e: any) {
      setLoadOptsError(String(e?.response?.data?.detail || e?.message || t('k_a11afdff4c')));
    }
  }, []);

  useEffect(() => { void load(); void loadOptions(); }, [load, loadOptions]);

  const tasks = useMemo(() => buildTasks(pool), [pool]);
  // 队列徽章用服务端 counts（覆盖全部队列，不随当前队列/页码抖动）
  const counts = useMemo(() => ({
    metric: serverCounts?.metrics ?? 0,
    dimension: serverCounts?.dimensions ?? 0,
    term: serverCounts?.terms ?? 0,
  }), [serverCounts]);

  // 队列/关键词已下推服务端（P1-2）：这里只做展示排序，不再前端 filter 全量
  const visible = useMemo(
    () => [...tasks].sort((a, b) => a.title.localeCompare(b.title, 'zh')),
    [tasks]);

  const assignable = useMemo(
    () => visible.filter((x) => x.queue === 'metric' || x.queue === 'dimension'),
    [visible]);
  const pickedAssign = useMemo(
    () => assignable.filter((x) => pickedIds.has(x.id)),
    [assignable, pickedIds]);

  const objectOptions: Option[] = useMemo(() => objects.map((o) => ({
    value: o.key,
    label: o.display_name || o.key,
    hint: t('k_cb3994da81', { p0: (o.metric_count || 0) + (o.dimension_count || 0) }),
  })), [objects]);

  /** 出口可用性必须与服务端实际把关同源：字典行归属/删除按 metrics:manage 裁决 */
  const canAssign = hasPerm('metrics:manage');

  const modelBusy = useCallback(async (id: string, fn: () => Promise<unknown>, okMsg: string) => {
    setBusyId(id);
    try {
      await fn();
      setHandled((n) => n + 1);
      toast.success(okMsg);
      await load();          // 处理完必须自更新：历史缺陷是处理完列表不动
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
    for (const x of list) {
      try {
        if (x.queue === 'metric') await metricsApi.update(Number(x.dict!.id), { bound_object_key: objectKey });
        else await metricsApi.updateDimension(Number(x.dict!.id), { bound_object_key: objectKey });
        ok += 1;
      } catch (e: any) {
        failed.push(`${x.title}（${e?.response?.data?.detail || tr('k_3e3c8068bb')}）`);
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
    // 弹窗里重试只会把已成功的行再写一遍
    return true;
  };

  const doDelete = (task: Task) => modelBusy(task.id, async () => {
    if (task.queue === 'metric') await metricsApi.delete(Number(task.dict!.id));
    else await metricsApi.deleteDimension(Number(task.dict!.id));
  }, t('k_b873150c44', { p0: task.title }));

  return (
    <div className="space-y-3">
      {/* 顶栏：页签名在页签上，这里只给状态与刷新（不在页内再造一个同名大标题） */}
      <div className="flex items-center gap-3 flex-wrap">
        <p className="text-[11px] text-muted-foreground">{t('k_ce763e5d30')}</p>
        <div className="flex-1" />
        {handled > 0 && (
          <span className="text-xs text-green-600 inline-flex items-center gap-1">
            <CheckCircle2 className="h-3.5 w-3.5" />{t('k_654f54473f', { p0: handled })}
          </span>
        )}
        <Button variant="outline" size="sm" className="h-7 text-xs"
          onClick={() => { void load(); void loadOptions(); }} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />{t('k_38108eaa1d')}
        </Button>
      </div>

      {loadError && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-600">
          <AlertTriangle className="h-4 w-4 mr-1 inline" />{loadError}
          <Button size="sm" variant="ghost" className="ml-2 h-6 text-xs" onClick={() => void load()}>{t('k_e2d53a6d3a')}</Button>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-3">
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
                <div className="text-xs text-muted-foreground mt-1">{t('k_7bdfc906f7')}</div>
              </div>
            ) : visible.length === 0 ? (
              <div className="py-10 text-center text-sm text-muted-foreground">
                {t('k_c54e79f64e', { p0: kw })}</div>
            ) : (
              <ScrollArea className="h-[52vh] pr-2">
                <div className="space-y-1.5">
                  {visible.map((x) => {
                    const selectable = x.queue === 'metric' || x.queue === 'dimension';
                    const checked = pickedIds.has(x.id);
                    const busy = busyId === x.id;
                    return (
                      <div key={x.id}
                        className={`rounded-lg border px-3 py-2.5 flex items-start gap-3 ${busy ? 'opacity-70' : ''}`}>
                        {selectable && (
                          <input type="checkbox" checked={checked} className="mt-1.5 shrink-0"
                            onChange={() => setPickedIds((p) => {
                              const n = new Set(p);
                              if (n.has(x.id)) n.delete(x.id); else n.add(x.id);
                              return n;
                            })} />
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-medium">{x.title}</span>
                            <Badge variant="outline" className="text-[10px] px-1.5 h-4 shrink-0">
                              {x.kindLabel}
                            </Badge>
                            {x.reason && (
                              <Badge variant="outline"
                                className={`text-[10px] px-1.5 h-4 shrink-0 ${TONE_CLS[x.reason.tone]}`}>
                                {x.reason.text}
                              </Badge>
                            )}
                            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
                          </div>
                          {x.sub && (
                            <div className="text-[11px] text-muted-foreground mt-0.5 truncate">{x.sub}</div>
                          )}
                          {x.meta.length > 0 && (
                            <div className="mt-1 space-y-0.5">
                              {x.meta.map((m, i) => (
                                <div key={i} className="text-[11px] text-muted-foreground">
                                  <span className="text-foreground/70">{m.label}：</span>{m.value}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
                          {(x.queue === 'metric' || x.queue === 'dimension') && (
                            <>
                              <Button size="sm" variant="outline" className="h-7 text-xs"
                                disabled={!canAssign || busy}
                                title={!canAssign ? tr('k_5806d13ef6') : undefined}
                                onClick={() => setDialog({ kind: 'assign', tasks: [x] })}>
                                <UserPlus className="h-3 w-3 mr-1" />{tr('k_ae9956c8b3')}
                              </Button>
                              <Button size="sm" variant="ghost" className="h-7 text-xs"
                                onClick={() => navigate(`/data/metrics?search=${encodeURIComponent(x.title)}`)}>
                                <ExternalLink className="h-3 w-3 mr-1" />{tr('k_05b3227b46')}
                              </Button>
                              <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-muted-foreground hover:text-red-500"
                                title={tr('k_f0ca2d215d')} disabled={busy} onClick={() => setDialog({ kind: 'delete', task: x })}>
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </>
                          )}
                          {x.queue === 'term' && (
                            <Button size="sm" variant="outline" className="h-7 text-xs"
                              onClick={() => navigate('/data/glossary')}>
                              <ExternalLink className="h-3 w-3 mr-1" />{tr('k_8f2d2537d8')}
                            </Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
            )}
            {pool && (
              <PaginationBar page={page} pageSize={pageSize} total={total} busy={loading}
                onChange={setPage}
                onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
                pageSizeOptions={[20, 50]} className="mt-2" />
            )}
          </CardContent>
        </Card>
      </div>

      {dialog && (
        <TaskDialog
          dialog={dialog}
          objects={objects}
          onClose={() => setDialog(null)}
          onAssign={doAssign}
          onBatchAssign={doBatchAssign}
          onDelete={doDelete}
        />
      )}
    </div>
  );
}

/* ── 出口弹窗：只剩归属（单/批量）与删除 ─────────────────────────── */
function TaskDialog({ dialog, objects, onClose, onAssign, onBatchAssign, onDelete }: {
  dialog: NonNullable<DialogState>;
  objects: DictObjectFacet[];
  onClose: () => void;
  onAssign: (task: Task, objectKey: string) => Promise<boolean>;
  onBatchAssign: (list: Task[], objectKey: string) => Promise<boolean>;
  onDelete: (task: Task) => Promise<boolean>;
}) {
  const { t } = useTranslation('ui');
  const [objKey, setObjKey] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const assignList = dialog.kind === 'assign' ? dialog.tasks : [];
  const task = dialog.kind === 'delete' ? dialog.task : (assignList.length === 1 ? assignList[0] : undefined);

  const objectOptions: Option[] = useMemo(() => objects.map((o) => ({
    value: o.key,
    label: o.display_name || o.key,
    hint: t('k_cb3994da81', { p0: (o.metric_count || 0) + (o.dimension_count || 0) }),
  })), [objects, t]);

  const submit = async () => {
    setSubmitting(true);
    try {
      if (dialog.kind === 'assign') {
        const ok = assignList.length > 1
          ? await onBatchAssign(assignList, objKey)
          : await onAssign(assignList[0], objKey);
        if (ok) onClose();
      } else {
        const ok = await onDelete(dialog.task);
        if (ok) onClose();
      }
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = dialog.kind === 'assign' ? !!objKey : true;

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{dialog.kind === 'assign' ? t('k_ae9956c8b3') : t('k_8d57e08bd5')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          {/* 单项出口点名是哪条待办；批量只给计数与汇总，不逐条摊标题 */}
          {dialog.kind === 'assign' && assignList.length > 1 ? (
            <div className="text-xs text-muted-foreground">
              {t('k_c668aefc12', { p0: assignList.length })}
              {assignList.map((x) => x.title).join(', ')}
            </div>
          ) : task && (
            <div className="text-xs text-muted-foreground">
              {t('k_08a4d71586')}<span className="text-foreground font-medium">{task.title}</span>
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
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>{t('k_4d0b4688c7')}</Button>
          <Button onClick={() => void submit()} disabled={!canSubmit || submitting}>
            {submitting && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
            {dialog.kind === 'delete' ? t('k_3c06abe116') : t('k_f526c89937')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}