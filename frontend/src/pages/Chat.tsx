import { useState, useRef, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import {
  Send, Zap, Trash2, Download, Bot, User, Lightbulb,
  CheckCircle, BarChart3, Table, Code, Clock,
  Plus, MessageSquare, Trash, TrendingUp, X, RefreshCw,
  MoreHorizontal, Pencil, Check, ThumbsUp, ThumbsDown, Cpu,
  Workflow, Loader2, Maximize2, Minimize2, Paperclip, FileText, Box,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useChatStore } from '../stores/chatStore';
import type { AttachmentInfo } from '../stores/chatStore';
import ChartPicker from '../components/ChartPicker';
import { CHART_TYPES } from '../components/DashboardChart';
import MarkdownWithCharts from '../components/MarkdownWithCharts';
import ProcessTimeline from '../components/ProcessTimeline';
import InlineDetails from '../components/InlineDetails';
import Model3DViewer from '../components/chat/Model3DViewer';
import client from '../api/client';

const CATEGORY_LABELS: Record<string, string> = {
  image: '图片', table: '表格', document: '文档', model3d: '3D 模型',
};

// Attachment file URLs require auth; append token for <img>/three.js loaders
function attUrl(att: AttachmentInfo): string {
  if (!att.url) return '';
  const token = localStorage.getItem('token');
  return token ? `${att.url}?token=${encodeURIComponent(token)}` : att.url;
}

// 图表类型标签与看板共用同一套 CHART_TYPES(前置为历史消息的旧类型别名,仅用于旧消息回放)
const chartTypeLabels: Record<string, string> = {
  timeseries_table: '时间序列表格', timeseries_percent: '时间序列百分比变化',
  timeseries_pivot: '时间序列周期透视', chord: '弦图', column: '柱状图', table: '表格',
  ...Object.fromEntries(CHART_TYPES.map(t => [t.value, t.label])),
};

export default function Chat() {
  const { workspaceId: urlWorkspaceId } = useParams<{ workspaceId: string }>();
  const [input, setInput] = useState('');
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [atMenuOpen, setAtMenuOpen] = useState(false);
  const [atMenuIndex, setAtMenuIndex] = useState(0);
  const [atFilter, setAtFilter] = useState('');
  const [focusMode, setFocusMode] = useState(false);
  const [pendingAtts, setPendingAtts] = useState<AttachmentInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [preview3d, setPreview3d] = useState<AttachmentInfo | null>(null);
  const [recommendedQuestions, setRecommendedQuestions] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    conversations, currentConvId, messages, loading,
    selectedDsId,
    pipelineMode: chatPipelineMode,
    selectedWorkspaceId, setSelectedWorkspaceId, loadWorkspaceConfig,
    executionLayer, selectedModelRef, loadExecutionLayer, setSelectedModelRef,
    wakers, selectedWakerKey, loadWakers, setSelectedWakerKey,
    loadConversations, loadDatasources, loadLLMModels, loadSystemConfig,
    setSelectedDsId, setSelectedModelId, setPipelineMode,
    createConversation, switchConversation, deleteConversation, renameConversation,
    sendMessage, cancelMessage, respondToAsk, cancelAsk, updateMessageFeedback, setViewMode, analyzeData, predictData, clear,
    uploadAttachment,
    mcpServers, loadMcpTools,
  } = useChatStore();
  // 加载推荐问题（Chat 空白时展示）
  useEffect(() => {
    client.get('/admin/knowledge/random', {
      params: { knowledge_type: 'recommend_question', limit: 3, workspace_id: 0 },
    })
      .then(({ data }) => {
        const qs = (data.items || []).map((it: any) => it.title).filter(Boolean);
        if (qs.length > 0) setRecommendedQuestions(qs);
      })
      .catch(() => { /* 静默:无推荐问题时使用默认 */ });
  }, []);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const thinkingRef = useRef<HTMLDivElement>(null);

  // 深度模式 = 平台内置 Agent(支持 MCP 工具 @ 提及);Agent 模式 = 外部执行层(默认 qoder)
  const isAgentMode = chatPipelineMode === 'deep';

  // Flatten all MCP tools from all servers (only in deep/built-in agent mode)
  const allMcpTools = isAgentMode ? mcpServers.flatMap((s: any) =>
    (s.tools || []).map((t: any) => ({
      name: `${s.server_name}__${t.name}`,
      displayName: t.name,
      serverName: s.server_name,
      description: t.description || '',
    }))
  ) : [];

  // Filtered tools for @ mention
  const filteredTools = atFilter
    ? allMcpTools.filter((t: any) =>
        t.name.toLowerCase().includes(atFilter.toLowerCase()) ||
        t.displayName.toLowerCase().includes(atFilter.toLowerCase()) ||
        t.description.toLowerCase().includes(atFilter.toLowerCase())
      )
    : allMcpTools;

  // Parse @tool mentions from input
  const parseMcpTools = (text: string): string[] => {
    const matches = text.match(/@[\w_]+__[\w_]+/g) || [];
    return matches.map(m => m.substring(1)); // remove @
  };

  // Handle input change with @ detection
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setInput(value);

    // Detect @ trigger
    const cursorPos = e.target.selectionStart || value.length;
    const textBeforeCursor = value.substring(0, cursorPos);
    const atIndex = textBeforeCursor.lastIndexOf('@');

    if (atIndex >= 0) {
      const afterAt = textBeforeCursor.substring(atIndex + 1);
      // Only show menu if @ is at start or preceded by space
      if (atIndex === 0 || textBeforeCursor[atIndex - 1] === ' ') {
        if (!afterAt.includes(' ') && afterAt.length < 30) {
          setAtFilter(afterAt);
          setAtMenuOpen(true);
          setAtMenuIndex(0);
          return;
        }
      }
    }
    setAtMenuOpen(false);
  };

  // Select a tool from @ menu
  const selectAtTool = (toolName: string) => {
    const cursorPos = inputRef.current?.selectionStart || input.length;
    const textBeforeCursor = input.substring(0, cursorPos);
    const atIndex = textBeforeCursor.lastIndexOf('@');

    if (atIndex >= 0) {
      const before = input.substring(0, atIndex);
      const after = input.substring(cursorPos);
      const newInput = `${before}@${toolName} ${after}`;
      setInput(newInput);
      setAtMenuOpen(false);

      // Focus and set cursor after the inserted tool
      setTimeout(() => {
        if (inputRef.current) {
          const newPos = atIndex + toolName.length + 2; // +2 for @ and space
          inputRef.current.focus();
          inputRef.current.setSelectionRange(newPos, newPos);
        }
      }, 0);
    }
  };

  // Handle keyboard in @ menu
  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (atMenuOpen && filteredTools.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setAtMenuIndex(prev => Math.min(prev + 1, filteredTools.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setAtMenuIndex(prev => Math.max(prev - 1, 0));
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        selectAtTool(filteredTools[atMenuIndex].name);
        return;
      }
      if (e.key === 'Escape') {
        setAtMenuOpen(false);
        return;
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };
  const prevMsgCountRef = useRef(0);
  const [feedbackMap, setFeedbackMap] = useState<Record<number, 'up' | 'down'>>({});
  const [expectedTableInput, setExpectedTableInput] = useState<Record<number, string>>({});
  const [showExpectedInput, setShowExpectedInput] = useState<Record<number, boolean>>({});

  const handleFeedback = async (idx: number, satisfied: boolean, note?: string) => {
    const msg = messages[idx];
    if (!msg) return;
    // Extract tables_used from warnings
    const tablesWarning = msg.warnings?.find((w: string) => w.startsWith('涉及表:'));
    const tablesUsed = tablesWarning ? tablesWarning.replace('涉及表: ', '') : '';
    const isNl2sql = !!msg.sql;
    try {
      await client.post('/chat/feedback', {
        question: msg.question || msg.content || '',
        tables_used: tablesUsed,
        datasource_id: selectedDsId,
        satisfied,
        // NL2SQL 结果卡收集"期望表名";Agent/对话输出收集自由原因(reason)
        expected_table: isNl2sql ? (note || '') : '',
        reason: isNl2sql ? '' : (note || ''),
        // 可观测关联键:有 trace 时反馈能在 Trace 列表/详情回显
        trace_id: msg.trace_id || '',
        message_uuid: msg.message_uuid || '',
        conversation_id: currentConvId || 0,
        workspace_id: selectedWorkspaceId || 0,
      });
      // Persist feedback in message
      updateMessageFeedback(idx, satisfied ? 'up' : 'down', isNl2sql ? note : undefined);
      setFeedbackMap(prev => ({ ...prev, [idx]: satisfied ? 'up' : 'down' }));
      setShowExpectedInput(prev => ({ ...prev, [idx]: false }));
      toast.success(satisfied ? '已标记满意，感谢反馈' : '已标记不满意，感谢反馈');
    } catch {
      toast.error('反馈失败');
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
        loadConversations();
        loadDatasources();
        loadLLMModels();
        loadSystemConfig();
        loadWorkspaceConfig(wsId);
        loadExecutionLayer(wsId);
        loadWakers(wsId);
      }
    }
  }, [urlWorkspaceId]);

  // 执行层与模型候选、Waker 清单:跟随工作空间变化刷新(不依赖 URL 参数,
  // 覆盖同工作空间重新进入 Chat 等场景,避免候选停留在空/旧层)
  useEffect(() => {
    if (selectedWorkspaceId) {
      loadExecutionLayer(selectedWorkspaceId);
      loadWakers(selectedWorkspaceId);
    }
  }, [selectedWorkspaceId, loadExecutionLayer, loadWakers]);

  // Load MCP tools only when in deep (built-in agent) mode
  useEffect(() => {
    if (isAgentMode) {
      loadMcpTools();
    } else {
      setAtMenuOpen(false);
    }
  }, [isAgentMode]);

  // Auto-scroll: new messages or streaming thinking content
  const lastThinking = messages.length > 0 ? messages[messages.length - 1]?.thinking || '' : '';
  useEffect(() => {
    const count = messages.length;
    if (count > prevMsgCountRef.current || loading) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
    prevMsgCountRef.current = count;
  }, [messages.length, loading]);

  useEffect(() => {
    if (lastThinking && thinkingRef.current) {
      thinkingRef.current.scrollTop = thinkingRef.current.scrollHeight;
    }
  }, [lastThinking]);

  const handleSend = () => {
    if ((!input.trim() && pendingAtts.length === 0) || loading) return;

    // Parse @tool mentions
    const mcpTools = parseMcpTools(input);
    // Strip @tool mentions from the display message
    const cleanMessage = input.replace(/@[\w_]+__[\w_]+\s*/g, '').trim();
    const attachments = pendingAtts.length > 0 ? pendingAtts : undefined;
    const questionText = cleanMessage || input.trim() || '请分析我上传的附件';

    if (mcpTools.length > 0) {
      // Pass with MCP tools
      sendMessage(questionText, mcpTools, attachments);
    } else {
      sendMessage(questionText, undefined, attachments);
    }

    setInput('');
    setPendingAtts([]);
    setAtMenuOpen(false);
  };

  // 推荐追问/快捷动作：以按钮形式直接发起一轮对话（不污染输入框）
  const runQuestion = (text: string) => {
    if (loading || !text.trim()) return;
    const mcpTools = parseMcpTools(text);
    const clean = text.replace(/@[\w_]+__[\w_]+\s*/g, '').trim();
    sendMessage(mcpTools.length ? clean : text, mcpTools.length ? mcpTools : undefined);
  };

  // 赞踩打标渲染 — 内置结果卡与 Agent 输出共用(外层需 flex flex-wrap 容器)
  const renderFeedback = (idx: number, msg: any) => {
    const fb = msg.feedback || feedbackMap[idx];
    const isNl2sql = !!msg.sql;
    return (
      <>
        <div className="flex-1" />
        {!fb ? (
          <div className="flex items-center gap-1 ml-auto">
            <span className="text-xs text-muted-foreground mr-1">结果准确?</span>
            <Button variant="ghost" size="sm" className="h-7 px-2"
              onClick={() => handleFeedback(idx, true)}>
              <ThumbsUp className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="sm" className="h-7 px-2"
              onClick={() => setShowExpectedInput(prev => ({ ...prev, [idx]: !prev[idx] }))}>
              <ThumbsDown className="h-3.5 w-3.5" />
            </Button>
          </div>
        ) : (
          <span className="text-xs text-muted-foreground ml-auto">
            {fb === 'up' ? '👍 已反馈' : '👎 已反馈'}
          </span>
        )}
        {showExpectedInput[idx] && !fb && (
          <div className="flex items-center gap-2 mt-2 w-full">
            <Input
              value={expectedTableInput[idx] || ''}
              onChange={(e) => setExpectedTableInput(prev => ({ ...prev, [idx]: e.target.value }))}
              placeholder={isNl2sql ? '期望的表名（可选，如 t_user_customer）' : '补充原因（可选，帮助我们改进）'}
              className="h-7 text-xs flex-1"
            />
            <Button size="sm" variant="default" className="h-7 text-xs"
              onClick={() => handleFeedback(idx, false, expectedTableInput[idx])}>
              提交
            </Button>
            <Button size="sm" variant="ghost" className="h-7 text-xs"
              onClick={() => { handleFeedback(idx, false); setShowExpectedInput(prev => ({ ...prev, [idx]: false })); }}>
              跳过
            </Button>
          </div>
        )}
      </>
    );
  };

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (files.length === 0) return;
    setUploading(true);
    try {
      const atts = await uploadAttachment(files);
      setPendingAtts(prev => [...prev, ...atts]);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || '附件上传失败');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className={focusMode ? 'fixed inset-0 z-50 bg-background flex overflow-hidden' : 'flex h-full overflow-hidden'}>
      {/* Left sidebar: conversation list */}
      <div className={focusMode ? 'hidden' : 'hidden md:flex w-[240px] flex-shrink-0 flex-col border-r bg-muted/30 relative z-10'}>
        <div className="p-3 border-b">
          <Button className="w-full" size="sm" onClick={() => createConversation()}>
            <Plus className="h-4 w-4 mr-2" />
            新建对话
          </Button>
        </div>
        <ScrollArea className="flex-1 min-h-0 p-2">
          {conversations.length === 0 && (
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
                if (renamingId !== conv.id) switchConversation(conv.id);
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
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3 border-b flex-shrink-0">
          <div className="flex items-center gap-2">
            <Bot className="h-6 w-6 text-primary" />
            <h1 className="text-lg font-bold">Chat 数据分析</h1>
          </div>
          <div className="flex items-center gap-2">
            {/* Mode Selector — 全面转向 Qoder 执行层:隐藏 quick/内置 deep,仅保留 Qoder */}
            {(() => {
              const modes: string[] = ['agent'];
              const modeLabel: Record<string, { icon: any; text: string }> = {
                quick: { icon: <Zap className="h-3 w-3 inline mr-1" />, text: '快速' },
                deep: { icon: <Workflow className="h-3 w-3 inline mr-1" />, text: '深度' },
                agent: { icon: <Bot className="h-3 w-3 inline mr-1" />, text: 'Qoder' },
              };
              const currentMode = modes.includes(chatPipelineMode || '') ? chatPipelineMode! : (modes[0] || 'agent');
              if (modes.length <= 1) {
                const m = modes[0] || 'agent';
                return (
                  <div className="h-8 px-3 flex items-center text-xs border rounded-md bg-background">
                    {modeLabel[m]?.icon}{modeLabel[m]?.text || m}
                  </div>
                );
              }
              return (
                <Select
                  key={`mode-${selectedWorkspaceId}`}
                  value={currentMode}
                  onValueChange={(v) => {
                    setPipelineMode(v as 'quick' | 'deep' | 'agent');
                    const msgs: Record<string, string> = {
                      quick: '快速模式：简化 RAG 检索，响应快，适合简单查询',
                      deep: '深度模式：平台内置 Agent，LLM 自主工具调用（SQL/分析/MCP）',
                      agent: 'Qoder 执行层：角色化智能体，自主工具调用与可视化',
                    };
                    toast.info(msgs[v] || '');
                  }}
                >
                  <SelectTrigger className="w-[120px] h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {modes.map(m => (
                      <SelectItem key={m} value={m}>
                        {modeLabel[m]?.icon}{modeLabel[m]?.text || m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              );
            })()}

            {/* Waker 选择器 — 仅当 (工作空间+角色) 可选 Waker > 1 时展示;只有一个时隐藏 */}
            {wakers.length > 1 && (
              <Select
                key={`waker-${selectedWorkspaceId}`}
                value={selectedWakerKey || wakers[0]?.waker_key}
                onValueChange={(v) => setSelectedWakerKey(v)}
              >
                <SelectTrigger className="w-[160px] h-8" title="选择 Waker(角色化智能体)">
                  <Bot className="h-3.5 w-3.5 mr-1.5" />
                  <SelectValue placeholder="选择 Waker" />
                </SelectTrigger>
                <SelectContent>
                  {wakers.map((w) => (
                    <SelectItem key={w.waker_key} value={w.waker_key}>{w.display_name || w.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {/* 模型选择器:
                - 有 Waker 时: 候选 = 选中 Waker 的可用模型; >1 才展示 (=1 自动选中 / 留空用执行层默认)
                - 无 Waker 时: 回退执行层模型候选(原行为) */}
            {(() => {
              const waker = wakers.find(w => w.waker_key === (selectedWakerKey || wakers[0]?.waker_key)) || null;
              if (wakers.length > 0 && waker) {
                const wm = waker.models || [];
                if (wm.length <= 1) return null;
                const cur = selectedModelRef && wm.includes(selectedModelRef) ? selectedModelRef : wm[0];
                return (
                  <Select key={`waker-model-${waker.waker_key}`} value={cur} onValueChange={(v) => setSelectedModelRef(v)}>
                    <SelectTrigger className="w-[200px] h-8" title={`Waker「${waker.display_name || waker.name}」可用模型`}>
                      <Cpu className="h-3.5 w-3.5 mr-1.5" />
                      <SelectValue placeholder="选择模型" />
                    </SelectTrigger>
                    <SelectContent>
                      {wm.map((ref) => (<SelectItem key={ref} value={ref}>{ref}</SelectItem>))}
                    </SelectContent>
                  </Select>
                );
              }
              // 无 Waker 配置: 回退执行层模型选择
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
        <ScrollArea className="flex-1 min-h-0 p-6">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <Zap className="h-14 w-14 text-primary mb-5" />
              <p className="text-lg">输入你的数据查询问题</p>
              <p className="text-sm mt-2">AI 将自动生成 SQL、执行查询并可视化结果</p>
              <div className="flex gap-3 mt-8 flex-wrap justify-center">
                {(recommendedQuestions.length > 0 ? recommendedQuestions : ['查看最近7天的病例数量', '各区域设备使用率统计', '本月新增用户趋势']).map((q) => (
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
              <div className={`w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center ${
                msg.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-primary/10 text-primary'
              }`}>
                {msg.role === 'user' ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
              </div>

              <div className={`max-w-[96%] min-w-0 overflow-hidden ${msg.role === 'user' ? 'max-w-[70%]' : ''}`}>
                {msg.role === 'user' && (
                  <div className="bg-primary text-primary-foreground rounded-2xl rounded-tr-sm px-4 py-3">
                    {msg.attachments && msg.attachments.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-2">
                        {msg.attachments.map(att => (
                          att.category === 'image' ? (
                            <img
                              key={att.id}
                              src={attUrl(att)}
                              alt={att.filename}
                              className="max-h-40 max-w-[200px] rounded-md object-contain bg-white/10 cursor-pointer"
                              onClick={() => window.open(attUrl(att), '_blank')}
                            />
                          ) : (
                            <div
                              key={att.id}
                              className={`flex items-center gap-1.5 bg-white/15 rounded-md px-2 py-1 text-xs ${att.category === 'model3d' ? 'cursor-pointer hover:bg-white/25' : ''}`}
                              onClick={att.category === 'model3d' ? () => setPreview3d(att) : undefined}
                              title={att.category === 'model3d' ? '点击预览 3D 模型' : att.filename}
                            >
                              {att.category === 'model3d' ? <Box className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
                              <span className="max-w-[140px] truncate">{att.filename}</span>
                              {att.category === 'model3d' && <span className="opacity-70">预览</span>}
                            </div>
                          )
                        ))}
                      </div>
                    )}
                    {msg.content}
                  </div>
                )}
                {msg.role === 'assistant' && (
                  <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3 overflow-hidden">
                    {/* 等待模型响应:气泡还没有任何可见输出时给明确提示(而非空白) */}
                    {loading && !msg.content && !msg.reply && !msg.intent && !msg.error && !msg.thinking && !(msg.tool_calls && msg.tool_calls.length > 0) && (
                      <div className="flex items-center gap-2">
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                        <span className="text-sm text-muted-foreground">等待模型响应...</span>
                      </div>
                    )}

                    {/* 执行过程:思考与工具调用按实际发生顺序穿插展示 */}
                    <ProcessTimeline
                      process={msg.process}
                      toolCalls={msg.tool_calls}
                      progressStages={msg.progressStages}
                      thinking={msg.thinking}
                      isStreaming={loading && idx === messages.length - 1 && !msg.sql}
                    />

                    {/* 工具已全部结束但模型还在组织最终回答 → 同样给等待提示 */}
                    {loading && idx === messages.length - 1 && !msg.content && !msg.reply && (msg.tool_calls?.length ?? 0) > 0 && msg.tool_calls!.every(t => t.result || t.error) && (
                      <div className="flex items-center gap-2 mt-1">
                        <Loader2 className="h-3 w-3 animate-spin text-primary" />
                        <span className="text-xs text-muted-foreground">等待模型响应...</span>
                      </div>
                    )}

                    {/* 执行层统计摘要条(轮次/工具调用/耗时) */}
                    {msg.executionStats && (msg.executionStats.num_turns || msg.executionStats.tool_call_count || msg.executionStats.duration_ms) && (
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

                    {/* 流式 token 输出(intent 未确定前,如执行层 CLI 的流式回复,含内嵌图表块) */}
                    {!msg.intent && msg.content && <MarkdownWithCharts text={msg.content} />}

                    {msg.intent && ['chat', 'explain'].includes(msg.intent) && msg.reply && (
                      <div className="leading-relaxed prose prose-sm max-w-none">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.reply}</ReactMarkdown>
                      </div>
                    )}
                    {msg.reply && !msg.sql && !msg.error && msg.intent === 'query' && (
                      <div>
                        <div className="leading-relaxed prose prose-sm max-w-none">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.reply}</ReactMarkdown>
                        </div>
                      </div>
                    )}

                    {/* Agent/执行层最终回复(无 SQL 结果,如 qoder 执行层,含内嵌图表块)+ 赞踩打标 */}
                    {msg.reply && !msg.sql && !msg.error && msg.intent === 'agent' && (
                      <>
                        <MarkdownWithCharts text={msg.reply} />
                        <div className="flex gap-2 mt-1 flex-wrap items-center">{renderFeedback(idx, msg)}</div>
                      </>
                    )}

                    {/* Agent mode: show analysis text above chart when both reply and sql exist */}
                    {msg.reply && msg.sql && !msg.error && (
                      <div className="mb-3 leading-relaxed prose prose-sm max-w-none">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.reply}</ReactMarkdown>
                      </div>
                    )}

                    {/* Agent ask_user interactive card */}
                    {msg.pendingAsk && (
                      <div className="mt-3 p-3 border rounded-lg bg-muted/50 space-y-2">
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
                      <div>
                        <Badge variant="destructive" className="mb-2">{msg.error}</Badge>
                      </div>
                    )}

                    {msg.sql && msg.intent !== 'chat' && !msg.error && (
                      <div>
                        <div className="flex gap-1.5 mb-3 flex-wrap items-center">
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
                          <div className="flex items-center justify-between mb-3">
                            <Tabs value={msg.viewMode || 'chart'} onValueChange={(v) => setViewMode(idx, v as any)}>
                              <TabsList>
                                <TabsTrigger value="chart"><BarChart3 className="h-4 w-4 mr-1" />图表</TabsTrigger>
                                <TabsTrigger value="table"><Table className="h-4 w-4 mr-1" />明细</TabsTrigger>
                                <TabsTrigger value="sql"><Code className="h-4 w-4 mr-1" />SQL</TabsTrigger>
                              </TabsList>
                            </Tabs>
                          </div>
                        )}

                        {msg.viewMode === 'chart' && msg.result && !msg.result.error && (
                          <ChartPicker data={msg.result} defaultType={msg.chart_type} />
                        )}
                        {msg.viewMode === 'table' && msg.result && !msg.result.error && (
                          <div className="overflow-auto max-h-[400px]">
                            <table className="w-full text-xs">
                              <thead>
                                <tr className="border-b bg-muted/50">
                                  {msg.result.columns?.map((c: string) => (
                                    <th key={c} className="h-8 px-3 text-left align-middle font-medium text-muted-foreground">{c}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {msg.result.rows?.slice(0, 50).map((row: any, i: number) => (
                                  <tr key={i} className="border-b hover:bg-muted/50">
                                    {msg.result.columns?.map((c: string) => (
                                      <td key={c} className="px-3 py-1.5">{String(row[c] ?? '')}</td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                        {msg.viewMode === 'sql' && (
                          <pre className="p-4 bg-muted text-foreground rounded-lg border text-xs leading-relaxed overflow-auto max-h-[400px] font-mono">
                            {msg.sql}
                          </pre>
                        )}

                        {!msg.result && !msg.error && (
                          <div className="flex items-center gap-2 py-3">
                            <Spinner size={16} />
                            <span className="text-sm text-muted-foreground">正在执行查询...</span>
                          </div>
                        )}

                        {msg.result && !msg.result.error && (
                          <div className="flex gap-2 mt-2 flex-wrap">
                            <Button variant="outline" size="sm" onClick={() => {
                              const { columns, rows } = msg.result;
                              if (!columns || !rows) return;
                              // Build CSV content
                              const csvHeader = columns.join(',');
                              const csvRows = rows.map((row: any) =>
                                columns.map((col: string) => {
                                  const val = row[col];
                                  if (val === null || val === undefined) return '';
                                  const str = String(val);
                                  // Escape quotes and wrap in quotes if contains comma/newline/quote
                                  if (str.includes(',') || str.includes('\n') || str.includes('"')) {
                                    return `"${str.replace(/"/g, '""')}"`;
                                  }
                                  return str;
                                }).join(',')
                              );
                              const csvContent = '﻿' + csvHeader + '\n' + csvRows.join('\n');
                              const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement('a');
                              a.href = url;
                              a.download = `chatbi_export_${new Date().toISOString().slice(0,10)}.csv`;
                              a.click();
                              URL.revokeObjectURL(url);
                            }}>
                              <Download className="h-4 w-4 mr-1" />导出
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

                        {/* Prediction result */}
                        {msg.prediction && (
                          <div className="mt-3 p-3 bg-background rounded-lg border">
                            <div className="flex items-center gap-2 mb-2">
                              <TrendingUp className="h-4 w-4 text-green-500" />
                              <span className="font-medium text-sm">数据预测</span>
                              <div className="flex-1" />
                              <Button variant="ghost" size="sm" disabled={msg.predicting}
                                onClick={() => predictData(idx, msg.brief || '预测趋势')}>
                                <RefreshCw className="h-3 w-3 mr-1" />重新预测
                              </Button>
                            </div>
                            <div className="text-sm leading-relaxed whitespace-pre-wrap">{msg.prediction}</div>
                          </div>
                        )}

                        {/* Analysis result - shown prominently before chart */}
                        {msg.analysis && typeof msg.analysis === 'string' && msg.analysis.trim() && (
                          <div className="mb-3 p-3 bg-primary/5 rounded-lg border border-primary/20">
                            <div className="flex items-center gap-2 mb-1">
                              <Lightbulb className="h-4 w-4 text-primary" />
                              <span className="font-medium text-sm">结果分析</span>
                            </div>
                            <div className="text-sm leading-relaxed whitespace-pre-wrap">{msg.analysis}</div>
                          </div>
                        )}
                        <div className="flex gap-4 mt-3 pt-2 border-t text-xs text-muted-foreground flex-wrap">
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

          {/* 推荐追问 — 末条为助手回复且未在加载时展示，强化交互（下钻/拆解/看板/质量） */}
          {!loading && messages.length > 0 && messages[messages.length - 1]?.role === 'assistant' && (
            <div className="pl-12 flex flex-wrap gap-2 items-center pt-1">
              <span className="text-xs text-muted-foreground"><Lightbulb className="h-3 w-3 inline mr-1" />继续探索：</span>
              {[
                { label: '下钻分析', icon: <BarChart3 className="h-3 w-3 mr-1" />, text: '请对上一步结果做下钻分析：按关键维度逐层拆解，找出贡献最大与异常的细分，并给出可执行结论。' },
                { label: '场景拆解', icon: <Workflow className="h-3 w-3 mr-1" />, text: '请把该问题拆解成 3-5 个可执行的子分析场景，分别列出需要验证的指标、维度与预期结论。' },
                { label: '生成看板', icon: <TrendingUp className="h-3 w-3 mr-1" />, text: '请基于以上结论，规划一个可视化看板：给出关键指标卡、图表类型与布局建议。' },
                { label: '数据质量检查', icon: <Table className="h-3 w-3 mr-1" />, text: '请检查上述结论涉及数据的质量问题（缺失/重复/异常值/口径一致性），并给出修正建议。' },
              ].map((f) => (
                <Button key={f.label} variant="outline" size="sm" className="h-7 text-xs" onClick={() => runQuestion(f.text)}>
                  {f.icon}{f.label}
                </Button>
              ))}
            </div>
          )}

          {/* Loading indicator (when no assistant message exists yet) */}
          {loading && !(messages.length > 0 && messages[messages.length - 1]?.role === 'assistant' && !messages[messages.length - 1]?.sql && !messages[messages.length - 1]?.intent) && (
            <div className="flex gap-3">
              <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center bg-primary/10 text-primary">
                <Bot className="h-4 w-4" />
              </div>
              <div className="flex-1 max-w-md">
                <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3">
                  <div className="flex items-center gap-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                    <span className="text-xs text-muted-foreground">等待模型响应...</span>
                  </div>
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </ScrollArea>

        {/* Input area */}
        <div className="flex-shrink-0 p-4 border-t bg-background">
          <div className="relative">
            {/* @ mention dropdown - only in agent mode */}
            {isAgentMode && atMenuOpen && filteredTools.length > 0 && (
              <div className="absolute bottom-full left-0 right-0 mb-1 bg-popover border rounded-lg shadow-lg max-h-48 overflow-y-auto z-50">
                <div className="px-2 py-1.5 text-xs text-muted-foreground border-b">
                  选择 MCP 工具 (↑↓ 选择，Enter 确认)
                </div>
                {filteredTools.map((tool: any, i: number) => (
                  <div
                    key={tool.name}
                    className={`px-3 py-2 cursor-pointer text-sm ${
                      i === atMenuIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'
                    }`}
                    onClick={() => selectAtTool(tool.name)}
                  >
                    <span className="font-mono font-medium text-primary">{tool.displayName}</span>
                    <span className="ml-2 text-xs text-muted-foreground">({tool.serverName})</span>
                    <span className="ml-2 text-xs text-muted-foreground truncate">{tool.description}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Pending attachments preview bar */}
            {pendingAtts.length > 0 && (
              <div className="flex items-center gap-2 mb-2 flex-wrap">
                {pendingAtts.map(att => (
                  <div key={att.id} className="flex items-center gap-1.5 border rounded-lg px-2 py-1 bg-muted/50">
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
                      onClick={() => setPendingAtts(prev => prev.filter(a => a.id !== att.id))}
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
              <Button variant="outline" size="icon" className="flex-shrink-0" disabled={loading || uploading}
                title="上传附件(图片/表格/文档/3D 模型)"
                onClick={() => fileInputRef.current?.click()}>
                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
              </Button>
              <Input
                ref={inputRef}
                value={input}
                onChange={isAgentMode ? handleInputChange : (e) => setInput(e.target.value)}
                placeholder={isAgentMode ? "输入问题... 输入 @ 调用 MCP 工具" : "输入你的数据查询问题..."}
                className="flex-1"
                onKeyDown={isAgentMode ? handleInputKeyDown : (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
              />
              <Button onClick={handleSend} disabled={loading || (!input.trim() && pendingAtts.length === 0)}>
                {loading ? <Spinner className="h-4 w-4 mr-2" /> : <Send className="h-4 w-4 mr-2" />}
                发送
              </Button>
              {loading && (
                <Button variant="destructive" onClick={cancelMessage}>
                  <X className="h-4 w-4 mr-2" />
                  停止
                </Button>
              )}
            </div>

            {/* Show selected @tools as chips - only in agent mode */}
            {isAgentMode && parseMcpTools(input).length > 0 && (
              <div className="flex items-center gap-1 mt-1.5 flex-wrap">
                <span className="text-xs text-muted-foreground">调用:</span>
                {parseMcpTools(input).map(toolName => (
                  <Badge key={toolName} variant="secondary" className="text-xs py-0">
                    @{toolName}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 3D model preview dialog */}
      <Dialog open={!!preview3d} onOpenChange={(open) => { if (!open) setPreview3d(null); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{preview3d?.filename}</DialogTitle>
          </DialogHeader>
          {preview3d && <Model3DViewer url={attUrl(preview3d)} filename={preview3d.filename} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
