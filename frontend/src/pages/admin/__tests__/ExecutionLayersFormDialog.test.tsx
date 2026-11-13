import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ExecutionLayers from '../ExecutionLayers';
import client from '@/api/client';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const ROWS = [
  { id: 11, name: 'cli-qoder', display_name: 'qoder CLI', description: '版本: 1.1.17',
    layer_type: 'cli', status: 'active', is_default: 1, source: 'admin',
    config: { cli_name: 'qoder', cli_path: 'runtime/qodercli/bin/qodercli', timeout: 300, mode: 'sdk' } },
  { id: 12, name: 'cli-claude', display_name: 'claude CLI', description: '供应商直连',
    layer_type: 'cli', status: 'inactive', source: 'admin',
    config: { cli_name: 'claude', mode: 'sdk', max_turns: 40, allowed_tools: ['read_file'] } },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/admin/execution-layers') return { data: ROWS } as any;
    return { data: [] } as any;   // /tools 与 /models
  });
  vi.mocked(client.put).mockResolvedValue({ data: {} } as any);
  vi.mocked(client.post).mockResolvedValue({ data: {} } as any);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const clickEdit = (displayName: string) => {
  const card = screen.getByText(displayName).closest('.p-3') as HTMLElement;
  fireEvent.click(card.querySelector('svg.lucide-pencil')?.closest('button') as Element);
};

/** 按当前显示文案定位弹窗内的下拉（Label 未与控件关联，拿不到可访问名） */
const openSelect = (dlg: HTMLElement, currentText: string) => {
  const trigger = within(dlg).getAllByRole('combobox')
    .find(c => (c.textContent || '').includes(currentText)) as HTMLElement;
  fireEvent.click(trigger);
};

describe('Harness 页 — 新建/编辑走弹窗（不再内联顶开列表）', () => {
  it('点编辑弹出 dialog，表单字段在弹窗内并回填该行配置', async () => {
    render(<ExecutionLayers />);
    await screen.findByText('qoder CLI');
    // 收起态：页面上不该有表单，列表直接可见（旧内联形态会把列表顶到首屏之下）
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByPlaceholderText('cli-qoder')).toBeNull();

    clickEdit('qoder CLI');
    const dlg = await screen.findByRole('dialog', { name: '编辑 Harness' });
    expect(within(dlg).getByText('名称(唯一标识)')).toBeInTheDocument();
    expect(within(dlg).getByPlaceholderText('cli-qoder').closest('[role="dialog"]')).toBe(dlg);
    expect(within(dlg).getByPlaceholderText('cli-qoder')).toHaveValue('cli-qoder');
    // 可执行路径输入框的 placeholder 固定，值来自该行 config
    expect(within(dlg).getByPlaceholderText('/usr/local/bin/qodercli')).toHaveValue('runtime/qodercli/bin/qodercli');
  });

  it('取消后编辑态复位：再点「新建 Harness」是空表单，不残留上一次编辑的行', async () => {
    render(<ExecutionLayers />);
    await screen.findByText('qoder CLI');

    clickEdit('qoder CLI');
    const editDlg = await screen.findByRole('dialog', { name: '编辑 Harness' });
    fireEvent.click(within(editDlg).getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: /新建 Harness/ }));
    const createDlg = await screen.findByRole('dialog', { name: '新建 Harness' });
    expect((within(createDlg).getByPlaceholderText('cli-qoder') as HTMLInputElement).value).toBe('');
    expect(within(createDlg).queryByDisplayValue('cli-qoder')).toBeNull();
  });

  it('保存走 PUT 并关闭弹窗（新建走 POST）', async () => {
    render(<ExecutionLayers />);
    await screen.findByText('qoder CLI');

    clickEdit('claude CLI');
    const dlg = await screen.findByRole('dialog', { name: '编辑 Harness' });
    fireEvent.click(within(dlg).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith(
      '/admin/execution-layers/12', expect.objectContaining({ name: 'cli-claude' })));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: /新建 Harness/ }));
    const createDlg = await screen.findByRole('dialog', { name: '新建 Harness' });
    fireEvent.change(within(createDlg).getByPlaceholderText('cli-qoder'), { target: { value: 'cli-new' } });
    // 新建态 CLI 类型为空会被 handleSave 拦住（fail-loud），先选一个后端
    openSelect(createDlg, '选择 CLI');
    fireEvent.click(await screen.findByRole('option', { name: /Qoder CLI/ }));
    fireEvent.click(within(createDlg).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith(
      '/admin/execution-layers', expect.objectContaining({ name: 'cli-new' })));
  });
});

describe('Harness 页 — 单轮工具调用上限（config.max_turns）', () => {
  const roundsInput = (dlg: HTMLElement) => within(dlg).getByPlaceholderText('默认 20');

  it('回填该行已配的值，改后保存写进 config.max_turns', async () => {
    render(<ExecutionLayers />);
    await screen.findByText('qoder CLI');

    clickEdit('claude CLI');   // 该行 config.max_turns=40
    const dlg = await screen.findByRole('dialog', { name: '编辑 Harness' });
    expect(roundsInput(dlg)).toHaveValue(40);      // number 输入框的 toHaveValue 是数字口径
    expect(screen.getByText('单轮工具调用上限: 40')).toBeInTheDocument();

    fireEvent.change(roundsInput(dlg), { target: { value: '30' } });
    fireEvent.click(within(dlg).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith(
      '/admin/execution-layers/12',
      expect.objectContaining({ config: expect.objectContaining({ max_turns: 30 }) })));
  });

  it('留空 = 显式传 null 清键（浅合并会删键 → 回默认 20），不是不传而留着旧值', async () => {
    render(<ExecutionLayers />);
    await screen.findByText('qoder CLI');

    fireEvent.click(screen.getByRole('button', { name: /新建 Harness/ }));
    const dlg = await screen.findByRole('dialog', { name: '新建 Harness' });
    openSelect(dlg, '选择 CLI');
    fireEvent.click(await screen.findByRole('option', { name: /Qoder CLI/ }));
    expect(roundsInput(dlg)).toHaveValue(null);    // 新建态不预填假数字（空 number 框即 null）
    fireEvent.change(within(dlg).getByPlaceholderText('cli-qoder'), { target: { value: 'cli-new' } });
    fireEvent.click(within(dlg).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith(
      '/admin/execution-layers',
      expect.objectContaining({ config: expect.objectContaining({ max_turns: null }) })));
  });

  it('填 0 或超界时提交前就拒：0 会被消费点的 or 静默回落成 20（配了等于没配）', async () => {
    render(<ExecutionLayers />);
    await screen.findByText('qoder CLI');

    clickEdit('claude CLI');
    const dlg = await screen.findByRole('dialog', { name: '编辑 Harness' });
    fireEvent.change(roundsInput(dlg), { target: { value: '0' } });
    fireEvent.click(within(dlg).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('1~200')));
    expect(client.put).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '保存' })).toBeInTheDocument();  // 弹窗不关，修完才能提
  });
});
