/**
 * 数据画像计算 — 结果表列统计/缺失率/分布(纯函数,DataProfileCard 与测试共用)。
 * 在结果行(≤1 万行量级)上就地计算,不回服务端;大结果以传入行集为准。
 */

export interface HistogramBin {
  label: string;
  count: number;
}

export interface ColumnProfile {
  name: string;
  /** 列语义类型: 数值 / 类别(低基数) / 文本 / 时间 / 空列 */
  kind: 'number' | 'category' | 'text' | 'datetime' | 'empty';
  /** 非空值数量 */
  count: number;
  missing: number;
  /** 缺失率 0~1 */
  missingRate: number;
  uniqueCount: number;
  /** 数值列统计 */
  min?: number;
  max?: number;
  mean?: number;
  /** 类别列 TopN(按出现次数降序) */
  topValues?: HistogramBin[];
  /** 迷你分布: 数值列等宽分箱 / 类别列 TopN 计数 */
  histogram?: HistogramBin[];
}

const DATE_RE = /^\d{4}([-/]\d{1,2}){1,2}/; // YYYY-MM 或 YYYY-MM-DD(均视为时间列)

function isMissing(v: any): boolean {
  return v === null || v === undefined || v === '';
}

function looksNumeric(v: any): boolean {
  return typeof v === 'number' && Number.isFinite(v);
}

function looksDate(v: any): boolean {
  return typeof v === 'string' && DATE_RE.test(v.trim());
}

/** 数值列等宽分箱;常量列退化为单箱。 */
function numericHistogram(values: number[], bins: number): HistogramBin[] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [{ label: String(min), count: values.length }];
  const width = (max - min) / bins;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    const idx = Math.min(bins - 1, Math.floor((v - min) / width));
    counts[idx] += 1;
  }
  return counts.map((count, i) => ({
    label: `${(min + i * width).toLocaleString()}~${(min + (i + 1) * width).toLocaleString()}`,
    count,
  }));
}

/**
 * 结果表列画像:逐列统计类型/非空/缺失率/唯一值,数值列补 min/max/均值与分箱,
 * 类别列补 TopN 与计数分布。maxTop/bins 控制输出规模。
 */
export function profileTable(
  columns: string[],
  rows: Record<string, any>[],
  maxTop = 5,
  bins = 8,
): ColumnProfile[] {
  return columns.map(name => {
    const raw = rows.map(r => r[name]);
    const present = raw.filter(v => !isMissing(v));
    const missing = raw.length - present.length;
    const uniqueCount = new Set(present.map(v => String(v))).size;

    const numValues = present.filter(looksNumeric).map(Number);
    const dateCount = present.filter(looksDate).length;

    let kind: ColumnProfile['kind'];
    if (present.length === 0) kind = 'empty';
    else if (numValues.length / present.length >= 0.8) kind = 'number';
    else if (dateCount / present.length >= 0.8) kind = 'datetime';
    else if (uniqueCount <= Math.max(10, present.length * 0.3)) kind = 'category';
    else kind = 'text';

    const profile: ColumnProfile = {
      name,
      kind,
      count: present.length,
      missing,
      missingRate: rows.length ? missing / rows.length : 0,
      uniqueCount,
    };

    if (kind === 'number' && numValues.length) {
      profile.min = Math.min(...numValues);
      profile.max = Math.max(...numValues);
      profile.mean = numValues.reduce((a, b) => a + b, 0) / numValues.length;
      profile.histogram = numericHistogram(numValues, bins);
    } else if (kind === 'category' || kind === 'text' || kind === 'datetime') {
      const counter = new Map<string, number>();
      for (const v of present) {
        const k = String(v);
        counter.set(k, (counter.get(k) || 0) + 1);
      }
      const top = [...counter.entries()]
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh'))
        .slice(0, maxTop)
        .map(([label, count]) => ({ label, count }));
      profile.topValues = top;
      if (kind === 'category') profile.histogram = top;
    }
    return profile;
  });
}
