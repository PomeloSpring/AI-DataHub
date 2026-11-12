// 首页「问数」带问题进问数页的落地契约：
// 1. 带 send 标记（首页回车/问数按钮）= 用户已经按下发送，落地必须直接执行，不能只预填；
// 2. 执行要等取数上下文定形（工作空间 + 站点/AS-BOT 清单），否则问题发进没解析完的范围；
// 3. 不带 send（阅读页「就本主题提问」的半句）只预填；
// 4. 上下文迟迟不定形时超时兜底：退回预填 + 说出来，不能把问题无限期悬着；
// 5. 回归锁定：登录后 selectedWorkspaceId=0 → 挂载即触发的工作空间同步会 setInput('')，
//    不得把刚带进来的问题抹掉（历史缺陷：首页问数带不进问题）。
import { act, render, screen, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Chat from '../Chat';
import { stashQuestion } from '../home/shared';
import { useChatStore } from '../../stores/chatStore';
import { toast } from '@/lib/toast';

vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}));
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }));
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }));
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }));
vi.mock('../../components/ChartPicker', () => ({ default: () => null }));
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }));

const sendMock = vi.fn().mockResolvedValue(undefined);
/** 清单加载全部换成 no-op：sitesLoaded/asBotsLoaded 由用例自己推进，不被真实请求抢先置位 */
const noopLoaders = {
  loadAuthorizedSites: vi.fn().mockResolvedValue(undefined),
  loadAsBots: vi.fn().mockResolvedValue(undefined),
  loadExecutionLayer: vi.fn().mockResolvedValue(undefined),
  loadConversations: vi.fn().mockResolvedValue(undefined),
  loadDatasources: vi.fn().mockResolvedValue(undefined),
  loadLLMModels: vi.fn().mockResolvedValue(undefined),
  loadSystemConfig: vi.fn().mockResolvedValue(undefined),
  loadWorkspaceConfig: vi.fn().mockResolvedValue(undefined),
};

/** 登录后刚进问数页的样子：URL 带工作空间 301，store 里还是 0（同步 effect 会去切） */
const seedStore = (over: Record<string, unknown> = {}) => useChatStore.setState({
  currentConvId: null, messages: [], conversations: [], loading: false, stopping: false,
  selectedWorkspaceId: 0,
  sitesLoaded: true, asBotsLoaded: true, siteError: '',
  ...noopLoaders,
  ...over,
  sendMessage: sendMock,
} as never);

const renderChat = () => render(
  <MemoryRouter initialEntries={['/ws/301/chat']}>
    <Routes>
      <Route path="/ws/:workspaceId/chat" element={<Chat />} />
    </Routes>
  </MemoryRouter>,
);

const inputBox = () => screen.getByPlaceholderText('输入你的数据查询问题…') as HTMLInputElement;

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  sendMock.mockResolvedValue(undefined);
  seedStore();
});
afterEach(() => { cleanup(); useChatStore.getState().reset(); });

describe('首页问题带进问数页', () => {
  it('带 send 标记：上下文就绪即自动发送，不预填也不等人再按一次', async () => {
    stashQuestion('上个月各站点的活跃装机数同比', { send: true });
    renderChat();
    await waitFor(() => expect(sendMock).toHaveBeenCalledWith('上个月各站点的活跃装机数同比'));
    await waitFor(() => expect(inputBox().value).toBe(''));
    // 一次性消费：两个键都必须清掉，重挂载不能把同一问题再发一遍
    expect(sessionStorage.getItem('home_question')).toBeNull();
    expect(sessionStorage.getItem('home_question_send')).toBeNull();
  });

  it('待发期间就把问题摆进会话区（不停在欢迎页）并说明在准备什么', async () => {
    seedStore({ sitesLoaded: false, asBotsLoaded: false, sendMessage: sendMock });
    stashQuestion('昨天的访问量', { send: true });
    renderChat();
    // 欢迎页不得再出现（否则用户以为没收到）
    expect(screen.queryByText(/你好，我是/)).not.toBeInTheDocument();
    expect(screen.getByText('昨天的访问量')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('正在准备会话上下文');
  });

  it('已发出但消息还没落进 store：不重复发送，store 接管后不留两句同文', async () => {
    stashQuestion('本月装机同比', { send: true });
    renderChat();
    await waitFor(() => expect(sendMock).toHaveBeenCalledTimes(1));
    // 发送中（loading 拉起又落下）不能拿同一句再发一次
    useChatStore.setState({ loading: true } as never);
    useChatStore.setState({ loading: false } as never);
    await new Promise(r => setTimeout(r, 30));
    expect(sendMock).toHaveBeenCalledTimes(1);
    // 本轮进入 store → 待发气泡让位，页面上只剩那一句
    useChatStore.setState({ messages: [{ role: 'user', content: '本月装机同比' } as never] } as never);
    await waitFor(() => expect(screen.getAllByText('本月装机同比')).toHaveLength(1));
  });

  it('登录后工作空间同步（0→301）不得抹掉带进来的问题', async () => {
    stashQuestion('本月装机同比', { send: true });
    renderChat();
    await waitFor(() => expect(useChatStore.getState().selectedWorkspaceId).toBe(301));
    await waitFor(() => expect(sendMock).toHaveBeenCalledWith('本月装机同比'));
  });

  it('站点清单还没拉到就不发（等问题发进已解析的取数范围），就绪后补发', async () => {
    seedStore({ sitesLoaded: false, asBotsLoaded: false });
    stashQuestion('昨天的访问量', { send: true });
    renderChat();
    await new Promise(r => setTimeout(r, 30));
    expect(sendMock).not.toHaveBeenCalled();
    // 清单定形（真实链路由 loadAuthorizedSites/loadAsBots 置位，这里直接推进 store）
    useChatStore.setState({ sitesLoaded: true, asBotsLoaded: true } as never);
    await waitFor(() => expect(sendMock).toHaveBeenCalledWith('昨天的访问量'));
  });

  it('站点清单加载失败：不带着未解析的范围自动发，退回预填并把原因说出来', async () => {
    seedStore({ siteError: '站点清单加载失败' });
    stashQuestion('昨天的访问量', { send: true });
    renderChat();
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('站点清单加载失败')));
    expect(sendMock).not.toHaveBeenCalled();
    expect(inputBox().value).toBe('昨天的访问量');
  });

  it('上下文迟迟不定形：超时退回预填并说出原因，不把问题无限期悬着', () => {
    vi.useFakeTimers();
    try {
      seedStore({ sitesLoaded: false, asBotsLoaded: false });
      stashQuestion('昨天的访问量', { send: true });
      renderChat();
      expect(sendMock).not.toHaveBeenCalled();
      act(() => { vi.advanceTimersByTime(6100); });
      expect(sendMock).not.toHaveBeenCalled();
      expect(inputBox().value).toBe('昨天的访问量');
      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('取数上下文尚未就绪'));
    } finally {
      vi.useRealTimers();
    }
  });

  it('不带 send（阅读页的半句）只预填，且不被工作空间同步抹掉', async () => {
    stashQuestion('关于《扫描精度》：');
    renderChat();
    await waitFor(() => expect(inputBox().value).toBe('关于《扫描精度》：'));
    expect(sendMock).not.toHaveBeenCalled();
  });

  it('没有带问题时不发送也不填东西（普通进入问数页）', async () => {
    renderChat();
    await new Promise(r => setTimeout(r, 30));
    expect(sendMock).not.toHaveBeenCalled();
    expect(inputBox().value).toBe('');
  });
});
