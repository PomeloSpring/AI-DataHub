import { beforeEach, describe, expect, it, vi } from 'vitest';
import client from '@/api/client';
import { useAsBotStore } from '../asBotStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

/**
 * AS-BOT 面板的「后台静默任务」视图态回归。
 *
 * 背景：『生成本体模型』改为服务端后台执行后，面板只是观察者——关面板不影响执行，
 * 但状态刷新失败必须可见（不得让用户以为还在跑），且轮询不得打断用户自己的回答流。
 */

const task = (over: any = {}) => ({
  conversation_id: 777, workspace_id: 5, datasource_id: 5, datasource_name: 'prd-sls-cn',
  status: 'running', progress: [], model_id: null, object_count: 0, warnings: [], error: '', ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  useAsBotStore.setState({
    backgroundTasks: [], tasksError: '', conversationId: null, messages: [], loading: false,
  });
});

describe('refreshBackgroundTasks', () => {
  it('成功时填充任务清单并清掉旧的错误提示', async () => {
    useAsBotStore.setState({ tasksError: '上一次刷新失败' });
    vi.mocked(client.get).mockResolvedValue({ data: { items: [task()] } } as any);
    await useAsBotStore.getState().refreshBackgroundTasks();
    const st = useAsBotStore.getState();
    expect(st.backgroundTasks.map((t) => t.conversation_id)).toEqual([777]);
    expect(st.tasksError).toBe('');
    // 只拉运行中的任务（面板横幅不堆历史）
    expect(vi.mocked(client.get)).toHaveBeenCalledWith('/catalog/ontology/generate/tasks',
      { params: { active_only: true } });
  });

  it('失败时把原因留在 tasksError（面板可见），不把失败伪装成“没有任务”', async () => {
    useAsBotStore.setState({ backgroundTasks: [task()] });
    vi.mocked(client.get).mockRejectedValue({ response: { data: { detail: '服务不可用' } } } as any);
    await useAsBotStore.getState().refreshBackgroundTasks();
    const st = useAsBotStore.getState();
    expect(st.tasksError).toContain('服务不可用');
    expect(st.backgroundTasks.length).toBe(1);   // 保留上次成功视图
  });
});

describe('refreshConversation', () => {
  it('用户自己的回答正在流式接收时不插手（不得把进行中的转录刷掉）', async () => {
    useAsBotStore.setState({ conversationId: 777, loading: true });
    await useAsBotStore.getState().refreshConversation(777);
    expect(client.get).not.toHaveBeenCalled();
  });

  it('空闲且仍停在同一会话时静默重拉消息', async () => {
    useAsBotStore.setState({ conversationId: 777, loading: false });
    vi.mocked(client.get).mockResolvedValue({ data: { messages: [{ role: 'assistant', content: '进度' }] } } as any);
    await useAsBotStore.getState().refreshConversation(777);
    expect(client.get).toHaveBeenCalledWith('/chat/conversations/777');
    expect(useAsBotStore.getState().messages[0].content).toBe('进度');
  });
});
