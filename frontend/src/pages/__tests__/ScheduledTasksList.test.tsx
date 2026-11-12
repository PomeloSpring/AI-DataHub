// 任务调度列表：一列「执行身份」讲清任务按谁的角色跑，不再显示任务类型。
//
// 工作空间的定时任务只有 Agent 分析形态（SQL 定时在数据中台的数据同步里），所以类型列
// 是恒等值、没有信息量；而"这个任务用谁的身份、能查哪些数据"才是用户真正要看的——答案
// 是"按我的角色"，不是某个内部 AS-BOT key 或数据源编号（那两者对用户是黑盒）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import ScheduledTasks from '@/pages/admin/ScheduledTasks';
import * as api from '@/api/scheduledTask';

vi.mock('@/api/scheduledTask', () => ({
  listScheduledTasks: vi.fn(), deleteScheduledTask: vi.fn(),
  toggleScheduledTask: vi.fn(), triggerScheduledTask: vi.fn(),
}));
vi.mock('@/pages/admin/ScheduledTaskForm', () => ({ default: () => null }));
vi.mock('@/pages/admin/ScheduledTaskLogs', () => ({ default: () => null }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

// 存量任务的配置里可能还留着旧版写入的 as_bot_key/datasource_id：列表不得把它们显示出来
const task = {
  id: 12, name: '每日销售分析', description: '', workspace_id: 3, owner_id: 7,
  task_type: 'agent', trigger_type: 'cron', cron_expression: '0 9 * * *',
  task_config: { as_bot_key: 'sales', datasource_id: 8, questions: [{ title: '销量', question: '统计销量' }] },
  is_active: true, timeout_seconds: 300, max_retries: 0,
  notify_on_success: true, notify_on_failure: true, last_status: 'success', run_count: 3,
} as unknown as api.ScheduledTask;

beforeEach(() => {
  vi.mocked(api.listScheduledTasks).mockResolvedValue({ items: [task], total: 1 });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

describe('任务调度列表', () => {
  it('没有任务类型列，改为执行身份列', async () => {
    render(<ScheduledTasks />);
    await screen.findByText('每日销售分析');
    expect(screen.queryByRole('columnheader', { name: /类型/ })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '执行身份' })).toBeInTheDocument();
  });

  it('执行身份只写「按角色权限执行」，不外泄 AS-BOT key 与数据源标识', async () => {
    render(<ScheduledTasks />);
    const badge = await screen.findByText('按角色权限执行');
    expect(badge.getAttribute('title')).toMatch(/角色/);
    const row = (await screen.findByText('每日销售分析')).closest('tr');
    expect(row?.textContent).not.toMatch(/sales|datasource/i);
  });
});
