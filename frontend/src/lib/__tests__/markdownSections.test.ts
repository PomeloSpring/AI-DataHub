import { describe, it, expect } from 'vitest';
import { splitMarkdownBlocks, splitMarkdownSections } from '../markdownSections';

describe('splitMarkdownSections', () => {
  it('按标题切成章节:开头无标题段落单独成节', () => {
    const text = [
      '抱歉,再次尝试仍然被拒绝。',
      '',
      '## 可能原因',
      '',
      '1. 原因一',
      '2. 原因二',
      '',
      '## **建议操作**',
      '',
      '请前往建模页面。',
    ].join('\n');
    const secs = splitMarkdownSections(text);
    expect(secs).toHaveLength(3);
    expect(secs[0].title).toBeNull();
    expect(secs[0].body).toContain('再次尝试仍然被拒绝');
    expect(secs[1].title).toBe('可能原因');
    expect(secs[1].body).toContain('原因一');
    // 标题中的加粗记号去掉
    expect(secs[2].title).toBe('建议操作');
    expect(secs[2].body.trim()).toBe('请前往建模页面。');
  });

  it('围栏代码块内的 # 不切分', () => {
    const text = ['## SQL 说明', '', '```sql', '# 这是注释不是标题', 'SELECT 1', '```'].join('\n');
    const secs = splitMarkdownSections(text);
    expect(secs).toHaveLength(1);
    expect(secs[0].title).toBe('SQL 说明');
    expect(secs[0].body).toContain('# 这是注释不是标题');
  });

  it('空体标题节不产生卡片', () => {
    const text = ['正文', '', '## 孤标题', ''].join('\n');
    const secs = splitMarkdownSections(text);
    expect(secs).toHaveLength(1);
    expect(secs[0].title).toBeNull();
  });

  it('无标题纯文本整体作为一节', () => {
    const secs = splitMarkdownSections('第一行\n第二行');
    expect(secs).toHaveLength(1);
    expect(secs[0].title).toBeNull();
    expect(secs[0].body).toBe('第一行\n第二行');
  });
});

describe('splitMarkdownBlocks（正文分段渐入：一段内容都不能丢）', () => {
  it('按标题切段且标题行留在段内', () => {
    const blocks = splitMarkdownBlocks(['## 一、总体', '', '第一段', '', '## 二、明细', '', '第二段'].join('\n'));
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toContain('## 一、总体');
    expect(blocks[0]).toContain('第一段');
    expect(blocks[1]).toContain('## 二、明细');
    // 拼回去仍是原文的内容（只少了段间空行，markdown 不认它）
    expect(blocks.join('\n').replace(/\n/g, '')).toBe('## 一、总体第一段## 二、明细第二段');
  });

  it('开头无标题的引子自成一节，不会被并进下一节', () => {
    const blocks = splitMarkdownBlocks(['引子', '', '# 标题', '正文'].join('\n'));
    expect(blocks).toEqual(['引子', '# 标题\n正文']);
  });

  it('围栏代码块里的 # 不切；孤立标题也不会被吞掉', () => {
    const blocks = splitMarkdownBlocks(['## SQL 说明', '', '```sql', '# 注释不是标题', 'SELECT 1', '```'].join('\n'));
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toContain('# 注释不是标题');
    expect(splitMarkdownBlocks('正文\n\n## 孤标题')).toEqual(['正文', '## 孤标题']);
  });

  it('空文本不产块（渲染面据此一个 class 也不加）', () => {
    expect(splitMarkdownBlocks('')).toEqual([]);
    expect(splitMarkdownBlocks('   \n  ')).toEqual([]);
  });
});
