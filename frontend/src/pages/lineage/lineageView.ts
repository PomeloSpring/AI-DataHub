/**
 * 血缘页面的纯视图逻辑（可单测，不依赖 DOM / ReactFlow）。
 *
 * 为什么单独拆出来：可见性判定（字段开关 / 类型显隐 / 搜索 + 1 跳邻居）与
 * 展示名口径是「图看不看得懂」的核心，写在组件里只能靠 jsdom 渲染 ReactFlow
 * 去验证（脆且慢）；拆成纯函数后可用 vitest 直接锁行为。
 */

export type LineageNodeType =
  | 'table' | 'column' | 'product' | 'ontology_object'
  | 'metric' | 'dimension' | 'dataset' | 'chart' | 'etl_job' | 'report';

export interface LineageNodeData {
  id: number;
  name: string;
  /** 带数据源维度的限定名（数据源.表 / 数据源.表.列）；图表/数据集为内部 key */
  node_id?: string;
  node_type: LineageNodeType;
  /** 表节点由后端按 node_id 前缀回填（业务名，不是 id） */
  datasource_name?: string;
  metadata?: Record<string, any>;
}

export interface LineageEdgeData {
  id: number;
  source_id: number;
  target_id: number;
  edge_type: string;
  transform_expr?: string | null;
  confidence?: number;
}

/** 血缘节点类型 → 图谱基准色板 key + 业务名（图例与节点颜色同源）。 */
export const TYPE_META: Record<string, { key: string; label: string }> = {
  table: { key: 'Table', label: '表' },
  column: { key: 'Column', label: '字段' },
  product: { key: 'Product', label: '数据产品' },
  ontology_object: { key: 'Object', label: '本体对象' },
  metric: { key: 'Metric', label: '指标' },
  dimension: { key: 'Dimension', label: '维度' },
  dataset: { key: 'Dataset', label: '数据集' },
  chart: { key: 'Chart', label: '图表' },
  etl_job: { key: 'ETLTask', label: '同步任务' },
  report: { key: 'Report', label: '报表' },
};

export const typeMeta = (type: string) => TYPE_META[type] || { key: 'Table', label: type };

/** 边类型 → 业务可读名（不展示 enum 原文）。 */
export const EDGE_LABELS: Record<string, string> = {
  produces: '产出',
  binds_to: '绑定',
  defines: '定义',
  consumed_by: '消费',
  derive: '派生',
  visualizes: '可视化',
  transform: '加工',
};

/**
 * 节点展示名：表/字段用限定名（数据源.表），资产层用业务名。
 * 图表/数据集的 node_id 是内部 key（chart:123），不得当作名称显示。
 */
export function displayLabel(n: LineageNodeData): string {
  if (n.node_type === 'table' || n.node_type === 'column') return n.node_id || n.name || '';
  return n.name || '';
}

/** 节点数超过该阈值走紧凑 LOD 渲染（大圆 + 圆内标签在千级图上会糊成一片色球）。 */
export const DENSE_THRESHOLD = 160;
/** 边数超过该阈值不渲染关系标签（上千个绝对定位标签会拖垮渲染）。 */
export const EDGE_LABEL_LIMIT = 240;

/**
 * 主干类型：走 hub 渲染（圆点 + 圆外名称胶囊，胶囊按 1/zoom 反向缩放，缩远仍可读）。
 * 血缘的分档结构是「表 → 产品 → 本体对象 → 指标/维度 → 数据集 → 图表」，
 * 一档动辄上百个节点，全图 fitView 只能压到 0.02（亚像素点阵）。
 * 所以只让主干常显名、明细走小圆点 + 缩放分级出标签（与 KnowledgeGraph 同款 LOD）。
 */
export const HUB_TYPES: ReadonlySet<string> = new Set(['table', 'product', 'ontology_object']);
export const isHubType = (type: string): boolean => HUB_TYPES.has(type);

/** 聚焦默认展开的跳数：1 跳只剩直接上下游，2 跳才看得到「表→产品→对象/指标」的来龙去脉。 */
export const FOCUS_HOPS = 2;
/**
 * 聚焦子图节点上限：超过就退回 1 跳。
 * 扇出巨大的表（一张操作日志挂几百张图表）2 跳后仍是一条竖带，
 * 与全图一样不可读——聚焦的意义是“看一条链”，不是“换个方式看全部”。
 */
export const FOCUS_MAX_NODES = 120;

/** 搜索命中集合（按展示名子串，大小写不敏感）。空关键词 = 空集合。 */
export function computeSearchHits(nodes: LineageNodeData[], query: string): Set<number> {
  const q = String(query || '').toLowerCase().trim();
  if (!q) return new Set<number>();
  return new Set(
    nodes
      .filter((n) => (displayLabel(n) || n.name || '').toLowerCase().includes(q))
      .map((n) => n.id),
  );
}

/**
 * 可见节点集合：① 字段级开关（默认隐藏 column）；② 图例点选隐藏的类型；
 * ③ 搜索命中节点 + 其直接上下游（只留命中点会变成孤立点，看不出「从哪来、到哪去」）。
 */
