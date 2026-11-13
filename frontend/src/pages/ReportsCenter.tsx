// 报告中心: 报告跟随属主(跨工作空间), 支持管理「不一定是定时任务产出」的报告。
// 与资产清单的分工: 资产=归档的文件(下载复用), 报告=稳定内容(在线实时查看)。
// 正文不在详情响应里: 详情回「域名+签名」的直链, 本页 fetch 直链取正文(见 api/report.ts)。
// 归集口径: 同归集键(series_key, 服务端一处算)的多行是同一份报告的历史版本 ——
//          任务按任务归集、「用最新数据重新生成」继承来源报告、内容型一条一份、其余按标题。
// 卡片身份取原始报告(最早一版): 会话保存的报告重跑后仍是「会话保存」，可编辑/删除。
// 删除分两个意图（不得共用一个“删除”）：卡片垃圾桶 = 删整份报告（含全部历史版本）；
// 详情页「删除这一版」= 只删当前选中的那一版。定时任务产出留给任务调度。
import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useParams } from 'react-router-dom';
import {
  FileBarChart, Loader2, Eye, Inbox, Pencil, Trash2, Search,
  RefreshCw, CalendarClock, Layers, X, Send,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import ActionOverflowMenu, { type OverflowItem } from '@/components/ActionOverflowMenu';
import ReportContent from '@/components/ReportContent';
import ReportExportMenu from '@/components/ReportExportMenu';
import ReportTaskDialog from '@/components/ReportTaskDialog';
import ReportPromoteDialog from '@/components/ReportPromoteDialog';
import { useContentPerms } from '@/pages/content/shared';
import { triggerScheduledTask } from '@/api/scheduledTask';
import {
  listReports, getReportDetail, updateReport, deleteReport,
  refreshReportStream, fetchReportContent, isEditableReport, isDeletableReport,
  saveReportAsTemplate,
  type ReportSummary, type ReportDetail, type ReportSourceType, type RefreshStreamEvent,
} from '@/api/report';
import { getToolLabel } from '@/lib/toolLabel';
import { evidenceNote, layoutBadge, layoutNote } from '@/lib/reportLayout';

/** 归集后的一组报告(版本按时间倒序, [0] 为最新) */
interface Series {
  key: string;
  name: string;
  source: ReportSourceType;
  editable: boolean;
  deletable: boolean;
  reports: ReportSummary[];
}

/** 编辑器状态：重命名 / 改正文（只服务存量内容型报告）。
 *
 * 「新建报告」入口已退役（用户拍板）：报告中心的定位是"分析产物的稳定版本"，
 * 手建一张没有取数来源、没有证据链的空白报告，既不能按最新数据重新生成也不能建定时任务，
 * 混在清单里只造成歧义。存量手写报告不删不改，仍可编辑/导出/删除。
 */
interface EditorState {
  open: boolean;
  reportId: number;
  title: string;
  content: string;
  format: string;
  saving: boolean;
}

const SOURCE_META: Record<string, { label: string; cls: string }> = {
  scheduled: { label: '定时任务', cls: 'text-blue-600 border-blue-200' },
  analysis: { label: '分析生成', cls: 'text-indigo-600 border-indigo-200' },
  manual: { label: '手动新建', cls: 'text-emerald-600 border-emerald-200' },
  conversation: { label: '会话保存', cls: 'text-teal-600 border-teal-200' },
  legacy: { label: '历史报告', cls: 'text-amber-600 border-amber-200' },
};
const SOURCE_ORDER: ReportSourceType[] = ['scheduled', 'analysis', 'conversation', 'manual', 'legacy'];

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** 任务报告标题形如「任务名 - 2026-07-05」, 归集名只留任务名(显示用 name) */
function seriesName(r: ReportSummary): string {
  if (r.task_name) return r.task_name;
  return (r.title || '').replace(/\s*-\s*\d{4}-\d{2}-\d{2}.*$/, '').trim() || '未命名报告';
}

/** 归集键: 服务端一处算（series_key）——任务按任务归集、「用最新数据重新生成」的产物
 * 继承来源报告、内容型一条一份、其余按标题。前端只在旧服务端未下发时兜底，
 * 不得另拼一套判断（历史缺陷：重跑产物落到另一张卡片，原报告的历史版本看不见）。*/
function editableKey(r: ReportSummary): string {
  if (r.series_key) return r.series_key;
  if (isEditableReport(r)) return `report:${r.id}`;
  return r.task_id ? `task:${r.task_id}` : `title:${r.title}`;
}

// 生成状态真实映射：degraded 现在只表示**证据不成立**（没受治理取数结果 / 正文为空），
// 判定在 `evidence_verdict` 一处；旧版把“退回事实表格”也归到 degraded（全仓写死），
// 于是所有报告都挂上个“降级”，那句提醒就没人当真了。
const GEN_STATUS: Record<string, { label: string; cls: string }> = {
  queued: { label: '排队中', cls: 'text-blue-500' },
  running: { label: '生成中', cls: 'text-blue-500' },
  ready: { label: '已就绪', cls: 'text-green-500' },
  degraded: { label: '证据不全', cls: 'text-amber-500' },
  failed: { label: '生成失败', cls: 'text-red-500' },
  timeout: { label: '生成超时', cls: 'text-yellow-500' },
  // 离开页面/点停止造成的中止：是“没跑完”，不是“跑坏了”（旧写法只标“生成失败”，
  // 把人自己关掉页面说成平台故障，也不告诉人重跑入口在哪）
  cancelled: { label: '已取消', cls: 'text-muted-foreground' },
  legacy: { label: '历史报告', cls: 'text-amber-500' },
};
const PENDING = ['queued', 'running'];
const NO_BODY = ['queued', 'running', 'failed', 'timeout', 'cancelled'];

/** 在线重跑的过程面板状态。
 *
 * 旧写法只有一句“报告生成中，页面将自动刷新”：服务端在跑什么、跑到哪一步、
 * 为什么没沿用原版式，全看不见（用户实拍）。现在逐条事件翻成人话，
 * **判定一律用服务端送的字**（不在这页再拼一套“成功/失败”判断）。
 */
interface RefreshStep {
  key: string;
  label: string;
  state: 'run' | 'ok' | 'err';
}
interface RefreshPanel {
  active: boolean;
  note: string;
  steps: RefreshStep[];
}
const EMPTY_REFRESH: RefreshPanel = { active: false, note: '', steps: [] };

const EMPTY_EDITOR: EditorState = {
  open: false, reportId: 0, title: '', content: '', format: 'markdown', saving: false,
};

export default function ReportsCenter() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const wsId = Number(workspaceId || 0);

  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeKey, setActiveKey] = useState<string>('');
  const [activeId, setActiveId] = useState<number>(0);
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  // 正文与详情分离：详情是元信息 + 直链，正文是直链指向的对象内容
  const [body, setBody] = useState('');
  const [bodyLoading, setBodyLoading] = useState(false);
  const [bodyError, setBodyError] = useState('');
  const bodyRef = useRef<HTMLDivElement>(null);
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [keyword, setKeyword] = useState('');
  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR);
  // 报告模板：报告中心只当生产者（好报告「另存为模板」沉淀回配置页）；
  // 消费方 = 定时任务表单选模板 + 报告重跑沿用上一版版式
  const [saveTpl, setSaveTpl] = useState<{ open: boolean; name: string; description: string; saving: boolean }>({
    open: false, name: '', description: '', saving: false,
  });
  // 报告复跑：「按最新数据重新生成」与「创建为定时任务」（可复用问题由服务端登记）
  // 重跑现在是本报页面上的直播（见 api/report.ts#refreshReportStream）：
  // 面板状态 + 一个能随时撕掉 SSE 的控制柄（离开页面/切版本/点停止都要中止本轮）
  const [refresh, setRefresh] = useState<RefreshPanel>(EMPTY_REFRESH);
  const refreshCtrl = useRef<AbortController | null>(null);
  // 服务端建好的新版本与终态：不放进 state（回调里读到的是旧闭包，且要等重拉后才能切过去）
  const refreshNewId = useRef(0);
  const refreshOutcome = useRef<RefreshStreamEvent | null>(null);
  const [taskDialogOpen, setTaskDialogOpen] = useState(false);
  // 提交到内容管理审核（报告 → 内容提升通道）：入口按 article:manage 置灰，原因写进 title
  const [promoteOpen, setPromoteOpen] = useState(false);
  const { has: hasPerm } = useContentPerms();

  // 报告跟随属主、跨工作空间取全量：创建工作空间暂未开放(一个账号一个默认空间),
  // 而报告本来就不按空间隔离 —— 空间筛选器给不出任何收窄效果, 已移除。
  const reload = useCallback(() =>
    listReports(0)
      .then(data => setReports(data || []))
      .catch(e => setError(e?.response?.data?.detail || e?.message || '加载报告清单失败')),
  []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    listReports(0)
      .then(data => { if (!cancelled) setReports(data || []); })
      .catch(e => {
        if (!cancelled) setError(e?.response?.data?.detail || e?.message || '加载报告清单失败');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  // 按报告归集(list 已时间倒序, 组内顺序即版本顺序)
  const series = useMemo<Series[]>(() => {
    const grouped = new Map<string, ReportSummary[]>();
    reports.forEach(r => {
      // 内容型一条即一份报告(编辑就地更新); 数据派生按任务/重跑归集为多版本
      const key = editableKey(r);
      const list = grouped.get(key);
      if (list) list.push(r); else grouped.set(key, [r]);
    });
    const kw = keyword.trim().toLowerCase();
    return Array.from(grouped.entries()).map(([key, list]) => {
      // 卡片身份取**原始报告**（最早一版 = list 末位）：会话保存的报告被「用最新数据
      // 重新生成」后，不能因为最新一版是分析产物就把整张卡片改标为「分析生成」并丢掉
      // 编辑/删除入口（历史缺陷：原会话报告重跑后卡片变成不可删的分析生成）。
      const base = list[list.length - 1];
      return {
        key, name: seriesName(base), source: (base.source_type || 'legacy') as ReportSourceType,
        editable: isEditableReport(base), deletable: isDeletableReport(base), reports: list,
      };
    }).filter(s =>
      (sourceFilter === 'all' || s.source === sourceFilter)
      && (!kw || s.name.toLowerCase().includes(kw)));
  }, [reports, sourceFilter, keyword]);

  // 默认选中最新的报告组及其最新版本
  useEffect(() => {
    if (!series.length) return;
    if (!activeKey || !series.some(s => s.key === activeKey)) {
      setActiveKey(series[0].key);
      setActiveId(series[0].reports[0].id);
    }
  }, [series, activeKey]);

  const loadDetail = useCallback((id: number) => {
    if (!id) return;
    setDetailLoading(true);
    setBodyError('');
    setBody('');
    getReportDetail(id)
      .then(data => {
        setDetail(data);
        // 排队/生成中不是错误：那句文案交给下方轮询占位渲染，不得当成红字报错摆出来；
        // 终态读不到才把原因（生成失败/超时/权限快照变化）显式回显
        setError(PENDING.includes(data.generation_status || '') ? '' : (data.access_warning || ''));
        if (!data.content_url) return undefined;   // 无直链: access_warning 已说明原因
        setBodyLoading(true);
        return fetchReportContent(data.content_url)
          .then(text => setBody(text))
          .catch(e => setBodyError(e?.message || '读取报告正文失败'))
          .finally(() => setBodyLoading(false));
      })
      .catch(e => {
        setDetail(null);
        setError(e?.response?.data?.detail || '加载报告内容失败');
      })
      .finally(() => setDetailLoading(false));
  }, []);

  useEffect(() => {
    if (activeId) loadDetail(activeId);
  }, [activeId, loadDetail]);

  // 生成中的报告自动轮询，直到进入终态；终态后重拉清单，使左侧版本标记与版本数一致。
  // 本报页面正在直播重跑时不轮询：详情会在同一份报告上反复刷新，把过程面板刷掉。
  const wasPending = useRef(false);
  useEffect(() => {
    const status = detail?.generation_status || '';
    if (!detail || !PENDING.includes(status) || refresh.active) {
      if (wasPending.current && !refresh.active) { wasPending.current = false; void reload(); }
      return;
    }
    wasPending.current = true;
    const timer = setTimeout(() => loadDetail(detail.id), 2000);
    return () => clearTimeout(timer);
  }, [detail, loadDetail, reload, refresh.active]);

  const activeSeries = series.find(s => s.key === activeKey) || null;

  const pickSeries = (s: Series) => {
    // 切到另一份报告 = 离开了正在直播的那一轮 → 中止它（用户要求：离开就终止）
    if (refresh.active) stopRefresh(false);
    setActiveKey(s.key);
    setActiveId(s.reports[0]?.id ?? 0);
    setError('');
  };

  const openSaveTpl = () => setSaveTpl({
    open: true,
    name: `${detail?.title || activeSeries?.name || '报告'} 版式模板`,
    description: '', saving: false,
  });

  const submitSaveTpl = async () => {
    if (!detail) return;
    setSaveTpl(prev => ({ ...prev, saving: true }));
    try {
      const out = await saveReportAsTemplate(detail.id, {
        name: saveTpl.name.trim(), description: saveTpl.description.trim(),
      });
      toast.success(`模板「${out.name}」已沉淀：可在任务表单选模板、报告重跑时沿用，也可去系统配置-报告模板调版式`);
      setSaveTpl({ open: false, name: '', description: '', saving: false });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '另存为模板失败，请重试');
      setSaveTpl(prev => ({ ...prev, saving: false }));
    }
  };

  const openEdit = (r: ReportDetail, bodyText: string) => setEditor({
    open: true, reportId: r.id, title: r.title || '',
    content: bodyText, format: r.format || 'markdown', saving: false,
  });

  /** 列表与详情都不带正文, 编辑前按直链取回正文再进编辑器 */
  const editSeries = async (s: Series) => {
    try {
      const d = await getReportDetail(s.reports[0].id);
      setActiveKey(s.key);
      setActiveId(d.id);
      const text = d.content_url ? await fetchReportContent(d.content_url) : '';
      if (!text.trim()) { toast.error('报告正文为空，无法编辑；请先重新生成或重新保存'); return; }
      openEdit(d, text);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || '读取报告正文失败，请重试');
    }
  };

  const saveEditor = async () => {
    if (!editor.title.trim()) { toast.error('报告标题不能为空'); return; }
    if (!editor.content.trim()) { toast.error('报告正文不能为空'); return; }
    setEditor(prev => ({ ...prev, saving: true }));
    try {
      await updateReport(editor.reportId, {
        title: editor.title, content: editor.content, format: editor.format,
      });
      toast.success('报告已更新');
      await reload();
      loadDetail(editor.reportId);   // 正文已换新对象, 重取直链而不是复用旧正文
      setEditor(EMPTY_EDITOR);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败，请重试');
      setEditor(prev => ({ ...prev, saving: false }));
    }
  };

  /** 删整份报告（卡片上的垃圾桶）：按归集键带走全部历史版本，确认框先说清删几版。 */
  const removeSeries = async (s: Series) => {
    const versions = s.reports.length;
    const suffix = versions > 1 ? `（连同 ${versions} 个历史版本一起删除）` : '';
    if (!window.confirm(`确定删除整份报告「${s.name}」${suffix}？删除后不可恢复。`)) return;
    try {
      // 归集键同一组的 id 都能代删（服务端拿第一行的键反查整组），传最新一版即可
      await deleteReport(s.reports[0].id, 'series');
      toast.success('报告已删除');
      // 删的是当前选中卡片时清空选中（整份报告已不在，不能只按 latest id 判）
      if (activeKey && activeKey === s.key) { setActiveKey(''); setActiveId(0); setDetail(null); }
      await reload();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败，请重试');
    }
  };

  /** 只删当前选中的这一版：同系列其余历史版本保留。
   *
   * 旧写法这里走的也是“按归集键删整组”：在版本下拉里选中 v3 点删除，v1/v2 连同卡片一起没了。
   * 删一版与删整份是两个意图，所以页面上也列两条入口，不再让人猜垃圾桶到底删多少。
   */
  const removeVersion = async (r: ReportDetail) => {
    const s = activeSeries;
    if (!s || s.reports.length < 2) return;
    const idx = s.reports.findIndex(x => x.id === r.id);
    if (idx < 0) return;
    const label = `v${s.reports.length - idx}（${formatDate(r.created_at)}）`;
    const lines = [`确定删除报告「${s.name}」的 ${label} 这一版？`,
                   `其余 ${s.reports.length - 1} 个历史版本保留，删除后不可恢复。`];
    // 最早一版 = 原始报告（卡片身份那一版）：删它后卡片的来源与编辑入口会改按剩余最早版算，
    // 后果必须在删之前说出来，不能让人事后发现“卡片变成不可编辑了”
    if (idx === s.reports.length - 1) {
      lines.push('这一版是最早的原始报告：删除后这张卡片的来源与编辑入口将改按剩余最早的一版计算。');
    }
    if (!window.confirm(lines.join('\n'))) return;
    const next = s.reports.find(x => x.id !== r.id);   // 清单按时间倒序 → 剩余里最新的一版
    try {
      const out = await deleteReport(r.id, 'version');
      toast.success(`已删除 ${label}，剩余 ${out?.remaining ?? s.reports.length - 1} 个版本`);
      await reload();
      if (next) setActiveId(next.id);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败，请重试');
    }
  };

  /** 离开直播 = 中止本轮生成（用户要求）：断开 SSE 连接，服务端探到断连就停下，
   *  新版本记为「已取消」，之前那些版本一字不动。`silent` 只给 unmount 用
   *  （页面都没了，弹一句 toast 没人看得见）。 */
  const stopRefresh = useCallback((silent = false) => {
    refreshCtrl.current?.abort();
    refreshCtrl.current = null;
    setRefresh(EMPTY_REFRESH);
    if (!silent) {
      toast.info('已停止本次重新生成：新版本记为已取消，之前生成的版本不受影响');
    }
  }, []);

  // 离开报告中心（路由跳走/组件卸载）同样要终止：后台续跑一轮取数不是用户的意思
  useEffect(() => () => { refreshCtrl.current?.abort(); }, []);

  /** 过一条事件就更新一次面板：只把服务端送的字译成人话，不自己判结局。 */
  const onRefreshEvent = useCallback((ev: RefreshStreamEvent) => {
    if (ev.type === 'done') refreshOutcome.current = ev;
    if (ev.report_id) refreshNewId.current = ev.report_id;
    setRefresh(prev => {
      const steps = [...prev.steps];
      // 同 key 就地更新（一步从“执行中”变成“完成 2.3s”），不同 key 才新增一行
      const put = (key: string, label: string, state: RefreshStep['state'] = 'run') => {
        const at = steps.findIndex(s => s.key === key);
        if (at >= 0) { if (steps[at].label !== label || steps[at].state !== state) steps[at] = { key, label, state }; }
        else steps.push({ key, label, state });
      };
      let note = prev.note;
      if (ev.type === 'stage') {
        note = ev.message || note;
        if (ev.step) put(`stage:${ev.step}`, ev.message || ev.step, 'ok');
      } else if (ev.type === 'capacity') {
        note = ev.estimated_wait_s != null
          ? `平台正忙，你在第 ${ev.position || 1} 位（预计约 ${Math.max(1, Math.round(ev.estimated_wait_s))} 秒）`
          : `平台正忙，你在第 ${ev.position || 1} 位`;
        put('capacity', note);
      } else if (ev.type === 'data') {
        // 取数结果只报“第几条 / 多少行 / 有无截断 / 哪条受治理通道”，不报 SQL 与表列
        note = `已完成 ${ev.query_count || 1} 条受治理取数…`;
        put(`query:${ev.query_count || steps.filter(s => s.key.startsWith('query:')).length + 1}`,
          `取数 #${ev.query_count || 1}：${ev.source || '受治理查询'} 返回 ${ev.row_count || 0} 行`
          + (ev.truncated ? '（被行数上限截断，仅代表样本）' : ''), 'ok');
      } else if (ev.type === 'tool_start') {
        note = `${getToolLabel(ev.tool || '')} 执行中…`;
        put(`tool:${ev.tool_call_id || ev.call_index || steps.length}`, `${getToolLabel(ev.tool || '')} 执行中`);
      } else if (ev.type === 'tool_result') {
        const secs = typeof ev.elapsed === 'number' ? ` · ${ev.elapsed.toFixed(1)}s` : '';
        put(`tool:${ev.tool_call_id || ev.call_index || steps.length}`,
          `${getToolLabel(ev.tool || '')}${ev.ok ? ' 完成' : ' 未成功'}${secs}`,
          ev.ok ? 'ok' : 'err');
      } else if (ev.type === 'text') {
        note = `模型正在写正文（已 ${ev.chars || 0} 字）…`;
        put('answer', note);
      } else if (ev.type === 'done') {
        note = ev.generation_status === 'ready' ? '新版生成完成' : (ev.reason || '本轮未正常完成');
        steps.forEach((s, i) => { if (s.state === 'run') steps[i] = { ...s, state: ev.generation_status === 'ready' ? 'ok' : 'err' }; });
      }
      return { ...prev, steps, note };
    });
  }, []);

  /** 按最新数据重新生成：报告自己可重跑就在本报告下直播过整一轮，产出一个历史版本；
   *  任务产出的报告去触发所属任务（不另开一条链路）。 */
  const refreshWithLatest = async () => {
    if (!detail) return;
    const info = detail.refresh;
    if (info && !info.available) { toast.error(info.hint || '该报告无法按最新数据重新生成'); return; }
    if (info?.channel === 'task') {
      if (!detail.task_id) { toast.error('该报告的任务归属信息缺失，无法触发重跑'); return; }
      try {
        await triggerScheduledTask(detail.task_id);
        toast.success('已触发所属定时任务，新版本稍后出现在本报告的历史版本里');
      } catch (e: any) {
        toast.error(e?.response?.data?.detail || '触发定时任务失败，请重试');
      }
      return;
    }
    const controller = new AbortController();
    refreshCtrl.current = controller;
    refreshNewId.current = 0;
    refreshOutcome.current = null;
    setRefresh({ active: true, note: '正在准备新版本…', steps: [] });
    try {
      await refreshReportStream(detail.id, { onEvent: onRefreshEvent, signal: controller.signal });
    } catch (e: any) {
      if (controller.signal.aborted) return;   // 用户自己点的停止，不再补一句错误
      toast.error(e?.message || '重新生成未能开始，请稍后重试');
      setRefresh(EMPTY_REFRESH);
      refreshCtrl.current = null;
      return;
    }
    if (controller.signal.aborted) return;
    // 这一行要显式断言：上面刚写过 `refreshOutcome.current = null`，
    // 控制流分析会把读到的值当作 null（再走 `if (!outcome) return` 就成了 never）。
    const outcome = refreshOutcome.current as RefreshStreamEvent | null;
    refreshCtrl.current = null;
    setRefresh(EMPTY_REFRESH);
    if (!outcome) {
      // 流断了但没收到终态：报告到底怎么样了以库里为准，重拉详情而不是猜一个结局
      toast.error('重新生成过程连接中断，已重拉最新版本确认实际结果');
      await reload();
      if (refreshNewId.current) setActiveId(refreshNewId.current);
      return;
    }
    await reload();
    if (refreshNewId.current) setActiveId(refreshNewId.current);
    const status = outcome.generation_status || '';
    if (status === 'ready') {
      const badge = layoutBadge(outcome.layout_result);
      toast.success(badge ? `已按最新数据生成新版本（${badge}）` : '已按最新数据生成新版本');
    } else if (status === 'cancelled') {
      toast.info('本轮已中止，新版本记为已取消；之前的版本仍可正常查看');
    } else {
      toast.error(outcome.reason
        || `本轮未能生成可用版本（${GEN_STATUS[status]?.label || status || '未完成'}），原因见版本标记`);
    }
  };

  /** 次要动作收进「更多」（用户实拍：头栏按钮越加越挤，标题都被顶没了）。
   *
   * 常驻只留三件事：选版本、按最新数据重跑、导出；其余不是删功能，是不再各占一位。
   * 文案与置灰原因跟原按钮一字不差 —— 收纳不等于藏起来，入口看得见、为什么不能点也看得见。 */
  const buildMoreItems = (): OverflowItem[] => {
    if (!detail) return [];
    const hasBody = !!body.trim();
    const items: OverflowItem[] = [
      {
        key: 'task', label: '创建为定时任务', icon: <CalendarClock className="h-3.5 w-3.5" />,
        title: '复用本报告的分析问题，创建周期执行的 Agent 定时任务（问题正文在弹窗里可改）',
        onSelect: () => setTaskDialogOpen(true),
      },
      {
        // 好报告的版式不该只留在这一次：沉淀成模板后，定时任务表单与报告重跑共用同一份版式
        key: 'template', label: '另存为模板', icon: <Layers className="h-3.5 w-3.5" />,
        disabled: !hasBody,
        title: hasBody
          ? '把本报告的章节与图表结构存成报告模板（只搬版式，不带数据）'
          : '正文还没加载完成，无法抽版式',
        onSelect: openSaveTpl,
      },
    ];
    if (isEditableReport(detail)) {
      items.push({
        key: 'edit', label: '编辑', icon: <Pencil className="h-3.5 w-3.5" />,
        disabled: !hasBody,
        title: hasBody ? '重命名 / 编辑正文' : '正文尚未加载完成，无法编辑',
        onSelect: () => openEdit(detail, body),
      });
    }
    // 好报告不只留在个人报告中心：提交给内容团队走「审核 → 发布」对外发布。
    // 入口的可用性判据与端点同一个码（article:manage）：没权限也照样列出来并写明置灰原因，
    // 不让人以为“报告中心没这个功能”（menu-permission-sync / no-silent-degradation）。
    const canPromote = hasPerm('article:manage');
    items.push({
      key: 'promote', label: '提交到内容管理审核', icon: <Send className="h-3.5 w-3.5" />,
      disabled: !hasBody || !canPromote,
      title: !canPromote
        ? '需要「编辑内容条目」（article:manage）权限才能把报告提交到内容管理：当前账号没有这个权限，请找内容同事代提'
        : !hasBody
          ? '正文还没加载完成，暂时提不了'
          : '复制正文成内容中心的一个新版本（同报告系列自动追加为新一期），随后走审核与发布',
      onSelect: () => setPromoteOpen(true),
    });
    if (isDeletableReport(detail)) {
      const versions = activeSeries?.reports.length ?? 1;
      if (versions > 1) {
        // 只删当前这一版：重跑追加的失败版/旧版可以单独清掉，不必连原始报告一起没
        items.push({
          key: 'delete-version', label: '删除这一版', icon: <Trash2 className="h-3.5 w-3.5" />,
          danger: true, separatorBefore: true,
          disabled: refresh.active,
          title: refresh.active
            ? '正在按最新数据重新生成，先停掉这一轮再删版本'
            : `只删当前选中的这一版，同系列其余 ${versions - 1} 个历史版本保留`,
          onSelect: () => void removeVersion(detail),
        });
      }
      items.push({
        key: 'delete',
        label: versions > 1 ? `删除整份报告（含 ${versions} 个版本）` : '删除',
        icon: <Trash2 className="h-3.5 w-3.5" />,
        danger: true, separatorBefore: versions <= 1,
        title: versions > 1 ? '连同这份报告的全部历史版本一起删除，不可恢复' : '删除这份报告，不可恢复',
        onSelect: () => { if (activeSeries) void removeSeries(activeSeries); },
      });
    }
    return items;
  };

  return (
    <div className="h-full flex flex-col gap-4 p-6 overflow-hidden">
      <div className="flex items-center gap-2 shrink-0 flex-wrap">
        <FileBarChart className="h-5 w-5 text-primary" />
        <h1 className="text-lg font-semibold">报告中心</h1>
        <span className="text-sm text-muted-foreground">
          报告跟随账号，在线实时查看；报告都是分析产物（会话存为报告 / 定时任务产出），按版本归集
        </span>
      </div>

      {/* 筛选行: 来源 / 关键词。没有工作空间筛选器 —— 创建工作空间暂未开放,
          且报告跟随属主跨空间, 该筛选给不出任何收窄效果(留着一个动不了的开关
          比没有更糟: 角色页会把它当成可配的能力)。 */}
      <div className="flex items-center gap-2 shrink-0 flex-wrap">
        <Select value={sourceFilter} onValueChange={setSourceFilter}>
          <SelectTrigger className="w-[140px] h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部来源</SelectItem>
            {SOURCE_ORDER.map(s => (
              <SelectItem key={s} value={s}>{SOURCE_META[s].label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input value={keyword} onChange={e => setKeyword(e.target.value)}
            placeholder="按报告名称搜索" className="h-8 w-56 pl-7 text-xs" />
        </div>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中...
        </div>
      ) : !series.length ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground border border-dashed rounded-lg bg-card">
          <Inbox className="h-10 w-10 opacity-50" />
          <p className="text-sm">{error || '暂无报告：报告是分析产物，请在智能问数里把分析结果「存为报告」，或交给定时任务产出'}</p>
        </div>
      ) : (
        <div className="flex-1 flex gap-4 min-h-0">
          {/* 左栏: 报告清单(任务型按版本归集) */}
          <div className="w-72 shrink-0 overflow-y-auto border rounded-lg bg-card">
            {series.map(s => {
              const latest = s.reports[0];
              const active = s.key === activeKey;
              return (
                <div key={s.key}
                  className={`w-full px-3 py-2.5 border-b last:border-b-0 transition-colors ${
                    active ? 'bg-primary/10 border-l-2 border-l-primary' : 'hover:bg-muted/50'
                  }`}>
                  <div className="flex items-center gap-2">
                    <button onClick={() => pickSeries(s)} className="flex-1 min-w-0 text-left">
                      <span className={`block text-sm font-medium truncate ${active ? 'text-primary' : ''}`}>
                        {s.name}
                      </span>
                    </button>
                    {s.reports.length > 1 && (
                      <Badge variant="outline" className="shrink-0 text-[10px]">
                        {s.reports.length} 个版本
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <Badge variant="outline" className={`text-[10px] ${SOURCE_META[s.source]?.cls || ''}`}>
                      {SOURCE_META[s.source]?.label || s.source}
                    </Badge>
                    <span className="text-xs text-muted-foreground truncate">
                      最新 {latest ? new Date(latest.created_at).toLocaleDateString() : '-'}
                    </span>
                    {s.editable && (
                      <Button variant="ghost" size="sm" className="h-6 w-6 p-0 ml-auto" title="重命名 / 编辑正文"
                        onClick={() => void editSeries(s)}>
                        <Pencil className="h-3 w-3" />
                      </Button>
                    )}
                    {s.deletable && (
                      <Button variant="ghost" size="sm"
                        className={`h-6 w-6 p-0 text-destructive ${s.editable ? '' : 'ml-auto'}`}
                        title={s.reports.length > 1
                          ? `删除整份报告（含 ${s.reports.length} 个历史版本）；只想删某一版请进详情页用「删除这一版」`
                          : '删除报告'}
                        onClick={() => void removeSeries(s)}>
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* 右栏: 报告详情 */}
          <div className="flex-1 min-w-0 flex flex-col border rounded-lg bg-card overflow-hidden">
            <div className="shrink-0 px-5 py-3 border-b flex items-center justify-between gap-3 flex-wrap gap-y-2">
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-semibold truncate">{detail?.title || activeSeries?.name || '选择报告'}</h2>
                <div className="flex items-center gap-2 flex-wrap mt-0.5 text-xs text-muted-foreground">
                  {detail && (
                    <>
                      <Badge variant="outline" className="text-[10px]">
                        {detail.format === 'html' ? 'HTML' : 'Markdown'}
                      </Badge>
                      {/* 会话来源不单独占一行：会话标题与报告标题讲的是同一件事，
                          并排显示等于把标题写两遍（用户反馈）。「会话保存」徽章已说明来源，
                          具体会话号收进徽章 title 供排查。 */}
                      <Badge variant="outline"
                        title={detail.origin?.channel === 'chat' && detail.origin?.conversation_id
                          ? `来自会话 #${detail.origin.conversation_id}` : undefined}
                        className={`text-[10px] ${SOURCE_META[detail.source_type || 'legacy']?.cls || ''}`}>
                        {SOURCE_META[detail.source_type || 'legacy']?.label || detail.source_type}
                      </Badge>
                      {detail.generation_status && GEN_STATUS[detail.generation_status] && (
                        <Badge variant="outline" className={`text-[10px] ${GEN_STATUS[detail.generation_status].cls}`}>
                          {GEN_STATUS[detail.generation_status].label}
                        </Badge>
                      )}
                      <span>{formatDate(detail.created_at)}</span>
                      <span className="flex items-center gap-1">
                        <Eye className="h-3 w-3" /> {detail.view_count}
                      </span>
                    </>
                  )}
                </div>
              </div>
              {/* 常驻只留三件事：重跑、导出、（多版时）选版本；次要动作收进「更多」（用户实拍：按钮越加越挤）*/}
              <div className="shrink-0 flex items-center gap-2 ml-auto flex-wrap justify-end">
                {detail && (
                  /* 可用性只消费服务端 refresh（available/action_label/hint），前端不再拼第二套判断：
                     手写/历史报告没有可复跑问题 → 置灰 + 原因（旧写法按钮看着能点，点了才弹一句错误）。*/
                  <Button variant="outline" size="sm" className="h-8 text-xs"
                    onClick={refreshWithLatest}
                    disabled={refresh.active || !detail.refresh || !detail.refresh.available}
                    title={detail.refresh?.hint || '按报告登记的原始问题重新取数，过程在本页直播'}>
                    {refresh.active
                      ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      : <RefreshCw className="h-3 w-3 mr-1" />}
                    {refresh.active ? '重新生成中…' : (detail.refresh?.action_label || '用最新数据重新生成')}
                  </Button>
                )}
                {detail && (
                  <ReportExportMenu
                    title={detail.title || activeSeries?.name || '报告'}
                    createdAt={detail.created_at}
                    bodyFormat={detail.format}
                    body={body}
                    getContainer={() => bodyRef.current}
                  />
                )}
                {/* 版本管理: 所选任务的历史报告(时间倒序)。只有一版时不给下拉 ——
                    一个只能选中自己的“版本管理”是噪声（日期与状态上方的徽章已说清）。 */}
                {activeSeries && activeSeries.reports.length > 1 && (
                  <select
                    value={activeId}
                    onChange={e => {
                      // 切版本 = 离开正在直播的那一轮（与切卡片同一个道理：离开就终止）
                      if (refresh.active) stopRefresh(false);
                      setActiveId(Number(e.target.value));
                    }}
                    className="shrink-0 max-w-[220px] h-8 rounded-md border bg-background px-2 text-xs"
                    title="版本管理"
                  >
                    {activeSeries.reports.map((r, i) => (
                      // 版本名带上生成状态：失败/超时的版本只看时间看不出来，
                      // 切过去只会得到一句报错，不知道哪一版才是有正文的那一版
                      <option key={r.id} value={r.id}>
                        v{activeSeries.reports.length - i} · {formatDate(r.created_at)}
                        {r.generation_status && r.generation_status !== 'ready'
                          ? ` · ${GEN_STATUS[r.generation_status]?.label || r.generation_status}` : ''}
                        {/* 同一份报告的新版式变了要能在选版本时看出来（否则只能切过去才发现） */}
                        {layoutBadge(r.layout) ? ` · ${layoutBadge(r.layout)}` : ''}
                      </option>
                    ))}
                  </select>
                )}
                {detail && <ActionOverflowMenu items={buildMoreItems()} />}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-5">
              {/* 重跑过程面板：服务端逐条送事件（取了几次数、走到哪个工具、正文在不在写），
                  旧写法只剩一句“生成中，页面将自动刷新”——看不到过程，离开页面它照跑。
                  期间旧正文继续可读（新版本只在跑完那一瞬才切过去）。 */}
              {refresh.active && (
                <div data-testid="refresh-panel" className="mb-4 rounded-lg border bg-muted/40 px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-xs font-medium flex items-center gap-2">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      正在按最新数据重新生成
                    </p>
                    <Button variant="ghost" size="sm" className="h-7 text-xs"
                      title="停止本轮生成（新版本记为已取消，已生成的历史版本不受影响）"
                      onClick={() => stopRefresh(false)}>
                      <X className="h-3 w-3 mr-1" /> 停止
                    </Button>
                  </div>
                  <p data-testid="refresh-note" className="mt-1 text-xs text-muted-foreground">{refresh.note}</p>
                  {refresh.steps.length > 0 && (
                    <ol className="mt-2 space-y-1">
                      {refresh.steps.map(s => (
                        <li key={s.key} data-testid={`refresh-step-${s.state}`}
                            className="flex items-center gap-2 text-[11px] text-muted-foreground">
                          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                            s.state === 'err' ? 'bg-red-500' : s.state === 'ok' ? 'bg-green-500' : 'bg-blue-500 animate-pulse'}`} />
                          <span className="truncate">{s.label}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              )}
              {error ? (
                <p className="text-sm text-destructive">{error}</p>
              ) : detailLoading || !detail ? (
                <div className="h-full flex items-center justify-center text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载报告内容...
                </div>
              ) : NO_BODY.includes(detail.generation_status || '') ? (
                <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                  {PENDING.includes(detail.generation_status || '')
                    ? <><Loader2 className="h-4 w-4 animate-spin mr-2" /> 报告生成中（由定时任务或其他页面触发），页面将自动刷新...</>
                    : (detail.generation_status === 'cancelled'
                        ? '这一版在重新生成过程中页面被关闭，已按取消中止；可再点一次「用最新数据重新生成」。'
                        : '本次运行未生成可展示的报告正文（生成失败或超时）。')}
                </div>
              ) : bodyError ? (
                /* 正文取不到必须显式暴露原因（直链过期 / 桶没开跨域 / 对象丢失），
                   并给一个换直链重试的入口 —— 静默渲染空白会被当成"报告没内容"。 */
                <div role="alert" className="h-full flex flex-col items-center justify-center gap-3 text-sm">
                  <p className="text-destructive max-w-prose text-center">{bodyError}</p>
                  <Button variant="outline" size="sm" className="h-8 text-xs"
                    onClick={() => loadDetail(detail.id)}>
                    <RefreshCw className="h-3 w-3 mr-1" /> 重新获取正文
                  </Button>
                </div>
              ) : bodyLoading || !body.trim() ? (
                <div className="h-full flex items-center justify-center text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载报告正文...
                </div>
              ) : (
                <>
                  {/* 证据口径只翻译服务端判定：ready 且未截断不打扰，样本/证据不成立都要显式说 */}
                  {!!evidenceNote(detail.evidence_summary) && (
                    <p data-testid="evidence-note" className="mb-3 text-xs text-muted-foreground">
                      {evidenceNote(detail.evidence_summary)}
                    </p>
                  )}
                  {/* 没沿用原版式必须显式说原因（上一版正文读不到 / 模型未交稿 / 缺章节图表），
                      否则用户只看到“新报告长得不一样”，会当成报告中心把报告改坏了 */}
                  {layoutNote(detail.layout) && (
                    <p role="note" className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                      {layoutNote(detail.layout)}
                    </p>
                  )}
                  {/* data-report-print: 导出 PDF 时 globals.css 的打印规则只露这一块 */}
                  <div ref={bodyRef} data-report-print>
                    <ReportContent content={body} format={detail.format} />
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 新建 / 编辑报告 */}
      <Dialog open={editor.open} onOpenChange={o => { if (!o) setEditor(EMPTY_EDITOR); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>编辑报告</DialogTitle>
            <DialogDescription>
              内容型报告（会话存为报告、改造前的手写报告）仅本人可见、可在线查看与再次编辑；
              定时任务/分析来源产出的报告正文由受治理取数生成，不在此编辑。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="report-title">报告名称</Label>
              <Input id="report-title" value={editor.title}
                onChange={e => setEditor(prev => ({ ...prev, title: e.target.value }))}
                placeholder="业务可读名称，如「新加坡近30天新增案例」" />
            </div>
            <div className="flex items-center gap-3">
              <Label className="shrink-0">正文格式</Label>
              <Select value={editor.format}
                onValueChange={v => setEditor(prev => ({ ...prev, format: v }))}>
                <SelectTrigger className="w-[160px] h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="markdown">Markdown</SelectItem>
                  <SelectItem value="html">HTML</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="report-content">报告正文</Label>
              <Textarea id="report-content" value={editor.content}
                onChange={e => setEditor(prev => ({ ...prev, content: e.target.value }))}
                placeholder={editor.format === 'html'
                  ? '<h1>报告标题</h1><p>整份自包含 HTML（样式写进正文的 <style>）</p>'
                  : '报告标题的下一行开始写正文…'}
                className="font-mono text-xs min-h-[320px]" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditor(EMPTY_EDITOR)} disabled={editor.saving}>取消</Button>
            <Button onClick={saveEditor} disabled={editor.saving}>
              {editor.saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />保存中…</> : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 把本报告的版式沉淀成报告模板（只抽结构，不带正文里的数字） */}
      <Dialog open={saveTpl.open} onOpenChange={o => { if (!o) setSaveTpl({ open: false, name: '', description: '', saving: false }); }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>另存为报告模板</DialogTitle>
            <DialogDescription>
              只沉淀版式（章节标题 + 图表规格 + 主题），正文里的数据一个也不进模板；
              没有可抽结构的报告会被服务端拒回并说明原因。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>模板名称</Label>
              <Input value={saveTpl.name} onChange={e => setSaveTpl(prev => ({ ...prev, name: e.target.value }))}
                placeholder="如：周报版式模板" />
            </div>
            <div className="space-y-1.5">
              <Label>适用场景（可选）</Label>
              <Input value={saveTpl.description}
                onChange={e => setSaveTpl(prev => ({ ...prev, description: e.target.value }))}
                placeholder="如：面向区域运营的同款周报" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline"
              onClick={() => setSaveTpl({ open: false, name: '', description: '', saving: false })}
              disabled={saveTpl.saving}>取消</Button>
            <Button onClick={submitSaveTpl} disabled={saveTpl.saving || !saveTpl.name.trim()}>
              {saveTpl.saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />保存中…</> : '存为模板'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 从本报告快捷创建同款 Agent 定时任务（复用服务端登记的问题正文） */}
      <ReportTaskDialog
        open={taskDialogOpen}
        report={detail}
        workspaceId={detail?.workspace_id || wsId}
        onClose={() => setTaskDialogOpen(false)}
      />

      {/* 提升通道：归集预判在点下去之前就要看得见（服务端算，前端不猜） */}
      <ReportPromoteDialog
        open={promoteOpen}
        reportId={detail?.id ?? 0}
        reportTitle={detail?.title || activeSeries?.name || ''}
        onClose={() => setPromoteOpen(false)}
      />
    </div>
  );
}
