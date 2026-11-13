import { describe, it, expect } from 'vitest';
import { parseChartBody, parseArtifactBody, parseDesignBody, parseReportBody, normalizeRows, splitChartBlocks } from '../chartBlocks';

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

describe('parseArtifactBody', () => {
  it('按引用 path 产物(无 content)合法', () => {
    const seg = parseArtifactBody('{"type":"html","filename":"报告.html","path":"报告.html"}') as any;
    expect(seg.kind).toBe('artifact');
    expect(seg.path).toBe('报告.html');
    expect(seg.content).toBeUndefined();
  });

  it('artifact 携带可选 theme 字段', () => {
    const seg = parseArtifactBody('{"type":"html","filename":"r.html","path":"r.html","theme":"datafoundry"}') as any;
    expect(seg.kind).toBe('artifact');
    expect(seg.theme).toBe('datafoundry');
  });

  it('内联 content 产物(无 path)合法', () => {
    const seg = parseArtifactBody('{"type":"md","filename":"a.md","content":"hello"}') as any;
    expect(seg.kind).toBe('artifact');
    expect(seg.content).toBe('hello');
    expect(seg.path).toBeUndefined();
  });

  it('content 与 path 均缺失 → 降级 raw', () => {
    expect(parseArtifactBody('{"type":"md","filename":"a.md"}').kind).toBe('raw');
  });

  it('splitChartBlocks 能识别 artifact 围栏', () => {
    const text = '结论。\n```artifact\n{"type":"excel","filename":"r.xlsx","path":"r.xlsx"}\n```';
    const segs = splitChartBlocks(text);
    expect(segs.map(s => s.kind)).toContain('artifact');
  });
});

describe('parseDesignBody', () => {
  it('合法 design 块仅携带 design_id', () => {
    const seg = parseDesignBody('{"design_id":"abc123"}') as any;
    expect(seg.kind).toBe('design');
    expect(seg.designId).toBe('abc123');
  });

  it('缺 design_id / 非法 JSON → 降级 raw', () => {
    expect(parseDesignBody('{"name":"x"}').kind).toBe('raw');
    expect(parseDesignBody('{ not json }').kind).toBe('raw');
    expect(parseDesignBody('{"design_id":""}').kind).toBe('raw');
  });

  it('splitChartBlocks 在回答内嵌 design 卡片', () => {
    const id = 'a'.repeat(32);
    const text = `请完成选择。\n\`\`\`design\n{"design_id":"${id}"}\n\`\`\`\n完成后告诉我。`;
    const segs = splitChartBlocks(text);
    expect(segs.map(s => s.kind)).toEqual(['markdown', 'design', 'markdown']);
    expect((segs[1] as any).designId).toBe(id);
  });

  it('同一回答重复引用同一设计只保留一块（不重复弹卡片）', () => {
    const block = '```design\n{"design_id":"same"}\n```';
    const text = `${block}\n中间文字\n${block}`;
    const segs = splitChartBlocks(text);
    expect(segs.filter(s => s.kind === 'design')).toHaveLength(1);
  });

  it('不同 design_id 的块各自保留', () => {
    const text = '```design\n{"design_id":"d1"}\n```\n```design\n{"design_id":"d2"}\n```';
    const segs = splitChartBlocks(text);
    expect(segs.filter(s => s.kind === 'design')).toHaveLength(2);
  });
});

