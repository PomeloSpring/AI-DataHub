import i18n from '@/i18n';

/**
 * locale 感知的格式化层（多语言架构 §五）。
 * 一律在渲染期调用、不做模块级缓存——语言切换后随重渲染自然更新。
 * 本批触碰页面内的裸 toLocaleString()/硬编码 'zh' localeCompare 逐步收口到这里。
 */

const locale = (): string => i18n.language || 'zh-CN';

/** 日期（不含时间）：2026/11/14 */
export function formatDate(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === '') return '-';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  return new Intl.DateTimeFormat(locale(), { year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
}

/** 日期 + 时间（到分钟）：2026/11/14 09:30 */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === '') return '-';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  return new Intl.DateTimeFormat(locale(), {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(d);
}

/** 数字千分位；非法值回 '-'（不编造 0） */
export function formatNumber(value: number | null | undefined, options?: Intl.NumberFormatOptions): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  return new Intl.NumberFormat(locale(), options).format(value);
}

/** 排序比较器：跟随当前语言（替代硬编码 'zh' 的 localeCompare） */
export function localeCompare(a: string, b: string): number {
  return a.localeCompare(b, locale());
}