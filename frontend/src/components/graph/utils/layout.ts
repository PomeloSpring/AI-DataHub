import dagre from 'dagre';
import { Node, Edge, Position } from '@xyflow/react';

// ── Types ──────────────────────────────────────────────────────────────

export type LayoutDirection = 'TB' | 'BT' | 'LR' | 'RL';

export interface LayoutOptions {
  direction?: LayoutDirection;
  nodeWidth?: number;
  nodeHeight?: number;
  ranksep?: number;
  nodesep?: number;
  edgesep?: number;
}

export interface LayoutResult {
  nodes: Node[];
  edges: Edge[];
}

// ── Default Options ────────────────────────────────────────────────────

const defaultOptions: Required<LayoutOptions> = {
  direction: 'TB',
  nodeWidth: 200,
  nodeHeight: 100,
  ranksep: 80,
  nodesep: 50,
  edgesep: 10,
};

// ── Layout Function ────────────────────────────────────────────────────

/**
 * Apply dagre layout to nodes and edges
 *
 * @param nodes - React Flow nodes
 * @param edges - React Flow edges
 * @param options - Layout options
 * @returns Layout result with positioned nodes and edges
 */
export function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
  options: LayoutOptions = {}
): LayoutResult {
  const opts = { ...defaultOptions, ...options };

  // Create a new dagre graph
  const g = new dagre.graphlib.Graph();

  // Set graph options
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: opts.direction,
    ranksep: opts.ranksep,
    nodesep: opts.nodesep,
    edgesep: opts.edgesep,
    marginx: 20,
    marginy: 20,
  });

  // Add nodes to the graph
  nodes.forEach((node) => {
    g.setNode(node.id, {
      width: node.measured?.width ?? node.width ?? opts.nodeWidth,
      height: node.measured?.height ?? node.height ?? opts.nodeHeight,
    });
  });

  // Add edges to the graph
  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  // Run the layout algorithm
  dagre.layout(g);

  // Apply the layout positions to nodes
  const layoutedNodes = nodes.map((node) => {
    const nodeWithPosition = g.node(node.id);

    // Calculate position (dagre returns center position, React Flow uses top-left)
    const position = {
      x: nodeWithPosition.x - nodeWithPosition.width / 2,
      y: nodeWithPosition.y - nodeWithPosition.height / 2,
    };

    // Set source and target positions based on direction
    let sourcePosition = Position.Bottom;
    let targetPosition = Position.Top;

    if (opts.direction === 'LR') {
      sourcePosition = Position.Right;
      targetPosition = Position.Left;
    } else if (opts.direction === 'RL') {
      sourcePosition = Position.Left;
      targetPosition = Position.Right;
    } else if (opts.direction === 'BT') {
      sourcePosition = Position.Top;
      targetPosition = Position.Bottom;
    }

    return {
      ...node,
      position,
      sourcePosition,
      targetPosition,
      // 自定义节点的 Handle 读取 data；只设置 Node 顶层方向不会改变其连接点。
      data: { ...node.data, sourcePosition, targetPosition },
    };
  });

  return {
    nodes: layoutedNodes,
    edges,
  };
}

// ── Force Layout (Alternative) ─────────────────────────────────────────

/**
 * Apply simple force-directed layout (for smaller graphs)
 * This is a simplified version - for production, consider using d3-force
 */
