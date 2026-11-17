import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { applyForceLayout, type LayoutResult } from '../layout';
import { compactNodeSize } from '../../KnowledgeGraph';

/** 造一张贴近业务全景图的图：18 对象 + 120 指标 + 127 维度 + 541 属性（全部挂在对象下）。 */
function buildUnifiedGraph() {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const add = (id: string, type: string, label: string) => {
    const size = compactNodeSize(type, label);
    nodes.push({ id, type, width: size, height: size, position: { x: 0, y: 0 }, data: { label } });
  };

  const objects = 18;
  const metrics = 120;
  const dimensions = 127;
  const properties = 541;

  for (let i = 0; i < objects; i++) add(`obj:${i}`, 'Object', `对象${i}`);
  const attach = (prefix: string, type: string, count: number) => {
    for (let i = 0; i < count; i++) {
      const id = `${prefix}:${i}`;
      add(id, type, `${type === 'Metric' ? '指标' : type === 'Dimension' ? '维度' : '属性'}${i}`);
      edges.push({ id: `e-${id}`, source: `obj:${i % objects}`, target: id });
    }
  };
  attach('m', 'Metric', metrics);
  attach('d', 'Dimension', dimensions);
  attach('p', 'Property', properties);

  return { nodes, edges };
}

function overlaps(result: LayoutResult) {
  const circles = result.nodes.map((n) => {
    const size = Math.max(n.width ?? 72, n.height ?? 72);
    return { x: n.position.x + size / 2, y: n.position.y + size / 2, r: size / 2 };
  });
  const bad: string[] = [];
  for (let i = 0; i < circles.length; i++) {
    for (let j = i + 1; j < circles.length; j++) {
      const a = circles[i];
      const b = circles[j];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      if (dist < a.r + b.r - 1) bad.push(`${i}-${j}@${dist.toFixed(1)}`);
    }
  }
  return bad;
}

describe('applyForceLayout（图可视化去重叠）', () => {
  it('业务全景图规模（800+ 节点）布局后无圆节点重叠', () => {
    const { nodes, edges } = buildUnifiedGraph();
    const result = applyForceLayout(nodes, edges);
    expect(result.nodes).toHaveLength(nodes.length);
    expect(result.edges).toHaveLength(edges.length);
    expect(overlaps(result)).toEqual([]);
  });

  it('全部节点坐标有限且不塌在一点（无 NaN / 无贴边成环）', () => {
    const { nodes, edges } = buildUnifiedGraph();
    const result = applyForceLayout(nodes, edges);
    const xs = result.nodes.map((n) => n.position.x);
    const ys = result.nodes.map((n) => n.position.y);
    expect(xs.every((v) => Number.isFinite(v))).toBe(true);
    expect(ys.every((v) => Number.isFinite(v))).toBe(true);
    // 展开面积应随节点数增长（旧实现把节点全夹在画布边界上）
    const spread = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys));
    expect(spread).toBeGreaterThan(nodes.length * 100 * 100);
  });

  it('确定性：同样输入两次布局结果一致（无随机数）', () => {
    const { nodes, edges } = buildUnifiedGraph();
    const a = applyForceLayout(nodes, edges);
    const b = applyForceLayout(nodes, edges);
    expect(a.nodes.map((n) => [n.position.x, n.position.y])).toEqual(
      b.nodes.map((n) => [n.position.x, n.position.y])
    );
  });

  it('空图不抛错', () => {
    expect(applyForceLayout([], []).nodes).toEqual([]);
  });
});
