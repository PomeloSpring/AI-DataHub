import { describe, expect, it } from 'vitest';
import { pruneEmptySections } from '@/lib/menuUtils';

const item = (key: string) => ({ key });
const section = (name: string) => ({ section: name });

describe('pruneEmptySections — 无菜单权限就不渲染一级分组', () => {
  it('分组下有可见项时保留分组标题', () => {
    const out = pruneEmptySections([section('数据源'), item('/data/datasources')]);
    expect(out).toEqual([section('数据源'), item('/data/datasources')]);
  });

  it('分组下无可见项时不渲染该分组标题', () => {
    const out = pruneEmptySections([
      section('数据源'),            // 下面没有可见项 → 剪掉
      section('本体'), item('/data/ontology'),
      section('数据质量'),          // 下面没有可见项 → 剪掉
    ]);
    expect(out).toEqual([section('本体'), item('/data/ontology')]);
  });

  it('多个分组各自独立剪枝', () => {
    const out = pruneEmptySections([
      section('A'), item('/a1'),
      section('B'),
      section('C'), item('/c1'), item('/c2'),
    ]);
    expect(out.map(x => 'section' in x ? x.section : x.key)).toEqual(['A', '/a1', 'C', '/c1', '/c2']);
  });

  it('无分组标题的菜单项原样保留', () => {
    expect(pruneEmptySections([item('/x'), item('/y')])).toEqual([item('/x'), item('/y')]);
  });

  it('全部分组为空时输出空', () => {
    expect(pruneEmptySections([section('A'), section('B')])).toEqual([]);
  });
});
