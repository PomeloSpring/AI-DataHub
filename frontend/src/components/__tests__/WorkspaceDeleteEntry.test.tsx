import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '../ui/dropdown-menu';
import { WorkspaceMenuItem } from '../WorkspaceLayout';
import { WorkspaceDeleteDialog } from '../WorkspaceDeleteDialog';
import client from '@/api/client';
import type { Workspace } from '@/stores/workspaceStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const personal = {
  id: 301, name: 'admin的工作站', description: '个人工作站(随用户自动创建)',
  icon: '🏠', color: '#1890ff', is_default: true, user_default: true, role: 'owner',
} as Workspace;
const custom = {
  id: 302, name: 'test', description: '', icon: '📊', color: '#1890ff',
  is_default: false, user_default: false, role: 'owner',
} as Workspace;

const impact = {
  workspace_id: 302, workspace_name: 'test', is_default: 0, deletable: true,
  disk_usage_bytes: 10 * 1024 * 1024, conversations: 3, dashboards: 1,
  scheduled_tasks: 0, dag_workflows: 0, saved_queries: 2,
};

function renderItem(ws: Workspace, onSwitch = vi.fn(), onAskDelete = vi.fn()) {
  render(
    <DropdownMenu open onOpenChange={() => {}}>
      <DropdownMenuTrigger asChild><button>切换</button></DropdownMenuTrigger>
      <DropdownMenuContent forceMount>
        <WorkspaceMenuItem
          ws={ws} isCurrent={ws.id === 302}
          onSwitch={onSwitch} onAskDelete={onAskDelete}
        />
      </DropdownMenuContent>
    </DropdownMenu>,
  );
  return { onSwitch, onAskDelete };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockResolvedValue({ data: impact } as any);
  vi.mocked(client.delete).mockResolvedValue({ data: { success: true } } as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('工作空间下拉项的删除入口', () => {
  it('默认工作站不渲染删除按钮，只标注默认', () => {
    renderItem(personal);
    expect(screen.queryByRole('button', { name: /删除工作空间/ })).not.toBeInTheDocument();
    expect(screen.getByText('默认')).toBeInTheDocument();
    expect(screen.getByText('admin的工作站')).toBeInTheDocument();
  });

  it('自建空间渲染删除按钮，点击只打开确认框、不切空间', () => {
    const { onSwitch, onAskDelete } = renderItem(custom);
    const btn = screen.getByRole('button', { name: '删除工作空间 test' });
    fireEvent.click(btn);
    expect(onAskDelete).toHaveBeenCalledTimes(1);
    expect(onSwitch).not.toHaveBeenCalled();
  });
});

describe('工作空间删除确认框', () => {
  function renderDialog(onDeleted = vi.fn()) {
    render(
      <WorkspaceDeleteDialog
        workspace={{ id: 302, name: 'test' }} open
        onOpenChange={() => {}} onDeleted={onDeleted}
      />,
    );
    return onDeleted;
  }

  it('打开时拉取并展示影响清单（显示 name 而非裸 id）', async () => {
    renderDialog();
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/workspaces/302/delete-impact'));
    expect(screen.getByText(/将永久删除「test」/)).toBeInTheDocument();
    expect(screen.getByText('会话')).toBeInTheDocument();
    expect(screen.getByText('3 项')).toBeInTheDocument();
    expect(screen.getByText('保存查询/数据集')).toBeInTheDocument();
    // 数量为 0 的类别不渲染
    expect(screen.queryByText('定时任务')).not.toBeInTheDocument();
  });

  it('未勾选确认前删除按钮不可用，勾选后删除并回调', async () => {
    const onDeleted = renderDialog();
    const confirm = screen.getByRole('button', { name: /永久删除/ });
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByLabelText('确认永久删除工作空间'));
    await waitFor(() => expect(screen.getByRole('button', { name: /永久删除/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /永久删除/ }));
    await waitFor(() => expect(client.delete).toHaveBeenCalledWith('/workspaces/302'));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(302));
  });

  it('后端拒绝（如仍有会话在执行）时提示原因，不静默成功', async () => {
    vi.mocked(client.delete).mockRejectedValue({ response: { data: { detail: '该工作空间仍有 1 个会话在执行或正在停止，请先停止后再删除' } } });
    renderDialog();
    fireEvent.click(screen.getByLabelText('确认永久删除工作空间'));
    await waitFor(() => expect(screen.getByRole('button', { name: /永久删除/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /永久删除/ }));
    await waitFor(() => expect(client.delete).toHaveBeenCalledWith('/workspaces/302'));
    const { toast } = await import('sonner');
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      '该工作空间仍有 1 个会话在执行或正在停止，请先停止后再删除',
    ));
  });
});
