import { describe, expect, it } from 'vitest';
import { Node, Edge, Position } from '@xyflow/react';
import { applyDagreLayout, LayoutDirection } from './layout';

const nodes: Node[] = [
  { id: 'a', position: { x: 0, y: 0 }, width: 232, height: 112, data: { label: '订单' } },
  { id: 'b', position: { x: 0, y: 0 }, width: 232, height: 112, data: { label: '客户' } },
];
const edges: Edge[] = [{ id: 'ab', source: 'a', target: 'b' }];

describe('图谱布局尺寸与连接方向', () => {
  it.each([
    ['TB', Position.Bottom, Position.Top],
    ['BT', Position.Top, Position.Bottom],
    ['LR', Position.Right, Position.Left],
    ['RL', Position.Left, Position.Right],
  ] as const)('%s 的节点与自定义 Handle 方向一致', (direction, source, target) => {
    const result = applyDagreLayout(nodes, edges, { direction: direction as LayoutDirection });
    result.nodes.forEach((node) => {
      expect(node.sourcePosition).toBe(source);
      expect(node.targetPosition).toBe(target);
      expect(node.data.sourcePosition).toBe(source);
      expect(node.data.targetPosition).toBe(target);
    });
    expect(result.edges).toEqual(edges);
    expect(nodes[0].data).toEqual({ label: '订单' });
  });

  it('按真实尺寸保留层间距，不让高卡片占用连线路径', () => {
    const tallNodes = [{ ...nodes[0], measured: { width: 300, height: 320 } }, nodes[1]];
    const result = applyDagreLayout(tallNodes, edges, { ranksep: 120 });
    expect(result.nodes[1].position.y - result.nodes[0].position.y).toBeGreaterThanOrEqual(320 + 120);
  });

  it('循环、平行关系和独立节点均保留，布局坐标有效', () => {
    const allNodes = [...nodes, { id: 'c', position: { x: 0, y: 0 }, data: {} }];
    const allEdges = [...edges, { id: 'ab2', source: 'a', target: 'b' }, { id: 'ba', source: 'b', target: 'a' }];
    const result = applyDagreLayout(allNodes, allEdges);
    expect(result.nodes).toHaveLength(3);
    expect(result.edges).toEqual(allEdges);
    result.nodes.forEach((node) => {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
    });
  });
});
