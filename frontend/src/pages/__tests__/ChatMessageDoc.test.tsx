// Chat 页接线契约（参照千问的复制 / 转文档编辑）：
// 操作行挂在助手回答与用户气泡上；转文档编辑要落库并打开编辑面板，
// 会话未落库时必须报错而不是造一个存不下来的文档。
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Chat from '../Chat';
import { useChatStore } from '../../stores/chatStore';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
import { toast } from 'sonner';

const clientPut = vi.fn().mockResolvedValue({ data: {} });
// 草稿接口回初稿，报告中心（归档）回 report_id —— 两段式走两个不同端点
const postPayload = (url: unknown) => (String(url || '').includes('report-draft')
  ? { title: '本轮分析', markdown: '## 两种模式\n\n正文', created_at: '2026-11-06 15:00:00',
      updated_at: '2026-11-06 15:00:00', revisions: 0 }
  : { report_id: 9, title: '本轮分析', archived_at: '2026-11-06 15:02:00' });
const clientPost = vi.fn((...args: any[]) => Promise.resolve({ data: postPayload(args[0]) }));
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: (...args: any[]) => clientPost(...args),
    put: (...args: any[]) => clientPut(...args),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}))

// 文档链路不经过图表/3D/文件面板，桩掉重型依赖保持用例轻量（MarkdownWithCharts 保留真渲染）
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

const toastTexts = (mock: any): string[] =>
  ((mock?.mock?.calls ?? []) as unknown[][]).map(c => String(c[0]));

const writeText = vi.fn().mockResolvedValue(undefined);

const ANSWER = '## 两种模式\n\n1. **快速模式**：响应快\n2. *思考研究模式*：更严谨';

const assistantMsg = (over: Record<string, any> = {}) => ({
  role: 'assistant', content: ANSWER, reply: ANSWER, question: '两种模式有什么区别',
  intent: 'chat', message_uuid: 'm-1', ...over,
});

const seed = (over: Record<string, any> = {}) => {
  useChatStore.setState({
    conversations: [{ id: 1, title: '会话一', datasource_id: 0, workspace_id: 7, created_at: '', updated_at: '' }],
    conversationError: null,
    currentConvId: 1,
    selectedWorkspaceId: 7,
    messages: [
      { role: 'user', content: '两种模式有什么区别' },
      assistantMsg(over) as any,
    ],
    loading: false,
  });
};

/** 助手回答那一行的复制按钮（用户气泡也有同名按钮，助手在最后一条） */
const copyBtn = () => {
  const all = screen.getAllByRole('button', { name: '复制回答文本' }) as HTMLButtonElement[];
  return all[all.length - 1];
};
const moreBtn = () => screen.getByRole('button', { name: '更多操作' }) as HTMLButtonElement;

