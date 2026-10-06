---
trigger: always_on
description: 图可视化规范（Graph Visualization Spec）。所有 ReactFlow 图类页面（血缘图、本体图谱、DAG 编辑器、知识图谱等）的连线/节点/布局/信息层级强制标准。风格基准为 components/graph/KnowledgeGraph.tsx；与 ui-resource-display.md 互补（本规则管"图怎么画"，UI 规范管"资源怎么显示"）。
---

# 图可视化规范（Graph Visualization Spec）— 强制规范

> 平台内一切图类可视化（数据血缘、本体图谱、DAG 编辑器、知识图谱等）以
> `components/graph/KnowledgeGraph.tsx`（KnowledgeEdge / TableNode）为**唯一风格基准**。
> 新增/修改任何 ReactFlow 图页面不得另创画风；违反以下任一条视为可视化缺陷，必须修复而非妥协。

## 1. 连线（Edge）基准 — 易错点全部在此
- **必须用 `BaseEdge` + `getBezierPath`（curvature 0.18）**柔和贝塞尔，禁止裸 `<path>` 画边。
- **必须显式 `fill: 'none'`**：SVG 开放曲线默认填充，漏写会出现黑色楔形块（历史缺陷根因）。
- 样式基准：`stroke: #94a3b8`（slate 灰）、`strokeWidth: 1.2`、`strokeLinecap: 'round'`、`opacity: 0.65`；
  **选中/强调态**（`selected`/`data.emphasized`）：`hsl(var(--primary))`、`1.8`、`opacity: 1`。
- 箭头：`MarkerType.ArrowClosed, width: 12, height: 12`，颜色与线同色（默认 `#94a3b8`）。
- **不使用 `animated: true`**（流动虚线花哨，统一静态沉稳风格）；`interactionWidth: 16` 保证可点选。
- 关系 label：`EdgeLabelRenderer` 悬浮于弧线中点（`translate(-50%,-50%) translate(labelX,labelY)`），
  样式 `bg-card/95 border border-border/70 rounded-md px-2 py-1 shadow-sm text-[11px] whitespace-nowrap`；仅在有意义的关系名（如 transform/JOIN）时显示。

## 2. 节点（Node）基准
- 卡片式节点：`bg-card border rounded-lg shadow-sm`，头部（icon + 名称）+ 底部（类型徽标/辅助信息）两段式；
  类型 icon 与配色按 `NODE_CONFIG` 映射（Table 蓝 / Column 绿等），保持全平台一致。
- 标题必须显示**可读限定名**（`数据源.表` / `表.列`，见 ui-resource-display.md），不得显示裸 id 或类型名兜底；
  `label` 取值顺序：`node_id`（限定名）→ `name`，禁止回落成 "Table"/"Column" 类型标签。
- 状态指示用小圆点（成功绿/运行蓝/失败红），不在节点上堆砌文字。
- Handle 尺寸收敛（`!w-2 !h-2` 级别），主色描边，不喧宾夺主。

## 3. 信息层级 — 图面克制，细节下钻
- **默认视图只放关系主干**（如血缘默认仅表级）；字段级/明细级信息收进**详情面板**（选中节点后展示映射清单等），
  需要时以开关（如「字段级血缘」Switch）展开，禁止把明细节点默认平铺打碎图面。
- 一图一问题：图面回答"数据/依赖从哪来、到哪去"；明细回答"具体哪些字段"。

## 4. 布局与交互
- **拓扑分层布局**：依赖方向左→右分层（按入度/深度分列，同层纵向排布），下游在右、上游在左；
  无依赖的兜底节点单独成列，禁止全部塌成一列。
- 常驻工具：`Background`（点阵）、`Controls`、必要时 `MiniMap`；进入后 `fitView`（带 padding）。
- 点击节点开详情侧栏（含上下游列表、字段映射、影响分析）；搜索与类型筛选放顶栏，样式 `h-8 text-xs` 收敛。

## 5. 数据契约 — 前后端字段必须对齐（历史缺陷根因）
- 图接口输出必须携带前端契约字段：`name`（节点可读名）、`source_id`/`target_id`（边端点）、
  `metadata` **必须是对象**（DB 存 JSON 字符串时后端先 `json.loads` 归一，否则前端 `Object.entries`
  会把字符串按字符展开）；字段缺失/类型错会让边全部被过滤、节点标题空白、布局塌陷。
- 图页面改动后必须核对：节点有标题、边可见且分层、详情面板无逐字符展开的 Metadata。

## 6. 参考实现与回归
- 基准实现：`frontend/src/components/graph/KnowledgeGraph.tsx`（KnowledgeEdge/TableNode）；
  已对齐页面：`frontend/src/pages/lineage/LineageGraph.tsx`。
- 新增图页面先复用基准组件样式常量（边色/线宽/label 样式/布局参数），改基准风格须同步全部图页面并截图对比。
- 前端门禁：`tsc --noEmit` 零错误（见"前端 tsc 正确调用口径"），并人工核对图面三项：标题可读、连线干净、分层清晰。
