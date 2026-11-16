// 报告中心: 报告跟随属主(跨工作空间), 支持管理「不一定是定时任务产出」的报告。
// 与资产清单的分工: 资产=归档的文件(下载复用), 报告=稳定内容(在线实时查看)。
// 正文不在详情响应里: 详情回「域名+签名」的直链, 本页 fetch 直链取正文(见 api/report.ts)。
// 归集口径: 同归集键(series_key, 服务端一处算)的多行是同一份报告的历史版本 ——
//          任务按任务归集、「用最新数据重新生成」继承来源报告、内容型一条一份、其余按标题。
// 卡片身份取原始报告(最早一版): 会话保存的报告重跑后仍是「会话保存」，可编辑/删除。
// 删除分两个意图（不得共用一个“删除”）：卡片垃圾桶 = 删整份报告（含全部历史版本）；
// 详情页「删除这一版」= 只删当前选中的那一版。定时任务产出留给任务调度。
import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import {
  FileBarChart, Loader2, Eye, Inbox, Pencil, Trash2, Search,
  RefreshCw, CalendarClock, Layers, X, Send, Newspaper, Maximize2,
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
import ArticleReader from '@/pages/home/ArticleReader';
import { contentApi, type ContentArticle } from '@/api/content';
import { triggerScheduledTask } from '@/api/scheduledTask';
import {
  listReports, getReportDetail, updateReport, deleteReport,
  refreshReportStream, fetchReportContent, isEditableReport, isDeletableReport,
  saveReportAsTemplate,
  type ReportSummary, type ReportDetail, type ReportSourceType, type RefreshStreamEvent,
} from '@/api/report';
import { getToolLabel } from '@/lib/toolLabel';
import { evidenceNote, layoutBadge, layoutNote } from '@/lib/reportLayout';
import i18n from '@/i18n';

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

const SOURCE_META: Record<string, { cls: string }> = {
  scheduled: { cls: 'text-blue-600 border-blue-200' },
  analysis: { cls: 'text-indigo-600 border-indigo-200' },
  manual: { cls: 'text-emerald-600 border-emerald-200' },
  conversation: { cls: 'text-teal-600 border-teal-200' },
  legacy: { cls: 'text-amber-600 border-amber-200' },
  // 内容中心已发布内容（不是报告来源，仅供合并清单的徽章配色/取色复用）
  content: { cls: 'text-purple-600 border-purple-200' },
};
const SOURCE_ORDER: ReportSourceType[] = ['scheduled', 'analysis', 'conversation', 'manual', 'legacy'];

/** 来源/状态的业务名：key 命中语言包则译，未登记的原始值照显（不编文案） */
const sourceLabel = (s: string): string =>
  i18n.exists(`report:center.source.${s}`) ? i18n.t(`report:center.source.${s}` as 'report:center.source.scheduled') : s;
const statusLabel = (s: string): string =>
  i18n.exists(`report:center.status.${s}`) ? i18n.t(`report:center.status.${s}` as 'report:center.status.queued') : s;

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(i18n.language);
}

