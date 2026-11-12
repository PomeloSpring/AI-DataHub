// 聊天框「@」引用的页面级接线：什么时候弹面板、选中后拿到什么、发送时带上什么。
//
// 锁四件事（都是组件单测覆盖不到的接线）：
// * 输入 `@月` → 防抖后按当前会话向服务端要候选（检索口径只在服务端做一份）；
// * 选中 → 输入框里的 `@月` 片段被摘掉，文件变成 chip（引用凭据挂在 chip 上，正文不留文件名）；
// * 发送 → sendMessage 收到 cloudFiles，请求体里是 [{source, ref}]（不是文件名、不是附件 id 手填）；
// * Esc → 面板收起且摘掉片段（残留 `@` 会在下一个字符把面板顶回来，表现为"Esc 没反应"）。
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Chat from '../Chat';
import { useChatStore } from '../../stores/chatStore';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));

const MENTIONS = {
  assets: [
    { source: 'asset', ref: 'a1', filename: '月度营收报告.xlsx', name: '月度营收报告', size: 20480, origin_workspace_name: '全国数据' },
  ],
  session_files: [
    { source: 'session', ref: 'uploads/销售明细.csv', filename: '销售明细.csv', category: 'table', size: 1024 },
  ],
};

const seen: string[] = [];
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn(async (url: string, cfg?: any) => {
      const q = String(cfg?.params?.q ?? '');
      seen.push(url.includes('/chat/mention-files') ? `${url}?q=${q}` : url);
      if (url.includes('/chat/mention-files')) {
        // 服务端按文件名 LIKE 过滤：这里同样按 q 过滤，才能验出"没有匹配"那一面
        const hit = (arr: any[]) => arr.filter(i => `${i.filename}${i.name || ''}`.toLowerCase().includes(q.toLowerCase()));
        return { data: { assets: hit(MENTIONS.assets), session_files: hit(MENTIONS.session_files) } };
      }
      return { data: { skills: [], tasks: [], commands: [], modes: [] } };
    }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

// 本用例只关心输入区，桩掉重型渲染依赖（three.js/mermaid/图表）保持轻量
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }));
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }));
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }));
vi.mock('../../components/ChartPicker', () => ({ default: () => null }));
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }));

const inputEl = () => screen.getByPlaceholderText('输入你的数据查询问题…') as HTMLInputElement;
const sendMessageSpy = vi.fn();

describe('输入框「@」文件引用', () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  beforeEach(() => {
    localStorage.clear();
    seen.length = 0;
    sendMessageSpy.mockClear();
    useChatStore.setState({
      messages: [], currentConvId: 5, loading: false,
      selectedWorkspaceId: 301, sendMessage: sendMessageSpy,
    } as any);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('输入 @ 后按当前会话拉候选，面板列出归档资产与本会话文件', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>);
    fireEvent.change(inputEl(), { target: { value: '@' } });
    expect(seen.some(u => u.includes('/chat/mention-files'))).toBe(false);   // 防抖：立刻不发请求

    await waitFor(() => expect(screen.getByText('月度营收报告')).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /归档资产/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /本会话文件/ })).toBeInTheDocument();
    expect(seen.some(u => u.includes('/chat/mention-files'))).toBe(true);
  });

  it('选中即摘掉 @片段并挂成 chip，再按 Enter 把引用凭据交给发送', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>);
    fireEvent.change(inputEl(), { target: { value: '帮我看看@月' } });
    await waitFor(() => expect(screen.getByText('月度营收报告')).toBeInTheDocument());

    fireEvent.keyDown(inputEl(), { key: 'Enter' });
    await waitFor(() => expect(inputEl().value).toBe('帮我看看'));
    // 引用凭据在 chip 上（含来源标注），正文里不再留下会被模型当成问句一部分的文件名
    expect(screen.getByText('月度营收报告')).toBeInTheDocument();
    expect(screen.getByTitle('月度营收报告.xlsx')).toBeInTheDocument();

    fireEvent.keyDown(inputEl(), { key: 'Enter' });
    expect(sendMessageSpy).toHaveBeenCalledTimes(1);
    const [question, , , opts] = sendMessageSpy.mock.calls[0];
    expect(question).toBe('帮我看看');
    expect(opts).toEqual({ cloudFiles: [expect.objectContaining({ source: 'asset', ref: 'a1' })] });
  });

  it('只有 @ 引用、没写问题时也给一句可读的提问，而不是发空串', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>);
    fireEvent.change(inputEl(), { target: { value: '@' } });
    await waitFor(() => expect(screen.getByText('月度营收报告')).toBeInTheDocument());
    fireEvent.keyDown(inputEl(), { key: 'Enter' });      // 选中
    fireEvent.keyDown(inputEl(), { key: 'Enter' });      // 发送

    const [question] = sendMessageSpy.mock.calls[0];
    expect(question).toContain('请结合我引用的文件继续分析');
    expect(question).toContain('月度营收报告.xlsx');
  });

  it('Esc 收起面板并摘掉正在输入的 @ 片段（残留会在下个字符把面板顶回来）', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>);
    fireEvent.change(inputEl(), { target: { value: '帮我看看@月' } });
    await waitFor(() => expect(screen.getByText('月度营收报告')).toBeInTheDocument());

    fireEvent.keyDown(inputEl(), { key: 'Escape' });
    expect(screen.queryByText('月度营收报告')).not.toBeInTheDocument();
    expect(inputEl().value).toBe('帮我看看');
  });

  it('候选里没有匹配项时 Enter 先收面板，不拦发送（正文写邮箱不该被当成引用）', async () => {
    render(<MemoryRouter><Chat /></MemoryRouter>);
    fireEvent.change(inputEl(), { target: { value: '联系 a@zzz' } });
    await waitFor(() => expect(seen.some(u => u.includes('q=zzz'))).toBe(true));
    // 过滤后没有候选 → 面板落到"没有匹配"态并给出归档引导
    await waitFor(() => expect(screen.getByText(/没有文件名含「zzz」的可引用文件/)).toBeInTheDocument());

    fireEvent.keyDown(inputEl(), { key: 'Enter' });
    expect(sendMessageSpy).not.toHaveBeenCalled();       // 第一次 Enter 只收面板
    expect(screen.queryByText(/没有文件名含/)).not.toBeInTheDocument();
  });
});
