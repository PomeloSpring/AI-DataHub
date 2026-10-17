import { describe, it, expect } from 'vitest';
import { splitMarkdownSections } from '../markdownSections';

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