export function applyForceLayout(
  nodes: Node[],
  edges: Edge[],
  options: { width?: number; height?: number; iterations?: number } = {}
): LayoutResult {
  // 画布随节点数扩张(保持近似恒定密度): 固定 1400×1000 会把统一大图(可达数百节点)
  // 挤进小框, 圆节点(最大 150px)必然互相重叠。按 √n 定边长, 每节点约 240×240 网格位。
  const n = nodes.length;
  const span = Math.max(1400, Math.round(Math.sqrt(Math.max(n, 1)) * 240));
  const { width = span, height = span, iterations = n > 200 ? 160 : 240 } = options;

  // 尺寸感知: 圆节点以 width/height 为准(缺省 72)
  const sizeOf = (n: Node) => ({
    w: n.measured?.width ?? n.width ?? 72,
    h: n.measured?.height ?? n.height ?? 72,
  });
  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  // 确定性放射状播种: 高度数节点(hub)居中, 黄金角铺盘, 无随机数(可复现)
  const degree = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  edges.forEach((e) => {
    degree.set(e.source, (degree.get(e.source) || 0) + 1);
    degree.set(e.target, (degree.get(e.target) || 0) + 1);
  });
  const byHub = [...nodes].sort(
    (a, b) => (degree.get(b.id)! - degree.get(a.id)!) || a.id.localeCompare(b.id)
  );
  const positions = new Map<string, { x: number; y: number }>();
  byHub.forEach((node, i) => {
    const ring = Math.sqrt(i + 0.5);
    const angle = i * 2.399963229728653; // 黄金角
    const radius = ring * Math.min(width, height) * 0.12;
    positions.set(node.id, {
      x: width / 2 + radius * Math.cos(angle),
      y: height / 2 + radius * Math.sin(angle),
    });
  });

  // Build adjacency list
  const adjacency = new Map<string, string[]>();
  edges.forEach((edge) => {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
    if (!adjacency.has(edge.target)) adjacency.set(edge.target, []);
    adjacency.get(edge.source)!.push(edge.target);
    adjacency.get(edge.target)!.push(edge.source);
  });

  const repulsion = 60000;
  const attraction = 0.06;
  const centerPull = 0.012;

  for (let iter = 0; iter < iterations; iter++) {
    const forces = new Map<string, { fx: number; fy: number }>();
    nodes.forEach((n) => forces.set(n.id, { fx: 0, fy: 0 }));

    // 斥力(尺寸感知: 节点重叠时斥力显著加大)
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        const pa = positions.get(a.id)!;
        const pb = positions.get(b.id)!;
        const dx = pa.x - pb.x;
        const dy = pa.y - pb.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const sa = sizeOf(a);
        const sb = sizeOf(b);
        const minDist = (Math.max(sa.w, sa.h) + Math.max(sb.w, sb.h)) / 2 + 60;
        const overlapBoost = 1 + Math.max(0, minDist - dist) / minDist * 6;
        const force = (repulsion / (dist * dist)) * overlapBoost;
        const fa = forces.get(a.id)!;
        const fb = forces.get(b.id)!;
        fa.fx += (dx / dist) * force;
        fa.fy += (dy / dist) * force;
        fb.fx -= (dx / dist) * force;
        fb.fy -= (dy / dist) * force;
      }
    }

    // 弹力(静止距离随两侧节点尺寸)
    edges.forEach((edge) => {
      const na = nodeById.get(edge.source);
      const nb = nodeById.get(edge.target);
      if (!na || !nb) return;
      const pa = positions.get(edge.source)!;
      const pb = positions.get(edge.target)!;
      const dx = pb.x - pa.x;
      const dy = pb.y - pa.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = (Math.max(sizeOf(na).w, sizeOf(na).h) + Math.max(sizeOf(nb).w, sizeOf(nb).h)) / 2 + 120;
      const force = (dist - rest) * attraction;
      const fa = forces.get(edge.source)!;
      const fb = forces.get(edge.target)!;
      fa.fx += (dx / dist) * force;
      fa.fy += (dy / dist) * force;
      fb.fx -= (dx / dist) * force;
      fb.fy -= (dy / dist) * force;
    });

    // 向心力(防飞出)
    nodes.forEach((n) => {
      const p = positions.get(n.id)!;
      const f = forces.get(n.id)!;
      f.fx += (width / 2 - p.x) * centerPull;
      f.fy += (height / 2 - p.y) * centerPull;
    });

    // 逐步收敛 + 边界夹紧
    const factor = 2.2 / (iter + 8);
    nodes.forEach((n) => {
      const p = positions.get(n.id)!;
      const f = forces.get(n.id)!;
      p.x = Math.max(40, Math.min(width - 40, p.x + f.fx * factor));
      p.y = Math.max(40, Math.min(height - 40, p.y + f.fy * factor));
    });
  }

  // Apply positions to nodes (力导向无固定方向, 连接点沿用 TB 口径)
  const sourcePosition = Position.Bottom;
  const targetPosition = Position.Top;
  const layoutedNodes = nodes.map((node) => ({
    ...node,
    position: positions.get(node.id)!,
    sourcePosition,
    targetPosition,
    // 自定义节点的 Handle 读取 data；只设置 Node 顶层方向不会改变其连接点。
    data: { ...node.data, sourcePosition, targetPosition },
  }));

  return {
    nodes: layoutedNodes,
    edges,
  };
}

// ── Hierarchy Layout ───────────────────────────────────────────────────

/**
 * Apply hierarchical layout for tree-like structures
 */
export function applyHierarchyLayout(
  nodes: Node[],
  edges: Edge[],
  options: { direction?: LayoutDirection; levelHeight?: number; nodeSpacing?: number } = {}
): LayoutResult {
  const { direction = 'TB', levelHeight = 150, nodeSpacing = 200 } = options;

  // Find root nodes (no incoming edges)
  const incomingEdges = new Map<string, number>();
  edges.forEach((edge) => {
    incomingEdges.set(edge.target, (incomingEdges.get(edge.target) || 0) + 1);
  });

  const roots = nodes.filter((node) => !incomingEdges.has(node.id));

  // Build adjacency list
  const children = new Map<string, string[]>();
  edges.forEach((edge) => {
    if (!children.has(edge.source)) children.set(edge.source, []);
    children.get(edge.source)!.push(edge.target);
  });

  // BFS to assign levels
  const levels = new Map<string, number>();
  const queue: { id: string; level: number }[] = roots.map((r) => ({ id: r.id, level: 0 }));

  while (queue.length > 0) {
    const { id, level } = queue.shift()!;
    if (levels.has(id)) continue;
    levels.set(id, level);

    const kids = children.get(id) || [];
    kids.forEach((kid) => {
      if (!levels.has(kid)) {
        queue.push({ id: kid, level: level + 1 });
      }
    });
  }

  // Group nodes by level
  const levelGroups = new Map<number, string[]>();
  levels.forEach((level, id) => {
    if (!levelGroups.has(level)) levelGroups.set(level, []);
    levelGroups.get(level)!.push(id);
  });

  // Assign positions
  const positions = new Map<string, { x: number; y: number }>();
  levelGroups.forEach((ids, level) => {
    const totalWidth = (ids.length - 1) * nodeSpacing;
    const startX = -totalWidth / 2;

    ids.forEach((id, index) => {
      if (direction === 'TB' || direction === 'BT') {
        positions.set(id, {
          x: startX + index * nodeSpacing,
          y: level * levelHeight,
        });
      } else {
        positions.set(id, {
          x: level * levelHeight,
          y: startX + index * nodeSpacing,
        });
      }
    });
  });

  // Apply positions
  const layoutedNodes = nodes.map((node) => ({
    ...node,
    position: positions.get(node.id) || { x: 0, y: 0 },
  }));

  return {
    nodes: layoutedNodes,
    edges,
  };
}
