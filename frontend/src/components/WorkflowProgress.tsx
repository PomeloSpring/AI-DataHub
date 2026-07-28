import { useEffect, useState } from 'react';
import {
  Cpu, CheckSquare, Play, CheckCircle, Loader2,
  AlertCircle, Database, FileSearch, Zap,
} from 'lucide-react';

export interface StageInfo {
  key: string;
  label: string;
  icon: React.ReactNode;
}

export interface StageState {
  status: 'pending' | 'active' | 'done' | 'error';
  message?: string;
  elapsed?: number; // seconds
}

// All possible stages in order
export const WORKFLOW_STAGES: StageInfo[] = [
  { key: 'rag',              label: '元数据检索', icon: <Database className="h-3.5 w-3.5" /> },
  { key: 'metadata_supplement', label: '元数据补充', icon: <FileSearch className="h-3.5 w-3.5" /> },
  { key: 'llm',              label: 'SQL 生成',   icon: <Cpu className="h-3.5 w-3.5" /> },
  { key: 'validate',         label: 'SQL 校验',   icon: <CheckSquare className="h-3.5 w-3.5" /> },
  { key: 'execute',          label: 'SQL 执行',   icon: <Play className="h-3.5 w-3.5" /> },
  { key: 'interpret',        label: '结果解读',   icon: <Zap className="h-3.5 w-3.5" /> },
];

// Map stage keys to timing keys
const STAGE_TIMING_MAP: Record<string, string> = {
  rag: 'rag',
  metadata_supplement: 'rag',
  llm: 'llm',
  validate: 'validate',
  execute: 'execute',
};

interface Props {
  /** Current active stage key */
  activeStage?: string;
  /** Progress message from SSE */
  message?: string;
  /** Stage history: array of { stage, message, timestamp } */
  stages?: { stage: string; message: string; timestamp: number }[];
  /** Timings from done event (seconds) */
  timings?: Record<string, number>;
  /** Compact mode for inline display */
  compact?: boolean;
}

export default function WorkflowProgress({
  activeStage, message, stages = [], timings, compact = false,
}: Props) {
  const [stageStates, setStageStates] = useState<Record<string, StageState>>({});

  useEffect(() => {
    const states: Record<string, StageState> = {};

    // Initialize all stages as pending
    for (const s of WORKFLOW_STAGES) {
      states[s.key] = { status: 'pending' };
    }

    // Mark completed stages based on history
    const seenStages = new Set<string>();
    for (const h of stages) {
      const stageKey = normalizeStage(h.stage);
      if (stageKey && states[stageKey]) {
        states[stageKey] = {
          status: 'done',
          message: h.message,
        };
        seenStages.add(stageKey);
      }
    }

    // If we have timings, mark those stages as done with elapsed time
    if (timings) {
      for (const [stageKey, timingKey] of Object.entries(STAGE_TIMING_MAP)) {
        if (timings[timingKey] !== undefined && states[stageKey]) {
          states[stageKey] = {
            ...states[stageKey],
            status: 'done',
            elapsed: timings[timingKey],
          };
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

    // If no stages recorded yet and we have an active stage, mark prior stages as done
    if (activeStage && stages.length === 0 && !timings) {
      const activeIdx = WORKFLOW_STAGES.findIndex(s => s.key === normalizeStage(activeStage));
      for (let i = 0; i < activeIdx; i++) {
        if (states[WORKFLOW_STAGES[i].key]?.status === 'pending') {
          states[WORKFLOW_STAGES[i].key] = { status: 'done' };
        }
      }
    }

    setStageStates(states);
  }, [activeStage, message, stages, timings]);

  // Filter to only show relevant stages (skip metadata_supplement if not used, skip interpret if not used)
  const visibleStages = WORKFLOW_STAGES.filter(s => {
    if (s.key === 'metadata_supplement' && stageStates[s.key]?.status === 'pending') return false;
    if (s.key === 'interpret' && stageStates[s.key]?.status === 'pending') return false;
    return true;
  });

  if (compact) {
    return <CompactProgress stages={visibleStages} states={stageStates} activeStage={activeStage} message={message} />;
  }

  return (
    <div className="w-full py-2">
      <div className="flex items-center gap-0">
        {visibleStages.map((stage, i) => {
          const state = stageStates[stage.key] || { status: 'pending' };
          const isLast = i === visibleStages.length - 1;

          return (
            <div key={stage.key} className="flex items-center flex-1 min-w-0">
              {/* Stage node */}
              <div className="flex flex-col items-center min-w-0">
                <StageNode stage={stage} state={state} />
              </div>

              {/* Connector line */}
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

      {/* Active stage message */}
      {activeStage && message && (
        <div className="mt-2 flex items-center gap-2 px-1">
          <Loader2 className="h-3 w-3 animate-spin text-primary" />
          <span className="text-xs text-muted-foreground truncate">{message}</span>
        </div>
      )}
    </div>
  );
}

function StageNode({ stage, state }: { stage: StageInfo; state: StageState }) {
  const isActive = state.status === 'active';
  const isDone = state.status === 'done';
  const isError = state.status === 'error';

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
          <span className="text-muted-foreground">{stage.icon}</span>
        )}
      </div>
      <span className={`text-xs font-medium whitespace-nowrap ${
        isActive ? 'text-primary' :
        isDone ? 'text-green-600 dark:text-green-400' :
        isError ? 'text-destructive' :
        'text-muted-foreground'
      }`}>
        {stage.label}
      </span>
      {isDone && state.elapsed !== undefined && (
        <span className="text-[10px] text-muted-foreground ml-0.5">
          {state.elapsed < 1 ? `${Math.round(state.elapsed * 1000)}ms` : `${state.elapsed.toFixed(1)}s`}
        </span>
      )}
    </div>
  );
}

function CompactProgress({
  stages, states, activeStage, message,
}: {
  stages: StageInfo[];
  states: Record<string, StageState>;
  activeStage?: string;
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

/** Normalize various stage names from backend to our stage keys */
function normalizeStage(stage: string): string | null {
  const map: Record<string, string> = {
    rag: 'rag',
    metadata_retrieval: 'rag',
    metadata_supplement: 'metadata_supplement',
    llm: 'llm',
    llm_analysis: 'llm',
    sql_generation: 'llm',
    sql_retry: 'llm',  // SQL self-correction retry
    validate: 'validate',
    sql_execution: 'execute',
    execute: 'execute',
    interpret: 'interpret',
    result_analysis: 'interpret',
    completed: 'execute', // terminal
  };
  return map[stage] || null;
}