/** 任务报告标题形如「任务名 - 2026-07-05」, 归集名只留任务名(显示用 name) */
function seriesName(r: ReportSummary): string {
  if (r.task_name) return r.task_name;
  return (r.title || '').replace(/\s*-\s*\d{4}-\d{2}-\d{2}.*$/, '').trim() || i18n.t('report:center.untitled');
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
const GEN_STATUS: Record<string, { cls: string }> = {
  queued: { cls: 'text-blue-500' },
  running: { cls: 'text-blue-500' },
  ready: { cls: 'text-green-500' },
  degraded: { cls: 'text-amber-500' },
  failed: { cls: 'text-red-500' },
  timeout: { cls: 'text-yellow-500' },
  // 离开页面/点停止造成的中止：是“没跑完”，不是“跑坏了”（旧写法只标“生成失败”，
  // 把人自己关掉页面说成平台故障，也不告诉人重跑入口在哪）
  cancelled: { cls: 'text-muted-foreground' },
  legacy: { cls: 'text-amber-500' },
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
  const { t } = useTranslation(['report', 'common', 'menu']);
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
  const { has: hasPerm, loaded: contentPermsLoaded } = useContentPerms();
  const navigate = useNavigate();
  // 内容中心已发布内容混入本报告清单（只读消费，点击跳 /read/:slug）：可见性由 article:read 裁决，
  // 前端只据此决定发不发请求、列不列条目，服务端仍是唯一门（与首页资讯区同一把码）。
  const canReadContent = hasPerm('article:read');
  const [articles, setArticles] = useState<ContentArticle[]>([]);
  const [articlesError, setArticlesError] = useState('');
  // 内容内嵌阅读：选中哪篇（slug）与在读第几期（本地态，不进 URL）；置空 = 回到报告详情。
  const [activeArticleSlug, setActiveArticleSlug] = useState('');
  const [articleRelease, setArticleRelease] = useState(0);
  // 落地自动展示首项不计阅读（false），用户主动点击内容才计（true）——避免每次进报告中心都顶高门户「最多阅读」
  const [articleCountView, setArticleCountView] = useState(false);
  // 内容清单是否已落定（resolve 或 error）：自动选中首项需等它，否则报告先返回时会抢在内容之前锁错首项
  const [articlesSettled, setArticlesSettled] = useState(false);

  // 报告跟随属主、跨工作空间取全量：创建工作空间暂未开放(一个账号一个默认空间),
  // 而报告本来就不按空间隔离 —— 空间筛选器给不出任何收窄效果, 已移除。
  const reload = useCallback(() =>
    listReports(0)
      .then(data => setReports(data || []))
      .catch(e => setError(e?.response?.data?.detail || e?.message || i18n.t('report:center.loadListFailed'))),
  []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    listReports(0)
      .then(data => { if (!cancelled) setReports(data || []); })
      .catch(e => {
        if (!cancelled) setError(e?.response?.data?.detail || e?.message || i18n.t('report:center.loadListFailed'));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  // 内容中心已发布内容：空 status = 服务端只回在线（live）条目，草稿/待审在服端一道门里被挡。
  // 权限未就绪前不发请求（否则把注定 403 的请求当“没内容”）；失败显式报错 + 重试，不回退成空清单。
  const reloadArticles = useCallback(() => {
    setArticlesError('');
    setArticlesSettled(false);
    contentApi.list({ sort: 'latest', page_size: 50 })
      .then(({ data }) => { setArticles(data.items || []); setArticlesSettled(true); })
      .catch(() => { setArticles([]); setArticlesError(t('center.contentLoadFailed')); setArticlesSettled(true); });
  }, [t]);

  useEffect(() => {
    if (!contentPermsLoaded || !canReadContent) return;
    reloadArticles();
  }, [contentPermsLoaded, canReadContent, reloadArticles]);

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

  // 内容条目的排序日：优先发布时间，回退更新时间（与资讯流口径一致）。
  const articleDate = (a: ContentArticle): string => a.published_at || a.updated_at || '';

  // 左栏统一清单：报告（按版本归集的 series）与内容中心已发布内容**并集**，按日期倒序。
  // 来源筛选：'content' 只列内容（series 无此来源自然为空）、具体报告来源只列报告、'all' 列并集。
  // 内容只在具备 article:read 时才入列；内容条目不参与右栏详情（点击跳 /read/:slug）。
  type ListEntry =
    | { kind: 'report'; date: string; series: Series }
    | { kind: 'content'; date: string; article: ContentArticle };
  const entries = useMemo<ListEntry[]>(() => {
    const kw = keyword.trim().toLowerCase();
    const reportEntries: ListEntry[] = series.map(s => ({
      kind: 'report' as const, date: s.reports[0]?.created_at || '', series: s,
    }));
    const contentOn = canReadContent && (sourceFilter === 'all' || sourceFilter === 'content');
    const contentEntries: ListEntry[] = contentOn
      ? articles
          .filter(a => !kw || (a.title || '').toLowerCase().includes(kw))
          .map(a => ({ kind: 'content' as const, date: articleDate(a), article: a }))
      : [];
    return [...reportEntries, ...contentEntries]
      .sort((x, y) => (y.date || '').localeCompare(x.date || ''));
  }, [series, articles, canReadContent, sourceFilter, keyword]);

  // 自动选中首项的门控：报告清单加载完 +（若可读内容）内容清单已落定。
  // 不等内容就会“报告先回→锁成报告→内容后到不再改选”，使「进入即展示第一条（资讯）」在竞态下落空。
  // （权限在登录时就已就绪，canReadContent 首帧即正确；无需再等 contentPermsLoaded，否则不加载权限 store 的报告场景会选不中。）
  const canAutoSelect = !loading && (!canReadContent || articlesSettled);

  // 内容条目就地内嵌阅读（不离开报告中心）；全屏按钮才 navigate 到独立阅读页。
  // 切进内容 = 离开正在直播的那轮，中止它（与 pickSeries 同口径）；用稳定 ref/setter 内联，不依赖后置的 stopRefresh。
  const selectArticle = useCallback((slug: string) => {
    if (refreshCtrl.current) { refreshCtrl.current.abort(); refreshCtrl.current = null; setRefresh(EMPTY_REFRESH); }
    setActiveArticleSlug(slug);
    setArticleRelease(0);
    setArticleCountView(true);   // 用户主动点击（含同栏目推荐）= 一次真实阅读，计
    // 不清 activeKey：否则默认选中 effect（activeKey 为空时重选 series[0]）会把某报告重新选中并高亮；
    // 报告卡片高亮改由 !activeArticleSlug 门控（见列表渲染），内容阅读期间不高亮任何报告。
    setError('');
  }, []);

  // 进入报告中心默认展示「合并清单的第一条」（按时间倒序的真实首项，可能是资讯也可能是报告），
  // 与列表顶部一致（历史缺陷：只从报告里取 series[0]，资讯排在前时默认展示的却不是首项）。
  // 仅在“什么都没选过”时兜底选首项；用户手动选过报告/资讯后不再覆盖。
  useEffect(() => {
    if (!canAutoSelect || !entries.length) return;
    const nothingChosen = !activeArticleSlug
      && (!activeKey || !series.some(s => s.key === activeKey));
    if (!nothingChosen) return;
    const first = entries[0];
    if (first.kind === 'content') {
      setActiveArticleSlug(first.article.slug);
      setArticleRelease(0);
      setArticleCountView(false);   // 落地自动展示首项不算主动阅读，不计（避免顶高门户统计）
    } else {
      setActiveKey(first.series.key);
      setActiveId(first.series.reports[0]?.id ?? 0);
    }
  }, [canAutoSelect, entries, series, activeKey, activeArticleSlug]);

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
          .catch(e => setBodyError(e?.message || i18n.t('report:view.loadBodyFailed')))
          .finally(() => setBodyLoading(false));
      })
      .catch(e => {
        setDetail(null);
        setError(e?.response?.data?.detail || i18n.t('report:center.loadDetailFailed'));
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
    setActiveArticleSlug('');   // 回到报告详情态，收起内嵌阅读
    setActiveKey(s.key);
    setActiveId(s.reports[0]?.id ?? 0);
    setError('');
  };

  const openSaveTpl = () => setSaveTpl({
    open: true,
    name: t('center.tplName', { name: detail?.title || activeSeries?.name || t('export.fileFallback') }),
    description: '', saving: false,
  });

  const submitSaveTpl = async () => {
    if (!detail) return;
    setSaveTpl(prev => ({ ...prev, saving: true }));
    try {
      const out = await saveReportAsTemplate(detail.id, {
        name: saveTpl.name.trim(), description: saveTpl.description.trim(),
      });
      toast.success(t('center.tplSaved', { name: out.name }));
      setSaveTpl({ open: false, name: '', description: '', saving: false });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('center.tplSaveFailed'));
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
      if (!text.trim()) { toast.error(t('center.bodyEmptyEdit')); return; }
      openEdit(d, text);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || t('center.readBodyRetry'));
    }
  };

  const saveEditor = async () => {
    if (!editor.title.trim()) { toast.error(t('center.titleRequired')); return; }
    if (!editor.content.trim()) { toast.error(t('center.contentRequired')); return; }
    setEditor(prev => ({ ...prev, saving: true }));
    try {
      await updateReport(editor.reportId, {
        title: editor.title, content: editor.content, format: editor.format,
      });
      toast.success(t('center.updated'));
      await reload();
      loadDetail(editor.reportId);   // 正文已换新对象, 重取直链而不是复用旧正文
      setEditor(EMPTY_EDITOR);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('center.saveFailedRetry'));
      setEditor(prev => ({ ...prev, saving: false }));
    }
  };

  /** 删整份报告（卡片上的垃圾桶）：按归集键带走全部历史版本，确认框先说清删几版。 */
  const removeSeries = async (s: Series) => {
    const versions = s.reports.length;
    const suffix = versions > 1 ? t('center.deleteSeriesSuffix', { count: versions }) : '';
    if (!window.confirm(t('center.deleteSeriesConfirm', { name: s.name, suffix }))) return;
    try {
      // 归集键同一组的 id 都能代删（服务端拿第一行的键反查整组），传最新一版即可
      await deleteReport(s.reports[0].id, 'series');
      toast.success(t('center.deleted'));
      // 删的是当前选中卡片时清空选中（整份报告已不在，不能只按 latest id 判）
      if (activeKey && activeKey === s.key) { setActiveKey(''); setActiveId(0); setDetail(null); }
      await reload();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('center.deleteFailedRetry'));
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
    const lines = [t('center.deleteVersionConfirm1', { name: s.name, label }),
                   t('center.deleteVersionConfirm2', { count: s.reports.length - 1 })];
    // 最早一版 = 原始报告（卡片身份那一版）：删它后卡片的来源与编辑入口会改按剩余最早版算，
    // 后果必须在删之前说出来，不能让人事后发现“卡片变成不可编辑了”
    if (idx === s.reports.length - 1) {
      lines.push(t('center.deleteOriginalWarn'));
    }
    if (!window.confirm(lines.join('\n'))) return;
    const next = s.reports.find(x => x.id !== r.id);   // 清单按时间倒序 → 剩余里最新的一版
    try {
      const out = await deleteReport(r.id, 'version');
      toast.success(t('center.versionDeleted', { label, count: out?.remaining ?? s.reports.length - 1 }));
      await reload();
      if (next) setActiveId(next.id);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('center.deleteFailedRetry'));
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
      toast.info(i18n.t('report:center.stopToast'));
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
          ? i18n.t('report:center.capacityBusyWait', { position: ev.position || 1, seconds: Math.max(1, Math.round(ev.estimated_wait_s)) })
          : i18n.t('report:center.capacityBusy', { position: ev.position || 1 });
        put('capacity', note);
      } else if (ev.type === 'data') {
        // 取数结果只报“第几条 / 多少行 / 有无截断 / 哪条受治理通道”，不报 SQL 与表列
        note = i18n.t('report:center.queriesDone', { count: ev.query_count || 1 });
        put(`query:${ev.query_count || steps.filter(s => s.key.startsWith('query:')).length + 1}`,
          i18n.t('report:center.queryStep', { index: ev.query_count || 1, source: ev.source || i18n.t('report:center.governedQuery'), rows: ev.row_count || 0 })
          + (ev.truncated ? i18n.t('report:center.truncated') : ''), 'ok');
      } else if (ev.type === 'tool_start') {
        note = i18n.t('report:center.toolRunningNote', { tool: getToolLabel(ev.tool || '') });
        put(`tool:${ev.tool_call_id || ev.call_index || steps.length}`, i18n.t('report:center.toolRunningStep', { tool: getToolLabel(ev.tool || '') }));
      } else if (ev.type === 'tool_result') {
        const secs = typeof ev.elapsed === 'number' ? ` · ${ev.elapsed.toFixed(1)}s` : '';
        put(`tool:${ev.tool_call_id || ev.call_index || steps.length}`,
          i18n.t(ev.ok ? 'report:center.toolOk' : 'report:center.toolErr', { tool: getToolLabel(ev.tool || ''), secs }),
          ev.ok ? 'ok' : 'err');
      } else if (ev.type === 'text') {
        note = i18n.t('report:center.writingText', { chars: ev.chars || 0 });
        put('answer', note);
      } else if (ev.type === 'done') {
        note = ev.generation_status === 'ready' ? i18n.t('report:center.newVersionReady') : (ev.reason || i18n.t('report:center.roundIncomplete'));
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
    if (info && !info.available) { toast.error(info.hint || t('center.refreshUnavailable')); return; }
    if (info?.channel === 'task') {
      if (!detail.task_id) { toast.error(t('center.taskMissing')); return; }
      try {
        await triggerScheduledTask(detail.task_id);
        toast.success(t('center.taskTriggered'));
      } catch (e: any) {
        toast.error(e?.response?.data?.detail || t('center.taskTriggerFailed'));
      }
      return;
    }
    const controller = new AbortController();
    refreshCtrl.current = controller;
    refreshNewId.current = 0;
    refreshOutcome.current = null;
    setRefresh({ active: true, note: t('center.preparing'), steps: [] });
    try {
      await refreshReportStream(detail.id, { onEvent: onRefreshEvent, signal: controller.signal });
    } catch (e: any) {
      if (controller.signal.aborted) return;   // 用户自己点的停止，不再补一句错误
      toast.error(e?.message || t('center.refreshStartFailedFallback'));
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
      toast.error(t('center.streamBroken'));
      await reload();
      if (refreshNewId.current) setActiveId(refreshNewId.current);
      return;
    }
    await reload();
    if (refreshNewId.current) setActiveId(refreshNewId.current);
    const status = outcome.generation_status || '';
    if (status === 'ready') {
      const badge = layoutBadge(outcome.layout_result);
      toast.success(badge ? t('center.newVersionWithLayout', { badge }) : t('center.newVersion'));
    } else if (status === 'cancelled') {
      toast.info(t('center.cancelledToast'));
    } else {
      toast.error(outcome.reason
        || t('center.noUsableVersion', { status: statusLabel(status || '') || t('center.statusUnknown') }));
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
        key: 'task', label: t('center.more.task'), icon: <CalendarClock className="h-3.5 w-3.5" />,
        title: t('center.more.taskTitle'),
        onSelect: () => setTaskDialogOpen(true),
      },
      {
        // 好报告的版式不该只留在这一次：沉淀成模板后，定时任务表单与报告重跑共用同一份版式
        key: 'template', label: t('center.more.template'), icon: <Layers className="h-3.5 w-3.5" />,
        disabled: !hasBody,
        title: hasBody
          ? t('center.more.templateTitle')
          : t('center.more.templateDisabled'),
        onSelect: openSaveTpl,
      },
    ];
    if (isEditableReport(detail)) {
      items.push({
        key: 'edit', label: t('common:action.edit'), icon: <Pencil className="h-3.5 w-3.5" />,
        disabled: !hasBody,
        title: hasBody ? t('center.more.editTitle') : t('center.more.editDisabled'),
        onSelect: () => openEdit(detail, body),
      });
    }
    // 好报告不只留在个人报告中心：提交给内容团队走「审核 → 发布」对外发布。
    // 入口的可用性判据与端点同一个码（article:manage）：没权限也照样列出来并写明置灰原因，
    // 不让人以为“报告中心没这个功能”（menu-permission-sync / no-silent-degradation）。
    const canPromote = hasPerm('article:manage');
    items.push({
      key: 'promote', label: t('center.more.promote'), icon: <Send className="h-3.5 w-3.5" />,
      disabled: !hasBody || !canPromote,
      title: !canPromote
        ? t('center.more.promoteNoPerm')
        : !hasBody
          ? t('center.more.promoteNoBody')
          : t('center.more.promoteTitle'),
      onSelect: () => setPromoteOpen(true),
    });
    if (isDeletableReport(detail)) {
      const versions = activeSeries?.reports.length ?? 1;
      if (versions > 1) {
        // 只删当前这一版：重跑追加的失败版/旧版可以单独清掉，不必连原始报告一起没
        items.push({
          key: 'delete-version', label: t('center.more.deleteVersion'), icon: <Trash2 className="h-3.5 w-3.5" />,
          danger: true, separatorBefore: true,
          disabled: refresh.active,
          title: refresh.active
            ? t('center.more.deleteVersionBusy')
            : t('center.more.deleteVersionTitle', { count: versions - 1 }),
          onSelect: () => void removeVersion(detail),
        });
      }
      items.push({
        key: 'delete',
        label: versions > 1 ? t('center.more.deleteAll', { count: versions }) : t('common:action.delete'),
        icon: <Trash2 className="h-3.5 w-3.5" />,
        danger: true, separatorBefore: versions <= 1,
        title: versions > 1 ? t('center.more.deleteAllTitle') : t('center.more.deleteTitle'),
        onSelect: () => { if (activeSeries) void removeSeries(activeSeries); },
      });
    }
    return items;
  };

  return (
    <div className="h-full flex flex-col gap-4 p-6 overflow-hidden">
      <div className="flex items-center gap-2 shrink-0 flex-wrap">
        <FileBarChart className="h-5 w-5 text-primary" />
        <h1 className="text-lg font-semibold">{t('menu:workspace.reports')}</h1>
        <span className="text-sm text-muted-foreground">
          {t('center.subtitle')}
        </span>
      </div>

      {/* 筛选行: 来源 / 关键词。没有工作空间筛选器 —— 创建工作空间暂未开放,
          且报告跟随属主跨空间, 该筛选给不出任何收窄效果(留着一个动不了的开关
          比没有更糟: 角色页会把它当成可配的能力)。 */}
      <div className="flex items-center gap-2 shrink-0 flex-wrap">
        <Select value={sourceFilter} onValueChange={setSourceFilter}>
          <SelectTrigger className="w-[140px] h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('center.allSources')}</SelectItem>
            {canReadContent && <SelectItem value="content">{t('center.filterContent')}</SelectItem>}
            {SOURCE_ORDER.map(s => (
              <SelectItem key={s} value={s}>{sourceLabel(s)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input value={keyword} onChange={e => setKeyword(e.target.value)}
            placeholder={t('center.searchPlaceholder')} className="h-8 w-56 pl-7 text-xs" />
        </div>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> {t('common:state.loading')}
        </div>
      ) : !entries.length ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground border border-dashed rounded-lg bg-card">
          <Inbox className="h-10 w-10 opacity-50" />
          <p className="text-sm">{error || articlesError || t('center.empty')}</p>
          {articlesError && !error && (
            <Button variant="outline" size="sm" className="h-8 text-xs" onClick={reloadArticles}>
              <RefreshCw className="h-3 w-3 mr-1" /> {t('center.contentRetry')}
            </Button>
          )}
        </div>
      ) : (
        <div className="flex-1 flex gap-4 min-h-0">
          {/* 左栏: 报告清单(任务型按版本归集) + 内容中心已发布内容，按日期倒序混排 */}
          <div className="w-72 shrink-0 overflow-y-auto border rounded-lg bg-card">
            {canReadContent && articlesError && (
              <div className="px-3 py-2.5 border-b" role="alert">
                <p className="text-xs text-destructive">{articlesError}</p>
                <Button variant="outline" size="sm" className="mt-1.5 h-7 text-xs" onClick={reloadArticles}>
                  <RefreshCw className="h-3 w-3 mr-1" /> {t('center.contentRetry')}
                </Button>
              </div>
            )}
            {entries.map(entry => entry.kind === 'content' ? (
              /* 内容条目：只读消费，就地内嵌到右栏阅读（无版本/重跑/编辑/删除）。
                 名称一律用业务可读标题 + 栏目名，不露裸 id/slug（slug 只进选择态，ui-resource-display §1）。 */
              <button
                key={`content:${entry.article.slug}`}
                type="button"
                onClick={() => selectArticle(entry.article.slug)}
                className={`w-full px-3 py-2.5 border-b last:border-b-0 text-left transition-colors ${
                  activeArticleSlug === entry.article.slug
                    ? 'bg-primary/10 border-l-2 border-l-primary' : 'hover:bg-muted/50'
                }`}
              >
                <span className={`block text-sm font-medium truncate ${activeArticleSlug === entry.article.slug ? 'text-primary' : ''}`}>{entry.article.title}</span>
                <span className="flex items-center gap-2 mt-1">
                  <Badge variant="outline" className={`shrink-0 text-[10px] ${SOURCE_META.content.cls}`}>
                    <Newspaper className="h-3 w-3 mr-1" />{t('center.contentBadge')}
                  </Badge>
                  {!!entry.article.category_label && (
                    <span className="flex items-center gap-1 text-xs text-muted-foreground truncate">
                      {!!entry.article.category_color && (
                        <span aria-hidden className="size-1.5 rounded-full"
                          style={{ backgroundColor: entry.article.category_color }} />)}
                      {entry.article.category_label}
                    </span>
                  )}
                  <span className="ml-auto text-xs text-muted-foreground shrink-0">
                    {entry.date ? new Date(entry.date).toLocaleDateString(i18n.language) : '-'}
                  </span>
                </span>
              </button>
            ) : (() => {
              const s = entry.series;
              const latest = s.reports[0];
              const active = !activeArticleSlug && s.key === activeKey;
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
                        {t('center.versionCount', { count: s.reports.length })}
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <Badge variant="outline" className={`text-[10px] ${SOURCE_META[s.source]?.cls || ''}`}>
                      {sourceLabel(s.source)}
                    </Badge>
                    <span className="text-xs text-muted-foreground truncate">
                      {t('center.latest', { date: latest ? new Date(latest.created_at).toLocaleDateString(i18n.language) : '-' })}
                    </span>
                    {s.editable && (
                      <Button variant="ghost" size="sm" className="h-6 w-6 p-0 ml-auto" title={t('center.more.editTitle')}
                        onClick={() => void editSeries(s)}>
                        <Pencil className="h-3 w-3" />
                      </Button>
                    )}
                    {s.deletable && (
                      <Button variant="ghost" size="sm"
                        className={`h-6 w-6 p-0 text-destructive ${s.editable ? '' : 'ml-auto'}`}
                        title={s.reports.length > 1
                          ? t('center.deleteSeriesTitle', { count: s.reports.length })
                          : t('center.deleteReportTitle')}
                        onClick={() => void removeSeries(s)}>
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                </div>
              );
            })())}
          </div>

          {/* 右栏: 选中内容 → 内嵌阅读（就地看正文 + 全屏跳独立页）；否则 → 报告详情 */}
          <div className="flex-1 min-w-0 flex flex-col border rounded-lg bg-card overflow-hidden">
            {activeArticleSlug ? (
              <>
                <div className="shrink-0 px-5 py-3 border-b flex items-center justify-between gap-3">
                  <div className="min-w-0 flex items-center gap-2">
                    <Badge variant="outline" className={`shrink-0 text-[10px] ${SOURCE_META.content.cls}`}>
                      <Newspaper className="h-3 w-3 mr-1" />{t('center.contentBadge')}
                    </Badge>
                    <h2 className="text-sm font-semibold truncate">
                      {articles.find(a => a.slug === activeArticleSlug)?.title || t('center.contentBadge')}
                    </h2>
                  </div>
                  {/* 全屏 = 打开独立阅读页 /read/:slug（单独网页）；内嵌只读，编辑/发布回内容中心 */}
                  <Button variant="outline" size="sm" className="h-8 text-xs shrink-0"
                    title={t('center.contentFullscreenTitle')}
                    onClick={() => navigate(`/read/${encodeURIComponent(activeArticleSlug)}`)}>
                    <Maximize2 className="h-3 w-3 mr-1" />{t('center.contentFullscreen')}
                  </Button>
                </div>
                <ArticleReader
                  slug={activeArticleSlug}
                  release={articleRelease}
                  countView={articleCountView}
                  onPickRelease={v => setArticleRelease(Number(v) || 0)}
                  onOpenArticle={selectArticle}
                />
              </>
            ) : (
              <>
            <div className="shrink-0 px-5 py-3 border-b flex items-center justify-between gap-3 flex-wrap gap-y-2">
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-semibold truncate">{detail?.title || activeSeries?.name || t('center.selectReport')}</h2>
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
                          ? t('center.fromConversation', { id: detail.origin.conversation_id }) : undefined}
                        className={`text-[10px] ${SOURCE_META[detail.source_type || 'legacy']?.cls || ''}`}>
                        {sourceLabel(detail.source_type || 'legacy')}
                      </Badge>
                      {detail.generation_status && GEN_STATUS[detail.generation_status] && (
                        <Badge variant="outline" className={`text-[10px] ${GEN_STATUS[detail.generation_status].cls}`}>
                          {statusLabel(detail.generation_status)}
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
                    title={detail.refresh?.hint || t('center.refreshDefaultTitle')}>
                    {refresh.active
                      ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      : <RefreshCw className="h-3 w-3 mr-1" />}
                    {refresh.active ? t('center.refreshing') : (detail.refresh?.action_label || t('center.refreshDefaultLabel'))}
                  </Button>
                )}
                {detail && (
                  <ReportExportMenu
                    title={detail.title || activeSeries?.name || t('export.fileFallback')}
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
                    title={t('center.versionManage')}
                  >
                    {activeSeries.reports.map((r, i) => (
                      // 版本名带上生成状态：失败/超时的版本只看时间看不出来，
                      // 切过去只会得到一句报错，不知道哪一版才是有正文的那一版
                      <option key={r.id} value={r.id}>
                        v{activeSeries.reports.length - i} · {formatDate(r.created_at)}
                        {r.generation_status && r.generation_status !== 'ready'
                          ? ` · ${statusLabel(r.generation_status)}` : ''}
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
                      {t('center.refreshPanelTitle')}
                    </p>
                    <Button variant="ghost" size="sm" className="h-7 text-xs"
                      title={t('center.stopTitle')}
                      onClick={() => stopRefresh(false)}>
                      <X className="h-3 w-3 mr-1" /> {t('center.stop')}
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
                  <Loader2 className="h-5 w-5 animate-spin mr-2" /> {t('center.loadingDetail')}
                </div>
              ) : NO_BODY.includes(detail.generation_status || '') ? (
                <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                  {PENDING.includes(detail.generation_status || '')
                    ? <><Loader2 className="h-4 w-4 animate-spin mr-2" /> {t('center.generatingNote')}</>
                    : (detail.generation_status === 'cancelled'
                        ? t('center.cancelledNote')
                        : t('center.noBodyNote'))}
                </div>
              ) : bodyError ? (
                /* 正文取不到必须显式暴露原因（直链过期 / 桶没开跨域 / 对象丢失），
                   并给一个换直链重试的入口 —— 静默渲染空白会被当成"报告没内容"。 */
                <div role="alert" className="h-full flex flex-col items-center justify-center gap-3 text-sm">
                  <p className="text-destructive max-w-prose text-center">{bodyError}</p>
                  <Button variant="outline" size="sm" className="h-8 text-xs"
                    onClick={() => loadDetail(detail.id)}>
                    <RefreshCw className="h-3 w-3 mr-1" /> {t('center.refetchBody')}
                  </Button>
                </div>
              ) : bodyLoading || !body.trim() ? (
                <div className="h-full flex items-center justify-center text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" /> {t('view.loadingBody')}
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
              </>
            )}
          </div>
        </div>
      )}

      {/* 新建 / 编辑报告 */}
      <Dialog open={editor.open} onOpenChange={o => { if (!o) setEditor(EMPTY_EDITOR); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t('center.editor.title')}</DialogTitle>
            <DialogDescription>
              {t('center.editor.desc')}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="report-title">{t('center.editor.name')}</Label>
              <Input id="report-title" value={editor.title}
                onChange={e => setEditor(prev => ({ ...prev, title: e.target.value }))}
                placeholder={t('center.editor.namePlaceholder')} />
            </div>
            <div className="flex items-center gap-3">
              <Label className="shrink-0">{t('center.editor.format')}</Label>
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
              <Label htmlFor="report-content">{t('center.editor.body')}</Label>
              <Textarea id="report-content" value={editor.content}
                onChange={e => setEditor(prev => ({ ...prev, content: e.target.value }))}
                placeholder={editor.format === 'html'
                  ? t('center.editor.htmlPlaceholder')
                  : t('center.editor.mdPlaceholder')}
                className="font-mono text-xs min-h-[320px]" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditor(EMPTY_EDITOR)} disabled={editor.saving}>{t('common:action.cancel')}</Button>
            <Button onClick={saveEditor} disabled={editor.saving}>
              {editor.saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />{t('common:action.saving')}</> : t('common:action.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 把本报告的版式沉淀成报告模板（只抽结构，不带正文里的数字） */}
      <Dialog open={saveTpl.open} onOpenChange={o => { if (!o) setSaveTpl({ open: false, name: '', description: '', saving: false }); }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{t('center.tpl.title')}</DialogTitle>
            <DialogDescription>
              {t('center.tpl.desc')}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>{t('center.tpl.name')}</Label>
              <Input value={saveTpl.name} onChange={e => setSaveTpl(prev => ({ ...prev, name: e.target.value }))}
                placeholder={t('center.tpl.namePlaceholder')} />
            </div>
            <div className="space-y-1.5">
              <Label>{t('center.tpl.scenario')}</Label>
              <Input value={saveTpl.description}
                onChange={e => setSaveTpl(prev => ({ ...prev, description: e.target.value }))}
                placeholder={t('center.tpl.scenarioPlaceholder')} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline"
              onClick={() => setSaveTpl({ open: false, name: '', description: '', saving: false })}
              disabled={saveTpl.saving}>{t('common:action.cancel')}</Button>
            <Button onClick={submitSaveTpl} disabled={saveTpl.saving || !saveTpl.name.trim()}>
              {saveTpl.saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />{t('common:action.saving')}</> : t('center.tpl.submit')}
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
