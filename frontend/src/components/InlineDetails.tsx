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
  ChevronDown,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { Badge } from '@/components/ui/badge';
import { SlideCollapse } from './chat/StageCard';
import { useEnterClass } from '@/hooks/useMotion';
import client from '@/api/client';

export interface KnowledgeChunk {
  title: string;
  excerpt: string;
  freshness: 'current' | 'stale' | 'unknown';
  citation_ref?: string;
  version?: string;
  excerpt_truncated?: boolean;
}

export interface KnowledgeResult {
  status?: string;
  message?: string;
  error_code?: string;
  question?: string;
  count?: number;
  incomplete?: boolean;
  degraded?: boolean;
  warnings?: string[];
  chunks?: KnowledgeChunk[];
  results?: KnowledgeResult[];
}

function CitationCard({ chunk, conversationId }: { chunk: KnowledgeChunk; conversationId?: number | null }) {
  const { t } = useTranslation('chat');
  const [detail, setDetail] = useState<KnowledgeChunk | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const read = async () => {
    setLoading(true);
    setDetail(null);
    setError('');
    try {
      const { data } = await client.get(`/chat/conversations/${conversationId}/citations/${encodeURIComponent(chunk.citation_ref!)}`);
      setDetail(data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || t('inlineDetails.citationReadFailed'));
    } finally {
      setLoading(false);
    }
  };
  const shown = detail || chunk;
  const label = shown.freshness === 'current' ? (detail ? t('inlineDetails.freshnessCurrentVerified') : t('inlineDetails.freshnessCurrentAtSearch'))
    : shown.freshness === 'stale' ? t('inlineDetails.freshnessStale') : t('inlineDetails.freshnessUnknown');
  return <div className="rounded border p-2 space-y-1 text-xs">
    <div className="flex gap-2 items-center"><span className="font-medium">{shown.title}</span>
      <Badge variant="outline">{label}</Badge></div>
    {shown.version && <p className="text-muted-foreground break-all">{t('inlineDetails.version', { version: shown.version })}</p>}
    {error ? <p role="alert" className="text-destructive">{error}</p>
      : <p className="whitespace-pre-wrap break-words max-h-80 overflow-auto">{shown.excerpt}</p>}
    {!error && !detail && chunk.excerpt_truncated && <p className="text-muted-foreground">{t('inlineDetails.excerptTruncated')}</p>}
    {chunk.citation_ref && conversationId && <button type="button" disabled={loading} className="text-primary underline"
      onClick={read}>{loading ? t('inlineDetails.verifying') : t('inlineDetails.verifyFull')}</button>}
  </div>;
}

export function KnowledgeSummary({ data, conversationId }: { data: KnowledgeResult; conversationId?: number | null }) {
  const { t } = useTranslation('chat');
  return <div className="border rounded-lg p-3 mb-2 space-y-2" aria-label={t('inlineDetails.knowledgeAria')}>
    <p className="text-xs font-medium">{t('inlineDetails.knowledgeTitle')}{data.incomplete ? t('inlineDetails.knowledgePartial') : ''}</p>
    {data.results && data.message && <p role={data.status === 'error' || data.incomplete ? 'alert' : undefined} className="text-xs">{data.message}</p>}
    {(data.results || [data]).map((item, i) => <div key={i} className="space-y-2">
      {item.question && <p className="text-xs font-medium">{item.question}</p>}
      {item.status === 'error' && <p role="alert" className="text-xs text-destructive">{item.error_code ? `${item.error_code}: ` : ''}{item.message || t('inlineDetails.knowledgeIncomplete')}</p>}
      {item.status === 'empty' && <p className="text-xs text-muted-foreground">{t('inlineDetails.knowledgeEmpty')}</p>}
      {item.degraded && <p className="text-xs text-amber-600">{t('inlineDetails.knowledgeDegraded')}</p>}
      {item.warnings?.map((warning, index) => <p className="text-xs text-amber-600" key={index}>{warning}</p>)}
      {item.chunks?.map((chunk, index) => <CitationCard key={`${conversationId || 0}-${chunk.citation_ref || index}-${chunk.version || ''}-${i}`} chunk={chunk} conversationId={conversationId} />)}
    </div>)}
  </div>;
}

// ── RAG Summary ────────────────────────────────────────────────

interface RagData extends KnowledgeResult {
  rag_source?: string;
  table_info_count?: number;
  column_metadata_count?: number;
  datasets_count?: number;
  business_terms_count?: number;
  table_info?: any[];
  column_metadata?: any[];
}

