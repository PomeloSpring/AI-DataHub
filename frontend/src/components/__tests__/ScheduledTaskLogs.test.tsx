import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ScheduledTaskLogs from '@/pages/admin/ScheduledTaskLogs';
import * as api from '@/api/scheduledTask';

vi.mock('@/api/scheduledTask', () => ({
  listScheduledLogs: vi.fn(), getScheduledTaskStats: vi.fn(), cleanupScheduledLogs: vi.fn(),
  updateLogStatus: vi.fn(), cleanupStaleLogs: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(api.getScheduledTaskStats).mockResolvedValue({ total_runs: 1, success_runs: 0, failed_runs: 1, success_rate: 0, avg_elapsed_ms: 0 });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function logs(status: string) {
  vi.mocked(api.listScheduledLogs).mockResolvedValue({ items: [{
    id: 1, scheduled_task_id: 2, status, trigger_type: 'manual', started_at: '2026-09-21T10:00:00',
    report_id: 3, stage_error_code: 'QUERY_FAILED', run_key: 'run-1',
  } as api.ScheduledLog], total: 1 });
  render(<ScheduledTaskLogs taskId={2} onBack={() => {}} />);
}

describe('执行历史真实状态', () => {
  it('部分成功可见，报告链接不夹带分享 token，禁止人工标记成功', async () => {
    logs('partial');
    expect(await screen.findByText('部分成功')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '报告' })).toHaveAttribute('href', '/report/3');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '详情' })); });
    expect(screen.getByText('阶段状态：QUERY_FAILED')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /标记成功/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /标记失败/ })).not.toBeInTheDocument();
  });

  it('排队任务可取消', async () => {
    logs('queued');
    await screen.findByText('排队中');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '详情' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /中止任务/ })); });
    expect(api.updateLogStatus).toHaveBeenCalledWith(1, 'cancelled', '手动中止');
  });
});
