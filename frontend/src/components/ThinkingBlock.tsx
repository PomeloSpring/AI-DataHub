/**
 * ThinkingBlock — Collapsible model reasoning display.
 *
 * Shows the LLM's chain-of-thought inline within assistant messages.
 * 默认折叠(含流式期间),仅显缩略预览与状态;用户可手动展开。
 */

import { useState, useEffect, useRef } from 'react';
import { ChevronRight, ChevronDown, Brain, Loader2 } from 'lucide-react';

interface Props {
  content: string;
  isStreaming?: boolean;
}

export default function ThinkingBlock({ content, isStreaming = false }: Props) {
  // 不默认展开:流式期间也只显示“思考中”状态,避免刷屏
  const [expanded, setExpanded] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Auto-scroll body during streaming
  useEffect(() => {
    if (expanded && isStreaming && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [content, expanded, isStreaming]);

  if (!content) return null;

  const preview = content.length > 80
    ? content.slice(0, 80).replace(/\n/g, ' ') + '...'
    : content.replace(/\n/g, ' ');

  return (
    <div className="mb-3 rounded-lg border border-purple-200 dark:border-purple-800/40 bg-purple-50/50 dark:bg-purple-950/20 overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none hover:bg-purple-100/50 dark:hover:bg-purple-900/20 transition-colors"
        onClick={() => setExpanded(v => !v)}
      >
        <Brain className="h-3.5 w-3.5 text-purple-500 shrink-0" />
        <span className="text-xs font-medium text-purple-600 dark:text-purple-400 shrink-0">
          思考
        </span>
        {isStreaming && (
          <span className="flex items-center gap-1 text-xs text-purple-500/80 shrink-0">
            <Loader2 className="h-3 w-3 animate-spin" />思考中...
          </span>
        )}
        {!expanded && (
          <span className="text-xs text-muted-foreground truncate flex-1 min-w-0">
            {preview}
          </span>
        )}
        <span className="text-muted-foreground shrink-0">
          {expanded
            ? <ChevronDown className="h-3.5 w-3.5" />
            : <ChevronRight className="h-3.5 w-3.5" />
          }
        </span>
      </div>

      {/* Body */}
      {expanded && (
        <div
          ref={bodyRef}
          className="px-3 pb-3 max-h-[300px] overflow-y-auto"
        >
          <div className="pl-5.5 border-l-2 border-purple-200 dark:border-purple-800/40 text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap break-words">
            {content}
          </div>
        </div>
      )}
    </div>
  );
}
