/**
 * 字段显示格式（时间列怎么给人看）契约测试。
 *
 * 锁定三件事：
 * 1. 令牌输出正确，且**不经过时区换算**（历史踩坑：`toISOString()` 在 UTC+8 把
 *    `2026-10-12T00:00:00` 变成 `2026-10-11`，日期整体前移一天）；
 * 2. 认不出来的值/令牌一律原样返回（宁可看见旧值，不可编出错值）；
 * 3. 消费面真的接上了（表值图实渲染 + 源码接线护栏），防止"后端下发了、前端没读"。
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render } from '@testing-library/react';
import DashboardChart from '@/components/DashboardChart';
import {
  FIELD_DISPLAY_FORMATS, applyFieldFormats, formatFieldValue, normalizeFormats,
} from '../fieldFormat';

const ISO = '2026-10-12T14:30:05.000';

describe('formatFieldValue 令牌输出', () => {
  it.each([
    ['date', '2026-10-12'],
    ['datetime', '2026-10-12 14:30'],
    ['datetime_s', '2026-10-12 14:30:05'],
    ['time', '14:30:05'],
    ['month', '2026-10'],
    ['year', '2026'],
    ['md', '10-12'],
  ])('%s → %s', (fmt, expected) => {
    expect(formatFieldValue(ISO, fmt)).toBe(expected);
  });

  it('零点时间戳（DATE 列序列化出来的形态）按日就是纯日期，不带 T 和毫秒', () => {
    expect(formatFieldValue('2026-10-12T00:00:00.000', 'date')).toBe('2026-10-12');
  });

  it('不经过时区换算：跨时区也不会把日期挪前一天（toISOString 那一类坑）', () => {
    // 本地 0 点的朴素时间串必须原样落在同一天
    expect(formatFieldValue('2026-10-12T00:00:00.000', 'datetime')).toBe('2026-10-12 00:00');
    expect(formatFieldValue('2026-01-01T00:00:00.000', 'date')).toBe('2026-01-01');
    expect(formatFieldValue('2026-12-31T23:59:59.000', 'date')).toBe('2026-12-31');
  });

  it('空格分隔的 `YYYY-MM-DD HH:mm:ss` 同样可解析（不同引擎回法不一）', () => {
    expect(formatFieldValue('2026-10-12 14:30:05', 'date')).toBe('2026-10-12');
  });

  it('缺秒的时间串补零而不是丢时分', () => {
    expect(formatFieldValue('2026-10-12T14:30', 'datetime_s')).toBe('2026-10-12 14:30:00');
  });

  it('纯日期串当时刻格式化时补 00:00:00（不编一个"没有时间"的空值）', () => {
    expect(formatFieldValue('2026-10-12', 'time')).toBe('00:00:00');
  });

  it('纪元秒/毫秒数字形态也能出日期（与本地时区读数一致，不是 NaN）', () => {
    const d = new Date(1760000000 * 1000);
    const expectDate = [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'),
      String(d.getDate()).padStart(2, '0')].join('-');
    expect(formatFieldValue('1760000000', 'date')).toBe(expectDate);
    expect(formatFieldValue(String(d.getTime()), 'date')).toBe(expectDate);
  });
});

describe('认不出来就原样（不猜、不编）', () => {
  it('未知令牌原样返回', () => {
    expect(formatFieldValue(ISO, 'yyyy-MM-dd')).toBe(ISO);
    expect(formatFieldValue(ISO, undefined)).toBe(ISO);
    expect(formatFieldValue(ISO, '')).toBe(ISO);
  });

  it('非时间形态的值原样返回（普通文本列被误配格式也不改内容）', () => {
    expect(formatFieldValue('上海市', 'date')).toBe('上海市');
    expect(formatFieldValue('abc', 'date')).toBe('abc');
    expect(formatFieldValue(12345, 'date')).toBe('12345');
  });

  it('空值渲染为空串，不是 undefined/NaN', () => {
    expect(formatFieldValue(null, 'date')).toBe('');
    expect(formatFieldValue(undefined, 'date')).toBe('');
  });
});

describe('normalizeFormats', () => {
  it('只留认识别的令牌', () => {
    expect(normalizeFormats({ 统计日期: 'date', 渠道: 'nope', 备注: '' }))
      .toEqual({ 统计日期: 'date' });
  });

  it.each([null, undefined, 'date', ['date'], 42])('脏入参 %s 当没配', raw => {
    expect(normalizeFormats(raw)).toEqual({});
  });
});

describe('applyFieldFormats', () => {
  const data = {
    columns: ['stat_date', 'record_count'],
    rows: [
      { stat_date: '2026-10-12T00:00:00.000', record_count: 10952 },
      { stat_date: '2026-10-13T00:00:00.000', record_count: 17116 },
    ],
    total: 31,
    paging: { mode: 'offset', page_size: 20, page_num: 1, total: 31, can_jump: true },
    field_formats: { stat_date: 'date' },
  };

  it('受控列换成显示值，其他列原样', () => {
    const out = applyFieldFormats(data);
    expect(out.rows.map(r => r.stat_date)).toEqual(['2026-10-12', '2026-10-13']);
    expect(out.rows[0].record_count).toBe(10952);
  });

  it('分页/行数等渲染元信息原样保留（少了它会把首页当全量）', () => {
    const out = applyFieldFormats(data);
    expect(out.total).toBe(31);
    expect(out.paging).toEqual(data.paging);
    expect(out.columns).toEqual(data.columns);
  });

  it('不改调用方传入的原对象（取数结果还要给导出/下钻拿原值）', () => {
    const before = JSON.stringify(data);
    applyFieldFormats(data);
    expect(JSON.stringify(data)).toBe(before);
  });

  it('幂等：格式化后的值再过一次不变（多处消费面重复施加不会套娃）', () => {
    const once = applyFieldFormats(data);
    expect(applyFieldFormats(once)).toEqual(once);
  });

  it('没配格式时原对象返回（不产生无谓的新数组）', () => {
    const plain = { columns: ['a'], rows: [{ a: 1 }] };
    expect(applyFieldFormats(plain)).toBe(plain);
  });

  it('兼容「数组的数组」行（```chart 块归一化前的形态）', () => {
    const out = applyFieldFormats({
      columns: ['月份', '销售额'],
      rows: [['2026-10-12T00:00:00.000', 120]],
      field_formats: { 月份: 'date' },
    } as any);
    expect(out.rows).toEqual([['2026-10-12', 120]]);
  });
});

describe('令牌清单本身', () => {
  it('每个非空令牌都有业务可读标签与示例（选择框靠它所见即所得）', () => {
    const usable = FIELD_DISPLAY_FORMATS.filter(f => f.value);
    expect(usable.length).toBeGreaterThanOrEqual(7);
    usable.forEach(f => {
      expect(f.label, f.value).toBeTruthy();
      expect(f.example, f.value).toBeTruthy();
    });
  });

  it('示例与实现输出一致（改实现或改文案都不能让字面承诺失效）', () => {
    FIELD_DISPLAY_FORMATS.filter(f => f.value).forEach(f => {
      expect(formatFieldValue(ISO, f.value), f.value).toBe(f.example);
    });
  });
});

describe('渲染面真的消费了格式（防"下发了但没人读"）', () => {
  const src = (p: string) => readFileSync(resolve(__dirname, '..', '..', p), 'utf8');

  it('DashboardChart 在进 G2 spec 与表值图之前归一一次', () => {
    const chart = src('components/DashboardChart.tsx');
    expect(chart).toContain('applyFieldFormats');
    // spec 与表值图必须读归一后的那份，不得再拿原始 data.rows 渲染
    expect(chart).toContain('chartType, view.columns, view.rows, cfg');
    expect(chart).not.toContain('chartType, data.columns, data.rows, cfg');
  });

  it('数据集预览、图表配置面板、数据网格三处同源消费', () => {
    for (const file of [
      'pages/datasets/DatasetDetail.tsx',
      'components/ChartConfigPanel.tsx',
      'components/DataGrid.tsx',
    ]) expect(src(file), file).toContain('applyFieldFormats');
  });

  it('表值图单元格按声明渲染成人读日期，不再是一排 T00:00:00.000', () => {
    const { container } = render(<DashboardChart
      chartType="table_value"
      data={{
        columns: ['stat_date', 'record_count'],
        rows: [{ stat_date: '2026-10-12T00:00:00.000', record_count: 10952 }],
        field_formats: { stat_date: 'date' },
      }}
      config={{}}
      draft
    />);
    const text = container.textContent || '';
    expect(text).toContain('2026-10-12');
    expect(text).not.toContain('T00:00:00.000');
  });

  it('未声明格式时保持原样（不得默认替用户改数据表现）', () => {
    const { container } = render(<DashboardChart
      chartType="table_value"
      data={{ columns: ['stat_date'], rows: [{ stat_date: '2026-10-12T00:00:00.000' }] }}
      config={{}}
      draft
    />);
    expect(container.textContent).toContain('2026-10-12T00:00:00.000');
  });
});
