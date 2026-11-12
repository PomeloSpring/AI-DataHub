// 会话记录的两个 destructive 入口（用户 2026-11-06 要求）：
//  1. 删除会话中的某一问（连同它的回答）—— 走确认框，且确认文案必须说清
//     "模型已读过的内容不会回退"，不让人误以为删了就当没发生过；
//  2. 「/ 清空当前会话」放在指令面板里 —— 它是 kind=action 的客户端会话动作：
//     选中弹确认、就地执行，**不挂成本轮指令、不进请求体**（服务端对 action 类
//     上送会 400 拒，那属于契约错配）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() } }))
const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))
vi.mock('../../api/client', () => ({ default: mockClient }))
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

import Chat from '../Chat'
import { useChatStore } from '../../stores/chatStore'
// spy 句柄取自被 mock 的 'sonner'（业务代码走 @/lib/toast 包装器，包装器会调到它；
// 直接 import @/lib/toast 拿到的是真函数，不是 spy）
import { toast } from 'sonner'

const COMMANDS = [
  { key: 'create_scheduled_task', label: '创建定时任务', description: 'd1', kind: 'directive', action: '' },
  { key: 'clear', label: '清空当前会话', description: '擦掉本会话的历史消息与模型上下文（不可撤销）',
    kind: 'action', action: 'clear_conversation' },
];

beforeEach(() => {
  vi.clearAllMocks();
  mockClient.get.mockResolvedValue({ data: {
    skills: [], tasks: [], commands: COMMANDS, modes: [], scenes: [], scenes_hidden: 0,
  } });
  useChatStore.setState({
    messages: [
      { role: 'user', content: '第一问' }, { role: 'assistant', content: '第一答' },
      { role: 'user', content: '第二问' }, { role: 'assistant', content: '第二答' },
    ],
    currentConvId: 7, loading: false, stopping: false, selectedWorkspaceId: 301,
  });
});

afterEach(() => cleanup());

describe('删除某一轮问答', () => {
  it('用户气泡上有删除入口，点击先弹确认（含"模型记忆不会回退"的诚实说明）', async () => {
    const del = vi.fn().mockResolvedValue(true);
    useChatStore.setState({ deleteTurn: del } as any);
    render(<MemoryRouter><Chat /></MemoryRouter>);

    fireEvent.click(screen.getAllByTitle('删除这一问及其回答（不可撤销）')[0]);
    const dialog = await screen.findByText('删除这一轮问答');
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText(/模型在本会话里已经读过的内容不会因此回退/)).toBeInTheDocument();
    expect(del).not.toHaveBeenCalled();          // 确认前不能真的删

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith(0));
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('已删除'), expect.anything());
  });

  it('取消确认不会删除任何东西', async () => {
    const del = vi.fn().mockResolvedValue(true);
    useChatStore.setState({ deleteTurn: del } as any);
    render(<MemoryRouter><Chat /></MemoryRouter>);
    fireEvent.click(screen.getAllByTitle('删除这一问及其回答（不可撤销）')[0]);
    await screen.findByText('删除这一轮问答');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(del).not.toHaveBeenCalled();
  });
});

describe('/ 面板里的「清空当前会话」', () => {
  async function openPanelAndPick() {
    const input = screen.getByPlaceholderText(/输入你的数据查询问题/) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '/清空' } });
    input.selectionStart = 3;
    const option = await screen.findByText('清空当前会话');
    fireEvent.click(option);
  }

  it('选中后弹确认框，确认才清空；且不会被挂成本轮指令', async () => {
    const clearFn = vi.fn().mockResolvedValue(true);
    useChatStore.setState({ clear: clearFn } as any);
    render(<MemoryRouter><Chat /></MemoryRouter>);
    await openPanelAndPick();

    expect(await screen.findByText('清空当前会话', { selector: '.text-lg' })).toBeInTheDocument();
    expect(screen.getByText(/不会新开一个会话窗口/)).toBeInTheDocument();
    expect(clearFn).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认清空' }));
    await waitFor(() => expect(clearFn).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('已清空当前会话'), expect.anything());
  });

  it('清空动作不进请求体：面板里选中不会把 slash_command 设为 clear', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>);
    await openPanelAndPick();
    // 弹了确认框就说明走的是客户端动作分支；此时输入框不该留下任何"已选指令"chip
    await screen.findByRole('button', { name: '确认清空' });
    expect(screen.queryByText('清空当前会话', { selector: '[class*="rounded-full"]' })).toBeNull();
    expect(useChatStore.getState().messages).toHaveLength(4);   // 没确认 = 会话没被动过
  });
});
