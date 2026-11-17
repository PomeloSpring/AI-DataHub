import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { t , t as tr } from '@/i18n/runtime';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Input } from '@/components/ui/input';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import {
  Activity, MessagesSquare, GitBranch, Users as UsersIcon, RefreshCw,
  ThumbsUp, ThumbsDown, AlertCircle, Sparkles, ChevronDown, ChevronRight, Calendar,
  Gauge, History as HistoryIcon, Filter, CheckCircle2, Undo2,
} from 'lucide-react';
import History from '@/pages/History';
import {
  getUsageSummary, getUsageByModel, getUsageByUser, listSessions, listTraces, getTrace, getQuota,
  getFunnelStats, listFunnelIntercepts, reviewFunnelIntercept,
  type FunnelStats, type InterceptRow,
  parseObservabilityTime, observabilityTimeRange,
  observabilityTimeFromInput as toApiDT, observabilityTimeToInput as toInputDT,
  type UsageSummary, type DailyPoint, type ModelUsage, type UserUsage,
  type SessionRow, type TraceRow, type TraceDetail, type CommonFilters, type QuotaInfo,
} from '@/api/observability';
import client from '@/api/client';
import { useTranslation } from 'react-i18next';

const fmt = (n: number | null | undefined, digits = 0) =>
  n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: digits });

const fmtTime = (s: string | null | undefined) =>
  (s ? parseObservabilityTime(s).toLocaleString('zh-CN', { hour12: false }) : '—');

// 成本按行内币种显示（CLI 上报=USD，折算=配置币种，典型为 CNY），不强行把人民币标成 $。
const CURRENCY_SYMBOL: Record<string, string> = { CNY: '¥', USD: '$', EUR: '€', JPY: '¥' };
const fmtCost = (amount: number | null | undefined, currency?: string | null) => {
  if (!amount) return '—';
  const ccy = (currency || '').toUpperCase();
  return `${CURRENCY_SYMBOL[ccy] || (ccy ? ccy + ' ' : '')}${fmt(amount, 4)}`;
};

// 自定义时间范围触发按钮的紧凑显示(本地 YYYY-MM-DD HH:mm)
const fmtShort = (s: string) => { const v = toInputDT(s); return v ? v.replace('T', ' ') : '—'; };

// 入口筛选项:Chat 已全面走 Qoder 执行层(pipelineMode 恒为 agent),
// 旧 chat/quick/deep 入口不再产生数据,已从可选项移除。
const ENTRYPOINTS = [
  { value: 'all', label: t('k_f8f2860ed7') },
  { value: 'agent', label: 'Chat / Qoder（Agent）' },
  { value: 'playground', label: 'Playground' },
];

// 时间范围快捷选项(小时数;0=全部)。默认近 1 小时。
const RANGE_PRESETS = [
  { key: '1h', label: t('k_d956598c75'), hours: 1 },
  { key: '3h', label: t('k_e162cea985'), hours: 3 },
  { key: '1d', label: t('k_451c0cd688'), hours: 24 },
  { key: '7d', label: t('k_e6ff9178a1'), hours: 24 * 7 },
  { key: 'all', label: t('k_778fc8f994'), hours: 0 },
];

// span 类型中文标签(执行步骤/产物)
const SPAN_KIND_LABELS: Record<string, string> = {
  llm_call: t('k_890ceb1b84'),
  assistant: t('k_423fcf7f61'),
  tool_call: t('k_850b4e4dcc'),
  retrieval: t('k_0730538b51'),
  phase: t('k_8bfd170043'),
  result: t('k_adaf94c06b'),
  intent_route: t('k_f7e29f5c6a'),
};

// 处理方（意图漏斗拦截率口径）与判定标签的中文显示名——页面不得把枚举直给用户看
const HANDLED_BY_LABELS: Record<string, string> = {
  agent: t('k_d5b5ef9f7f'),
  intent_gate: t('k_7ee49dad6c'),
};

// 模型通道显示名：两条通道计量单位不同，页面必须把单位说清而不是只换个名字
export const CHANNEL_LABELS: Record<string, string> = {
  'qoder-gateway': t('k_63999d32e9'),
  'anthropic-direct': t('k_7fecb86f77'),
  // 这两个是“伪桶”：库里 channel 为 NULL，但语义完全不同，不能混为一谈
  'not-executed': t('k_21a4b5456d'),
  unrecorded: t('k_1c028f5541'),
};
const channelLabel = (c?: string | null) => CHANNEL_LABELS[c || 'unrecorded'] || c || t('k_38e1bd74a2');
const LABEL_LABELS: Record<string, string> = {
  empty_or_noise: t('k_acb69b3f2d'),
  social_ack: t('k_d3a9d718a1'),
  probe_refuse: t('k_5d9e0ff3b4'),
  help_usage: t('k_9e1bb02b03'),
  capability_probe: t('k_3ec2cb265c'),
  data_query: t('k_02c2b08683'),
  platform_write: t('k_bd195b0051'),
  context_followup: t('k_3b5b5e69a8'),
  ambiguous: t('k_ed4d4b1bbc'),
};
const DECISION_LABELS: Record<string, string> = {
  local: t('k_6d448faeaf'),
  shadow_local: t('k_d5f8ffe1bc'),
  agent: t('k_65c38a45b9'),
};

function presetRange(key: string): { start: string; end: string } {
  const p = RANGE_PRESETS.find((x) => x.key === key);
  return observabilityTimeRange(p?.hours || 0);
}

