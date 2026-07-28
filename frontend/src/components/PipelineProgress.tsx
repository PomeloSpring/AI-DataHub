/**
 * PipelineProgress — Unified progress visualization for Quick-Deep pipeline.
 *
 * Renders a horizontal pipeline with stage nodes. Automatically adapts
 * visible stages based on the active mode (quick shows fewer stages).
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Cpu, CheckSquare, Play, CheckCircle, Loader2,
  AlertCircle, Database, FileSearch, Zap, Target,
} from 'lucide-react';
import { PIPELINE_STAGES, STAGE_TIMING_MAP, type PipelineMode } from '../config/pipeline';
import type { PipelineStageState } from '../stores/pipelineStore';

// ── Stage Icons ─────────────────────────────────────────────────

const STAGE_ICONS: Record<string, React.ReactNode> = {
  intent: <Target className="h-3.5 w-3.5" />,
  rag: <Database className="h-3.5 w-3.5" />,
  metadata_supplement: <FileSearch className="h-3.5 w-3.5" />,
  llm: <Cpu className="h-3.5 w-3.5" />,
  validate: <CheckSquare className="h-3.5 w-3.5" />,
  execute: <Play className="h-3.5 w-3.5" />,
  interpret: <Zap className="h-3.5 w-3.5" />,
};

// ── Props ───────────────────────────────────────────────────────

interface Props {
  /** Current active stage key */
  activeStage?: string | null;
  /** Progress message from SSE */
  message?: string;
  /** Stage history from progress events */
  stages?: { stage: string; message: string; timestamp: number }[];
  /** Timings from done event (seconds) */
  timings?: Record<string, number>;
  /** Pipeline mode from backend */
  mode?: PipelineMode;
  /** Stage states from pipelineStore (optional, computed from stages if omitted) */
  stageStates?: Record<string, PipelineStageState>;
  /** Compact mode: progress bar + message only */
  compact?: boolean;
}

// ── Component ───────────────────────────────────────────────────

