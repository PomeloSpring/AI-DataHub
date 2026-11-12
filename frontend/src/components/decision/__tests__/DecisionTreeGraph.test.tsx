import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { DecisionAdviceRow, DecisionTreeDocInput } from '@/api/decisions';

const { seen } = vi.hoisted(() => ({
  seen: { nodes: [] as any[], edges: [] as any[] },
}));

// 复用图基准的测试替身：ReactFlow 本体不在 jsdom 里排版，这里把节点/边交回真实
// CircleNode / KnowledgeEdge 渲染，并留下最后一次的 nodes/edges 供断言布局方向。
vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    useReactFlow: () => ({ fitView: vi.fn(), zoomIn: vi.fn(), zoomOut: vi.fn() }),
    ReactFlowProvider: ({ children }: any) => <>{children}</>,
    EdgeLabelRenderer: ({ children }: any) => <>{children}</>,
    Handle: ({ position, type }: any) => <i data-testid={`handle-${type}`} data-position={position} />,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    ReactFlow: ({ nodes, edges, nodeTypes, edgeTypes, children, ...handlers }: any) => {
      seen.nodes = nodes;
      seen.edges = edges;
      return (
        <div>
          <button onClick={handlers.onPaneClick}>画布空白</button>
          {nodes.map((node: any) => {
            const Component = nodeTypes[node.type];
            return <div key={node.id} data-testid={`node-${node.id}`}
              onClick={(event) => handlers.onNodeClick(event, node)}>
              <Component {...node} />
            </div>;
          })}
          {edges.map((edge: any) => {
            const Component = edgeTypes[edge.type];
            return <svg key={edge.id} data-testid={`edge-${edge.id}`}>
              <Component {...edge} sourceX={10} sourceY={10} targetX={200} targetY={200}
                sourcePosition={actual.Position.Right} targetPosition={actual.Position.Left} />
            </svg>;
          })}
          {children}
        </div>
      );
    },
  };
});

import DecisionTreeGraph from '../DecisionTreeGraph';

const adviceRows: DecisionAdviceRow[] = [{
  id: 1, advice_key: 'adv_recalibrate', action: '安排一次现场校准', rationale: '',
  severity: 'high', kb_refs: [], workspace_id: 0, is_active: 1,
}];

const DOC: DecisionTreeDocInput = {
  root: {
    type: 'branch', label: '精度风险判定', op: 'all', children: [
      { type: 'leaf', label: '校准超差', advice_key: 'adv_recalibrate',
        when: { metric: '扫描精度偏差均值', comparator: '>=', value: 0.05, unit: 'mm' } },
      { type: 'branch', label: '再看负载', op: 'any', children: [
        { type: 'leaf', label: '高负载', advice_key: 'adv_recalibrate',
          when: { metric: '扫描精度偏差均值', comparator: '>', value: 0.1 } },
      ] },
    ],
  },
  note: '',
};

const clone = (d: DecisionTreeDocInput): DecisionTreeDocInput => JSON.parse(JSON.stringify(d));

afterEach(cleanup);

const renderGraph = (onChange = vi.fn(), value = clone(DOC)) => {
  render(<DecisionTreeGraph value={value} onChange={onChange} adviceRows={adviceRows} />);
  return onChange;
};

/** 取最后一次 onChange 回写的 doc（当前 tsconfig lib 不含 Array.prototype.at） */
const lastDoc = (onChange: ReturnType<typeof vi.fn>) => {
  const calls = onChange.mock.calls as any[][];
  return calls[calls.length - 1][0] as DecisionTreeDocInput;
};

