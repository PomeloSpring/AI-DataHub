/**
 * ToolCallTimeline — Inline tool call visualization for agent mode.
 *
 * Renders tool calls as a collapsible timeline within assistant messages,
 * grouped by consecutive same-tool calls. Each item shows tool name,
 * summary, elapsed time, and expandable input/output.
 */

import { useState, useMemo } from 'react';
import {
  Wrench, CheckCircle, XCircle, Loader2,
  ChevronRight, ChevronDown,
} from 'lucide-react';
import type { ToolCall, ProgressStage } from '../stores/chatStore';
import { KnowledgeSummary } from './InlineDetails';
import { SlideCollapse } from './chat/StageCard';
// 工具名人话 = 全站一份（报告重跑的过程面板与这里同一口径），不在页面里各拼一套
import { getToolLabel } from '../lib/toolLabel';

// ── Extract summary from tool call ─────────────────────────────

function extractSummary(tc: ToolCall): string {
  if (tc.knowledge) {
    if (tc.knowledge.incomplete) return '部分知识检索未完成';
    if (tc.knowledge.status === 'error') return tc.knowledge.message || '知识检索失败';
    if (tc.knowledge.status === 'empty') return '未找到相关文档';
    return '知识检索已完成，引用状态见下方';
  }
  if (tc.error) {
    const firstLine = tc.error.split('\n')[0] ?? '';
    return firstLine.length > 60 ? firstLine.slice(0, 60) + '...' : firstLine;
  }
  if (tc.result_preview) return tc.result_preview;
  if (tc.result) {
    const o = tc.result;
    const rowMatch = o.match(/(\d+)\s*行/);
    if (rowMatch) return `返回 ${rowMatch[1]} 行`;
    if (o.length > 50) return o.slice(0, 50).replace(/\n/g, ' ') + '...';
    return o.replace(/\n/g, ' ').slice(0, 50);
  }
  return '执行中...';
}

// ── Format arguments for display ───────────────────────────────

