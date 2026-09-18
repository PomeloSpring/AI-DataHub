import { describe, it, expect } from 'vitest';
import { parseChartBody, normalizeRows, splitChartBlocks } from '../chartBlocks';

describe('splitChartBlocks', () => {
  it('提取 chart 块并保留其余 markdown', () => {
    const text = [
      '这是结论。',
      '```chart',
      '{"title":"月度销售额","chart_type":"line","columns":["月份","销售额"],"rows":[["2026-01",120],["2026-02",150]]}',
      '```',
      '以上。',
    ].join('\n');
    const segs = splitChartBlocks(text);
    expect(segs.map((s) => s.kind)).toEqual(['markdown', 'chart', 'markdown']);
    const chart = segs[1] as any;
    expect(chart.chartType).toBe('line');
    expect(chart.title).toBe('月度销售额');
    expect(chart.data.columns).toEqual(['月份', '销售额']);
    // rows 归一化为对象数组
    expect(chart.data.rows[0]).toEqual({ 月份: '2026-01', 销售额: 120 });
    expect(chart.data.rows[1]).toEqual({ 月份: '2026-02', 销售额: 150 });
  });

  it('解析失败降级为 raw 段', () => {
    const text = '```chart\n{ not valid json }\n```';
    const segs = splitChartBlocks(text);
    expect(segs).toHaveLength(1);
    expect(segs[0].kind).toBe('raw');
  });

  it('多个 chart 块', () => {
    const one = '{"columns":["a","b"],"rows":[["x",1]]}';
    const text = `\`\`\`chart
${one}
\`\`\`
中间
\`\`\`chart
${one}
\`\`\``;
    const kinds = splitChartBlocks(text).map((s) => s.kind);
    expect(kinds.filter((k) => k === 'chart')).toHaveLength(2);
  });

  it('无 chart 块时全部为 markdown', () => {
    const segs = splitChartBlocks('普通回答，没有图表。');
    expect(segs).toHaveLength(1);
    expect(segs[0].kind).toBe('markdown');
  });
});

describe('normalizeRows', () => {
  it('数组的数组 → 对象数组', () => {
    expect(normalizeRows(['a', 'b'], [['x', 1], ['y', 2]])).toEqual([
      { a: 'x', b: 1 },
      { a: 'y', b: 2 },
    ]);
  });

  it('对象的数组原样保留', () => {
    const rows = [{ a: 'x', b: 1 }];
    expect(normalizeRows(['a', 'b'], rows)).toEqual(rows);
  });
});

describe('parseChartBody', () => {
  it('空 rows 视为无效 → raw', () => {
    expect(parseChartBody('{"columns":["a","b"],"rows":[]}').kind).toBe('raw');
  });

  it('解析可选 sql 字段供卡片 SQL 视图使用', () => {
    const seg = parseChartBody('{"chart_type":"radar","sql":"  SELECT 1 ","columns":["a","b"],"rows":[["x",1]]}') as any;
    expect(seg.kind).toBe('chart');
    expect(seg.chartType).toBe('radar');
    expect(seg.sql).toBe('SELECT 1');
  });

  it('无 sql / 空 sql 字段时不产生空字符串', () => {
    const noSql = parseChartBody('{"columns":["a","b"],"rows":[["x",1]]}') as any;
    expect(noSql.sql).toBeUndefined();
    const blankSql = parseChartBody('{"sql":"   ","columns":["a","b"],"rows":[["x",1]]}') as any;
    expect(blankSql.sql).toBeUndefined();
  });
});
