import { describe, it, expect } from 'vitest';
import { splitChartBlocks, parseGraphBody } from '../chartBlocks';

describe('splitChartBlocks — graph 围栏', () => {
  it('```graph 块解析为 graph 段(节点/边清洗)', () => {
    const body = JSON.stringify({
      title: '血缘',
      nodes: [{ id: 'a', label: '订单表', type: 'table' }, { id: 2 }, { id: '', label: '幽灵' }],
      edges: [{ source: 'a', target: '2', label: '流向' }, { source: 'a', target: 'ghost' }],
    });
    const segs = splitChartBlocks('```graph\n' + body + '\n```');
    expect(segs).toHaveLength(1);
    const g = segs[0] as any;
    expect(g.kind).toBe('graph');
    expect(g.title).toBe('血缘');
    // 无 id 节点剔除;数字 id 归一为字符串
    expect(g.nodes.map((n: any) => n.id)).toEqual(['a', '2']);
    // 悬空边(ghost)剔除,合法边保留
    expect(g.edges).toEqual([{ source: 'a', target: '2', label: '流向' }]);
  });

  it('非法 JSON 降级为 raw 段', () => {
    const segs = splitChartBlocks('```graph\n{not json\n```');
    expect(segs[0].kind).toBe('raw');
  });

  it('缺 nodes/edges 降级为 raw 段', () => {
    expect(parseGraphBody('{"title":"x"}').kind).toBe('raw');
    expect(parseGraphBody('{"nodes":[],"edges":[]}').kind).toBe('graph');
  });
});