function formatArgs(args: Record<string, any> | undefined): string {
  if (!args || Object.keys(args).length === 0) return '';
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v.slice(0, 80) : JSON.stringify(v).slice(0, 80)}`)
    .join(', ');
}

// ── Group consecutive same-tool calls ──────────────────────────

interface ToolCallGroup {
  tool: string;
  items: (ToolCall & { _index: number })[];
}

// 导出供 ProcessTimeline 复用相邻同工具分组的类型
export type { ToolCallGroup };

function groupToolCalls(toolCalls: ToolCall[]): ToolCallGroup[] {
  const groups: ToolCallGroup[] = [];
  for (let i = 0; i < toolCalls.length; i++) {
    const tc = toolCalls[i];
    const last = groups[groups.length - 1];
    if (last && last.tool === tc.tool) {
      last.items.push({ ...tc, _index: i });
    } else {
      groups.push({ tool: tc.tool, items: [{ ...tc, _index: i }] });
    }
  }
  return groups;
}

// ── Single Tool Item ───────────────────────────────────────────
// 导出供 ProcessTimeline(思考/工具穿插的有序时间线)复用
export function SingleToolItem({ tc, conversationId }: { tc: ToolCall & { _index: number }; conversationId?: number | null }) {
  const [expanded, setExpanded] = useState(false);
  const failed = !!tc.error || tc.knowledge?.status === 'error' || !!tc.knowledge?.incomplete;
  const pending = !tc.result && !tc.error && !tc.knowledge;
  const summary = extractSummary(tc);
  const argsStr = formatArgs(tc.arguments);
  const stepNum = tc.step || tc._index + 1;

  return (
    <div>
      <div
        className={`flex items-center gap-2 px-2 py-1.5 rounded text-xs cursor-pointer hover:bg-muted/50 transition-colors ${
          failed ? 'text-destructive' : ''
        }`}
        onClick={() => setExpanded(!expanded)}
      >
        {/* Status dot */}
        {pending ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground shrink-0" />
        ) : failed ? (
          <XCircle className="h-3 w-3 text-destructive shrink-0" />
        ) : (
          <CheckCircle className="h-3 w-3 text-green-500 shrink-0" />
        )}

        {/* Step number */}
        <span className="w-4 h-4 rounded bg-blue-500/10 text-blue-500 flex items-center justify-center text-[9px] font-medium shrink-0">
          {stepNum}
        </span>

        {/* Tool name */}
        <span className="font-medium text-foreground shrink-0">
          {getToolLabel(tc.tool)}
        </span>

        {/* Summary */}
        <span className={`truncate flex-1 min-w-0 ${failed ? 'text-destructive' : 'text-muted-foreground'}`}>
          {summary}
        </span>

        {/* Elapsed */}
        {tc.elapsed !== undefined && (
          <span className="text-[10px] text-muted-foreground shrink-0 tabular-nums">
            {tc.elapsed < 1 ? `${Math.round(tc.elapsed * 1000)}ms` : `${tc.elapsed.toFixed(1)}s`}
          </span>
        )}

        {/* Expand arrow */}
        {(argsStr || tc.result || tc.result_preview || tc.error) && (
          <span className="text-muted-foreground shrink-0">
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </span>
        )}
      </div>

      {tc.knowledge && <KnowledgeSummary data={tc.knowledge} conversationId={conversationId} />}
      {/* 滑动展开的明细(参数/返回) */}
      <SlideCollapse open={expanded}>
        <div className="ml-9 mr-2 mb-1.5 space-y-1.5">
          {argsStr && (
            <div>
              <span className="text-[10px] font-medium text-muted-foreground">参数</span>
              <pre className="mt-0.5 p-1.5 bg-muted rounded text-[10px] whitespace-pre-wrap break-all" style={{ maxHeight: 100, overflowY: 'auto' }}>
                {argsStr}
              </pre>
            </div>
          )}
          {(tc.result || tc.result_preview) && (
            <div>
              <span className="text-[10px] font-medium text-muted-foreground">
                {failed ? '❌ 错误' : '返回结果'}
              </span>
              <pre className="mt-0.5 p-1.5 bg-muted rounded text-[10px] whitespace-pre-wrap break-all" style={{ maxHeight: 120, overflowY: 'auto' }}>
                {tc.error || tc.result || tc.result_preview}
              </pre>
            </div>
          )}
          {/* 明细被入库投影截断或被保留策略裁剪时显式告知，不静默少内容（no-silent-degradation）*/}
          {(tc.result_truncated || tc.result_omitted) && (
            <p className="text-[10px] text-muted-foreground">
              {tc.result_omitted
                ? '工具返回明细不持久化（仅保留摘要），完整产物见会话工作区文件。'
                : '工具返回明细过长，仅保存开头部分。'}
            </p>
          )}
        </div>
      </SlideCollapse>
    </div>
  );
}

// ── Grouped Tool Items ─────────────────────────────────────────
// 导出供 ProcessTimeline 有序时间线复用(相邻同工具调用折叠成组)
export function GroupedToolItems({ group, conversationId }: { group: ToolCallGroup; conversationId?: number | null }) {
  const [expanded, setExpanded] = useState(false);
  const okCount = group.items.filter(t => t.result && !t.error).length;
  const failCount = group.items.filter(t => t.error).length;
  const pendingCount = group.items.filter(t => !t.result && !t.error).length;

  return (
    <div className="border rounded-lg bg-background mb-1 overflow-hidden">
      <div
        className="flex items-center gap-2 px-2.5 py-1.5 cursor-pointer hover:bg-muted/50 transition-colors text-xs"
        onClick={() => setExpanded(!expanded)}
      >
        <Wrench className="h-3 w-3 text-muted-foreground" />
        <span className="font-medium">{getToolLabel(group.tool)}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground font-medium">
          ×{group.items.length}
        </span>
        <span className="flex items-center gap-1.5 ml-auto text-[10px]">
          {okCount > 0 && <span className="text-green-500">✓{okCount}</span>}
          {failCount > 0 && <span className="text-destructive">✗{failCount}</span>}
          {pendingCount > 0 && <span className="text-muted-foreground">⏳{pendingCount}</span>}
        </span>
        {expanded ? <ChevronDown className="h-3 w-3 text-muted-foreground" /> : <ChevronRight className="h-3 w-3 text-muted-foreground" />}
      </div>
      <SlideCollapse open={expanded}>
        <div className="border-t px-1 py-0.5">
          {group.items.map((tc, i) => (
            <SingleToolItem key={i} tc={tc} conversationId={conversationId} />
          ))}
        </div>
      </SlideCollapse>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────

interface Props {
  conversationId?: number | null;
  toolCalls?: ToolCall[];
  progressStages?: ProgressStage[];
  isStreaming?: boolean;
}

export default function ToolCallTimeline({ toolCalls, progressStages, isStreaming = false, conversationId }: Props) {
  // Build tool calls from progressStages if toolCalls not available
  const effectiveToolCalls = useMemo(() => {
    if (toolCalls && toolCalls.length > 0) return toolCalls;

    // Fallback: extract from progressStages
    if (!progressStages || progressStages.length === 0) return [];

    const execStages = progressStages.filter(s => s.stage === 'agent_exec');
    if (execStages.length === 0) return [];

    return execStages.map((s, i) => ({
      step: s.step || i + 1,
      tool: s.message?.split(' ')[0] || 'unknown',
      arguments: undefined,
      result: undefined,
      result_preview: s.message,
      elapsed: s.elapsed,
    }));
  }, [toolCalls, progressStages]);

  if (effectiveToolCalls.length === 0) return null;

  const groups = groupToolCalls(effectiveToolCalls);

  // If only one group with one item, show it directly without group wrapper
  const showFlat = groups.length === 1 && groups[0].items.length === 1;

  return (
    <div className="mb-3">
      {/* Header */}
      <div className="flex items-center gap-2 mb-1.5">
        <Wrench className="h-3.5 w-3.5 text-blue-500" />
        <span className="text-xs font-medium text-foreground">
          工具调用 · {effectiveToolCalls.length} 步
        </span>
        {isStreaming && (
          <Loader2 className="h-3 w-3 animate-spin text-primary" />
        )}
      </div>

      {/* Timeline */}
      <div className="space-y-0.5">
        {showFlat ? (
          <SingleToolItem tc={{ ...groups[0].items[0], _index: 0 }} conversationId={conversationId} />
        ) : (
          groups.map((group, i) =>
            group.items.length === 1 ? (
              <SingleToolItem key={i} tc={{ ...group.items[0], _index: 0 }} conversationId={conversationId} />
            ) : (
              <GroupedToolItems key={i} group={group} conversationId={conversationId} />
            )
          )
        )}
      </div>
    </div>
  );
}
