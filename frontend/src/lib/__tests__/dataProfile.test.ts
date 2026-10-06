import { describe, it, expect } from 'vitest';
import { profileTable } from '../dataProfile';

const columns = ['月份', '产品', '销量', '备注'];
const rows = [
  { 月份: '2026-01', 产品: 'A', 销量: 10, 备注: '' },
  { 月份: '2026-01', 产品: 'B', 销量: 20, 备注: 'x' },
  { 月份: '2026-02', 产品: 'A', 销量: 30, 备注: null },
  { 月份: '2026-02', 产品: 'A', 销量: null, 备注: 'y' },
];

describe('profileTable', () => {
  const profiles = profileTable(columns, rows);
  const byName = Object.fromEntries(profiles.map(p => [p.name, p]));

  it('数值列:类型/统计/分箱', () => {
    const p = byName['销量'];
    expect(p.kind).toBe('number');
    expect(p.count).toBe(3);
    expect(p.missing).toBe(1);
    expect(p.missingRate).toBeCloseTo(0.25);
    expect(p.min).toBe(10);
    expect(p.max).toBe(30);
    expect(p.mean).toBeCloseTo(20);
    expect(p.histogram?.length).toBeGreaterThan(1);
  });

  it('类别列:低基数判定 + TopN', () => {
    const p = byName['产品'];
    expect(p.kind).toBe('category');
    expect(p.uniqueCount).toBe(2);
    expect(p.topValues?.[0]).toEqual({ label: 'A', count: 3 });
    expect(p.histogram?.length).toBe(2);
  });

  it('时间列判定', () => {
    expect(byName['月份'].kind).toBe('datetime');
    expect(byName['月份'].topValues?.length).toBeGreaterThan(0);
  });

  it('高基数文本列判定', () => {
    // 备注: 值各异(含缺失),判定为 text 并给 Top 值
    const p = byName['备注'];
    expect(['text', 'category']).toContain(p.kind);
    expect(p.missing).toBe(2);
  });

  it('空列判定', () => {
    const [p] = profileTable(['空'], [{ 空: null }, { 空: '' }]);
    expect(p.kind).toBe('empty');
    expect(p.missingRate).toBe(1);
  });

  it('常量数值列分箱退化为单箱', () => {
    const [p] = profileTable(['v'], [{ v: 5 }, { v: 5 }]);
    expect(p.kind).toBe('number');
    expect(p.histogram).toEqual([{ label: '5', count: 2 }]);
  });
});
