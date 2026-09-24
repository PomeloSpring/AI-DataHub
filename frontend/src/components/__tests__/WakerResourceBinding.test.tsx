import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  listScheduledWakers: vi.fn(), listNotificationChannels: vi.fn(), listReportTemplates: vi.fn(),
  createScheduledTask: vi.fn(), updateScheduledTask: vi.fn(), regenerateWebhookToken: vi.fn(),
}));
vi.mock('@/stores/workspaceStore', () => ({ useWorkspaceStore: () => ({ currentWorkspaceId: 99 }) }));
vi.mock('@/components/MenuEditorTab', () => ({ default: () => null }));
vi.mock('@/components/CronInput', () => ({ default: () => null }));

const option: api.ScheduledWakerOption = {
  waker_key: 'sales', name: '销售分析', available: true, datasource_ids: [8],
  tools: ['get_metrics', 'run_semantic_query'], unavailable_tools: [],
};
const task = {
  id: 12, name: '日报', task_type: 'agent', workspace_id: 3, owner_id: 7,
  task_config: { datasource_id: 8, waker_key: 'sales', questions: [{ title: '数量', question: '统计订单' }] },
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
  vi.mocked(api.listScheduledWakers).mockResolvedValue([option]);
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

describe('Waker 唯一资源绑定入口', () => {
  it('工作空间仅保留用户、角色、数据源、Waker、菜单标签', async () => {
    render(<WorkspaceManager />);
    await screen.findByText('测试空间');
    await userEvent.click(screen.getByRole('button', { name: '管理' }));
    const dialog = screen.getByRole('dialog');
    const tabs = within(dialog).getAllByRole('tab');
    expect(tabs.map(t => t.textContent)).toEqual(['用户 (0)', '角色', '数据源 (0)', 'Waker', '菜单管理']);
    expect(vi.mocked(client.get).mock.calls.some(([url]) => /knowledge-bases|mcp-servers/.test(String(url)))).toBe(false);
  });

  it('知识库目录不再展示或修改工作空间关联', async () => {
    render(<KnowledgeBase />);
    await screen.findByText('业务知识');
    expect(screen.queryByRole('columnheader', { name: '关联工作空间' })).not.toBeInTheDocument();
    expect(screen.queryByTitle('关联工作空间')).not.toBeInTheDocument();
    expect(screen.getByText(/使用范围统一在 Waker/)).toBeInTheDocument();
  });

  it('编辑任务按持久化工作空间和创建者加载候选，保存仅含 Waker', async () => {
    form();
    await waitFor(() => expect(api.listScheduledWakers).toHaveBeenCalledWith(3, 12));
    await screen.findByText(/定时可用工具/);
    expect(screen.queryByText('子 Agent')).not.toBeInTheDocument();
    expect(screen.queryByText('MCP 服务', { exact: true })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.updateScheduledTask).mock.calls[0];
    expect(payload.workspace_id).toBe(3);
    expect(payload.task_config).toMatchObject({ waker_key: 'sales', datasource_id: 8 });
    expect(payload.task_config).not.toHaveProperty('mcp_server_id');
    expect(payload.task_config).not.toHaveProperty('agent_name');
  });

  it('旧任务必须明确选择 Waker，不自动使用候选默认项', async () => {
    form({ ...task, requires_waker_migration: true, task_config: { ...task.task_config, agent_name: 'old' } });
    await screen.findByText(/需重新配置 Waker/);
    await waitFor(() => expect(api.listScheduledWakers).toHaveBeenCalled());
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    expect(api.updateScheduledTask).not.toHaveBeenCalled();
    await userEvent.click(screen.getByLabelText('Waker *'));
    await userEvent.click(screen.getByRole('option', { name: '销售分析' }));
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalled());
    expect(vi.mocked(api.updateScheduledTask).mock.calls[0][1].task_config).not.toHaveProperty('agent_name');
  });

  it('候选加载失败可见且不能保存', async () => {
    vi.mocked(api.listScheduledWakers).mockRejectedValue(new Error('failed'));
    form();
    expect(await screen.findByRole('alert')).toHaveTextContent('加载 Waker 失败');
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    expect(api.updateScheduledTask).not.toHaveBeenCalled();
  });
});
