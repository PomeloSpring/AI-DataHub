import { describe, it, expect } from 'vitest';
import { buildTimeFrames, frameKey, frameRows, inferChartFields } from '../chartFrames';

describe('frameKey / buildTimeFrames', () => {
  it('null/undefined 归为空键', () => {
    expect(frameKey(null)).toBe('');
    expect(frameKey(undefined)).toBe('');
    expect(frameKey(0)).toBe('0');
  });

  it('唯一值按数值语义升序', () => {
    const rows = [{ t: '2' }, { t: '10' }, { t: '2' }, { t: '1' }];
    expect(buildTimeFrames(rows, 't')).toEqual(['1', '2', '10']);
  });

  it('日期字符串按日期语义升序', () => {
    const rows = [{ t: '2026-02-01' }, { t: '2026-01-15' }, { t: '2026-03-01' }];
    expect(buildTimeFrames(rows, 't')).toEqual(['2026-01-15', '2026-02-01', '2026-03-01']);
  });

  it('月份字符串按 locale 升序,空值不参与', () => {
    const rows = [{ t: '2026-02' }, { t: '' }, { t: '2026-01' }, { t: null }];
    expect(buildTimeFrames(rows, 't')).toEqual(['2026-01', '2026-02']);
  });
});

describe('frameRows', () => {
  const rows = [
    { m: '2026-01', p: 'A', v: 5 },
    { m: '2026-01', p: 'B', v: 9 },
    { m: '2026-02', p: 'A', v: 7 },
    { m: '2026-02', p: 'B', v: 3 },
    { m: '2026-03', p: 'A', v: 12 },
    { m: '2026-03', p: 'B', v: 8 },
  ];

  it('at 模式仅取该时刻的行', () => {
    const picked = frameRows(rows, { field: 'm', frame: '2026-01', mode: 'at' });
    expect(picked).toHaveLength(2);
    expect(picked.every(r => r.m === '2026-01')).toBe(true);
  });

  it('at 模式按数值降序取前 N(bar_race)', () => {
    const picked = frameRows(rows, { field: 'm', frame: '2026-01', mode: 'at', valueField: 'v', topN: 1 });
    expect(picked).toEqual([{ m: '2026-01', p: 'B', v: 9 }]);
  });

  it('upto 模式累计至该时刻(timeline_play)', () => {
    const picked = frameRows(rows, { field: 'm', frame: '2026-02', mode: 'upto' });
    expect(picked).toHaveLength(4);
    expect(picked.filter(r => r.m === '2026-03')).toHaveLength(0);
  });
});

describe('inferChartFields', () => {
  it('默认口径: 首字符串列=时间,首数值列=值,其余字符串列=系列', () => {
    const cols = ['月份', '产品', '销量'];
    const rows = [{ 月份: '2026-01', 产品: 'A', 销量: 1 }];
    expect(inferChartFields(cols, rows)).toEqual({ xField: '月份', yField: '销量', groupField: '产品' });
  });

  it('config 显式指定优先于推断', () => {
    const cols = ['月份', '产品', '销量'];
    const rows = [{ 月份: '2026-01', 产品: 'A', 销量: 1 }];
    const f = inferChartFields(cols, rows, { xCol: '产品', yCol: '销量', groupCol: '月份' });
    expect(f).toEqual({ xField: '产品', yField: '销量', groupField: '月份' });
  });

  it('无候选列时回退列序', () => {
    const f = inferChartFields([], []);
    expect(f.xField).toBe('');
    expect(f.yField).toBe('');
    expect(f.groupField).toBeNull();
  });
});