describe('report 交付块（渲染机引用）', () => {
  it('解析 doc_id + 形态 + 标题', () => {
    const seg = parseReportBody('{"doc_id":"abc","body_kind":"html","title":"订单分析报告"}') as any;
    expect(seg.kind).toBe('report');
    expect(seg.docId).toBe('abc');
    expect(seg.bodyKind).toBe('html');
    expect(seg.title).toBe('订单分析报告');
  });

  it('body_kind 缺失时按 html 处理，但仍是一块（不得当“没这个块”丢掉）', () => {
    const seg = parseReportBody('{"doc_id":"abc"}') as any;
    expect(seg.kind).toBe('report');
    expect(seg.bodyKind).toBe('html');
  });

  it('缺 doc_id / 非法 JSON → 降级 raw', () => {
    expect(parseReportBody('{"title":"x"}').kind).toBe('raw');
    expect(parseReportBody('{ not json }').kind).toBe('raw');
    expect(parseReportBody('{"doc_id":"  "}').kind).toBe('raw');
  });

  it('splitChartBlocks 在回答内嵌交付卡，其余正文仍是 markdown', () => {
    const text = '结论如下。\n```report\n{"doc_id":"d1","body_kind":"html","title":"报告"}\n```\n以上。';
    const segs = splitChartBlocks(text);
    expect(segs.map(s => s.kind)).toEqual(['markdown', 'report', 'markdown']);
    expect((segs[1] as any).docId).toBe('d1');
  });

  it('一轮只一个主交付物：同一 doc 重复引用只渲一次，且不回退成源码', () => {
    const block = '```report\n{"doc_id":"same"}\n```';
    const segs = splitChartBlocks(`${block}\n中间文字\n${block}`);
    expect(segs.filter(s => s.kind === 'report')).toHaveLength(1);
    // 被消耗的那块不得以 raw/markdown 形式把 JSON 源码漏给用户
    expect(JSON.stringify(segs)).not.toMatch(/doc_id":\s*"same"/);
  });

  it('未闭合围栏不切块（流式期不把半份报告当正文念出来）', () => {
    const segs = splitChartBlocks('正在生成…\n```report\n{"doc_id":"d1"}');
    expect(segs.filter(s => s.kind === 'report')).toHaveLength(0);
  });

  it('流式模式下未闭合的 report 围栏换成进度占位，不漏 JSON 源码', () => {
    const segs = splitChartBlocks('结论如下。\n```report\n{"doc_id":"d1", "bo', true);
    expect(segs.map(s => s.kind)).toEqual(['markdown', 'report-pending']);
    expect(JSON.stringify(segs)).not.toContain('doc_id');
  });

  it('流式模式下已闭合的 report 块照常规渲染（不拿占位顶替成品）', () => {
    const segs = splitChartBlocks('```report\n{"doc_id":"d1"}\n```\n以上。', true);
    expect(segs.map(s => s.kind)).toEqual(['report', 'markdown']);
  });

  it('非流式（历史回放）不受占位影响：未闭合就照旧当正文', () => {
    const segs = splitChartBlocks('```report\n{"doc_id":"d1"}', false);
    expect(segs.every(s => s.kind !== 'report-pending')).toBe(true);
  });
});

describe('chart 引用形态（render_chart 渲染机）', () => {
  it('chart_ref 块解析成 chart-ref 段（数据不在消息里）', () => {
    const seg = parseChartBody('{"chart_ref":"c1","title":"月度订单量"}');
    expect(seg).toEqual({ kind: 'chart-ref', chartRef: 'c1', title: '月度订单量' });
  });

  it('同时给了 chart_ref 与内联 columns/rows 时以引用为准（服务端取数才是事实源）', () => {
    const seg = parseChartBody(
      '{"chart_ref":"c1","columns":["月","额"],"rows":[["2026-01",999]]}');
    expect(seg.kind).toBe('chart-ref');
  });

  it('缺 chart_ref 的块仍走内联形态（历史消息回放不回退）', () => {
    const seg = parseChartBody('{"chart_ref":"","columns":["月","额"],"rows":[{"月":"1","额":2}]}');
    expect(seg.kind).toBe('chart');
  });

  it('坏 JSON 降级为 raw，不猜 ref', () => {
    expect(parseChartBody('{"chart_ref": 语法错').kind).toBe('raw');
  });

  it('混排：markdown / chart-ref / chart 内联各归其位', () => {
    const text = [
      '先看趋势。',
      '```chart',
      '{"chart_ref":"c1"}',
      '```',
      '再看明细。',
      '```chart',
      '{"chart_type":"bar","columns":["月","额"],"rows":[["2026-01",120]]}',
      '```',
    ].join('\n');
    const segs = splitChartBlocks(text);
    expect(segs.map(s => s.kind)).toEqual(['markdown', 'chart-ref', 'markdown', 'chart']);
  });
});
