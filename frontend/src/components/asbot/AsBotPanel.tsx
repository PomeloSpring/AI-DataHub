import { useState, useRef, useEffect } from 'react';
import { X, Send, Loader2, Bot, User, Plus, History, Trash2, ExternalLink } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import MarkdownWithCharts from '../MarkdownWithCharts';
import ProcessTimeline from '../ProcessTimeline';
import { useAsBotStore } from '../../stores/asBotStore';
import type { AsBotMessage } from '../../stores/asBotStore';
import { useThemeStore, applyTheme } from '../../stores/themeStore';
import { hasPerm } from '../../stores/permissionStore';
import { useChatStore } from '../../stores/chatStore';

/**
 * AS-BOT 聊天面板 — 抽屉(variant=drawer)与全屏独立标签页(fullscreen)复用同一实现.
 */
export default function AsBotPanel({ fullscreen = false }: { fullscreen?: boolean }) {
  const {
    panelOpen, messages, loading, loadingStep,
    sendMessage, cancelMessage,
    closePanel, newConversation,
    conversations, conversationId, switchConversation, deleteConversation, loadConversations,
  } = useAsBotStore();

  const [input, setInput] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { theme } = useThemeStore();
  const navigate = useNavigate();

  // 跳转到工作空间 Chat 的对应会话：会话 id 经 sessionStorage 一次性携带
  // （与首页 home_question 同惯例），由 Chat 挂载时消费并 switchConversation。
  const jumpToWorkspaceConversation = () => {
    const conv = conversations.find((c) => c.id === conversationId);
    const wsId = conv?.workspace_id || useChatStore.getState().selectedWorkspaceId || 0;
    if (conversationId != null) sessionStorage.setItem('asbot_open_conversation', String(conversationId));
    closePanel();
    navigate(wsId ? `/ws/${wsId}/chat` : '/ask');
  };

  // 全屏独立标签页：挂载即加载会话（不依赖抽屉的 openPanel）
  useEffect(() => {
    if (fullscreen) { void loadConversations(); }
  }, [fullscreen]);

  // 应用主题到全屏页面
  useEffect(() => { applyTheme(theme); }, [theme]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if ((fullscreen || panelOpen) && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [panelOpen, fullscreen]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput('');
    sendMessage(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!fullscreen && !panelOpen) return null;

  // 全屏标签页权限门禁：菜单与功能权限码(asbot:use)裁决，无权限时不暴露会话能力
  if (fullscreen && !hasPerm('asbot:use')) {
    return (
      <div className="h-screen w-full flex flex-col items-center justify-center gap-2 text-muted-foreground bg-background">
        <Bot className="h-10 w-10 opacity-30" />
        <p className="text-sm">当前账号无权访问 AS-BOT</p>
      </div>
    );
  }

  const contentWidth = fullscreen ? 'max-w-3xl mx-auto w-full' : '';

  return (
    <div className={fullscreen
      ? 'h-screen w-full bg-background flex flex-col overflow-hidden'
      : 'fixed right-0 top-0 bottom-0 w-[420px] bg-background border-l shadow-2xl z-50 flex flex-col overflow-hidden animate-in slide-in-from-right duration-300'}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-14 border-b shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="h-5 w-5 text-primary" />
          <span className="font-semibold text-sm">AS-BOT 系统助手</span>
        </div>
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={() => { newConversation(); setShowHistory(false); }}>
                <Plus className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>新对话</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8 w-8 p-0" aria-pressed={showHistory}
                onClick={() => { setShowHistory(v => !v); if (!showHistory) void loadConversations(); }}>
                <History className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>历史记录</TooltipContent>
          </Tooltip>
          {!fullscreen && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" className="h-8 w-8 p-0" aria-label="在工作空间中打开该会话"
                  onClick={jumpToWorkspaceConversation}>
                  <ExternalLink className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>在工作空间中打开该会话</TooltipContent>
            </Tooltip>
          )}
          {!fullscreen && (
            <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={closePanel}>
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* 历史记录浮层 */}
      {showHistory && (
        <div className="border-b bg-muted/30 px-3 py-2 max-h-56 overflow-y-auto">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-muted-foreground">历史对话</span>
            <button className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setShowHistory(false)}>收起</button>
          </div>
          {conversations.length === 0 ? (
            <p className="text-xs text-muted-foreground py-2">暂无历史记录</p>
          ) : (
            <div className="space-y-1">
              {conversations.map(c => (
                <div key={c.id}
                  className={`group flex items-center gap-2 rounded px-2 py-1.5 cursor-pointer text-sm hover:bg-muted ${c.id === conversationId ? 'bg-primary/10 text-primary' : ''}`}
                  onClick={() => { switchConversation(c.id); setShowHistory(false); }}>
                  <Bot className="h-3.5 w-3.5 shrink-0 opacity-60" />
                  <span className="flex-1 truncate">{c.title || '未命名'}</span>
                  <span className="text-[10px] text-muted-foreground shrink-0">{new Date(c.updated_at).toLocaleDateString()}</span>
                  <button
                    className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive shrink-0"
                    title="删除" aria-label="删除对话"
                    onClick={(e) => { e.stopPropagation(); void deleteConversation(c.id); }}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 py-3" ref={scrollRef}>
        <div className={`space-y-4 ${contentWidth}`}>
          {messages.length === 0 && (
            <div className="text-center text-muted-foreground py-12 space-y-3">
              <Bot className="h-10 w-10 mx-auto opacity-30" />
              <div className="space-y-1">
                <p className="text-sm font-medium">AS-BOT 系统助手</p>
                <p className="text-xs">我可以帮您构建本体模型、管理元数据、了解系统能力。</p>
                <p className="text-xs text-muted-foreground/70">所有写操作都会经过您的审批确认。</p>
              </div>
              <div className="flex flex-wrap gap-2 justify-center pt-2">
                {['帮我构建本体模型', '查看系统能力', '列出已有本体'].map(q => (
                  <Button
                    key={q}
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => { setInput(q); setTimeout(() => inputRef.current?.focus(), 100); }}
                  >
                    {q}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, idx) => (
            <MessageBubble
              key={idx}
              msg={msg}
              conversationId={conversationId}
              isStreaming={loading && idx === messages.length - 1}
              onDesignContinue={text => void sendMessage(text)}
            />
          ))}

          {/* Loading indicator */}
          {loading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>{loadingStep || '处理中...'}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 text-xs ml-auto"
                onClick={cancelMessage}
              >
                取消
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* Input */}
      <div className="border-t px-4 py-3 shrink-0">
        <div className={`flex gap-2 ${contentWidth}`}>
          <Input
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入消息... (Enter 发送)"
            className="flex-1 h-9 text-sm"
            disabled={loading}
          />
          <Button
            size="sm"
            className="h-9 w-9 p-0"
            disabled={!input.trim() || loading}
            onClick={handleSend}
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Message Bubble ────────────────────────────────────────────────

function MessageBubble({
  msg, isStreaming, conversationId, onDesignContinue,
}: {
  msg: AsBotMessage;
  isStreaming: boolean;
  conversationId: number | null;
  onDesignContinue?: (text: string) => void;
}) {
  const isUser = msg.role === 'user';

  return (
    <div className={`flex gap-2 ${isUser ? 'justify-end' : 'justify-start'}`}>
      {!isUser && (
        <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
          <Bot className="h-3.5 w-3.5 text-primary" />
        </div>
      )}
      <div className={`min-w-0 flex-1 max-w-[85%] space-y-2 ${isUser ? 'order-first' : ''}`}>
        {/* 思考 + 工具调用时间线（复用 Chat 的 ProcessTimeline） */}
        {!isUser && (
          <ProcessTimeline
            toolCalls={msg.tool_calls as any}
            progressStages={msg.progressStages as any}
            thinking={msg.thinking}
            isStreaming={isStreaming}
          />
        )}

        {/* Main content */}
        {msg.content && (
          <div className={`text-sm break-words ${isUser
            ? 'bg-primary text-primary-foreground rounded-2xl rounded-tr-sm px-4 py-3'
            : 'bg-muted rounded-2xl rounded-tl-sm px-4 py-3 overflow-hidden'}`}>
            {isUser ? (
              <p className="whitespace-pre-wrap">{msg.content}</p>
            ) : (
              <div className="prose prose-sm dark:prose-invert max-w-none break-words [&_table]:block [&_table]:overflow-x-auto [&_table]:max-w-full [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_code]:break-all">
                {/* 与工作空间 Chat 同一渲染（图表契约/表格/代码/设计卡片样式一致） */}
                <MarkdownWithCharts text={msg.content} conversationId={conversationId} onDesignContinue={onDesignContinue} />
              </div>
            )}
          </div>
        )}

        {/* Error */}
        {msg.error && (
          <div className="rounded-lg px-3 py-2 text-sm bg-destructive/10 text-destructive border border-destructive/20 break-words">
            {msg.error}
          </div>
        )}
      </div>

      {isUser && (
        <div className="h-7 w-7 rounded-full bg-muted flex items-center justify-center shrink-0 mt-0.5">
          <User className="h-3.5 w-3.5 text-muted-foreground" />
        </div>
      )}
    </div>
  );
}
