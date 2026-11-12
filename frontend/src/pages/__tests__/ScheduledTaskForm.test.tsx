// 新建/编辑定时任务的表单契约：只填「要分析什么 + 什么时候跑」，不选身份与资源。
//
// 新架构里工作空间的定时任务只有 Agent 分析形态，执行身份与可查数据范围在每次执行时
// 按任务属主的角色重新解析，因此表单上不允许再长出 AS-BOT / 数据源 / 任务类型三个选择器
// （用户看不见数据源，也不该为一个他无法理解的内部标识做决定）。本用例把"字段清单"与
// "提交体"两头都锁住：选择器少回来一次就红一次，内部键混进 payload 也红一次。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ScheduledTaskForm from '@/pages/admin/ScheduledTaskForm';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import * as api from '@/api/scheduledTask';

vi.mock('@/api/scheduledTask', () => ({
  listNotificationChannels: vi.fn(), createScheduledTask: vi.fn(),
  updateScheduledTask: vi.fn(), regenerateWebhookToken: vi.fn(),
}));
vi.mock('@/stores/workspaceStore', () => ({ useWorkspaceStore: () => ({ currentWorkspaceId: 3 }) }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => ({ items: [] }) }));
vi.mock('@/components/CronInput', () => ({ default: () => null }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

beforeEach(() => {
  vi.mocked(api.listNotificationChannels).mockResolvedValue([]);
  vi.mocked(api.createScheduledTask).mockResolvedValue({ id: 1 });
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function openForm(task: api.ScheduledTask | null = null) {
  return render(
    <Dialog open><DialogContent><ScheduledTaskForm task={task} onClose={() => {}} /></DialogContent></Dialog>,
  );
}

describe('定时任务表单：零选择（能力与范围按角色继承）', () => {
  it('只剩交付相关的三个选择器，没有任务类型/AS-BOT/数据源', () => {
    openForm();
    // 触发方式 / 通知渠道 / 报告模板 —— 都是"怎么交付"，不是"用谁的身份查什么库"
    expect(screen.getAllByRole('combobox')).toHaveLength(3);
    expect(screen.queryByText('任务类型')).not.toBeInTheDocument();
    expect(screen.queryByText('数据源')).not.toBeInTheDocument();
    expect(screen.queryByText('SQL')).not.toBeInTheDocument();
    // 身份来源必须写明，否则用户不知道任务到底按谁的角色跑
    expect(screen.getByText(/按你的角色与权限执行/)).toBeInTheDocument();
  });

  it('字段清单：名称、分析问题、上下文、周期、通知、模板、高级项', () => {
    openForm();
    ['任务名称', '分析问题', '上下文（可选）', '触发方式', '通知渠道', '报告模板',
     '超时（秒）', '失败重试次数'].forEach(label => {
      expect(screen.getByText(new RegExp(label))).toBeInTheDocument();
    });
  });

  it('新建提交体只带问题与上下文，不含任何内部标识', async () => {
    openForm();
    await userEvent.type(screen.getByPlaceholderText('如：每日销售日报'), '每日销售分析');
    await userEvent.type(screen.getByPlaceholderText('标题'), '销售趋势');
    await userEvent.type(screen.getByPlaceholderText('分析本月销售趋势，找出异常波动'), '统计本月销售额');
    await userEvent.type(screen.getByPlaceholderText(/给 Agent 的额外说明/), '只看中国站点');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => expect(api.createScheduledTask).toHaveBeenCalled());
    const [req] = vi.mocked(api.createScheduledTask).mock.calls[0];
    expect(req.task_config).toEqual({
      questions: [{ title: '销售趋势', question: '统计本月销售额' }],
      context: '只看中国站点',
    });
    expect(req).not.toHaveProperty('task_type');
    expect(req).not.toHaveProperty('datasource_id');
    expect(req.workspace_id).toBe(3);
  });

  it('问题内容没填就不提交（免得长出每次执行都失败的任务）', async () => {
    openForm();
    await userEvent.type(screen.getByPlaceholderText('如：每日销售日报'), '空问题任务');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(api.createScheduledTask).not.toHaveBeenCalled();
  });

  it('存量任务里残留的 as_bot_key/datasource_id 不再回填到表单', async () => {
    const task = {
      id: 12, name: '日报', workspace_id: 3, owner_id: 7, task_type: 'agent',
      task_config: { as_bot_key: 'sales', datasource_id: 8, questions: [{ title: '数量', question: '统计订单' }] },
      cron_expression: '0 9 * * *', trigger_type: 'cron', is_active: true,
      timeout_seconds: 300, max_retries: 0, notify_on_success: true, notify_on_failure: true,
    } as unknown as api.ScheduledTask;
    openForm(task);
    await screen.findByDisplayValue('日报');
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.updateScheduledTask).mock.calls[0];
    expect(payload.task_config).toEqual({ questions: [{ title: '数量', question: '统计订单' }] });
  });
});
