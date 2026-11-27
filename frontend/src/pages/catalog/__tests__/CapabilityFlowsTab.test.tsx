/**
 * 能力流程页签（P3-5）契约：
 * 1. 仅 hasPerm('capability:run') 可见；无权限显示缺失权限码与原因（不渲染点了必 403 的按钮）；
 * 2. 列表服务端真分页：翻页带 page 请求，total 来自响应；
 * 3. 人工入口：waiting_human 显示「复核推进」+ 人工待完成提示；终态不给取消按钮；
 * 4. 失败原因显式展示；不裸显内部自增 id（run_id 仅作 React key）。
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CapabilityFlowsTab from '../CapabilityFlowsTab';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const RUNS = [
  {
    run_id: 1, scenario: 'ops_content_publish', scenario_title: '内容草稿到发布',
    status: 'waiting_human', current_step: 3, initiated_by_name: 'alice',
    error_code: '', error_message: '', created_at: '2026-11-27 10:00:00', finished_at: '',
    steps: [
      { step_no: 1, action: 'content_draft.create', label: '创建内容草稿', channel: 'ai',
        status: 'succeeded', attempts: 1, error_message: '', finished_at: '' },
      { step_no: 2, action: 'content_review.submit', label: '提交内容审核', channel: 'ai',
        status: 'succeeded', attempts: 1, error_message: '', finished_at: '' },
      { step_no: 3, action: 'content_review.publish', label: '发布内容', channel: 'human',
        status: 'waiting_human', attempts: 0, error_message: '', finished_at: '' },
    ],
    events: [{ step_no: 0, event: 'created', detail: 'scenario=ops_content_publish', at: '10:00:00' }],
  },
  {
    run_id: 2, scenario: 'ops_content_publish', scenario_title: '内容草稿到发布（旧运行）',
    status: 'failed', current_step: 1, initiated_by_name: 'bob',
    error_code: 'PERMISSION_DENIED', error_message: '角色未授予「article:manage」权限',
    created_at: '2026-11-27 09:00:00', finished_at: '2026-11-27 09:00:01',
    steps: [], events: [],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  usePermissionStore.setState({ perms: ['capability:run'], allowedMenus: null, unrestricted: false, loaded: true });
  vi.mocked(client.get).mockImplementation(async (url: string, cfg?: any) => {
    if (url === '/capability-flows/runs') {
      return { data: { items: RUNS, total: 50, page: cfg?.params?.page || 1,
        page_size: cfg?.params?.page_size || 20 } } as any;
    }
    return { data: {} } as any;
  });
  vi.mocked(client.post).mockResolvedValue({ data: { status: 'succeeded' } } as any);
});
afterEach(() => cleanup());

const renderTab = () => render(<MemoryRouter><CapabilityFlowsTab /></MemoryRouter>);

describe('能力流程页签（P3-5）', () => {
  it('无 capability:run：只显示缺失权限码提示，不渲染列表与操作', async () => {
    usePermissionStore.setState({ perms: ['evolution:read'], allowedMenus: null, unrestricted: false, loaded: true });
    renderTab();
    expect(await screen.findByText(/缺少 capability:run 权限/)).toBeInTheDocument();
    expect(screen.queryByText('内容草稿到发布')).toBeNull();
  });

  it('列表服务端分页：翻页带 page 请求，total 来自响应', async () => {
    renderTab();
    await screen.findByText('内容草稿到发布');
    expect(screen.getByText(/共 50 条/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => {
      const pages = vi.mocked(client.get).mock.calls
        .filter(([u]) => u === '/capability-flows/runs')
        .map(([, cfg]) => (cfg as any)?.params?.page);
      expect(pages).toContain(2);
    });
  });

  it('人工入口：waiting_human 显示复核推进与人工待完成提示；终态不给取消', async () => {
    renderTab();
    const row = (await screen.findByText('内容草稿到发布')).closest('div.border') as HTMLElement;
    expect(within(row).getByText('复核推进')).toBeInTheDocument();
    expect(within(row).getByText(/人工步骤待完成/)).toBeInTheDocument();
    // 终态（failed）行没有「取消运行」
    const failed = screen.getByText(/角色未授予「article:manage」权限/).closest('div.border') as HTMLElement;
    expect(within(failed).queryByText('取消运行')).toBeNull();
    // 失败原因显式展示
    expect(within(failed).getByText(/角色未授予「article:manage」权限/)).toBeInTheDocument();
    // 不裸显内部自增 id
    expect(screen.queryByText(/^1$/)).toBeNull();
    expect(screen.queryByText(/^2$/)).toBeNull();
  });

  it('复核推进走 resume 端点并刷新', async () => {
    renderTab();
    const row = (await screen.findByText('内容草稿到发布')).closest('div.border') as HTMLElement;
    await userEvent.click(within(row).getByText('复核推进'));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/capability-flows/runs/1/resume'));
    await waitFor(() => expect(vi.mocked(client.get).mock.calls
      .filter(([u]) => u === '/capability-flows/runs').length).toBeGreaterThan(1));
  });
});
