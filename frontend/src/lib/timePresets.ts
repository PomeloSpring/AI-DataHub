/**
 * 时间区间控件的预设窗口 —— 单一事实源。
 *
 * 约定（与既有取数契约一致）：
 *   - 控件值写成 { start, end, preset }，经后端 _flatten_params 得到
 *     {{<paramKey>.start}} / {{<paramKey>.end}}；bind_param 只取 start/end 写页面参数。
 *   - start/end 一律是**本地时区**的规范字符串（date → YYYY-MM-DD，
 *     datetime → YYYY-MM-DD HH:MM:SS）。不要用 toISOString()：那是 UTC，
 *     在 UTC+8 的晚间会算成前一天，导致按时间窗过滤静默取错数据。
 */

export type TimeGranularity = 'date' | 'datetime';

export interface TimePreset {
  key: string;
  label: string;
  /** 固定毫秒跨度（小时/天级预设） */
  ms?: number;
  /** 日历月跨度（月级预设按自然月回退，不用固定天数近似） */
  months?: number;
}

const HOUR = 3_600_000;
export const DAY_MS = 24 * HOUR;
const DAY = DAY_MS;

export const TIME_PRESETS: TimePreset[] = [
  { key: '1h', label: '近 1 小时', ms: HOUR },
  { key: '6h', label: '近 6 小时', ms: 6 * HOUR },
  { key: '12h', label: '近 12 小时', ms: 12 * HOUR },
  { key: '1d', label: '近 1 天', ms: DAY },
  { key: '7d', label: '近 7 天', ms: 7 * DAY },
  { key: '1mo', label: '近一个月', months: 1 },
  { key: '3mo', label: '近 3 个月', months: 3 },
];

export const DEFAULT_TIME_PRESET_KEYS: string[] = TIME_PRESETS.map(p => p.key);
/** 「自由选择」在预设下拉里的取值：选中它表示区间由用户手填 */
export const CUSTOM_PRESET = 'custom';

export const presetOf = (key?: string | null): TimePreset | undefined =>
  TIME_PRESETS.find(p => p.key === key);

/** 小时级预设必须带时分秒，否则窗口会塌成整天而失去意义 */
export const needsTime = (key?: string | null): boolean => key === '1h' || key === '6h' || key === '12h';

/** 生效粒度：配置的粒度为 datetime、或当前选中的是小时级预设时，走带时分秒的格式 */
export function effectiveGranularity(configured: TimeGranularity | undefined, presetKey?: string | null): TimeGranularity {
  return configured === 'datetime' || needsTime(presetKey) ? 'datetime' : 'date';
}

/** 可选预设清单：未配置（非数组）时给全套默认项；显式空数组表示“不要快捷选项” */
export function enabledPresets(keys?: string[] | null): TimePreset[] {
  if (!Array.isArray(keys)) return TIME_PRESETS;
  return keys.map(presetOf).filter((p): p is TimePreset => !!p);
}

/** 按预设 key 算出 [start, end]；未知 key 返回 null（调用方据此回落到自定义） */
export function resolvePreset(key: string | null | undefined, now: Date = new Date()): { start: Date; end: Date } | null {
  const preset = presetOf(key);
  if (!preset) return null;
  const end = new Date(now.getTime());
  if (preset.months) {
    const start = new Date(now.getTime());
    // 先捕日号再置 1 号，避免 setMonth 在 3/31 这类日期上溢出到下个月初
    const day = start.getDate();
    start.setDate(1);
    start.setMonth(start.getMonth() - preset.months);
    const lastDay = new Date(start.getFullYear(), start.getMonth() + 1, 0).getDate();
    start.setDate(Math.min(day, lastDay));
    return { start, end };
  }
  return { start: new Date(now.getTime() - (preset.ms as number)), end };
}

const pad = (n: number) => String(n).padStart(2, '0');

/** 本地时区格式化：date → YYYY-MM-DD；datetime → YYYY-MM-DD HH:MM:SS */
export function formatTime(date: Date, granularity: TimeGranularity): string {
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return granularity === 'datetime' ? `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` : day;
}

/** 解析规范字符串；无时区标记按本地时区理解（与 formatTime 对称） */
export function parseTime(value: unknown): Date | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  const matched = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(value.trim());
  if (!matched) return null;
  const [, y, mo, d, h = '0', mi = '0', s = '0'] = matched;
  const date = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s));
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * 自由选择的时间范围上限：返回 end 允许的最大值（规范字符串）。
 * 超过 rangeMaxDays 时不再静默改数据，而是由输入框的 max 属性挡住并提示。
 */
export function maxEndFor(start: string, rangeMaxDays: number): string {
  const from = parseTime(start);
  if (!from) return '';
  const granularity: TimeGranularity = start.length > 10 ? 'datetime' : 'date';
  return formatTime(new Date(from.getTime() + rangeMaxDays * DAY_MS), granularity);
}

/** 规范值 → <input type=date|datetime-local> 的 value（datetime-local 必须是 YYYY-MM-DDTHH:MM） */
export function toInputValue(value: unknown, granularity: TimeGranularity): string {
  const date = parseTime(value);
  if (!date) return '';
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return granularity === 'datetime' ? `${day}T${pad(date.getHours())}:${pad(date.getMinutes())}` : day;
}

/** <input> 的 value → 规范值；用户在 datetime 输入里只填到分钟时补 :00 秒 */
export function fromInputValue(value: string, granularity: TimeGranularity): string {
  if (!value) return '';
  const date = parseTime(value);
  return date ? formatTime(date, granularity) : '';
}
