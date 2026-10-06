import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  listScheduledAsBots: vi.fn(), listNotificationChannels: vi.fn(), listReportTemplates: vi.fn(),
  createScheduledTask: vi.fn(), updateScheduledTask: vi.fn(), regenerateWebhookToken: vi.fn(),
}));
vi.mock('@/stores/workspaceStore', () => ({ useWorkspaceStore: () => ({ currentWorkspaceId: 99 }) }));
vi.mock('@/components/CronInput', () => ({ default: () => null }));

const option: api.ScheduledAsBotOption = {
  as_bot_key: 'sales', name: '销售分析', available: true, datasource_ids: [8],
  tools: ['get_metrics', 'run_semantic_query'], unavailable_tools: [],
};
const task = {
  id: 12, name: '日报', task_type: 'agent', workspace_id: 3, owner_id: 7,
  task_config: { datasource_id: 8, as_bot_key: 'sales', questions: [{ title: '数量', question: '统计订单' }] },
  cron_expression: '0 9 * * *', trigger_type: 'cron', is_active: true,
  timeout_seconds: 300, max_retries: 0, notify_on_success: true, notify_on_failure: true,
} as api.ScheduledTask;

beforeEach(() => {
  vi.mocked(client.get).mockImplementation(async (url) => {
    if (url === '/workspaces') return { data: [{ id: 3, name: '测试空间', role: 'owner', user_default: true }] };
    if (url === '/datasources/') return { data: [{ id: 8, name: '订单', db_type: 'mysql', database_name: 'test' }] };
    if (url === '/knowledge-bases') return { data: [{ id: 4, name: '业务知识', kb_type: 'qmind', status: 'active', document_count: 0 }] };
    return { data: [] };
  });
  vi.mocked(api.listScheduledAsBots).mockResolvedValue([option]);
  vi.mocked(api.listNotificationChannels).mockResolvedValue([]);
  vi.mocked(api.listReportTemplates).mockResolvedValue([]);
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function form(value = task) {
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

  it('编辑任务按持久化工作空间和创建者加载候选，保存仅含 AS-BOT', async () => {
    form();
    await waitFor(() => expect(api.listScheduledAsBots).toHaveBeenCalledWith(3, 12));
    await screen.findByText(/定时可用工具/);
    expect(screen.queryByText('子 Agent')).not.toBeInTheDocument();
    expect(screen.queryByText('MCP 服务', { exact: true })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.updateScheduledTask).mock.calls[0];
    expect(payload.workspace_id).toBe(3);
    expect(payload.task_config).toMatchObject({ as_bot_key: 'sales', datasource_id: 8 });
    expect(payload.task_config).not.toHaveProperty('mcp_server_id');
    expect(payload.task_config).not.toHaveProperty('agent_name');
  });

  it('旧任务必须明确选择 AS-BOT，不自动使用候选默认项', async () => {
    form({ ...task, requires_as_bot_migration: true, task_config: { ...task.task_config, agent_name: 'old' } });
    await screen.findByText(/需重新配置 AS-BOT/);
    await waitFor(() => expect(api.listScheduledAsBots).toHaveBeenCalled());
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    expect(api.updateScheduledTask).not.toHaveBeenCalled();
    await userEvent.click(screen.getByLabelText('AS-BOT *'));
    await userEvent.click(screen.getByRole('option', { name: '销售分析' }));
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalled());
    expect(vi.mocked(api.updateScheduledTask).mock.calls[0][1].task_config).not.toHaveProperty('agent_name');
  });

  it('候选加载失败可见且不能保存', async () => {
    vi.mocked(api.listScheduledAsBots).mockRejectedValue(new Error('failed'));
    form();
    expect(await screen.findByRole('alert')).toHaveTextContent('加载 AS-BOT 失败');
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    expect(api.updateScheduledTask).not.toHaveBeenCalled();
  });
});
