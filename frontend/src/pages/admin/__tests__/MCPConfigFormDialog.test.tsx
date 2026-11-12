import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MCPConfig from '../MCPConfig';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const EXTERNAL_ROWS = [
  { id: 7, name: 'log-warehouse', description: '日志仓查询服务', transport: 'streamable_http',
    url: 'http://10.0.0.9:3001/mcp', command: '', args: '', tools_config: '[]',
    discovered_tools: JSON.stringify([{ name: 'search_logs', description: '检索日志' }]),
    is_active: 1, datasource_id: 0 },
  { id: 8, name: 'ticket-mcp', description: '工单服务', transport: 'stdio',
    url: '', command: 'npx', args: '', tools_config: '[]', discovered_tools: '',
    is_active: 1, datasource_id: 0 },
];

const mockList = (rows: any[]) =>
  vi.mocked(client.get).mockImplementation(async (url: string) =>
    url === '/admin/mcp-servers' ? { data: rows } as any : { data: [] } as any);

beforeEach(() => {
  vi.clearAllMocks();
  mockList(EXTERNAL_ROWS);
  vi.mocked(client.post).mockResolvedValue({ data: {} } as any);
  vi.mocked(client.put).mockResolvedValue({ data: {} } as any);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const rowCard = (label: string) =>
  screen.getByText(label).closest('.p-3') as HTMLElement;

const clickEdit = (label: string) => {
  const btn = rowCard(label).querySelector('svg.lucide-pencil')?.closest('button');
  fireEvent.click(btn as Element);
};

describe('MCP 服务页 — 新建/编辑走弹窗（不再内联顶开列表）', () => {
  it('点编辑弹出 dialog，表单字段在弹窗内而非列表上方', async () => {
    render(<MCPConfig />);
    await screen.findByText('日志仓查询服务');
    // 未打开时页面上不该有表单
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByPlaceholderText('filesystem')).toBeNull();

    clickEdit('日志仓查询服务');
    const dlg = await screen.findByRole('dialog', { name: '编辑 MCP 服务' });
    expect(within(dlg).getByText('服务名称')).toBeInTheDocument();
    // 字段必须挂在弹窗节点里（内联展开的历史形态是渲染在列表 div 内）
    expect(within(dlg).getByPlaceholderText('filesystem').closest('[role="dialog"]')).toBe(dlg);
    // 编辑态回填了该行的传输方式与地址
    expect(within(dlg).getByPlaceholderText('http://localhost:3001/mcp')).toHaveValue('http://10.0.0.9:3001/mcp');
  });

  it('取消关闭弹窗；再点「添加服务」不残留上一次编辑的工具清单', async () => {
    render(<MCPConfig />);
    await screen.findByText('日志仓查询服务');

    clickEdit('日志仓查询服务');
    const editDlg = await screen.findByRole('dialog', { name: '编辑 MCP 服务' });
    // 该行有已发现工具 → 编辑态渲染勾选清单
    expect(within(editDlg).getByText('search_logs')).toBeInTheDocument();
    fireEvent.click(within(editDlg).getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // 历史缺陷：新建只复位 form/editing，discoveredToolsList 仍留着上一个服务的清单，
    // 于是新建表单显示别人的工具勾选，保存把别人的 tools_config 写进新服务。
    fireEvent.click(screen.getByRole('button', { name: /添加服务/ }));
    const createDlg = await screen.findByRole('dialog', { name: '添加 MCP 服务' });
    expect(within(createDlg).queryByText('search_logs')).toBeNull();
    expect(within(createDlg).getByPlaceholderText(/query_sql/)).toBeInTheDocument();
    expect((within(createDlg).getByPlaceholderText('filesystem') as HTMLInputElement).value).toBe('');
  });

  it('保存成功后关闭弹窗并提交（新建走 POST，编辑走 PUT）', async () => {
    render(<MCPConfig />);
    await screen.findByText('日志仓查询服务');

    fireEvent.click(screen.getByRole('button', { name: /添加服务/ }));
    const dlg = await screen.findByRole('dialog', { name: '添加 MCP 服务' });
    fireEvent.change(within(dlg).getByPlaceholderText('filesystem'), { target: { value: 'new-mcp' } });
    fireEvent.click(within(dlg).getByRole('button', { name: '保存' }));

    await waitFor(() => expect(client.post).toHaveBeenCalledWith(
      '/admin/mcp-servers', expect.objectContaining({ name: 'new-mcp' })));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    clickEdit('日志仓查询服务');
    const editDlg = await screen.findByRole('dialog', { name: '编辑 MCP 服务' });
    fireEvent.click(within(editDlg).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith(
      '/admin/mcp-servers/7', expect.objectContaining({ name: 'log-warehouse' })));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});
