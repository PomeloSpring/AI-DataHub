/**
 * Pipeline Monitoring — Performance metrics and alert thresholds.
 */

import type { PipelineMode } from './pipeline';

// ── Metrics Types ───────────────────────────────────────────────

export interface PipelineMetric {
  mode: PipelineMode;
  startTime: number;
  endTime: number;
  elapsed: number;
  success: boolean;
  fallbackUsed: boolean;
  stageTimings: Record<string, number>;
  tokenCount?: { input: number; output: number; total: number };
}

// ── Alert Thresholds ────────────────────────────────────────────

export const PIPELINE_THRESHOLDS = {
  /** Quick mode P95 target in ms */
  quickP95Target: 3_000,
  /** Deep mode P95 target in ms */
  deepP95Target: 30_000,
  /** Warn if quick mode exceeds this (ms) */
  quickWarnMs: 5_000,
  /** Warn if deep mode exceeds this (ms) */
  deepWarnMs: 45_000,
} as const;

// ── Metrics Collector ───────────────────────────────────────────

const _metrics: PipelineMetric[] = [];
const MAX_METRICS = 200;

export function recordMetric(metric: PipelineMetric): void {
  _metrics.push(metric);
  if (_metrics.length > MAX_METRICS) {
    _metrics.splice(0, _metrics.length - MAX_METRICS);
  }

  // Check thresholds
  const threshold = metric.mode === 'quick'
    ? PIPELINE_THRESHOLDS.quickWarnMs
    : PIPELINE_THRESHOLDS.deepWarnMs;

  if (metric.elapsed > threshold) {
    console.warn(
      `[Pipeline] Slow ${metric.mode} mode: ${metric.elapsed}ms (threshold: ${threshold}ms)`,
      metric.stageTimings,
    );
  }
}

export function getMetrics(): PipelineMetric[] {
  return [..._metrics];
}

export function getMetricsSummary(): {
  total: number;
  byMode: Record<string, { count: number; avgMs: number; p95Ms: number; fallbackRate: number }>;
} {
  const byMode: Record<string, PipelineMetric[]> = {};
  for (const m of _metrics) {
    if (!byMode[m.mode]) byMode[m.mode] = [];
    byMode[m.mode].push(m);
  }

  const result: Record<string, { count: number; avgMs: number; p95Ms: number; fallbackRate: number }> = {};
  for (const [mode, items] of Object.entries(byMode)) {
    const sorted = [...items].sort((a, b) => a.elapsed - b.elapsed);
    const count = sorted.length;
    const avgMs = Math.round(sorted.reduce((s, m) => s + m.elapsed, 0) / count);
    const p95Idx = Math.floor(count * 0.95);
    const p95Ms = sorted[Math.min(p95Idx, count - 1)]?.elapsed ?? 0;
    const fallbackRate = sorted.filter(m => m.fallbackUsed).length / count;
    result[mode] = { count, avgMs, p95Ms, fallbackRate: Math.round(fallbackRate * 100) / 100 };
  }

  return { total: _metrics.length, byMode: result };
}
