import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import OntologyModeling from '../OntologyModeling';
import client from '@/api/client';
import { useAsBotStore } from '@/stores/asBotStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/api/metrics', () => ({
  metricsApi: { assetPool: vi.fn(async () => ({ data: { counts: { total: 0 } } })) },
}));

/**
 * 「生成本体模型」入口回归（用户拍板：后台静默执行 + AS-BOT 侧边栏呈现 + 数据源不是筛选器）。
 *
 * 锁住三条行为：
 * 1. 选数据源只决定“给哪个源生成”，模型列表不得随之过滤（旧实现会把源本体藏起来）；
 * 2. 点生成 = 派发后台任务（POST 带 kb_ids）并直接打开 AS-BOT 侧边会话，页面不再持有流；
 * 3. 参考知识库从清单选择（显示 name），不是手输 id。
 */

const MODEL = {
  id: 901, name: '联耀业务本体', datasource_id: 0, kind: 'business', status: 'active',
  object_count: 1, created_by: 'admin', created_at: '2026-10-16T00:00:00Z',
  updated_at: '2026-10-16T00:00:00Z',
  json_content: JSON.stringify({ objects: [{ key: 'case_file', display_name: '案例', primary_table: 't_case' }] }),
};

beforeEach(() => {
  vi.clearAllMocks();
  useAsBotStore.setState({ panelOpen: false, backgroundTasks: [], tasksError: '', conversationId: null, messages: [] });
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/datasources') return { data: [{ id: 5, name: 'prd-sls-cn', db_type: 'sls' }] } as any;
    if (url === '/knowledge-bases') {
      return { data: [{ id: 11, name: '业务知识库', status: 'active' }, { id: 12, name: '已停用库', status: 'inactive' }] } as any;
    }
    if (url === '/catalog/ontology/models') return { data: { items: [MODEL] } } as any;
    if (url.startsWith('/catalog/ontology/models/')) return { data: MODEL } as any;
    if (url === '/catalog/ontology/generate/tasks') return { data: { items: [] } } as any;
    if (url === '/chat/conversations') return { data: [] } as any;
    return { data: [] } as any;
  });
  vi.mocked(client.post).mockResolvedValue({
    data: {
      conversation_id: 777, workspace_id: 5, datasource_id: 5, datasource_name: 'prd-sls-cn',
      status: 'running', progress: [], model_id: null, object_count: 0, warnings: [], error: '',
    },
  } as any);
});

afterEach(() => cleanup());

const listCalls = () => vi.mocked(client.get).mock.calls
  .filter(([url]) => url === '/catalog/ontology/models');

/** 页面顶栏有「建模待办」跳转入口（useNavigate），必须包在 Router 上下文里 */
const renderPage = () => render(
  <MemoryRouter initialEntries={['/data/ontology']}>
    <OntologyModeling />
  </MemoryRouter>,
);

describe('本体建模 · 生成入口', () => {
  it('选择数据源不过滤模型列表（数据源不是筛选器）', async () => {
    renderPage();
    await screen.findByText('联耀业务本体');
    const before = listCalls().length;

    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.click(await screen.findByText('prd-sls-cn（sls）'));

    // 选完数据源后不得“补一发带过滤的列表请求”
    await new Promise((r) => setTimeout(r, 50));
    expect(listCalls().length).toBe(before);
    // 列表请求不得带 datasource_id 过滤参数（数据源只是生成目标）
    for (const [, config] of listCalls()) {
      expect((config as any)?.params?.datasource_id).toBeUndefined();
    }
    // 模型仍在（源本体也照常列出，不随选择消失）
    expect(screen.getByText('联耀业务本体')).toBeTruthy();
  });

  it('生成 = 派发后台任务（带 kb_ids）并打开 AS-BOT 会话，不在页面内持有流', async () => {
    renderPage();
    await screen.findByText('联耀业务本体');

    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.click(await screen.findByText('prd-sls-cn（sls）'));

    // 参考知识库：选择框呈现名称，停用库不进候选
    await userEvent.click(screen.getByRole('button', { name: /参考知识库/ }));
    expect(await screen.findByText('业务知识库')).toBeTruthy();
    expect(screen.queryByText('已停用库')).toBeNull();
    await userEvent.click(screen.getByText('业务知识库'));

    await userEvent.click(screen.getByRole('button', { name: /生成本体模型/ }));

    await waitFor(() => expect(vi.mocked(client.post)).toHaveBeenCalledWith(
      '/catalog/ontology/generate', { datasource_id: 5, kb_ids: [11] }));
    // 面板被打开并定位到任务会话（进度在 AS-BOT 侧边栏呈现，页面不再有进度窗口）
    await waitFor(() => expect(useAsBotStore.getState().panelOpen).toBe(true));
    await waitFor(() => expect(useAsBotStore.getState().conversationId).toBe(777));
    expect(screen.queryByText('生成进度')).toBeNull();
  });
});
