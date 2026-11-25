// 定时物化前端回归（data-sync-engineering / list-pagination / ui-resource-display）：
// 1) 单步物化落库形状 = 单节点 `materialize` DAG（数据集 → 已登记目标表 + 分区/预算）；
// 2) 运行记录页 = DAG 运行实例的**服务端分页**（total 来自服务端，翻页带 page 重新请求）；
// 3) 数据集选择器只列 SQL 型（语义型数据集物化会被执行器显式拒绝）。
import { cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import SyncLogs from '../SyncLogs';
import { formToGraph, type MaterializeForm } from '../SyncTasks';
import { useMaterializableDatasets } from '../DatasetSelect';
import { dagApi } from '@/api/dag';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/api/dag', () => ({
  dagApi: {
    listWorkflows: vi.fn(),
    listRuns: vi.fn(),
    createWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
    deleteWorkflow: vi.fn(),
    runWorkflow: vi.fn(),
    listWorkflowRuns: vi.fn(),
    deleteRun: vi.fn(),
    batchDeleteRuns: vi.fn(),
    batchStopRuns: vi.fn(),
  },
}));
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const BASE_FORM: MaterializeForm = {
  name: '订单宽表日物化', description: '', schedule_cron: '0 2 * * *',
  dataset: '订单宽表', target_datasource: 'doris_main', target_table: 'dwh.ods_order_daily',
  write_mode: 'partition_overwrite',
  partition_column: 'dt', date_arg: 'dt', target_partition: 'p{yyyyMMdd}', lookback_days: '2',
  max_rows: '500000', max_seconds: '', batch_size: '2000',
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockResolvedValue({ data: { items: [] } } as any);
});
afterEach(() => cleanup());

describe('单步物化 → 单节点 materialize DAG', () => {
  it('分区覆盖：分区/预算/批量按配置落库（不是 sync 节点）', () => {
    const graph = formToGraph(BASE_FORM);
    expect(graph.edges).toEqual([]);
    expect(graph.nodes).toHaveLength(1);
    const node = graph.nodes[0];
    expect(node.type).toBe('materialize');
    expect(node.config.dataset).toBe('订单宽表');
    expect(node.config.target_datasource).toBe('doris_main');
    expect(node.config.target_table).toBe('dwh.ods_order_daily');
    expect(node.config.write_mode).toBe('partition_overwrite');
    expect(node.config.partition).toEqual({
      column: 'dt', date_arg: 'dt', target_partition: 'p{yyyyMMdd}', lookback_days: 2,
    });
    expect(node.config.budget).toEqual({ max_rows: 500000, max_seconds: undefined });
    expect(node.config.batch_size).toBe(2000);
  });

  it('全量覆盖：不带 partition（空分区配置不得落库，否则执行器会当成按日分区）', () => {
    const graph = formToGraph({
      ...BASE_FORM, write_mode: 'overwrite', partition_column: '', date_arg: '', lookback_days: '',
    });
    expect(graph.nodes[0].config.write_mode).toBe('overwrite');
    expect(graph.nodes[0].config.partition).toBeUndefined();
  });
});

describe('数据集选择器只列可物化的 SQL 型', () => {
  it('语义型数据集被过滤，SQL 型带上参数清单（分区日期参数候选）', async () => {
    vi.mocked(client.get).mockResolvedValue({
      data: {
        items: [
          {
            name: '订单宽表', source_type: 'sql', status: 'active',
            param_summary: [{ key: 'dt', label: '业务日期', value_type: 'date' }],
          },
          { name: '语义口径数据集', source_type: 'semantic', status: 'active', param_summary: [] },
        ],
      },
    } as any);
    const { result } = renderHook(() => useMaterializableDatasets());
    await waitFor(() => expect(result.current).toHaveLength(1));
    expect(result.current[0].name).toBe('订单宽表');
    expect(result.current[0].params[0].key).toBe('dt');
    // 选择面板档：不传 page_size（全量 {items}），与 list-pagination §5 的两档契约一致
    expect(client.get).toHaveBeenCalledWith('/datasets/');
  });
});

