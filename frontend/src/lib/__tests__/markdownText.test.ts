// 回答文本双形态转换的契约：复制为Markdown 给原文、复制给纯文本、转文档给可渲染 markdown。
// 关键护栏：特殊块（chart/graph/artifact/design/sql）与渲染同源（splitChartBlocks），
// 复制出来不能是一片空白，也不能把围栏 JSON 整坨糊进用户剪贴板。
import { describe, expect, it } from 'vitest';
import {
  buildDownloadHtml, docFileName, formatDocTime, markdownBlockToPlainText,
  markdownForDocument, markdownToPlainText,
} from '../markdownText';

describe('markdownToPlainText（复制）', () => {
  it('去掉标记语法但保留正文结构与有序编号', () => {
    const md = [
      '## 两种模式',
      '',
      '1. **快速模式**：响应快',
      '2. *思考研究模式*：更严谨',
      '',
      '- 优点一',
      '- 优点二',
    ].join('\n');
    expect(markdownToPlainText(md)).toBe(
      '两种模式\n\n1. 快速模式：响应快\n2. 思考研究模式：更严谨\n\n• 优点一\n• 优点二',
    );
  });

  it('链接保留 http 地址、图片给可读占位', () => {
    expect(markdownToPlainText('详见[官方文档](https://example.com/doc)')).toBe(
      '详见官方文档 (https://example.com/doc)',
    );
    // 锚文本就是地址本身时不重复一遍
    expect(markdownToPlainText('[https://example.com](https://example.com)')).toBe('https://example.com');
    expect(markdownToPlainText('![销售趋势](/img/a.png)')).toBe('【图片：销售趋势】');
    // 非 web 协议（如站内路径）不进剪贴板噪声
    expect(markdownToPlainText('[报告](/ws/1/reports)')).toBe('报告');
  });

  it('表格丢掉分隔行、分隔线不占行', () => {
    const md = ['| 模式 | 适用 |', '| --- | --- |', '| 快速 | 80% |', '', '---', ''].join('\n');
    expect(markdownToPlainText(md)).toBe('| 模式 | 适用 |\n| 快速 | 80% |');
  });

  it('图表/看板/文件块给可读占位而不是空白或裸 JSON', () => {
    const md = [
      '先看趋势：',
      '',
      '```chart',
      '{"title":"月度销售额","chart_type":"line","columns":["月","额"],"rows":[["2026-01",10]]}',
      '```',
      '',
      '```design',
      '{"design_id":"d-9"}',
      '```',
      '',
      '```artifact',
      '{"type":"xlsx","filename":"明细.xlsx","path":"uploads/明细.xlsx"}',
      '```',
    ].join('\n');
    const text = markdownToPlainText(md);
    expect(text).toContain('先看趋势：');
    expect(text).toContain('【图表：月度销售额】');
    expect(text).toContain('【看板设计】');
    expect(text).toContain('【文件：明细.xlsx】');
    expect(text).not.toContain('chart_type');
    expect(text).not.toContain('design_id');
  });

  it('SQL 块复制语句本身（卡片上显示的就是它）', () => {
    const md = '```sql\nSELECT 1\n```';
    expect(markdownToPlainText(md)).toBe('SELECT 1');
  });

  it('解析失败的块原样保留文本（用户看到什么就复制什么）', () => {
    const md = '```chart\n{不是合法 JSON}\n```';
    expect(markdownToPlainText(md)).toContain('{不是合法 JSON}');
  });

  it('空正文返回空串（调用方据此提示"没有可复制的内容"）', () => {
    expect(markdownToPlainText('')).toBe('');
    expect(markdownToPlainText('```design\n{"design_id":"d1"}\n```')).toBe('【看板设计】');
  });
});

describe('markdownBlockToPlainText', () => {
  it('行内码反引号与加粗同时去掉', () => {
    expect(markdownBlockToPlainText('用 `SUM(**amt**)` 统计**金额**')).toBe('用 SUM(amt) 统计金额');
  });

  it('引用标记去掉但文字保留', () => {
    expect(markdownBlockToPlainText('> 结论：销售额上涨')).toBe('结论：销售额上涨');
  });
});

describe('markdownForDocument（转文档初稿）', () => {
  it('普通 markdown 原样保留，特殊块转引用行', () => {
    const md = ['## 标题', '', '正文段落', '', '```chart',
      '{"title":"趋势","columns":["a"],"rows":[[1]]}', '```'].join('\n');
    const out = markdownForDocument(md);
    expect(out).toContain('## 标题');
    expect(out).toContain('正文段落');
    expect(out).toContain('> 【图表：趋势】');
    expect(out).not.toContain('"columns"');
  });
});

describe('下载与展示辅助', () => {
  it('文件名去掉路径非法字符并回落默认名', () => {
    expect(docFileName('销售/月度:报告')).toBe('销售_月度_报告.html');
    expect(docFileName('   ')).toBe('未命名文档.html');
    expect(docFileName('报告', 'md')).toBe('报告.md');
  });

  it('下载 HTML 转义标题、原样嵌入正文（不做二次格式转换）', () => {
    const html = buildDownloadHtml('A & B', '<p>正文 <strong>粗体</strong></p>');
    expect(html).toContain('<title>A &amp; B</title>');
    expect(html).toContain('<h1>A &amp; B</h1>');
    expect(html).toContain('<p>正文 <strong>粗体</strong></p>');
  });

  it('时间标注可读、非法时间返回空串', () => {
    expect(formatDocTime('2026-11-06T10:51:00', '创建于')).toMatch(/^创建于 11-06 10:51$/);
    expect(formatDocTime('not-a-date')).toBe('');
  });
});
