/**
 * 动画图表帧计算 — 竞速条形图(bar_race)/时序播放(timeline_play)共用(纯函数,可单测)。
 * 帧序列 = 时间列(x)唯一值按时间/数值/字符串语义升序;帧数据支持"此刻"与"累计"两种切片。
 */

/** 单元格 → 帧键字符串(稳定排序与比对用)。 */
export function frameKey(v: any): string {
  return v === null || v === undefined ? '' : String(v);
}

/**
 * 字段推断(与 DashboardChart 同一口径): 时间列=首个字符串列,数值列=首个数值列,
 * 系列列=其余字符串列;config.xCol/yCol/groupCol 显式优先。
 */
export function inferChartFields(
  columns: string[], rows: Record<string, any>[], config?: Record<string, any>,
): { xField: string; yField: string; groupField: string | null } {
  const xField = config?.xCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0] || '';
  const yField = config?.yCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1] || '';
  const groupField = config?.groupCol
    || columns.find(c => c !== xField && c !== yField && typeof rows[0]?.[c] === 'string')
    || null;
  return { xField, yField, groupField };
}

/** 帧键排序: 可解析为日期/数值按其语义排,否则 localeCompare。 */
function compareKeys(a: string, b: string): number {
  const na = Number(a);
  const nb = Number(b);
  const da = Date.parse(a);
  const db = Date.parse(b);
  if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
  if (!Number.isNaN(da) && !Number.isNaN(db)) return da - db;
  return a.localeCompare(b, 'zh');
}

/** 帧序列: 时间列唯一值升序(空值不参与)。 */
export function buildTimeFrames(rows: Record<string, any>[], field: string): string[] {
  const seen = new Set<string>();
  for (const r of rows) {
    const k = frameKey(r[field]);
    if (k !== '') seen.add(k);
  }
  return [...seen].sort(compareKeys);
}

export interface FrameOptions {
  field: string;
  frame: string;
  /** 'at' = 仅该时刻的行(bar_race);'upto' = 累计至该时刻的行(timeline_play) */
  mode: 'at' | 'upto';
  /** 数值列,用于 bar_race 排序取前 N */
  valueField?: string;
  /** bar_race 每帧保留条数,缺省 12 */
  topN?: number;
}

/** 单帧数据: 按时刻切片,bar_race 帧内按数值降序取前 N。 */
export function frameRows(rows: Record<string, any>[], opts: FrameOptions): Record<string, any>[] {
  const { field, frame, mode, valueField, topN } = opts;
  let picked = rows.filter(r => (mode === 'upto'
    ? compareKeys(frameKey(r[field]), frame) <= 0
    : frameKey(r[field]) === frame));
  if (mode === 'at' && valueField) {
    picked = [...picked].sort((a, b) => (Number(b[valueField]) || 0) - (Number(a[valueField]) || 0));
    if (topN && topN > 0) picked = picked.slice(0, topN);
  }
  return picked;
}
