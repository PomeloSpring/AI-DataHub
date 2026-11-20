import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Send, Zap, Download, Bot, User, Lightbulb,
  CheckCircle, BarChart3, Table, Code, Clock,
  Plus, MessageSquare, Trash, TrendingUp, X, RefreshCw,
  MoreHorizontal, Pencil, Check, Cpu,
  Loader2, Maximize2, Minimize2, Paperclip, FileText, Box, FolderOpen, Save, ClipboardList,
  Globe, AlertTriangle, Sparkles, Terminal, ListChecks, Archive, Square, Workflow,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { TOUR } from '@/lib/onboarding/guides';
import { useChatStore } from '../stores/chatStore';
import type { AttachmentInfo, PendingAttachment, CloudFileRef, ChatReportDraft } from '../stores/chatStore';
import { ATTACH_MAX_FILES, validateLocalAttachment, filesFromClipboard, hasModelStats } from '../stores/chatStore';
import ChartPicker from '../components/ChartPicker';
import { chartTypeLabel } from '../components/DashboardChart';
import MarkdownWithCharts from '../components/MarkdownWithCharts';
import ExecutionProcess from '../components/ExecutionProcess';
import { TurnScreenPreview } from '../components/ScreenPreviewEntry';
import InlineDetails from '../components/InlineDetails';
import Model3DViewer from '../components/chat/Model3DViewer';
import ConversationSidePanel from '../components/chat/ConversationSidePanel';
import ReportDraftEditor from '../components/chat/ReportDraftEditor';
import { ContextUsageChip } from '../components/chat/ContextUsageChip';
import { CapacityNotice } from '../components/chat/CapacityNotice';
import StageCard from '../components/chat/StageCard';
import { useMotion } from '../hooks/useMotion';
import { enterClass } from '../lib/motion';
import BotAvatar from '../components/chat/BotAvatar';
import WaitingIndicator from '../components/chat/WaitingIndicator';
import SlashCommandMenu, {
  slashQueryOf, stripSlashToken, useSlashMenu, recordSlashUsage,
  slashTriggerHint, slashTriggerNote,
  type SlashItem, type SlashOptionsPayload,
} from '../components/chat/SlashCommandMenu';
import MessageActions from '../components/chat/MessageActions';
import TurnNavigator, { jumpToTurnAnchor } from '../components/chat/TurnNavigator';
import type { TurnNavItem } from '../components/chat/TurnNavigator';
import DocCard from '../components/chat/DocCard';
import DocEditorPanel from '../components/chat/DocEditorPanel';
import CloudFileMenu, {
  mentionQueryOf, stripMentionToken, useCloudFileMenu, formatSize,
  mentionTriggerHint,
  type CloudFileItem,
} from '../components/chat/CloudFileMenu';
import { IntentGateBadge } from '../components/chat/IntentGateBadge';
import DataGrid from '../components/DataGrid';
import DataProfileCard from '../components/DataProfileCard';
import SqlCard from '../components/SqlCard';
import { toCsv } from '../lib/dataGrid';
import client from '../api/client';
// 首页/阅读页带问题跳入问数页的一次性契约（与首页共用同一份实现，不各写一套 sessionStorage 键）
import { consumeStashedQuestion, type StashedQuestion } from './home/shared';
import { saveConversationAsReport, createReportDraft } from '../api/report';
import { t as tr } from '@/i18n/runtime';

// 附件类别文案只存 key，渲染期经 t() 取（模块顶层常量会冻结语言）
const CATEGORY_LABEL_KEYS: Record<string, 'page.category.image' | 'page.category.table' | 'page.category.document' | 'page.category.model3d'> = {
  image: 'page.category.image', table: 'page.category.table', document: 'page.category.document', model3d: 'page.category.model3d',
};

/** 右侧栏展开偏好的 localStorage 键（个人视图偏好，不进账号配置） */
const SIDE_PANEL_KEY = 'chat.sidePanel.open';

/**
 * 首页带入的待自动发送问题最多等多久。上下文（工作空间/站点/AS-BOT）正常情况下几百毫秒就定形，
 * 超阈值说明请求挂住了 —— 与其把问题无限期悬着，不如退回预填并说出来。
 */
const PENDING_ASK_FALLBACK_MS = 6000;

/**
 * 一条回答的正文（复制 / 转文档编辑的唯一取值口径）。
 *
 * reply 是服务端定稿正文；content 是流式原文（出错回合是中断前的部分正文）。
 * 两者同文时取 reply，不拼接（拼一次就把同一件事复制两遍）。
 */
function answerMarkdownOf(msg: any): string {
  const reply = String(msg?.reply || '').trim();
  return reply ? String(msg.reply) : String(msg?.content || '');
}

/**
 * 会话侧栏技能徽标的数据：服务端把每块会话的回合 meta 聚合成
 * `skill_usage = {技能名: {resident: 随提示词常驻的回合数, attached: 随工具结果附带的回合数,
 *  fetched: 模型主动取回正文的回合数}}`。
 * 按"加载过的回合总数"降序，让最常真正被读到的细则排在前面。
 * 显示名走 slashOptions 的 display_name（与消息体里的技能徽章同一口径），
 * 侧栏不出现 screen-builder 这类技术名。
 */
function convSkillChips(conv: { skill_usage?: Record<string, { resident?: number; attached?: number; fetched?: number }> }) {
  return Object.entries(conv.skill_usage || {})
    .map(([name, usage]) => ({
      name,
      resident: usage?.resident || 0,
      attached: usage?.attached || 0,
      fetched: usage?.fetched || 0,
    }))
    .sort((a, b) => (b.resident + b.attached + b.fetched) - (a.resident + a.attached + a.fetched)
      || a.name.localeCompare(b.name));
}

// 错误卡按服务端分类给下一步（分类码与 capacity.classify_failure 同源）：
// 繁忙/限流是“平台过载”（稍后再试/换模型），超时是“本轮太大”，failed 才是查配置。
// 文案只存 key，渲染期经 t() 取。
type ErrorKindKey = 'page.errorHint.provider_busy' | 'page.errorHint.rate_limited' | 'page.errorHint.capacity'
  | 'page.errorHint.timeout' | 'page.errorHint.aborted' | 'page.errorHint.failed';
const ERROR_KIND_HINT_KEYS: Record<string, ErrorKindKey> = {
  provider_busy: 'page.errorHint.provider_busy',
  rate_limited: 'page.errorHint.rate_limited',
  capacity: 'page.errorHint.capacity',
  timeout: 'page.errorHint.timeout',
  aborted: 'page.errorHint.aborted',
  failed: 'page.errorHint.failed',
};

// 单轮最多指定几个技能：与后端 prompt_composer.MAX_SKILL_BODIES 同口径，
// 超出部分正文不会展开，选了等于没选，所以在选择环节就提醒而不是静默丢弃。
const MAX_PINNED_SKILLS = 2;

// 单轮最多指定几个分析场景：与技能同口径（多份查询方案同时注入会相互干扰，
// 模型会挑一份先执行，结果取决于它先读到哪份），所以超上限在选择环节就提醒。
const MAX_PINNED_SCENES = 2;

// 一次批量删除的上限：与服务端 _MAX_BATCH_DELETE 同口径。前端先卡住而不是递上去
// 被整批 400 拒回（那会连带已勾选项也删不了）。
const MAX_BATCH_DELETE = 50;

// 附件 URL:待发/刚发送的本轮附件用本地 blob 预览;
// 历史消息附件按工作区相对路径走 session-file 伺服(带 token,供 <img>/three.js 加载)
function attUrl(att: AttachmentInfo, conversationId?: number | null): string {
  if (att.blobUrl) return att.blobUrl;
  if (!att.path || !conversationId) return '';
  const token = localStorage.getItem('token');
  const base = `/api/chat/session-file?conversation_id=${conversationId}&path=${encodeURIComponent(att.path)}`;
  return token ? `${base}&token=${encodeURIComponent(token)}` : base;
}

// 图表类型标签与看板共用同一套 CHART_TYPES（经 chartTypeLabel 渲染期解析）；
// 前置为历史消息的旧类型别名（仅用于旧消息回放），文案只存 key。
const LEGACY_CHART_LABEL_KEYS: Record<string, 'page.chartLegacy.timeseries_table' | 'page.chartLegacy.timeseries_percent'
  | 'page.chartLegacy.timeseries_pivot' | 'page.chartLegacy.chord' | 'page.chartLegacy.map_heat'> = {
  timeseries_table: 'page.chartLegacy.timeseries_table',
  timeseries_percent: 'page.chartLegacy.timeseries_percent',
  timeseries_pivot: 'page.chartLegacy.timeseries_pivot',
  chord: 'page.chartLegacy.chord',
  // 地图热力(chat 图表卡专属,字模库地图瓦片 + 热力层)及其 LLM 常用别名
  map_heat: 'page.chartLegacy.map_heat', map: 'page.chartLegacy.map_heat', heatmap_map: 'page.chartLegacy.map_heat',
  geo_heatmap: 'page.chartLegacy.map_heat', map_heatmap: 'page.chartLegacy.map_heat',
};

