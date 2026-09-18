// buildAstTree: sqlglot dump(扁平父索引) -> 树 的重建逻辑
import { describe, it, expect } from 'vitest';
import { buildAstTree } from '../ASTTreeView';

// 取自 /playground/ast 真实响应结构: SELECT a FROM t
const DUMP = [
  { c: 'sqlglot.expressions.query.Select' },
  { i: 0, k: 'expressions', a: true, c: 'sqlglot.expressions.core.Column' },
  { i: 1, k: 'this', c: 'sqlglot.expressions.core.Identifier', m: { line: 1, col: 8, start: 7, end: 7 } },
  { i: 2, k: 'this', v: 'a' },
  { i: 2, k: 'quoted', v: false },
  { i: 0, k: 'from', c: 'sqlglot.expressions.query.From' },
  { i: 5, k: 'this', a: true, c: 'sqlglot.expressions.query.Table' },
  { i: 6, k: 'this', c: 'sqlglot.expressions.core.Identifier', m: { line: 1, col: 17, start: 16, end: 16 } },
  { i: 7, k: 'this', v: 't' },
  { i: 7, k: 'quoted', v: false },
];

describe('buildAstTree', () => {
  it('按父索引 i 重建层级', () => {
    const root = buildAstTree(DUMP)!;
    expect(root).toBeTruthy();
    expect(root.cls).toBe('Select');
    // Select 的两个分支子节点: Column(1) 与 From(5)
    expect(root.children.map((c) => c.cls)).toEqual(['Column', 'From']);
    // Column -> Identifier -> 叶子 this/quoted
    const ident = root.children[0].children[0];
    expect(ident.cls).toBe('Identifier');
    expect(ident.pos).toMatchObject({ line: 1, col: 8 });
    expect(ident.children.map((c) => c.value)).toEqual(['a', false]);
    expect(ident.children.every((c) => c.cls === '')).toBe(true);
  });

  it('保留 arg 键与数组标记', () => {
    const root = buildAstTree(DUMP)!;
    expect(root.children[0].argKey).toBe('expressions');
    expect(root.children[0].isArray).toBe(true);
    expect(root.children[1].argKey).toBe('from');
  });

  it('非法输入返回 null(调用方回退 JSON 视图)', () => {
    expect(buildAstTree(null)).toBeNull();
    expect(buildAstTree([])).toBeNull();
    expect(buildAstTree({} as any)).toBeNull();
    expect(buildAstTree('x' as any)).toBeNull();
  });

  it('悬空父索引不崩(挂不到任何父上时忽略, 根仍存在)', () => {
    const bad = [DUMP[0], { i: 99, k: 'x', c: 'foo.Bar' } as any];
    const root = buildAstTree(bad)!;
    expect(root.cls).toBe('Select');
    expect(root.children).toHaveLength(0);
  });
});
