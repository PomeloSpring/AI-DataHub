import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
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
  { value: 'all', label: '全部入口' },
  { value: 'agent', label: 'Chat / Qoder（Agent）' },
  { value: 'playground', label: 'Playground' },
];

// 时间范围快捷选项(小时数;0=全部)。默认近 1 小时。
const RANGE_PRESETS = [
  { key: '1h', label: '近1小时', hours: 1 },
  { key: '3h', label: '近3小时', hours: 3 },
  { key: '1d', label: '近1天', hours: 24 },
  { key: '7d', label: '近1周', hours: 24 * 7 },
  { key: 'all', label: '全部', hours: 0 },
];

// span 类型中文标签(执行步骤/产物)
const SPAN_KIND_LABELS: Record<string, string> = {
  llm_call: '模型请求',
  assistant: '模型输出',
  tool_call: '工具调用',
  result: '执行结果',
  intent_route: '前置分流判定',
};

// 处理方（意图漏斗拦截率口径）与判定标签的中文显示名——页面不得把枚举直给用户看
const HANDLED_BY_LABELS: Record<string, string> = {
  agent: '模型（执行层）',
  intent_gate: '前置意图层直答',
};
const LABEL_LABELS: Record<string, string> = {
  empty_or_noise: '无效输入',
  social_ack: '寒暄/应答',
  probe_refuse: '涉密索要拒答',
  help_usage: '使用说明',
  capability_probe: '能力/权限询问',
  data_query: '业务取数',
  platform_write: '平台功能动作',
  context_followup: '多轮延续',
  ambiguous: '判不准（默认放行）',
};
const DECISION_LABELS: Record<string, string> = {
  local: '已本地直答',
  shadow_local: '判定可拦（shadow未拦）',
  agent: '已交模型',
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
  const [tab, setTab] = useState('overview');
  const [filters, setFilters] = useState<CommonFilters>(() => ({ ...presetRange('1h') }));
  const [entrypoint, setEntrypoint] = useState('all');
  const [status, setStatus] = useState('all');
  const [handledBy, setHandledBy] = useState('all');
  const [modelRef, setModelRef] = useState('all');
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
    model_ref: modelRef === 'all' ? '' : modelRef, start, end,
  }), [entrypoint, status, handledBy, modelRef, start, end]);

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
      toast.error('加载失败：' + (e?.response?.data?.detail || e?.message || e));
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
    catch (e: any) { toast.error('加载 trace 失败：' + (e?.response?.data?.detail || e?.message)); }
  };

  const s = summary?.summary;
  const satRate = s && ((s.thumbs_up || 0) + (s.thumbs_down || 0)) > 0
    ? Math.round(((s.thumbs_up || 0) / ((s.thumbs_up || 0) + (s.thumbs_down || 0))) * 100) : null;

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2"><Activity className="h-5 w-5" /> LLM 交互可观测</h1>
          <p className="text-sm text-muted-foreground">Session 清单 / 用户与模型用量 / 对话执行 Trace / 工具与 SQL 产物（受控管理员视图）</p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}><RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> 刷新</Button>
      </div>

      {/* 过滤条 */}
      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">入口</label>
          <Select value={entrypoint} onValueChange={setEntrypoint}>
            <SelectTrigger className="w-52"><SelectValue /></SelectTrigger>
            <SelectContent>{ENTRYPOINTS.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">状态</label>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="success">成功</SelectItem>
              <SelectItem value="error">错误</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">处理方式</label>
          <Select value={handledBy} onValueChange={setHandledBy}>
            <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="agent">模型（执行层）</SelectItem>
              <SelectItem value="intent_gate">前置意图层直答</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">模型</label>
          <Select value={modelRef} onValueChange={setModelRef}>
            <SelectTrigger className="w-52"><SelectValue placeholder="全部模型" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部模型</SelectItem>
              {modelOptions.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">时间范围（本地时间）</label>
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
                  {rangePreset === 'custom' ? `${fmtShort(start)} ~ ${fmtShort(end)}` : '自定义'}
                </Button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-64 space-y-3">
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">起</label>
                  <Input type="datetime-local" value={toInputDT(start)}
                    onChange={e => { setRangePreset('custom'); setStart(toApiDT(e.target.value)); }} />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">止</label>
                  <Input type="datetime-local" value={toInputDT(end)}
                    onChange={e => { setRangePreset('custom'); setEnd(toApiDT(e.target.value)); }} />
                </div>
                <Button size="sm" className="w-full" onClick={() => { setRangeOpen(false); applyFilters(); }}>应用</Button>
              </PopoverContent>
            </Popover>
          </div>
        </div>
        <Button size="sm" onClick={applyFilters}>应用</Button>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="overview"><Activity className="h-4 w-4 mr-1" />概览</TabsTrigger>
          <TabsTrigger value="funnel"><Filter className="h-4 w-4 mr-1" />意图漏斗</TabsTrigger>
          <TabsTrigger value="sessions"><MessagesSquare className="h-4 w-4 mr-1" />会话清单</TabsTrigger>
          <TabsTrigger value="traces"><GitBranch className="h-4 w-4 mr-1" />Trace</TabsTrigger>
          <TabsTrigger value="models"><Sparkles className="h-4 w-4 mr-1" />模型用量</TabsTrigger>
          <TabsTrigger value="users"><UsersIcon className="h-4 w-4 mr-1" />用户用量</TabsTrigger>
          <TabsTrigger value="query_history"><HistoryIcon className="h-4 w-4 mr-1" />查询历史</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard icon={MessagesSquare} label="会话数" value={fmt(s?.sessions)} />
            <StatCard icon={GitBranch} label="回合数" value={fmt(s?.turns)} hint={`用户 ${fmt(s?.users)}`} />
            <StatCard icon={Sparkles} label="模型请求" value={fmt(s?.llm_calls)} hint={`平均耗时 ${fmt(s?.avg_duration_ms)}ms`} />
            <StatCard icon={AlertCircle} label="错误" value={fmt(s?.errors)} />
            <StatCard icon={Activity} label="总 Token" value={fmt(s?.total_tokens)} />
            <StatCard icon={Activity} label="Credit（主口径）" value={fmt(s?.credits, 4)} />
            <StatCard icon={Activity} label="成本（估算）" value={fmtCost(s?.cost_amount, s?.cost_currency)} />
            <StatCard icon={ThumbsUp} label="满意率" value={satRate === null ? '—' : satRate + '%'} hint={`👍${fmt(s?.thumbs_up)} 👎${fmt(s?.thumbs_down)}`} />
            {/* 前置意图漏斗：拦截掉的回合不起子进程、不烧 credit */}
            <StatCard
              icon={Filter} label="前置拦截率"
              value={s?.intercept_rate != null ? Math.round(s.intercept_rate * 1000) / 10 + '%' : '—'}
              hint={`本地直答 ${fmt(s?.intercepted)} / 回合 ${fmt(s?.turns)}`}
            />
            <StatCard
              icon={Activity} label="节省 Credit（估算）"
              value={fmt(s?.estimated_saved_credits, 2)}
              hint="拦截回合数 × agent 回合均值 credit；Qoder 不回报单回合实测成本"
            />
          </div>
          <QuotaPanel />
          <div className="rounded-lg border bg-card p-4">
            <div className="text-sm font-medium mb-2">按日趋势</div>
            {summary?.daily?.length ? (
              <table className="w-full text-sm">
                <thead><tr className="text-muted-foreground text-xs border-b">
                  <th className="text-left py-1">日期</th><th className="text-right">回合</th><th className="text-right">Token</th><th className="text-right">Credit</th><th className="text-right">错误</th>
                </tr></thead>
                <tbody>{summary.daily.map(d => (
                  <tr key={d.dt} className="border-b last:border-0">
                    <td className="py-1">{d.dt}</td><td className="text-right tabular-nums">{fmt(d.turns)}</td>
                    <td className="text-right tabular-nums">{fmt(d.total_tokens)}</td><td className="text-right tabular-nums">{fmt(d.credits, 2)}</td>
                    <td className="text-right tabular-nums">{fmt(d.errors)}</td>
                  </tr>))}</tbody>
              </table>
            ) : <div className="text-sm text-muted-foreground">当前筛选无数据。可切换“全部”时间范围和“全部入口”；Qoder 对话归类为“Chat / Qoder（Agent）”。</div>}
          </div>
        </TabsContent>

        <TabsContent value="funnel">
          <FunnelPanel filters={filters} />
        </TabsContent>

        <TabsContent value="sessions">
          <DataTable
            head={['会话', '用户', '入口数据源', '回合', 'Token', 'Credit(回合和)', '错误', '最近活跃', '']}
            rows={sessions.items.map(r => [
              <div><div className="font-medium truncate max-w-xs">{r.title || `#${r.conversation_id}`}</div><div className="text-xs text-muted-foreground">conv {r.conversation_id}</div></div>,
              `${r.username}(${r.user_id})`,
              r.datasource_id ? (dsNames[r.datasource_id] || '数据源已删除') : '未绑定',
              fmt(r.turns), fmt(r.total_tokens), fmt(r.credits, 2), fmt(r.errors), fmtTime(r.last_active),
              <Button size="sm" variant="ghost" onClick={() => { setConvFilter(r.conversation_id); setTab('traces'); }}>查看 Trace</Button>,
            ])}
            total={sessions.total} page={sessPage} pageSize={obsPageSize} onPage={setSessPage}
            onPageSizeChange={(n) => { setObsPageSize(n); setSessPage(1); }}
          />
        </TabsContent>

        <TabsContent value="traces">
          {convFilter > 0 && (
            <div className="mb-2 flex items-center gap-2 text-sm">
              <Badge variant="secondary">会话 #{convFilter}</Badge>
              <button className="text-muted-foreground hover:text-foreground" onClick={() => setConvFilter(0)}>清除会话过滤</button>
            </div>
          )}
          <DataTable
            head={['开始时间', '用户', '入口', '处理方式', '模型', '问题', '状态', 'Credit', '耗时', '反馈', '']}
            rows={traces.items.map(r => [
              fmtTime(r.started_at), `${r.username}`, r.entrypoint,
              <Badge variant={r.handled_by === 'intent_gate' ? 'secondary' : 'outline'}>
                {HANDLED_BY_LABELS[r.handled_by || 'agent'] || r.handled_by}
              </Badge>,
              r.model_ref || (r.handled_by === 'intent_gate' ? '未调用模型' : '—'),
              <span className="truncate max-w-xs inline-block align-bottom" title={r.question}>{r.question}</span>,
              <Badge variant={r.status === 'error' ? 'destructive' : 'outline'}>{r.status}</Badge>,
              fmt(r.credits, 2), `${fmt(r.duration_ms)}ms`,
              r.feedback_satisfied === 1 ? <ThumbsUp className="h-4 w-4 text-green-600" /> : r.feedback_satisfied === 0 ? <ThumbsDown className="h-4 w-4 text-red-500" /> : '—',
              <Button size="sm" variant="ghost" onClick={() => openTrace(r.trace_id)}>详情</Button>,
            ])}
            total={traces.total} page={tracePage} pageSize={obsPageSize} onPage={setTracePage}
            onPageSizeChange={(n) => { setObsPageSize(n); setTracePage(1); }}
          />
        </TabsContent>

        <TabsContent value="models">
          <DataTable
            head={['模型', '回合', '请求数', '输入Token', '输出Token', 'Credit', '成本']}
            rows={models.map(m => [m.model_ref || '—', fmt(m.turns), fmt(m.llm_calls), fmt(m.input_tokens), fmt(m.output_tokens), fmt(m.credits, 4), fmtCost(m.cost_amount, m.cost_currency)])}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            模型为回合内实际生效模型（未指定时由执行层上报的模型名回填）；Credit 为回合增量（会话累计值之差）；
            成本 = credit × 单价（默认取 Qoder CN 企业资源包官方价 80 元 / 2,000 Credits = ¥0.04/Credit，
            可用 OBSERVABILITY_CREDIT_COST_RATE / _CURRENCY 改为你自己的实际采购价），属估算而非账单值。
          </p>
        </TabsContent>

        <TabsContent value="users">
          <DataTable
            head={['用户', '角色', '会话', '回合', 'Token', 'Credit', '错误', '最近活跃']}
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
              Trace 详情
              {detail?.trace.feedback_satisfied === 1 && <Badge className="bg-green-600"><ThumbsUp className="h-3 w-3 mr-1" />满意</Badge>}
              {detail?.trace.feedback_satisfied === 0 && <Badge variant="destructive"><ThumbsDown className="h-3 w-3 mr-1" />不满意</Badge>}
            </DialogTitle>
            <DialogDescription className="font-mono text-xs">{detail?.trace.trace_id}</DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                <KV k="用户" v={`${detail.trace.username}(${detail.trace.user_id})`} />
                <KV k="入口" v={detail.trace.entrypoint} />
                <KV k="模型" v={detail.trace.model_ref || '—'} />
                <KV k="状态" v={detail.trace.status} />
                <KV k="Token" v={fmt(detail.trace.total_tokens)} />
                <KV k="Credit(回合)" v={fmt(detail.trace.credits, 2)} />
                <KV k="Credit(会话)" v={fmt(detail.trace.session_credits, 2)} />
                <KV k="耗时" v={`${fmt(detail.trace.duration_ms)}ms`} />
              </div>
              <TextBlock label={`问题（${detail.trace.question?.length || 0} 字）`} text={detail.trace.question} />
              <TextBlock label="最终回答" text={detail.trace.final_answer} />
              {detail.trace.error_message ? <TextBlock label="错误" text={detail.trace.error_message} /> : null}
              {detail.trace.feedback_reason ? <TextBlock label="反馈原因" text={detail.trace.feedback_reason} /> : null}
              <div>
                <div className="font-medium mb-2">执行步骤 / 产物（{detail.spans.length}）</div>
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
                      <TextBlock label="输入 (input)" text={sp.input_text} />
                      <TextBlock label="输出 (output / SQL / 产物)" text={sp.output_text} />
                      {sp.error_text ? <TextBlock label="错误" text={sp.error_text} /> : null}
                    </div>
                  ))}
                  {detail.spans.length === 0 && <div className="text-muted-foreground text-xs">该 trace 无 span 明细。</div>}
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
          <Gauge className="h-4 w-4" /> Qoder Credit 配额（账号级真实消耗）
          {quota?.is_quota_exceeded && <Badge variant="destructive">配额已耗尽</Badge>}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {quota?.available && (
            <span>
              {quota.layer ? `执行层：${quota.layer} · ` : ''}
              {quota.cached ? '缓存值' : '实时读取'} · 采集于 {fmtTime(quota.fetched_at)}
            </span>
          )}
          <Button size="sm" variant="outline" disabled={busy} onClick={() => load(true)}>
            <RefreshCw className={`h-3.5 w-3.5 mr-1 ${busy ? 'animate-spin' : ''}`} /> 重读
          </Button>
        </div>
      </div>

      {!quota && <div className="text-sm text-muted-foreground">正在读取账号 credit 配额…（需起一次执行层 CLI）</div>}
      {quota && !quota.available && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>credit 配额不可用：{quota.reason || '未知原因'}（下方表格的 credit 仍为回合真实累加值，不受影响）</span>
        </div>
      )}
      {quota?.available && (
        <div className="space-y-3">
          {(quota.buckets || []).map(b => (
            <div key={b.source} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium">{b.label}{b.available ? '' : '（不可用）'}</span>
                <span className="tabular-nums text-muted-foreground">
                  {fmt(b.used)} / {fmt(b.total)} {b.unit} · 剩 {fmt(b.remaining)}
                </span>
              </div>
              {bar(b.percentage)}
              {b.percentage != null && (
                <div className="text-[11px] text-muted-foreground tabular-nums">已用 {fmt(b.percentage, 1)}%</div>
              )}
            </div>
          ))}
          <div className="text-[11px] text-muted-foreground">
            口径：Qoder 按 credit 计量计费，不回报账单成本（实测 total_cost_usd / costUSD 恒为 0），
            因此本面板是“实际花了多少配额”的权威口径；下方「成本」列是按资源包官方单价折算的估算值。
            {quota.expires_at ? ` 配额到期：${fmtTime(quota.expires_at)}` : ''}
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
          )) : <tr><td colSpan={head.length} className="px-3 py-8 text-center text-muted-foreground">暂无数据</td></tr>}
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
      toast.error('加载意图漏斗数据失败：' + (e?.response?.data?.detail || e?.message || e));
    } finally {
      setLoading(false);
    }
  }, [filters, decision, label, page, pageSize]);

  useEffect(() => { void load(); }, [load]);

  const markReview = async (row: InterceptRow, reviewed: boolean) => {
    // reviewed=1 → 判定成立；reviewed=0 → 误拦（这条本该交给模型）
    try {
      await reviewFunnelIntercept(row.id, reviewed, reviewed ? '人工确认判定成立' : '误拦：该输入需要模型');
      setRows(prev => ({
        ...prev,
        items: prev.items.map(r => (r.id === row.id ? { ...r, reviewed: reviewed ? 1 : 0,
          review_note: reviewed ? '人工确认判定成立' : '误拦：该输入需要模型' } : r)),
      }));
    } catch (e: any) {
      toast.error('标注失败：' + (e?.response?.data?.detail || e?.message || e));
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
        <StatCard icon={Filter} label="本轮判定数" value={fmt(o?.judged)} hint={loading ? '加载中…' : undefined} />
        <StatCard icon={CheckCircle2} label="已本地直答" value={fmt(o?.local)}
          hint={stats ? `拦截率 ${stats.intercept_rate != null ? Math.round((stats.intercept_rate || 0) * 1000) / 10 : 0}%` : undefined} />
        <StatCard icon={Gauge} label="判定可拦（shadow）" value={fmt(o?.shadow_local)}
          hint={stats ? `可拦比例 ${stats.eligible_rate != null ? Math.round((stats.eligible_rate || 0) * 1000) / 10 : 0}%` : undefined} />
        <StatCard icon={AlertCircle} label="网关异常/超预算放行" value={fmt(o?.gate_errors)}
          hint={`主动转深度分析 ${fmt(o?.forced_agent)}；平均判定 ${fmt(o?.avg_latency_ms)}ms`} />
      </div>

      {stats?.by_label?.length ? (
        <div className="rounded-lg border bg-card p-4">
          <div className="text-sm font-medium mb-2">按判定标签分布</div>
          <table className="w-full text-sm">
            <thead><tr className="text-muted-foreground text-xs border-b">
              <th className="text-left py-1">标签</th><th className="text-left">去向</th>
              <th className="text-right">条数</th><th className="text-right">平均判定耗时</th>
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
            shadow 档下「判定可拦」不改变行为（仍由模型回答），只用于评估切 enforce 的把握；
            切 enforce 是需要人看这些数据决定的事，不由代码默认放行。
          </p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">去向</label>
          <Select value={decision} onValueChange={v => { setDecision(v); setPage(1); }}>
            <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              {Object.entries(DECISION_LABELS).map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">判定标签</label>
          <Select value={label} onValueChange={v => { setLabel(v); setPage(1); }}>
            <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              {labels.map(l => <SelectItem key={l} value={l}>{LABEL_LABELS[l] || l}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> 刷新
        </Button>
      </div>

      <DataTable
        head={['时间', '用户', 'AS-BOT', '输入摘要', '判定', '去向', '依据', '耗时', '复盘', '']}
        rows={rows.items.map(r => [
          fmtTime(r.dt),
          r.username || `用户已删除(${r.user_id})`,
          r.as_bot_name || (r.as_bot_key ? 'AS-BOT 已删除' : '—'),
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
              ? <span className="text-destructive">异常放行：{r.gate_error}</span>
              : (r.reason || '—')}
          </span>,
          `${fmt(r.latency_ms)}ms`,
          r.reviewed
            ? <span className="text-xs text-muted-foreground">{r.review_note || '已标注'}</span>
            : <span className="text-xs text-muted-foreground">未标注</span>,
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" title="判定成立（这条确实不需要模型）"
              onClick={() => void markReview(r, true)}>
              <CheckCircle2 className="h-3.5 w-3.5" />
            </Button>
            <Button size="sm" variant="ghost" title="误拦：这条应该交给模型"
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