describe('决策树图面视图（复用图可视化基准）', () => {
  it('节点用基准圆节点与统一配色：分支取「规则」色、叶子取「行为」色', () => {
    renderGraph();
    const root = screen.getByTestId('node-root').firstElementChild as HTMLElement;
    const leaf = screen.getByTestId('node-0').firstElementChild as HTMLElement;
    expect(root).toHaveClass('rounded-full');
    expect(leaf).toHaveClass('rounded-full');
    expect(root.style.background).toBe('rgb(245, 158, 11)');    // NODE_CONFIG.Rule
    expect(leaf.style.background).toBe('rgb(16, 185, 129)');    // NODE_CONFIG.Action
    // 图例与节点同源（不另写一套色表）
    expect(screen.getByText('规则')).toBeTruthy();
    expect(screen.getByText('行为')).toBeTruthy();
  });

  it('连线沿用基准 KnowledgeEdge：BaseEdge + 显式 fill:none', () => {
    renderGraph();
    const edge = document.getElementById('root->0')!;
    expect(edge).toHaveClass('react-flow__edge-path');
    expect(edge).toHaveStyle({ fill: 'none' });
    expect(seen.edges).toHaveLength(3);   // 根 2 条 + 二级分支 1 条，一条不丢
  });

  it('判定方向左→右：子节点在父节点右侧，连接点在左右两侧', () => {
    renderGraph();
    const byId = Object.fromEntries(seen.nodes.map((n: any) => [n.id, n]));
    expect(byId['0'].position.x).toBeGreaterThan(byId['root'].position.x);
    expect(byId['1'].position.x).toBeGreaterThan(byId['root'].position.x);
    expect(byId['1.0'].position.x).toBeGreaterThan(byId['1'].position.x);
    expect(byId['0'].data.sourcePosition).toBe('right');
    expect(byId['0'].data.targetPosition).toBe('left');
  });

  it('节点尺寸收得住：不沿用本体图的大圆上限，也不让 fitView 把少量节点放大到占满画布', () => {
    /* 2026-11-12 反馈“太大了”：基准 circleSize 上限 150 + fitView 默认 maxZoom=2，
       两个节点的树会被拉到满屏。画法仍走基准，但尺寸必须自己封顶。 */
    renderGraph();
    for (const n of seen.nodes) {
      expect(n.width).toBeLessThanOrEqual(96);
      expect(n.height).toBeLessThanOrEqual(96);
      expect(n.data.size).toBeLessThanOrEqual(96);
    }
    const src = fs.readFileSync(path.resolve(__dirname, '..', 'DecisionTreeGraph.tsx'), 'utf8');
    expect(src).toMatch(/maxZoom:\s*1[,\s}]/);
  });

  it('明细不平铺在图面上：阈值/单位只出现在选中节点的详情侧栏', () => {
    renderGraph();
    const canvas = screen.getByTestId('node-0').textContent || '';
    expect(canvas).not.toContain('0.05');
    expect(canvas).not.toContain('mm');

    fireEvent.click(screen.getByTestId('node-0'));
    const sidebar = screen.getByLabelText('节点详情');
    // 情形描述在侧栏的输入框里（文本内容不含输入值，拿值断言）
    expect((screen.getByLabelText('情形描述') as HTMLInputElement).value).toBe('校准超差');
    expect(sidebar.textContent).toContain('判定条件明细');
    expect((screen.getByLabelText('阈值') as HTMLInputElement).value).toBe('0.05');
  });

  it('在侧栏改阈值会写回同一份 tree_doc（两种视图共用一份事实源）', () => {
    const onChange = renderGraph();
    fireEvent.click(screen.getByTestId('node-0'));
    fireEvent.change(screen.getByLabelText('阈值'), { target: { value: '0.08' } });
    const next = lastDoc(onChange);
    const leaf = (next.root as any).children[0];
    expect(leaf.when.value).toBe(0.08);
    expect(leaf.advice_key).toBe('adv_recalibrate');
  });

  it('图面可加判定条件与分支，新增节点立刻出现在画布上', () => {
    const onChange = renderGraph();
    fireEvent.click(screen.getByLabelText('图面加判定条件'));
    let next = lastDoc(onChange);
    expect((next.root as any).children).toHaveLength(3);

    fireEvent.click(screen.getByLabelText('图面加分支'));
    next = lastDoc(onChange);
    expect((next.root as any).children).toHaveLength(3);
    expect((next.root as any).children[2].type).toBe('branch');
  });

  it('删除叶子后父分支不空掉（契约要求 children≥1）', () => {
    const onChange = renderGraph(vi.fn(), clone(DOC));
    fireEvent.click(screen.getByTestId('node-1.0'));
    fireEvent.click(screen.getByLabelText('图面删除节点'));
    const next = lastDoc(onChange);
    const branch = (next.root as any).children[1];
    expect(branch.children).toHaveLength(1);
    expect(branch.children[0].type).toBe('leaf');
  });

  it('不完整叶子（缺指标或缺建议）在图面上标出来，而不是等发布才报错', () => {
    const value = clone(DOC);
    (value.root as any).children[0].advice_key = '';
    render(<DecisionTreeGraph value={value} onChange={vi.fn()} adviceRows={adviceRows} />);
    const dot = screen.getByTestId('node-0').querySelector('span[title]') as HTMLElement;
    expect(dot.getAttribute('title')).toContain('发布会被阻断');
  });

  it('只读态不给编辑入口', () => {
    render(<DecisionTreeGraph value={clone(DOC)} onChange={vi.fn()} adviceRows={adviceRows} readOnly />);
    expect(screen.queryByLabelText('图面加判定条件')).toBeNull();
    expect(screen.queryByLabelText('图面删除节点')).toBeNull();
  });
});

describe('图面视图静态护栏：必须复用基准件而不是自画一套', () => {
  const SRC = path.resolve(__dirname, '..', 'DecisionTreeGraph.tsx');
  const code = fs.readFileSync(SRC, 'utf8');

  it('圆节点/连线/布局都从基准模块导入', () => {
    expect(code).toMatch(/from '@\/components\/graph\/KnowledgeGraph'/);
    for (const sym of ['CircleNode', 'KnowledgeEdge', 'NODE_CONFIG']) {
      expect(code, `缺基准符号 ${sym}`).toContain(sym);
    }
    expect(code).toContain('applyDagreLayout');
    expect(code).toContain("'LR'");
  });

  it('不自建连线画法，也不把明细写进节点', () => {
    for (const banned of ['getSmoothStepPath', 'getSimpleBezierPath', '<path ', 'getEdgePath']) {
      expect(code, `图面里出现了自画连线的痕迹：${banned}`).not.toContain(banned);
    }
    // 节点上只放 label：阈值/单位/建议正文走侧栏（基准 §3 一图一问题）
    expect(code).not.toMatch(/data:\s*\{[^}]*threshold/);
  });
});