describe('运行记录页读 DAG 运行实例（服务端分页）', () => {
  const run = {
    id: 7001, workflow_id: 9, workflow_name: '订单宽表日物化', run_key: 'k-1',
    trigger_type: 'cron', status: 'success',
    stats_json: { rows_read: 1200, rows_written: 1200 },
    started_at: '2026-11-21 02:00:00', finished_at: '2026-11-21 02:00:10',
  };

  it('total 取自服务端，翻页带 page 重新请求', async () => {
    vi.mocked(dagApi.listWorkflows).mockResolvedValue({ data: { items: [] } } as any);
    vi.mocked(dagApi.listRuns).mockResolvedValue({
      data: { items: [run], total: 120, page: 1, size: 20 },
    } as any);

    render(<MemoryRouter><SyncLogs /></MemoryRouter>);

    expect(await screen.findByText('订单宽表日物化')).toBeInTheDocument();
    expect(dagApi.listRuns).toHaveBeenCalledWith({ page: 1, size: 20 });
    // 共 120 条 → 6 页：总数不是当前页长度
    expect(screen.getByText(/共 120 条/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('下一页'));
    await waitFor(() => expect(dagApi.listRuns).toHaveBeenLastCalledWith({ page: 2, size: 20 }));
  });
});

describe('运行记录删除（写码把关 + 二次确认）', () => {
  const baseRun = {
    id: 7001, workflow_id: 9, workflow_name: '订单宽表日物化', run_key: 'k-1',
    trigger_type: 'cron', status: 'success',
    stats_json: { rows_read: 1200, rows_written: 1200 },
    started_at: '2026-11-21 02:00:00', finished_at: '2026-11-21 02:00:10',
  };

  it('确认后调 DELETE /dag/runs/:id，并重取当前页（不停在已删行上）', async () => {
    vi.mocked(dagApi.listWorkflows).mockResolvedValue({ data: { items: [] } } as any);
    vi.mocked(dagApi.listRuns).mockResolvedValue({
      data: { items: [baseRun], total: 1, page: 1, size: 20 },
    } as any);
    vi.mocked(dagApi.deleteRun).mockResolvedValue({ data: { deleted: true } } as any);

    render(<MemoryRouter><SyncLogs /></MemoryRouter>);
    await screen.findByText('订单宽表日物化');

    fireEvent.click(screen.getByLabelText('删除'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByText('删除'));

    await waitFor(() => expect(dagApi.deleteRun).toHaveBeenCalledWith(7001));
    // 删完重取（不是本地把行抽掉：total 必须来自服务端）
    await waitFor(() => expect(dagApi.listRuns).toHaveBeenCalledTimes(2));
  });

  it('运行中的记录不可删（先停止，否则执行体继续写节点行）', async () => {
    vi.mocked(dagApi.listWorkflows).mockResolvedValue({ data: { items: [] } } as any);
    vi.mocked(dagApi.listRuns).mockResolvedValue({
      data: { items: [{ ...baseRun, status: 'running', finished_at: null }], total: 1, page: 1, size: 20 },
    } as any);

    render(<MemoryRouter><SyncLogs /></MemoryRouter>);
    await screen.findByText('订单宽表日物化');

    expect(screen.getByLabelText('删除')).toBeDisabled();
  });
});

describe('运行记录筛选（全部下推服务端）', () => {
  const run = {
    id: 7001, workflow_id: 9, workflow_name: '订单宽表日物化', run_key: 'k-1',
    trigger_type: 'cron', status: 'success',
    stats_json: { rows_read: 1200, rows_written: 1200 },
    started_at: '2026-11-21 02:00:00', finished_at: '2026-11-21 02:00:10',
  };

  beforeEach(() => {
    vi.mocked(dagApi.listWorkflows).mockResolvedValue({ data: { items: [] } } as any);
    vi.mocked(dagApi.listRuns).mockResolvedValue({
      data: { items: [run], total: 1, page: 1, size: 20 },
    } as any);
  });

  it('日期区间与关键字带参重取，且回第 1 页', async () => {
    render(<MemoryRouter><SyncLogs /></MemoryRouter>);
    await screen.findByText('订单宽表日物化');

    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-11-01' } });
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-11-25' } });

    await waitFor(() => expect(dagApi.listRuns).toHaveBeenLastCalledWith({
      page: 1, size: 20, start_from: '2026-11-01', start_to: '2026-11-25',
    }));

    // 关键字防抖后才发请求（不是每敲一个字符一次）
    fireEvent.change(screen.getByLabelText('按任务名筛选'), { target: { value: '订单' } });
    await waitFor(() => expect(dagApi.listRuns).toHaveBeenLastCalledWith({
      page: 1, size: 20, start_from: '2026-11-01', start_to: '2026-11-25', q: '订单',
    }), { timeout: 2000 });
  });
});

describe('运行记录批量操作（逐条复用单条 + 逐条回报）', () => {
  const run = {
    id: 7001, workflow_id: 9, workflow_name: '订单宽表日物化', run_key: 'k-1',
    trigger_type: 'cron', status: 'success',
    stats_json: { rows_read: 1200, rows_written: 1200 },
    started_at: '2026-11-21 02:00:00', finished_at: '2026-11-21 02:00:10',
  };
  const activeRun = { ...run, id: 7002, status: 'running', started_at: '2026-11-21 03:00:00', finished_at: null };

  beforeEach(() => {
    vi.mocked(dagApi.listWorkflows).mockResolvedValue({ data: { items: [] } } as any);
    vi.mocked(dagApi.listRuns).mockResolvedValue({
      data: { items: [run, activeRun], total: 2, page: 1, size: 20 },
    } as any);
  });

  it('全选后批量删除：调用批量端点，未处理项逐条点名且保留勾选', async () => {
    vi.mocked(dagApi.batchDeleteRuns).mockResolvedValue({
      data: {
        action: 'delete',
        succeeded: [{ id: 7001, workflow_name: '订单宽表日物化' }],
        failed: [{ id: 7002, workflow_name: '订单宽表日物化', reason: '运行实例仍在排队/执行中，请先停止再删除' }],
        succeeded_count: 1,
        failed_count: 1,
        message: '已删除 1 条；1 条未处理（原因逐条列出）',
      },
    } as any);

    render(<MemoryRouter><SyncLogs /></MemoryRouter>);
    await screen.findAllByText('订单宽表日物化');

    fireEvent.click(screen.getByText('批量操作'));
    fireEvent.click(screen.getByText('本页全选'));
    expect(screen.getByText('已选 2 条')).toBeInTheDocument();

    fireEvent.click(screen.getByText('批量删除'));
    const confirm = await screen.findByRole('dialog');
    fireEvent.click(within(confirm).getByText('批量删除'));

    await waitFor(() => expect(dagApi.batchDeleteRuns).toHaveBeenCalledWith([7001, 7002]));
    // 结果逐条点名（不笼统报“部分失败”），失败项仍保持勾选可重试
    const result = await screen.findByText(/请先停止再删除/);
    expect(result).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('已选 1 条')).toBeInTheDocument());
  });

  it('批量停止：走 stop 端点，结束后重取当前页', async () => {
    vi.mocked(dagApi.batchStopRuns).mockResolvedValue({
      data: {
        action: 'stop', succeeded: [{ id: 7002 }], failed: [],
        succeeded_count: 1, failed_count: 0, message: '已停止 1 条',
      },
    } as any);

    render(<MemoryRouter><SyncLogs /></MemoryRouter>);
    await screen.findAllByText('订单宽表日物化');

    fireEvent.click(screen.getByText('批量操作'));
    fireEvent.click(screen.getByText('本页全选'));
    fireEvent.click(screen.getByText('批量停止'));
    const confirm = await screen.findByRole('dialog');
    fireEvent.click(within(confirm).getByText('停止'));

    await waitFor(() => expect(dagApi.batchStopRuns).toHaveBeenCalledWith([7001, 7002]));
    await waitFor(() => expect(dagApi.listRuns).toHaveBeenCalledTimes(2));
  });
});