describe('Chat 消息操作行与转文档编辑', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 右侧栏展开偏好是 localStorage 级的会话外状态：不重置的话，
    // 上一用例“生成草稿自动展开侧栏”会让本用例从一开始就多一份侧栏清单（同名文本双命中）
    localStorage.setItem('chat.sidePanel.open', '0');
    clientPut.mockResolvedValue({ data: {} });
    clientPost.mockImplementation((...args: any[]) => Promise.resolve({ data: postPayload(args[0]) }));
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    seed();
  });

  const renderChat = () => render(<MemoryRouter><Chat /></MemoryRouter>);

  it('助手回答给复制入口：写入的是纯文本而不是 markdown 源码', async () => {
    renderChat();
    await userEvent.click(copyBtn());
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('两种模式\n\n1. 快速模式：响应快\n2. 思考研究模式：更严谨'));
    expect(toastTexts(toast.success)).toContain('已复制');
  });

  it('用户提问也有复制入口（但不给转文档）', async () => {
    renderChat();
    const userCopy = screen.getAllByRole('button', { name: '复制回答文本' });
    expect(userCopy.length).toBeGreaterThanOrEqual(2);
    // 转文档入口只在助手回答那一行：整页只有一个「更多操作」
    expect(screen.getAllByRole('button', { name: '更多操作' })).toHaveLength(1);
  });

  it('转文档编辑：落库 + 气泡内出现文档卡 + 打开编辑面板', async () => {
    renderChat();
    await userEvent.click(moreBtn());
    await userEvent.click(await screen.findByRole('menuitem', { name: '转文档编辑' }));

    await waitFor(() => expect(screen.getByText('已为你把消息转换成文档，可直接编辑或下载。')).toBeInTheDocument());
    expect(screen.getByText('无标题')).toBeInTheDocument();

    // 会话记录里已带上文档（随消息 JSON 落库），正文由回答 markdown 初稿化
    await waitFor(() => {
      const doc = useChatStore.getState().messages[1].doc;
      expect(doc?.html).toContain('两种模式');
    });
    expect(clientPut).toHaveBeenCalled();
    expect(screen.getByLabelText('文档正文')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载' })).toBeInTheDocument();
  });

  it('结果轮的赞踩与复制同一行；存为报告草稿收进更多菜单（两段式）', async () => {
    seed({
      intent: 'query', sql: 'SELECT 1', reportable: true,
      result: { columns: ['a'], rows: [{ a: 1 }], row_count: 1 },
    });
    renderChat();
    // 旧版“结果准确?”文字标签与靠右的 thumbs 已收拢：不再分两端、也不在卡脚另开一行
    expect(screen.queryByText('结果准确?')).not.toBeInTheDocument();
    const thumbs = screen.getByRole('button', { name: '结果准确？满意' });
    const row = thumbs.parentElement as HTMLElement;
    expect(row.contains(copyBtn())).toBe(true);

    await userEvent.click(moreBtn());
    await userEvent.click(await screen.findByRole('menuitem', { name: '存为报告草稿' }));
    // 第一段：只生成会话内草稿（打草稿接口 + 随会话落库），**不碰报告中心**
    await waitFor(() => expect(clientPost)
      .toHaveBeenCalledWith('/chat/conversations/1/report-draft', expect.objectContaining({ message_index: 1 })));
    expect(clientPost).not.toHaveBeenCalledWith('/reports', expect.anything());
    await waitFor(() => expect(
      (useChatStore.getState().messages[1] as any).reportDraft?.markdown).toContain('两种模式'));

    // 第二段：在草稿面板点「归档」才进报告中心（正文仍由服务端从会话读）
    await userEvent.click(await screen.findByRole('button', { name: '归档进报告中心' }));
    await waitFor(() => expect(clientPost)
      .toHaveBeenCalledWith('/reports', expect.objectContaining({ source_type: 'conversation' })));
    await waitFor(() => expect(
      (useChatStore.getState().messages[1] as any).reportDraft?.archived?.report_id).toBe(9));
  });

  it('已有文档时点入口是继续编辑，不再新建一份', async () => {
    seed({
      doc: {
        title: '已有文档', html: '<p>已有正文</p>', created_at: '2026-11-06T10:51:00',
        updated_at: '2026-11-06T10:51:00', revisions: 2,
      },
    });
    renderChat();
    expect(screen.getByText('已有文档')).toBeInTheDocument();
    await userEvent.click(moreBtn());
    await userEvent.click(await screen.findByRole('menuitem', { name: '继续编辑文档' }));
    expect((screen.getByLabelText('文档正文') as HTMLElement).innerHTML).toBe('<p>已有正文</p>');
    expect(clientPut).not.toHaveBeenCalled();
  });

  it('会话还没落库时显式报错，不造一个存不下来的文档', async () => {
    useChatStore.setState({ currentConvId: null });
    renderChat();
    await userEvent.click(moreBtn());
    await userEvent.click(await screen.findByRole('menuitem', { name: '转文档编辑' }));
    await waitFor(() => expect(toastTexts(toast.error))
      .toContain('会话尚未落库，无法转文档编辑；请等本轮回答完成后再试'));
    expect(clientPut).not.toHaveBeenCalled();
    expect(screen.queryByText('已为你把消息转换成文档，可直接编辑或下载。')).not.toBeInTheDocument();
  });

  it('流式进行中的回答不给复制（内容还没定稿）', () => {
    useChatStore.setState({
      messages: [{ role: 'user', content: 'q' }, assistantMsg() as any],
      loading: true,
    });
    renderChat();
    expect(copyBtn().disabled).toBe(true);
    expect(moreBtn().disabled).toBe(true);
  });
});