function StatCard({ icon: Icon, label, value, hint }: { icon: any; label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2 text-muted-foreground text-xs">
        <Icon className="h-4 w-4" /> {label}
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-muted-foreground mt-1">{hint}</div>}
    </div>
  );
}

function TextBlock({ label, text }: { label: string; text: string | null | undefined }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="mt-2">
      <button className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} {label}
      </button>
      {open && (
        <pre className="mt-1 max-h-80 overflow-auto rounded-md bg-muted p-2 text-[11px] whitespace-pre-wrap break-all">
          {text}
        </pre>
      )}
    </div>
  );
}

export default function Observability() {
  const { t } = useTranslation('ui');
  const [tab, setTab] = useState('overview');
  const [filters, setFilters] = useState<CommonFilters>(() => ({ ...presetRange('1h') }));
  const [entrypoint, setEntrypoint] = useState('all');
  const [status, setStatus] = useState('all');
  const [handledBy, setHandledBy] = useState('all');
  const [modelRef, setModelRef] = useState('all');
  // 模型通道筛选：Qoder 网关与供应商直连的成本/计量口径不同，混在一起看会得出错结论
  const [channel, setChannel] = useState('all');
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [rangeOpen, setRangeOpen] = useState(false);
  const [rangePreset, setRangePreset] = useState('1h');
  const [appliedRangePreset, setAppliedRangePreset] = useState('1h');
  const [start, setStart] = useState(() => presetRange('1h').start);
  const [end, setEnd] = useState(() => presetRange('1h').end);

  const [summary, setSummary] = useState<{ summary: UsageSummary; daily: DailyPoint[] } | null>(null);
  const [models, setModels] = useState<ModelUsage[]>([]);
  const [users, setUsers] = useState<UserUsage[]>([]);
  const [sessions, setSessions] = useState<{ items: SessionRow[]; total: number }>({ items: [], total: 0 });
  // 数据源 id → name 映射(页面显示资源一律用 name)
  const [dsNames, setDsNames] = useState<Record<number, string>>({});
  useEffect(() => {
    client.get('/datasources/').then(({ data }) => {
      const m: Record<number, string> = {};
      (Array.isArray(data) ? data : []).forEach((d: any) => { m[d.id] = d.name; });
      setDsNames(m);
    }).catch(() => {});
  }, []);
  const [sessPage, setSessPage] = useState(1);
  const [traces, setTraces] = useState<{ items: TraceRow[]; total: number }>({ items: [], total: 0 });
  const [tracePage, setTracePage] = useState(1);
  // sessions/traces 共用每页条数（分属不同页签）
  const [obsPageSize, setObsPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [convFilter, setConvFilter] = useState(0);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const effFilters = useCallback<() => CommonFilters>(() => ({
    entrypoint: entrypoint === 'all' ? '' : entrypoint,
    status: status === 'all' ? '' : status,
    handled_by: handledBy === 'all' ? '' : handledBy,
    channel: channel === 'all' ? '' : channel,
    model_ref: modelRef === 'all' ? '' : modelRef, start, end,
  }), [entrypoint, status, handledBy, channel, modelRef, start, end]);

  const applyFilters = () => {
    const r = rangePreset === 'custom' ? { start, end } : presetRange(rangePreset);
    setStart(r.start); setEnd(r.end);
    setAppliedRangePreset(rangePreset);
    setFilters({ ...effFilters(), ...r });
    setSessPage(1); setTracePage(1); setConvFilter(0);
  };

  // 时间快捷选项:直接应用(无需再点“应用”)
  const applyPreset = (key: string) => {
    const r = presetRange(key);
    const ep = entrypoint === 'all' ? '' : entrypoint;
    const st = status === 'all' ? '' : status;
    const hb = handledBy === 'all' ? '' : handledBy;
    const mr = modelRef === 'all' ? '' : modelRef;
    setRangePreset(key); setAppliedRangePreset(key); setStart(r.start); setEnd(r.end);
    setFilters({ entrypoint: ep, status: st, handled_by: hb, model_ref: mr, start: r.start, end: r.end });
    setSessPage(1); setTracePage(1); setConvFilter(0);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const f = filters;
      if (tab === 'overview') setSummary(await getUsageSummary(f));
      else if (tab === 'models') setModels(await getUsageByModel(f));
      else if (tab === 'users') setUsers(await getUsageByUser(f));
      else if (tab === 'sessions') setSessions(await listSessions({ ...f, page: sessPage, size: obsPageSize }));
      else if (tab === 'traces') setTraces(await listTraces({ ...f, conversation_id: convFilter, page: tracePage, size: obsPageSize }));
      // 'funnel' 页签自己拉数据（面板内 useEffect），不在这条主链重复请求
    } catch (e: any) {
      toast.error(t('k_fc3f31a5be', { p0: e?.response?.data?.detail || e?.message || e }));
    } finally {
      setLoading(false);
    }
  }, [tab, filters, sessPage, tracePage, obsPageSize, convFilter]);

  useEffect(() => { load(); }, [load]);

  // 模型下拉候选:取可观测中出现过的去重 model_ref(不带筛选=全量);接口无数据静默。
  useEffect(() => {
    getUsageByModel({})
      .then(items => setModelOptions([...new Set(items.map(m => m.model_ref).filter(Boolean))]))
      .catch(() => { /* 忽略:候选加载失败仅使下拉为空 */ });
  }, []);

  const refresh = () => {
    if (appliedRangePreset === 'custom') {
      void load();
      return;
    }
    // 相对时间刷新到当前时刻；保留已应用的其他过滤条件和会话选择。
    const r = presetRange(appliedRangePreset);
    setRangePreset(appliedRangePreset); setStart(r.start); setEnd(r.end);
    setFilters(previous => ({ ...previous, ...r }));
  };

  const openTrace = async (traceId: string) => {
    try { setDetail(await getTrace(traceId)); }
    catch (e: any) { toast.error(t('k_f878aea078', { p0: e?.response?.data?.detail || e?.message })); }
  };

  const s = summary?.summary;
  const satRate = s && ((s.thumbs_up || 0) + (s.thumbs_down || 0)) > 0
    ? Math.round(((s.thumbs_up || 0) / ((s.thumbs_up || 0) + (s.thumbs_down || 0))) * 100) : null;

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2"><Activity className="h-5 w-5" /> {t("k_d07aac91df")}</h1>
          <p className="text-sm text-muted-foreground">{t("k_032c8a6427")}</p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}><RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> {t("k_38108eaa1d")}</Button>
      </div>

      {/* 过滤条 */}
      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("k_38e9cd1ffb")}</label>
          <Select value={entrypoint} onValueChange={setEntrypoint}>
            <SelectTrigger className="w-52"><SelectValue /></SelectTrigger>
            <SelectContent>{ENTRYPOINTS.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("k_62e951a692")}</label>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_778fc8f994")}</SelectItem>
              <SelectItem value="success">{t("k_51991a5d11")}</SelectItem>
              <SelectItem value="error">{t("k_b859c7be75")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("k_8085ebce1a")}</label>
          <Select value={handledBy} onValueChange={setHandledBy}>
            <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_778fc8f994")}</SelectItem>
              <SelectItem value="agent">{t("k_d5b5ef9f7f")}</SelectItem>
              <SelectItem value="intent_gate">{t("k_7ee49dad6c")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("k_6df119c076")}</label>
          <Select value={channel} onValueChange={setChannel}>
            <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_25f2a403bc")}</SelectItem>
              <SelectItem value="qoder-gateway">{t("k_63999d32e9")}</SelectItem>
              <SelectItem value="anthropic-direct">{t("k_7fecb86f77")}</SelectItem>
              <SelectItem value="not-executed">{t("k_21a4b5456d")}</SelectItem>
              <SelectItem value="unrecorded">{t("k_1c028f5541")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("k_98fd0cbd9c")}</label>
          <Select value={modelRef} onValueChange={setModelRef}>
            <SelectTrigger className="w-52"><SelectValue placeholder={t("k_688facc5f5")} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_688facc5f5")}</SelectItem>
              {modelOptions.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("k_438e783e45")}</label>
          <div className="flex items-center gap-1">
            {RANGE_PRESETS.map(p => (
              <Button key={p.key} size="sm" variant={rangePreset === p.key ? 'default' : 'outline'}
                className="h-8 px-2 text-xs" onClick={() => applyPreset(p.key)}>{p.label}</Button>
            ))}
            {/* 起/止合并为一个自定义时间范围选择器 */}
            <Popover open={rangeOpen} onOpenChange={setRangeOpen}>
              <PopoverTrigger asChild>
                <Button size="sm" variant={rangePreset === 'custom' ? 'default' : 'outline'} className="h-8 px-2 text-xs">
                  <Calendar className="h-3.5 w-3.5 mr-1" />
                  {rangePreset === 'custom' ? `${fmtShort(start)} ~ ${fmtShort(end)}` : t('k_c493338e8c')}
                </Button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-64 space-y-3">
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">{t("k_29195326fd")}</label>
                  <Input type="datetime-local" value={toInputDT(start)}
                    onChange={e => { setRangePreset('custom'); setStart(toApiDT(e.target.value)); }} />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">{t("k_bc30bf6b96")}</label>
                  <Input type="datetime-local" value={toInputDT(end)}
                    onChange={e => { setRangePreset('custom'); setEnd(toApiDT(e.target.value)); }} />
                </div>
                <Button size="sm" className="w-full" onClick={() => { setRangeOpen(false); applyFilters(); }}>{t("k_4562024dde")}</Button>
              </PopoverContent>
            </Popover>
          </div>
        </div>
        <Button size="sm" onClick={applyFilters}>{t("k_4562024dde")}</Button>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="overview"><Activity className="h-4 w-4 mr-1" />{t("k_5060421d15")}</TabsTrigger>
          <TabsTrigger value="funnel"><Filter className="h-4 w-4 mr-1" />{t("k_5f5b05bffd")}</TabsTrigger>
          <TabsTrigger value="sessions"><MessagesSquare className="h-4 w-4 mr-1" />{t("k_007b266214")}</TabsTrigger>
          <TabsTrigger value="traces"><GitBranch className="h-4 w-4 mr-1" />Trace</TabsTrigger>
          <TabsTrigger value="models"><Sparkles className="h-4 w-4 mr-1" />{t("k_5510a8f0bf")}</TabsTrigger>
          <TabsTrigger value="users"><UsersIcon className="h-4 w-4 mr-1" />{t("k_5022606e4f")}</TabsTrigger>
          <TabsTrigger value="query_history"><HistoryIcon className="h-4 w-4 mr-1" />{t("k_6079f66ae4")}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard icon={MessagesSquare} label={t("k_4d72abd2e9")} value={fmt(s?.sessions)} />
            <StatCard icon={GitBranch} label={t("k_e4197d757a")} value={fmt(s?.turns)} hint={t('k_75dc38f8a3', { p0: fmt(s?.users) })} />
            <StatCard icon={Sparkles} label={t("k_890ceb1b84")} value={fmt(s?.llm_calls)} hint={t('k_5125154a57', { p0: fmt(s?.avg_duration_ms) })} />
            <StatCard icon={AlertCircle} label={t("k_b859c7be75")} value={fmt(s?.errors)} />
            <StatCard icon={Activity} label={t("k_2d5ae33603")} value={fmt(s?.total_tokens)}
              hint={t('k_571949dcc9')} />
            <StatCard icon={Activity} label={t("k_58e95fffe1")} value={fmt(s?.credits, 4)}
              hint={t('k_7099c0fed4')} />
            {/* 成本按币种分别显示：Qoder 通道是按资源包单价折算的 CNY、直连是供应商上报的 USD，
                历史实现把两者直接相加再挂一个“MAX 出来的币种”，是假数字。 */}
            <StatCard icon={Activity} label={t("k_eb9c6ad937")}
              value={(s?.cost_by_currency?.length)
                ? '≈ ' + s.cost_by_currency.map(c => fmtCost(c.amount, c.currency)).join(' + ')
                : '—'}
              hint={s?.cost_by_currency?.length
                ? t('k_4c8c2383bc', { p0: s.cost_by_currency.map(c => tr('k_b924b0a011', { p0: (c.currency || '?').toUpperCase(), p1: fmt(c.turns) })).join('，') })
                : t('k_fab5414bab')}
            />
            <StatCard icon={ThumbsUp} label={t("k_06aa7bd1ca")} value={satRate === null ? '—' : satRate + '%'} hint={`👍${fmt(s?.thumbs_up)} 👎${fmt(s?.thumbs_down)}`} />
            {/* 前置意图漏斗：拦截掉的回合不起子进程、不烧 credit */}
            <StatCard
              icon={Filter} label={t("k_26bf0fbfdc")}
              value={s?.intercept_rate != null ? Math.round(s.intercept_rate * 1000) / 10 + '%' : '—'}
              hint={t('k_bcded2a153', { p0: fmt(s?.intercepted), p1: fmt(s?.turns) })}
            />
            <StatCard
              icon={Activity} label={t("k_dfe182df6e")}
              value={fmt(s?.estimated_saved_credits, 2)}
              hint={t('k_1d2e2f2678')}
            />
          </div>
          {/* 模型通道分桶：credit 与 token 是两个不可互推的量（Qoder 不报 token、直连没有 credit），
              混在一行里就会得出“直连什么都没花”的错结论。 */}
          <div className="rounded-lg border bg-card p-4">
            <div className="text-sm font-medium mb-2">{t("k_6df119c076")}</div>
            {s?.by_channel?.length ? (
              <table className="w-full text-sm">
                <thead><tr className="text-muted-foreground text-xs border-b">
                  <th className="text-left py-1">{t("k_f3c5b9d1e6")}</th><th className="text-right">{t("k_52d27a21f8")}</th>
                  <th className="text-right">{t("k_890ceb1b84")}</th><th className="text-right">Token</th>
                  <th className="text-right">Credit</th><th className="text-right">{t("k_eb9c6ad937")}</th>
                </tr></thead>
                <tbody>
                  {s.by_channel.map(c => {
                    // 未走执行层（前置拦截）根本没有模型消耗：写 0/不回报 都是误导，只能给 —
                    const na = c.channel === 'not-executed';
                    return (
                      <tr key={c.channel} className="border-b border-border/40 last:border-0">
                        <td className="py-1">
                          {channelLabel(c.channel)}
                          {!!c.backfilled_turns && (
                            <span className="ml-1 text-xs text-muted-foreground"
                              title={t('k_5ed0fd6bbb', { p0: c.backfilled_turns })}>
                              {t('k_31127c53ef', { p0: c.backfilled_turns })}
                            </span>
                          )}
                        </td>
                        <td className="text-right">{fmt(c.turns)}</td>
                        <td className="text-right">{na ? '—' : fmt(c.llm_calls)}</td>
                        <td className="text-right">{na ? '—' : (c.channel === 'qoder-gateway' ? t('k_e0fcd3bff7') : fmt(c.total_tokens))}</td>
                        <td className="text-right">{na || c.channel === 'anthropic-direct' ? t('k_eec46c136f') : fmt(c.credits, 4)}</td>
                        <td className="text-right">{na ? '—' : fmtCost(c.cost_amount, c.cost_currency)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div className="text-sm text-muted-foreground">{t("k_451c95d1e9")}</div>
            )}
            {!!s?.direct_turns && (
              <p className="mt-2 text-xs text-muted-foreground">
                {t('k_4a6f89496a', { p0: fmt(s.direct_turns), p1: t('k_c0f27b46f9') })}
              </p>
            )}
            {/* 估算依据必须写清来源：这两个数字都不是账单，且币种不同不能相加 */}
            <p className="mt-2 text-xs text-muted-foreground">
              {t('k_e9320e47fd', { p0: t('k_220cc0a6c3') })}
            </p>
          </div>
          <QuotaPanel />
          <div className="rounded-lg border bg-card p-4">
            <div className="text-sm font-medium mb-2">{t("k_ffb8802b1a")}</div>
            {summary?.daily?.length ? (
              <table className="w-full text-sm">
                <thead><tr className="text-muted-foreground text-xs border-b">
                  <th className="text-left py-1">{t("k_b6fed9af83")}</th><th className="text-right">{t("k_52d27a21f8")}</th><th className="text-right">Token</th><th className="text-right">Credit</th><th className="text-right">{t("k_b859c7be75")}</th>
                </tr></thead>
                <tbody>{summary.daily.map(d => (
                  <tr key={d.dt} className="border-b last:border-0">
                    <td className="py-1">{d.dt}</td><td className="text-right tabular-nums">{fmt(d.turns)}</td>
                    <td className="text-right tabular-nums">{fmt(d.total_tokens)}</td><td className="text-right tabular-nums">{fmt(d.credits, 2)}</td>
                    <td className="text-right tabular-nums">{fmt(d.errors)}</td>
                  </tr>))}</tbody>
              </table>
            ) : <div className="text-sm text-muted-foreground">{t('k_ba27220a12')}</div>}
          </div>
        </TabsContent>

        <TabsContent value="funnel">
          <FunnelPanel filters={filters} />
        </TabsContent>

        <TabsContent value="sessions">
          <DataTable
            head={[t('k_9a834e9a3e'), t('k_9ba763ea34'), t('k_12ce22c45e'), t('k_52d27a21f8'), 'Token', t('k_2ca61cd46f'), t('k_b859c7be75'), t('k_d672e9d4a8'), '']}
            rows={sessions.items.map(r => [
              <div><div className="font-medium truncate max-w-xs">{r.title || `#${r.conversation_id}`}</div><div className="text-xs text-muted-foreground">conv {r.conversation_id}</div></div>,
              `${r.username}(${r.user_id})`,
              r.datasource_id ? (dsNames[r.datasource_id] || t('k_32cc05509f')) : t('k_906ad18b48'),
              fmt(r.turns), fmt(r.total_tokens), fmt(r.credits, 2), fmt(r.errors), fmtTime(r.last_active),
              <Button size="sm" variant="ghost" onClick={() => { setConvFilter(r.conversation_id); setTab('traces'); }}>{t("k_3d8532c4ea")}</Button>,
            ])}
            total={sessions.total} page={sessPage} pageSize={obsPageSize} onPage={setSessPage}
            onPageSizeChange={(n) => { setObsPageSize(n); setSessPage(1); }}
          />
        </TabsContent>

        <TabsContent value="traces">
          {convFilter > 0 && (
            <div className="mb-2 flex items-center gap-2 text-sm">
              <Badge variant="secondary">{t('k_11b07cc5ea', { p0: convFilter })}</Badge>
              <button className="text-muted-foreground hover:text-foreground" onClick={() => setConvFilter(0)}>{t("k_e469cba0a5")}</button>
            </div>
          )}
          <DataTable
            head={[t('k_e8868af6eb'), t('k_9ba763ea34'), t('k_38e9cd1ffb'), t('k_8085ebce1a'), t('k_fd17d9f4b0'), t('k_98fd0cbd9c'), t('k_2fb49eec39'), t('k_62e951a692'), 'Credit', 'Token', t('k_a9704e1997'), t('k_1d50fa5b3a'), '']}
            rows={traces.items.map(r => [
              fmtTime(r.started_at), `${r.username}`, r.entrypoint,
              <Badge variant={r.handled_by === 'intent_gate' ? 'secondary' : 'outline'}>
                {HANDLED_BY_LABELS[r.handled_by || 'agent'] || r.handled_by}
              </Badge>,
              // 通道与执行层直接列出来：排查“这条会话到底跑在哪、花的谁的钱”不该靠猜
              <span title={`layer_id=${r.layer_id ?? '—'}`}>
                {channelLabel(r.channel)}
                {r.layer_name ? <span className="block text-xs text-muted-foreground">{r.layer_name}</span> : null}
              </span>,
              r.model_ref || (r.handled_by === 'intent_gate' ? t('k_4788ad256c') : '—'),
              <span className="truncate max-w-xs inline-block align-bottom" title={r.question}>{r.question}</span>,
              <Badge variant={r.status === 'error' ? 'destructive' : 'outline'}>{r.status}</Badge>,
              // 直连通道没有 credit 这个量：写 0 会被读成“跑了但没花钱”，写 — 又会被当成缺数据
              r.channel === 'anthropic-direct' ? t('k_eec46c136f')
                : (r.handled_by === 'intent_gate' ? t('k_4788ad256c')
                  : ((r.channel === 'unrecorded' || !r.channel_source) && !r.credits ? '—' : fmt(r.credits, 2))),
              r.channel === 'anthropic-direct' ? fmt(r.total_tokens)
                : (r.handled_by === 'intent_gate' ? '—' : t('k_e0fcd3bff7')),
              `${fmt(r.duration_ms)}ms`,
              r.feedback_satisfied === 1 ? <ThumbsUp className="h-4 w-4 text-green-600" /> : r.feedback_satisfied === 0 ? <ThumbsDown className="h-4 w-4 text-red-500" /> : '—',
              <Button size="sm" variant="ghost" onClick={() => openTrace(r.trace_id)}>{t("k_4f55ee1e68")}</Button>,
            ])}
            total={traces.total} page={tracePage} pageSize={obsPageSize} onPage={setTracePage}
            onPageSizeChange={(n) => { setObsPageSize(n); setTracePage(1); }}
          />
        </TabsContent>

        <TabsContent value="models">
          <DataTable
            head={[t('k_98fd0cbd9c'), t('k_f3c5b9d1e6'), t('k_52d27a21f8'), t('k_0f81e35924'), t('k_47f166b021'), t('k_36f616e51f'), 'Credit', t('k_bed3a8acac')]}
            rows={models.map(m => [m.model_ref || '—', channelLabel(m.channel), fmt(m.turns), fmt(m.llm_calls), fmt(m.input_tokens), fmt(m.output_tokens), fmt(m.credits, 4), fmtCost(m.cost_amount, m.cost_currency)])}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            {t('k_1ecda8b258')}
          </p>
        </TabsContent>

        <TabsContent value="users">
          <DataTable
            head={[t('k_9ba763ea34'), t('k_6b26695e4d'), t('k_9a834e9a3e'), t('k_52d27a21f8'), 'Token', 'Credit', t('k_b859c7be75'), t('k_d672e9d4a8')]}
            rows={users.map(u => [`${u.username}(${u.user_id})`, u.user_role || '—', fmt(u.sessions), fmt(u.turns), fmt(u.total_tokens), fmt(u.credits, 2), fmt(u.errors), fmtTime(u.last_active)])}
          />
        </TabsContent>

        <TabsContent value="query_history">
          <History compact />
        </TabsContent>
      </Tabs>

      {/* Trace 详情 */}
      <Dialog open={!!detail} onOpenChange={(o) => !o && setDetail(null)}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {t('k_8537345c0f')}
              {detail?.trace.feedback_satisfied === 1 && <Badge className="bg-green-600"><ThumbsUp className="h-3 w-3 mr-1" />{t("k_fda7539bde")}</Badge>}
              {detail?.trace.feedback_satisfied === 0 && <Badge variant="destructive"><ThumbsDown className="h-3 w-3 mr-1" />{t("k_f497cad99f")}</Badge>}
            </DialogTitle>
            <DialogDescription className="font-mono text-xs">{detail?.trace.trace_id}</DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                <KV k={t('k_9ba763ea34')} v={`${detail.trace.username}(${detail.trace.user_id})`} />
                <KV k={t('k_38e9cd1ffb')} v={detail.trace.entrypoint} />
                <KV k={t('k_98fd0cbd9c')} v={detail.trace.model_ref || '—'} />
                <KV k={t('k_62e951a692')} v={detail.trace.status} />
                <KV k="Token" v={fmt(detail.trace.total_tokens)} />
                <KV k={t('k_c78ba805e6')} v={fmt(detail.trace.credits, 2)} />
                <KV k={t('k_34abf8d837')} v={fmt(detail.trace.session_credits, 2)} />
                <KV k={t('k_6df119c076')} v={channelLabel(detail.trace.channel)} />
                <KV k="Harness" v={detail.trace.layer_name || (detail.trace.layer_id ? `#${detail.trace.layer_id}` : t('k_38e1bd74a2'))} />
                <KV k={t('k_bed3a8acac')} v={fmtCost(detail.trace.cost_amount, detail.trace.cost_currency)} />
                <KV k={t('k_a9704e1997')} v={`${fmt(detail.trace.duration_ms)}ms`} />
              </div>
              <TextBlock label={t('k_984cd66d4c', { p0: detail.trace.question?.length || 0 })} text={detail.trace.question} />
              <TextBlock label={t("k_5af5d02385")} text={detail.trace.final_answer} />
              {detail.trace.error_message ? <TextBlock label={t("k_b859c7be75")} text={detail.trace.error_message} /> : null}
              {detail.trace.feedback_reason ? <TextBlock label={t("k_05dda3d34e")} text={detail.trace.feedback_reason} /> : null}
              <div>
                <div className="font-medium mb-1">{t('k_84eb47ea23', { p0: detail.spans.length })}</div>
                <p className="text-xs text-muted-foreground mb-2">
                  {t('k_915a0acaa8')}
                </p>
                <div className="space-y-2">
                  {detail.spans.map(sp => (
                    <div key={sp.span_id} className="rounded-md border p-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline">{SPAN_KIND_LABELS[sp.kind] || sp.kind}</Badge>
                          <span className="font-medium">{sp.name || '(unnamed)'}</span>
                          {sp.model_ref && <span className="text-xs text-muted-foreground">{sp.model_ref}</span>}
                        </div>
                        <div className="text-xs text-muted-foreground tabular-nums">
                          {fmt(sp.total_tokens)} tok · {sp.credits != null ? fmt(sp.credits, 3) + ' cr' : '—'} · {fmt(sp.duration_ms)}ms
                        </div>
                      </div>
                      <TextBlock label={t('k_0ae328132f')} text={sp.input_text} />
                      <TextBlock label={t('k_69f20aeee8')} text={sp.output_text} />
                      {sp.error_text ? <TextBlock label={t("k_b859c7be75")} text={sp.error_text} /> : null}
                    </div>
                  ))}
                  {detail.spans.length === 0 && <div className="text-muted-foreground text-xs">{t("k_bf4da70cf6")}</div>}
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Qoder 账号 credit 配额（执行层实测值）。
 *
 * 为什么需要它：Qoder 按 credit 计量、不回报账单成本（total_cost_usd / costUSD 实测恒为 0），
 * 所以账号级“实际花了多少配额”的唯一可信口径就是这个面板。
 * 读取要起一次执行层 CLI（服务端带 TTL 缓存），故独立于概览刷新节律自己加载。
 */
function QuotaPanel() {
  const { t } = useTranslation('ui');
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    try {
      setQuota(await getQuota(refresh));
    } catch (e: any) {
      setQuota({ available: false, reason: e?.response?.data?.detail || e?.message || String(e) });
    } finally {
      setBusy(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const bar = (pct: number | null | undefined) => (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
      <div className="h-2 rounded-full bg-primary transition-all"
        style={{ width: `${Math.max(0, Math.min(100, pct ?? 0))}%` }} />
    </div>
  );

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Gauge className="h-4 w-4" /> {t('k_29f21890f6')}
          {quota?.is_quota_exceeded && <Badge variant="destructive">{t("k_707faf6f86")}</Badge>}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {quota?.available && (
            <span>
              {quota.layer ? t('k_c5e7eaa67d', { p0: quota.layer }) : ''}
              {quota.cached ? t('k_b31566ab8e') : t('k_a08ec5ca10')}{t('k_8b23f69f51', { p0: fmtTime(quota.fetched_at) })}
            </span>
          )}
          <Button size="sm" variant="outline" disabled={busy} onClick={() => load(true)}>
            <RefreshCw className={`h-3.5 w-3.5 mr-1 ${busy ? 'animate-spin' : ''}`} /> {t('k_60d296730a')}
          </Button>
        </div>
      </div>

      {!quota && <div className="text-sm text-muted-foreground">{t("k_7ee48fba36")}</div>}
      {quota && !quota.available && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>{t('k_25c5986ecb', { p0: quota.reason || t('k_f91d273b96') })}</span>
        </div>
      )}
      {quota?.available && (
        <div className="space-y-3">
          {(quota.buckets || []).map(b => (
            <div key={b.source} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium">{b.label}{b.available ? '' : t('k_63f7431407')}</span>
                <span className="tabular-nums text-muted-foreground">
                  {fmt(b.used)} / {fmt(b.total)} {b.unit} {t('k_3aca2abfc7', { p0: fmt(b.remaining) })}
                </span>
              </div>
              {bar(b.percentage)}
              {b.percentage != null && (
                <div className="text-[11px] text-muted-foreground tabular-nums">{t('k_abe5cdb2a7', { p0: fmt(b.percentage, 1) })}</div>
              )}
            </div>
          ))}
          <div className="text-[11px] text-muted-foreground">
            {t('k_e13c3886fa', { p0: quota.expires_at ? ' ' + t('k_4954d7611b', { p0: fmtTime(quota.expires_at) }) : '' })}
          </div>
        </div>
      )}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return <div className="rounded bg-muted px-2 py-1"><div className="text-muted-foreground">{k}</div><div className="font-medium truncate">{v}</div></div>;
}

function DataTable({ head, rows, total, page, pageSize, onPage, onPageSizeChange }: {
  head: React.ReactNode[]; rows: React.ReactNode[][];
  total?: number; page?: number; pageSize?: number; onPage?: (p: number) => void; onPageSizeChange?: (n: number) => void;
}) {
  return (
    <div className="rounded-lg border bg-card">
      <table className="w-full text-sm">
        <thead><tr className="text-muted-foreground text-xs border-b">
          {head.map((h, i) => <th key={i} className="text-left px-3 py-2 font-medium">{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.length ? rows.map((r, i) => (
            <tr key={i} className="border-b last:border-0 hover:bg-muted/40">
              {r.map((c, j) => <td key={j} className="px-3 py-2 align-top">{c}</td>)}
            </tr>
          )) : <tr><td colSpan={head.length} className="px-3 py-8 text-center text-muted-foreground">{t("k_b246458f20")}</td></tr>}
        </tbody>
      </table>
      {onPage && total !== undefined && (
        <PaginationBar
          page={page || 1}
          pageSize={pageSize || 20}
          total={total}
          onChange={onPage}
          onPageSizeChange={onPageSizeChange}
          hideWhenSingle
          className="rounded-b-lg"
        />
      )}
    </div>
  );
}

/**
 * 意图漏斗面板 —— 拦截率、判定分布与误拦复盘。
 *
 * 为什么这页必须存在：分流策略的每一次收紧/放宽都必须由数据说话
 * （fde-evolution §1「没有分桶数据不做策略取舍」），而 shadow 期的判定
 * 不改变任何用户可见行为，不在这页看就什么都没有。表不可达时显式提示跑迁移，
 * 不给一张"看起来正常"的空表（no-silent-degradation）。
 */
function FunnelPanel({ filters }: { filters: CommonFilters }) {
  const { t } = useTranslation('ui');
  const [stats, setStats] = useState<FunnelStats | null>(null);
  const [rows, setRows] = useState<{ items: InterceptRow[]; total: number }>({ items: [], total: 0 });
  const [decision, setDecision] = useState('all');
  const [label, setLabel] = useState('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const f = {
        workspace_id: filters.workspace_id, user_id: filters.user_id,
        datasource_id: filters.datasource_id, start: filters.start, end: filters.end,
        decision: decision === 'all' ? '' : decision,
        label: label === 'all' ? '' : label,
      };
      const [st, list] = await Promise.all([
        getFunnelStats(f),
        listFunnelIntercepts({ ...f, page, size: pageSize }),
      ]);
      setStats(st);
      setRows({ items: list.items, total: list.total });
      if (!st.available && st.reason) toast.error(st.reason);
    } catch (e: any) {
      toast.error(t('k_d5883e1cff', { p0: e?.response?.data?.detail || e?.message || e }));
    } finally {
      setLoading(false);
    }
  }, [filters, decision, label, page, pageSize]);

  useEffect(() => { void load(); }, [load]);

  const markReview = async (row: InterceptRow, reviewed: boolean) => {
    // reviewed=1 → 判定成立；reviewed=0 → 误拦（这条本该交给模型）
    try {
      await reviewFunnelIntercept(row.id, reviewed, reviewed ? t('k_2acda770c4') : t('k_803daf6a49'));
      setRows(prev => ({
        ...prev,
        items: prev.items.map(r => (r.id === row.id ? { ...r, reviewed: reviewed ? 1 : 0,
          review_note: reviewed ? t('k_2acda770c4') : t('k_803daf6a49') } : r)),
      }));
    } catch (e: any) {
      toast.error(t('k_4b14097969', { p0: e?.response?.data?.detail || e?.message || e }));
    }
  };

  const o = stats?.overall;
  const labels = [...new Set((stats?.by_label || []).map(x => x.label).filter(Boolean))];

  return (
    <div className="space-y-4">
      {stats && !stats.available && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {stats.reason}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard icon={Filter} label={t("k_5a44f31020")} value={fmt(o?.judged)} hint={loading ? t('k_300ee3dee4') : undefined} />
        <StatCard icon={CheckCircle2} label={t("k_6d448faeaf")} value={fmt(o?.local)}
          hint={stats ? t('k_989a4fc9fa', { p0: stats.intercept_rate != null ? Math.round((stats.intercept_rate || 0) * 1000) / 10 : 0 }) : undefined} />
        <StatCard icon={Gauge} label={t("k_40dfb51013")} value={fmt(o?.shadow_local)}
          hint={stats ? t('k_fe6a5b787a', { p0: stats.eligible_rate != null ? Math.round((stats.eligible_rate || 0) * 1000) / 10 : 0 }) : undefined} />
        <StatCard icon={AlertCircle} label={t("k_bd425797e5")} value={fmt(o?.gate_errors)}
          hint={t('k_a4c40d812c', { p0: fmt(o?.forced_agent), p1: fmt(o?.avg_latency_ms) })} />
      </div>

      {stats?.by_label?.length ? (
        <div className="rounded-lg border bg-card p-4">
          <div className="text-sm font-medium mb-2">{t("k_1ffa34d68b")}</div>
          <table className="w-full text-sm">
            <thead><tr className="text-muted-foreground text-xs border-b">
              <th className="text-left py-1">{t("k_ae0a7afece")}</th><th className="text-left">{t("k_1056c8860d")}</th>
              <th className="text-right">{t("k_d67cef510f")}</th><th className="text-right">{t("k_283649ee16")}</th>
            </tr></thead>
            <tbody>{stats.by_label.map((b, i) => (
              <tr key={i} className="border-b last:border-0">
                <td className="py-1">{LABEL_LABELS[b.label] || b.label || '—'}</td>
                <td>{DECISION_LABELS[b.decision] || b.decision}</td>
                <td className="text-right tabular-nums">{fmt(b.cnt)}</td>
                <td className="text-right tabular-nums">{fmt(b.avg_latency_ms)}ms</td>
              </tr>))}</tbody>
          </table>
          <p className="mt-2 text-xs text-muted-foreground">
            {t('k_bdac1f7c69')}
          </p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("k_1056c8860d")}</label>
          <Select value={decision} onValueChange={v => { setDecision(v); setPage(1); }}>
            <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_778fc8f994")}</SelectItem>
              {Object.entries(DECISION_LABELS).map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("k_fedb9a2370")}</label>
          <Select value={label} onValueChange={v => { setLabel(v); setPage(1); }}>
            <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_778fc8f994")}</SelectItem>
              {labels.map(l => <SelectItem key={l} value={l}>{LABEL_LABELS[l] || l}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> {t('k_38108eaa1d')}
        </Button>
      </div>

      <DataTable
        head={[t('k_89b4aa6364'), t('k_9ba763ea34'), 'AS-BOT', t('k_33b7512925'), t('k_e777f81234'), t('k_1056c8860d'), t('k_fcf698355a'), t('k_a9704e1997'), t('k_ea87dc05cd'), '']}
        rows={rows.items.map(r => [
          fmtTime(r.dt),
          r.username || t('k_193d49f8d0', { p0: r.user_id }),
          r.as_bot_name || (r.as_bot_key ? t('k_ed7f4cc237') : '—'),
          <span className="truncate max-w-xs inline-block align-bottom" title={r.question_digest}>
            {r.question_digest || '—'}
          </span>,
          <div>
            <Badge variant="outline">{LABEL_LABELS[r.label] || r.label}</Badge>
            <div className="text-[11px] text-muted-foreground mt-1">{r.mode} / {r.layer}</div>
          </div>,
          <Badge variant={r.decision === 'local' ? 'secondary' : 'outline'}>
            {DECISION_LABELS[r.decision] || r.decision}
          </Badge>,
          <span className="text-xs break-words max-w-sm" title={r.signals}>
            {r.gate_error
              ? <span className="text-destructive">{t('k_e9ee3d66dd', { p0: r.gate_error })}</span>
              : (r.reason || '—')}
          </span>,
          `${fmt(r.latency_ms)}ms`,
          r.reviewed
            ? <span className="text-xs text-muted-foreground">{r.review_note || t('k_1cb742ed55')}</span>
            : <span className="text-xs text-muted-foreground">{t("k_86f9195e25")}</span>,
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" title={t("k_556b48dcdb")}
              onClick={() => void markReview(r, true)}>
              <CheckCircle2 className="h-3.5 w-3.5" />
            </Button>
            <Button size="sm" variant="ghost" title={t("k_b71d4e1640")}
              onClick={() => void markReview(r, false)}>
              <Undo2 className="h-3.5 w-3.5" />
            </Button>
          </div>,
        ])}
        total={rows.total} page={page} pageSize={pageSize} onPage={setPage}
        onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
      />
    </div>
  );
}
