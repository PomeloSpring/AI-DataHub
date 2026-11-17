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
  _options: { width?: number; height?: number; iterations?: number } = {}
): LayoutResult {
  // 布局模型：**分簇铺开 + 环形占位 + 网格碰撞松弛**（确定性、无随机数、可复现）。
  // 旧实现是纯力导向，两个硬伤：
  //   1) 播种半径写成 `√i * 画布边长 * 0.12`，几百节点时最外环半径远超画布，节点被夹到边界成环；
  //   2) 中心区靠固定斥力（60000）堆不下 400+ 个最大 150px 的圆，视觉上糊成一团，
  //      且力导向本身不保证零重叠（历史反馈「图谱节点重叠」）。
  // 新模型：
  //   1) 簇心 = 业务对象（本体图天然是「对象 hub + 指标/维度/属性」）；无 Object 的图取高度数节点；
  //   2) 簇内成员按类型排序后沿圆周铺开，环容量由周长决定，放不下就扩环 → 同环节点间距 ≥ 直径 + 间隙；
  //   3) 簇心按度数黄金角螺旋排布，间距按各自簇半径之和 → 簇与簇不交叉；
  //   4) 末尾用网格邻域互推做碰撞松弛 → 任意两个圆不压叠。
  // 复杂度近似 O(n)（纯力导向是 O(n²·迭代)，千级节点会卡主线程）。
  if (nodes.length === 0) return { nodes, edges };

  const GAP = 34;
  // 每环最多 14 个成员：只按周长算容量会塞进 20+ 个，视觉上就是「一坨葡萄」；
  // 商用图谱每个 hub 周围是稀疏的一圈（花瓣状），密度靠分层而不是靠挤。
  const PER_RING = 14;
  // 尺寸感知：圆节点以 width/height 为准（缺省 72）
  const sizeOf = (n: Node) =>
    Math.max(n.measured?.width ?? n.width ?? 72, n.measured?.height ?? n.height ?? 72);
  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  const adjacency = new Map<string, string[]>(nodes.map((n) => [n.id, []]));
  edges.forEach((edge) => {
    adjacency.get(edge.source)?.push(edge.target);
    adjacency.get(edge.target)?.push(edge.source);
  });
  const degree = new Map<string, number>(
    nodes.map((n) => [n.id, adjacency.get(n.id)!.length])
  );

  // ── 1) 簇心：业务对象优先；无 Object 的图退化为高度数节点当锚点 ──
  const objectIds = nodes.filter((n) => n.type === 'Object').map((n) => n.id);
  const anchorIds = objectIds.length
    ? objectIds
    : [...nodes]
        .sort((a, b) => (degree.get(b.id)! - degree.get(a.id)!) || a.id.localeCompare(b.id))
        .slice(0, Math.max(1, Math.round(Math.sqrt(nodes.length) / 2)))
        .map((n) => n.id);
  const anchorSet = new Set(anchorIds);

  // ── 2) 成员归属最近的簇心（取第一个命中的锚点，保证一个节点只在一个簇里） ──
  const members = new Map<string, string[]>(anchorIds.map((id) => [id, []]));
  const stray: string[] = [];
  for (const n of nodes) {
    if (anchorSet.has(n.id)) continue;
    const hit = adjacency.get(n.id)!.find((x) => anchorSet.has(x));
    if (hit) members.get(hit)!.push(n.id);
    else stray.push(n.id);
  }
  // 簇内按类型分组（指标/维度/属性各成一段，同色成片更好读）
  const typeRank: Record<string, number> = {
    Metric: 0, Dimension: 1, Property: 2, Term: 3, Table: 4, Column: 5,
  };
  members.forEach((list) =>
    list.sort(
      (a, b) =>
        (typeRank[nodeById.get(a)!.type ?? ''] ?? 9) -
          (typeRank[nodeById.get(b)!.type ?? ''] ?? 9) || a.localeCompare(b)
    )
  );

  const positions = new Map<string, { x: number; y: number }>();
  const clusterRadius = new Map<string, number>();
  const avgSize = (ids: string[]) =>
    ids.length
      ? ids.reduce((s, id) => s + sizeOf(nodeById.get(id)!), 0) / ids.length
      : 72;

  // ── 3) 簇内「花瓣式」铺开：每环最多 PER_RING 个，角度均分；放不下才向外扩一环 ──
  const placeRinged = (
    ids: string[],
    cx: number,
    cy: number,
    startR: number,
    seedAngle: number
  ): number => {
    const d = avgSize(ids);
    let r = startR;
    let idx = 0;
    let ring = 0;
    while (idx < ids.length) {
      const capacity = Math.max(
        1,
        Math.min(PER_RING, Math.floor((2 * Math.PI * r) / (d + GAP * 2)))
      );
      const take = Math.min(capacity, ids.length - idx);
      for (let k = 0; k < take; k++) {
        const ang = seedAngle + (2 * Math.PI * k) / take + ring * 0.42;
        positions.set(ids[idx + k], {
          x: cx + r * Math.cos(ang),
          y: cy + r * Math.sin(ang),
        });
      }
      idx += take;
      ring += 1;
      if (idx < ids.length) r += d + GAP * 2; // 环间留出空白带，簇内不糊
    }
    return r + d / 2; // 该簇的外接半径（含成员半径）
  };

  // 簇间距按「最大簇半径 × 2 + 余量」，先粗估每簇所需半径
  const approxClusterRadius = (id: string) => {
    const list = members.get(id) || [];
    const d = avgSize(list);
    const perRing = Math.max(
      1,
      Math.min(PER_RING, Math.floor((2 * Math.PI * (140 + d)) / (d + GAP * 2)))
    );
    const rings = Math.ceil(list.length / perRing);
    return sizeOf(nodeById.get(id)!) / 2 + d / 2 + GAP + 26 + rings * (d + GAP * 2);
  };
  let maxCR = 120;
  let sumCR = 0;
  anchorIds.forEach((id) => {
    const cr = approxClusterRadius(id);
    sumCR += cr;
    maxCR = Math.max(maxCR, cr);
  });
  // 初摆间距用「平均簇半径」而非最大值：按最大簇取间距会把图拉成 14000px 宽，
  // 全图缩略后全是空白；簇与簇的防重叠交给下一步的斥力 + 碰撞松弛。
  const avgCR = anchorIds.length ? sumCR / anchorIds.length : maxCR;
  const spacing = Math.max(avgCR, maxCR * 0.55) * 2 + 180;

  const ordered = [...anchorIds].sort(
    (a, b) => (degree.get(b)! - degree.get(a)!) || a.localeCompare(b)
  );

  // ── 3.5) 簇心初摆 + 微调：有关系的对象要相邻，否则就是“横跨全图的贯穿长线”（图面显乱主因） ──
  // 先按黄金角确定初位，再用「簇半径斥力 + Link 弹力」跑几十轮（对象数最多几十，O(k²) 可接受）。
  const hubPos = new Map<string, { x: number; y: number }>();
  ordered.forEach((id, i) => {
    const ring = Math.sqrt(i + 0.5);
    const ang = i * 2.399963229728653; // 黄金角
    hubPos.set(id, { x: ring * spacing * Math.cos(ang), y: ring * spacing * Math.sin(ang) });
  });
  const hubEdges = edges.filter((e) => hubPos.has(e.source) && hubPos.has(e.target));
  for (let iter = 0; iter < 120; iter++) {
    const force = new Map<string, { x: number; y: number }>(
      ordered.map((id) => [id, { x: 0, y: 0 }])
    );
    // 斥力：两个 hub 至少相隔各自簇半径 + 余量
    for (let i = 0; i < ordered.length; i++) {
      for (let j = i + 1; j < ordered.length; j++) {
        const a = ordered[i];
        const b = ordered[j];
        const pa = hubPos.get(a)!;
        const pb = hubPos.get(b)!;
        const dx = pb.x - pa.x;
        const dy = pb.y - pa.y;
        const dist = Math.hypot(dx, dy) || 1;
        const min = approxClusterRadius(a) + approxClusterRadius(b) + 140;
        if (dist >= min) continue;
        const push = (min - dist) * 0.35;
        const ux = dx / dist;
        const uy = dy / dist;
        const fa = force.get(a)!;
        const fb = force.get(b)!;
        fa.x -= ux * push;
        fa.y -= uy * push;
        fb.x += ux * push;
        fb.y += uy * push;
      }
    }
    // 弹簧：有 Link 的簇心互相靠拢（静止距离 = 两簇半径 + 一条空白带）
    hubEdges.forEach((e) => {
      const pa = hubPos.get(e.source)!;
      const pb = hubPos.get(e.target)!;
      const dx = pb.x - pa.x;
      const dy = pb.y - pa.y;
      const dist = Math.hypot(dx, dy) || 1;
      const rest = approxClusterRadius(e.source) + approxClusterRadius(e.target) + 240;
      const pull = (dist - rest) * 0.02;
      const ux = dx / dist;
      const uy = dy / dist;
      const fa = force.get(e.source)!;
      const fb = force.get(e.target)!;
      fa.x += ux * pull;
      fa.y += uy * pull;
      fb.x -= ux * pull;
      fb.y -= uy * pull;
    });
    // 向心（弱）+ 应用（步长 0.7，防漂移）
    ordered.forEach((id) => {
      const p = hubPos.get(id)!;
      const f = force.get(id)!;
      p.x = (p.x + f.x * 0.7) * 0.996;
      p.y = (p.y + f.y * 0.7) * 0.996;
    });
  }

  // 簇心定稿后再展开成员（成员相对簇心固定，避免“移了 hub 忘移叶子”）
  ordered.forEach((id) => {
    const p = hubPos.get(id)!;
    positions.set(id, p);
    const list = members.get(id) || [];
    clusterRadius.set(
      id,
      list.length
        ? placeRinged(
            list,
            p.x,
            p.y,
            sizeOf(nodeById.get(id)!) / 2 + avgSize(list) / 2 + GAP + 26,
            id.length * 0.17
          )
        : sizeOf(nodeById.get(id)!) / 2
    );
  });

  // 无归属节点（孤立术语、物理桥接表等）铺在最外环，不挤占簇内空间
  if (stray.length) {
    let outer = 0;
    ordered.forEach((id) => {
      const p = positions.get(id)!;
      outer = Math.max(outer, Math.hypot(p.x, p.y) + (clusterRadius.get(id) ?? 120));
    });
    placeRinged(stray, 0, 0, outer + avgSize(stray) / 2 + GAP * 2, 0);
  }

  // ── 4) 碰撞松弛：网格邻域互推，保证任意两个圆不压叠（收敛即停） ──
  const ids = nodes.map((n) => n.id);
  const radius = new Map<string, number>(
    // hub 的名称胶囊在圆下方（约 18px 高），碰撞半径补一点，别让叶子压住名字
    nodes.map((n) => [n.id, sizeOf(n) / 2 + (n.type === 'Object' ? 12 : 0)])
  );
  let maxR = 36;
  radius.forEach((r) => {
    maxR = Math.max(maxR, r);
  });
  const cell = maxR * 2 + GAP;
  const keyOf = (x: number, y: number) => `${Math.floor(x / cell)}:${Math.floor(y / cell)}`;
  for (let iter = 0; iter < 80; iter++) {
    const grid = new Map<string, string[]>();
    for (const id of ids) {
      const p = positions.get(id)!;
      const k = keyOf(p.x, p.y);
      const bucket = grid.get(k);
      if (bucket) bucket.push(id);
      else grid.set(k, [id]);
    }
    let moved = 0;
    for (const id of ids) {
      const p = positions.get(id)!;
      const gx = Math.floor(p.x / cell);
      const gy = Math.floor(p.y / cell);
      for (let ox = -1; ox <= 1; ox++) {
        for (let oy = -1; oy <= 1; oy++) {
          const bucket = grid.get(`${gx + ox}:${gy + oy}`);
          if (!bucket) continue;
          for (const jd of bucket) {
            if (jd <= id) continue; // 同一对只处理一次
            const q = positions.get(jd)!;
            const min = radius.get(id)! + radius.get(jd)! + GAP * 0.6;
            let dx = q.x - p.x;
            let dy = q.y - p.y;
            let dist = Math.hypot(dx, dy);
            if (dist >= min) continue;
            if (dist < 0.01) {
              // 完全重合时按 id 派生一个确定方向，避免除零
              dx = ((id.length % 7) - 3) || 1;
              dy = ((jd.length % 5) - 2) || 1;
              dist = Math.hypot(dx, dy);
            }
            const push = (min - dist) / 2 + 0.5;
            const ux = dx / dist;
            const uy = dy / dist;
            p.x -= ux * push;
            p.y -= uy * push;
            q.x += ux * push;
            q.y += uy * push;
            moved += 1;
          }
        }
      }
    }
    if (!moved) break;
  }

  // Apply positions to nodes (力导向无固定方向, 连接点沿用 TB 口径)
  const sourcePosition = Position.Bottom;
  const targetPosition = Position.Top;
  const layoutedNodes = nodes.map((node) => {
    const center = positions.get(node.id)!;
    const half = sizeOf(node) / 2;
    return {
      ...node,
      // position 是节点左上角（碰撞模型用的是圆心），必须回退半径，否则整体偏移半径
      position: { x: center.x - half, y: center.y - half },
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