export function computeVisibleIds(params: {
  nodes: LineageNodeData[];
  edges: LineageEdgeData[];
  hiddenTypes: ReadonlySet<string>;
  showColumns: boolean;
  searchHits: ReadonlySet<number>;
}): Set<number> {
  const { nodes, edges, hiddenTypes, showColumns, searchHits } = params;
  const base = new Set<number>();
  for (const n of nodes) {
    if (!showColumns && n.node_type === 'column') continue;
    if (hiddenTypes.has(n.node_type)) continue;
    base.add(n.id);
  }
  if (searchHits.size === 0) return base;
  const keep = new Set<number>();
  for (const id of searchHits) if (base.has(id)) keep.add(id);
  // 1 跳邻居必须基于「命中快照」展开，不能在遍历里边加边扩（否则结果随边序变成 N 跳）
  const seeds = [...keep];
  const fwd = new Map<number, number[]>();
  const rev = new Map<number, number[]>();
  for (const e of edges) {
    if (!fwd.has(e.source_id)) fwd.set(e.source_id, []);
    fwd.get(e.source_id)!.push(e.target_id);
    if (!rev.has(e.target_id)) rev.set(e.target_id, []);
    rev.get(e.target_id)!.push(e.source_id);
  }
  for (const id of seeds) {
    for (const nxt of fwd.get(id) || []) if (base.has(nxt)) keep.add(nxt);
    for (const pre of rev.get(id) || []) if (base.has(pre)) keep.add(pre);
  }
  return keep;
}

/** 图例项：按图中实际出现的类型统计数量，按数量倒序（颜色由调用方从同一色板取）。 */
export function buildLegend(nodes: LineageNodeData[]): Array<{ type: string; label: string; count: number }> {
  const counts = new Map<string, number>();
  for (const n of nodes) counts.set(n.node_type, (counts.get(n.node_type) || 0) + 1);
  return [...counts.entries()]
    .map(([type, count]) => ({ type, label: typeMeta(type).label, count }))
    .sort((a, b) => b.count - a.count);
}

/**
 * 聚焦展开：以 rootId 为中心取 N 跳邻域（无向，上下游都要看），结果始终 ∩ baseIds。
 * root 不在可见集（被类型筛选/字段开关藏掉）时原样返回，不制造空图。
 */
export function expandNeighbors(
  baseIds: ReadonlySet<number>,
  edges: LineageEdgeData[],
  rootId: number,
  hops: number = FOCUS_HOPS,
): Set<number> {
  if (!baseIds.has(rootId) || hops <= 0) return new Set(baseIds);
  const adj = new Map<number, number[]>();
  for (const e of edges) {
    if (!adj.has(e.source_id)) adj.set(e.source_id, []);
    adj.get(e.source_id)!.push(e.target_id);
    if (!adj.has(e.target_id)) adj.set(e.target_id, []);
    adj.get(e.target_id)!.push(e.source_id);
  }
  const keep = new Set<number>([rootId]);
  let frontier = [rootId];
  for (let h = 0; h < hops && frontier.length > 0; h++) {
    const next: number[] = [];
    for (const id of frontier) {
      for (const nb of adj.get(id) || []) {
        if (baseIds.has(nb) && !keep.has(nb)) {
          keep.add(nb);
          next.push(nb);
        }
      }
    }
    frontier = next;
  }
  return keep;
}

/**
 * 首屏聚焦种子：选「2 跳邻域不超上限、且连接最多」的表（tie 按名称，结果确定可复现）。
 * 为什么不是“度数最高的表”：度数最高往往是扇出几百张图表的大宽表，2 跳后仍不可读；
 * 也不能取字母序第一张——常常只有一两根边，首屏看过去像没有血缘。
 */
export function pickFocusSeed(
  nodes: LineageNodeData[],
  edges: LineageEdgeData[],
  maxNodes: number = FOCUS_MAX_NODES,
): number | null {
  if (nodes.length === 0) return null;
  const adj = new Map<number, number[]>();
  const degree = new Map<number, number>();
  for (const e of edges) {
    if (!adj.has(e.source_id)) adj.set(e.source_id, []);
    adj.get(e.source_id)!.push(e.target_id);
    if (!adj.has(e.target_id)) adj.set(e.target_id, []);
    adj.get(e.target_id)!.push(e.source_id);
    degree.set(e.source_id, (degree.get(e.source_id) || 0) + 1);
    degree.set(e.target_id, (degree.get(e.target_id) || 0) + 1);
  }
  const neighborhoodSize = (id: number, hops: number): number => {
    const seen = new Set<number>([id]);
    let frontier = [id];
    for (let h = 0; h < hops && frontier.length > 0; h++) {
      const next: number[] = [];
      for (const cur of frontier) {
        for (const nb of adj.get(cur) || []) {
          if (!seen.has(nb)) {
            seen.add(nb);
            next.push(nb);
          }
        }
      }
      frontier = next;
    }
    return seen.size;
  };

  const tables = nodes.filter((n) => n.node_type === 'table');
  const pool = tables.length > 0 ? tables : nodes;
  const byLabel = (a: LineageNodeData, b: LineageNodeData) =>
    displayLabel(a).localeCompare(displayLabel(b)) || a.id - b.id;

  // 优先：2 跳不超上限且至少有一条边（孤立表首屏没东西可看）
  const fitting = pool
    .map((n) => ({ n, size: neighborhoodSize(n.id, FOCUS_HOPS) }))
    .filter(({ size }) => size > 1 && size <= maxNodes)
    .sort(
      (a, b) =>
        (degree.get(b.n.id) || 0) - (degree.get(a.n.id) || 0) || byLabel(a.n, b.n),
    );
  if (fitting.length > 0) return fitting[0].n.id;

  // 兜底：全是扇出巨表时取最小的那条链（用 1 跳大小估）
  const fallback = pool
    .map((n) => ({ n, size: neighborhoodSize(n.id, 1) }))
    .sort((a, b) => a.size - b.size || byLabel(a.n, b.n));
  return fallback[0].n.id;
}