/**
 * Pipeline Store — Mode selection and progress tracking for Quick-Deep pipeline.
 *
 * This store manages pipeline-specific state. Message handling remains in chatStore;
 * Chat.tsx decides which API endpoint to call based on pipelineMode.
 */

import { create } from 'zustand';
import type { PipelineMode } from '../config/pipeline';
import { recordMetric, type PipelineMetric } from '../config/pipeline-monitoring';

// ── Types ───────────────────────────────────────────────────────

export interface PipelineStageState {
  status: 'pending' | 'active' | 'done' | 'error';
  message?: string;
  elapsed?: number;  // seconds
}

interface PipelineState {
  /** Selected pipeline mode */
  pipelineMode: PipelineMode;
  /** Current active stage key */
  activeStage: string | null;
  /** Stage states keyed by stage key */
  stageStates: Record<string, PipelineStageState>;
  /** Detected mode from backend (may differ from requested in auto) */
  resolvedMode: PipelineMode | null;
  /** Whether fallback was used (quick → deep) */
  fallbackUsed: boolean;

  // Actions
  setPipelineMode: (mode: PipelineMode) => void;
  resetProgress: () => void;
  setActiveStage: (stage: string, message?: string) => void;
  markStageDone: (stage: string) => void;
  markStageError: (stage: string, message?: string) => void;
  setResolvedMode: (mode: PipelineMode) => void;
  setFallbackUsed: (used: boolean) => void;
  recordMetricOnComplete: (elapsed: number, success: boolean, stageTimings?: Record<string, number>, tokenCount?: { input: number; output: number; total: number }) => void;
}

// ── Store ───────────────────────────────────────────────────────

const STORAGE_KEY = 'chatbi_pipeline_mode';

function loadSavedMode(): PipelineMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && ['auto', 'quick', 'deep'].includes(saved)) {
      return saved as PipelineMode;
    }
  } catch {}
  return 'auto';
}

export const usePipelineStore = create<PipelineState>((set, get) => ({
  pipelineMode: loadSavedMode(),
  activeStage: null,
  stageStates: {},
  resolvedMode: null,
  fallbackUsed: false,

  setPipelineMode: (mode) => {
    try { localStorage.setItem(STORAGE_KEY, mode); } catch {}
    set({ pipelineMode: mode });
  },

  resetProgress: () => {
    set({
      activeStage: null,
      stageStates: {},
      resolvedMode: null,
      fallbackUsed: false,
    });
  },

  setActiveStage: (stage, message) => {
    set(state => {
      const newStates = { ...state.stageStates };

      // Mark previous stages as done if they were still active
      for (const [key, val] of Object.entries(newStates)) {
        if (val.status === 'active' && key !== stage) {
          newStates[key] = { ...val, status: 'done' };
        }
      }

      newStates[stage] = { status: 'active', message };
      return { activeStage: stage, stageStates: newStates };
    });
  },

  markStageDone: (stage) => {
    set(state => ({
      stageStates: {
        ...state.stageStates,
        [stage]: { ...state.stageStates[stage], status: 'done' },
      },
    }));
  },

  markStageError: (stage, message) => {
    set(state => ({
      stageStates: {
        ...state.stageStates,
        [stage]: { status: 'error', message },
      },
    }));
  },

  setResolvedMode: (mode) => set({ resolvedMode: mode }),

  setFallbackUsed: (used) => set({ fallbackUsed: used }),

  recordMetricOnComplete: (elapsed, success, stageTimings, tokenCount) => {
    const { pipelineMode, resolvedMode, fallbackUsed } = get();
    const metric: PipelineMetric = {
      mode: resolvedMode || pipelineMode,
      startTime: Date.now() - elapsed,
      endTime: Date.now(),
      elapsed,
      success,
      fallbackUsed,
      stageTimings: stageTimings || {},
      tokenCount,
    };
    recordMetric(metric);
  },
}));
