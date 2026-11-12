import { useMemo, useState } from 'react';
import { ReactFlow, Background, Controls, MarkerType, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  CircleNode, KnowledgeEdge, NODE_CONFIG,
} from '@/components/graph/KnowledgeGraph';
import { applyDagreLayout } from '@/components/graph/utils/layout';
import { LeafEditor, emptyBranch, emptyLeaf } from '@/components/decision/DecisionTreeEditor';
import type { DecisionAdviceRow, DecisionTreeDocInput, DecisionTreeNode } from '@/api/decisions';

/**
 * 决策树的图面视图（判定方向左→右）。
 *
 * 复用图可视化基准，不自建第二套画法：
 * - 节点 = `KnowledgeGraph` 的 `CircleNode`（实心圆、按 `NODE_CONFIG` 着色），
 *   分支取「规则」色、叶子取「行为」色（叶子产出的正是一条建议动作）；
 * - 连线 = 基准 `KnowledgeEdge`（`BaseEdge` + `getBezierPath` + 显式 `fill:'none'`）；
 * - 布局 = `applyDagreLayout` 的 `LR` 拓扑（与数据血缘同一条判定方向惯例）。
 *
 * 一图一问题：阈值、单位、时间窗、建议正文这些明细**不摊在图面上**，只在选中
 * 节点后于右侧详情侧栏编辑（基准 §3）。图面与表单视图共用同一份 `tree_doc`，
 * 两边改的都是同一个对象，不存在"切了视图数据就分叉"。
 */

const ROOT_ID = 'root';

/** 图面节点语义 → 基准配色（只从 NODE_CONFIG 取，不新增色值） */
const BRAND_NODE_TYPE = 'Rule';
const LEAF_NODE_TYPE = 'Action';

/**
 * 图面节点尺寸：不复用基准 `circleSize`（上限 150）—— 那是本体图“少量大圆 +
 * 标签当节点名”的形状；决策树是主干拓扑，一屏十几个节点，沿用那个尺寸会把
 * 图面占满、看不出层级（2026-11-12 反馈）。画法/配色/连线仍全部走基准组件，
 * 这里只收尺寸（`CircleNode` 真实消费 `data.size`）。
 */
const NODE_SIZE = 92;
const nodeSize = (label: string): number =>
  Math.max(60, Math.min(NODE_SIZE, 44 + Array.from(label).length * 7));

type IdxPath = number[];

const toPath = (id: string): IdxPath => (id === ROOT_ID ? [] : id.split('.').map(Number));
const toId = (path: IdxPath): string => (path.length ? path.join('.') : ROOT_ID);

function navigate(node: DecisionTreeNode, path: IdxPath): DecisionTreeNode | null {
  let cur: DecisionTreeNode | null = node;
  for (const i of path) {
    if (!cur || cur.type !== 'branch') return null;
    cur = cur.children[i] ?? null;
  }
  return cur;
}

function replaceAt(root: DecisionTreeNode, path: IdxPath, next: DecisionTreeNode): DecisionTreeNode {
  if (!path.length) return next;
  if (root.type !== 'branch') return root;
  const [head, ...rest] = path;
  const children = root.children.map((c, i) => (i === head ? replaceAt(c, rest, next) : c));
  return { ...root, children };
}

/** 删除非根节点：父分支不能因此空掉（后端契约 children min_length=1），补一条空判定。 */
function removeAt(root: DecisionTreeNode, path: IdxPath): DecisionTreeNode {
  if (!path.length) return root;
  const parentPath = path.slice(0, -1);
  const parent = navigate(root, parentPath);
  if (!parent || parent.type !== 'branch') return root;
  const idx = path[path.length - 1];
  const children = parent.children.filter((_, i) => i !== idx);
  return replaceAt(root, parentPath, {
    ...parent, children: children.length ? children : [emptyLeaf()],
  } as DecisionTreeNode);
}

