/**
 * DataFoundry-style UI Token Constants
 *
 * Centralized Tailwind class-name bundles for consistent styling.
 * Reference: DataFoundry's ui-tokens.ts pattern
 *
 * Usage:
 *   import { btnPrimaryClass, panelShellClass } from '@/lib/ui-tokens';
 *   <button className={btnPrimaryClass}>Save</button>
 *   <div className={panelShellClass}>...</div>
 */

// ─── Buttons ─────────────────────────────────────────────────────────────────

export const btnPrimaryClass =
  'bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-xs font-semibold ' +
  'hover:opacity-90 transition-colors duration-200 cursor-pointer ' +
  'focus-visible:ring-2 focus-visible:ring-ring/20 disabled:opacity-50 disabled:cursor-not-allowed';

export const btnSecondaryClass =
  'border border-border bg-card rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground ' +
  'hover:bg-secondary transition-colors duration-200 cursor-pointer ' +
  'focus-visible:ring-2 focus-visible:ring-ring/20 disabled:opacity-50 disabled:cursor-not-allowed';

export const btnGhostClass =
  'rounded-lg px-2.5 py-1 text-xs font-medium text-muted-foreground ' +
  'hover:bg-secondary transition-colors duration-200 cursor-pointer ' +
  'focus-visible:ring-2 focus-visible:ring-ring/20';

export const btnDangerClass =
  'bg-destructive text-destructive-foreground rounded-lg px-3 py-1.5 text-xs font-semibold ' +
  'hover:opacity-90 transition-colors duration-200 cursor-pointer ' +
  'focus-visible:ring-2 focus-visible:ring-destructive/20 disabled:opacity-50 disabled:cursor-not-allowed';

// ─── Panels & Cards ──────────────────────────────────────────────────────────

export const panelShellClass =
  'rounded-xl border border-border bg-card p-3 shadow-[0_1px_1px_rgb(13_13_13/0.02)]';

export const panelHoverClass =
  'hover:shadow-[0_2px_8px_rgb(13_13_13/0.04)] transition-shadow duration-200';

export const cardClass =
  'rounded-xl border border-border bg-card shadow-[0_1px_1px_rgb(13_13_13/0.02)]';

// ─── Data Tables ─────────────────────────────────────────────────────────────

export const tableShellClass =
  'rounded-xl border border-border overflow-x-auto';

export const tableHeaderClass =
  'sticky top-0 bg-secondary shadow-[inset_0_-1px_0_var(--border)]';

export const tableCellClass =
  'px-3 py-2 text-sm border-t border-border';

export const tableNumericCellClass =
  'px-3 py-2 text-sm border-t border-border text-right tabular-nums';

// ─── Chips & Badges ──────────────────────────────────────────────────────────

export const chipClass =
  'inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2.5 py-0.5 text-[11px] font-medium';

export const badgeClass =
  'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium';

// ─── Form Elements ───────────────────────────────────────────────────────────

export const inputClass =
  'h-9 w-full rounded-lg border border-border bg-card px-3 text-sm ' +
  'focus:border-primary focus:ring-2 focus:ring-primary/10 transition-colors duration-200 ' +
  'placeholder:text-muted-foreground';

export const textareaClass =
  'w-full rounded-lg border border-border bg-card px-3 py-2 text-sm ' +
  'focus:border-primary focus:ring-2 focus:ring-primary/10 transition-colors duration-200 ' +
  'placeholder:text-muted-foreground resize-none';

export const selectClass =
  'h-9 w-full rounded-lg border border-border bg-card px-3 text-sm ' +
  'focus:border-primary focus:ring-2 focus:ring-primary/10 transition-colors duration-200';

// ─── Labels & Descriptions ───────────────────────────────────────────────────

export const labelClass =
  'text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground';

export const descriptionClass =
  'text-xs text-muted-foreground';

export const sectionTitleClass =
  'text-sm font-semibold text-foreground';

// ─── Layout ──────────────────────────────────────────────────────────────────

export const pageHeaderClass =
  'flex items-center justify-between pb-4';

export const dividerClass =
  'border-t border-border';

// ─── Status Tone Functions ───────────────────────────────────────────────────

export type StatusTone = 'success' | 'warning' | 'error' | 'info';

interface ToneClasses {
  bg: string;
  border: string;
  text: string;
}

/**
 * Returns semantic color classes for a given status.
 * Uses DataFoundry's jewel-tone palette via CSS variables.
 *
 * Usage:
 *   const tone = statusTone('success');
 *   <span className={`${tone.bg} ${tone.border} ${tone.text} ...`}>OK</span>
 */
export function statusTone(status: StatusTone): ToneClasses {
  const tones: Record<StatusTone, ToneClasses> = {
    success: {
      bg: 'bg-[hsl(var(--step-success)/0.1)]',
      border: 'border-[hsl(var(--step-success)/0.3)]',
      text: 'text-[hsl(var(--step-success))]',
    },
    warning: {
      bg: 'bg-[hsl(var(--step-warning)/0.1)]',
      border: 'border-[hsl(var(--step-warning)/0.3)]',
      text: 'text-[hsl(var(--step-warning))]',
    },
    error: {
      bg: 'bg-[hsl(var(--step-error)/0.1)]',
      border: 'border-[hsl(var(--step-error)/0.3)]',
      text: 'text-[hsl(var(--step-error))]',
    },
    info: {
      bg: 'bg-[hsl(var(--step-inspect)/0.1)]',
      border: 'border-[hsl(var(--step-inspect)/0.3)]',
      text: 'text-[hsl(var(--step-inspect))]',
    },
  };
  return tones[status];
}

/**
 * Returns a full chip/badge class string for a status.
 */
export function statusChipClass(status: StatusTone): string {
  const tone = statusTone(status);
  return `${chipClass} ${tone.bg} ${tone.border} ${tone.text}`;
}

// ─── Agent Step Tone Functions ───────────────────────────────────────────────

export type StepKind =
  | 'inspect'
  | 'query'
  | 'transform'
  | 'fetch'
  | 'visualize'
  | 'knowledge'
  | 'success'
  | 'warning'
  | 'error';

/**
 * Returns color classes for agent step types.
 * Maps to the jewel-tone palette defined in CSS variables.
 */
export function stepKindTone(kind: StepKind): ToneClasses {
  return {
    bg: `bg-[hsl(var(--step-${kind})/0.1)]`,
    border: `border-[hsl(var(--step-${kind})/0.3)]`,
    text: `text-[hsl(var(--step-${kind}))]`,
  };
}
