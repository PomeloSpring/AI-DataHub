import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import WorkspaceManager from '@/pages/WorkspaceManagerV2';
import KnowledgeBase from '@/pages/admin/KnowledgeBase';
import ScheduledTaskForm from '@/pages/admin/ScheduledTaskForm';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import client from '@/api/client';
import * as api from '@/api/scheduledTask';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));
vi.mock('@/api/scheduledTask', () => ({
  listNotificationChannels: vi.fn(),
  createScheduledTask: vi.fn(), updateScheduledTask: vi.fn(), regenerateWebhookToken: vi.fn(),
}));
vi.mock('@/stores/workspaceStore', () => ({ useWorkspaceStore: () => ({ currentWorkspaceId: 99 }) }));
vi.mock('@/components/CronInput', () => ({ default: () => null }));

// 存量任务配置里仍带着旧版写入的 as_bot_key/datasource_id：表单必须不再读它们、
// 也不再渲染对应选择器（新架构按属主角色继承，任务不持有内部标识）。
const task = {
  id: 12, name: '日报', task_type: 'agent', workspace_id: 3, owner_id: 7,
  task_config: { datasource_id: 8, as_bot_key: 'sales', questions: [{ title: '数量', question: '统计订单' }] },
  cron_expression: '0 9 * * *', trigger_type: 'cron', is_active: true,
  timeout_seconds: 300, max_retries: 0, notify_on_success: true, notify_on_failure: true,
} as unknown as api.ScheduledTask;

beforeEach(() => {
  vi.mocked(client.get).mockImplementation(async (url) => {
    if (url === '/workspaces') return { data: [{ id: 3, name: '测试空间', role: 'owner', user_default: true }] };
    if (url === '/datasources/') return { data: [{ id: 8, name: '订单', db_type: 'mysql', database_name: 'test' }] };
    if (url === '/knowledge-bases') return { data: [{ id: 4, name: '业务知识', kb_type: 'qmind', status: 'active', document_count: 0 }] };
    return { data: [] };
  });
  vi.mocked(api.listNotificationChannels).mockResolvedValue([]);
  // 报告模板候选现取字模库（useVisLibrary），scheduledTask 侧不再有该入口
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function form(value: api.ScheduledTask | null = task) {
  return render(<Dialog open><DialogContent><ScheduledTaskForm task={value} onClose={() => {}} /></DialogContent></Dialog>);
}

describe('AS-BOT 唯一资源绑定入口', () => {
  it('工作空间统管页按用户展示空间与配额，成员/角色配置已退役', async () => {
    vi.mocked(client.get).mockImplementation(async (url) => {
      if (url === '/workspaces/quotas') return {
        data: [{
          user_id: 10, username: 'zhangsan', max_workspaces: 5, disk_quota_bytes: 5 * 1024 ** 3,
          workspaces: [{ id: 3, name: '测试空间', is_default: 1, disk_usage_bytes: 1024 }],
        }],
      };
      return { data: [] };
    });
    render(<WorkspaceManager />);
    await screen.findByText('zhangsan');
    // 空间卡片: 名称 + 默认标 + 磁盘用量条(双配额统管)
    expect(screen.getByText('测试空间')).toBeInTheDocument();
    expect(screen.getByText(/1\/5 个空间/)).toBeInTheDocument();
    expect(screen.getByText('配额设置')).toBeInTheDocument();
    // 成员/角色配置已退役(权限由用户角色裁决, 工作空间随用户走)
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(vi.mocked(client.get).mock.calls.some(([url]) => /knowledge-bases|mcp-servers|menu-tree/.test(String(url)))).toBe(false);
  });

  it('知识库目录不再展示或修改工作空间关联', async () => {
    render(<KnowledgeBase />);
    await screen.findByText('业务知识');
    expect(screen.queryByRole('columnheader', { name: '关联工作空间' })).not.toBeInTheDocument();
    expect(screen.queryByTitle('关联工作空间')).not.toBeInTheDocument();
    expect(screen.getByText(/使用范围统一在 AS-BOT/)).toBeInTheDocument();
  });

  it('编辑任务不再渲染 AS-BOT 与数据源选择器，保存体只带问题清单', async () => {
    form();
    await screen.findByDisplayValue('日报');
    expect(screen.queryByLabelText(/AS-BOT/)).not.toBeInTheDocument();
    expect(screen.queryByText('数据源')).not.toBeInTheDocument();
    // 来源说明必须在（否则用户不知道任务到底按谁的身份跑）
    expect(screen.getByText(/按你的角色与权限执行/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.updateScheduledTask).mock.calls[0];
    expect(payload.workspace_id).toBe(3);
    expect(payload.task_config).toEqual({ questions: [{ title: '数量', question: '统计订单' }] });
    expect(payload.task_config).not.toHaveProperty('as_bot_key');
    expect(payload.task_config).not.toHaveProperty('datasource_id');
    expect(payload).not.toHaveProperty('task_type');
  });

  it('新建任务没有任务类型选择器：工作空间定时任务只有 Agent 分析形态', async () => {
    form(null);   // 新建态
    await screen.findByText(/任务名称/);
    expect(screen.queryByText('任务类型')).not.toBeInTheDocument();
    expect(screen.queryByText('SQL')).not.toBeInTheDocument();
  });

  it('分析问题为空不给保存（不长出每次执行都失败的任务）', async () => {
    form({ ...task, task_config: { questions: [{ title: '空', question: '' }] } });
    await screen.findByDisplayValue('日报');
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    expect(api.updateScheduledTask).not.toHaveBeenCalled();
  });
});
