import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useChatStore } from '../chatStore';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import client from '../../api/client';
const mockClient = vi.mocked(client);

const SITES = [
  { site_code: 'global', display_name: '全球站点', is_aggregate: 1, sort_order: 0, datasources: [] },
  { site_code: 'cn', display_name: '中国站点', is_aggregate: 0, sort_order: 10,
    datasources: [{ id: 1, name: 'prd-cn', db_type: 'mysql' }] },
  { site_code: 'jp', display_name: '日本站点', is_aggregate: 0, sort_order: 50,
    datasources: [{ id: 2, name: 'prd-jp', db_type: 'mysql' }] },
];

beforeEach(() => {
  useChatStore.getState().reset();
  vi.clearAllMocks();
});

describe('loadAuthorizedSites — 可查站点是角色授权的派生视图', () => {
  it('多站时默认选「全球」（后端已只在 ≥2 站时下发聚合态）', async () => {
    mockClient.get.mockResolvedValue({ data: { items: SITES } } as never);
    await useChatStore.getState().loadAuthorizedSites(301);
    const s = useChatStore.getState();
    expect(s.authorizedSites.map((x) => x.site_code)).toEqual(['global', 'cn', 'jp']);
    expect(s.selectedSiteCode).toBe('global');
    expect(s.siteError).toBe('');
  });

  it('唯一具体站点直接选中，不摆无意义的选择', async () => {
    mockClient.get.mockResolvedValue({
      data: { items: [SITES[1]] },
    } as never);
    await useChatStore.getState().loadAuthorizedSites(301);
    expect(useChatStore.getState().selectedSiteCode).toBe('cn');
  });

  it('无站点化数据源（只接平台库）→ 空清单，选择器不渲染', async () => {
    mockClient.get.mockResolvedValue({ data: { items: [] } } as never);
    await useChatStore.getState().loadAuthorizedSites(301);
    const s = useChatStore.getState();
    expect(s.authorizedSites).toEqual([]);
    expect(s.selectedSiteCode).toBe('');
    expect(s.siteError).toBe('');
  });

  it('加载失败显式记错并可重试，不静默当成「无站点」', async () => {
    mockClient.get.mockRejectedValue({ response: { data: { detail: '站点服务不可用' } } } as never);
    await useChatStore.getState().loadAuthorizedSites(301);
    const s = useChatStore.getState();
    expect(s.siteError).toBe('站点服务不可用');
    expect(s.selectedSiteCode).toBe('');
  });

  it('已选站点仍在清单内时保持不变（切工作空间重载不覆盖用户选择）', async () => {
    useChatStore.setState({ selectedSiteCode: 'jp' });
    mockClient.get.mockResolvedValue({ data: { items: SITES } } as never);
    await useChatStore.getState().loadAuthorizedSites(301);
    expect(useChatStore.getState().selectedSiteCode).toBe('jp');
  });
});

describe('setSelectedSiteCode — 站点是会话属性', () => {
  it('已落库会话切换站点会持久化到会话行', () => {
    useChatStore.setState({ currentConvId: 42 });
    mockClient.put.mockResolvedValue({ data: { success: true } } as never);
    useChatStore.getState().setSelectedSiteCode('cn');
    expect(useChatStore.getState().selectedSiteCode).toBe('cn');
    expect(mockClient.put).toHaveBeenCalledWith('/chat/conversations/42', { site_code: 'cn' });
  });

  it('未落库的新会话只改本地状态，不凭空 PUT', () => {
    useChatStore.setState({ currentConvId: null });
    useChatStore.getState().setSelectedSiteCode('cn');
    expect(useChatStore.getState().selectedSiteCode).toBe('cn');
    expect(mockClient.put).not.toHaveBeenCalled();
  });
});

describe('会话创建/切换携带站点', () => {
  it('createConversation 把当前站点写入请求体并回填会话', async () => {
    useChatStore.setState({ selectedSiteCode: 'jp', selectedWorkspaceId: 301 });
    mockClient.post.mockResolvedValue({
      data: { id: 7, title: '新对话', datasource_id: 0, workspace_id: 301, site_code: 'jp', created_at: 'x' },
    } as never);
    const id = await useChatStore.getState().createConversation();
    expect(id).toBe(7);
    expect(mockClient.post).toHaveBeenCalledWith('/chat/conversations', {
      datasource_id: 0, workspace_id: 301, site_code: 'jp',
    });
    expect(useChatStore.getState().conversations[0].site_code).toBe('jp');
  });

  it('switchConversation 回填持久化站点；行内为空时保留当前选择', async () => {
    useChatStore.setState({ selectedSiteCode: 'cn' });
    mockClient.get.mockResolvedValue({
      data: { messages: [], workspace_id: 301, as_bot_key: '', datasource_id: 0, site_code: 'jp' },
    } as never);
    await useChatStore.getState().switchConversation(9);
    expect(useChatStore.getState().selectedSiteCode).toBe('jp');

    mockClient.get.mockResolvedValue({
      data: { messages: [], workspace_id: 301, as_bot_key: '', datasource_id: 0, site_code: '' },
    } as never);
    await useChatStore.getState().switchConversation(10);
    expect(useChatStore.getState().selectedSiteCode).toBe('jp');
  });
});
