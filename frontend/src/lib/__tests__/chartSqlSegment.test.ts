import { describe, it, expect } from 'vitest';
import { splitChartBlocks } from '../chartBlocks';

describe('splitChartBlocks — sql 围栏', () => {
  it('```sql 块解析为 sql 片段', () => {
    const segs = splitChartBlocks('前言\n```sql\nSELECT 1\n```\n后记');
    expect(segs.map(s => s.kind)).toEqual(['markdown', 'sql', 'markdown']);
    expect((segs[1] as any).code).toBe('SELECT 1');
    expect((segs[0] as any).text).toContain('前言');
    expect((segs[2] as any).text).toContain('后记');
  });

  it('sql 与其他围栏共存且保持出现顺序', () => {
    const text = [
      '```sql\nSELECT a FROM t\n```',
      '```mermaid\ngraph TD; A-->B;\n```',
      '```sql\nSELECT b FROM t\n```',
    ].join('\n');
    const segs = splitChartBlocks(text);
    expect(segs.map(s => s.kind)).toEqual(['sql', 'markdown', 'mermaid', 'markdown', 'sql']);
    expect((segs[0] as any).code).toBe('SELECT a FROM t');
    expect((segs[4] as any).code).toBe('SELECT b FROM t');
  });

  it('大小写不敏感,内容含空行保留', () => {
    const segs = splitChartBlocks('```SQL\nSELECT 1\n\nFROM dual\n```');
    expect(segs[0].kind).toBe('sql');
    expect((segs[0] as any).code).toBe('SELECT 1\n\nFROM dual');
  });
});
