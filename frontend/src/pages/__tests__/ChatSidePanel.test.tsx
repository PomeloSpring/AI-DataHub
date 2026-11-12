// 右侧栏在 Chat 页的接线契约：默认收起且正文列仍守 75%（右侧那段留白就是它的位置）；
// 展开时正文列放开（否则双重留白），入口按钮在输入卡片底行且能来回切换；
// 偏好落 localStorage，重进页面保持上次状态。
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Chat from '../Chat';
import { useChatStore } from '../../stores/chatStore';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}))
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

const column = () => document.querySelectorAll('[data-chat-column]')[1] as HTMLElement;
const toggleBtn = () => screen.getByRole('button', { name: /会话侧栏/ }) as HTMLButtonElement;

const seed = () => useChatStore.setState({
  currentConvId: 7, loading: false, stopping: false, selectedWorkspaceId: 7,
  messages: [
    { role: 'user', content: '查一下最近 12 个月各应用的独立用户数' },
    { role: 'assistant', content: '结论：见下表', reply: '结论：见下表' },
  ] as any,
});

describe('Chat 右侧栏接线', () => {
  beforeEach(() => { localStorage.removeItem('chat.sidePanel.open'); seed(); });
  afterEach(() => cleanup());

  it('默认收起：不渲染侧栏，正文列仍是 75% 份额', () => {
    render(<MemoryRouter><Chat /></MemoryRouter>);
    expect(screen.queryByLabelText('会话侧栏')).toBeNull();
    expect(column().className).toContain('w-[75%]');
  });

  it('点入口展开、再点收起，展开时正文列放开为全宽', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>);
    await userEvent.click(toggleBtn());
    await waitFor(() => expect(screen.getByLabelText('会话侧栏')).toBeInTheDocument());
    expect(column().className).toContain('w-full');
    expect(localStorage.getItem('chat.sidePanel.open')).toBe('1');

    await userEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
    await waitFor(() => expect(screen.queryByLabelText('会话侧栏')).toBeNull());
    expect(localStorage.getItem('chat.sidePanel.open')).toBe('0');
  });

  it('上次展开过就保持展开（偏好跨会话生效）', async () => {
    localStorage.setItem('chat.sidePanel.open', '1');
    render(<MemoryRouter><Chat /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText('会话侧栏')).toBeInTheDocument());
    // 入口按钮本身不拿“会话未落库”当禁用理由（收起入口任何时候都可用）
    expect(screen.getByRole('button', { name: '收起会话侧栏' })).toBeEnabled();
  });

  // 历史缺陷：正文列宽用 sidePanelOpen 判，而侧栏只在有消息时才渲染 —— 新会话空态点一下
  // 文件夹，侧栏没出现、欢迎页与输入卡片却被“让位给侧栏”的全宽规则拉满屏。
  it('新会话空态：展开偏好为真也不放开正文列，入口禁用并说明原因', () => {
    localStorage.setItem('chat.sidePanel.open', '1');
    useChatStore.setState({ messages: [] as any });
    render(<MemoryRouter><Chat /></MemoryRouter>);
    const col = document.querySelector('[data-chat-column]') as HTMLElement;
    expect(col.className).toContain('w-[75%]');
    expect(screen.queryByLabelText('会话侧栏')).toBeNull();
    const btn = screen.getByRole('button', { name: '展开会话侧栏' });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute('title')).toContain('发送消息后');
  });
});
