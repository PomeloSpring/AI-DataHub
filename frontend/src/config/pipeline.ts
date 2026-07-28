/**
 * Pipeline Configuration — Constants and stage definitions for Quick-Deep pipeline.
 */

// ── Pipeline Modes ──────────────────────────────────────────────

export type PipelineMode = 'auto' | 'quick' | 'deep' | 'agent';

export const PIPELINE_MODES: Record<PipelineMode, { label: string; description: string }> = {
  auto: { label: '自动', description: '根据问题复杂度自动选择模式' },
  quick: { label: '快速', description: '跳过元数据检索，直接生成 SQL（~2s）' },
  deep: { label: '深度', description: '完整 RAG + Loop Engineering（~30s）' },
  agent: { label: 'Agent', description: 'LLM 自主决策检索和生成策略（~60s）' },
};

// ── Stage Definitions ───────────────────────────────────────────

export interface PipelineStageDef {
  key: string;
  label: string;
  modes: PipelineMode[];  // Which modes show this stage
}

/**
 * All pipeline stages in execution order.
 * Quick mode only shows a subset; Deep mode shows all.
 */
export const PIPELINE_STAGES: PipelineStageDef[] = [
  { key: 'intent',              label: '意图分析',     modes: ['quick'] },
  { key: 'rag',                 label: '元数据检索',   modes: ['deep', 'agent'] },
  { key: 'metadata_supplement', label: '元数据补充',   modes: ['deep'] },
  { key: 'agent_plan',          label: 'Agent 规划',   modes: ['agent'] },
  { key: 'agent_exec',          label: 'Agent 执行',   modes: ['agent'] },
  { key: 'llm',                 label: 'SQL 生成',     modes: ['quick', 'deep', 'agent'] },
  { key: 'validate',            label: 'SQL 校验',     modes: ['quick', 'deep', 'agent'] },
  { key: 'execute',             label: 'SQL 执行',     modes: ['quick', 'deep', 'agent'] },
  { key: 'interpret',           label: '结果解读',     modes: ['deep', 'agent'] },
];

/** Get stages visible for a given mode. */
export function getStagesForMode(mode: PipelineMode): PipelineStageDef[] {
  if (mode === 'auto') return PIPELINE_STAGES; // show all during auto
  return PIPELINE_STAGES.filter(s => s.modes.includes(mode));
}

// ── Timeouts ────────────────────────────────────────────────────

export const PIPELINE_TIMEOUTS = {
  quick: 30_000,    // 30s
  deep: 300_000,    // 5min
  agent: 600_000,   // 10min (agent may iterate)
  auto: 300_000,    // 5min (worst case = deep)
} as const;

// ── Stage → Timing Key Map ──────────────────────────────────────

export const STAGE_TIMING_MAP: Record<string, string> = {
  rag: 'rag',
  metadata_supplement: 'rag',
  llm: 'llm',
  validate: 'validate',
  execute: 'execute',
};
