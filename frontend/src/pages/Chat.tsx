import { useState, useRef, useEffect } from 'react';
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import {
  Send, Zap, Trash2, Download, Bot, User, Lightbulb,
  CheckCircle, BarChart3, Table, Code, Clock,
  Plus, MessageSquare, Trash, TrendingUp, X, RefreshCw,
  MoreHorizontal, Pencil, Check, ThumbsUp, ThumbsDown, Cpu,
  Loader2, Maximize2, Minimize2, Paperclip, FileText, Box, FolderOpen, Save, ClipboardList,
  FileBarChart, Globe, AlertTriangle,
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
import { useChatStore } from '../stores/chatStore';
import type { AttachmentInfo, PendingAttachment } from '../stores/chatStore';
import { ATTACH_MAX_FILES, validateLocalAttachment, filesFromClipboard, hasModelStats } from '../stores/chatStore';
import ChartPicker from '../components/ChartPicker';
import { CHART_TYPES } from '../components/DashboardChart';
import MarkdownWithCharts from '../components/MarkdownWithCharts';
import ExecutionProcess from '../components/ExecutionProcess';
import InlineDetails from '../components/InlineDetails';
import Model3DViewer from '../components/chat/Model3DViewer';
import SessionFilesPanel from '../components/chat/SessionFilesPanel';
import StageCard from '../components/chat/StageCard';
import BotAvatar from '../components/chat/BotAvatar';
import WaitingIndicator from '../components/chat/WaitingIndicator';
import { IntentGateBadge } from '../components/chat/IntentGateBadge';
import DataGrid from '../components/DataGrid';
import DataProfileCard from '../components/DataProfileCard';
import SqlCard from '../components/SqlCard';
import { toCsv } from '../lib/dataGrid';
import client from '../api/client';
import { saveConversationAsReport } from '../api/report';

const CATEGORY_LABELS: Record<string, string> = {
  image: '图片', table: '表格', document: '文档', model3d: '3D 模型',
};

// 附件 URL:待发/刚发送的本轮附件用本地 blob 预览;
// 历史消息附件按工作区相对路径走 session-file 伺服(带 token,供 <img>/three.js 加载)
function attUrl(att: AttachmentInfo, conversationId?: number | null): string {
  if (att.blobUrl) return att.blobUrl;
  if (!att.path || !conversationId) return '';
  const token = localStorage.getItem('token');
  const base = `/api/chat/session-file?conversation_id=${conversationId}&path=${encodeURIComponent(att.path)}`;
  return token ? `${base}&token=${encodeURIComponent(token)}` : base;
}

// 图表类型标签与看板共用同一套 CHART_TYPES(前置为历史消息的旧类型别名,仅用于旧消息回放)
const chartTypeLabels: Record<string, string> = {
  timeseries_table: '时间序列表格', timeseries_percent: '时间序列百分比变化',
  timeseries_pivot: '时间序列周期透视', chord: '弦图', column: '柱状图', table: '表格',
  // 地图热力(chat 图表卡专属,字模库地图瓦片 + 热力层)及其 LLM 常用别名
  map_heat: '地图热力', map: '地图热力', heatmap_map: '地图热力', geo_heatmap: '地图热力', map_heatmap: '地图热力',
  ...Object.fromEntries(CHART_TYPES.map(t => [t.value, t.label])),
};

export default function Chat() {
  const { workspaceId: urlWorkspaceId } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();
  const independent = useLocation().pathname.startsWith('/ask/');
  const [conversationsOpen, setConversationsOpen] = useState(false);
  const conversationError = useChatStore(s => s.conversationError);
  const [input, setInput] = useState('');
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [focusMode, setFocusMode] = useState(false);
  const [pendingAtts, setPendingAtts] = useState<PendingAttachment[]>([]);
  // 历史附件文件已随会话清理(session-file 404/无路径)的标记,键含会话与位置
  const [brokenAtts, setBrokenAtts] = useState<Record<string, boolean>>({});
  const [preview3d, setPreview3d] = useState<AttachmentInfo | null>(null);
  const [filesOpen, setFilesOpen] = useState(false);
  const [recommendedQuestions, setRecommendedQuestions] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    conversations, currentConvId, messages, loading,
    selectedDsId,
    selectedWorkspaceId, setSelectedWorkspaceId, loadWorkspaceConfig,
    executionLayer, selectedModelRef, loadExecutionLayer, setSelectedModelRef,
    asBots, loadAsBots, asBotsLoaded,
    loadConversations, loadDatasources, loadLLMModels, loadSystemConfig,
    loadAuthorizedSites, authorizedSites, selectedSiteCode, setSelectedSiteCode, siteError,
    setSelectedDsId, setSelectedModelId,
    startNewConversation, switchConversation, deleteConversation, renameConversation,
    sendMessage, cancelMessage, respondToAsk, cancelAsk, updateMessageFeedback, setViewMode, analyzeData, predictData, clear,
    stopping,
  } = useChatStore();
  // 加载快速路径示例（Chat 空白时展示）
  useEffect(() => {
    client.get('/admin/knowledge/random', {
      params: { knowledge_type: 'quick_path', limit: 3, workspace_id: 0 },
    })
      .then(({ data }) => {
        const qs = (data.items || []).map((it: any) => it.title).filter(Boolean);
        if (qs.length > 0) setRecommendedQuestions(qs);
      })
      .catch(() => { /* 静默:无快速路径时使用默认 */ });
  }, []);

  // 首页「试试这样问」跳转带入的问题:挂载时消费一次并预填输入框
  useEffect(() => {
    const q = sessionStorage.getItem('home_question');
    if (q) {
      sessionStorage.removeItem('home_question');
      setInput(q);
      inputRef.current?.focus();
    }
  }, []);

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

  const DISSATISFACTION_REASONS: { key: string; label: string }[] = [
    { key: 'missing_metric', label: '缺这个指标' },
    { key: 'missing_dimension', label: '缺这个维度' },
    { key: 'wrong_answer', label: '结果不对' },
    { key: 'not_understood', label: '没理解我的问题' },
    { key: 'data_incomplete', label: '数据不全' },
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
        for (const it of mItems) if (it?.name) out.push({ name: String(it.name), kind: '指标' });
      }
      if (Array.isArray(dItems)) {
        for (const it of dItems) if (it?.name) out.push({ name: String(it.name), kind: '维度' });
      }
      setRefOptions(out);
    } catch {
      // 候选拉不到时不伪造成可选：显式告知，并保留自由文字渠道
      toast.error('业务指标/维度清单未能加载，反馈仍可提交，但“期望哪个业务概念”无法选择');
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
    const tablesWarning = msg.warnings?.find((w: string) => w.startsWith('涉及表:'));
    const tablesUsed = tablesWarning ? tablesWarning.replace('涉及表: ', '') : '';
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
        ? '已标记满意，感谢反馈'
        : '已记录。该负反馈会进入每日归纳作业，产出待开发指标/维度或待修绑定的候选');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '反馈提交失败');
    }
  };

  // Escape key to exit focus mode
  useEffect(() => {
    if (!focusMode) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFocusMode(false);
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

  const handleSend = () => {
    if ((!input.trim() && pendingAtts.length === 0) || loading) return;

    const attachments = pendingAtts.length > 0 ? pendingAtts : undefined;
    const questionText = input.trim() || '请分析我上传的附件';

    sendMessage(questionText, undefined, attachments);

    setInput('');
    setPendingAtts([]);
  };

  // 赞踩打标渲染 — 内置结果卡与 Agent 输出共用(外层需 flex flex-wrap 容器)
  const renderFeedback = (idx: number, msg: any) => {
    const fb = msg.feedback || feedbackMap[idx];
    return (
      <>
        {/* 只有真产出过数据/分析的回合才给报表入口（服务端 reportable 判定）；
            纯问答/系统操作确认不是报表，不给按钮（否则报表中心会堆满无意义条目） */}
        {msg.reportable === true && (
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground"
            disabled={savingReport !== null || loading}
            title="把本轮分析结论存到报表中心（仅本人可见，可在线查看与编辑）"
            onClick={() => saveAsReport(idx, msg)}>
            {savingReport === idx
              ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
              : <FileBarChart className="h-3.5 w-3.5 mr-1" />}
            存为报表
          </Button>
        )}
        {!fb ? (
          <div className="flex items-center gap-1 ml-auto">
            <span className="text-xs text-muted-foreground mr-1">结果准确?</span>
            <Button variant="ghost" size="sm" className="h-7 px-2"
              onClick={() => handleFeedback(idx, true)}>
              <ThumbsUp className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="sm" className="h-7 px-2"
              onClick={() => {
                setShowExpectedInput(prev => ({ ...prev, [idx]: !prev[idx] }));
                if (!showExpectedInput[idx]) void loadRefOptions();
              }}>
              <ThumbsDown className="h-3.5 w-3.5" />
            </Button>
          </div>
        ) : (
          <span className="text-xs text-muted-foreground ml-auto">
            {fb === 'up' ? '👍 已反馈' : '👎 已反馈'}
          </span>
        )}
        {showExpectedInput[idx] && !fb && (
          <div className="mt-2 w-full space-y-2 border border-border/60 rounded-md p-2 bg-muted/20">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-muted-foreground shrink-0">哪里不对?</span>
              {DISSATISFACTION_REASONS.map(r => (
                <button key={r.key} type="button"
                  onClick={() => setFeedbackReason(prev => ({ ...prev, [idx]: r.key }))}
                  className={`h-6 px-2 rounded-md text-xs border transition-colors ${
                    feedbackReason[idx] === r.key
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border text-muted-foreground hover:bg-muted'}`}
                >{r.label}</button>
              ))}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-muted-foreground shrink-0">期望的业务指标/维度</span>
              <Select
                value={feedbackRef[idx] || 'none'}
                onValueChange={(v) => setFeedbackRef(prev => ({ ...prev, [idx]: v === 'none' ? '' : v }))}
                disabled={refLoading && !refOptions.length}
              >
                <SelectTrigger className="w-[240px] h-7 text-xs">
                  <SelectValue placeholder={refLoading ? '清单加载中…' : '从字典清单选择（可不选）'} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">暂不指定</SelectItem>
                  {refOptions.map(o => (
                    <SelectItem key={`${o.kind}:${o.name}`} value={o.name}>
                      {o.name}
                      <span className="ml-1 text-[10px] text-muted-foreground">({o.kind})</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {refOptions.length === 0 && !refLoading && (
                <span className="text-[10px] text-amber-600">
                  字典清单为空——仍可提交，但无法定位到具体业务概念
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Input
                value={expectedNoteInput[idx] || ''}
                onChange={(e) => setExpectedNoteInput(prev => ({ ...prev, [idx]: e.target.value }))}
                placeholder="补充说明（可选，只写业务描述，不需要也不应写表名/字段名）"
                className="h-7 text-xs flex-1"
              />
              <Button size="sm" variant="default" className="h-7 text-xs"
                disabled={!feedbackReason[idx] && !feedbackRef[idx] && !expectedNoteInput[idx]}
                onClick={() => handleFeedback(idx, false, {
                  reason: feedbackReason[idx], ref: feedbackRef[idx], note: expectedNoteInput[idx],
                })}>
                提交
              </Button>
              <Button size="sm" variant="ghost" className="h-7 text-xs"
                onClick={() => { handleFeedback(idx, false); setShowExpectedInput(prev => ({ ...prev, [idx]: false })); }}>
                跳过
              </Button>
            </div>
          </div>
        )}
      </>
    );
  };

  // 本地校验 + blob 预览,暂存待发附件;文件随消息以 multipart 上传,服务端落盘到会话工作区
  const addPendingFiles = (files: File[]): number => {
    const next = [...pendingAtts];
    for (const f of files) {
      if (next.length >= ATTACH_MAX_FILES) {
        toast.error(`单次最多携带 ${ATTACH_MAX_FILES} 个附件`);
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
    if (added > 0) toast.success(`已放入 ${added} 个附件，将随消息一起发送`);
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
        filename: `查询结果_${n}.xlsx`,
        columns: result.columns,
        rows: result.rows || [],
      });
      toast.success(`已保存到会话工作区 ${data.filename}(文件面板可查看/归档)`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败,请稍后重试');
    } finally {
      setSavingResult(false);
    }
  };

  // 存为报表: 本轮已展示的分析结论进报表中心(内容型报表, 仅本人可见、可在线查看与编辑)。
  // 正文由服务端从已落库的会话读, 前端不传内容(不信任 body 里的正文)。
  const [savingReport, setSavingReport] = useState<number | null>(null);
  const saveAsReport = async (idx: number, msg: any) => {
    if (!currentConvId) {
      toast.error('会话尚未落库，无法存为报表；请等本轮回答完成后再试');
      return;
    }
    setSavingReport(idx);
    try {
      const { title } = await saveConversationAsReport({
        conversation_id: currentConvId,
        message_index: idx,
        message_uuid: msg?.message_uuid || '',
      });
      toast.success(`已存入报表中心：${title}`, {
        // 独立问答页(/ask/)没有工作站侧栏，只提示结果，不给跳转
        action: independent || !selectedWorkspaceId ? undefined
          : { label: '去查看', onClick: () => navigate(`/ws/${selectedWorkspaceId}/reports`) },
      });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '存为报表失败，请重试');
    } finally {
      setSavingReport(null);
    }
  };

  if (urlWorkspaceId && Number(urlWorkspaceId) !== selectedWorkspaceId) return <div role="status" className="p-6">正在切换工作空间…</div>;
  return (
    <div className={focusMode ? 'fixed inset-0 z-50 bg-background flex overflow-hidden' : 'relative flex h-full min-w-0 overflow-hidden'}>
      {conversationsOpen && !focusMode && <button className="absolute inset-0 z-20 bg-black/30 md:hidden" aria-label="关闭会话列表" onClick={() => setConversationsOpen(false)} />}
      {/* Left sidebar: conversation list */}
      <div aria-label="会话列表" className={focusMode ? 'hidden' : `${conversationsOpen ? 'absolute inset-y-0 left-0 z-30 flex bg-background' : 'hidden'} md:relative md:flex md:z-10 w-[240px] flex-shrink-0 flex-col border-r bg-muted/30`}>
        <div className="p-3 border-b">
          <Button className="w-full" size="sm" onClick={() => { startNewConversation(); setConversationsOpen(false); }}>
            <Plus className="h-4 w-4 mr-2" />
            新建对话
          </Button>
        </div>
        <ScrollArea className="flex-1 min-h-0 p-2">
          {conversationError && <p role="alert" className="p-2 text-xs text-destructive">{conversationError}<Button variant="link" size="sm" onClick={() => void loadConversations()}>重试</Button></p>}
          {!conversationError && conversations.length === 0 && (
            <div className="text-center py-12 text-muted-foreground">暂无对话</div>
          )}
          {conversations.map(conv => (
            <div
              key={conv.id}
              className={`group flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer mb-1 transition-colors min-w-0 ${
                currentConvId === conv.id
                  ? 'bg-primary/10 text-primary'
                  : 'hover:bg-muted'
              }`}
              onClick={() => {
                if (renamingId !== conv.id) { void switchConversation(conv.id); setConversationsOpen(false); }
              }}
            >
              <MessageSquare className="h-4 w-4 shrink-0" />
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
                <span className="text-sm flex-1 truncate min-w-0">{conv.title}</span>
              )}
              {renamingId !== conv.id && (
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
                      重命名
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteConversation(conv.id);
                      }}
                    >
                      <Trash className="h-4 w-4 mr-2" />
                      删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          ))}
        </ScrollArea>
      </div>

      {/* Main chat area */}
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 overflow-x-auto px-3 md:px-6 py-3 border-b flex-shrink-0">
          <div className="flex shrink-0 items-center gap-2">
            {!focusMode && <Button className="md:hidden" variant="ghost" size="icon" aria-label="打开会话列表" onClick={() => setConversationsOpen(true)}><MessageSquare className="h-4 w-4" /></Button>}
            <Bot className="h-6 w-6 text-primary" />
            <h1 className="text-lg font-bold">{independent ? '工作空间' : '智能问数'}</h1>
          </div>
          <div className="flex items-center gap-2">
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
                    <SelectTrigger className="w-[200px] h-8" title={`AS-BOT「${asBot.display_name || asBot.name}」可用模型`}>
                      <Cpu className="h-3.5 w-3.5 mr-1.5" />
                      <SelectValue placeholder="选择模型" />
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
                  <SelectTrigger className="w-[220px] h-8" title={`执行层: ${executionLayer?.display_name || 'Qoder 执行层'}`}>
                    <Cpu className="h-3.5 w-3.5 mr-1.5" />
                    <SelectValue placeholder="选择模型" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="default">默认模型(执行层配置)</SelectItem>
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
                  title="查询站点范围（对话中直接说「查中国站点」可覆盖本轮）"
                >
                  <Globe className="h-3.5 w-3.5 mr-1.5" />
                  <SelectValue placeholder="选择站点" />
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
                站点加载失败，点击重试
              </Button>
            )}

            <Button variant="outline" size="sm" onClick={clear} disabled={messages.length === 0}>
              <Trash2 className="h-4 w-4 mr-2" />
              清空对话
            </Button>
            <Button
              variant={focusMode ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFocusMode(!focusMode)}
              title={focusMode ? '退出专注模式 (Esc)' : '专注模式'}
            >
              {focusMode ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        {/* Messages area */}
        <ScrollArea ref={scrollAreaRef} className="flex-1 min-h-0 p-6">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <Zap className="h-14 w-14 text-primary mb-5" />
              <p className="text-lg">输入你的数据查询问题</p>
              <p className="text-sm mt-2">AI 将自动生成 SQL、执行查询并可视化结果</p>
              <div className="flex gap-3 mt-8 flex-wrap justify-center">
                {(recommendedQuestions.length > 0 ? recommendedQuestions : ['查看最近7天的案例数量', '各区域设备使用率统计', '本月新增用户趋势']).map((q) => (
                  <Badge
                    key={q}
                    variant="outline"
                    className="cursor-pointer px-4 py-1.5 text-sm"
                    onClick={() => { if (!loading) sendMessage(q); }}
                  >
                    {q}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, idx) => (
            <div key={idx} className={`flex gap-3 mb-5 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
              {msg.role === 'user' ? (
                <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center bg-primary text-primary-foreground">
                  <User className="h-4 w-4" />
                </div>
              ) : (
                <BotAvatar size={36} active={loading && idx === messages.length - 1} />
              )}

              {/* 卡片宽度固定为容器的固定比例(不随内容变化)：流式思考与最终答案等宽，
                  避免流式期被内容撑到全屏、答案出来又缩窄的宽度跳变；随窗口等比例缩放。 */}
              <div className={`min-w-0 overflow-hidden ${msg.role === 'user' ? 'max-w-[70%]' : 'w-[70%]'}`}>
                {msg.role === 'user' && (
                  <div className="bg-primary text-primary-foreground rounded-2xl rounded-tr-sm px-4 py-3">
                    {msg.attachments && msg.attachments.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-2">
                        {msg.attachments.map((att, ai) => {
                          const key = `${currentConvId}:${idx}:${ai}`;
                          const url = attUrl(att, currentConvId);
                          const gone = brokenAtts[key] || !url;
                          if (gone) {
                            // 文件已随会话清理(或旧记录无工作区路径):显式标注,不回退裸 ID
                            return (
                              <div key={key} className="flex items-center gap-1.5 bg-white/15 rounded-md px-2 py-1 text-xs opacity-80" title={att.filename}>
                                <FileText className="h-3.5 w-3.5" />
                                <span className="max-w-[140px] truncate">{att.filename}</span>
                                <span className="opacity-70">附件已随会话清理</span>
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
                              title={att.category === 'model3d' ? '点击预览 3D 模型' : att.filename}
                            >
                              {att.category === 'model3d' ? <Box className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
                              <span className="max-w-[140px] truncate">{att.filename}</span>
                              {att.category === 'model3d' && <span className="opacity-70">预览</span>}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {msg.content}
                  </div>
                )}
                {msg.role === 'assistant' && (
                  <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3 overflow-hidden">
                    {/* 等待模型响应:气泡还没有任何可见输出时给明确提示(而非空白) */}
                    {/* 仅对当前正在流式的最后一条生效;否则历史里被取消/清空的空 assistant 气泡会随任意新请求的 loading 再次转圈 */}
                    {loading && idx === messages.length - 1 && !msg.content && !msg.reply && !msg.intent && !msg.error && !msg.thinking && !(msg.tool_calls && msg.tool_calls.length > 0) && (
                      <WaitingIndicator />
                    )}

                    {/* 执行过程:思考与工具调用收敛进限高窗口, 执行中自动跟随、结束自动折叠 */}
                    <ExecutionProcess
                      conversationId={currentConvId}
                      process={msg.process}
                      toolCalls={msg.tool_calls}
                      progressStages={msg.progressStages}
                      thinking={msg.thinking}
                      isStreaming={loading && idx === messages.length - 1 && !msg.sql}
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
                          <span className="px-1.5 py-0.5 rounded bg-muted">{msg.executionStats.num_turns} 轮推理</span>
                        )}
                        {msg.executionStats.tool_call_count != null && msg.executionStats.tool_call_count > 0 && (
                          <span className="px-1.5 py-0.5 rounded bg-muted">{msg.executionStats.tool_call_count} 次工具调用</span>
                        )}
                        {msg.executionStats.duration_ms != null && (
                          <span className="px-1.5 py-0.5 rounded bg-muted">
                            耗时 {(msg.executionStats.duration_ms / 1000).toFixed(1)}s
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
                        回答内容按章节切成阶段卡片(卡头滑动展开/收起,逐段滑入) */}
                    {!msg.intent && msg.content && (
                      <MarkdownWithCharts
                        text={msg.content}
                        sections
                        streaming={loading && idx === messages.length - 1}
                        conversationId={currentConvId}
                        onDesignContinue={text => { void sendMessage(text); }}
                      />
                    )}

                    {msg.intent && ['chat', 'explain'].includes(msg.intent) && msg.reply && (
                      <MarkdownWithCharts text={msg.reply} sections conversationId={currentConvId} onDesignContinue={text => { void sendMessage(text); }} />
                    )}
                    {msg.reply && !msg.sql && !msg.error && msg.intent === 'query' && (
                      <MarkdownWithCharts text={msg.reply} sections conversationId={currentConvId} onDesignContinue={text => { void sendMessage(text); }} />
                    )}

                    {/* Agent/执行层最终回复(无 SQL 结果,如 qoder 执行层,含内嵌图表/设计卡片块)+ 赞踩打标 */}
                    {msg.reply && !msg.sql && !msg.error && msg.intent === 'agent' && (
                      <>
                        <MarkdownWithCharts text={msg.reply} sections streaming={loading && idx === messages.length - 1} conversationId={currentConvId} onDrill={handleDrill} onDesignContinue={text => { void sendMessage(text); }} />
                        <div className="flex gap-2 mt-1 flex-wrap items-center">{renderFeedback(idx, msg)}</div>
                      </>
                    )}

                    {/* 前置意图漏斗本地直答：界面上只留「转深度分析」这个逃生门，
                        不打“未调用模型”类内部术语标记（归属看 handled_by，管理侧可审计）。 */}
                    {msg.intent === 'intent_gate' && (msg.reply || msg.content) && (
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
                        <MarkdownWithCharts text={msg.reply} sections conversationId={currentConvId} onDesignContinue={text => { void sendMessage(text); }} />
                      </div>
                    )}

                    {/* Agent ask_user 交互卡 */}
                    {msg.pendingAsk && (
                      <div className="mt-3 p-3 rounded-xl border bg-card shadow-sm space-y-2 animate-stage-in">
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
                            placeholder="输入自定义回复..."
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
                              const input = document.querySelector('input[placeholder="输入自定义回复..."]') as HTMLInputElement;
                              if (input?.value.trim()) {
                                respondToAsk(msg.pendingAsk!.request_id, input.value.trim());
                                input.value = '';
                              }
                            }}
                          >
                            发送
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => cancelAsk(msg.pendingAsk!.request_id)}
                          >
                            取消
                          </Button>
                        </div>
                      </div>
                    )}
                    {msg.error && (
                      <div className="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 shadow-sm animate-stage-in">
                        <div className="flex items-center gap-2">
                          <span className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-destructive/10">
                            <X className="h-3.5 w-3.5 text-destructive" />
                          </span>
                          <span className="text-sm font-medium text-destructive">{msg.error}</span>
                        </div>
                      </div>
                    )}

                    {/* 数据结果卡:查询结果集(图表/明细/画像/SQL 多视图) */}
                    {msg.sql && msg.intent !== 'chat' && !msg.error && (
                      <div className="rounded-xl border bg-card shadow-sm overflow-hidden animate-stage-in">
                        {/* 卡头:结果摘要徽标 + 视图切换 */}
                        <div className="flex items-center justify-between gap-2 px-3 py-2 border-b bg-muted/30 flex-wrap">
                          <div className="flex gap-1.5 flex-wrap items-center">
                            {msg.brief && <Badge variant="default">{msg.brief}</Badge>}
                            {msg.intent === 'correction' && <Badge variant="secondary">已修正</Badge>}
                            {msg.chart_type && msg.chart_type !== 'table' && (
                              <Badge variant="outline">{chartTypeLabels[msg.chart_type] || msg.chart_type}</Badge>
                            )}
                            {msg.warnings?.map((w: string, i: number) => (
                              <Badge key={i} variant="outline" className="text-yellow-500">{w}</Badge>
                            ))}
                          </div>

                          {msg.result && !msg.result.error && (
                            <Tabs value={msg.viewMode || 'chart'} onValueChange={(v) => setViewMode(idx, v as any)}>
                              <TabsList>
                                <TabsTrigger value="chart"><BarChart3 className="h-4 w-4 mr-1" />图表</TabsTrigger>
                                <TabsTrigger value="table"><Table className="h-4 w-4 mr-1" />明细</TabsTrigger>
                                <TabsTrigger value="profile"><ClipboardList className="h-4 w-4 mr-1" />画像</TabsTrigger>
                                <TabsTrigger value="sql"><Code className="h-4 w-4 mr-1" />SQL</TabsTrigger>
                              </TabsList>
                            </Tabs>
                          )}
                        </div>

                        {/* 卡体:视图内容(切换视图滑入) */}
                        <div key={msg.viewMode || 'chart'} className="p-3 animate-stage-in">
                          {msg.viewMode === 'chart' && msg.result && !msg.result.error && (
                            <ChartPicker data={msg.result} defaultType={msg.chart_type} onDrill={handleDrill} />
                          )}
                          {msg.viewMode === 'table' && msg.result && !msg.result.error && (
                            <DataGrid
                              columns={msg.result.columns || []}
                              rows={msg.result.rows || []}
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
                              <span className="text-sm text-muted-foreground">正在执行查询...</span>
                            </div>
                          )}

                          {/* 预测结果子卡 */}
                          {msg.prediction && (
                            <StageCard
                              icon={TrendingUp}
                              title="数据预测"
                              tone="green"
                              className="mt-3"
                              extra={
                                <Button variant="ghost" size="sm" disabled={msg.predicting}
                                  onClick={() => predictData(idx, msg.brief || '预测趋势')}>
                                  <RefreshCw className="h-3 w-3 mr-1" />重新预测
                                </Button>
                              }
                            >
                              <div className="text-sm leading-relaxed whitespace-pre-wrap">{msg.prediction}</div>
                            </StageCard>
                          )}

                          {/* 结果分析子卡 */}
                          {msg.analysis && typeof msg.analysis === 'string' && msg.analysis.trim() && (
                            <StageCard icon={Lightbulb} title="结果分析" className="mt-3">
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
                              <Download className="h-4 w-4 mr-1" />导出
                            </Button>
                            <Button variant="outline" size="sm" disabled={savingResult || !currentConvId}
                              title={currentConvId ? '把完整结果保存为会话工作区 xlsx 文件(随会话清理,可归档收藏)' : '发送消息后可保存'}
                              onClick={() => saveResultToWorkspace(msg.result, idx + 1)}>
                              <Save className="h-4 w-4 mr-1" />存为工作区
                            </Button>
                            <Button variant="outline" size="sm" disabled={msg.analyzing}
                              onClick={() => analyzeData(idx, msg.brief || '分析这些数据')}>
                              <Lightbulb className="h-4 w-4 mr-1" />数据分析
                            </Button>
                            <Button variant="outline" size="sm" disabled={msg.predicting}
                              onClick={() => predictData(idx, msg.brief || '预测趋势')}>
                              <TrendingUp className="h-4 w-4 mr-1" />数据预测
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => sendMessage(msg.question || msg.content)}>
                              <RefreshCw className="h-4 w-4 mr-1" />重新执行
                            </Button>
                            {renderFeedback(idx, msg)}
                          </div>
                        )}

                        {/* 卡底:耗时/行数/Token */}
                        <div className="flex gap-4 px-3 py-2 border-t text-xs text-muted-foreground flex-wrap">
                          {msg.elapsed_ms && <span><Clock className="h-3 w-3 inline mr-1" />总耗时 {(msg.elapsed_ms / 1000).toFixed(1)}s</span>}
                          {msg.result?.elapsed_ms && <span><Zap className="h-3 w-3 inline mr-1" />查询 {msg.result.elapsed_ms}ms</span>}
                          {msg.result?.row_count !== undefined && <span><CheckCircle className="h-3 w-3 inline mr-1" />{msg.result.row_count} 行</span>}
                          {msg.tokens && <span><Code className="h-3 w-3 inline mr-1" />Token: {msg.tokens.input}+{msg.tokens.output}={msg.tokens.total}</span>}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}

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
        </ScrollArea>

        {/* Input area */}
        <div className="flex-shrink-0 p-4 border-t bg-background">
          <div className="relative">
            {/* Pending attachments preview bar */}
            {pendingAtts.length > 0 && (
              <div className="flex items-center gap-2 mb-2 flex-wrap">
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
                    <Badge variant="secondary" className="text-[10px] px-1 py-0">{CATEGORY_LABELS[att.category] || att.category}</Badge>
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

            <div className="flex gap-2">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                accept=".png,.jpg,.jpeg,.gif,.webp,.csv,.xlsx,.pdf,.md,.txt,.docx,.obj,.glb,.stl"
                onChange={handleFileSelect}
              />
              <Button variant="outline" size="icon" className="flex-shrink-0" disabled={loading}
                title="上传附件(图片/表格/文档/3D 模型,支持 Ctrl+V 粘贴截图;随消息发送并存入会话工作区)"
                onClick={() => fileInputRef.current?.click()}>
                <Paperclip className="h-4 w-4" />
              </Button>
              <Button variant="outline" size="icon" className="flex-shrink-0" disabled={!currentConvId}
                title={currentConvId ? '会话工作区文件(预览/下载/归档)' : '发送消息后可查看会话文件'}
                onClick={() => setFilesOpen(true)}>
                <FolderOpen className="h-4 w-4" />
              </Button>
              <Input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={handlePaste}
                placeholder={stopping ? '上一轮正在停止，请稍候…' : '输入你的数据查询问题...'}
                className="flex-1"
                disabled={stopping}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
              />
              <Button onClick={handleSend} disabled={loading || stopping || (!input.trim() && pendingAtts.length === 0)}>
                {loading || stopping ? <Spinner className="h-4 w-4 mr-2" /> : <Send className="h-4 w-4 mr-2" />}
                发送
              </Button>
              {(loading || stopping) && (
                <Button variant="destructive" onClick={cancelMessage} disabled={stopping}>
                  {stopping ? <Spinner className="h-4 w-4 mr-2" /> : <X className="h-4 w-4 mr-2" />}
                  {stopping ? '正在停止…' : '停止'}
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 3D model preview dialog */}
      <Dialog open={!!preview3d} onOpenChange={(open) => { if (!open) setPreview3d(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{preview3d?.filename}</DialogTitle>
          </DialogHeader>
          {preview3d && <Model3DViewer url={attUrl(preview3d, currentConvId)} filename={preview3d.filename} />}
        </DialogContent>
      </Dialog>

      {/* 会话工作区文件面板 */}
      <SessionFilesPanel
        open={filesOpen}
        onClose={() => setFilesOpen(false)}
        workspaceId={selectedWorkspaceId}
        conversationId={currentConvId}
      />
    </div>
  );
}
