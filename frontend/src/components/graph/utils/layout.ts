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
      width: opts.nodeWidth,
      height: opts.nodeHeight,
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
      x: nodeWithPosition.x - opts.nodeWidth / 2,
      y: nodeWithPosition.y - opts.nodeHeight / 2,
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
  const { width = 800, height = 600, iterations = 50 } = options;

  // Initialize positions randomly
  const positions = new Map<string, { x: number; y: number }>();
  nodes.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / nodes.length;
    const radius = Math.min(width, height) * 0.3;
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

  // Simple force simulation
  const repulsion = 1000;
  const attraction = 0.01;
  const damping = 0.9;

  for (let iter = 0; iter < iterations; iter++) {
    const forces = new Map<string, { fx: number; fy: number }>();

    // Initialize forces
    nodes.forEach((node) => {
      forces.set(node.id, { fx: 0, fy: 0 });
    });

    // Repulsion between all nodes
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const posA = positions.get(nodes[i].id)!;
        const posB = positions.get(nodes[j].id)!;
        const dx = posA.x - posB.x;
        const dy = posA.y - posB.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = repulsion / (dist * dist);

        const forceA = forces.get(nodes[i].id)!;
        const forceB = forces.get(nodes[j].id)!;
        forceA.fx += (dx / dist) * force;
        forceA.fy += (dy / dist) * force;
        forceB.fx -= (dx / dist) * force;
        forceB.fy -= (dy / dist) * force;
      }
    }

    // Attraction along edges
    edges.forEach((edge) => {
      const posA = positions.get(edge.source)!;
      const posB = positions.get(edge.target)!;
      const dx = posB.x - posA.x;
      const dy = posB.y - posA.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = dist * attraction;

      const forceA = forces.get(edge.source)!;
      const forceB = forces.get(edge.target)!;
      forceA.fx += (dx / dist) * force;
      forceA.fy += (dy / dist) * force;
      forceB.fx -= (dx / dist) * force;
      forceB.fy -= (dy / dist) * force;
    });

    // Apply forces with damping
    const factor = damping / (iter + 1);
    nodes.forEach((node) => {
      const pos = positions.get(node.id)!;
      const force = forces.get(node.id)!;
      pos.x += force.fx * factor;
      pos.y += force.fy * factor;

      // Keep within bounds
      pos.x = Math.max(50, Math.min(width - 50, pos.x));
      pos.y = Math.max(50, Math.min(height - 50, pos.y));
    });
  }

  // Apply positions to nodes
  const layoutedNodes = nodes.map((node) => ({
    ...node,
    position: positions.get(node.id)!,
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