export default function Chat() {
  const { t } = useTranslation(['chat', 'common', 'menu']);
  // 旧类型别名 → 历史文案 key；column/table 归一到看板同名类型，其余走 chartTypeLabel
  const chartLabelOf = (type: string): string => {
    const legacyKey = LEGACY_CHART_LABEL_KEYS[type];
    if (legacyKey) return t(legacyKey);
    if (type === 'column') return chartTypeLabel('bar');
    if (type === 'table') return t('page.category.table');
    return chartTypeLabel(type);
  };
  const { workspaceId: urlWorkspaceId } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();
  const independent = useLocation().pathname.startsWith('/ask/');
  const [conversationsOpen, setConversationsOpen] = useState(false);
  const conversationError = useChatStore(s => s.conversationError);
  const [input, setInput] = useState('');
  // 输入框 `/` 快捷检索：候选清单（场景 / 技能 / 定时任务 / 指令）+ 当前检索词。
  // 候选只在挂载与工作空间变化时拉一次，后续过滤在本地做（不每敲一个字就多一次往返）。
  // 类别栏与栏内高亮位由 useSlashMenu 管（←→ 切类别、↑↓ 选条目），页面不再自己数下标。
  const [slashOptions, setSlashOptions] = useState<SlashOptionsPayload | null>(null);
  const [slashError, setSlashError] = useState('');
  const [slashQuery, setSlashQuery] = useState<string | null>(null);
  // 输入框 `@` 文件引用：候选 = 归档资产（账号级跨工作空间）+ 本会话工作区文件。
  // 口径：上云是备份、归档才是资产 —— 备份副本不摊进 @ 清单，免得选到没打算留的中间产物。
  const [cloudRefs, setCloudRefs] = useState<CloudFileRef[]>([]);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionPayload, setMentionPayload] = useState<{ assets: CloudFileItem[]; session_files: CloudFileItem[] } | null>(null);
  const [mentionLoading, setMentionLoading] = useState(false);
  const [mentionError, setMentionError] = useState('');
  // 已选中的技能名与指令 key（发送时随 selected_skills / slash_command 上送）
  const [pickedSkills, setPickedSkills] = useState<string[]>([]);
  // `/` 选中的分析场景 id（与候选同源下发）：服务端按主体权限裁决后强制注入查询方案
  const [pickedScenes, setPickedScenes] = useState<string[]>([]);
  const [pickedCommand, setPickedCommand] = useState('');
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [focusMode, setFocusMode] = useState(false);
  const [pendingAtts, setPendingAtts] = useState<PendingAttachment[]>([]);
  // 历史附件文件已随会话清理(session-file 404/无路径)的标记,键含会话与位置
  const [brokenAtts, setBrokenAtts] = useState<Record<string, boolean>>({});
  const [preview3d, setPreview3d] = useState<AttachmentInfo | null>(null);
  // 会话右侧栏（产物 + 关键知识）：用户拍板默认收起，偏好跨会话记住。
  const [sidePanelOpen, setSidePanelOpen] = useState(() => localStorage.getItem(SIDE_PANEL_KEY) === '1');
  const toggleSidePanel = () => setSidePanelOpen(prev => {
    localStorage.setItem(SIDE_PANEL_KEY, prev ? '0' : '1');
    return !prev;
  });
  // 会话产物落盘后递增（侧栏文件清单只靠 conversationId 变化会漏掉同会话新产物）
  const [filesRefreshKey, setFilesRefreshKey] = useState(0);
  // 会话历史批量管理：批量模式开关 + 已勾选项 + 确认弹框与进行中标记。
  // 勾选是视图态，不进 store（避免跨工作空间/刷新残留一堆待删项）。
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [batchConfirm, setBatchConfirm] = useState(false);
  const [batchDeleting, setBatchDeleting] = useState(false);
  // 删除单轮（提问+其回答）与「/清空当前会话」的确认态：destructive 动作走 Dialog，不用原生 confirm
  const [deleteTurnIdx, setDeleteTurnIdx] = useState<number | null>(null);
  const [clearConfirm, setClearConfirm] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // 首页/阅读页带入的问题（sessionStorage 一次性契约）：挂载时只取进 state，不在那一步写输入框——
  // 同一次 commit 里下面的工作空间同步 effect 会 setInput('')（登录后 selectedWorkspaceId=0 → 必然不相等），
  // 先写后清等于把刚带进来的问题抹掉（历史缺陷：首页问数带不进问题）。
  const [pendingAsk, setPendingAsk] = useState<(StashedQuestion & { sent?: boolean }) | null>(null);
  const {
    conversations, currentConvId, messages, loading,
    selectedDsId,
    selectedWorkspaceId, setSelectedWorkspaceId, loadWorkspaceConfig,
    executionLayer, selectedModelRef, loadExecutionLayer, setSelectedModelRef,
    asBots, loadAsBots, asBotsLoaded,
    loadConversations, loadDatasources, loadLLMModels, loadSystemConfig,
    loadAuthorizedSites, authorizedSites, selectedSiteCode, setSelectedSiteCode, siteError, sitesLoaded,
    setSelectedDsId, setSelectedModelId,
    startNewConversation, switchConversation, deleteConversation, renameConversation,
    deleteConversations, deleteTurn, clear,
    // 分页读窗口：长会话首屏只拿最近一页，“加载更早消息”往前翻
    hasMoreEarlier, loadingEarlier, messageTotal, loadEarlierMessages,
    sendMessage, cancelMessage, respondToAsk, cancelAsk, updateMessageFeedback, setViewMode, analyzeData, predictData,
    saveMessageDoc,
    saveMessageReportDraft,
    stopping,
    // 上下文占用（进度条）与并发/繁忙提示（排队、重试）——数据只来自服务端
    contextUsage, contextCompacting, compacting, capacity, draftRestore,
    loadContextUsage, compactContext, consumeDraftRestore, retryLastTurn,
  } = useChatStore();
  const modelPickerRef = useRef<HTMLDivElement>(null);
  // “切换模型”不新造第二套模型入口：引导到页头已有的选择器（打不开就明说去哪找）
  const openModelPicker = () => {
    const trigger = modelPickerRef.current?.querySelector('button');
    if (trigger) { trigger.click(); return; }
    toast.info(t('page.switchModelHint'));
  };
  // 进度条属于“当前会话”视图：切会话/新开会话都重拉（新会话无值时组件会说“未测量”）
  useEffect(() => { void loadContextUsage(currentConvId); }, [currentConvId, loadContextUsage]);
  // 本轮失败后服务端递回的草稿：回填输入框与待发区（一次性消费，不重复回填）。
  // 用函数式更新：用户在这空档里已经敲了新问题时，不能把刚敲的盖掉（覆盖比不回填更让人丢数据）。
  useEffect(() => {
    const draft = consumeDraftRestore();
    if (!draft) return;
    setInput(prev => prev || draft.question);
    if (draft.attachments.length) setPendingAtts(prev => (prev.length ? prev : draft.attachments));
  }, [draftRestore, consumeDraftRestore]);
  // 「重试本轮」：服务端递回的原文+附件原样重发（store 会先摘掉待重试那一轮，不重复出现同一提问）。
  // 输入框要清：那句已被重试发出去，留着会让用户以为没发成功而再按一次发送。
  const retryTurn = () => {
    setInput('');
    setPendingAtts([]);
    void retryLastTurn();
  };
  // 切工作空间时退出批量态：勾选属于上一个清单，带过来会变成“看不见却待删”的隐患。
  useEffect(() => {
    setBatchMode(false); setSelectedIds([]); setBatchConfirm(false);
  }, [selectedWorkspaceId]);

  // `/` 候选清单（技能/定时任务/指令）：服务端按当前生效 AS-BOT 与属主任务解析，
  // 加载失败要可见（否则用户会以为“没有技能可用”而不是“没拉到”）。
  useEffect(() => {
    let cancelled = false;
    setSlashError('');
    client.get('/chat/slash-options', { params: { workspace_id: selectedWorkspaceId || 0 } })
      .then(({ data }) => {
        if (cancelled) return;
        setSlashOptions({
          skills: data?.skills || [], tasks: data?.tasks || [], commands: data?.commands || [],
          // 场景（快速入口）也走服务端同源清单：卡片按钮与 `/` 面板不得两套口径
          modes: data?.modes || [],
          // 场景候选与“因主体无权限被隐去的条数”都来自服务端同源下发：
          // 只下发可见集而不报隐去数，用户会把“没列出来”当成“没配场景”。
          scenes: data?.scenes || [],
          scenes_hidden: data?.scenes_hidden || 0,
        });
      })
      .catch((e: any) => {
        if (cancelled) return;
        setSlashOptions(null);
        setSlashError(e?.response?.data?.detail || t('slash.searchFailed', { hint: slashTriggerHint() }));
      });
    return () => { cancelled = true; };
  }, [selectedWorkspaceId]);

  const slashMenu = useSlashMenu(slashOptions, slashQuery);
  const slashOpen = slashQuery !== null;
  // Esc 的优先级要靠 ref 判：输入框的 keydown 先跑（React 挂在容器上），但同批
  // 里 state 还没提交，全局监听看到的是“面板还开着”——那时只该退出选择，
  // 不该顺手把用户从专注模式里踢出去。
  const slashOpenRef = useRef(slashOpen);
  slashOpenRef.current = slashOpen;

  const mentionMenu = useCloudFileMenu(mentionPayload, mentionQuery, cloudRefs);
  // 两个面板不同时开：`@` 片段命中时不再顶出 `/` 面板（同一光标位只有一个正在输入的片段）
  const mentionOpen = mentionQuery !== null && !slashOpen;

  // `@` 检索防抖 250ms：文件名过滤在服务端按 LIKE 做（本地不再实现一套匹配口径），
  // 带上 currentConvId 才能把本会话已有产物也列进候选。
  useEffect(() => {
    if (mentionQuery === null) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setMentionLoading(true);
      setMentionError('');
      client.get('/chat/mention-files', {
        params: { q: mentionQuery, conversation_id: currentConvId || 0, workspace_id: selectedWorkspaceId || 0 },
      })
        .then(({ data }) => {
          if (cancelled) return;
          setMentionPayload({ assets: data?.assets || [], session_files: data?.session_files || [] });
        })
        .catch((e: any) => {
          if (cancelled) return;
          setMentionPayload(null);
          // 检索失败必须可见（不能静默成"你没有可引用的文件"）
          setMentionError(e?.response?.data?.detail || t('cloudFiles.searchFailed', { hint: mentionTriggerHint() }));
        })
        .finally(() => { if (!cancelled) setMentionLoading(false); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [mentionQuery, currentConvId, selectedWorkspaceId]);

  // 首页「试试这样问」/「问数」带入的问题：挂载时消费一次，放进待处理态（不直接碰输入框）
  useEffect(() => {
    const st = consumeStashedQuestion();
    if (st?.question) setPendingAsk(st);
  }, []);

  // 待处理问题落地。两条路口径不同是刻意的：
  // - send（首页回车/「问数」）= 用户在首页已经按下发送，到这边必须直接执行；执行要等取数
  //   上下文定形（工作空间已切到 URL 那个 + 站点/AS-BOT 清单已拉到），否则问题会发进一个还没
  //   解析完的范围（站点未选中 → 拿默认源查错站），也会丢 model_ref 派生。
  // - 预填（阅读页「就本主题提问」递的是半句）只进输入框，等人补全后自己发。
  // sent 标记：已交给 sendMessage 但消息还没落进 store（createConversation 是一个往返），
  // 这一段把 pendingAsk 直接置 null 会让画面闪回欢迎页，因此只标 sent 不清空，
  // 由待发气泡继续占位；标了 sent 就不再重发（否则 loading 一落又发一次）。
  useEffect(() => {
    if (!pendingAsk) return;
    if (!pendingAsk.send) {
      const q = pendingAsk.question;
      setPendingAsk(null);
      setInput(q);
      inputRef.current?.focus();
      return;
    }
    if (pendingAsk.sent) {
      // 本轮已进入 store（用户气泡接管）→ 待发态可以退了
      if (messages.length > 0) setPendingAsk(null);
      return;
    }
    const wsNum = Number(urlWorkspaceId);
    // 上一轮还在流式输出时 sendMessage 会静默 return（问题不能就这么丢），等它空下来
    if (loading) return;
    if ((wsNum && selectedWorkspaceId !== wsNum) || !sitesLoaded || !asBotsLoaded) return;
    const q = pendingAsk.question;
    if (siteError) {
      setPendingAsk(null);
      // 站点清单没拉到：不带着未解析的取数范围自动发，退回预填 + 把原因说出来
      setInput(q);
      inputRef.current?.focus();
      toast.error(t('page.siteNotLoaded', { reason: siteError }));
      return;
    }
    setPendingAsk({ ...pendingAsk, sent: true });
    void sendMessage(q);
  }, [pendingAsk, urlWorkspaceId, selectedWorkspaceId, sitesLoaded, asBotsLoaded, siteError, loading, messages.length, sendMessage]);

  // 兜底：上下文迟迟不定形（站点/AS-BOT 请求挂住、上一轮长时间流式）时不能把问题无限期悬着——
  // 超时就退回预填并把原因说出来（按 Enter 仍可发）。已发出（sent）或已清空时，
  // 本 effect 会重新跑并直接早退，旧计时器在清理函数里被撤掉，不会二次触发。
  useEffect(() => {
    if (!pendingAsk?.send || pendingAsk.sent) return;
    const st = pendingAsk;
    const timer = window.setTimeout(() => {
      setPendingAsk(null);
      setInput(st.question);
      inputRef.current?.focus();
      toast.error(t('page.contextNotReady', { question: st.question }));
    }, PENDING_ASK_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [pendingAsk]);

  // AS-BOT 面板「在工作空间中打开该会话」带入的会话 id:挂载时消费一次并切换到该会话
  useEffect(() => {
    const raw = sessionStorage.getItem('asbot_open_conversation');
    if (!raw) return;
    sessionStorage.removeItem('asbot_open_conversation');
    const convId = Number(raw);
    if (convId) void switchConversation(convId);
  }, []);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  // 用户是否停留在底部: 上滚查看历史时不打断, 回到底部后恢复自动跟随。
  const stickToBottomRef = useRef(true);

  const prevMsgCountRef = useRef(0);
  const prevConvIdRef = useRef<number | null | undefined>(undefined);
  const [feedbackMap, setFeedbackMap] = useState<Record<number, 'up' | 'down'>>({});
  const [showExpectedInput, setShowExpectedInput] = useState<Record<number, boolean>>({});
  // 踩之后的归因与期望业务名：不再让用户输**物理表名**（旧文案 "期望的表名，
  // 如 t_user_customer" 同时违反 ui-resource-display §2 与护栏 §7，且业务用户根本不知道表名）
  const [feedbackReason, setFeedbackReason] = useState<Record<number, string>>({});
  const [feedbackRef, setFeedbackRef] = useState<Record<number, string>>({});
  const [expectedNoteInput, setExpectedNoteInput] = useState<Record<number, string>>({});
  const [refOptions, setRefOptions] = useState<{ name: string; kind: string }[]>([]);
  const [refLoading, setRefLoading] = useState(false);

  const DISSATISFACTION_REASONS: { key: string; labelKey: 'page.feedback.missing_metric' | 'page.feedback.missing_dimension'
    | 'page.feedback.wrong_answer' | 'page.feedback.not_understood' | 'page.feedback.data_incomplete' }[] = [
    { key: 'missing_metric', labelKey: 'page.feedback.missing_metric' },
    { key: 'missing_dimension', labelKey: 'page.feedback.missing_dimension' },
    { key: 'wrong_answer', labelKey: 'page.feedback.wrong_answer' },
    { key: 'not_understood', labelKey: 'page.feedback.not_understood' },
    { key: 'data_incomplete', labelKey: 'page.feedback.data_incomplete' },
  ];

  // 期望值候选：从真实字典清单来（显示 name，选框选择）。
  // 指标/维度接口只支持 workspace 与模型作用域（无 datasource 过滤项），
  // 这里按当前工作空间取全量清单，不拿数据源去碰不存在的查询参数。
  const loadRefOptions = async () => {
    if (refOptions.length || refLoading) return;
    setRefLoading(true);
    try {
      const [m, d] = await Promise.all([
        client.get('/metrics', { params: { size: 500, workspace_id: selectedWorkspaceId || 0 } }),
        client.get('/metrics/dimensions', { params: {} }),
      ]);
      const mItems = (m.data?.items ?? m.data ?? []) as any[];
      const dItems = (d.data?.items ?? d.data ?? []) as any[];
      const out: { name: string; kind: string }[] = [];
      if (Array.isArray(mItems)) {
        for (const it of mItems) if (it?.name) out.push({ name: String(it.name), kind: 'kindMetric' });
      }
      if (Array.isArray(dItems)) {
        for (const it of dItems) if (it?.name) out.push({ name: String(it.name), kind: 'kindDimension' });
      }
      setRefOptions(out);
    } catch {
      // 候选拉不到时不伪造成可选：显式告知，并保留自由文字渠道
      toast.error(t('page.feedback.refLoadFailed'));
    } finally {
      setRefLoading(false);
    }
  };

  const handleFeedback = async (
    idx: number, satisfied: boolean,
    opts?: { reason?: string; ref?: string; note?: string },
  ) => {
    const msg = messages[idx];
    if (!msg) return;
    // Extract tables_used from warnings
    // 服务端 warnings 里的固定前缀是后端契约文案（功能性解析，不随界面语言变）
    const tablesWarning = msg.warnings?.find((w: string) => w.startsWith(tr('k_6b5a74a8d2')));
    const tablesUsed = tablesWarning ? tablesWarning.replace(tr('k_6b5a74a8d2'), '') : '';
    try {
      await client.post('/chat/feedback', {
        question: msg.question || msg.content || '',
        tables_used: tablesUsed,
        datasource_id: selectedDsId,
        satisfied,
        // 期望值一律用**业务名**（来自字典清单选择框），不再收集物理表名
        expected_ref: opts?.ref || '',
        dissatisfaction_reason: opts?.reason || '',
        // Agent/对话输出的自由补充说明
        reason: opts?.note || '',
        // 可观测关联键:有 trace 时反馈能在 Trace 列表/详情回显
        trace_id: msg.trace_id || '',
        message_uuid: msg.message_uuid || '',
        conversation_id: currentConvId || 0,
        workspace_id: selectedWorkspaceId || 0,
      });
      // Persist feedback in message
      updateMessageFeedback(idx, satisfied ? 'up' : 'down', opts?.ref || opts?.note);
      setFeedbackMap(prev => ({ ...prev, [idx]: satisfied ? 'up' : 'down' }));
      setShowExpectedInput(prev => ({ ...prev, [idx]: false }));
      toast.success(satisfied
        ? t('page.feedback.thanksUp')
        : t('page.feedback.recordedDown'));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('page.feedback.submitFailed'));
    }
  };

  // Escape key to exit focus mode
  useEffect(() => {
    if (!focusMode) return;
    const handler = (e: KeyboardEvent) => {
      // `/` 选择面板开着时 Esc 归输入框（退出选择），不叠加“退出专注模式”
      if (e.key === 'Escape' && !slashOpenRef.current) setFocusMode(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [focusMode]);

  // Sync workspaceId from URL to chatStore and reload all data
  useEffect(() => {
    if (urlWorkspaceId) {
      const wsId = Number(urlWorkspaceId);
      if (wsId && wsId !== selectedWorkspaceId) {
        setSelectedWorkspaceId(wsId);
        // Reset selections for new workspace
        setSelectedDsId(0);
        setSelectedModelId(null);
        // Clear current conversation
        useChatStore.setState({ currentConvId: null, messages: [] });
        // Reload all data
        loadDatasources();
        loadAuthorizedSites(wsId);
        loadLLMModels();
        loadSystemConfig();
        loadWorkspaceConfig(wsId);
        setInput(''); setPendingAtts([]); setRenamingId(null); setConversationsOpen(false);
      }
      if (wsId) void loadConversations();
    }
  }, [urlWorkspaceId]);

  // 执行层与模型候选、AS-BOT 清单:跟随工作空间变化刷新(不依赖 URL 参数,
  // 覆盖同工作空间重新进入 Chat 等场景,避免候选停留在空/旧层)
  useEffect(() => {
    if (selectedWorkspaceId) {
      loadExecutionLayer(selectedWorkspaceId);
      loadAsBots(selectedWorkspaceId);
      loadAuthorizedSites(selectedWorkspaceId);
    }
  }, [selectedWorkspaceId, loadExecutionLayer, loadAsBots, loadAuthorizedSites]);

  // 跟踪用户是否贴底(基于 Radix ScrollArea 的 viewport), 用于决定流式期间是否自动跟随。
  useEffect(() => {
    const viewport = scrollAreaRef.current?.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement | null;
    if (!viewport) return;
    const onScroll = () => {
      stickToBottomRef.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 80;
    };
    viewport.addEventListener('scroll', onScroll, { passive: true });
    return () => viewport.removeEventListener('scroll', onScroll);
  }, []);

  // Auto-scroll: 新消息 / 流式过程(思考、工具调用、正文)增长时跟随到底部。
  const lastMsg = messages[messages.length - 1];
  const streamSig = lastMsg
    ? `${(lastMsg.thinking || '').length}:${(lastMsg.content || '').length}:${(lastMsg.reply || '').length}:${lastMsg.process?.length || 0}:${lastMsg.tool_calls?.length || 0}`
    : '';
  useEffect(() => {
    const count = messages.length;
    const convChanged = currentConvId !== prevConvIdRef.current;
    const newMessage = count > prevMsgCountRef.current;
    if (convChanged) {
      // 打开/切换历史会话: 整段消息被替换 → 直接瞬时定位到底部, 并恢复自动跟随。
      stickToBottomRef.current = true;
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
      // 图表/图片异步渲染撑高容器后再补一次瞬时定位, 确保真正贴底。
      requestAnimationFrame(() => messagesEndRef.current?.scrollIntoView({ behavior: 'auto' }));
    } else if (newMessage) {
      // 自己发出的新消息 → 强制跟随到底部。
      stickToBottomRef.current = true;
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    } else if (loading && stickToBottomRef.current) {
      // 流式内容增长 → 仅当用户仍停留在底部时跟随(瞬时, 避免 smooth 动画堆积)。
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
    }
    prevMsgCountRef.current = count;
    prevConvIdRef.current = currentConvId;
  }, [messages.length, loading, currentConvId, streamSig]);

  // ── 长会话定位（右侧提问节点导航）────────────────────────────────
  // 节点只有已渲染的消息正文可用：不请求接口、不调模型，纯前端从会话消息里取。
  // 锚点用 ref 表而不是 DOM id（同一时刻只挂一个 Chat，但 ref 表跟着消息数组走，
  // 会话切换/清空后不会残留指向已卸载节点的悬空引用）。
  const msgRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const getMsgAnchor = useCallback(
    (idx: number) => msgRefs.current[idx] ?? null, []);
  const getScrollViewport = useCallback(
    () => scrollAreaRef.current?.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement | null,
    []);
  /**
   * 往前翻一页更早的消息，并补偿滞后的滚动位置。
   *
   * 不补偿的话，prepend 会把原本在顶部的内容往下顶，用户看着像“翻不动”；
   * 贴底跟随的判定（stickToBottomRef）也要跟着重置，否则刚拉到的旧消息会被
   * 当成“新内容”又把页面弹回底部。
   */
  const onLoadEarlierMessages = useCallback(async () => {
    const viewport = getScrollViewport();
    const prevHeight = viewport?.scrollHeight ?? 0;
    await loadEarlierMessages();
    requestAnimationFrame(() => {
      const box = getScrollViewport();
      if (!box) return;
      const delta = box.scrollHeight - prevHeight;
      if (delta > 0) box.scrollTop += delta;
      stickToBottomRef.current = false;
    });
  }, [getScrollViewport, loadEarlierMessages]);
  const questionTurns: TurnNavItem[] = useMemo(() => messages
    .map((m, idx) => ({ idx, m }))
    .filter(({ m }) => m.role === 'user')
    .map(({ idx, m }) => {
      const text = String(m.content || '').replace(/\s+/g, ' ').trim();
      // 只带附件的提问不能显示成空白节点（点了不知道跳到哪一问）
      const atts = (m.attachments || []).map(a => a.filename).filter(Boolean);
      return {
        index: idx,
        text: text || (atts.length ? t('page.turnNavAttachments', { files: atts.join(t('page.listSeparator')) }) : t('page.turnNavNoText')),
      };
    }), [messages, t]);

  // 摘掉检索片段用的 stripSlashToken 与 slashQueryOf 同在 SlashCommandMenu（同一触发符集，不两处各写一份）
  const pickSlashItem = (item: SlashItem) => {
    const el = inputRef.current;
    const caret = el?.selectionStart ?? input.length;
    const rest = stripSlashToken(input, caret).replace(/^\s+/, '');
    // 选中是否真落定：超上限被拒的那次不该计入使用度（否则「常用」会被没生效的点刷出来）
    let applied = true;
    if (item.kind === 'task') {
      // 定时任务：复用它的分析问题作为本轮提问起点，用户可继续补充信息
      setInput(`${item.question}${rest ? ` ${rest}` : ''}`);
    } else if (item.kind === 'scene') {
      // 候选没带可用标识 = 服务端契约对不上：显式拒，不能把一个 undefined 挂上 chip
      // 再当“已选中”发出去（选了等于没选，比报错更难查）。
      if (!item.scene) {
        toast.error(t('page.sceneMissingId', { label: item.label }));
        applied = false;
        setSlashQuery(null);
        return;
      }
      // 场景：选中即本轮强制按它的查询方案做（服务端 paths_by_ids，不看触发词）。
      // 上限与技能同口径：多份方案同时注入会相互干扰，选了等于没选就先把话讲清。
      if (!pickedScenes.includes(item.scene)) {
        if (pickedScenes.length >= MAX_PINNED_SCENES) {
          toast.error(t('page.sceneLimit', { max: MAX_PINNED_SCENES }));
          applied = false;
        } else {
          setPickedScenes(prev => [...prev, item.scene]);
        }
      }
      setInput(rest);
    } else if (item.kind === 'skill') {
      if (!pickedSkills.includes(item.name)) {
        if (pickedSkills.length >= MAX_PINNED_SKILLS) {
          toast.error(t('page.skillLimit', { max: MAX_PINNED_SKILLS }));
          applied = false;
        } else {
          setPickedSkills(prev => [...prev, item.name]);
        }
      }
      setInput(rest);
    } else {
      // kind=action 的指令（如清空当前会话）是客户端会话动作：就地弹确认执行，
      // 不挂成本轮指令也不进请求体（服务端对 action 类上送会 400 拒，那是契约错配）。
      if (item.action) {
        if (item.action === 'clear_conversation') {
          if (messages.length === 0) toast.info(t('page.alreadyEmpty'));
          else setClearConfirm(true);
        }
        // 确认前不算“落定”，不写入最近使用（否则取消的操会把「常用」刷出来）
        applied = false;
        setInput(rest);
      } else {
        setPickedCommand(item.command);
        setInput(rest);
      }
    }
    if (applied) recordSlashUsage(item.id);
    setSlashQuery(null);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  // `@` 选中一个文件：摘掉 `@xxx` 片段，把引用凭据挂成 chip（正文不留文件名——
  // 文件名不是引用凭据，重名/改名都会引错，凭据随请求体上送由服务端按 ref 取内容）。
  const pickCloudFile = (item: CloudFileItem) => {
    const el = inputRef.current;
    const caret = el?.selectionStart ?? input.length;
    setInput(stripMentionToken(input, caret).replace(/^\s+/, ''));
    if (pendingAtts.length + cloudRefs.length >= ATTACH_MAX_FILES) {
      // 与服务端同口径（上传附件与 @ 引用合计计入单轮上限），超了当场说，不发出去再被拒
      toast.error(t('page.fileLimit', { max: ATTACH_MAX_FILES }));
    } else if (!cloudRefs.some(c => c.source === item.source && c.ref === item.ref)) {
      setCloudRefs(prev => [...prev, item]);
    }
    setMentionQuery(null);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const handleSlashInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    const caret = e.target.selectionStart ?? v.length;
    setInput(v);
    const mq = mentionQueryOf(v, caret);
    setMentionQuery(mq);
    // 检索词变了就重算清单，高亮由 useSlashMenu 归回首条
    setSlashQuery(mq !== null ? null : slashQueryOf(v, caret));
  };

  const handleSlashInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (slashOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        slashMenu.moveRow(e.key === 'ArrowDown' ? 1 : -1);
        return;
      }
      // ←→ 切类别：面板开着时光标的左右移动被接管（要改 `/` 前的正文先按 Esc 收起）。
      // 只有一栏时不接管，避免按方向键却什么都没发生的哑键。
      if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && slashMenu.groups.length > 1) {
        e.preventDefault();
        slashMenu.moveTab(e.key === 'ArrowRight' ? 1 : -1);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        const item = slashMenu.current();
        if (item) { e.preventDefault(); pickSlashItem(item); return; }
        // 菜单开着却没候选：Enter 先把面板收起，不把半截过滤串（如 `/技能 数`）当问题发出去；
        // 确实想发以 / 开头的正文，再按一次 Enter 即可。
        e.preventDefault();
        setSlashQuery(null);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        // 退出「/」选择：关面板 + 摘掉正在检索的 `/xxx` 片段。只关面板不摘片段的话，
        // 残留的 `/` 会在下一个字符又把面板顶回来，用户看到的是“Esc 按了没反应”。
        const el = inputRef.current;
        const caret = el?.selectionStart ?? input.length;
        const stripped = stripSlashToken(input, caret);
        if (stripped !== input) setInput(stripped);
        setSlashQuery(null);
        return;
      }
    }
    if (mentionOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        mentionMenu.moveRow(e.key === 'ArrowDown' ? 1 : -1);
        return;
      }
      if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && mentionMenu.enabled.length > 1) {
        e.preventDefault();
        mentionMenu.moveTab(e.key === 'ArrowRight' ? 1 : -1);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        const item = mentionMenu.current();
        if (item) { e.preventDefault(); pickCloudFile(item); return; }
        // 无候选时 Enter 先收面板：正文里写邮箱（a@b.com）不该被当成引用检索拦下发送
        e.preventDefault();
        setMentionQuery(null);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        const el = inputRef.current;
        const caret = el?.selectionStart ?? input.length;
        const stripped = stripMentionToken(input, caret);
        if (stripped !== input) setInput(stripped);
        setMentionQuery(null);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const handleSend = () => {
    if ((!input.trim() && pendingAtts.length === 0 && cloudRefs.length === 0) || loading) return;
    // 用户已经在框里自己发了 → 首页带入的待发句不再自动补发（否则本轮结束后凭空多出一问）
    if (pendingAsk && !pendingAsk.sent) setPendingAsk(null);

    const attachments = pendingAtts.length > 0 ? pendingAtts : undefined;
    const questionText = input.trim()
      || (cloudRefs.length
        ? t('page.analyzeRefsQuestion', { files: cloudRefs.map(c => c.filename).join(t('page.listSeparator')) })
        : t('page.analyzeAttachmentsQuestion'));

    // 未选技能/场景/指令/引用文件时不传第四个参数（保持与旧调用形同，也不往请求体里塞空字段）
    const opts: NonNullable<Parameters<typeof sendMessage>[3]> = {
      ...(pickedSkills.length ? { skills: pickedSkills } : {}),
      ...(pickedScenes.length ? { scenes: pickedScenes } : {}),
      ...(pickedCommand ? { command: pickedCommand } : {}),
      ...(cloudRefs.length ? { cloudFiles: cloudRefs } : {}),
    };
    if (Object.keys(opts).length) {
      sendMessage(questionText, undefined, attachments, opts);
    } else {
      sendMessage(questionText, undefined, attachments);
    }

    setInput('');
    setPendingAtts([]);
    setPickedSkills([]);
    setPickedScenes([]);
    setPickedCommand('');
    setSlashQuery(null);
    setCloudRefs([]);
    setMentionQuery(null);
  };

  // 点踩后的归因区：展开在操作行下方（不属于操作行本身）。原因走固定清单、
  // 期望业务名走字典清单选择，不让人手输表名/字段名。
  const renderFeedbackPanel = (idx: number, fb?: string) => (
    <>
        {showExpectedInput[idx] && !fb && (
          <div className="mt-2 w-full space-y-2 border border-border/60 rounded-md p-2 bg-muted/20">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-muted-foreground shrink-0">{t('page.feedback.whatWrong')}</span>
              {DISSATISFACTION_REASONS.map(r => (
                <button key={r.key} type="button"
                  onClick={() => setFeedbackReason(prev => ({ ...prev, [idx]: r.key }))}
                  className={`h-6 px-2 rounded-md text-xs border transition-colors ${
                    feedbackReason[idx] === r.key
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border text-muted-foreground hover:bg-muted'}`}
                >{t(r.labelKey)}</button>
              ))}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-muted-foreground shrink-0">{t('page.feedback.expectedRef')}</span>
              <Select
                value={feedbackRef[idx] || 'none'}
                onValueChange={(v) => setFeedbackRef(prev => ({ ...prev, [idx]: v === 'none' ? '' : v }))}
                disabled={refLoading && !refOptions.length}
              >
                <SelectTrigger className="w-[240px] h-7 text-xs">
                  <SelectValue placeholder={refLoading ? t('page.feedback.refLoading') : t('page.feedback.refPlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t('page.feedback.refNone')}</SelectItem>
                  {refOptions.map(o => (
                    <SelectItem key={`${o.kind}:${o.name}`} value={o.name}>
                      {o.name}
                      <span className="ml-1 text-[10px] text-muted-foreground">({t(o.kind === 'kindMetric' ? 'page.feedback.kindMetric' : 'page.feedback.kindDimension')})</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {refOptions.length === 0 && !refLoading && (
                <span className="text-[10px] text-amber-600">
                  {t('page.feedback.refEmpty')}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Input
                value={expectedNoteInput[idx] || ''}
                onChange={(e) => setExpectedNoteInput(prev => ({ ...prev, [idx]: e.target.value }))}
                placeholder={t('page.feedback.notePlaceholder')}
                className="h-7 text-xs flex-1"
              />
              <Button size="sm" variant="default" className="h-7 text-xs"
                disabled={!feedbackReason[idx] && !feedbackRef[idx] && !expectedNoteInput[idx]}
                onClick={() => handleFeedback(idx, false, {
                  reason: feedbackReason[idx], ref: feedbackRef[idx], note: expectedNoteInput[idx],
                })}>
                {t('common:action.submit')}
              </Button>
              <Button size="sm" variant="ghost" className="h-7 text-xs"
                onClick={() => { handleFeedback(idx, false); setShowExpectedInput(prev => ({ ...prev, [idx]: false })); }}>
                {t('page.feedback.skip')}
              </Button>
            </div>
          </div>
        )}
    </>
  );

  /**
   * 会话内「存为报告草稿」动作（消息操作行与交付文档卡共用一份）：
   * 入口可以有两处，流程只能有一份 —— 两处各写一套就会有一处漏跟 reportable 判定。
   */
  const reportSaveAction = (idx: number, msg: any) => (msg?.reportable === true ? {
    busy: savingReport !== null || loading,
    // 两段式第一段：先在会话里生成草稿（可反复改），侧栏里点「归档」才进报告中心
    label: msg.reportDraft ? t('messageActions.continueReportDraft') : t('messageActions.saveReportDraft'),
    onSave: () => { void createReportDraftFromTurn(idx, msg); },
  } : undefined);

  /**
   * 消息操作行（参照千问）：一条回答只有**一行**操作，全部左靠，不分两端也不另起一行。
   *
   * 不是每轮都有的入口一律收进 `⋯`：存为报告要服务端判定本轮 reportable，
   * 转文档编辑要有正文；主行只留确定会出现的复制与赞踩。
   */
  const renderMessageActions = (idx: number, msg: any) => {
    const fb = msg.feedback || feedbackMap[idx];
    const answer = answerMarkdownOf(msg);
    const hasResultCard = !!msg.result && !msg.result.error;
    // 赞踩的可达范围沿用原口径：有结果卡，或 Agent 纯文本回复（无 SQL 卡、无报错）
    const giveFeedback = hasResultCard
      || (!!msg.reply && !msg.sql && !msg.error && msg.intent === 'agent');
    if (!answer.trim() && !giveFeedback && msg.reportable !== true) return null;
    return (
      <>
        <MessageActions
          markdown={answer}
          allowDoc
          doc={msg.doc}
          docBusy={docBusyIdx === idx}
          disabled={loading && idx === messages.length - 1}
          onCreateDoc={() => { void createDocFromMessage(idx, msg); }}
          onOpenDoc={() => setDocEditorIdx(idx)}
          report={reportSaveAction(idx, msg)}
          feedback={giveFeedback ? {
            value: fb,
            onUp: () => { void handleFeedback(idx, true); },
            onDown: () => {
              setShowExpectedInput(prev => ({ ...prev, [idx]: !prev[idx] }));
              if (!showExpectedInput[idx]) void loadRefOptions();
            },
          } : undefined}
        />
        {renderFeedbackPanel(idx, fb)}
      </>
    );
  };

  // 本地校验 + blob 预览,暂存待发附件;文件随消息以 multipart 上传,服务端落盘到会话工作区
  const addPendingFiles = (files: File[]): number => {
    const next = [...pendingAtts];
    for (const f of files) {
      if (next.length >= ATTACH_MAX_FILES) {
        toast.error(t('page.attachLimit', { max: ATTACH_MAX_FILES }));
        break;
      }
      const v = validateLocalAttachment(f);
      if ('error' in v) {
        toast.error(v.error);
        continue;
      }
      next.push({ file: f, filename: f.name, category: v.category, size: f.size, blobUrl: URL.createObjectURL(f) });
    }
    const added = next.length - pendingAtts.length;
    if (added > 0) setPendingAtts(next);
    return added;
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (files.length === 0) return;
    addPendingFiles(files);
  };

  // Ctrl+V 粘贴截图/文件 → 直接放入待发附件,随消息一起发给模型;纯文本粘贴走默认行为
  const handlePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    const files = filesFromClipboard(e.clipboardData);
    if (files.length === 0) return;
    e.preventDefault();
    const added = addPendingFiles(files);
    if (added > 0) toast.success(t('page.attachAdded', { count: added }));
  };

  const removePendingAtt = (att: PendingAttachment) => {
    URL.revokeObjectURL(att.blobUrl);
    setPendingAtts(prev => prev.filter(a => a.blobUrl !== att.blobUrl));
  };

  // 图上下钻:点击图表元素 → 预填下钻问题到输入框(用户确认后发送)
  const handleDrill = (q: string) => {
    setInput(q);
    inputRef.current?.focus();
  };

  // 结果表保存到会话工作区(落盘 uploads/ 的 xlsx,可在文件面板查看/归档)
  const [savingResult, setSavingResult] = useState(false);
  const saveResultToWorkspace = async (result: any, n: number) => {
    if (!currentConvId || !result?.columns?.length) return;
    setSavingResult(true);
    try {
      const { data } = await client.post('/chat/session-file/table', {
        conversation_id: currentConvId,
        filename: t('page.resultFilename', { n }),
        columns: result.columns,
        rows: result.rows || [],
      });
      toast.success(t('page.savedToWorkspace', { filename: data.filename }));
      setFilesRefreshKey(k => k + 1);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('page.saveFailedRetry'));
    } finally {
      setSavingResult(false);
    }
  };

  // ── 存为报告（两段式）─────────────────────────────────────────
  // 第一段：服务端从该轮回答渲染**草稿初稿** → 挂在消息上随会话落库（只属于本会话、可反复改）。
  // 第二段：点「归档」才进报告中心（服务端读会话里那份草稿建报告，正文仍不信 body）。
  // 为什么不一步入报告中心：报告中心清单不过滤 publication_status，未定稿的东西会混进正式产物。
  const [savingReport, setSavingReport] = useState<number | null>(null);
  const [draftEditorIdx, setDraftEditorIdx] = useState<number | null>(null);

  const createReportDraftFromTurn = async (idx: number, msg: any) => {
    const existing = msg?.reportDraft as ChatReportDraft | undefined;
    if (existing) { setDraftEditorIdx(idx); return; }   // 一条回答只一份：再点就是继续编辑
    if (!currentConvId) {
      toast.error(t('page.reportNotPersisted'));
      return;
    }
    setSavingReport(idx);
    try {
      const draft = await createReportDraft({
        conversation_id: currentConvId, message_index: idx, message_uuid: msg?.message_uuid || '',
      });
      const ok = await saveMessageReportDraft(idx, {
        title: draft.title, markdown: draft.markdown,
        // 正文格式与产物名随草稿带走：HTML 报告产物的草稿不是 markdown，
        // 编辑器得按格式分流（当纯文本改会把整份报告改坏）
        format: draft.format, artifact: draft.artifact || null,
        created_at: draft.created_at, updated_at: draft.updated_at,
        revisions: 0, archived: null,
      });
      if (!ok) {
        toast.error(t('page.reportDraftWriteFailed'));
        return;
      }
      setDraftEditorIdx(idx);
      setSidePanelOpen(prev => {
        // 产物刚生成却藏在收起的侧栏里等于没告知：未展开时自动展开一次
        if (prev) return prev;
        localStorage.setItem(SIDE_PANEL_KEY, '1');
        return true;
      });
    } catch (e: any) {
      // 服务端给的是可解释原因（“该回合是问答不是分析结果”等），必须原样告知
      toast.error(e?.response?.data?.detail || t('page.reportDraftCreateFailed'));
    } finally {
      setSavingReport(null);
    }
  };

  const archiveReportDraft = async (idx: number) => {
    const msg: any = messages[idx];
    const draft = msg?.reportDraft as ChatReportDraft | undefined;
    if (!currentConvId || !draft) {
      toast.error(t('page.reportDraftNotFound'));
      return;
    }
    setSavingReport(idx);
    try {
      const res = await saveConversationAsReport({
        conversation_id: currentConvId, message_index: idx, message_uuid: msg?.message_uuid || '',
      });
      const archived = {
        report_id: res.report_id, title: res.title,
        archived_at: res.archived_at || new Date().toISOString().replace('T', ' ').slice(0, 19),
      };
      const ok = await saveMessageReportDraft(idx, { ...draft, archived });
      if (!ok) {
        // 报告已落库、会话里的归档标记没写回：如实说出来（服务端幂等，再点不会重复建）
        toast.warning(t('page.archiveMarkFailed', { title: res.title }));
        return;
      }
      toast.success(res.already_archived
        ? t('page.alreadyArchived', { title: res.title })
        : t('page.archivedToCenter', { title: res.title }), {
        // 独立问答页(/ask/)没有工作空间侧栏，只提示结果，不给跳转
        action: independent || !selectedWorkspaceId ? undefined
          : { label: t('sidePanel.goView'), onClick: () => navigate(`/ws/${selectedWorkspaceId}/reports`) },
      });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('page.archiveFailed'));
    } finally {
      setSavingReport(null);
    }
  };

  // ── 转文档编辑（参照千问）────────────────────────────────────
  // 文档正文随会话消息 JSON 落库（与会话同生命周期），不另建资产表；
  // 一条回答只有一份文档：重复点击是继续编辑，不是再建一份。
  const [docBusyIdx, setDocBusyIdx] = useState<number | null>(null);
  const [docEditorIdx, setDocEditorIdx] = useState<number | null>(null);
  const createDocFromMessage = async (idx: number, msg: any) => {
    if (msg?.doc) { setDocEditorIdx(idx); return; }
    if (!currentConvId) {
      toast.error(t('page.docNotPersisted'));
      return;
    }
    if (!answerMarkdownOf(msg).trim()) {
      toast.info(t('page.docNoBody'));
      return;
    }
    setDocBusyIdx(idx);
    try {
      const now = new Date().toISOString();
      // html 留空：打开面板时由与气泡同一渲染器把 markdown 初稿化并落一次库
      const ok = await saveMessageDoc(idx, {
        title: '', html: '', created_at: now, updated_at: now, revisions: 0,
      });
      if (!ok) {
        toast.error(t('page.docCreateFailed'));
        return;
      }
      setDocEditorIdx(idx);
    } finally {
      setDocBusyIdx(null);
    }
  };

  // ── 会话历史批量管理 ──────────────────────────────────────────
  const exitBatch = () => { setBatchMode(false); setSelectedIds([]); setBatchConfirm(false); };
  const toggleConvSelect = (id: number) => setSelectedIds(prev => {
    if (prev.includes(id)) return prev.filter(x => x !== id);
    if (prev.length >= MAX_BATCH_DELETE) {
      toast.error(t('page.batchLimit', { max: MAX_BATCH_DELETE }));
      return prev;
    }
    return [...prev, id];
  });
  const allSelected = conversations.length > 0 && conversations.every(c => selectedIds.includes(c.id));
  const toggleSelectAll = () => {
    if (allSelected) { setSelectedIds([]); return; }
    const ids = conversations.map(c => c.id);
    if (ids.length > MAX_BATCH_DELETE) {
      setSelectedIds(ids.slice(0, MAX_BATCH_DELETE));
      toast.error(t('page.batchLimitSelected', { max: MAX_BATCH_DELETE }));
      return;
    }
    setSelectedIds(ids);
  };
  const pendingDelete = conversations.filter(c => selectedIds.includes(c.id));

  const runBatchDelete = async () => {
    const ids = [...selectedIds];
    if (!ids.length || batchDeleting) return;
    setBatchDeleting(true);
    try {
      const { deleted, failed } = await deleteConversations(ids);
      if (deleted.length) toast.success(t('page.batchDeleted', { count: deleted.length }));
      if (failed.length) {
        // 失败要点名到具体会话（用标题，不是裸 id）并说清原因：正在执行/清理未完/已不存在
        // 的下一步动作不同，把整批笼统说成“部分失败”等于没告知。
        const detail = failed.slice(0, 3)
          .map(f => t('page.batchFailedItem', { title: f.title || t('page.untitledConversation'), reason: f.reason })).join(t('page.itemSeparator'));
        toast.error(t('page.batchFailedSummary', { count: failed.length, detail, more: failed.length > 3 ? t('page.etc') : '' }));
      }
      // 失败的保持勾选便于重试，成功的从勾选里移除；不静默退出批量态
      setSelectedIds(failed.map(f => f.id));
      if (!failed.length) setBatchMode(false);
      void loadConversations();
    } finally {
      setBatchDeleting(false);
      setBatchConfirm(false);
    }
  };

  /** 删除一轮问答（提问 + 其回答）；结果以 store/saveMessages 的返回为准，不发假成功提示 */
  const handleDeleteTurn = async () => {
    if (deleteTurnIdx === null) return;
    const ok = await deleteTurn(deleteTurnIdx);
    if (ok) toast.success(t('page.turnDeleted'));
    setDeleteTurnIdx(null);
  };

  /** 「/ 清空当前会话」：clear() 自己报失败原因，成功才给确认文案 */
  const handleClearConversation = async () => {
    setClearConfirm(false);
    if (await clear()) toast.success(t('page.cleared'));
  };

  // 输入卡片（参考千问起始页）：不横贯全屏，而是收窄居中（与消息列同量级）；
  // 附件/会话文件/发送都收进卡片底行，卡片自身是唯一边框（focus 时高亮）。
  // 新对话空态下这张卡片跟着欢迎语一起居中，有消息后落回底部。
  const assistantName = asBots[0]?.display_name || asBots[0]?.name || t('page.defaultAssistantName');
  // 首页带问题进来、自动发送还没能发出的这几百毫秒（要等工作空间同步 + 站点/AS-BOT 清单定形，
  // 否则问题会发进一个还没解析完的取数范围）：这段时间按“会话中”版式先把那句摆出来，
  // 不能让画面停在欢迎页上——用户已经按下发送了，画面必须立刻承认收到了。
  // 待发气泡只在本轮还没落进 store 时占位（落进后由 store 里那条用户消息接管，不得两句同文）
  const pendingQuestion = pendingAsk?.send && messages.length === 0 ? pendingAsk.question : '';
  const urlWsNum = Number(urlWorkspaceId);
  const pendingAskStage = pendingAsk?.sent
    ? t('page.stageSending')
    : loading
      ? t('page.stageQueued')
      : (urlWsNum && selectedWorkspaceId !== urlWsNum)
        ? t('page.stageSwitchingWs')
        : (!sitesLoaded || !asBotsLoaded)
          ? t('page.stagePreparing')
          : t('page.stageSending');
  // 消息列/底部输入卡片的版式开关：有历史消息、或有一句待发的提问，都走“会话中”布局
  const hasTurnUI = messages.length > 0 || !!pendingQuestion;
  // 消息列与输入卡片共用**同一根内容列**：用户气泡、AI 回复卡、输入卡片三者右边缘对齐。
  // 宽度口径 = **可用宽度的 75%**（按百分比而不是像素上限）：像素上限在窄窗口（常见
  // 1280~1440 CSS px）等于没约束，看上去“改了等于没改”。再叠一个像素上限，
  // 防止超宽屏把正文拉成一行几百字。
  // 侧栏**真的在页面上渲染**的口径（唯一裁决点）：展开偏好 + 有会话消息可摘。
  // 正文列放开全宽的前提是“右侧那段留白已被侧栏接管”，而新会话空态根本不渲染侧栏——
  // 直接用 sidePanelOpen 判宽度就会出现“点一下文件夹，欢迎页/输入卡片被拉满屏、侧栏却没出现”。
  // 宽度、侧栏渲染、入口按钮三处一律读这一个标志，不得各写各的条件。
  const sidePanelShown = sidePanelOpen && messages.length > 0;
  const CHAT_COLUMN_W = sidePanelShown
    ? 'w-full min-w-0 max-w-[1400px]'   // 右侧那段留白已被侧栏接管，正文列再守 75% 就是双重留白
    : 'w-[75%] min-w-0 max-w-[1400px]';
  // 锚位按版式分流（用户拍板）：
  // - **会话中**：靠左（ml-0 mr-auto），右侧那 25% 整块留给后续功能（会话产物展示等）；
  //   居中会把这块留白劈成两半，等于两边都放不下东西。
  // - **新会话空态**：居中（mx-auto）——还没有产物，不需要右侧留白，跟着欢迎语走居中版式。
  // focusMode 不参与判定：全屏只是窗口变宽，“会话中要留右侧空白”照样成立。
  const chatAnchor = hasTurnUI ? 'ml-0 mr-auto' : 'mx-auto';
  const CHAT_COLUMN = `${chatAnchor} ${CHAT_COLUMN_W}`;
  // 出现效果（卡片入场）走唯一解析口 useMotion：Chat 面 = 「个人偏好 > 系统默认」（不传 board）。
  // 默认档 mp_default 产出的 class 与改造前的 animate-stage-in 同时长同曲线同位移（观感零变化），
  // 用户调过时长/错峰才换 CSS 变量 —— globals.css 不写死数值，改字模即时生效。
  const { motion, cssVars: motionCss } = useMotion();
  const CHAT_ENTER = enterClass(motion.enter);
  // 卡片出场只播在「内容首次就绪」那一刻：msg.sql 在流式阶段就到了、结果集还要等几秒，
  // 外壳若在容器挂载时就播，用户看到图时入场早已结束 —— 剩下的只有图表自己在动。
  // class 由空变非空只发生一次：后续刷新数据不重播，空结果也算内容（它确实出现了）。
  const cardEnterOf = (msg: any) => (msg.result || msg.viewMode === 'sql' ? CHAT_ENTER : '');
  const chatComposer = (
    <div className="w-full min-w-0">
      {/* 排队/重试提示：挂在卡片上方（与 `/` 面板同一锚位），不遮住正在输入的正文 */}
      <CapacityNotice
        state={capacity}
        onCancel={loading || capacity.phase === 'queued' || capacity.phase === 'retrying' ? cancelMessage : undefined}
        onRetry={retryTurn}
        onSwitchModel={openModelPicker}
      />
      <div className="relative rounded-2xl border border-border/70 bg-card shadow-sm transition-colors focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/10">
        {/* Pending attachments preview bar */}
        {pendingAtts.length > 0 && (
          <div className="flex items-center gap-2 px-3 pt-3 flex-wrap">
            {pendingAtts.map(att => (
              <div key={att.blobUrl} className="flex items-center gap-1.5 border rounded-lg px-2 py-1 bg-muted/50">
                {att.category === 'image' ? (
                  <img src={attUrl(att)} alt={att.filename} className="h-9 w-9 object-cover rounded" />
                ) : att.category === 'model3d' ? (
                  <Box className="h-5 w-5 text-muted-foreground" />
                ) : (
                  <FileText className="h-5 w-5 text-muted-foreground" />
                )}
                <span className="text-xs max-w-[130px] truncate" title={att.filename}>{att.filename}</span>
                <Badge variant="secondary" className="text-[10px] px-1 py-0">{CATEGORY_LABEL_KEYS[att.category] ? t(CATEGORY_LABEL_KEYS[att.category]) : att.category}</Badge>
                <button
                  className="text-muted-foreground hover:text-destructive"
                  onClick={() => removePendingAtt(att)}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 已 @ 引用的文件 chip：引用凭据挂在这里，正文不留文件名（重名/改名不会引错） */}
        {cloudRefs.length > 0 && (
          <div className="flex items-center gap-2 px-3 pt-3 flex-wrap">
            {cloudRefs.map(c => (
              <div key={`${c.source}:${c.ref}`} className="flex items-center gap-1.5 border rounded-lg px-2 py-1 bg-muted/50" title={c.filename}>
                {c.source === 'asset'
                  ? <Archive className="h-4 w-4 shrink-0 text-muted-foreground" />
                  : <FolderOpen className="h-4 w-4 shrink-0 text-muted-foreground" />}
                <span className="text-xs max-w-[150px] truncate">{c.name || c.filename}</span>
                <Badge variant="secondary" className="text-[10px] px-1 py-0">
                  {c.source === 'asset' ? t('cloudFiles.groupAsset') : t('cloudFiles.groupSession')}
                </Badge>
                <span className="text-[10px] text-muted-foreground">{formatSize(c.size)}</span>
                <button
                  className="text-muted-foreground hover:text-destructive"
                  title={t('page.unrefFile')}
                  onClick={() => setCloudRefs(prev => prev.filter(x => !(x.source === c.source && x.ref === c.ref)))}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 已选技能 / 场景 / 指令 chip：发送前就能看清本轮要用什么，不对着空输入框猜 */}
        {(pickedSkills.length > 0 || pickedScenes.length > 0 || pickedCommand) && (
          <div className="flex items-center gap-1.5 px-3 pt-3 flex-wrap">
            {pickedScenes.map(id => (
              <Badge key={id} variant="secondary" className="gap-1 pr-1 text-xs">
                <Workflow className="h-3 w-3" />
                {slashOptions?.scenes?.find(s => s.key === id)?.name || t('page.sceneFallback')}
                <button className="hover:text-destructive" title={t('page.removeSceneTitle')}
                  onClick={() => setPickedScenes(prev => prev.filter(x => x !== id))}>
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
            {pickedSkills.map(name => (
              <Badge key={name} variant="secondary" className="gap-1 pr-1 text-xs">
                <Sparkles className="h-3 w-3" />
                {slashOptions?.skills.find(s => s.name === name)?.display_name || name}
                <button className="hover:text-destructive" title={t('page.removeSkillTitle')}
                  onClick={() => setPickedSkills(prev => prev.filter(x => x !== name))}>
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
            {pickedCommand && (
              <Badge variant="secondary" className="gap-1 pr-1 text-xs">
                <Terminal className="h-3 w-3" />
                {slashOptions?.commands.find(c => c.key === pickedCommand)?.label || pickedCommand}
                <button className="hover:text-destructive" title={t('page.removeCommandTitle')}
                  onClick={() => setPickedCommand('')}>
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            )}
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          accept=".png,.jpg,.jpeg,.gif,.webp,.csv,.xlsx,.pdf,.md,.txt,.docx,.obj,.glb,.stl"
          onChange={handleFileSelect}
        />
        <div className="px-2 pt-1.5">
          <Input
            ref={inputRef}
            data-tour={TOUR.chatInput}
            value={input}
            onChange={handleSlashInputChange}
            onPaste={handlePaste}
            placeholder={stopping ? t('page.inputStopping') : t('page.inputPlaceholder')}
            className="h-11 border-0 bg-transparent px-3 text-[15px] shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
            disabled={stopping}
            onKeyDown={handleSlashInputKeyDown}
          />
        </div>

        {/* 卡片底行：左侧工具、右侧上下文占用小徽标 + 发送（千问/Qoder 同构） */}
        <div className="flex items-center gap-1 px-2 pb-2">
          {/* 问答模式入口已撤（用户拍板：不区分快速问答/深度分析）：对话回到单一主路。
              前端不再上送 chat_mode（服务端收到空 = 不注入任何模式作业要求 = 存量行为），
              chatStore 也不再保留 chatMode 状态：没入口的开关与读不到的字段都是死配置。 */}
          <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0 rounded-full text-muted-foreground hover:text-foreground" disabled={loading}
            title={t('page.uploadTitle')}
            onClick={() => fileInputRef.current?.click()}>
            <Paperclip className="h-4 w-4" />
          </Button>
          <Button
            variant={sidePanelShown ? 'secondary' : 'ghost'}
            size="icon" className="h-9 w-9 shrink-0 rounded-full text-muted-foreground hover:text-foreground"
            aria-label={sidePanelShown ? t('page.sidePanelCollapse') : t('page.sidePanelExpand')}
            title={sidePanelShown
              ? t('page.sidePanelCollapseTitle')
              : messages.length > 0
                ? t('page.sidePanelExpandTitle')
                : t('page.sidePanelEmptyTitle')}
            // 空态没有产物/关键知识可摘，侧栏不渲染：与其给一颗点了只会把正文列拉宽、
            // 画面却什么都不发生的按钮，不如显式禁用并把原因写在 title 里。
            disabled={messages.length === 0}
            onClick={toggleSidePanel}>
            <FolderOpen className="h-4 w-4" />
          </Button>
          <span
            title={slashTriggerNote()}
            className="ml-1 hidden min-w-0 truncate text-xs text-muted-foreground/70 sm:block"
          >
            {t('slash.inputHint', { slash: slashTriggerHint(), mention: mentionTriggerHint() })}
          </span>
          <span className="flex-1" />
          {/* 上下文占用：平时只是右下角一条微进度条（不展示百分比数字），数值/明细与手动压缩收在点击浮层里
              （输入卡片是打字焦点，通栏进度条会把它撑高并抢走视线） */}
          <ContextUsageChip
            usage={contextUsage}
            autoCompacting={contextCompacting}
            compacting={compacting}
            blocked={loading || stopping || !!contextUsage?.busy}
            blockedReason={loading || stopping
              ? t('page.compactBlockedBusy')
              : (contextUsage?.busy ? t('page.compactBlockedExec') : undefined)}
            onCompact={() => compactContext()}
          />
          {/* 运行中：发送位就地换成「停止」方块按钮（点一下即停），不再在旁边另挂一个 ✕停止 文字按钮；
              已点停止但后端尚未释放执行权时转圈并禁用（口径同 chatStore.stopping，此时再点没有意义）。
              配色不抄外部控件的黑底：走 Button 默认变体（bg-primary / text-primary-foreground），
              跟发送钮同一颗主题色，九套主题自动跟随；形状（圆→方）之外再叠主题色 halo，状态不只靠一个视觉线索。 */}
          {loading || stopping ? (
            <Button onClick={cancelMessage} size="icon" aria-label={t('page.stopAria')} title={stopping ? t('page.stoppingTitle') : t('page.stopTitle')}
              className="h-8 w-8 shrink-0 rounded-lg ring-1 ring-primary/35"
              disabled={stopping}>
              {stopping ? <Spinner className="h-4 w-4" /> : <Square className="h-3 w-3 fill-current" />}
            </Button>
          ) : (
            <Button onClick={handleSend} size="icon" aria-label={t('page.send')} title={t('page.send')}
              className="h-8 w-8 rounded-full shrink-0"
              disabled={!input.trim() && pendingAtts.length === 0 && cloudRefs.length === 0}>
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>

        {/* `@` 文件引用面板：锚在卡片上方、与卡片同宽（与 `/` 面板同一套键位） */}
        {mentionOpen && (
          <CloudFileMenu
            groups={mentionMenu.groups}
            tab={mentionMenu.tab}
            activeIndex={mentionMenu.row}
            loading={mentionLoading}
            error={mentionError}
            query={mentionQuery || ''}
            onHover={mentionMenu.setRow}
            onTabChange={mentionMenu.gotoTab}
            onPick={pickCloudFile}
          />
        )}

        {/* `/` 候选面板：锚在卡片上方、与卡片同宽 */}
        {slashOpen && (
          <SlashCommandMenu
            groups={slashMenu.groups}
            tab={slashMenu.tab}
            activeIndex={slashMenu.row}
            error={slashError}
            query={slashQuery || ''}
            sceneNote={(slashOptions?.scenes_hidden || 0) > 0
              ? t('page.scenesHiddenNote', { count: slashOptions?.scenes_hidden })
              : undefined}
            onHover={slashMenu.setRow}
            onTabChange={slashMenu.gotoTab}
            onPick={pickSlashItem}
          />
        )}
      </div>
    </div>
  );

  if (urlWorkspaceId && Number(urlWorkspaceId) !== selectedWorkspaceId) return <div role="status" className="p-6">{t('page.stageSwitchingWs')}</div>;
  return (
    <div className={focusMode ? 'fixed inset-0 z-50 bg-background flex overflow-hidden' : 'relative flex h-full min-w-0 overflow-hidden'}>
      {conversationsOpen && <button className="absolute inset-0 z-20 bg-black/30 md:hidden" aria-label={t('page.closeConvListAria')} onClick={() => setConversationsOpen(false)} />}
      {/* 左侧会话列表在全屏（专注）模式下照样保留：切会话是高频动作，
          全屏要的是“不被外壳干扰”，不是把历史入口藏掉 */}
      <div aria-label={t('page.convListAria')} data-tour={TOUR.chatSessions} className={`${conversationsOpen ? 'absolute inset-y-0 left-0 z-30 flex bg-background' : 'hidden'} md:relative md:flex md:z-10 w-[240px] flex-shrink-0 flex-col border-r bg-muted/30`}>
        <div className="flex items-center gap-2 p-3 border-b">
          <Button className="flex-1" size="sm" onClick={() => { startNewConversation(); setConversationsOpen(false); exitBatch(); }}>
            <Plus className="h-4 w-4 mr-2" />
            {t('page.newConversation')}
          </Button>
          <Button variant={batchMode ? 'default' : 'outline'} size="icon" className="h-8 w-8 shrink-0"
            aria-pressed={batchMode}
            title={batchMode ? t('page.batchExit') : t('page.batchEnterTitle')}
            onClick={() => (batchMode ? exitBatch() : setBatchMode(true))}>
            <ListChecks className="h-4 w-4" />
          </Button>
        </div>
        {batchMode && (
          <div className="flex items-center gap-2 px-3 py-1.5 border-b bg-muted/20 text-xs">
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input type="checkbox" className="h-3.5 w-3.5 accent-primary"
                checked={allSelected} onChange={toggleSelectAll} disabled={!conversations.length}
                aria-label={t('page.selectAllAria')} />
              {t('common:action.selectAll')}
            </label>
            <span className="text-muted-foreground">{t('page.selectedCount', { selected: selectedIds.length, total: conversations.length })}</span>
          </div>
        )}
        <ScrollArea className="flex-1 min-h-0 p-2">
          {conversationError && <p role="alert" className="p-2 text-xs text-destructive">{conversationError}<Button variant="link" size="sm" onClick={() => void loadConversations()}>{t('common:action.retry')}</Button></p>}
          {!conversationError && conversations.length === 0 && (
            <div className="text-center py-12 text-muted-foreground">{t('page.noConversations')}</div>
          )}
          {conversations.map(conv => (
            <div
              key={conv.id}
              className={`group flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer mb-1 transition-colors min-w-0 ${
                batchMode
                  ? (selectedIds.includes(conv.id) ? 'bg-primary/10 text-primary' : 'hover:bg-muted')
                  : (currentConvId === conv.id ? 'bg-primary/10 text-primary' : 'hover:bg-muted')
              }`}
              onClick={() => {
                if (batchMode) { toggleConvSelect(conv.id); return; }
                if (renamingId !== conv.id) { void switchConversation(conv.id); setConversationsOpen(false); }
              }}
            >
              {batchMode ? (
                <input type="checkbox" className="h-3.5 w-3.5 accent-primary shrink-0"
                  checked={selectedIds.includes(conv.id)} onChange={() => toggleConvSelect(conv.id)}
                  onClick={(e) => e.stopPropagation()}
                  aria-label={t('page.selectConvAria', { title: conv.title || t('page.untitledConversation') })} />
              ) : (
                <MessageSquare className="h-4 w-4 shrink-0" />
              )}
              {renamingId === conv.id ? (
                <div className="flex items-center gap-1 flex-1 min-w-0">
                  <Input
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        renameConversation(conv.id, renameValue.trim());
                        setRenamingId(null);
                      }
                      if (e.key === 'Escape') setRenamingId(null);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    className="h-7 text-sm flex-1 min-w-0"
                    autoFocus
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0 shrink-0"
                    onClick={(e) => {
                      e.stopPropagation();
                      renameConversation(conv.id, renameValue.trim());
                      setRenamingId(null);
                    }}
                  >
                    <Check className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ) : (
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate">{conv.title}</div>
                  {/* 这块会话到底读过哪份作业细则：用户排查"答得不按口径"时的第一手依据，
                      也是"技能没加载"这类争论的唯一事实源（数据来自回合 meta，不是模型自述）。
                      字段缺失 = 该功能上线前的历史会话，不渲染，绝不等于"没加载"。 */}
                  {convSkillChips(conv).length > 0 && (
                    <div className="flex flex-wrap items-center gap-1 mt-0.5" role="group"
                      aria-label={t('page.convSkillsAria')}>
                      {convSkillChips(conv).slice(0, 2).map(chip => (
                        <span key={chip.name} className="inline-flex max-w-full items-center gap-0.5 rounded bg-muted px-1 py-px text-[10px] text-muted-foreground"
                          title={t('page.convSkillsTip', {
                            name: slashOptions?.skills.find(o => o.name === chip.name)?.display_name || chip.name,
                            resident: chip.resident, attached: chip.attached, fetched: chip.fetched,
                          })}>
                          <Sparkles className="h-2.5 w-2.5 shrink-0" />
                          <span className="truncate">{slashOptions?.skills.find(o => o.name === chip.name)?.display_name || chip.name}</span>
                          <span className="shrink-0 tabular-nums opacity-70">{chip.resident + chip.attached + chip.fetched}</span>
                        </span>
                      ))}
                      {convSkillChips(conv).length > 2 && (
                        <span className="text-[10px] text-muted-foreground">
                          {t('page.convSkillsMore', { count: convSkillChips(conv).length - 2 })}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}
              {!batchMode && renamingId !== conv.id && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 relative z-20"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <MoreHorizontal className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-36">
                    <DropdownMenuItem
                      onClick={(e) => {
                        e.stopPropagation();
                        setRenamingId(conv.id);
                        setRenameValue(conv.title);
                      }}
                    >
                      <Pencil className="h-4 w-4 mr-2" />
                      {t('page.rename')}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteConversation(conv.id);
                      }}
                    >
                      <Trash className="h-4 w-4 mr-2" />
                      {t('common:action.delete')}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          ))}
        </ScrollArea>
        {batchMode && (
          <div className="flex items-center gap-2 p-2 border-t bg-background">
            <Button variant="outline" size="sm" className="h-8 text-xs" onClick={exitBatch} disabled={batchDeleting}>{t('common:action.cancel')}</Button>
            <Button variant="destructive" size="sm" className="h-8 text-xs ml-auto"
              disabled={!selectedIds.length || batchDeleting} onClick={() => setBatchConfirm(true)}>
              {batchDeleting ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Trash className="h-3.5 w-3.5 mr-1" />}
              {selectedIds.length ? t('page.deleteSelected', { count: selectedIds.length }) : t('common:action.delete')}
            </Button>
          </div>
        )}
      </div>

      {/* Main chat area */}
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 overflow-x-auto px-3 md:px-6 py-3 border-b flex-shrink-0">
          <div className="flex shrink-0 items-center gap-2">
            <Button className="md:hidden" variant="ghost" size="icon" aria-label={t('page.openConvListAria')} onClick={() => setConversationsOpen(true)}><MessageSquare className="h-4 w-4" /></Button>
            <Bot className="h-6 w-6 text-primary" />
            <h1 className="text-lg font-bold">{independent ? t('page.workspaceTitle') : t('menu:workspace.chat')}</h1>
          </div>
          <div ref={modelPickerRef} className="flex items-center gap-2" data-tour={TOUR.chatModel}>
            {/* 模型选择器:
                - AS-BOT 清单未拉取完成前**什么都不渲染**：旧写法把「还没加载」当成「确实没配
                  AS-BOT」，先走执行层兜底分支弹出选择框，清单回来又因只 1 个模型而返回 null
                  → 先弹后失（闪烁）。「只有一个模型」本来就不需要选择框，应一上来就不出现。
                - 有 AS-BOT 时: 候选 = AS-BOT 的可用模型; >1 才展示 (=1 自动选中 / 留空用执行层默认)
                - 无 AS-BOT 时: 回退执行层模型候选(原行为) */}
            {asBotsLoaded && (() => {
              const asBot = asBots.length > 0 ? asBots[0] : null;
              if (asBots.length > 0 && asBot) {
                const wm = asBot.models || [];
                if (wm.length <= 1) return null;
                const cur = selectedModelRef && wm.includes(selectedModelRef) ? selectedModelRef : wm[0];
                return (
                  <Select key={`asBot-model-${asBot.as_bot_key}`} value={cur} onValueChange={(v) => setSelectedModelRef(v)}>
                    <SelectTrigger className="w-[200px] h-8" title={t('page.asBotModelsTitle', { name: asBot.display_name || asBot.name })}>
                      <Cpu className="h-3.5 w-3.5 mr-1.5" />
                      <SelectValue placeholder={t('page.selectModel')} />
                    </SelectTrigger>
                    <SelectContent>
                      {wm.map((ref) => (<SelectItem key={ref} value={ref}>{ref}</SelectItem>))}
                    </SelectContent>
                  </Select>
                );
              }
              // 无 AS-BOT 配置: 回退执行层模型选择
              return (
                <Select
                  key={`exec-model-${selectedWorkspaceId}`}
                  value={selectedModelRef || 'default'}
                  onValueChange={(v) => setSelectedModelRef(v === 'default' ? null : v)}
                >
                  <SelectTrigger className="w-[220px] h-8" title={`Harness: ${executionLayer?.display_name || 'Qoder Harness'}`}>
                    <Cpu className="h-3.5 w-3.5 mr-1.5" />
                    <SelectValue placeholder={t('page.selectModel')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="default">{t('page.defaultModel')}</SelectItem>
                    {(executionLayer?.models || []).map((ref) => (
                      <SelectItem key={ref} value={ref}>{ref}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              );
            })()}

            {/* 站点选择器：可查站点 = 角色数据源授权的派生视图（不是第二套授权）。
                会话没任何站点化数据源时不渲染（不放选不出东西的死开关）；
                对话里点名某站（如“查中国站点”）可自然语言覆盖本轮范围。 */}
            {authorizedSites.length > 0 && (
              <Select value={selectedSiteCode} onValueChange={(v) => setSelectedSiteCode(v)}>
                <SelectTrigger
                  className="w-[150px] h-8"
                  title={t('page.siteScopeTitle')}
                >
                  <Globe className="h-3.5 w-3.5 mr-1.5" />
                  <SelectValue placeholder={t('page.selectSite')} />
                </SelectTrigger>
                <SelectContent>
                  {authorizedSites.map((s) => (
                    <SelectItem key={s.site_code} value={s.site_code}>{s.display_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {siteError && (
              <Button
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-xs text-destructive"
                title={siteError}
                onClick={() => loadAuthorizedSites(selectedWorkspaceId)}
              >
                <AlertTriangle className="h-3.5 w-3.5 mr-1" />
                {t('page.siteLoadFailed')}
              </Button>
            )}

            {/* 「清空对话」入口按产品决策隐藏（保留会话内继续追加）：
                历史回合是分析结论的溯源，误点即丢；需要重来时新建对话即可。
                store 的 clear() 与后端擦历史接口照旧保留，不在这里挂入口。 */}
            <Button
              variant={focusMode ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFocusMode(!focusMode)}
              title={focusMode ? t('page.exitFocus') : t('page.enterFocus')}
            >
              {focusMode ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        {/* 新对话起始页（参考千问）：欢迎语 + 输入卡片整体居中（空会话没有产物，
            不需要右侧留白）；一旦有消息，卡片落回底部并改为靠左，消息区接管页面
            （ScrollArea 不能包居中块，否则 viewport 的 overflow 会剪掉锚在卡片上方的候选面板）。
            列内必须自己带 gap：外层那个 gap-8 只作用于外层的子元素，列内两块（欢迎语/
            输入卡片）过去挤在一起就是这个原因。
            示例问题区已删（用户拍板）：原先写死三条与角色无关的问题，而它们的数据源
            （adh_knowledge_items 的场景/分析场景）为空。场景的实质是“一类问题的通用 workflow”，
            不该为凑首页空位把具体问题写成场景；提问引导由输入卡片底行的
            `/` 候选（场景/技能/定时任务/指令）承担。 */}
        {messages.length === 0 && !pendingQuestion && (
          <div className="flex flex-1 min-h-0 flex-col justify-center px-6">
            <div className={`${CHAT_COLUMN} flex flex-col gap-10`} data-chat-column>
            <div className="flex flex-col items-center gap-4">
              <BotAvatar size={52} />
              <p className="text-[26px] font-semibold">{t('page.welcome', { name: assistantName })}</p>
              <p className="text-[15px] text-muted-foreground">{t('page.welcomeSub')}</p>
            </div>
            {chatComposer}
            </div>
          </div>
        )}

        {/* Messages area */}
        {hasTurnUI && (
        <div className="relative flex-1 min-h-0">
        <ScrollArea ref={scrollAreaRef} className="h-full p-6">
          {/* 分页读：服务端还有更早的消息时给一个可见入口（不静默只展示最近一页） */}
          {hasMoreEarlier && (
            <div className={`${CHAT_COLUMN} mb-4 flex items-center justify-center gap-2`}>
              <button
                type="button"
                onClick={onLoadEarlierMessages}
                disabled={loadingEarlier}
                className="h-7 rounded-md border border-border/70 bg-card px-3 text-[12px] text-muted-foreground hover:bg-accent disabled:opacity-60"
              >
                {loadingEarlier ? t('common:state.loading') : t('page.loadEarlier')}
              </button>
              <span className="text-[11px] text-muted-foreground">
                {t('page.loadedCount', { loaded: messages.length, total: messageTotal || messages.length })}
              </span>
            </div>
          )}
          <div className={CHAT_COLUMN} data-chat-column style={motionCss}>
          {messages.map((msg, idx) => (
            <div
              key={idx}
              ref={el => { msgRefs.current[idx] = el; }}
              className={`flex gap-3 mb-5 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
            >
              {msg.role === 'user' ? (
                <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center bg-primary text-primary-foreground">
                  <User className="h-4 w-4" />
                </div>
              ) : (
                <BotAvatar size={36} active={loading && idx === messages.length - 1} />
              )}

              {/* 卡片宽度固定为容器的固定比例(不随内容变化)：流式思考与最终答案等宽，
                  避免流式期被内容撑到全屏、答案出来又缩窄的宽度跳变。AI 回复占满内容列
                  （头像栏之外），与底部输入卡片左右对齐；用户气泡限宽并右靠同一列边。 */}
              <div className={`min-w-0 overflow-hidden ${msg.role === 'user' ? 'max-w-[78%]' : 'flex-1'}`}>
                {msg.role === 'user' && (
                  <>
                  <div className="bg-primary text-primary-foreground rounded-2xl rounded-tr-sm px-4 py-3">
                    {msg.attachments && msg.attachments.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-2">
                        {msg.attachments.map((att, ai) => {
                          const key = `${currentConvId}:${idx}:${ai}`;
                          const url = attUrl(att, currentConvId);
                          const gone = brokenAtts[key] || !url;
                          if (gone && att.origin === 'cloud' && !att.path) {
                            // 本轮刚 @ 引用、服务端还没回带落盘路径：标"云空间引用"，
                            // 不能套下面那句"附件已随会话清理"（那是另一回事，会误导排查）
                            return (
                              <div key={key} className="flex items-center gap-1.5 bg-white/15 rounded-md px-2 py-1 text-xs opacity-90" title={att.filename}>
                                <FileText className="h-3.5 w-3.5" />
                                <span className="max-w-[140px] truncate">{att.filename}</span>
                                <span className="opacity-70">{t('page.cloudRefBadge')}</span>
                              </div>
                            );
                          }
                          if (gone) {
                            // 文件已随会话清理(或旧记录无工作区路径):显式标注,不回退裸 ID
                            return (
                              <div key={key} className="flex items-center gap-1.5 bg-white/15 rounded-md px-2 py-1 text-xs opacity-80" title={att.filename}>
                                <FileText className="h-3.5 w-3.5" />
                                <span className="max-w-[140px] truncate">{att.filename}</span>
                                <span className="opacity-70">{t('page.attCleaned')}</span>
                              </div>
                            );
                          }
                          return att.category === 'image' ? (
                            <img
                              key={key}
                              src={url}
                              alt={att.filename}
                              className="max-h-40 max-w-[200px] rounded-md object-contain bg-white/10 cursor-pointer"
                              onClick={() => window.open(url, '_blank')}
                              onError={() => setBrokenAtts(prev => ({ ...prev, [key]: true }))}
                            />
                          ) : (
                            <div
                              key={key}
                              className={`flex items-center gap-1.5 bg-white/15 rounded-md px-2 py-1 text-xs ${att.category === 'model3d' ? 'cursor-pointer hover:bg-white/25' : ''}`}
                              onClick={att.category === 'model3d' ? () => setPreview3d(att) : undefined}
                              title={att.category === 'model3d' ? t('page.preview3dTitle') : att.filename}
                            >
                              {att.category === 'model3d' ? <Box className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
                              <span className="max-w-[140px] truncate">{att.filename}</span>
                              {att.category === 'model3d' && <span className="opacity-70">{t('common:action.preview')}</span>}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {/* 本轮用 `/` 显式选中的场景/技能/指令：回放时仍能看到“这题是按哪个场景/技能答的”，
                        否则“选了场景”在服务端注入了方案却从答案里看不出来，用户只能猜生效没有 */}
                    {((msg.scenes?.length || 0) > 0 || (msg.skills?.length || 0) > 0 || !!msg.command) && (
                      <div className="flex flex-wrap gap-1.5 mb-1.5">
                        {(msg.scenes || []).map(id => (
                          <span key={id} className="inline-flex items-center gap-1 rounded bg-white/20 px-1.5 py-0.5 text-[11px]">
                            <Workflow className="h-3 w-3" />
                            {slashOptions?.scenes?.find(s => s.key === id)?.name || t('page.sceneFallback')}
                          </span>
                        ))}
                        {(msg.skills || []).map(s => (
                          <span key={s} className="inline-flex items-center gap-1 rounded bg-white/20 px-1.5 py-0.5 text-[11px]">
                            <Sparkles className="h-3 w-3" />
                            {slashOptions?.skills.find(o => o.name === s)?.display_name || s}
                          </span>
                        ))}
                        {msg.command && (
                          <span className="inline-flex items-center gap-1 rounded bg-white/20 px-1.5 py-0.5 text-[11px]">
                            <Terminal className="h-3 w-3" />
                            {slashOptions?.commands.find(c => c.key === msg.command)?.label || msg.command}
                          </span>
                        )}
                      </div>
                    )}
                    {msg.content}
                  </div>
                  {/* 提问可复制（重问同款问题、存档都用得上），也可删除这一轮。
                      删除是 destructive：走确认框且说清“模型已读过的内容不会回退”，
                      不让人以为了“删了就当没发生过”。 */}
                  {!!msg.content && (
                    <div className="mt-1 flex items-center justify-end gap-1">
                      <MessageActions markdown={msg.content} />
                      <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground"
                        disabled={loading || stopping}
                        title={loading || stopping
                          ? t('page.deleteTurnBlocked')
                          : t('page.deleteTurnTitle')}
                        onClick={() => setDeleteTurnIdx(idx)}>
                        <Trash className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  )}
                  </>
                )}
                {msg.role === 'assistant' && (
                  <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3 overflow-hidden">
                    {/* 等待模型响应:气泡还没有任何可见输出时给明确提示(而非空白) */}
                    {/* 仅对当前正在流式的最后一条生效;否则历史里被取消/清空的空 assistant 气泡会随任意新请求的 loading 再次转圈 */}
                    {loading && idx === messages.length - 1 && !msg.content && !msg.reply && !msg.intent && !msg.error && !msg.thinking && !(msg.tool_calls && msg.tool_calls.length > 0) && (
                      <WaitingIndicator />
                    )}

                    {/* 执行过程:思考与工具调用收敛进限高窗口, 执行中自动跟随、结束自动折叠。
                        运行中状态只跟随 done(loading) —— agent loop 中模型会先输出中间文本
                        再续调工具, “出现文本”不等于回合结束(旧口径用 !msg.content 兜底,
                        把运行中误渲成“已完成”、看不出在跑)。思考块转圈另用 thinkingActive:
                        开始出正文即停(done 要等后端收尾核验才发, 否则“结果已输出还在转圈”)。 */}
                    <ExecutionProcess
                      conversationId={currentConvId}
                      process={msg.process}
                      toolCalls={msg.tool_calls}
                      progressStages={msg.progressStages}
                      thinking={msg.thinking}
                      isStreaming={loading && idx === messages.length - 1 && !msg.sql}
                      thinkingActive={loading && idx === messages.length - 1 && !msg.sql && !msg.content}
                    />

                    {/* 工具已全部结束但模型还在组织最终回答 → 同样给等待提示 */}
                    {loading && idx === messages.length - 1 && !msg.content && !msg.reply && (msg.tool_calls?.length ?? 0) > 0 && msg.tool_calls!.every(t => t.result || t.error) && (
                      <WaitingIndicator size="sm" stage="composing" className="mt-1" />
                    )}

                    {/* 执行层统计摘要条(轮次/工具调用/耗时)。hasModelStats 显式转布尔：
                        全 0 时写 `a || b || c` 会把数字 0 当文本渲染到气泡上方。 */}
                    {msg.executionStats && hasModelStats(msg.executionStats) && (
                      <div className="flex items-center gap-2 mb-2 text-[10px] text-muted-foreground">
                        {msg.executionStats.num_turns != null && (
                          <span className="px-1.5 py-0.5 rounded bg-muted">{t('page.statsTurns', { count: msg.executionStats.num_turns })}</span>
                        )}
                        {msg.executionStats.tool_call_count != null && msg.executionStats.tool_call_count > 0 && (
                          <span className="px-1.5 py-0.5 rounded bg-muted">{t('page.statsToolCalls', { count: msg.executionStats.tool_call_count })}</span>
                        )}
                        {msg.executionStats.duration_ms != null && (
                          <span className="px-1.5 py-0.5 rounded bg-muted">
                            {t('page.statsDuration', { value: (msg.executionStats.duration_ms / 1000).toFixed(1) })}
                          </span>
                        )}
                      </div>
                    )}

                    {/* Inline: RAG/timings/workflow details */}
                    {(msg.timings || msg.rag || msg.workflow_info) && (
                      <InlineDetails
                        rag={msg.rag}
                        timings={msg.timings}
                        elapsedMs={msg.elapsed_ms}
                        workflowInfo={msg.workflow_info}
                      />
                    )}

                    {/* 流式 token 输出(intent 未确定前,如执行层 CLI 的流式回复,含内嵌图表/设计卡片块)。
                        回答内容按章节切成阶段卡片(卡头滑动展开/收起,逐段滑入)。
                        出错/超时中断的回合同样走这里：已输出的正文是用户读到的内容，
                        不能因为结尾报错就整段丢弃只剩错误卡（下方错误卡说明中断原因）。 */}
                    {(!msg.intent || msg.error) && msg.content && (
                      <>
                        <MarkdownWithCharts
                          text={msg.content}
                          sections
                          streaming={loading && idx === messages.length - 1}
                          conversationId={currentConvId}
                          onSaveReport={reportSaveAction(idx, msg)}
                        />
                        {msg.error && (
                          <p className="mt-1 text-xs text-muted-foreground">{t('page.interruptedNote')}</p>
                        )}
                      </>
                    )}

                    {msg.intent && ['chat', 'explain'].includes(msg.intent) && msg.reply && (
                      <MarkdownWithCharts text={msg.reply} sections conversationId={currentConvId} />
                    )}
                    {msg.reply && !msg.sql && !msg.error && msg.intent === 'query' && (
                      <MarkdownWithCharts text={msg.reply} sections conversationId={currentConvId} />
                    )}

                    {/* Agent/执行层最终回复(无 SQL 结果,如 qoder 执行层,含内嵌图表/设计卡片块)+ 赞踩打标 */}
                    {msg.reply && !msg.sql && !msg.error && msg.intent === 'agent' && (
                      <MarkdownWithCharts text={msg.reply} sections streaming={loading && idx === messages.length - 1} conversationId={currentConvId} onDrill={handleDrill} onSaveReport={reportSaveAction(idx, msg)} />
                    )}

                    {/* 前置意图漏斗本地直答：界面上只留「转深度分析」这个逃生门，
                        不打“未调用模型”类内部术语标记（归属看 handled_by，管理侧可审计）。
                        出错回合的正文由上面的中断分支渲染，此处排除避免双份。 */}
                    {msg.intent === 'intent_gate' && !msg.error && (msg.reply || msg.content) && (
                      <>
                        <MarkdownWithCharts
                          text={(msg.reply || msg.content) as string}
                          sections
                          streaming={loading && idx === messages.length - 1}
                          conversationId={currentConvId}
                        />
                        <IntentGateBadge
                          gate={msg.gate}
                          deepAnalysisDisabled={loading}
                          onDeepAnalysis={() => {
                            const q = msg.question
                              || messages.slice(0, idx).filter(m => m.role === 'user').pop()?.content
                              || '';
                            if (q) void sendMessage(q, undefined, undefined, { forceAgent: true });
                          }}
                        />
                      </>
                    )}

                    {/* Agent mode: show analysis text above chart when both reply and sql exist */}
                    {msg.reply && msg.sql && !msg.error && (
                      <div className="mb-3">
                        <MarkdownWithCharts text={msg.reply} sections conversationId={currentConvId} />
                      </div>
                    )}

                    {/* Agent ask_user 交互卡 */}
                    {msg.pendingAsk && (
                      <div className={`mt-3 p-3 rounded-xl border bg-card shadow-sm space-y-2 ${CHAT_ENTER}`}>
                        <div className="flex items-start gap-2">
                          <span className="text-sm">🤔</span>
                          <p className="text-sm">{msg.pendingAsk.question}</p>
                        </div>
                        {msg.pendingAsk.options.length > 0 && (
                          <div className="flex flex-wrap gap-2 pl-6">
                            {msg.pendingAsk.options.map((opt, i) => (
                              <Button
                                key={i}
                                variant="outline"
                                size="sm"
                                onClick={() => respondToAsk(msg.pendingAsk!.request_id, opt)}
                              >
                                {opt}
                              </Button>
                            ))}
                          </div>
                        )}
                        <div className="flex gap-2 pl-6">
                          <Input
                            placeholder={t('page.askPlaceholder')}
                            data-ask-reply
                            className="text-sm"
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && e.currentTarget.value.trim()) {
                                respondToAsk(msg.pendingAsk!.request_id, e.currentTarget.value.trim());
                                e.currentTarget.value = '';
                              }
                            }}
                          />
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              // 按稳定 data 属性定位（placeholder 已 i18n 化，不能当选择器用）
                              const input = document.querySelector('input[data-ask-reply]') as HTMLInputElement;
                              if (input?.value.trim()) {
                                respondToAsk(msg.pendingAsk!.request_id, input.value.trim());
                                input.value = '';
                              }
                            }}
                          >
                            {t('page.send')}
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => cancelAsk(msg.pendingAsk!.request_id)}
                          >
                            {t('common:action.cancel')}
                          </Button>
                        </div>
                      </div>
                    )}
                    {msg.error && (
                      <div className={`rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 shadow-sm ${CHAT_ENTER}`}>
                        <div className="flex items-center gap-2">
                          <span className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-destructive/10">
                            <X className="h-3.5 w-3.5 text-destructive" />
                          </span>
                          <span className="text-sm font-medium text-destructive">{msg.error}</span>
                        </div>
                        {/* 分类文案：过去的“一句通用错误”同时背“模型过载”与“配置坏了”，
                            用户只能反复重发。这里把“不是你的问题”与下一步说清楚。 */}
                        {msg.errorKind && ERROR_KIND_HINT_KEYS[msg.errorKind] && (
                          <p className="mt-1.5 pl-8 text-xs text-muted-foreground">
                            {t(ERROR_KIND_HINT_KEYS[msg.errorKind])}
                          </p>
                        )}
                        {msg.errorKind && (
                          <div className="mt-2 flex flex-wrap gap-2 pl-8">
                            <Button variant="outline" size="sm" disabled={loading}
                              onClick={() => (idx === messages.length - 1
                                ? retryTurn()
                                : void sendMessage(msg.question || msg.content))}>
                              <RefreshCw className="mr-1 h-3.5 w-3.5" />{t('page.retryTurn')}
                            </Button>
                            {(msg.errorKind === 'provider_busy' || msg.errorKind === 'rate_limited') && (
                              <Button variant="outline" size="sm" onClick={openModelPicker}>{t('page.switchModel')}</Button>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {/* 数据结果卡:查询结果集(图表/明细/画像/SQL 多视图) */}
                    {msg.sql && msg.intent !== 'chat' && !msg.error && (
                      <div className={`rounded-xl border bg-card shadow-sm overflow-hidden ${cardEnterOf(msg)}`}>
                        {/* 卡头:结果摘要徽标 + 视图切换 */}
                        <div className="flex items-center justify-between gap-2 px-3 py-2 border-b bg-muted/30 flex-wrap">
                          <div className="flex gap-1.5 flex-wrap items-center">
                            {msg.brief && <Badge variant="default">{msg.brief}</Badge>}
                            {msg.intent === 'correction' && <Badge variant="secondary">{t('page.correctedBadge')}</Badge>}
                            {msg.chart_type && msg.chart_type !== 'table' && (
                              <Badge variant="outline">{chartLabelOf(msg.chart_type)}</Badge>
                            )}
                            {msg.warnings?.map((w: string, i: number) => (
                              <Badge key={i} variant="outline" className="text-yellow-500">{w}</Badge>
                            ))}
                          </div>

                          {msg.result && !msg.result.error && (
                            <Tabs value={msg.viewMode || 'chart'} onValueChange={(v) => setViewMode(idx, v as any)}>
                              <TabsList>
                                <TabsTrigger value="chart"><BarChart3 className="h-4 w-4 mr-1" />{t('page.viewChart')}</TabsTrigger>
                                <TabsTrigger value="table"><Table className="h-4 w-4 mr-1" />{t('page.viewTable')}</TabsTrigger>
                                <TabsTrigger value="profile"><ClipboardList className="h-4 w-4 mr-1" />{t('page.viewProfile')}</TabsTrigger>
                                <TabsTrigger value="sql"><Code className="h-4 w-4 mr-1" />SQL</TabsTrigger>
                              </TabsList>
                            </Tabs>
                          )}
                        </div>

                        {/* 卡体:视图内容(切换视图滑入；与外壳同一份「内容就绪才播」口径) */}
                        <div key={msg.viewMode || 'chart'} className={`p-3 ${cardEnterOf(msg)}`}>
                          {msg.viewMode === 'chart' && msg.result && !msg.result.error && (
                            <ChartPicker data={msg.result} defaultType={msg.chart_type} onDrill={handleDrill} />
                          )}
                          {msg.viewMode === 'table' && msg.result && !msg.result.error && (
                            <DataGrid
                              columns={msg.result.columns || []}
                              rows={msg.result.rows || []}
                              field_formats={msg.result.field_formats}
                              height={400}
                              filename={`chatbi_${idx + 1}`}
                            />
                          )}
                          {msg.viewMode === 'profile' && msg.result && !msg.result.error && (
                            <DataProfileCard columns={msg.result.columns || []} rows={msg.result.rows || []} />
                          )}
                          {msg.viewMode === 'sql' && (
                            <SqlCard code={msg.sql || ''} maxHeight={400} />
                          )}

                          {!msg.result && !msg.error && (
                            <div className="flex items-center gap-2 py-3">
                              <Spinner size={16} />
                              <span className="text-sm text-muted-foreground">{t('page.executingQuery')}</span>
                            </div>
                          )}

                          {/* 预测结果子卡 */}
                          {msg.prediction && (
                            <StageCard
                              icon={TrendingUp}
                              title={t('page.predictionTitle')}
                              tone="green"
                              className="mt-3"
                              extra={
                                <Button variant="ghost" size="sm" disabled={msg.predicting}
                                  onClick={() => predictData(idx, msg.brief || t('page.predictBrief'))}>
                                  <RefreshCw className="h-3 w-3 mr-1" />{t('page.repredict')}
                                </Button>
                              }
                            >
                              <div className="text-sm leading-relaxed whitespace-pre-wrap">{msg.prediction}</div>
                            </StageCard>
                          )}

                          {/* 结果分析子卡 */}
                          {msg.analysis && typeof msg.analysis === 'string' && msg.analysis.trim() && (
                            <StageCard icon={Lightbulb} title={t('page.analysisTitle')} className="mt-3">
                              <div className="text-sm leading-relaxed whitespace-pre-wrap">{msg.analysis}</div>
                            </StageCard>
                          )}
                        </div>

                        {/* 卡脚:结果操作 */}
                        {msg.result && !msg.result.error && (
                          <div className="flex gap-2 px-3 py-2 border-t bg-muted/20 flex-wrap items-center">
                            <Button variant="outline" size="sm" onClick={() => {
                              const { columns, rows } = msg.result;
                              if (!columns || !rows) return;
                              const blob = new Blob([toCsv(columns, rows)], { type: 'text/csv;charset=utf-8;' });
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement('a');
                              a.href = url;
                              a.download = `chatbi_export_${new Date().toISOString().slice(0,10)}.csv`;
                              a.click();
                              URL.revokeObjectURL(url);
                            }}>
                              <Download className="h-4 w-4 mr-1" />{t('common:action.export')}
                            </Button>
                            <Button variant="outline" size="sm" disabled={savingResult || !currentConvId}
                              title={currentConvId ? t('page.saveWorkspaceTitle') : t('page.saveWorkspaceDisabled')}
                              onClick={() => saveResultToWorkspace(msg.result, idx + 1)}>
                              <Save className="h-4 w-4 mr-1" />{t('page.saveToWorkspace')}
                            </Button>
                            <Button variant="outline" size="sm" disabled={msg.analyzing}
                              onClick={() => analyzeData(idx, msg.brief || t('page.analyzeBrief'))}>
                              <Lightbulb className="h-4 w-4 mr-1" />{t('page.analyzeData')}
                            </Button>
                            <Button variant="outline" size="sm" disabled={msg.predicting}
                              onClick={() => predictData(idx, msg.brief || t('page.predictBrief'))}>
                              <TrendingUp className="h-4 w-4 mr-1" />{t('page.predictData')}
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => sendMessage(msg.question || msg.content)}>
                              <RefreshCw className="h-4 w-4 mr-1" />{t('page.rerun')}
                            </Button>
                          </div>
                        )}

                        {/* 卡底:耗时/行数/Token */}
                        <div className="flex gap-4 px-3 py-2 border-t text-xs text-muted-foreground flex-wrap">
                          {msg.elapsed_ms && <span><Clock className="h-3 w-3 inline mr-1" />{t('page.totalDuration', { value: (msg.elapsed_ms / 1000).toFixed(1) })}</span>}
                          {msg.result?.elapsed_ms && <span><Zap className="h-3 w-3 inline mr-1" />{t('page.queryMs', { value: msg.result.elapsed_ms })}</span>}
                          {msg.result?.row_count !== undefined && <span><CheckCircle className="h-3 w-3 inline mr-1" />{t('page.rowCount', { count: msg.result.row_count })}</span>}
                          {msg.tokens && <span><Code className="h-3 w-3 inline mr-1" />Token: {msg.tokens.input}+{msg.tokens.output}={msg.tokens.total}</span>}
                        </div>
                      </div>
                    )}

                    {/* 已转出的文档卡片：点进去继续编辑/复制或下载 */}
                    {msg.doc && (
                      <div className="mt-3 space-y-1.5">
                        <p className="text-xs text-muted-foreground">{t('page.docConvertedNote')}</p>
                        <DocCard
                          title={msg.doc.title}
                          createdAt={msg.doc.created_at}
                          onOpen={() => setDocEditorIdx(idx)}
                        />
                      </div>
                    )}

                    {/* 整回合的产物入口：建/改大屏任务完成后**只给一次**「预览大屏」。
                        不挂在每条 MCP 工具结果下（逐图 update 会冒出好几个，看着像每步各交付一屏）。 */}
                    <TurnScreenPreview
                      toolCalls={msg.tool_calls}
                      streaming={loading && idx === messages.length - 1}
                    />

                    {/* 消息操作行（复制 / 赞踩 / 更多）：整条回答只有这一行，不分两端也不分两行 */}
                    {renderMessageActions(idx, msg)}
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* 待自动发送的那一句：与真实用户气泡同款（右靠、主色底），下面接一条“在准备什么”的状态。
              它只是待发态的镜像，不进 messages（真发送后由 store 里那条接管，不会出现两句同文）。 */}
          {pendingQuestion && (
            <div className="flex gap-3 mb-5 flex-row-reverse">
              <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center bg-primary text-primary-foreground">
                <User className="h-4 w-4" />
              </div>
              <div className="min-w-0 max-w-[78%] overflow-hidden">
                <div className="bg-primary text-primary-foreground rounded-2xl rounded-tr-sm px-4 py-3">
                  {pendingQuestion}
                </div>
              </div>
            </div>
          )}
          {pendingQuestion && (
            <div className="flex gap-3 mb-5">
              <BotAvatar size={36} active />
              <div className="flex-1 max-w-md">
                <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3">
                  <WaitingIndicator size="sm" label={pendingAskStage} />
                </div>
              </div>
            </div>
          )}

          {/* Loading indicator (when no assistant message exists yet) */}
          {loading && !(messages.length > 0 && messages[messages.length - 1]?.role === 'assistant' && !messages[messages.length - 1]?.sql && !messages[messages.length - 1]?.intent) && (
            <div className="flex gap-3">
              <BotAvatar size={36} active />
              <div className="flex-1 max-w-md">
                <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3">
                  <WaitingIndicator size="sm" />
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
          </div>
        </ScrollArea>
        {/* 提问节点导航：挂在 ScrollArea 根层（不随内容滚动），占正文右侧那段留白 */}
        <TurnNavigator turns={questionTurns} getAnchor={getMsgAnchor} getViewport={getScrollViewport} />
        </div>
        )}

        {/* Input area：卡片已在上方定义，有消息时落回底部（新对话空态下它在页面正中）。
            px-6 与消息列的 p-6 同左边缘；列本身靠左（CHAT_COLUMN），两根列才能真的对齐。 */}
        {hasTurnUI && (
          <div className="flex-shrink-0 px-6 pb-4 pt-2 bg-background">
            <div className={CHAT_COLUMN} data-chat-column>{chatComposer}</div>
          </div>
        )}
      </div>

      {/* 会话右侧栏（产物 + 关键知识）：与主区同级、全高，不叠在正文上；
          新会话空态没有产物也没有知识可摘，不渲染（也不给一个永远空的侧栏）。 */}
      {sidePanelShown && (
        <ConversationSidePanel
          open
          onToggle={toggleSidePanel}
          workspaceId={selectedWorkspaceId}
          conversationId={currentConvId}
          messages={messages}
          onJumpToMessage={(idx: number) => { jumpToTurnAnchor(idx, getMsgAnchor, getScrollViewport); }}
          onOpenDoc={(idx: number) => setDocEditorIdx(idx)}
          onEditDraft={(idx: number) => setDraftEditorIdx(idx)}
          onArchiveDraft={(idx: number) => { void archiveReportDraft(idx); }}
          archivingIdx={savingReport}
          filesRefreshKey={filesRefreshKey}
        />
      )}

      {/* 删除单轮确认：删的是「这一问 + 它的回答」（只删问题会留下没有上文的回答），
          并把诚实边界写出来：会话记录与后续上送的 history 会变，但执行层 SDK 会话记忆
          不会回退 —— 不声明这一点就是让用户误以为“删了就当没发生过”。 */}
      <Dialog open={deleteTurnIdx !== null} onOpenChange={(open) => { if (!open) setDeleteTurnIdx(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('page.deleteTurnDialogTitle')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            <p>{t('page.deleteTurnDialogBody')}</p>
            <p className="text-xs text-muted-foreground">
              {t('page.deleteTurnDialogNote')}
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setDeleteTurnIdx(null)}>{t('common:action.cancel')}</Button>
            <Button variant="destructive" size="sm" onClick={() => void handleDeleteTurn()}>{t('page.confirmDelete')}</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 「/ 清空当前会话」确认（kind=action 指令，客户端就地执行）：不新开窗口，
          下一轮仍在同一会话以零上下文继续；会话运行中会被服务端拒（toast 说原因）。 */}
      <Dialog open={clearConfirm} onOpenChange={(open) => { if (!open) setClearConfirm(false); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('page.clearDialogTitle')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            <p>{t('page.clearDialogBody')}</p>
            <p className="text-xs text-muted-foreground">
              {t('page.clearDialogNote')}
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setClearConfirm(false)}>{t('common:action.cancel')}</Button>
            <Button variant="destructive" size="sm" onClick={() => void handleClearConversation()}>{t('page.confirmClear')}</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 批量删除确认：destructive 动作走 Dialog（不用原生 confirm），
          并把名单逐条列出来——用户要能看清“到底删哪几个”而不是只看到一个数字。 */}
      <Dialog open={batchConfirm} onOpenChange={(open) => { if (!open && !batchDeleting) setBatchConfirm(false); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('page.batchDialogTitle')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            <p>{t('page.batchDialogBody', { count: pendingDelete.length })}</p>
            <div className="max-h-40 overflow-y-auto rounded-md border p-2 text-xs space-y-1">
              {pendingDelete.map(c => (
                <div key={c.id} className="truncate" title={c.title}>· {c.title || t('page.untitledConversation')}</div>
              ))}
            </div>
            {selectedIds.length > pendingDelete.length && (
              <p className="text-xs text-muted-foreground">
                {t('page.batchStaleNote', { count: selectedIds.length - pendingDelete.length })}
              </p>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setBatchConfirm(false)} disabled={batchDeleting}>{t('common:action.cancel')}</Button>
            <Button variant="destructive" size="sm" onClick={() => void runBatchDelete()}
              disabled={batchDeleting || !pendingDelete.length}>
              {batchDeleting && <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />}
              {t('page.confirmDelete')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 3D model preview dialog */}
      <Dialog open={!!preview3d} onOpenChange={(open) => { if (!open) setPreview3d(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{preview3d?.filename}</DialogTitle>
          </DialogHeader>
          {preview3d && <Model3DViewer url={attUrl(preview3d, currentConvId)} filename={preview3d.filename} />}
        </DialogContent>
      </Dialog>

      {/* 报告草稿编辑面板（两段式第一段）：只在目标消息仍带草稿时挂载，
          会话切换/消息被删后自然收口，不指向一份已不存在的草稿 */}
      {draftEditorIdx !== null && (messages[draftEditorIdx] as any)?.reportDraft && (
        <ReportDraftEditor
          draft={(messages[draftEditorIdx] as any).reportDraft}
          busy={savingReport === draftEditorIdx}
          onSave={patch => saveMessageReportDraft(draftEditorIdx, patch)}
          onArchive={() => { setDraftEditorIdx(null); void archiveReportDraft(draftEditorIdx); }}
          onClose={() => setDraftEditorIdx(null)}
        />
      )}

      {/* 文档编辑面板（转文档编辑）：只在目标消息仍带文档时挂载，
          会话切换/消息被清后自然收口，不留一个指向不存在文档的空面板 */}
      {docEditorIdx !== null && messages[docEditorIdx]?.doc && (
        <DocEditorPanel
          doc={messages[docEditorIdx].doc}
          sourceMarkdown={answerMarkdownOf(messages[docEditorIdx])}
          onSave={patch => saveMessageDoc(docEditorIdx, patch)}
          onClose={() => setDocEditorIdx(null)}
        />
      )}
    </div>
  );
}
