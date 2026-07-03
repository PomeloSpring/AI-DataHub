import { useState, useRef, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import {
  Send, Zap, Trash2, Download, Bot, User, Lightbulb, Database,
  FileSearch, CheckCircle, BarChart3, Table, Code, Clock, Info,
  Plus, MessageSquare, Trash, TrendingUp, X, RefreshCw,
  MoreHorizontal, Pencil, Check, ThumbsUp, ThumbsDown, Cpu,
  Workflow, Loader2, Copy, Maximize2, Minimize2, Folder,
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
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useChatStore } from '../stores/chatStore';
import ChartPicker from '../components/ChartPicker';
import client from '../api/client';

const chartTypeLabels: Record<string, string> = {
  table: '表格', column: '柱状图', bar: '柱状图', line: '折线图', pie: '饼图',
  funnel: '漏斗图', sankey: '桑葚图', chord: '弦图',
  calendar_heatmap: '日历热力图', big_number_trend: '趋势大数字图',
  boxplot: '箱线图', bubble: '气泡图',
  timeseries_table: '时间序列表格', timeseries_area: '时间序列面积图',
  timeseries_bar: '时间序列柱状图', timeseries_line: '时间序列折线图',
  timeseries_percent: '时间序列百分比变化', timeseries_pivot: '时间序列周期透视',
  tree: '树图', treemap: '矩形树图', waterfall: '瀑布图',
  text_display: '文本展示', table_value: '表值图',
};

export default function Chat() {
  const { workspaceId: urlWorkspaceId } = useParams<{ workspaceId: string }>();
  const [input, setInput] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMsg, setDrawerMsg] = useState<any>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [atMenuOpen, setAtMenuOpen] = useState(false);
  const [atMenuIndex, setAtMenuIndex] = useState(0);
  const [atFilter, setAtFilter] = useState('');
  const [focusMode, setFocusMode] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const {
    conversations, currentConvId, messages, loading, loadingStep,
    selectedDsId, datasources, selectedModelId, llmModels,
    pipelineMode: chatPipelineMode,
    retrievalStrategy, setRetrievalStrategy,
    selectedWorkspaceId, setSelectedWorkspaceId, workspaceConfig, loadWorkspaceConfig,
    loadConversations, loadDatasources, loadLLMModels, loadWorkflows, loadSystemConfig,
    setSelectedDsId, setSelectedModelId, setPipelineMode,
    createConversation, switchConversation, deleteConversation, renameConversation,
    sendMessage, cancelMessage, respondToAsk, cancelAsk, updateMessageFeedback, setViewMode, analyzeData, predictData, clear,
    mcpServers, loadMcpTools,
  } = useChatStore();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const thinkingRef = useRef<HTMLDivElement>(null);

  // Only load MCP tools and enable @ mention in agent mode
  const isAgentMode = chatPipelineMode === 'agent';

  // Flatten all MCP tools from all servers (only in agent mode)
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

  const handleFeedback = async (idx: number, satisfied: boolean, expectedTable?: string) => {
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
        expected_table: expectedTable || '',
      });
      // Persist feedback in message
      updateMessageFeedback(idx, satisfied ? 'up' : 'down', expectedTable);
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
        loadWorkflows();
        loadSystemConfig();
        loadWorkspaceConfig(wsId);
      }
    }
  }, [urlWorkspaceId]);

  // Load MCP tools only when in agent mode
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
    if (!input.trim() || loading) return;

    // Parse @tool mentions
    const mcpTools = parseMcpTools(input);
    // Strip @tool mentions from the display message
    const cleanMessage = input.replace(/@[\w_]+__[\w_]+\s*/g, '').trim();

    if (mcpTools.length > 0) {
      // Pass with MCP tools
      sendMessage(cleanMessage || input.trim(), mcpTools);
    } else {
      sendMessage(input.trim());
    }

    setInput('');
    setAtMenuOpen(false);
  };

  const openDetails = (msg: any) => { setDrawerMsg(msg); setDrawerOpen(true); };

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
            {/* Mode Selector - filtered by workspace config */}
            {(() => {
              const allowed = workspaceConfig.allowed_pipeline_modes;
              const modes = Array.isArray(allowed) ? allowed : ['quick', 'deep', 'agent'];
              const modeLabel: Record<string, { icon: any; text: string }> = {
                quick: { icon: <Zap className="h-3 w-3 inline mr-1" />, text: '快速' },
                deep: { icon: <Workflow className="h-3 w-3 inline mr-1" />, text: '深度' },
                agent: { icon: <Bot className="h-3 w-3 inline mr-1" />, text: 'Agent' },
              };
              const currentMode = modes.includes(chatPipelineMode || '') ? chatPipelineMode! : (modes[0] || 'quick');
              if (modes.length <= 1) {
                const m = modes[0] || 'quick';
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
                      deep: '深度模式：完整 RAG + Loop 自修复，适合复杂问题',
                      agent: 'Agent 模式：LLM 自主决策，可调用 MCP 工具和外部 Agent',
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

            {/* Datasource Selector - Only for Quick/Deep modes */}
            {!isAgentMode && (
              <Select
                key={`ds-${selectedWorkspaceId}`}
                value={selectedDsId ? String(selectedDsId) : ''}
                onValueChange={(v) => setSelectedDsId(Number(v))}
              >
                <SelectTrigger className="w-[200px] h-8">
                  <Database className="h-3.5 w-3.5 mr-1.5" />
                  <SelectValue placeholder="选择数据源" />
                </SelectTrigger>
                <SelectContent>
                  {datasources.map((ds) => (
                    <SelectItem key={ds.id} value={String(ds.id)}>
                      {ds.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {/* Model Selector - Always visible */}
            <Select
              key={`model-${selectedWorkspaceId}`}
              value={selectedModelId ? String(selectedModelId) : 'default'}
              onValueChange={(v) => setSelectedModelId(v === 'default' ? null : Number(v))}
            >
              <SelectTrigger className="w-[200px] h-8">
                <Cpu className="h-3.5 w-3.5 mr-1.5" />
                <SelectValue placeholder="选择模型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">默认模型</SelectItem>
                {llmModels.map((m) => (
                  <SelectItem key={m.id} value={String(m.id)}>
                    {m.name} {m.is_default ? '⭐' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Retrieval Strategy - filtered by workspace config */}
            {!isAgentMode && (
              <Select
                value={retrievalStrategy}
                onValueChange={(v) => setRetrievalStrategy(v)}
              >
                <SelectTrigger className="w-[150px] h-8">
                  <SelectValue placeholder="检索策略" />
                </SelectTrigger>
                <SelectContent>
                  {(!workspaceConfig.allowed_retrieval_strategies || workspaceConfig.allowed_retrieval_strategies.includes('hybrid')) && (
                    <SelectItem value="hybrid">混合检索 — BM25+向量 RRF 融合（推荐）</SelectItem>
                  )}
                  {(!workspaceConfig.allowed_retrieval_strategies || workspaceConfig.allowed_retrieval_strategies.includes('full_table')) && (
                    <SelectItem value="full_table">整表检索 — 返回命中表的全部字段</SelectItem>
                  )}
                  {(!workspaceConfig.allowed_retrieval_strategies || workspaceConfig.allowed_retrieval_strategies.includes('column_first')) && (
                    <SelectItem value="column_first">字段优先 — 向量搜字段，只返回匹配字段</SelectItem>
                  )}
                  {(!workspaceConfig.allowed_retrieval_strategies || workspaceConfig.allowed_retrieval_strategies.includes('two_stage')) && (
                    <SelectItem value="two_stage">两阶段 — 先选表，再筛字段</SelectItem>
                  )}
                  {(!workspaceConfig.allowed_retrieval_strategies || workspaceConfig.allowed_retrieval_strategies.includes('bidirectional')) && (
                    <SelectItem value="bidirectional">双向合并 — 表+字段双路召回，筛字段</SelectItem>
                  )}
                  {(!workspaceConfig.allowed_retrieval_strategies || workspaceConfig.allowed_retrieval_strategies.includes('graph')) && (
                    <SelectItem value="graph">图检索 — 关系遍历，只返回触及的字段</SelectItem>
                  )}
                </SelectContent>
              </Select>
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
        <ScrollArea className="flex-1 min-h-0 p-6">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <Zap className="h-14 w-14 text-primary mb-5" />
              <p className="text-lg">输入你的数据查询问题</p>
              <p className="text-sm mt-2">AI 将自动生成 SQL、执行查询并可视化结果</p>
              <div className="flex gap-3 mt-8 flex-wrap justify-center">
                {['查看最近7天的病例数量', '各区域设备使用率统计', '本月新增用户趋势'].map((q) => (
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
                    {msg.content}
                  </div>
                )}
                {msg.role === 'assistant' && (
                  <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3 overflow-hidden">
                    {/* Streaming: show current action */}
                    {loading && !msg.intent && !msg.error && (
                      <div>
                        <div className="flex items-center gap-2">
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                          <span className="text-sm text-muted-foreground">{loadingStep || '推理中...'}</span>
                        </div>
                      </div>
                    )}

                    {msg.intent && ['chat', 'explain'].includes(msg.intent) && msg.reply && (
                      <div className="leading-relaxed prose prose-sm max-w-none dark:prose-invert">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.reply}</ReactMarkdown>
                      </div>
                    )}
                    {msg.reply && !msg.sql && !msg.error && msg.intent === 'query' && (
                      <div>
                        <div className="leading-relaxed prose prose-sm max-w-none dark:prose-invert">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.reply}</ReactMarkdown>
                        </div>
                        <Button variant="ghost" size="sm" className="mt-2" onClick={() => openDetails(msg)}>
                          <Info className="h-4 w-4 mr-1" />
                          执行详情
                        </Button>
                      </div>
                    )}

                    {/* Agent mode: show analysis text above chart when both reply and sql exist */}
                    {msg.reply && msg.sql && !msg.error && (
                      <div className="mb-3 leading-relaxed prose prose-sm max-w-none dark:prose-invert">
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
                        <Button variant="ghost" size="sm" className="mt-1" onClick={() => openDetails(msg)}>
                          <Info className="h-4 w-4 mr-1" />
                          执行详情
                        </Button>
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
                            <Button variant="ghost" size="sm" onClick={() => openDetails(msg)}>
                              <Info className="h-4 w-4 mr-1" />执行详情
                            </Button>
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
                            <div className="flex-1" />
                            {(() => {
                              const fb = msg.feedback || feedbackMap[idx];
                              return !fb ? (
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
                              );
                            })()}
                            {showExpectedInput[idx] && !(msg.feedback || feedbackMap[idx]) && (
                              <div className="flex items-center gap-2 mt-2 w-full">
                                <Input
                                  value={expectedTableInput[idx] || ''}
                                  onChange={(e) => setExpectedTableInput(prev => ({ ...prev, [idx]: e.target.value }))}
                                  placeholder="期望的表名（可选，如 t_user_customer）"
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
                    <span className="text-xs text-muted-foreground">{loadingStep || '推理中...'}</span>
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

            <div className="flex gap-2">
              <Input
                ref={inputRef}
                value={input}
                onChange={isAgentMode ? handleInputChange : (e) => setInput(e.target.value)}
                placeholder={isAgentMode ? "输入问题... 输入 @ 调用 MCP 工具" : "输入你的数据查询问题..."}
                className="flex-1"
                onKeyDown={isAgentMode ? handleInputKeyDown : (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
              />
              <Button onClick={handleSend} disabled={loading || !input.trim()}>
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

      {/* Execution Details Drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/50" onClick={() => setDrawerOpen(false)} />
          <div className="relative w-full max-w-[480px] bg-background border-l overflow-auto">
            <div className="flex items-center justify-between p-4 border-b">
              <h2 className="font-bold">执行详情</h2>
              <Button variant="ghost" size="sm" onClick={() => setDrawerOpen(false)}>
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="p-4 space-y-4">
              {drawerMsg?.timings && Object.keys(drawerMsg.timings).length > 0 && (
                <div className="p-3 bg-muted rounded-lg">
                  <h4 className="font-medium text-sm mb-2">分步耗时</h4>
                  {(['intent', 'rag', 'llm', 'validate', 'execute', 'total'] as const).filter(key => key in drawerMsg.timings).map(key => (
                    <div key={key} className={`flex justify-between py-1 text-xs ${key === 'total' ? 'border-t mt-1 pt-2' : ''}`}>
                      <span className="text-muted-foreground">{({ intent: '意图识别', rag: 'RAG 检索', llm: 'SQL 生成', validate: 'SQL 校验', execute: 'SQL 执行', total: '合计' })[key]}</span>
                      <span className="font-medium">{typeof drawerMsg.timings[key] === 'number' ? `${drawerMsg.timings[key]}s` : drawerMsg.timings[key]}</span>
                    </div>
                  ))}
                  {drawerMsg?.elapsed_ms && (
                    <div className="flex justify-between py-1 text-xs border-t mt-1 pt-2">
                      <span className="text-muted-foreground">前端总耗时</span>
                      <span className="font-medium">{(drawerMsg.elapsed_ms / 1000).toFixed(1)}s</span>
                    </div>
                  )}
                </div>
              )}

              {drawerMsg?.rag && (
                <div className="p-3 bg-muted rounded-lg space-y-3">
                  <h4 className="font-medium text-sm">RAG 检索摘要</h4>
                  {drawerMsg.rag.rag_source && (
                    <p className="text-xs text-muted-foreground">
                      数据来源: {drawerMsg.rag.rag_source === 'keyword_selected' ? '关键词匹配' : drawerMsg.rag.rag_source === 'vector_search' ? '向量检索' : drawerMsg.rag.rag_source}
                    </p>
                  )}
                  <div className="flex gap-2 flex-wrap">
                    {(drawerMsg.rag.table_info_count || 0) > 0 && <Badge variant="outline"><Database className="h-3 w-3 mr-1" />表 {drawerMsg.rag.table_info_count}</Badge>}
                    {(drawerMsg.rag.column_metadata_count || 0) > 0 && <Badge variant="outline"><Database className="h-3 w-3 mr-1" />字段 {drawerMsg.rag.column_metadata_count}</Badge>}
                    {drawerMsg.rag.sql_templates_count > 0 && <Badge variant="outline"><FileSearch className="h-3 w-3 mr-1" />SQL 模板 {drawerMsg.rag.sql_templates_count}</Badge>}
                    {drawerMsg.rag.business_terms_count > 0 && <Badge variant="outline"><Lightbulb className="h-3 w-3 mr-1" />术语 {drawerMsg.rag.business_terms_count}</Badge>}
                  </div>
                  {drawerMsg.rag.table_info?.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">匹配的表:</p>
                      <div className="flex flex-wrap gap-1">
                        {drawerMsg.rag.table_info.map((t: any, i: number) => (
                          <Badge key={i} variant="secondary" className="text-xs">
                            {t.table_name}{t.table_comment ? ` (${t.table_comment})` : ''}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {drawerMsg.rag.column_metadata?.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">匹配的字段:</p>
                      <div className="flex flex-wrap gap-1">
                        {drawerMsg.rag.column_metadata.map((c: any, i: number) => (
                          <Badge key={i} variant="outline" className="text-xs">
                            {c.table_name}.{c.column_name}{c.column_comment ? ` (${c.column_comment})` : ''}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {drawerMsg?.thinking && (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="font-medium text-sm"><Lightbulb className="h-4 w-4 inline mr-1 text-purple-500" />模型推理</h4>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2 text-xs"
                      onClick={() => {
                        navigator.clipboard.writeText(drawerMsg.thinking || '');
                        toast.success('已复制');
                      }}
                    >
                      <Copy className="h-3.5 w-3.5 mr-1" />
                      复制
                    </Button>
                  </div>
                  <pre className="p-3 bg-muted rounded-lg text-xs whitespace-pre-wrap max-h-[200px] overflow-auto">{drawerMsg.thinking}</pre>
                </div>
              )}

              {/* Execution flow — agent mode structured view */}
              {drawerMsg?.progressStages && drawerMsg.progressStages.length > 0 && (() => {
                // Build structured items: merge exec+exec_done into one line
                const doneSteps = new Set<number>();
                drawerMsg.progressStages.forEach((s: any) => {
                  if (s.stage === 'agent_exec_done' && s.step) doneSteps.add(s.step);
                });

                let toolIdx = 0;
                return (
                  <div className="p-3 bg-muted rounded-lg">
                    <h4 className="font-medium text-sm mb-3">执行流程</h4>
                    <div className="space-y-2">
                      {drawerMsg.progressStages.map((s: any, i: number) => {
                        // Skip agent_exec_done — its elapsed is shown on the exec line
                        if (s.stage === 'agent_exec_done') return null;

                        if (s.stage === 'agent_exec') {
                          toolIdx++;
                          // Find matching done stage for elapsed
                          const done = drawerMsg.progressStages.find(
                            (x: any) => x.stage === 'agent_exec_done' && x.step === s.step
                          );
                          return (
                            <div key={i} className="flex items-center gap-2 text-xs ml-3">
                              <span className="w-4 h-4 rounded bg-blue-500/10 text-blue-500 flex items-center justify-center text-[9px] font-medium shrink-0">
                                {toolIdx}
                              </span>
                              <span className="font-medium">{s.message}</span>
                              {done?.elapsed !== undefined && (
                                <span className="text-muted-foreground text-[10px] ml-auto shrink-0">
                                  {done.elapsed < 1 ? `${Math.round(done.elapsed * 1000)}ms` : `${done.elapsed.toFixed(1)}s`}
                                </span>
                              )}
                            </div>
                          );
                        }

                        // Plan / think / decide / start
                        const icon = s.stage === 'agent_start' ? '▶' : s.stage === 'agent_decide' ? '◆' : '○';
                        const color = s.stage === 'agent_decide' ? 'text-primary' : 'text-muted-foreground';
                        return (
                          <div key={i} className={`flex items-center gap-2 text-xs ${color}`}>
                            <span className="w-4 text-center text-[10px] shrink-0">{icon}</span>
                            <span className={s.stage === 'agent_decide' ? 'font-medium' : ''}>{s.message}</span>
                          </div>
                        );
                      })}
                    </div>
                    {drawerMsg.timings?.total && (
                      <div className="mt-3 pt-2 border-t text-xs text-muted-foreground">
                        总耗时: {drawerMsg.timings.total}s
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Workflow info (Deep mode) */}
              {drawerMsg?.workflow_info && (
                <div className="p-3 bg-muted rounded-lg">
                  <h4 className="font-medium text-sm mb-2">工作流信息</h4>
                  <div className="space-y-1 text-xs">
                    {drawerMsg.workflow_info.name && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">工作流</span>
                        <span className="font-medium">{drawerMsg.workflow_info.name}</span>
                      </div>
                    )}
                    {drawerMsg.workflow_info.rounds_used !== undefined && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">执行轮数</span>
                        <span className="font-medium">{drawerMsg.workflow_info.rounds_used}</span>
                      </div>
                    )}
                    {drawerMsg.workflow_info.loop_count !== undefined && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">元数据循环</span>
                        <span className="font-medium">{drawerMsg.workflow_info.loop_count} 次</span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Agent tool calls */}
              {drawerMsg?.tool_calls && drawerMsg.tool_calls.length > 0 && (
                <div className="p-3 bg-muted rounded-lg">
                  <h4 className="font-medium text-sm mb-2">
                    <Workflow className="h-4 w-4 inline mr-1 text-blue-500" />
                    Agent 工具调用 · {drawerMsg.tool_calls.length} 步
                  </h4>
                  <div className="space-y-3">
                    {drawerMsg.tool_calls.map((tc: any, i: number) => (
                      <div key={i} className="border rounded-md p-2 bg-background">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="w-5 h-5 rounded-full bg-blue-500/10 text-blue-500 flex items-center justify-center text-[10px] font-medium shrink-0">
                            {tc.step || i + 1}
                          </span>
                          <span className="font-medium text-xs">{tc.tool}</span>
                          {tc.elapsed !== undefined && (
                            <Badge variant="outline" className="text-[10px] ml-auto shrink-0">
                              {tc.elapsed < 1 ? `${Math.round(tc.elapsed * 1000)}ms` : `${tc.elapsed.toFixed(1)}s`}
                            </Badge>
                          )}
                        </div>
                        {tc.arguments && Object.keys(tc.arguments).length > 0 && (
                          <div className="text-[11px] text-muted-foreground ml-7 mb-1">
                            <span className="font-medium">参数:</span>{' '}
                            {Object.entries(tc.arguments).map(([k, v]) => `${k}=${typeof v === 'string' ? v.slice(0, 80) : JSON.stringify(v).slice(0, 80)}`).join(', ')}
                          </div>
                        )}
                        {(tc.result_preview || tc.result || tc.error) && (
                          <details className="ml-7">
                            <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                              {tc.error ? '❌ 查看错误' : '查看返回结果'}
                            </summary>
                            <pre className="mt-1 p-2 bg-muted rounded text-[11px] whitespace-pre-wrap max-h-[150px] overflow-auto">
                              {tc.error || tc.result || tc.result_preview}
                            </pre>
                          </details>
                        )}
                      </div>
                    ))}
                  </div>
                  {drawerMsg.timings?.total && (
                    <div className="mt-2 pt-2 border-t text-xs text-muted-foreground">
                      总耗时: {drawerMsg.timings.total}s
                    </div>
                  )}
                </div>
              )}

              {!drawerMsg?.timings && !drawerMsg?.rag && !drawerMsg?.thinking && !drawerMsg?.progressStages && !drawerMsg?.tool_calls && (
                <div className="text-center py-12 text-muted-foreground">暂无执行详情</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
