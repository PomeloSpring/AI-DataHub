import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MCPConfig from '../MCPConfig';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

/**
 * 内置服务行：后端 `_project_builtin` 已剥光 url/command/args/env/tools_config，
 * 所以这里的 mock 结构也**不带**这些字段 —— 前端类型里就没有它们，才是"配置隐藏"的真凭据。
 */
const BUILTIN_ROW = {
  id: 9001,
  name: 'elasticsearch',
  label: 'Elasticsearch 检索服务',
  description: '平台内置 Elasticsearch 只读查询能力（列开放索引范围 / 看字段 / 跑 SQL）。',
  transport: 'builtin',
  builtin: true,
  state: 'ready',
  status_message: '',
  is_active: 1,
  // 只给数量：模式名属部署配置，后端不返回，所以 mock 里也没有可泄的字段
  open_view_count: 3,
  discovered_tools: JSON.stringify([
    { name: 'list_es_indexes', description: '列出可查索引模式' },
    { name: 'describe_es_index', description: '查看索引字段' },
    { name: 'query_es_sql', description: '执行只读 SQL' },
  ]),
};

const EXTERNAL_ROWS = [
  { id: 7, name: 'log-warehouse', description: '日志仓查询服务', transport: 'streamable_http',
    url: 'http://10.0.0.9:3001/mcp', command: '', args: '', tools_config: '[]',
    discovered_tools: JSON.stringify([{ name: 'search_logs', description: '检索日志' }]),
    is_active: 1, datasource_id: 0, last_test_status: 'success' },
  // 描述为空的那一行用于锁死「回退显示 url/command」这类地址明文泄露
  { id: 8, name: 'ticket-mcp', description: '', transport: 'stdio',
    url: '', command: 'npx internal-runner', args: '', tools_config: '[]',
    discovered_tools: '', is_active: 1, datasource_id: 0 },
];

const mockList = (rows: any[]) =>
  vi.mocked(client.get).mockImplementation(async (url: string) =>
    url === '/admin/mcp-servers' ? { data: rows } as any : { data: [] } as any);

