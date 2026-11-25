// 运行详情页「返回」回到进入来源（运行记录 / 物化任务 / 任务监控），
// 而不是写死在物化任务页：本页是运行记录的下钻页，返回错页会丢掉刚看的历史。
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import DagRunDetail from '../DagRunDetail';
import { dagApi } from '@/api/dag';

vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    Handle: () => null,
    Background: () => null,
    Controls: () => null,
    ReactFlow: ({ children }: any) => <div>{children}</div>,
  };
});
vi.mock('@/components/graph/AutoFitView', () => ({ default: () => null }));
vi.mock('@/api/dag', () => ({
  dagApi: {
    getRun: vi.fn(),
    getWorkflow: vi.fn(),
    workflowDataflow: vi.fn(),
    stopRun: vi.fn(),
    retryRun: vi.fn(),
  },
}));
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const RUN = {
  id: 7001, workflow_id: 9, workflow_name: '订单宽表日物化', run_key: 'k-1',
  trigger_type: 'cron', status: 'success', stats_json: {},
  started_at: '2026-11-21 02:00:00', finished_at: '2026-11-21 02:00:10',
};

function renderAt(entry: any) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/data/sync/dag-runs/:runId" element={<DagRunDetail />} />
        <Route path="/data/sync/logs" element={<div>运行记录页</div>} />
        <Route path="/data/sync/dag" element={<div>物化任务页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(dagApi.getRun).mockResolvedValue({ data: { run: RUN, nodes: [] } } as any);
  vi.mocked(dagApi.getWorkflow).mockResolvedValue({
    data: { graph_json: { nodes: [], edges: [] } },
  } as any);
  vi.mocked(dagApi.workflowDataflow).mockResolvedValue({ data: { nodes: [], flows: [] } } as any);
});
afterEach(() => cleanup());

describe('运行详情「返回」的落点', () => {
  it('从运行记录进来 → 回运行记录（不是物化任务页）', async () => {
    renderAt({ pathname: '/data/sync/dag-runs/7001', state: { from: '/data/sync/logs' } });

    fireEvent.click(await screen.findByText('返回'));

    expect(await screen.findByText('运行记录页')).toBeInTheDocument();
    expect(screen.queryByText('物化任务页')).not.toBeInTheDocument();
  });

  it('直接打开链接（无来源）→ 回运行记录', async () => {
    renderAt('/data/sync/dag-runs/7001');

    fireEvent.click(await screen.findByText('返回'));

    expect(await screen.findByText('运行记录页')).toBeInTheDocument();
  });
});