export default function PipelineProgress({
  activeStage, message, stages = [], timings, mode = 'auto', stageStates: propStates, compact = false,
}: Props) {
  const [computedStates, setComputedStates] = useState<Record<string, PipelineStageState>>({});

  // Compute stage states from stages history + timings + activeStage
  useEffect(() => {
    if (propStates) return; // Use provided states

    const states: Record<string, PipelineStageState> = {};
    for (const s of PIPELINE_STAGES) {
      states[s.key] = { status: 'pending' };
    }

    // Mark completed stages from history
    for (const h of stages) {
      const key = normalizeStage(h.stage);
      if (key && states[key]) {
        states[key] = { status: 'done', message: h.message };
      }
    }

    // Apply timings
    if (timings) {
      for (const [stageKey, timingKey] of Object.entries(STAGE_TIMING_MAP)) {
        if (timings[timingKey] !== undefined && states[stageKey]) {
          states[stageKey] = { ...states[stageKey], status: 'done', elapsed: timings[timingKey] };
        }
      }
    }

    // Mark active stage
    if (activeStage) {
      const key = normalizeStage(activeStage);
      if (key && states[key] && states[key].status !== 'done') {
        states[key] = { status: 'active', message };
      }
    }

    // If no stages recorded yet, mark prior stages as done
    if (activeStage && stages.length === 0 && !timings) {
      const activeIdx = PIPELINE_STAGES.findIndex(s => s.key === normalizeStage(activeStage));
      for (let i = 0; i < activeIdx; i++) {
        if (states[PIPELINE_STAGES[i].key]?.status === 'pending') {
          states[PIPELINE_STAGES[i].key] = { status: 'done' };
        }
      }
    }

    setComputedStates(states);
  }, [activeStage, message, stages, timings, propStates]);

  const states = propStates || computedStates;

  // Determine effective mode for filtering stages
  const effectiveMode = useMemo(() => {
    if (mode !== 'auto') return mode;
    // In auto mode, infer from which stages are active/done
    const hasRagActivity = states.rag?.status !== 'pending' || states.metadata_supplement?.status !== 'pending';
    return hasRagActivity ? 'deep' : 'quick';
  }, [mode, states]);

  // Filter stages to only show relevant ones
  const visibleStages = useMemo(() => {
    return PIPELINE_STAGES.filter(s => {
      // Always show stages that are active or done
      if (states[s.key]?.status === 'active' || states[s.key]?.status === 'done' || states[s.key]?.status === 'error') {
        return true;
      }
      // Show stages for the effective mode
      return s.modes.includes(effectiveMode);
    });
  }, [effectiveMode, states]);

  if (compact) {
    return <CompactProgress stages={visibleStages} states={states} activeStage={activeStage} message={message} />;
  }

  return (
    <div className="w-full py-2">
      <div className="flex items-center gap-0">
        {visibleStages.map((stage, i) => {
          const state = states[stage.key] || { status: 'pending' };
          const isLast = i === visibleStages.length - 1;

          return (
            <div key={stage.key} className="flex items-center flex-1 min-w-0">
              <div className="flex flex-col items-center min-w-0">
                <StageNode stageKey={stage.key} label={stage.label} state={state} />
              </div>
              {!isLast && (
                <div className="flex-1 mx-1 h-0.5 min-w-[16px]">
                  <div className={`h-full rounded-full transition-colors duration-500 ${
                    state.status === 'done' ? 'bg-green-400' :
                    state.status === 'active' ? 'bg-gradient-to-r from-primary to-primary/30' :
                    'bg-muted-foreground/20'
                  }`} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {activeStage && message && (
        <div className="mt-2 flex items-center gap-2 px-1">
          <Loader2 className="h-3 w-3 animate-spin text-primary" />
          <span className="text-xs text-muted-foreground truncate">{message}</span>
        </div>
      )}
    </div>
  );
}

// ── Stage Node ──────────────────────────────────────────────────

function StageNode({ stageKey, label, state }: {
  stageKey: string;
  label: string;
  state: PipelineStageState;
}) {
  const isActive = state.status === 'active';
  const isDone = state.status === 'done';
  const isError = state.status === 'error';
  const icon = STAGE_ICONS[stageKey] || <Cpu className="h-3.5 w-3.5" />;

  return (
    <div className={`flex items-center gap-1.5 px-2 py-1 rounded-md transition-all duration-300 ${
      isActive ? 'bg-primary/10 shadow-sm' :
      isDone ? 'bg-green-50 dark:bg-green-950/30' :
      isError ? 'bg-destructive/10' :
      'opacity-50'
    }`}>
      <div className={`flex-shrink-0 ${isActive ? 'animate-pulse' : ''}`}>
        {isDone ? (
          <CheckCircle className="h-3.5 w-3.5 text-green-500" />
        ) : isError ? (
          <AlertCircle className="h-3.5 w-3.5 text-destructive" />
        ) : isActive ? (
          <Loader2 className="h-3.5 w-3.5 text-primary animate-spin" />
        ) : (
          <span className="text-muted-foreground">{icon}</span>
        )}
      </div>
      <span className={`text-xs font-medium whitespace-nowrap ${
        isActive ? 'text-primary' :
        isDone ? 'text-green-600 dark:text-green-400' :
        isError ? 'text-destructive' :
        'text-muted-foreground'
      }`}>
        {label}
      </span>
      {isDone && state.elapsed !== undefined && (
        <span className="text-[10px] text-muted-foreground ml-0.5">
          {state.elapsed < 1 ? `${Math.round(state.elapsed * 1000)}ms` : `${state.elapsed.toFixed(1)}s`}
        </span>
      )}
    </div>
  );
}

// ── Compact Progress ────────────────────────────────────────────

function CompactProgress({
  stages, states, activeStage, message,
}: {
  stages: { key: string; label: string }[];
  states: Record<string, PipelineStageState>;
  activeStage?: string | null;
  message?: string;
}) {
  const total = stages.length;
  const doneCount = stages.filter(s => states[s.key]?.status === 'done').length;
  const progressPct = total > 0 ? Math.round(((doneCount + (activeStage ? 0.5 : 0)) / total) * 100) : 0;

  return (
    <div className="w-full">
      <div className="flex items-center gap-2 mb-1.5">
        <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-primary to-primary/70 rounded-full transition-all duration-700 ease-out"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">
          {doneCount}/{total}
        </span>
      </div>
      {message && (
        <div className="flex items-center gap-1.5">
          <Loader2 className="h-3 w-3 animate-spin text-primary shrink-0" />
          <span className="text-xs text-muted-foreground truncate">{message}</span>
        </div>
      )}
    </div>
  );
}

// ── Stage Normalization ─────────────────────────────────────────

function normalizeStage(stage: string): string | null {
  const map: Record<string, string> = {
    // Backend stage names → our stage keys
    intent: 'intent',
    rag: 'rag',
    metadata_retrieval: 'rag',
    metadata_supplement: 'metadata_supplement',
    llm: 'llm',
    llm_analysis: 'llm',
    sql_generation: 'llm',
    sql_retry: 'llm',
    validate: 'validate',
    sql_execution: 'execute',
    execute: 'execute',
    interpret: 'interpret',
    result_analysis: 'interpret',
    completed: 'execute',
  };
  return map[stage] || null;
}
