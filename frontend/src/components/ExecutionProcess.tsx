/**
 * ExecutionProcess — 把"思考 + 工具调用"执行过程收敛展示, 保证不拉长对话框。
 *
 * 行为:
 *  - 执行中: inline 展示一个限高(260px)、内部自动滚到最新的窗口, 方便实时观察;
 *  - 执行结束: inline 只保留一行摘要(结果才是重点), 不再占用高度;
 *  - 点击摘要行: 通过 Portal 弹层(modal)查看完整过程 —— 弹层脱离文档流,
 *    无论内容多长都不会撑高消息气泡/对话框, 也不受任何祖先 overflow 裁剪影响。
 *
 * 无过程内容时不渲染。内部复用 ProcessTimeline(思考/工具按真实顺序穿插)。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Activity, CheckCircle2, Loader2, ChevronRight, X } from 'lucide-react';
import ProcessTimeline from './ProcessTimeline';
import type { ProcessSegment, ToolCall, ProgressStage } from '../stores/chatStore';

interface Props {
  process?: ProcessSegment[];
  toolCalls?: ToolCall[];
  progressStages?: ProgressStage[];
  thinking?: string;
  isStreaming?: boolean;
  conversationId?: number | null;
}

export default function ExecutionProcess({
  process, toolCalls, progressStages, thinking, isStreaming = false, conversationId,
}: Props) {
  const hasProcess =
    !!thinking ||
    (process?.length ?? 0) > 0 ||
    (toolCalls?.length ?? 0) > 0 ||
    (progressStages?.some(s => s.stage === 'agent_exec') ?? false);

  const stepCount = useMemo(() => {
    if (toolCalls && toolCalls.length) return toolCalls.length;
    if (process && process.length) {
      const tools = process.filter(s => s.kind === 'tool').length;
      return tools || process.length;
    }
    if (progressStages) return progressStages.filter(s => s.stage === 'agent_exec').length;
    return 0;
  }, [toolCalls, process, progressStages]);

  // 弹层(modal)开关: 仅用于"结束后查看完整过程", 脱离文档流不撑高对话框。
  const [modalOpen, setModalOpen] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // 执行中: inline 限高窗口内部自动滚到最新一步。
  const sig = `${(thinking || '').length}:${stepCount}:${process?.length || 0}:${toolCalls?.length || 0}`;
  useEffect(() => {
    if (isStreaming && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [sig, isStreaming]);

  // Esc 关闭弹层
  useEffect(() => {
    if (!modalOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setModalOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [modalOpen]);

  if (!hasProcess) return null;

  const timeline = (streaming: boolean) => (
    <ProcessTimeline
      conversationId={conversationId}
      process={process}
      toolCalls={toolCalls}
      progressStages={progressStages}
      thinking={thinking}
      isStreaming={streaming}
    />
  );

  return (
    <div className="mb-3 rounded-lg border bg-muted/20">
      {/* 摘要行 — 结束后仅保留这一行; 点击打开弹层查看完整过程 */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none rounded-lg hover:bg-muted/60 transition-colors"
        onClick={() => setModalOpen(true)}
      >
        {isStreaming ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary shrink-0" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />
        )}
        <span className="text-xs font-medium text-foreground shrink-0">执行过程</span>
        {stepCount > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground shrink-0 tabular-nums">
            {stepCount} 步
          </span>
        )}
        <span className="text-[11px] text-muted-foreground truncate flex-1 min-w-0">
          {isStreaming ? '正在思考与调用工具…' : '已完成，点击展开查看过程'}
        </span>
        <span className="flex items-center gap-1 text-[11px] text-muted-foreground shrink-0">
          {!isStreaming && <Activity className="h-3 w-3" />}
          <ChevronRight className="h-3.5 w-3.5" />
        </span>
      </div>

      {/* 执行中: inline 限高窗口(内部滚动), 结束后不渲染 → 不占高度 */}
      {isStreaming && (
        <div
          ref={bodyRef}
          className="px-2 pb-2"
          style={{ maxHeight: 260, overflowY: 'auto' }}
        >
          {timeline(true)}
        </div>
      )}

      {/* 结束后查看完整过程: Portal 弹层, 脱离文档流, 不撑高对话框 */}
      {modalOpen && createPortal(
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
          onClick={() => setModalOpen(false)}
        >
          <div
            className="flex flex-col w-[min(960px,94vw)] rounded-xl border bg-background shadow-2xl overflow-hidden"
            style={{ maxHeight: '82vh' }}
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 px-4 py-3 border-b shrink-0">
              <Activity className="h-4 w-4 text-primary shrink-0" />
              <span className="text-sm font-medium">执行过程详情</span>
              {stepCount > 0 && (
                <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground tabular-nums">
                  {stepCount} 步
                </span>
              )}
              <button
                className="ml-auto p-1 rounded hover:bg-muted text-muted-foreground"
                onClick={() => setModalOpen(false)}
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="px-3 py-2 flex-1 min-h-0" style={{ overflowY: 'auto' }}>
              {timeline(false)}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