beforeEach(() => {
  vi.clearAllMocks();
  mockList([BUILTIN_ROW, ...EXTERNAL_ROWS]);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const builtinCard = async () => {
  const title = await screen.findByText('Elasticsearch 检索服务');
  return title.closest('.p-3') as HTMLElement;
};

describe('MCP 服务页 — 内置服务与外部服务同列表呈现', () => {
  it('内置服务显示业务可读名称，工具清单展开后不泄露内部 name', async () => {
    render(<MCPConfig />);
    const card = await builtinCard();
    expect(screen.getByText('平台内置')).toBeInTheDocument();
    expect(screen.getByText('就绪')).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: /提供工具/ }));
    for (const name of ['list_es_indexes', 'describe_es_index', 'query_es_sql']) {
      expect(within(card).getByText(name)).toBeInTheDocument();
    }
    // 主标题用 label 而不是内部 name（ui-resource-display：显示用 name/label）
    expect(within(card).queryByText('elasticsearch')).not.toBeInTheDocument();
  });

  it('内置服务不显示任何配置：无地址/凭据/白名单内容', async () => {
    render(<MCPConfig />);
    const card = await builtinCard();
    const text = card.textContent || '';
    for (const leak of ['http://', '9200', 'es-inner', 'stale', 's3cr3t', 'orders-*', 'npx']) {
      expect(text).not.toContain(leak);
    }
    expect(text).toContain('由部署配置托管');
  });

  it('内置服务只有测试连接，没有编辑与删除入口', async () => {
    render(<MCPConfig />);
    const card = await builtinCard();
    const buttons = within(card).getAllByRole('button');
    // 工具清单展开按钮 + 测试连接；不给 Pencil/Trash2
    expect(within(card).getByRole('button', { name: '测试连接' })).toBeInTheDocument();
    expect(buttons.length).toBeLessThanOrEqual(2);
    expect(card.querySelector('svg.lucide-trash-2')).toBeNull();
    expect(card.querySelector('svg.lucide-pencil')).toBeNull();
  });

  it('外部服务仍保留编辑与删除入口（内置的写保护不外溢）', async () => {
    const { container } = render(<MCPConfig />);
    await builtinCard();
    expect(container.querySelectorAll('svg.lucide-trash-2').length).toBe(2);
    expect(container.querySelectorAll('svg.lucide-pencil').length).toBe(2);
  });

  it('内置服务显示已开放范围数量，但不显示模式名单', async () => {
    render(<MCPConfig />);
    const card = await builtinCard();
    expect(card.textContent).toContain('已开放 3 个索引范围');
    expect(card.textContent).not.toContain('orders-*');
  });

  it('内置服务测试连接走服务 id，不复用远程 MCP 的发现流程', async () => {
    vi.mocked(client.post).mockResolvedValue({ data: { success: true, message: '连接正常' } } as any);
    render(<MCPConfig />);
    const card = await builtinCard();
    fireEvent.click(within(card).getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/admin/mcp-servers/9001/test'));
  });

  it('工具清单默认收起（内置与外部同口径），点一下展开、再点能收起', async () => {
    // 历史缺陷两次：① isExpanded 写成 `s.builtin || expanded.has(id)`，默认展开被写成
    // 恒展开，点了没反应；② 内置行默认平铺，首屏被工具描述打散。默认值不等于锁。
    render(<MCPConfig />);
    const card = await builtinCard();
    const toggle = within(card).getByRole('button', { name: /提供工具/ });
    expect(within(card).queryByText('list_es_indexes')).not.toBeInTheDocument();
    expect(toggle.textContent).toContain('▼');
    fireEvent.click(toggle);
    expect(within(card).getByText('list_es_indexes')).toBeInTheDocument();
    expect(toggle.textContent).toContain('▲');
    fireEvent.click(toggle);
    expect(within(card).queryByText('query_es_sql')).not.toBeInTheDocument();
  });

  it('外部服务同样默认收起，点一下展开', async () => {
    render(<MCPConfig />);
    await builtinCard();
    const card = screen.getByText('日志仓查询服务').closest('.p-3') as HTMLElement;
    expect(within(card).queryByText('search_logs')).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: /已发现工具/ }));
    expect(within(card).getByText('search_logs')).toBeInTheDocument();
  });

  it('配置不完整时给出可见原因，而不是静默显示成正常', async () => {
    mockList([{ ...BUILTIN_ROW, state: 'incomplete', status_message: '已启用但未开放任何索引（白名单为空，默认不开放）', is_active: 0 }, ...EXTERNAL_ROWS]);
    render(<MCPConfig />);
    const card = await builtinCard();
    expect(within(card).getByText('配置不完整')).toBeInTheDocument();
    expect(card.textContent).toContain('白名单为空');
  });

  it('未启用状态显示为未启用并说明原因', async () => {
    mockList([{ ...BUILTIN_ROW, state: 'disabled', status_message: '未启用（部署侧开关关闭）', is_active: 0 }, ...EXTERNAL_ROWS]);
    render(<MCPConfig />);
    const card = await builtinCard();
    expect(within(card).getByText('未启用')).toBeInTheDocument();
    expect(card.textContent).toContain('部署侧开关关闭');
  });
});

describe('MCP 服务页 — 外部服务不泄露连接信息', () => {
  it('外部服务描述为空时显示占位，而不是回退成 url/command', async () => {
    render(<MCPConfig />);
    await builtinCard();
    expect(screen.getByText('日志仓查询服务')).toBeInTheDocument();
    // 旧写法 description || url || command 会把 10.0.0.9:3001 当名称摊在页面上
    expect(screen.queryByText(/10\.0\.0\.9/)).not.toBeInTheDocument();
    expect(screen.queryByText('npx internal-runner')).not.toBeInTheDocument();
    expect(screen.getByText('（无描述）')).toBeInTheDocument();
  });

  it('有服务时不显示空态', async () => {
    render(<MCPConfig />);
    await builtinCard();
    expect(screen.queryByText(/暂无 MCP 服务/)).not.toBeInTheDocument();
  });
});