function RagSummary({ rag }: { rag: RagData }) {
  const { t } = useTranslation('chat');
  // 出现效果跟随个人动效偏好（默认档 = 改造前的 animate-stage-in）
  const enter = useEnterClass();
  const [expanded, setExpanded] = useState(false);

  const total = (rag.table_info_count || 0) + (rag.column_metadata_count || 0)
    + (rag.datasets_count || 0) + (rag.business_terms_count || 0);

  if (rag.status || rag.chunks || rag.results) return <KnowledgeSummary data={rag} />;
  if (total === 0) return null;

  const sourceLabel = rag.rag_source === 'keyword_selected' ? t('inlineDetails.ragKeyword')
    : rag.rag_source === 'vector_search' ? t('inlineDetails.ragVector')
    : rag.rag_source || '';

  return (
    <div className={`rounded-xl border bg-card shadow-sm mb-2 overflow-hidden ${enter}`}>
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-muted/40 transition-colors"
        onClick={() => setExpanded(prev => !prev)}
      >
        <span className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-blue-500/10">
          <Database className="h-3.5 w-3.5 text-blue-500" />
        </span>
        <span className="text-xs font-medium">{t('inlineDetails.ragTitle')}</span>
        <span className="text-xs text-muted-foreground truncate flex-1 min-w-0">
          {sourceLabel && `${sourceLabel} · `}
          {t('inlineDetails.ragMatched', { tables: rag.table_info_count || 0, columns: rag.column_metadata_count || 0 })}
        </span>
        <ChevronDown
          className={`h-3.5 w-3.5 text-muted-foreground shrink-0 transition-transform duration-300 ${expanded ? 'rotate-180' : ''}`}
        />
      </div>

      <SlideCollapse open={expanded}>
        <div className="px-3 pb-3 space-y-2 border-t">
          {/* Stats badges */}
          <div className="flex gap-1.5 flex-wrap pt-2">
            {(rag.table_info_count || 0) > 0 && (
              <Badge variant="outline" className="text-[10px]">
                <Database className="h-2.5 w-2.5 mr-0.5" />{t('inlineDetails.badgeTables', { count: rag.table_info_count })}
              </Badge>
            )}
            {(rag.column_metadata_count || 0) > 0 && (
              <Badge variant="outline" className="text-[10px]">
                <Database className="h-2.5 w-2.5 mr-0.5" />{t('inlineDetails.badgeColumns', { count: rag.column_metadata_count })}
              </Badge>
            )}
            {(rag.datasets_count || 0) > 0 && (
              <Badge variant="outline" className="text-[10px]">
                <FileSearch className="h-2.5 w-2.5 mr-0.5" />{t('inlineDetails.badgeDatasets', { count: rag.datasets_count })}
              </Badge>
            )}
            {(rag.business_terms_count || 0) > 0 && (
              <Badge variant="outline" className="text-[10px]">
                <Lightbulb className="h-2.5 w-2.5 mr-0.5" />{t('inlineDetails.badgeTerms', { count: rag.business_terms_count })}
              </Badge>
            )}
          </div>

          {/* Matched tables */}
          {rag.table_info && rag.table_info.length > 0 && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground mb-1">{t('inlineDetails.matchedTables')}</p>
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
              <p className="text-[10px] font-medium text-muted-foreground mb-1">{t('inlineDetails.matchedColumns')}</p>
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
      </SlideCollapse>
    </div>
  );
}

// ── Timings ────────────────────────────────────────────────────

/** 耗时项只存 key：文案在渲染期经 i18n.t 取（模块顶层常量会冻结语言） */
const TIMING_LABEL_KEYS: Record<string, string> = {
  intent: 'inlineDetails.timing.intent',
  rag: 'inlineDetails.timing.rag',
  llm: 'inlineDetails.timing.llm',
  validate: 'inlineDetails.timing.validate',
  execute: 'inlineDetails.timing.execute',
  total: 'inlineDetails.timing.total',
};

const TIMING_ORDER = ['intent', 'rag', 'llm', 'validate', 'execute', 'total'] as const;

function TimingsView({ timings, elapsedMs }: { timings: Record<string, number>; elapsedMs?: number }) {
  const { t } = useTranslation('chat');
  const enter = useEnterClass();
  const keys = TIMING_ORDER.filter(k => k in timings);
  if (keys.length === 0) return null;

  return (
    <div className={`rounded-xl border bg-card shadow-sm mb-2 px-3 py-2 ${enter}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-muted">
          <Clock className="h-3.5 w-3.5 text-muted-foreground" />
        </span>
        <span className="text-xs font-medium">{t('inlineDetails.timingsTitle')}</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1">
        {keys.map(key => (
          <div key={key} className={`flex justify-between text-xs ${key === 'total' ? 'border-t pt-1 col-span-full' : ''}`}>
            <span className="text-muted-foreground">{i18n.t(`chat:${TIMING_LABEL_KEYS[key]}` as 'chat:inlineDetails.timing.intent')}</span>
            <span className="font-medium tabular-nums">
              {typeof timings[key] === 'number' ? `${timings[key]}s` : timings[key]}
            </span>
          </div>
        ))}
        {elapsedMs !== undefined && (
          <div className="flex justify-between text-xs col-span-full">
            <span className="text-muted-foreground">{t('inlineDetails.frontendTotal')}</span>
            <span className="font-medium tabular-nums">{(elapsedMs / 1000).toFixed(1)}s</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Workflow Info ───────────────────────────────────────────────

function WorkflowInfo({ info }: { info: any }) {
  const { t } = useTranslation('chat');
  const enter = useEnterClass();
  if (!info) return null;

  return (
    <div className={`rounded-xl border bg-card shadow-sm mb-2 px-3 py-2 ${enter}`}>
      <div className="flex items-center gap-2 mb-1.5">
        <span className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-muted">
          <Workflow className="h-3.5 w-3.5 text-muted-foreground" />
        </span>
        <span className="text-xs font-medium">{t('inlineDetails.workflowTitle')}</span>
      </div>
      <div className="space-y-1 text-xs">
        {info.name && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t('inlineDetails.workflowName')}</span>
            <span className="font-medium">{info.name}</span>
          </div>
        )}
        {info.rounds_used !== undefined && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t('inlineDetails.roundsUsed')}</span>
            <span className="font-medium">{info.rounds_used}</span>
          </div>
        )}
        {info.loop_count !== undefined && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t('inlineDetails.loopCount')}</span>
            <span className="font-medium">{t('inlineDetails.loopCountValue', { count: info.loop_count })}</span>
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
  const hasRag = rag && (rag.status || rag.chunks || rag.results || (rag.table_info_count || 0) > 0 || (rag.column_metadata_count || 0) > 0);
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
