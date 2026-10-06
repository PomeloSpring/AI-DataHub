import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import KnowledgeGraphView from './KnowledgeGraph';

const { graphData, fitView } = vi.hoisted(() => ({
  fitView: vi.fn(),
  graphData: {
    nodes: ['订单', '客户', '商品'].map((name) => ({
      id: name, label: 'Object', properties: { label: name, object_key: name },
    })),
    edges: [
      { id: 'order-customer', source: '订单', target: '客户', type: '属于', properties: { cardinality: 'N:1' } },
      { id: 'customer-product', source: '客户', target: '商品', type: '关注', properties: {} },
    ],
  },
}));
vi.mock('@/stores/graphStore', () => ({ useGraphStore: () => ({ graphData }) }));
vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    useReactFlow: () => ({ fitView, zoomIn: vi.fn(), zoomOut: vi.fn() }),
    ReactFlowProvider: ({ children }: any) => <>{children}</>,
    EdgeLabelRenderer: ({ children }: any) => <>{children}</>,
    Handle: ({ position, type }: any) => <i data-testid={`handle-${type}`} data-position={position} />,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    ReactFlow: ({ nodes, edges, nodeTypes, edgeTypes, children, ...handlers }: any) => (
      <div>
        <button onClick={handlers.onPaneClick}>画布空白</button>
        {nodes.map((node: any) => {
          const Component = nodeTypes[node.type];
          return <div key={node.id} data-testid={`node-${node.id}`} style={node.style}
            onClick={(event) => handlers.onNodeClick(event, node)}
            onMouseEnter={(event) => handlers.onNodeMouseEnter?.(event, node)}
            onMouseLeave={(event) => handlers.onNodeMouseLeave?.(event, node)}>
            <Component {...node} />
          </div>;
        })}
        {edges.map((edge: any) => {
          const Component = edgeTypes[edge.type];
          return <svg key={edge.id} data-testid={`edge-${edge.id}`}>
            <Component {...edge} sourceX={10} sourceY={10} targetX={200} targetY={200}
              sourcePosition={actual.Position.Bottom} targetPosition={actual.Position.Top} />
          </svg>;
        })}
        {children}
      </div>
    ),
  };
});

afterEach(cleanup);

const renderGraph = () => render(<KnowledgeGraphView graphType="ontology-overview" viewMode="edit" isLoading={false} />);

describe('图谱可读性', () => {
  it('连线明确禁用填充并保持细线，避免曲线被填成黑色块', () => {
    renderGraph();
    const path = document.getElementById('order-customer')!;
    expect(path).toHaveStyle({ fill: 'none', strokeWidth: '1.2' });
    expect(path).toHaveClass('react-flow__edge-path');
  });

  it('纵向布局的卡片使用上下连接点', () => {
    renderGraph();
    screen.getAllByTestId('handle-source').forEach((handle) => expect(handle).toHaveAttribute('data-position', 'bottom'));
    screen.getAllByTestId('handle-target').forEach((handle) => expect(handle).toHaveAttribute('data-position', 'top'));
  });

  it('默认不堆叠关系名，可切换显示全部关系名且不删除任何边', () => {
    renderGraph();
    expect(screen.queryByText('属于 · N:1')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '显示全部关系名' }));
    expect(screen.getByText('属于 · N:1')).toBeInTheDocument();
    expect(screen.getByText('关注')).toBeInTheDocument();
    expect(screen.getAllByTestId(/^edge-/)).toHaveLength(2);
  });

  it('选中节点聚焦直接关系，点击空白恢复总览', () => {
    renderGraph();
    fireEvent.click(screen.getByTestId('node-订单'));
    expect(screen.getByText('属于 · N:1')).toBeInTheDocument();
    expect(screen.queryByText('关注')).not.toBeInTheDocument();
    expect(document.getElementById('customer-product')).toHaveStyle({ opacity: '0.12' });
    expect(screen.getByTestId('node-商品')).toHaveStyle({ opacity: '0.3' });
    fireEvent.click(screen.getByRole('button', { name: '画布空白' }));
    expect(screen.queryByText('属于 · N:1')).not.toBeInTheDocument();
    expect(document.getElementById('customer-product')).toHaveStyle({ opacity: '0.65' });
  });

  it('悬停节点临时显示相关关系，移出后恢复', () => {
    renderGraph();
    fireEvent.mouseEnter(screen.getByTestId('node-订单'));
    expect(screen.getByText('属于 · N:1')).toBeInTheDocument();
    fireEvent.mouseLeave(screen.getByTestId('node-订单'));
    expect(screen.queryByText('属于 · N:1')).not.toBeInTheDocument();
  });
});
