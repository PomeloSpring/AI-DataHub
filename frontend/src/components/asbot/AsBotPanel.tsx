import { useState, useRef, useEffect } from 'react';
import { X, Send, Loader2, Bot, User, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ProcessTimeline from '../ProcessTimeline';
import { useAsBotStore } from '../../stores/asBotStore';
import type { AsBotMessage } from '../../stores/asBotStore';
import ApprovalCard from './ApprovalCard';

/**
 * AS-BOT 侧边聊天面板 — 从右侧滑出.
 */
export default function AsBotPanel() {
  const {
    panelOpen, messages, loading, loadingStep,
    sendMessage, cancelMessage, approveAction, rejectAction,
    closePanel, clearMessages,
  } = useAsBotStore();

  const [input, setInput] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if (panelOpen && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [panelOpen]);

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

  if (!panelOpen) return null;

  return (
    <div className="fixed right-0 top-0 bottom-0 w-[420px] bg-background border-l shadow-2xl z-50 flex flex-col overflow-hidden animate-in slide-in-from-right duration-300">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-14 border-b shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="h-5 w-5 text-primary" />
          <span className="font-semibold text-sm">AS-BOT 系统助手</span>
        </div>
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={clearMessages}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>清空对话</TooltipContent>
          </Tooltip>
          <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={closePanel}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 py-3" ref={scrollRef}>
        <div className="space-y-4">
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
              isStreaming={loading && idx === messages.length - 1}
              onApprove={(approvalId) => approveAction(approvalId, idx)}
              onReject={(approvalId) => rejectAction(approvalId, idx)}
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
        <div className="flex gap-2">
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
  msg, isStreaming, onApprove, onReject,
}: {
  msg: AsBotMessage;
  isStreaming: boolean;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
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
          <div className={`rounded-lg px-3 py-2 text-sm overflow-hidden break-words ${isUser ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}>
            {isUser ? (
              <p className="whitespace-pre-wrap">{msg.content}</p>
            ) : (
              <div className="prose prose-sm dark:prose-invert max-w-none break-words [&_table]:block [&_table]:overflow-x-auto [&_table]:max-w-full [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_code]:break-all">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.content}
                </ReactMarkdown>
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

        {/* Approval card */}
        {msg.pendingApproval && (
          <ApprovalCard
            approval={msg.pendingApproval}
            status={msg.approvalStatus || 'pending'}
            result={msg.approvalResult}
            onApprove={() => onApprove(msg.pendingApproval!.approval_id)}
            onReject={() => onReject(msg.pendingApproval!.approval_id)}
          />
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
