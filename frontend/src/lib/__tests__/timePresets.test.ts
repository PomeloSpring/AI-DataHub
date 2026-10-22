import { describe, expect, it } from 'vitest';
import {
  CUSTOM_PRESET, DAY_MS, DEFAULT_TIME_PRESET_KEYS, TIME_PRESETS, enabledPresets, effectiveGranularity,
  formatTime, fromInputValue, maxEndFor, needsTime, parseTime, resolvePreset, toInputValue,
} from '../timePresets';

// 固定一个本地时间基准：2026-10-22 18:30:00（本地时区）
const NOW = new Date(2026, 9, 22, 18, 30, 0);

describe('时间区间预设清单', () => {
  it('默认给出 1/6/12 小时、1/7 天、一个月、三个月，外加自由选择', () => {
    expect(TIME_PRESETS.map(p => p.key)).toEqual(['1h', '6h', '12h', '1d', '7d', '1mo', '3mo']);
    expect(TIME_PRESETS.map(p => p.label)).toEqual(
      ['近 1 小时', '近 6 小时', '近 12 小时', '近 1 天', '近 7 天', '近一个月', '近 3 个月']);
    expect(DEFAULT_TIME_PRESET_KEYS).toEqual(TIME_PRESETS.map(p => p.key));
    expect(CUSTOM_PRESET).toBe('custom');
  });

  it('enabledPresets 保留声明顺序并忽略未知 key', () => {
    expect(enabledPresets(['7d', 'nope', '1h']).map(p => p.key)).toEqual(['7d', '1h']);
    expect(enabledPresets(null)).toEqual(TIME_PRESETS);
    expect(enabledPresets(undefined)).toEqual(TIME_PRESETS);
    // 显式空数组 = 关掉快捷选项，不等于“没配置”
    expect(enabledPresets([])).toEqual([]);
  });
});

describe('预设窗口计算', () => {
  it('小时级按毫秒回退、月级按自然月回退（不用 30 天近似）', () => {
    expect(resolvePreset('1h', NOW)).toEqual({ start: new Date(2026, 9, 22, 17, 30, 0), end: NOW });
    expect(resolvePreset('12h', NOW)).toEqual({ start: new Date(2026, 9, 22, 6, 30, 0), end: NOW });
    expect(resolvePreset('7d', NOW)).toEqual({ start: new Date(2026, 9, 15, 18, 30, 0), end: NOW });
    expect(resolvePreset('1mo', NOW)?.start).toEqual(new Date(2026, 8, 22, 18, 30, 0));
    expect(resolvePreset('3mo', NOW)?.start).toEqual(new Date(2026, 6, 22, 18, 30, 0));
    // 跨月末按日历钳位：2024/3/31 往前一个月是闰年 2/29，而不是 setMonth 溢出到 3/2
    expect(resolvePreset('1mo', new Date(2024, 2, 31, 8, 0, 0))?.start).toEqual(new Date(2024, 1, 29, 8, 0, 0));
    expect(resolvePreset('1mo', new Date(2026, 4, 31))?.start).toEqual(new Date(2026, 3, 30));
    // 日号未超月末时原样保留，且不丢失时分秒
    expect(resolvePreset('1mo', new Date(2026, 9, 15, 18, 30, 0))?.start).toEqual(new Date(2026, 8, 15, 18, 30, 0));
  });

  it('未知预设返回 null，调用方据此回落自定义', () => {
    expect(resolvePreset('nope', NOW)).toBeNull();
    expect(resolvePreset(null, NOW)).toBeNull();
    expect(resolvePreset(undefined, NOW)).toBeNull();
  });
});

describe('本地时区格式化（不得走 UTC）', () => {
  it('date 只到日、datetime 到秒，且都用本地字段', () => {
    expect(formatTime(NOW, 'date')).toBe('2026-10-22');
    expect(formatTime(NOW, 'datetime')).toBe('2026-10-22 18:30:00');
  });

  it('晚间时刻不会被算成前一天（原实现用 toISOString 会偏 UTC+8）', () => {
    const evening = new Date(2026, 9, 22, 23, 30, 0);
    expect(formatTime(evening, 'date')).toBe('2026-10-22');
    // 该断言只在时区不是 UTC 时才暴露差异；UTC 环境下两者本就一致
    const utcDay = evening.toISOString().slice(0, 10);
    if (new Date(2026, 9, 22, 23, 30, 0).getTimezoneOffset() > 0) {
      expect(formatTime(evening, 'date')).not.toBe(utcDay);
    }
  });

  it('parseTime 与 formatTime 对称，T 与空格两种写法都吃', () => {
    expect(parseTime('2026-10-22 18:30:00')).toEqual(NOW);
    expect(parseTime('2026-10-22T18:30')).toEqual(NOW);
    expect(parseTime('2026-10-22')).toEqual(new Date(2026, 9, 22, 0, 0, 0));
    expect(parseTime('')).toBeNull();
    expect(parseTime('昨天')).toBeNull();
    expect(parseTime(null)).toBeNull();
  });
});

describe('粒度与输入框格式', () => {
  it('小时级预设强制带时分秒，其余跟随配置', () => {
    expect(needsTime('1h')).toBe(true);
    expect(needsTime('7d')).toBe(false);
    expect(effectiveGranularity('date', '1h')).toBe('datetime');
    expect(effectiveGranularity('datetime', '7d')).toBe('datetime');
    expect(effectiveGranularity('date', '7d')).toBe('date');
    expect(effectiveGranularity(undefined, null)).toBe('date');
  });

  it('规范值 → datetime-local 必须是 T 分隔且到分钟', () => {
    expect(toInputValue('2026-10-22 18:30:00', 'datetime')).toBe('2026-10-22T18:30');
    expect(toInputValue('2026-10-22', 'date')).toBe('2026-10-22');
    expect(toInputValue('', 'datetime')).toBe('');
    expect(toInputValue('垃圾值', 'date')).toBe('');
  });

  it('输入框值 → 规范值：只填到分钟时补 :00 秒', () => {
    expect(fromInputValue('2026-10-22T18:30', 'datetime')).toBe('2026-10-22 18:30:00');
    expect(fromInputValue('2026-10-22', 'date')).toBe('2026-10-22');
    expect(fromInputValue('', 'datetime')).toBe('');
  });

  it('最大跨度按天累加并保持原格式', () => {
    expect(maxEndFor('2026-10-22', 90)).toBe('2027-01-20');
    expect(maxEndFor('2026-10-22 18:30:00', 1)).toBe('2026-10-23 18:30:00');
    expect(maxEndFor('', 90)).toBe('');
    // 与 DAY_MS 一致：90 天不是 3 个月
    expect(DAY_MS * 90).toBe(90 * 24 * 3600 * 1000);
  });
});
