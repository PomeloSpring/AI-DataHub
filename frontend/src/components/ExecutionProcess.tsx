/**
 * ExecutionProcess — 把"思考 + 工具调用"执行过程收敛展示, 保证不拉长对话框。
 *
 * 行为:
 *  - 执行中: inline 展示一个限高(260px)、内部自动滚到最新的窗口, 方便实时观察;
 *  - 执行结束: inline 滑动收起, 只保留一行摘要(结果才是重点), 不再占用高度;
 *  - 点击摘要行: 滑动展开/收起阶段性内容(限高内滚);「全屏」按钮经 Portal 弹层
 *    查看完整过程 —— 弹层脱离文档流, 无论内容多长都不会撑高消息气泡/对话框。
 *
 * 两个状态信号必须分开(历史缺陷:
 * 「任务运行中但卡头显示已完成」—— agent loop 中模型会先输出中间文本再续调工具,
 * 出现文本 ≠ 回合结束):
 *  - isStreaming   = 回合是否仍在执行(done 未达), 驱动卡头 spinner/「执行中」/展开窗口;
 *  - thinkingActive = 思考阶段是否进行中(已开始出正文即停止), 只驱动思考块转圈。
 *
 * 无过程内容时不渲染。内部复用 ProcessTimeline(思考/工具按真实顺序穿插)。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Activity, CheckCircle2, Loader2, ChevronDown, Maximize2, X } from 'lucide-react';
import ProcessTimeline from './ProcessTimeline';
import { SlideCollapse } from './chat/StageCard';
import type { ProcessSegment, ToolCall, ProgressStage } from '../stores/chatStore';

interface Props {
  process?: ProcessSegment[];
  toolCalls?: ToolCall[];
  progressStages?: ProgressStage[];
  thinking?: string;
  /** 回合是否仍在执行(跟随 done), 驱动卡头运行中状态与展开窗口 */
  isStreaming?: boolean;
  /** 思考阶段是否进行中(出正文后 false), 仅驱动思考块转圈; 缺省跟随 isStreaming */
  thinkingActive?: boolean;
  conversationId?: number | null;
}

export default function ExecutionProcess({
  process, toolCalls, progressStages, thinking, isStreaming = false, thinkingActive, conversationId,
}: Props) {
  const thinkingOn = thinkingActive ?? isStreaming;
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

  // 弹层(modal)开关: 仅用于"全屏查看完整过程", 脱离文档流不撑高对话框。
  const [modalOpen, setModalOpen] = useState(false);
  // inline 滑动展开态: null = 跟随流式状态(执行中展开、结束自动滑动收起)。
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const inlineOpen = userOpen ?? isStreaming;
  const bodyRef = useRef<HTMLDivElement>(null);

  // 执行中: inline 限高窗口内部自动滚到最新一步。
  const sig = `${(thinking || '').length}:${stepCount}:${process?.length || 0}:${toolCalls?.length || 0}`;
  useEffect(() => {
    if (isStreaming && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [sig, isStreaming, inlineOpen]);

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
    <div className="mb-3 rounded-xl border bg-card shadow-sm overflow-hidden animate-stage-in">
      {/* 摘要行 — 点击滑动展开/收起阶段性过程;「全屏」打开弹层 */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none hover:bg-muted/40 transition-colors"
        onClick={() => setUserOpen(!inlineOpen)}
      >
        {isStreaming ? (
          <span className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-primary/10">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          </span>
        ) : (
          <span className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-green-500/10">
            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
          </span>
        )}
        <span className="text-xs font-medium text-foreground shrink-0">执行过程</span>
        {stepCount > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground shrink-0 tabular-nums">
            {stepCount} 步
          </span>
        )}
        <span className="text-[11px] text-muted-foreground truncate flex-1 min-w-0">
          {isStreaming ? '正在思考与调用工具…' : (inlineOpen ? '点击收起过程' : '已完成，点击滑动展开过程')}
        </span>
        <button
          className="p-1 rounded hover:bg-muted text-muted-foreground shrink-0"
          title="全屏查看完整过程"
          aria-label="全屏查看完整过程"
          onClick={(e) => { e.stopPropagation(); setModalOpen(true); }}
        >
          <Maximize2 className="h-3 w-3" />
        </button>
        <ChevronDown
          className={`h-3.5 w-3.5 text-muted-foreground shrink-0 transition-transform duration-300 ${inlineOpen ? 'rotate-180' : ''}`}
        />
      </div>

      {/* 阶段性内容: 滑动展开的限高窗口(内部滚动), 收起时不占高度。
          思考块转圈只跟随 thinkingActive(出正文即停); 卡头/窗口展开跟随 isStreaming
          (done 才收) —— 中间文本后还会续调工具, 不得提前渲成“已完成”。
          旧缺陷两处: 写死 timeline(true) 使历史展开永远转圈; 用同一信号驱动两者,
          使“文本已出现但回合还在跑”误渲成已完成(或反过来思考块永转)。 */}
      <SlideCollapse open={inlineOpen}>
        <div
          ref={bodyRef}
          className="px-2 pb-2 border-t"
          style={{ maxHeight: 260, overflowY: 'auto' }}
        >
          {timeline(thinkingOn)}
        </div>
      </SlideCollapse>

      {/* 全屏查看完整过程: Portal 弹层, 脱离文档流, 不撑高对话框 */}
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
