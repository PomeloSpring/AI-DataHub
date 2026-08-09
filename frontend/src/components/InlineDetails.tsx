/**
 * InlineDetails — Collapsible inline detail cards for assistant messages.
 *
 * Replaces the execution details drawer with inline collapsible sections:
 * - RAG retrieval summary
 * - Step-by-step timings
 * - Workflow info (Deep mode)
 */

import { useState } from 'react';
import {
  Database, FileSearch, Lightbulb, Clock, Workflow,
  ChevronRight, ChevronDown,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';

// ── RAG Summary ────────────────────────────────────────────────

interface RagData {
  rag_source?: string;
  table_info_count?: number;
  column_metadata_count?: number;
  sql_templates_count?: number;
  business_terms_count?: number;
  table_info?: any[];
  column_metadata?: any[];
}

function RagSummary({ rag }: { rag: RagData }) {
  const [expanded, setExpanded] = useState(false);

  const total = (rag.table_info_count || 0) + (rag.column_metadata_count || 0)
    + (rag.sql_templates_count || 0) + (rag.business_terms_count || 0);

  if (total === 0) return null;

  const sourceLabel = rag.rag_source === 'keyword_selected' ? '关键词匹配'
    : rag.rag_source === 'vector_search' ? '向量检索'
    : rag.rag_source || '';

  return (
    <div className="border rounded-lg bg-background mb-2 overflow-hidden">
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={() => setExpanded(prev => !prev)}
      >
        <Database className="h-3.5 w-3.5 text-blue-500 shrink-0" />
        <span className="text-xs font-medium">RAG 检索</span>
        <span className="text-xs text-muted-foreground truncate flex-1 min-w-0">
          {sourceLabel && `${sourceLabel} · `}
          匹配 {rag.table_info_count || 0} 张表、{rag.column_metadata_count || 0} 个字段
        </span>
        {expanded
          ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        }
      </div>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t">
          {/* Stats badges */}
          <div className="flex gap-1.5 flex-wrap pt-2">
            {(rag.table_info_count || 0) > 0 && (
              <Badge variant="outline" className="text-[10px]">
                <Database className="h-2.5 w-2.5 mr-0.5" />表 {rag.table_info_count}
              </Badge>
            )}
            {(rag.column_metadata_count || 0) > 0 && (
              <Badge variant="outline" className="text-[10px]">
                <Database className="h-2.5 w-2.5 mr-0.5" />字段 {rag.column_metadata_count}
              </Badge>
            )}
            {(rag.sql_templates_count || 0) > 0 && (
              <Badge variant="outline" className="text-[10px]">
                <FileSearch className="h-2.5 w-2.5 mr-0.5" />SQL 模板 {rag.sql_templates_count}
              </Badge>
            )}
            {(rag.business_terms_count || 0) > 0 && (
              <Badge variant="outline" className="text-[10px]">
                <Lightbulb className="h-2.5 w-2.5 mr-0.5" />术语 {rag.business_terms_count}
              </Badge>
            )}
          </div>

          {/* Matched tables */}
          {rag.table_info && rag.table_info.length > 0 && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground mb-1">匹配的表</p>
              <div className="flex flex-wrap gap-1">
                {rag.table_info.map((t: any, i: number) => (
                  <Badge key={i} variant="secondary" className="text-[10px]">
                    {t.table_name}{t.table_comment ? ` (${t.table_comment})` : ''}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {/* Matched columns */}
          {rag.column_metadata && rag.column_metadata.length > 0 && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground mb-1">匹配的字段</p>
              <div className="flex flex-wrap gap-1">
                {rag.column_metadata.map((c: any, i: number) => (
                  <Badge key={i} variant="outline" className="text-[10px]">
                    {c.table_name}.{c.column_name}{c.column_comment ? ` (${c.column_comment})` : ''}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Timings ────────────────────────────────────────────────────

const TIMING_LABELS: Record<string, string> = {
  intent: '意图识别',
  rag: 'RAG 检索',
  llm: 'SQL 生成',
  validate: 'SQL 校验',
  execute: 'SQL 执行',
  total: '合计',
};

const TIMING_ORDER = ['intent', 'rag', 'llm', 'validate', 'execute', 'total'] as const;

function TimingsView({ timings, elapsedMs }: { timings: Record<string, number>; elapsedMs?: number }) {
  const keys = TIMING_ORDER.filter(k => k in timings);
  if (keys.length === 0) return null;

  return (
    <div className="border rounded-lg bg-background mb-2 px-3 py-2">
      <div className="flex items-center gap-2 mb-2">
        <Clock className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs font-medium">分步耗时</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1">
        {keys.map(key => (
          <div key={key} className={`flex justify-between text-xs ${key === 'total' ? 'border-t pt-1 col-span-full' : ''}`}>
            <span className="text-muted-foreground">{TIMING_LABELS[key]}</span>
            <span className="font-medium tabular-nums">
              {typeof timings[key] === 'number' ? `${timings[key]}s` : timings[key]}
            </span>
          </div>
        ))}
        {elapsedMs !== undefined && (
          <div className="flex justify-between text-xs col-span-full">
            <span className="text-muted-foreground">前端总耗时</span>
            <span className="font-medium tabular-nums">{(elapsedMs / 1000).toFixed(1)}s</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Workflow Info ───────────────────────────────────────────────

function WorkflowInfo({ info }: { info: any }) {
  if (!info) return null;

  return (
    <div className="border rounded-lg bg-background mb-2 px-3 py-2">
      <div className="flex items-center gap-2 mb-1.5">
        <Workflow className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs font-medium">工作流信息</span>
      </div>
      <div className="space-y-1 text-xs">
        {info.name && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">工作流</span>
            <span className="font-medium">{info.name}</span>
          </div>
        )}
        {info.rounds_used !== undefined && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">执行轮数</span>
            <span className="font-medium">{info.rounds_used}</span>
          </div>
        )}
        {info.loop_count !== undefined && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">元数据循环</span>
            <span className="font-medium">{info.loop_count} 次</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────

interface Props {
  rag?: RagData;
  timings?: Record<string, number>;
  elapsedMs?: number;
  workflowInfo?: any;
}

export default function InlineDetails({ rag, timings, elapsedMs, workflowInfo }: Props) {
  const hasRag = rag && ((rag.table_info_count || 0) > 0 || (rag.column_metadata_count || 0) > 0);
  const hasTimings = timings && Object.keys(timings).length > 0;
  const hasWorkflow = !!workflowInfo;

  if (!hasRag && !hasTimings && !hasWorkflow) return null;

  return (
    <div className="mb-2">
      {hasTimings && <TimingsView timings={timings!} elapsedMs={elapsedMs} />}
      {hasRag && <RagSummary rag={rag!} />}
      {hasWorkflow && <WorkflowInfo info={workflowInfo} />}
    </div>
  );
}
