/**
 * 数据集页批量操作交互（开关式批量模式）。
 *
 * 锁定：
 *  - 默认（未进批量模式）卡片上不挂勾选框，也不显示操作条；
 *  - 点「批量操作」进入批量模式后：卡片出现勾选框 + 顶部操作条（含「本页全选」，未选时也在）；
 *  - 未选任何项时「批量删除 / 批量改数据源」置灰；全选后可点；
 *  - 「退出批量」收起勾选框并清空选择。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Datasets from '../Datasets';
import client from '@/api/client';

vi.mock('@/stores/permissionStore', () => ({ hasPerm: () => true }));
vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const row = (id: number, name: string) => ({
  id, name, description: '', source_type: 'sql', object_key: '', datasource_id: 11,
  datasource_name: 'prd-cn', available_source_names: ['prd-cn'], usable_now: ['prd-cn'],
  chart_type: '', owner_id: 1, field_count: 2, references: 0, updated_at: '',
});

beforeEach(() => {
  (client.get as any).mockImplementation(async (url: string) => {
    if (url === '/datasets/') return { data: { items: [row(1, '数据集甲'), row(2, '数据集乙')] } };
    if (url === '/datasources/') return { data: [{ id: 11, name: 'prd-cn', site_code: 'cn', db_type: 'mysql' }] };
    if (url === '/datasets/channels') return { data: { items: [] } };
    throw new Error(`unexpected GET ${url}`);
  });
  (client.post as any).mockResolvedValue({ data: { succeeded: [], failed: [], succeeded_count: 0, failed_count: 0, message: '' } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderPage() { return render(<MemoryRouter><Datasets /></MemoryRouter>); }

describe('数据集页批量操作', () => {
  it('默认不挂勾选框', async () => {
    renderPage();
    await screen.findByText('数据集甲');
    expect(screen.queryByRole('button', { name: '选择 数据集甲' })).toBeNull();
    expect(screen.queryByText('本页全选')).toBeNull();
  });

  it('点「批量操作」后卡片出现勾选框且操作条含全选（未选也在）', async () => {
    renderPage();
    await screen.findByText('数据集甲');
    fireEvent.click(screen.getByRole('button', { name: '批量操作' }));
    expect(await screen.findByRole('button', { name: '选择 数据集甲' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '本页全选' })).toBeTruthy();
    expect(screen.getAllByText(/已选/).length).toBeGreaterThan(0);
  });

  it('未选时动作按钮置灰，本页全选后启用', async () => {
    renderPage();
    await screen.findByText('数据集甲');
    fireEvent.click(screen.getByRole('button', { name: '批量操作' }));
    const del = screen.getByRole('button', { name: /批量删除/ });
    const src = screen.getByRole('button', { name: /批量改数据源/ });
    expect(del).toBeDisabled();
    expect(src).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '本页全选' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /批量删除/ })).toBeEnabled());
    expect(screen.getAllByText(/已选/).some(el => (el.textContent || '').includes('2'))).toBe(true);
  });

  it('退出批量收起勾选框', async () => {
    renderPage();
    await screen.findByText('数据集甲');
    fireEvent.click(screen.getByRole('button', { name: '批量操作' }));
    await screen.findByRole('button', { name: '选择 数据集甲' });
    fireEvent.click(screen.getByRole('button', { name: '退出批量' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '选择 数据集甲' })).toBeNull());
    expect(screen.queryByText('本页全选')).toBeNull();
  });
});