function flatten(node: DecisionTreeNode, path: IdxPath, out: { id: string; path: IdxPath; node: DecisionTreeNode }[]) {
  out.push({ id: toId(path), path, node });
  if (node.type === 'branch') node.children.forEach((c, i) => flatten(c, [...path, i], out));
}

/** 叶子是否"还差东西"：没指标或没选建议 = 这棵树发布不了，图面上先标出来。 */
function leafIncomplete(leaf: DecisionTreeNode): boolean {
  return leaf.type === 'leaf' && (!leaf.advice_key || !leaf.when?.metric);
}

export default function DecisionTreeGraph({
  value, onChange, adviceRows, readOnly,
}: {
  value: DecisionTreeDocInput;
  onChange: (next: DecisionTreeDocInput) => void;
  adviceRows: DecisionAdviceRow[];
  readOnly?: boolean;
}) {
  const root = value.root || (emptyBranch() as DecisionTreeNode);
  const [selectedId, setSelectedId] = useState<string>(ROOT_ID);

  const entries = useMemo(() => {
    const out: { id: string; path: IdxPath; node: DecisionTreeNode }[] = [];
    flatten(root, [], out);
    return out;
  }, [root]);

  const { nodes, edges } = useMemo(() => {
    const rawNodes: Node[] = entries.map((e) => {
      const isBranch = e.node.type === 'branch';
      const label = String(e.node.label || (isBranch ? (e.path.length ? '分支' : '判定起点') : '未命名情形'));
      const size = nodeSize(label);
      const incomplete = !isBranch && leafIncomplete(e.node);
      return {
        id: e.id,
        type: 'decision',
        position: { x: 0, y: 0 },
        width: size,
        height: size,
        selected: e.id === selectedId,
        draggable: false,
        data: {
          nodeType: isBranch ? BRAND_NODE_TYPE : LEAF_NODE_TYPE,
          label,
          size,
          // 琥珀点 = 这条判定还不完整（缺指标或缺建议），不是"风险等级"
          statusDot: incomplete ? '#f59e0b' : undefined,
          statusText: incomplete ? '还差指标或建议动作，发布会被阻断' : '',
        },
      };
    });
    const rawEdges: Edge[] = [];
    entries.forEach((e) => {
      if (e.node.type !== 'branch') return;
      e.node.children.forEach((_, i) => rawEdges.push({
        id: `${e.id}->${i}`,
        source: e.id,
        target: [...e.path, i].join('.'),
        type: 'knowledge',
        data: { label: e.node.type === 'branch' && e.node.op === 'any' ? '任一' : '且' },
      }));
    });
    const laid = applyDagreLayout(rawNodes, rawEdges, {
      direction: 'LR', nodeWidth: NODE_SIZE, nodeHeight: NODE_SIZE, ranksep: 110, nodesep: 26, edgesep: 18,
    });
    return { nodes: laid.nodes, edges: laid.edges };
  }, [entries, selectedId]);

  const legend = useMemo(() => {
    const used = Array.from(new Set(nodes.map((n: any) => String(n.data?.nodeType))));
    return used.map((t) => ({ type: t, ...(NODE_CONFIG[t] || { color: '#64748b', label: t }) }));
  }, [nodes]);

  const selectedEntry = useMemo(() => {
    const path = toPath(selectedId);
    const node = navigate(root, path);
    // 选中的 id 在当前 doc 里找不到（外部改了树、刚新增 yet 未回下一帧）：回到根，
    // 绝不往一个悬空路径上写（那会写成“按了没反应”的静默失败）
    return node ? { path, node } : { path: [] as IdxPath, node: root };
  }, [root, selectedId]);
  const selected = selectedEntry.node;
  const selectedPath = selectedEntry.path;

  const patch = (next: DecisionTreeNode) =>
    onChange({ ...value, root: replaceAt(root, selectedPath, next) as any });

  const addChild = (kind: 'leaf' | 'branch') => {
    const target = selectedPath.length ? selected : root;
    if (!target || target.type !== 'branch') return;
    patch({ ...target, children: [...target.children, kind === 'leaf' ? emptyLeaf() : emptyBranch()] } as DecisionTreeNode);
    setSelectedId(toId([...selectedPath, target.children.length]));
  };

  const removeSelected = () => {
    if (!selectedPath.length) return;
    const parentPath = selectedPath.slice(0, -1);
    onChange({ ...value, root: removeAt(root, selectedPath) as any });
    setSelectedId(toId(parentPath));
  };

  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_360px]">
      <div className="relative h-[360px] overflow-hidden rounded-xl border border-border bg-muted/20">
        {/* 图例与节点配色同源（基准 §1：顶部图例，色值只从 NODE_CONFIG 取） */}
        <div className="absolute left-3 top-3 z-40 flex flex-wrap items-center gap-3 rounded-lg border border-border/60 bg-card/90 px-2.5 py-1.5 text-xs">
          {legend.map((l) => (
            <span key={l.type} className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
          <span className="text-muted-foreground">判定方向 左 → 右</span>
        </div>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={{ decision: CircleNode }}
          edgeTypes={{ knowledge: KnowledgeEdge }}
          onNodeClick={(_, node) => setSelectedId(node.id)}
          onPaneClick={() => setSelectedId(ROOT_ID)}
          fitView
          fitViewOptions={{ padding: 0.18, maxZoom: 1 }}
          minZoom={0.2}
          maxZoom={1.4}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{
            type: 'knowledge',
            markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: '#94a3b8' },
          }}
        >
          <Background gap={24} size={0.6} color="hsl(var(--muted-foreground) / 0.2)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <aside className="space-y-3 rounded-xl border border-border p-3" aria-label="节点详情">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium">
            {selectedPath.length === 0 ? '根判定' : selected?.type === 'leaf' ? '判定条件明细' : '分支明细'}
          </h3>
          {!readOnly && (
            <span className="ml-auto flex gap-1">
              {selected?.type === 'branch' && (
                <>
                  <Button size="sm" variant="outline" aria-label="图面加判定条件" onClick={() => addChild('leaf')}>
                    <Plus className="h-4 w-4" /><span className="ml-1">条件</span>
                  </Button>
                  <Button size="sm" variant="outline" aria-label="图面加分支" onClick={() => addChild('branch')}>
                    <Plus className="h-4 w-4" /><span className="ml-1">分支</span>
                  </Button>
                </>
              )}
              {selectedPath.length > 0 && (
                <Button size="sm" variant="ghost" aria-label="图面删除节点" className="text-red-600"
                  onClick={removeSelected}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              )}
            </span>
          )}
        </div>

        {selected?.type === 'leaf' ? (
          <LeafEditor leaf={selected} adviceRows={adviceRows} readOnly={readOnly} onChange={patch} />
        ) : selected ? (
          <div className="space-y-2">
            <Input aria-label="图面分支名称" value={selected.label} disabled={readOnly}
              placeholder={selectedPath.length ? '分支名称' : '这棵树在判什么（如：精度风险判定）'}
              onChange={(e) => patch({ ...selected, label: e.target.value } as DecisionTreeNode)}
              className="h-8 text-sm" />
            <select aria-label="图面组合方式" value={selected.op} disabled={readOnly}
              onChange={(e) => patch({ ...selected, op: e.target.value as 'all' | 'any' } as DecisionTreeNode)}
              className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm">
              <option value="all">全部满足（且）</option>
              <option value="any">任一满足（或）</option>
            </select>
            <p className="text-xs text-muted-foreground">
              该分支下有 {selected.children.length} 个子节点。阈值、单位与建议正文请在右侧叶子明细里改，图面只画判定主干。
            </p>
          </div>
        ) : null}

        <p className="text-xs text-muted-foreground">
          选中图面节点即可在此改明细；明细不摊在图上（一图只回答一个判断问题）。
        </p>
      </aside>
    </div>
  );
}
