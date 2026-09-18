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
import { toast } from 'sonner';
import {
  Activity, MessagesSquare, GitBranch, Users as UsersIcon, RefreshCw,
  ThumbsUp, ThumbsDown, AlertCircle, Sparkles, ChevronDown, ChevronRight, Calendar,
  History as HistoryIcon,
} from 'lucide-react';
import History from '@/pages/History';
import {
  getUsageSummary, getUsageByModel, getUsageByUser, listSessions, listTraces, getTrace,
  parseObservabilityTime, observabilityTimeRange,
  observabilityTimeFromInput as toApiDT, observabilityTimeToInput as toInputDT,
  type UsageSummary, type DailyPoint, type ModelUsage, type UserUsage,
  type SessionRow, type TraceRow, type TraceDetail, type CommonFilters,
} from '@/api/observability';

const fmt = (n: number | null | undefined, digits = 0) =>
  n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: digits });

const fmtTime = (s: string | null | undefined) =>
  (s ? parseObservabilityTime(s).toLocaleString('zh-CN', { hour12: false }) : '—');

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
  const [sessPage, setSessPage] = useState(1);
  const [traces, setTraces] = useState<{ items: TraceRow[]; total: number }>({ items: [], total: 0 });
  const [tracePage, setTracePage] = useState(1);
  const [convFilter, setConvFilter] = useState(0);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const effFilters = useCallback<() => CommonFilters>(() => ({
    entrypoint: entrypoint === 'all' ? '' : entrypoint,
    status: status === 'all' ? '' : status,
    model_ref: modelRef === 'all' ? '' : modelRef, start, end,
  }), [entrypoint, status, modelRef, start, end]);

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
    const mr = modelRef === 'all' ? '' : modelRef;
    setRangePreset(key); setAppliedRangePreset(key); setStart(r.start); setEnd(r.end);
    setFilters({ entrypoint: ep, status: st, model_ref: mr, start: r.start, end: r.end });
    setSessPage(1); setTracePage(1); setConvFilter(0);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const f = filters;
      if (tab === 'overview') setSummary(await getUsageSummary(f));
      else if (tab === 'models') setModels(await getUsageByModel(f));
      else if (tab === 'users') setUsers(await getUsageByUser(f));
      else if (tab === 'sessions') setSessions(await listSessions({ ...f, page: sessPage, size: 20 }));
      else if (tab === 'traces') setTraces(await listTraces({ ...f, conversation_id: convFilter, page: tracePage, size: 20 }));
    } catch (e: any) {
      toast.error('加载失败：' + (e?.response?.data?.detail || e?.message || e));
    } finally {
      setLoading(false);
    }
  }, [tab, filters, sessPage, tracePage, convFilter]);

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
            <StatCard icon={Activity} label="Credit（主口径）" value={fmt(s?.credits, 2)} />
            <StatCard icon={Activity} label="成本 USD（估算）" value={s?.cost_usd ? '$' + fmt(s.cost_usd, 4) : '—'} />
            <StatCard icon={ThumbsUp} label="满意率" value={satRate === null ? '—' : satRate + '%'} hint={`👍${fmt(s?.thumbs_up)} 👎${fmt(s?.thumbs_down)}`} />
          </div>
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

        <TabsContent value="sessions">
          <DataTable
            head={['会话', '用户', '入口数据源', '回合', 'Token', 'Credit(回合和)', '错误', '最近活跃', '']}
            rows={sessions.items.map(r => [
              <div><div className="font-medium truncate max-w-xs">{r.title || `#${r.conversation_id}`}</div><div className="text-xs text-muted-foreground">conv {r.conversation_id}</div></div>,
              `${r.username}(${r.user_id})`,
              `ds:${r.datasource_id}`,
              fmt(r.turns), fmt(r.total_tokens), fmt(r.credits, 2), fmt(r.errors), fmtTime(r.last_active),
              <Button size="sm" variant="ghost" onClick={() => { setConvFilter(r.conversation_id); setTab('traces'); }}>查看 Trace</Button>,
            ])}
            total={sessions.total} page={sessPage} pageSize={20} onPage={setSessPage}
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
            head={['开始时间', '用户', '入口', '模型', '问题', '状态', 'Credit', '耗时', '反馈', '']}
            rows={traces.items.map(r => [
              fmtTime(r.started_at), `${r.username}`, r.entrypoint, r.model_ref || '—',
              <span className="truncate max-w-xs inline-block align-bottom" title={r.question}>{r.question}</span>,
              <Badge variant={r.status === 'error' ? 'destructive' : 'outline'}>{r.status}</Badge>,
              fmt(r.credits, 2), `${fmt(r.duration_ms)}ms`,
              r.feedback_satisfied === 1 ? <ThumbsUp className="h-4 w-4 text-green-600" /> : r.feedback_satisfied === 0 ? <ThumbsDown className="h-4 w-4 text-red-500" /> : '—',
              <Button size="sm" variant="ghost" onClick={() => openTrace(r.trace_id)}>详情</Button>,
            ])}
            total={traces.total} page={tracePage} pageSize={20} onPage={setTracePage}
          />
        </TabsContent>

        <TabsContent value="models">
          <DataTable
            head={['模型', '回合', '请求数', '输入Token', '输出Token', 'Credit', '成本USD']}
            rows={models.map(m => [m.model_ref || '—', fmt(m.turns), fmt(m.llm_calls), fmt(m.input_tokens), fmt(m.output_tokens), fmt(m.credits, 2), m.cost_usd ? '$' + fmt(m.cost_usd, 4) : '—'])}
          />
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

function KV({ k, v }: { k: string; v: string }) {
  return <div className="rounded bg-muted px-2 py-1"><div className="text-muted-foreground">{k}</div><div className="font-medium truncate">{v}</div></div>;
}

function DataTable({ head, rows, total, page, pageSize, onPage }: {
  head: React.ReactNode[]; rows: React.ReactNode[][];
  total?: number; page?: number; pageSize?: number; onPage?: (p: number) => void;
}) {
  const pages = total && pageSize ? Math.max(1, Math.ceil(total / pageSize)) : 1;
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
        <div className="flex items-center justify-between px-3 py-2 text-xs text-muted-foreground border-t">
          <span>共 {total} 条</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" disabled={(page || 1) <= 1} onClick={() => onPage((page || 1) - 1)}>上一页</Button>
            <span>{page} / {pages}</span>
            <Button size="sm" variant="outline" disabled={(page || 1) >= pages} onClick={() => onPage((page || 1) + 1)}>下一页</Button>
          </div>
        </div>
      )}
    </div>
  );
}
